from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PostEventEntrySignalResponse(BaseModel):
    """One vehicle's post-event entry reading.

    Advisory only. ``research_only`` and ``fill_claim`` are carried through
    from the analytics layer so a consumer cannot mistake this for an
    executable quote, and ``explanation_codes`` carries the evidence caveat
    rather than leaving it in a document nobody reads.
    """

    model_config = ConfigDict(frozen=True)

    item_id: int
    external_key: str
    item_name: str
    catalog_match_status: str
    matched_catalog_name: str | None
    state: str | None
    is_entry_candidate: bool
    event_key: str | None
    event_closed_at: datetime | None
    days_since_close: int | None
    pre_event_baseline: Decimal | None
    latest_price: Decimal | None
    drawdown_ratio: Decimal | None
    drawdown_threshold: Decimal | None
    take_profit_price: Decimal | None
    take_profit_net_roi: Decimal | None
    hold_until: datetime | None
    research_only: bool
    fill_claim: bool
    explanation_codes: list[str]
    version: str | None


class PostEventEntryListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: datetime
    policy: str
    item_count: int
    entry_candidate_count: int
    items: list[PostEventEntrySignalResponse]
