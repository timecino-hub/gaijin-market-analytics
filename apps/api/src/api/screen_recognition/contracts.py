from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Literal


PARSER_VERSION = "screen-order-book-parser/1.0.0"
CUT_RUNNER_VERSION = "screen-recognition-cut20/1.0.0"
MARKET_PRICE_CAP = Decimal("2000.00")


class CutStatus(str, Enum):
    PASSED = "passed"
    PASSED_WITH_WARNING = "passed_with_warning"
    FAILED = "failed"
    UNREADABLE = "unreadable"
    UNEXPECTED_ERROR = "unexpected_error"


class TestScope(str, Enum):
    END_TO_END = "end_to_end"
    PARSER_ONLY = "parser_only"


ERROR_ORDER = (
    "image_unreadable",
    "image_recognizer_not_configured",
    "unsupported_layout",
    "item_name_missing",
    "best_bid_missing",
    "best_ask_missing",
    "best_bid_mismatch",
    "best_ask_mismatch",
    "bid_ask_swapped",
    "total_bid_quantity_mismatch",
    "total_ask_quantity_mismatch",
    "total_bid_quantity_missing",
    "total_ask_quantity_missing",
    "aggregate_price_misclassified",
    "bid_levels_not_descending",
    "ask_levels_not_ascending",
    "first_bid_not_equal_best_bid",
    "first_ask_not_equal_best_ask",
    "displayed_quantity_sum_mismatch",
    "price_above_market_cap",
    "non_positive_price",
    "low_confidence",
    "ocr_candidate_ambiguous",
    "price_decimal_unconfirmed",
    "price_ocr_invalid",
    "quantity_ocr_invalid",
    "quantity_candidate_ambiguous",
    "quantity_label_not_detected",
    "quantity_candidate_outside_summary",
    "quantity_candidate_looks_like_price",
    "item_name_ocr_empty",
    "ground_truth_invalid",
    "unexpected_exception",
    "ocr_backend_error",
    "ocr_confidence_unavailable",
    "pair_current_image_missing",
    "pair_history_image_missing",
    "pair_duplicate_current_image",
    "pair_duplicate_history_image",
    "pair_invalid_filename",
    "pair_item_identity_mismatch",
    "pair_name_mismatch",
    "unexpected_extra_image",
    "price_series_not_detected",
    "volume_series_not_detected",
    "price_volume_series_swapped",
    "price_series_wrong_axis",
    "volume_series_wrong_axis",
    "left_axis_unreadable",
    "right_axis_unreadable",
    "time_axis_unreadable",
    "chart_region_not_detected",
    "order_book_distribution_not_detected",
    "chart_numeric_mapping_unavailable",
    "chart_value_out_of_axis_range",
)

HARD_ERROR_CODES = {
    "image_unreadable",
    "image_recognizer_not_configured",
    "unsupported_layout",
    "bid_ask_swapped",
    "aggregate_price_misclassified",
    "price_above_market_cap",
    "non_positive_price",
    "ground_truth_invalid",
    "unexpected_exception",
    "ocr_backend_error",
    "pair_current_image_missing",
    "pair_history_image_missing",
    "pair_duplicate_current_image",
    "pair_duplicate_history_image",
    "pair_invalid_filename",
    "pair_item_identity_mismatch",
    "pair_name_mismatch",
    "unexpected_extra_image",
    "price_series_not_detected",
    "volume_series_not_detected",
    "price_volume_series_swapped",
    "price_series_wrong_axis",
    "volume_series_wrong_axis",
    "left_axis_unreadable",
    "right_axis_unreadable",
    "time_axis_unreadable",
    "chart_region_not_detected",
    "order_book_distribution_not_detected",
    "chart_value_out_of_axis_range",
}


def stable_issue_codes(codes: list[str]) -> list[str]:
    known = [code for code in ERROR_ORDER if code in set(codes)]
    extra = sorted({code for code in codes if code not in ERROR_ORDER})
    return known + extra


@dataclass(frozen=True)
class NormalizedRoi:
    x: Decimal
    y: Decimal
    width: Decimal
    height: Decimal

    def to_json(self) -> dict[str, str]:
        return {
            "x": str(self.x),
            "y": str(self.y),
            "width": str(self.width),
            "height": str(self.height),
        }


@dataclass(frozen=True)
class LayoutProfile:
    name: str
    version: str
    min_width: int
    min_height: int
    min_aspect_ratio: Decimal
    max_aspect_ratio: Decimal
    rois: dict[str, NormalizedRoi]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "min_width": self.min_width,
            "min_height": self.min_height,
            "min_aspect_ratio": str(self.min_aspect_ratio),
            "max_aspect_ratio": str(self.max_aspect_ratio),
            "rois": {name: roi.to_json() for name, roi in sorted(self.rois.items())},
        }


@dataclass(frozen=True)
class ImageInfo:
    filename: str
    width: int
    height: int
    format: Literal["png", "jpeg"]

    def to_json(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "width": self.width,
            "height": self.height,
            "format": self.format,
        }


@dataclass(frozen=True)
class OcrBoundingBox:
    x: Decimal
    y: Decimal
    width: Decimal
    height: Decimal

    def to_json(self) -> dict[str, str]:
        return {
            "x": str(self.x),
            "y": str(self.y),
            "width": str(self.width),
            "height": str(self.height),
        }


