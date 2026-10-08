"""Small, dated history summaries for the catalog, independent of live quotes."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import HistoricalTradeBucket


@dataclass(frozen=True, slots=True)
class HistorySummary:
    daily_count: int
    first_day: datetime
    latest_day: datetime
    latest_daily_average: Decimal


async def load_history_summaries(
    session: AsyncSession, *, item_ids: list[int], as_of: datetime
) -> dict[int, HistorySummary]:
    if not item_ids:
        return {}
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    # Only source daily records whose UTC day is complete. A missing order book
    # says nothing about the availability of these historical observations.
    complete_before = as_of.astimezone(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    table = HistoricalTradeBucket
    summary = (
        select(
            table.item_id,
            func.count().label("daily_count"),
            func.min(table.bucket_start_utc).label("first_day"),
            func.max(table.bucket_start_utc).label("latest_day"),
        )
        .where(
            table.item_id.in_(item_ids),
            table.granularity == "1d",
            table.bucket_start_utc < complete_before,
        )
        .group_by(table.item_id)
        .subquery()
    )
    rows = await session.execute(
        select(summary, table.reported_vwap_price).join(
            table,
            (table.item_id == summary.c.item_id)
            & (table.bucket_start_utc == summary.c.latest_day)
            & (table.granularity == "1d"),
        )
    )
    return {
        row.item_id: HistorySummary(
            daily_count=row.daily_count,
            first_day=row.first_day,
            latest_day=row.latest_day,
            latest_daily_average=Decimal(row.reported_vwap_price),
        )
        for row in rows
    }
