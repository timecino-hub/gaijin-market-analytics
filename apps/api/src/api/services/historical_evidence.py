"""Load the price evidence shared by vehicle and strategy readings."""

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import HistoricalTradeBucket

# Both readings retain the full event history and its pre-event baselines.
_HISTORY_LOOKBACK = timedelta(days=3650)


async def load_historical_evidence(
    session: AsyncSession, *, item_ids: Collection[int], as_of: datetime
) -> dict[int, tuple[HistoricalTradeBucketEvidence, ...]]:
    """Read only calculation columns, in one query for the selected items.

    The bounds intentionally include a bucket starting at ``as_of``. Completed
    bucket and UTC-day selection remains the responsibility of pure analytics.
    """

    if not item_ids:
        return {}
    statement = (
        select(
            HistoricalTradeBucket.item_id,
            HistoricalTradeBucket.granularity,
            HistoricalTradeBucket.bucket_start_utc,
            HistoricalTradeBucket.bucket_duration_seconds,
            HistoricalTradeBucket.reported_vwap_price,
            HistoricalTradeBucket.reported_trade_volume,
        )
        .where(
            HistoricalTradeBucket.item_id.in_(item_ids),
            HistoricalTradeBucket.bucket_start_utc >= as_of - _HISTORY_LOOKBACK,
            HistoricalTradeBucket.bucket_start_utc <= as_of,
        )
        .order_by(
            HistoricalTradeBucket.item_id.asc(),
            HistoricalTradeBucket.bucket_start_utc.asc(),
        )
    )
    rows = await session.execute(statement)
    grouped: dict[int, list[HistoricalTradeBucketEvidence]] = {}
    for row in rows:
        grouped.setdefault(row.item_id, []).append(
            HistoricalTradeBucketEvidence(
                item_id=row.item_id,
                granularity=row.granularity,  # type: ignore[arg-type]
                bucket_start_utc=_utc(row.bucket_start_utc),
                bucket_duration=timedelta(seconds=row.bucket_duration_seconds),
                vwap_price=Decimal(row.reported_vwap_price),
                reported_trade_volume=row.reported_trade_volume,
            )
        )
    return {item_id: tuple(buckets) for item_id, buckets in grouped.items()}


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
