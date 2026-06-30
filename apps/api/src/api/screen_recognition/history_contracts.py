from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from api.screen_recognition.contracts import ImageInfo, OcrFieldEvidence


@dataclass(frozen=True)
class ChartEstimatePoint:
    normalized_x: Decimal
    pixel_x: int
    detected_pixel_y: int | None
    normalized_y: Decimal | None
    estimated_value: Decimal | None = None
    estimated_volume: Decimal | None = None
    source: str = "chart_estimate"
    exact: bool = False
    extraction_method: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "normalized_x": str(self.normalized_x),
            "pixel_x": self.pixel_x,
            "detected_pixel_y": self.detected_pixel_y,
            "normalized_y": None if self.normalized_y is None else str(self.normalized_y),
            "estimated_value": None if self.estimated_value is None else str(self.estimated_value),
            "estimated_volume": None if self.estimated_volume is None else str(self.estimated_volume),
            "source": self.source,
            "exact": self.exact,
            "extraction_method": self.extraction_method,
        }


@dataclass(frozen=True)
class AxisMapping:
    axis_name: str
    slope: Decimal
    intercept: Decimal
    min_value: Decimal
    max_value: Decimal
    tick_count: int
    max_residual_px: Decimal

    def value_for_pixel_y(self, pixel_y: int) -> Decimal:
        return self.slope * Decimal(pixel_y) + self.intercept

    def to_json(self) -> dict[str, Any]:
        return {
            "axis_name": self.axis_name,
            "slope": str(self.slope),
            "intercept": str(self.intercept),
            "min_value": str(self.min_value),
            "max_value": str(self.max_value),
            "tick_count": self.tick_count,
            "max_residual_px": str(self.max_residual_px),
        }


@dataclass(frozen=True)
class HistoryExpectedContract:
    filename: str
    expected_status: str = "ok"
    time_range: str | None = None
    price_series_color: str | None = None
    price_series_axis: str | None = None
    volume_series_color: str | None = None
    volume_series_axis: str | None = None
    left_axis_min: Decimal | None = None
    left_axis_max: Decimal | None = None
    right_axis_min: Decimal | None = None
    right_axis_max: Decimal | None = None
    sampled_points: tuple[dict[str, Any], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "expected_status": self.expected_status,
            "time_range": self.time_range,
            "price_series_color": self.price_series_color,
            "price_series_axis": self.price_series_axis,
            "volume_series_color": self.volume_series_color,
            "volume_series_axis": self.volume_series_axis,
            "left_axis_min": None if self.left_axis_min is None else str(self.left_axis_min),
            "left_axis_max": None if self.left_axis_max is None else str(self.left_axis_max),
            "right_axis_min": None if self.right_axis_min is None else str(self.right_axis_min),
            "right_axis_max": None if self.right_axis_max is None else str(self.right_axis_max),
            "sampled_points": list(self.sampled_points),
        }


@dataclass(frozen=True)
class HistoryRecognitionResult:
    item_name: str | None
    image_info: ImageInfo | None
    layout_match: bool
    time_range: str | None
    order_book_distribution_region_detected: bool
    historical_chart_region_detected: bool
    left_axis_raw_labels: tuple[str, ...]
    right_axis_raw_labels: tuple[str, ...]
    time_axis_raw_labels: tuple[str, ...]
    price_series_color: str | None
    price_series_axis: str | None
    volume_series_color: str | None
    volume_series_axis: str | None
    price_series_estimates: tuple[ChartEstimatePoint, ...]
    volume_series_estimates: tuple[ChartEstimatePoint, ...]
    left_axis_mapping: AxisMapping | None = None
    right_axis_mapping: AxisMapping | None = None
    sampled_point_comparisons: tuple[dict[str, Any], ...] = ()
    raw_ocr: dict[str, OcrFieldEvidence] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "item_name": self.item_name,
            "image_info": None if self.image_info is None else self.image_info.to_json(),
            "layout_match": self.layout_match,
            "time_range": self.time_range,
            "order_book_distribution_region_detected": self.order_book_distribution_region_detected,
            "historical_chart_region_detected": self.historical_chart_region_detected,
            "left_axis_raw_labels": list(self.left_axis_raw_labels),
            "right_axis_raw_labels": list(self.right_axis_raw_labels),
            "time_axis_raw_labels": list(self.time_axis_raw_labels),
            "price_series_color": self.price_series_color,
            "price_series_axis": self.price_series_axis,
            "volume_series_color": self.volume_series_color,
            "volume_series_axis": self.volume_series_axis,
            "price_series_estimates": [point.to_json() for point in self.price_series_estimates],
            "volume_series_estimates": [point.to_json() for point in self.volume_series_estimates],
            "left_axis_mapping": None if self.left_axis_mapping is None else self.left_axis_mapping.to_json(),
            "right_axis_mapping": None if self.right_axis_mapping is None else self.right_axis_mapping.to_json(),
            "sampled_point_comparisons": list(self.sampled_point_comparisons),
            "raw_ocr": {
                name: evidence.to_json() for name, evidence in sorted(self.raw_ocr.items())
            },
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }
