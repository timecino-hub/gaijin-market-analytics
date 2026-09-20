from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.services.manual_order_book_import import import_manual_order_book
from api.services.manual_order_book_snapshots import promote_manual_order_book_snapshots


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "manual-orderbook"
    / "confirmed-orderbook-capture.json"
)


def test_promotes_audited_order_book_to_idempotent_analysis_snapshot(
    migrated_database: str,
) -> None:
    engine = create_engine(migrated_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO items (external_key, name, category, is_active)
                    VALUES ('id50280_f_14a_iriaf_usa', 'Fixture item', 'vehicle', true)
                    """
                )
            )
    finally:
        engine.dispose()

    async def operation() -> tuple[object, object, object]:
        async_engine = create_async_engine(migrated_database)
        factory = async_sessionmaker(async_engine, expire_on_commit=False)
        try:
            async with factory() as session:
                await import_manual_order_book(
                    session=session,
                    content=FIXTURE.read_bytes(),
                    source_filename=FIXTURE.name,
                )
            async with factory() as session:
                dry_run = await promote_manual_order_book_snapshots(
                    session=session,
                    write=False,
                )
            async with factory() as session:
                written = await promote_manual_order_book_snapshots(
                    session=session,
                    write=True,
                )
            async with factory() as session:
                repeated = await promote_manual_order_book_snapshots(
                    session=session,
                    write=True,
                )
            return dry_run, written, repeated
        finally:
            await async_engine.dispose()

    dry_run, written, repeated = asyncio.run(operation())
    assert dry_run.would_create_count == 1
    assert dry_run.created_count == 0
    assert written.created_count == 1
    assert repeated.created_count == 0
    assert repeated.existing_count == 1

    engine = create_engine(migrated_database)
    try:
        with engine.connect() as connection:
            snapshot = connection.execute(
                text(
                    """
                    SELECT best_ask, best_bid, ask_count, bid_count,
                           estimated_volume, source_import_job_id
                    FROM market_snapshots
                    """
                )
            ).mappings().one()
            import_job_id = connection.execute(
                text("SELECT import_job_id FROM manual_order_book_imports")
            ).scalar_one()
    finally:
        engine.dispose()

    assert str(snapshot["best_ask"]) == "218.000000"
    assert str(snapshot["best_bid"]) == "171.030000"
    assert snapshot["ask_count"] == 70
    assert snapshot["bid_count"] == 41
    assert snapshot["estimated_volume"] is None
    assert snapshot["source_import_job_id"] == import_job_id
