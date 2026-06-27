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
