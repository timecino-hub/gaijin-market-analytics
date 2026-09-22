from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from gaijin_market_analytics.strategy_evidence import (
    EventOutcome,
    HorizonEvidence,
    ReturnSummary,
    StrategyEvidenceConfig,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api.clock import UtcClock, get_utc_clock
from api.db.session import get_session
from api.schemas.strategy_evidence import (
    EventLedgerRowResponse,
    ExitRuleResponse,
    HorizonEvidenceResponse,
    ReturnSummaryResponse,
    StrategyEvidenceResponse,
)
from api.services.strategy_evidence import (
    StrategyEvidenceLedger,
    StrategyEvidenceServiceError,
    build_strategy_evidence,
)

router = APIRouter(prefix="/api/v1/strategy-evidence", tags=["strategy-evidence"])


@router.get("", response_model=StrategyEvidenceResponse)
async def get_strategy_evidence(
    session: Annotated[AsyncSession, Depends(get_session)],
    clock: Annotated[UtcClock, Depends(get_utc_clock)],
    as_of: str | None = None,
) -> StrategyEvidenceResponse:
    moment = _parse_as_of(as_of, clock)
    try:
        ledger = await build_strategy_evidence(
            session=session, as_of=moment, config=StrategyEvidenceConfig()
        )
    except StrategyEvidenceServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": error.code, "message": error.message},
        ) from error
    return _response(ledger)


def _response(ledger: StrategyEvidenceLedger) -> StrategyEvidenceResponse:
    evidence = ledger.evidence
    return StrategyEvidenceResponse(
        as_of=evidence.as_of,
        drawdown_threshold=evidence.drawdown_threshold,
        primary_horizon_days=evidence.primary_horizon_days,
        fee_rate=evidence.fee_rate,
        completed_event_count=evidence.completed_event_count,
        evaluated_event_count=evidence.evaluated_event_count,
        vehicle_count=evidence.vehicle_count,
        selected_event_count=evidence.selected_event_count,
        rejected_event_count=evidence.rejected_event_count,
        no_baseline_count=evidence.no_baseline_count,
        no_entry_price_count=evidence.no_entry_price_count,
        unmatched_item_count=ledger.unmatched_item_count,
        horizons=[_horizon(value) for value in evidence.horizons],
        exit_frontier=[
            ExitRuleResponse(
                kind=rule.kind,
                target_net_roi=rule.target_net_roi,
                hit_ratio=rule.hit_ratio,
                median_days_to_hit=rule.median_days_to_hit,
                summary=_summary(rule.summary),
            )
            for rule in evidence.exit_frontier
        ],
        events=[
            _event_row(
                outcome,
                item_name=ledger.item_names.get(outcome.item_id, ""),
                horizon_days=evidence.primary_horizon_days,
            )
            for outcome in sorted(
                ledger.outcomes,
                key=lambda value: (value.closes_at, value.item_id),
                reverse=True,
            )
        ],
        explanation_codes=list(evidence.explanation_codes),
        research_only=evidence.research_only,
        version=evidence.version,
    )


def _horizon(value: HorizonEvidence) -> HorizonEvidenceResponse:
    return HorizonEvidenceResponse(
        horizon_days=value.horizon_days,
        all_events=_summary(value.all_events),
        selected=_summary(value.selected),
        rejected=_summary(value.rejected),
        pending_count=value.pending_count,
        superseded_count=value.superseded_count,
    )


def _summary(value: ReturnSummary) -> ReturnSummaryResponse:
    return ReturnSummaryResponse(
        sample_size=value.sample_size,
        vehicle_count=value.vehicle_count,
        median=value.median,
        mean=value.mean,
        support_ratio=value.support_ratio,
        tenth_percentile=value.tenth_percentile,
        ninetieth_percentile=value.ninetieth_percentile,
    )


def _event_row(
    outcome: EventOutcome, *, item_name: str, horizon_days: int
) -> EventLedgerRowResponse:
    reading = outcome.horizon(horizon_days)
    return EventLedgerRowResponse(
        item_id=outcome.item_id,
        item_name=item_name,
        event_key=outcome.event_key,
        closes_at=outcome.closes_at,
        state=outcome.state,
        pre_event_baseline=outcome.pre_event_baseline,
        entry_day=outcome.entry_day,
        entry_price=outcome.entry_price,
        drawdown_ratio=outcome.drawdown_ratio,
        selected=outcome.passes_drawdown_filter,
        horizon_state=reading.state if reading is not None else None,
        exit_price=reading.exit_price if reading is not None else None,
        net_roi=reading.net_roi if reading is not None else None,
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
