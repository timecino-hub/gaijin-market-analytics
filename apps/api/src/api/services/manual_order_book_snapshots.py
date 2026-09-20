from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ManualOrderBookImport, MarketSnapshot
from api.services.current_order_book import interpret_current_order_book_capture


class ManualOrderBookSnapshotError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ManualOrderBookSnapshotResult:
    examined_count: int
    created_count: int
    existing_count: int
    would_create_count: int


async def promote_manual_order_book_snapshots(
    *,
    session: AsyncSession,
    write: bool,
) -> ManualOrderBookSnapshotResult:
    imports = list(
        (
            await session.scalars(
                select(ManualOrderBookImport).order_by(
                    ManualOrderBookImport.captured_at.asc(),
                    ManualOrderBookImport.id.asc(),
                )
            )
        ).all()
    )
    created = existing_count = would_create = 0
    for imported in imports:
        values = _snapshot_values(imported)
        existing = await session.scalar(
            select(MarketSnapshot).where(
                MarketSnapshot.item_id == imported.item_id,
                MarketSnapshot.observed_at == imported.captured_at,
            )
        )
        if existing is not None:
            _validate_existing(existing, imported=imported, values=values)
            existing_count += 1
            continue
        would_create += 1
        if write:
            session.add(MarketSnapshot(**values))
            created += 1

    if write:
        await session.commit()
    else:
        await session.rollback()
    return ManualOrderBookSnapshotResult(
        examined_count=len(imports),
        created_count=created,
        existing_count=existing_count,
        would_create_count=would_create,
    )


def _snapshot_values(imported: ManualOrderBookImport) -> dict[str, object]:
    interpreted = interpret_current_order_book_capture(
        capture_payload=imported.capture_payload,
        captured_at=imported.captured_at,
        now=imported.captured_at,
    )
    levels = interpreted.read_model["levels"]
    buy = max(
        (level for level in levels if level["side"] == "BUY"),
        key=lambda level: level["price_raw"],
    )
    sell = min(
        (level for level in levels if level["side"] == "SELL"),
        key=lambda level: level["price_raw"],
    )
    return {
        "item_id": imported.item_id,
        "observed_at": imported.captured_at,
        "best_ask": Decimal(sell["canonical_display_text"]),
        "best_bid": Decimal(buy["canonical_display_text"]),
        "ask_count": imported.sell_level_count,
        "bid_count": imported.buy_level_count,
        "estimated_volume": None,
        "source_import_job_id": imported.import_job_id,
    }


def _validate_existing(
    existing: MarketSnapshot,
    *,
    imported: ManualOrderBookImport,
    values: dict[str, object],
) -> None:
    expected = (
        values["best_ask"],
        values["best_bid"],
        values["ask_count"],
        values["bid_count"],
    )
    actual = (
        existing.best_ask,
        existing.best_bid,
        existing.ask_count,
        existing.bid_count,
    )
    if actual != expected:
        raise ManualOrderBookSnapshotError(
            f"Snapshot collision for manual order-book import {imported.id}."
        )
    if existing.source_import_job_id not in (None, imported.import_job_id):
        raise ManualOrderBookSnapshotError(
            f"Snapshot provenance collision for manual order-book import {imported.id}."
        )
