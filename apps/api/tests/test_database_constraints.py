from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def _upgrade(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def test_database_accepts_valid_foundation_rows(test_database_url: str) -> None:
    _upgrade(test_database_url)
    engine = create_engine(test_database_url)

    try:
        with engine.begin() as conn:
            item_id = conn.execute(
                text(
                    """
                    INSERT INTO items (external_key, name, category)
                    VALUES ('synthetic-item-valid', 'Synthetic Item', 'vehicle')
                    RETURNING id
                    """
                )
            ).scalar_one()
            import_job_id = conn.execute(
                text(
                    """
                    INSERT INTO import_jobs (source_type, filename, checksum, status)
                    VALUES (
                        'synthetic_fixture',
                        'synthetic.csv',
                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                        'completed'
                    )
                    RETURNING id
                    """
                )
            ).scalar_one()
            best_ask = conn.execute(
                text(
                    """
                    INSERT INTO market_snapshots (
                        item_id,
                        observed_at,
                        best_ask,
                        estimated_volume,
                        source_import_job_id
                    )
                    VALUES (:item_id, '2026-06-27T00:00:00Z', 12.340000, NULL, :import_job_id)
                    RETURNING best_ask
                    """
                ),
                {"item_id": item_id, "import_job_id": import_job_id},
            ).scalar_one()

        assert best_ask == Decimal("12.340000")
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO items (external_key, name, category) VALUES ('', 'Bad', 'vehicle')",
        """
        INSERT INTO import_jobs (source_type, filename, checksum, status)
        VALUES ('synthetic_fixture', 'bad.csv', 'not-sha256', 'completed')
        """,
        """
        INSERT INTO import_jobs (source_type, filename, checksum, status)
        VALUES (
            'synthetic_fixture',
            'bad.csv',
            'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
            'unknown'
        )
        """,
        """
        INSERT INTO market_snapshots (item_id, observed_at, best_ask)
        VALUES (999999, '2026-06-27T00:00:00Z', 0)
        """,
    ],
)
def test_database_rejects_invalid_foundation_rows(
    test_database_url: str, statement: str
) -> None:
    _upgrade(test_database_url)
    engine = create_engine(test_database_url)

    try:
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(statement))
    finally:
        engine.dispose()
