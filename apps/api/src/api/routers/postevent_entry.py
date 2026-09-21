from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.clock import UtcClock, get_utc_clock
from api.db.session import get_session
from api.schemas.postevent_entry import (
    PostEventEntryListResponse,
    PostEventEntrySignalResponse,
)
from api.services.postevent_entry import (
    PostEventEntryItemSignal,
    PostEventEntryServiceError,
    list_postevent_entry_signals,
)

router = APIRouter(prefix="/api/v1/postevent-entry", tags=["postevent-entry"])

# The site runs the rule in its annotating form: it publishes the reasoning and
# gates nothing. The descriptive lift is large and the strict walk-forward lift
# is small, so the signal is offered for a reader to weigh rather than used to
# suppress anything.
POLICY = "ANNOTATE"


@router.get("", response_model=PostEventEntryListResponse)
async def list_postevent_entry(
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[UtcClock, Depends(get_utc_clock)],
    as_of: str | None = None,
    candidates_only: bool = Query(default=False),
    include_inactive: bool = Query(default=False),
) -> PostEventEntryListResponse:
    moment = _parse_as_of(as_of, clock)
    try:
        signals = await list_postevent_entry_signals(
            session=session,
            as_of=moment,
            candidates_only=candidates_only,
            include_inactive=include_inactive,
        )
    except PostEventEntryServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": error.code, "message": error.message},
        ) from error
    return _response(moment, signals)


@router.get("/{item_id}", response_model=PostEventEntrySignalResponse)
async def get_postevent_entry(
    item_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[UtcClock, Depends(get_utc_clock)],
    as_of: str | None = None,
) -> PostEventEntrySignalResponse:
    moment = _parse_as_of(as_of, clock)
    try:
        signals = await list_postevent_entry_signals(
            session=session, as_of=moment, item_id=item_id, include_inactive=True
        )
    except PostEventEntryServiceError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if error.code == "postevent_entry_item_not_found"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=code, detail={"code": error.code, "message": error.message}
        ) from error
    return _item(signals[0])


def _response(
    as_of: datetime, signals: list[PostEventEntryItemSignal]
) -> PostEventEntryListResponse:
    items = [_item(value) for value in signals]
    return PostEventEntryListResponse(
        as_of=as_of,
        policy=POLICY,
        item_count=len(items),
        entry_candidate_count=sum(value.is_entry_candidate for value in items),
        items=items,
    )


def _item(value: PostEventEntryItemSignal) -> PostEventEntrySignalResponse:
    signal = value.signal
    return PostEventEntrySignalResponse(
        item_id=value.item_id,
        external_key=value.external_key,
        item_name=value.item_name,
        catalog_match_status=value.catalog_match_status,
        matched_catalog_name=value.matched_catalog_name,
        state=signal.state if signal else None,
        is_entry_candidate=bool(signal and signal.is_entry_candidate),
        event_key=signal.event_key if signal else None,
        event_closed_at=signal.event_closed_at if signal else None,
        days_since_close=signal.days_since_close if signal else None,
        pre_event_baseline=signal.pre_event_baseline if signal else None,
        latest_price=signal.latest_price if signal else None,
        drawdown_ratio=signal.drawdown_ratio if signal else None,
        drawdown_threshold=signal.threshold if signal else None,
        take_profit_price=signal.take_profit_price if signal else None,
        take_profit_net_roi=signal.take_profit_net_roi if signal else None,
        hold_until=signal.hold_until if signal else None,
        research_only=signal.research_only if signal else True,
        fill_claim=signal.fill_claim if signal else False,
        explanation_codes=list(signal.explanation_codes) if signal else [],
        version=signal.version if signal else None,
    )


def _parse_as_of(value: str | None, clock: UtcClock) -> datetime:
    if value is None:
        # UtcClock is a callable returning the instant, not an object.
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
