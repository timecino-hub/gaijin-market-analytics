from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class VehicleEventRecordResponse(BaseModel):
    """One completed event in a vehicle's own record.

    ``outcome_closed`` is false while the holding horizon is still running, and
    ``net_roi`` is null there rather than estimated — an unfinished hold has no
    return yet.
    """

    model_config = ConfigDict(frozen=True)

    event_key: str
    closes_at: datetime
    pre_event_baseline: Decimal | None
    entry_price: Decimal | None
    drawdown_ratio: Decimal | None
    holding_days: int
    exit_price: Decimal | None
    net_roi: Decimal | None
    outcome_closed: bool


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
