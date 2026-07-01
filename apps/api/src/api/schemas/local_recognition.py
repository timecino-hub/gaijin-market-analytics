from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


class ReviewStatus(str, Enum):
    PROCESSING = "processing"
    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"
    CONFIRMED_WITH_EDITS = "confirmed_with_edits"
    REJECTED = "rejected"
    UNREADABLE = "unreadable"
    FAILED = "failed"
    EXPIRED = "expired"


TERMINAL_REVIEW_STATUSES = {
    ReviewStatus.CONFIRMED,
    ReviewStatus.CONFIRMED_WITH_EDITS,
    ReviewStatus.REJECTED,
    ReviewStatus.UNREADABLE,
    ReviewStatus.FAILED,
    ReviewStatus.EXPIRED,
}


class ObservedAtSource(str, Enum):
    REVIEW_CREATED_DEFAULT = "review_created_default"
    USER_EDITED = "user_edited"


class IdentityFieldSource(str, Enum):
    OCR_INITIAL = "ocr_initial"
    USER_DRAFT = "user_draft"
    CONFIRM_REQUEST = "confirm_request"
    CANONICAL_ITEM = "canonical_item"


PriceString = str | None
QuantityValue = int | None


class ErrorEntry(BaseModel):
    code: str
    message: str


class ImageMetadata(BaseModel):
    original_filename: str
    width: int
    height: int
    format: Literal["png", "jpeg"]


class RecognitionMetadata(BaseModel):
    ocr_backend: str
    ocr_backend_version: str
    layout_name: str
    layout_version: str
    config_sha256: str
    parser_version: str
    runner_version: str
    processing_duration_ms: int | None = None


class OcrCandidate(BaseModel):
    item_name_raw: str | None = None
    item_name_normalized: str | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    total_bid_quantity: int | None = None
    total_ask_quantity: int | None = None

    @field_serializer("best_bid", "best_ask")
    def serialize_optional_decimal(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class IdentitySources(BaseModel):
    selected_item_id: IdentityFieldSource | None = None
    item_key: IdentityFieldSource | None = None
    final_item_name: IdentityFieldSource | None = None


class ReviewDraft(BaseModel):
    selected_item_id: int | None = None
    item_key: str | None = None
    final_item_name: str | None = None
    identity_sources: IdentitySources = Field(default_factory=IdentitySources)
    final_best_bid: Decimal | None = None
    final_best_ask: Decimal | None = None
    final_total_bid_quantity: int | None = None
    final_total_ask_quantity: int | None = None
    observed_at: datetime | None = None
    observed_at_source: ObservedAtSource = ObservedAtSource.REVIEW_CREATED_DEFAULT
    reviewer_note: str | None = None

    @field_serializer("final_best_bid", "final_best_ask")
    def serialize_optional_decimal(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class OcrEvidenceSummary(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)
    confidence_source: str = "unavailable"
    confidence_available: bool = False


class ItemIdentity(BaseModel):
    item_id: int | None
    item_key: str
    item_name: str


class CandidateRecognition(BaseModel):
    layout_name: str
    layout_version: str
    config_sha256: str
    ocr_backend: str
    edited_fields: list[str]
    parser_version: str
    runner_version: str


class ReviewedCandidate(BaseModel):
    candidate_version: Literal["screen_review_candidate_v1"] = "screen_review_candidate_v1"
    review_id: str
    observed_at: datetime
    observed_at_source: ObservedAtSource
    item_identity: ItemIdentity
    best_bid: Decimal
    best_ask: Decimal
    total_bid_quantity: int | None
    total_ask_quantity: int | None
    recognition: CandidateRecognition
    status: Literal["confirmed", "confirmed_with_edits"]
    imported: Literal[False] = False
    database_written: Literal[False] = False
    quantity_semantics: Literal["screenshot_display_quantity"] = "screenshot_display_quantity"
    csv_quantity_mapping: Literal["not_mapped_to_ask_count_or_bid_count"] = (
        "not_mapped_to_ask_count_or_bid_count"
    )
    market_snapshot_created: Literal[False] = False

    @field_serializer("best_bid", "best_ask")
    def serialize_decimal(self, value: Decimal) -> str:
        return str(value)


class ReviewResponse(BaseModel):
    review_id: str
    created_at: datetime
    expires_at: datetime
    status: ReviewStatus
    suggested_observed_at: datetime
    image: ImageMetadata | None
    recognition: RecognitionMetadata
    ocr_candidate: OcrCandidate
    draft: ReviewDraft
    ocr_evidence_summary: OcrEvidenceSummary
    warnings: list[str]
    errors: list[str]
    candidate: ReviewedCandidate | None = None
    confirmed_at: datetime | None = None
    rejected_at: datetime | None = None


class ReviewCreateResponse(BaseModel):
    review_id: str
    status: ReviewStatus
    created_at: datetime
    expires_at: datetime


class ReviewListResponse(BaseModel):
    reviews: list[ReviewResponse]
    total: int
    store_count: int
    store_capacity: int


class ReviewCapabilitiesResponse(BaseModel):
    ocr_backend: str
    ocr_backend_version: str
    installed_ocr_languages: list[str]
    current_layout_profile: str
    layout_version: str
    config_sha256: str
    max_image_bytes: int
    max_image_pixels: int
    supported_image_formats: list[str]
    store_capacity: int
    store_ttl_seconds: int
    database_written: Literal[False]
    handles_history_images: Literal[False]
    browser_extension_connected: Literal[False]
    automatic_recognition_available: Literal[False]


class ReviewPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_item_id: int | None = None
    item_key: str | None = None
    final_item_name: str | None = None
    final_best_bid: Decimal | None = None
    final_best_ask: Decimal | None = None
    final_total_bid_quantity: int | None = None
    final_total_ask_quantity: int | None = None
    observed_at: datetime | None = None
    reviewer_note: str | None = None

    @field_validator("final_best_bid", "final_best_ask", mode="before")
    @classmethod
    def price_must_be_string(cls, value: object) -> Decimal | None:
        return _parse_price_string(value)

    @field_validator("final_total_bid_quantity", "final_total_ask_quantity")
    @classmethod
    def quantity_must_be_non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("invalid_quantity")
        return value

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _normalize_observed_at(value)


class ReviewConfirmRequest(ReviewPatchRequest):
    pass


class ReviewRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_note: str | None = None


class ReviewUnreadableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_note: str | None = None


class ReviewClearResponse(BaseModel):
    cleared: int


def _parse_price_string(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_price_string")
    stripped = value.strip()
    if stripped == "":
        return None
    try:
        parsed = Decimal(stripped)
    except InvalidOperation as exc:
        raise ValueError("invalid_price_string") from exc
    if not parsed.is_finite():
        raise ValueError("invalid_price_string")
    return parsed


def _normalize_observed_at(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at_timezone_required")
    normalized = value.astimezone(UTC)
    if normalized > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("observed_at_in_future")
    return normalized
