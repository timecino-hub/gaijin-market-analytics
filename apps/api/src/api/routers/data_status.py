from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.clock import UtcClock, get_utc_clock
from api.db.models import HistoricalTradeBucket, HistoricalTradeImport, Item
from api.db.session import get_session

router = APIRouter(prefix="/api/v1/data-status", tags=["data-status"])


class DataStatusResponse(BaseModel):
    item_count: int
    historical_bucket_count: int
    daily_record_count: int
    latest_complete_day: datetime | None
    latest_capture_at: datetime | None
    reference_as_of: datetime | None
    price_age_days: int | None
    stale: bool


@router.get("", response_model=DataStatusResponse)
async def data_status(
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[UtcClock, Depends(get_utc_clock)],
) -> DataStatusResponse:
    now = clock().astimezone(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    complete_daily = (
        HistoricalTradeBucket.granularity == "1d",
        HistoricalTradeBucket.bucket_start_utc < today,
    )
    row = (
        await session.execute(
            select(
                select(func.count(Item.id)).scalar_subquery().label("item_count"),
                select(func.count(HistoricalTradeBucket.id))
                .scalar_subquery()
                .label("bucket_count"),
                select(func.count(HistoricalTradeBucket.id))
                .where(*complete_daily)
                .scalar_subquery()
                .label("daily_count"),
                select(func.max(HistoricalTradeBucket.bucket_start_utc))
                .where(*complete_daily)
                .scalar_subquery()
                .label("latest_day"),
                select(func.max(HistoricalTradeImport.source_captured_at))
                .where(HistoricalTradeImport.source_captured_at <= now)
                .scalar_subquery()
                .label("latest_capture"),
            )
        )
    ).one()
    latest = row.latest_day
    if latest is not None:
        latest = (
            latest.replace(tzinfo=UTC)
            if latest.tzinfo is None
            else latest.astimezone(UTC)
        )
    reference = latest + timedelta(days=1) if latest is not None else None
    age = (now - reference).days if reference is not None else None
    return DataStatusResponse(
        item_count=row.item_count,
        historical_bucket_count=row.bucket_count,
        daily_record_count=row.daily_count,
        latest_complete_day=latest,
        latest_capture_at=row.latest_capture,
        reference_as_of=reference,
        price_age_days=age,
        stale=age is None or age > 14,
    )
