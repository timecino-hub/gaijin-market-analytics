from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_serializer


SortField = Literal["name", "created_at", "updated_at"]
SortOrder = Literal["asc", "desc"]


class SnapshotSummary(BaseModel):
    observed_at: datetime
    best_ask: Decimal
    best_bid: Decimal | None
    ask_count: int | None
    bid_count: int | None
    estimated_volume: Decimal | None

    @field_serializer("best_ask", "best_bid", "estimated_volume")
    def serialize_decimal(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class ItemSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_key: str
    name: str
    category: str
    rarity: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    latest_snapshot: SnapshotSummary | None


class ItemListResponse(BaseModel):
    items: list[ItemSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class ItemDetailResponse(ItemSummary):
    snapshot_count: int
    first_snapshot_at: datetime | None
    last_snapshot_at: datetime | None


class SnapshotResponse(SnapshotSummary):
    id: int
    item_id: int
    source_import_job_id: int | None
    created_at: datetime


class OrderBookObservationResponse(BaseModel):
    id: int
    item_id: int
    market_snapshot_id: int
    screen_review_import_id: int
    observed_at: datetime
    best_ask: Decimal
    best_bid: Decimal | None
    observed_bid_quantity: int | None
    observed_ask_quantity: int | None
    quantity_semantics: Literal["screenshot_display_quantity"]
    source_type: Literal["screen_review"]
    source_version: str
    review_status: Literal["confirmed", "confirmed_with_edits"]
    created_at: datetime

    @field_serializer("best_ask", "best_bid")
    def serialize_price(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class CurrentOrderBookLevelResponse(BaseModel):
    side: Literal["BUY", "SELL"]
    level_index: int
    quantity: int
    price_raw: int
    base_amount_gjn: str
    display_amount_gjn: str
    canonical_display_text: str


class CurrentOrderBookContractResponse(BaseModel):
    contract_id: str
    contract_version: int
    currency_code: Literal["GJN"]
    raw_scale: int
    display_decimal_places: int
    evidence_level: str


class CurrentOrderBookProvenanceResponse(BaseModel):
    source_type: Literal["manual_response_json"]
    source_capture_schema_version: str
    capture_method: str
    review_status: Literal["confirmed_by_user"]
    request_action: Literal["UNKNOWN"]
    normalized_capture_fingerprint: str
    source_file_sha256: str
    raw_response_sha256_claim: str
    raw_response_hash_verifiable: Literal[False]
    read_model_schema_version: str
    read_model_implementation_version: str


class CurrentOrderBookResponse(BaseModel):
    schema_version: Literal["web_current_order_book_v1"]
    item: dict[str, int | str]
    captured_at: datetime
    freshness: Literal["fresh", "stale"]
    stale_after_seconds: int
    best_buy: CurrentOrderBookLevelResponse
    best_sell: CurrentOrderBookLevelResponse
    spread_display_text: str
    contract: CurrentOrderBookContractResponse
    provenance: CurrentOrderBookProvenanceResponse
    buy_levels: list[CurrentOrderBookLevelResponse]
    sell_levels: list[CurrentOrderBookLevelResponse]