@dataclass(frozen=True)
class OcrWordEvidence:
    text: str
    order: int
    bounding_box: OcrBoundingBox | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "order": self.order,
            "bounding_box": None if self.bounding_box is None else self.bounding_box.to_json(),
        }


@dataclass(frozen=True)
class OcrLineEvidence:
    text: str
    order: int
    bounding_box: OcrBoundingBox | None = None
    words: tuple[OcrWordEvidence, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "order": self.order,
            "bounding_box": None if self.bounding_box is None else self.bounding_box.to_json(),
            "words": [word.to_json() for word in self.words],
        }


@dataclass(frozen=True)
class OcrFieldEvidence:
    field_name: str
    raw_text: str
    confidence: Decimal | None
    confidence_source: str = "unavailable"
    bounding_box: OcrBoundingBox | None = None
    lines: tuple[OcrLineEvidence, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "raw_text": self.raw_text,
            "confidence": None if self.confidence is None else str(self.confidence),
            "confidence_source": self.confidence_source,
            "bounding_box": None if self.bounding_box is None else self.bounding_box.to_json(),
            "lines": [line.to_json() for line in self.lines],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class OcrResult:
    backend_name: str
    backend_version: str
    fields: dict[str, OcrFieldEvidence]
    warnings: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "backend_name": self.backend_name,
            "backend_version": self.backend_version,
            "fields": {
                name: evidence.to_json() for name, evidence in sorted(self.fields.items())
            },
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PriceLevel:
    exact_price: Decimal | None
    quantity: int | None
    raw_display_price: str
    price_lower_bound: Decimal | None = None
    lower_bound_inclusive: bool | None = None
    aggregation_type: str | None = None
    raw_quantity: str | None = None

    @property
    def is_aggregate(self) -> bool:
        return self.aggregation_type is not None

    def comparable_price(self) -> Decimal | None:
        return self.exact_price if self.exact_price is not None else self.price_lower_bound

    def to_json(self) -> dict[str, Any]:
        return {
            "exact_price": None if self.exact_price is None else str(self.exact_price),
            "price_lower_bound": (
                None if self.price_lower_bound is None else str(self.price_lower_bound)
            ),
            "lower_bound_inclusive": self.lower_bound_inclusive,
            "aggregation_type": self.aggregation_type,
            "quantity": self.quantity,
            "raw_display_price": self.raw_display_price,
            "raw_quantity": self.raw_quantity,
        }


@dataclass(frozen=True)
class ScreenContract:
    item_key: str | None = None
    item_key_source: str | None = None
    item_name: str | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    total_bid_quantity: int | None = None
    total_ask_quantity: int | None = None
    bid_levels: tuple[PriceLevel, ...] = ()
    ask_levels: tuple[PriceLevel, ...] = ()
    raw_fields: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "item_key": self.item_key,
            "item_key_source": self.item_key_source,
            "item_name": self.item_name,
            "best_bid": None if self.best_bid is None else str(self.best_bid),
            "best_ask": None if self.best_ask is None else str(self.best_ask),
            "total_bid_quantity": self.total_bid_quantity,
            "total_ask_quantity": self.total_ask_quantity,
            "bid_levels": [level.to_json() for level in self.bid_levels],
            "ask_levels": [level.to_json() for level in self.ask_levels],
            "raw_fields": dict(sorted(self.raw_fields.items())),
        }


@dataclass(frozen=True)
class GroundTruthEntry:
    sample_id: str
    filename: str
    expected_status: str
    item_key: str | None = None
    expected: ScreenContract = field(default_factory=ScreenContract)

    def to_json(self) -> dict[str, Any]:
        payload = self.expected.to_json()
        payload.update(
            {
                "sample_id": self.sample_id,
                "filename": self.filename,
                "expected_status": self.expected_status,
                "item_key": self.item_key,
            }
        )
        return payload


@dataclass(frozen=True)
class FieldComparison:
    field_name: str
    evaluable: bool
    passed: bool | None
    expected: Any
    actual: Any
    error_code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "evaluable": self.evaluable,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "error_code": self.error_code,
            "details": self.details,
        }


@dataclass(frozen=True)
class SampleResult:
    sample_id: str
    filename: str
    status: CutStatus
    image_info: ImageInfo | None
    layout_profile: str | None
    layout_match: bool
    recognized: ScreenContract
    expected: ScreenContract
    field_comparisons: tuple[FieldComparison, ...]
    raw_ocr: dict[str, OcrFieldEvidence]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    processing_duration_ms: int
    recognizer_version: str
    parser_version: str
    used_sidecar_ocr_text: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "filename": self.filename,
            "status": self.status.value,
            "image_info": None if self.image_info is None else self.image_info.to_json(),
            "layout_profile": self.layout_profile,
            "layout_match": self.layout_match,
            "recognized_result": self.recognized.to_json(),
            "expected_result": self.expected.to_json(),
            "field_comparisons": [
                comparison.to_json() for comparison in self.field_comparisons
            ],
            "raw_ocr": {
                name: evidence.to_json() for name, evidence in sorted(self.raw_ocr.items())
            },
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "processing_duration_ms": self.processing_duration_ms,
            "recognizer_version": self.recognizer_version,
            "parser_version": self.parser_version,
            "used_sidecar_ocr_text": self.used_sidecar_ocr_text,
        }
