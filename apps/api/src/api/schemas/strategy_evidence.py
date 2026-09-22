"""Response shapes for the general strategy's own evidence.

This endpoint answers a different question from ``/vehicle-potential``: not
what one vehicle is, but what the one rule did when it was applied to all of
them. So it returns a single pooled reading rather than a list of vehicles,
with the per-event ledger attached underneath so the aggregates can be checked
against the events that produced them.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ReturnSummaryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    sample_size: int
    # Carried next to ``sample_size`` on purpose: these events repeat vehicles,
    # so the two numbers together say how much independent evidence there is.
    vehicle_count: int
    median: Decimal | None
    mean: Decimal | None
    support_ratio: Decimal | None
    tenth_percentile: Decimal | None
    ninetieth_percentile: Decimal | None


class HorizonEvidenceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    horizon_days: int
    all_events: ReturnSummaryResponse
    selected: ReturnSummaryResponse
    rejected: ReturnSummaryResponse
    pending_count: int
    superseded_count: int


class ExitRuleResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    target_net_roi: Decimal | None
    hit_ratio: Decimal | None
    median_days_to_hit: int | None
    summary: ReturnSummaryResponse


class EventLedgerRowResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    item_id: int
    item_name: str
    event_key: str
    closes_at: datetime
    state: str
    pre_event_baseline: Decimal | None
    entry_day: datetime | None
    entry_price: Decimal | None
    drawdown_ratio: Decimal | None
    selected: bool
    horizon_state: str | None
    exit_price: Decimal | None
    net_roi: Decimal | None


class StrategyEvidenceResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: datetime
    drawdown_threshold: Decimal
    primary_horizon_days: int
    fee_rate: Decimal

    completed_event_count: int
    evaluated_event_count: int
    vehicle_count: int
    selected_event_count: int
    rejected_event_count: int
    no_baseline_count: int
    no_entry_price_count: int
    unmatched_item_count: int

    horizons: list[HorizonEvidenceResponse]
    exit_frontier: list[ExitRuleResponse]
    events: list[EventLedgerRowResponse]

    explanation_codes: list[str]
    research_only: bool
    version: str
