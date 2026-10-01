from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from gaijin_market_analytics.vehicle_potential import VehiclePotentialConfig
from sqlalchemy.ext.asyncio import AsyncSession

from api.clock import UtcClock, get_utc_clock
from api.db.session import get_session
from api.schemas.vehicle_potential import (
    VehicleEventRecordResponse,
    VehiclePotentialListResponse,
    VehiclePotentialResponse,
    VehicleTargetReferenceResponse,
)
from api.services.vehicle_potential import (
    VehiclePotentialItem,
    VehiclePotentialServiceError,
    list_vehicle_potential,
)

router = APIRouter(prefix="/api/v1/vehicle-potential", tags=["vehicle-potential"])


@router.get("", response_model=VehiclePotentialListResponse)
async def list_potential(
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[UtcClock, Depends(get_utc_clock)],
    as_of: str | None = None,
    ranked_only: bool = Query(default=False),
    include_inactive: bool = Query(default=False),
) -> VehiclePotentialListResponse:
    moment = _parse_as_of(as_of, clock)
    config = VehiclePotentialConfig()
    try:
        items = await list_vehicle_potential(
            session=session,
            as_of=moment,
            ranked_only=ranked_only,
            include_inactive=include_inactive,
            config=config,
        )
    except VehiclePotentialServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": error.code, "message": error.message},
        ) from error
    rows = [_item(value) for value in items]
    return VehiclePotentialListResponse(
        as_of=moment,
        item_count=len(rows),
        ranked_count=sum(1 for value in rows if value.state == "RANKED"),
        history_days=config.history_days,
        items=rows,
    )


@router.get("/{item_id}", response_model=VehiclePotentialResponse)
async def get_potential(
    item_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[UtcClock, Depends(get_utc_clock)],
    as_of: str | None = None,
) -> VehiclePotentialResponse:
    moment = _parse_as_of(as_of, clock)
    try:
        items = await list_vehicle_potential(
            session=session, as_of=moment, item_id=item_id, include_inactive=True
        )
    except VehiclePotentialServiceError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if error.code == "vehicle_potential_item_not_found"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=code, detail={"code": error.code, "message": error.message}
        ) from error
    return _item(items[0])


def _item(value: VehiclePotentialItem) -> VehiclePotentialResponse:
    reading = value.reading
    return VehiclePotentialResponse(
        item_id=value.item_id,
        external_key=value.external_key,
        item_name=value.item_name,
        category=value.category,
        catalog_match_status=value.catalog_match_status,
        matched_catalog_name=value.matched_catalog_name,
        state=reading.state,
        current_price=reading.current_price,
        current_price_day=reading.current_price_day,
        price_age_days=reading.price_age_days,
        history_day_count=reading.history_day_count,
        window_start=reading.window_start,
        window_end=reading.window_end,
        price_percentile=reading.price_percentile,
        median_price=reading.median_price,
        low_price=reading.low_price,
        high_price=reading.high_price,
        ratio_to_median=reading.ratio_to_median,
        median_daily_volume=reading.median_daily_volume,
        recent_daily_volume=reading.recent_daily_volume,
        recent_trading_day_count=reading.recent_trading_day_count,
        target_reference=(
            VehicleTargetReferenceResponse.model_validate(
                reading.target_reference, from_attributes=True
            )
            if reading.target_reference is not None
            else None
        ),
        events=[
            VehicleEventRecordResponse(
                event_key=record.event_key,
                closes_at=record.closes_at,
                pre_event_baseline=record.pre_event_baseline,
                entry_price=record.entry_price,
                entry_price_day=record.entry_price_day,
                drawdown_ratio=record.drawdown_ratio,
                holding_days=record.holding_days,
                exit_price=record.exit_price,
                net_roi=record.net_roi,
                outcome_closed=record.outcome_closed,
                outcome_status=record.outcome_status,
            )
            for record in reading.events
        ],
        completed_event_count=reading.completed_event_count,
        closed_outcome_count=reading.closed_outcome_count,
        median_drawdown_ratio=reading.median_drawdown_ratio,
        median_net_roi=reading.median_net_roi,
        positive_outcome_count=reading.positive_outcome_count,
        days_since_last_event_close=reading.days_since_last_event_close,
        explanation_codes=list(reading.explanation_codes),
        research_only=reading.research_only,
        version=reading.version,
    )


def _parse_as_of(value: str | None, clock: UtcClock) -> datetime:
    if value is None:
        return _normalize(clock())
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_as_of", "message": "as_of must be ISO-8601."},
        ) from error
    return _normalize(parsed)


def _normalize(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
