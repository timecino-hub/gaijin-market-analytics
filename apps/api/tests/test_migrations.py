from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _table_names(database_url: str) -> set[str]:
    sync_url = database_url.replace("postgresql+psycopg://", "postgresql+psycopg://", 1)
    engine = create_engine(sync_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_initial_migration_upgrade_downgrade_and_upgrade_again(test_database_url: str) -> None:
    config = _alembic_config(test_database_url)

    command.upgrade(config, "head")
    assert {
        "items",
        "import_jobs",
        "market_snapshots",
        "screen_review_imports",
        "order_book_observations",
    }.issubset(_table_names(test_database_url))

    command.downgrade(config, "base")
    assert not {
        "items",
        "import_jobs",
        "market_snapshots",
        "screen_review_imports",
        "order_book_observations",
    }.intersection(_table_names(test_database_url))

    command.upgrade(config, "head")
    assert {
        "items",
        "import_jobs",
        "market_snapshots",
        "screen_review_imports",
        "order_book_observations",
    }.issubset(_table_names(test_database_url))


def test_order_book_observation_migration_backfills_existing_review_imports(
    test_database_url: str,
) -> None:
    config = _alembic_config(test_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "20260710_0002")

    engine = create_engine(test_database_url)
    try:
        with engine.begin() as conn:
            item_id = conn.execute(
                text(
                    """
                    INSERT INTO items (external_key, name, category, is_active)
                    VALUES ('migration-backfill', 'Migration Backfill', 'vehicle', true)
                    RETURNING id
                    """
                )
            ).scalar_one()
            snapshot_id = conn.execute(
                text(
                    """
                    INSERT INTO market_snapshots (
                        item_id, observed_at, best_ask, best_bid
                    )
                    VALUES (:item_id, '2026-07-10T12:00:00Z', 13.000000, 12.000000)
                    RETURNING id
                    """
                ),
                {"item_id": item_id},
            ).scalar_one()
            review_import_id = conn.execute(
                text(
                    """
                    INSERT INTO screen_review_imports (
                        review_id,
                        item_id,
                        market_snapshot_id,
                        review_status,
                        candidate_version,
                        candidate_sha256,
                        total_bid_quantity,
                        total_ask_quantity,
                        candidate_payload,
                        source_metadata,
                        imported_at
                    )
                    VALUES (
                        'migration-review',
                        :item_id,
                        :snapshot_id,
                        'confirmed_with_edits',
                        'screen_review_candidate_v1',
                        :candidate_sha256,
                        5,
                        7,
                        '{}'::jsonb,
                        '{}'::jsonb,
                        '2026-07-10T12:01:00Z'
                    )
                    RETURNING id
                    """
                ),
                {
                    "item_id": item_id,
                    "snapshot_id": snapshot_id,
                    "candidate_sha256": "a" * 64,
                },
            ).scalar_one()
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(test_database_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        market_snapshot_id,
                        screen_review_import_id,
                        observed_bid_quantity,
                        observed_ask_quantity,
                        quantity_semantics,
                        source_type,
                        source_version,
                        review_status,
                        created_at
                    FROM order_book_observations
                    """
                )
            ).mappings().one()
    finally:
        engine.dispose()

    assert row["market_snapshot_id"] == snapshot_id
    assert row["screen_review_import_id"] == review_import_id
    assert row["observed_bid_quantity"] == 5
    assert row["observed_ask_quantity"] == 7
    assert row["quantity_semantics"] == "screenshot_display_quantity"
    assert row["source_type"] == "screen_review"
    assert row["source_version"] == "screen_review_candidate_v1"
    assert row["review_status"] == "confirmed_with_edits"
    assert row["created_at"].isoformat().startswith("2026-07-10T12:01:00")
