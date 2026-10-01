from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer


class VehicleEventRecordResponse(BaseModel):
    """One completed event in a vehicle's own record.

    The legacy entry uses the first recorded complete day after close and its
    actual date. The exact day 180 price must exist and be complete for an
    observed outcome; unfinished or missing prices retain a null return.
    """

    model_config = ConfigDict(frozen=True)

    event_key: str
    closes_at: datetime
    pre_event_baseline: Decimal | None
    entry_price: Decimal | None
    entry_price_day: datetime | None
    drawdown_ratio: Decimal | None
    holding_days: int
    exit_price: Decimal | None
    net_roi: Decimal | None
    outcome_closed: bool
    outcome_status: Literal[
        "OBSERVED", "PENDING_HORIZON", "MISSING_EXIT_PRICE", "NO_ENTRY_PRICE"
    ]


class VehicleTargetReferenceResponse(BaseModel):
    """Offers from an observed average; no actual purchase or fill is implied."""

    model_config = ConfigDict(frozen=True)

    cost_basis: Literal["LATEST_COMPLETED_DAY_AVERAGE_PROXY"]
    reference_cost: Decimal
    fee_rate: Decimal
    preferred_offer: Decimal
    minimum_offer: Decimal
    median_net_roi: Decimal
    median_supports_preferred: bool
    median_supports_minimum: bool
    preferred_within_market_cap: bool
    minimum_within_market_cap: bool

    @field_serializer(
        "reference_cost",
        "fee_rate",
        "preferred_offer",
        "minimum_offer",
        "median_net_roi",
    )
    def decimal_string(self, value: Decimal) -> str:
        return format(value, "f")


class VehiclePotentialResponse(BaseModel):
    """One vehicle's reading: where its price sits, and what its events did.

    The ranking uses only the price position. ``completed_event_count`` and
    ``closed_outcome_count`` travel with every event aggregate so a consumer
    can see how little they rest on.
    """

    model_config = ConfigDict(frozen=True)

    item_id: int
    external_key: str
    item_name: str
    category: str | None
    catalog_match_status: str
    matched_catalog_name: str | None
    state: str

    current_price: Decimal | None
    current_price_day: datetime | None
    price_age_days: int | None
    history_day_count: int
    window_start: datetime | None
    window_end: datetime | None
    price_percentile: Decimal | None
    median_price: Decimal | None
    low_price: Decimal | None
    high_price: Decimal | None
    ratio_to_median: Decimal | None
    median_daily_volume: int | None
    recent_daily_volume: int | None
    recent_trading_day_count: int | None
    target_reference: VehicleTargetReferenceResponse | None

    events: list[VehicleEventRecordResponse]
    completed_event_count: int
    closed_outcome_count: int
    median_drawdown_ratio: Decimal | None
    median_net_roi: Decimal | None
    positive_outcome_count: int | None
    days_since_last_event_close: int | None

    explanation_codes: list[str]
    research_only: bool
    version: str


class VehiclePotentialListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: datetime
    item_count: int
    ranked_count: int
    history_days: int
    items: list[VehiclePotentialResponse]
