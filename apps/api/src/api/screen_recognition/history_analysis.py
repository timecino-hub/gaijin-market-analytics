from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Callable

from PIL import Image

from api.screen_recognition.axis_mapping import AxisMappingError, build_axis_mapping
from api.screen_recognition.config import PairedCutConfig
from api.screen_recognition.contracts import ImageInfo, OcrFieldEvidence, stable_issue_codes
from api.screen_recognition.history_contracts import ChartEstimatePoint, HistoryRecognitionResult
from api.screen_recognition.layouts import roi_to_pixels


RGBPredicate = Callable[[int, int, int], bool]


def analyze_history_image(
    *,
    image_path: Path,
    image_info: ImageInfo,
    layout_profile: object,
    ocr_fields: dict[str, OcrFieldEvidence],
    config: PairedCutConfig,
) -> HistoryRecognitionResult:
    errors: list[str] = []
    warnings: list[str] = []
    item_name = (ocr_fields.get("item_name").raw_text.strip() if ocr_fields.get("item_name") else "") or None
    with Image.open(image_path) as source:
        image = source.convert("RGB")
        order_region_detected = _roi_has_non_background(image, layout_profile.rois["order_book_distribution_region"], image_info)
        chart_region_detected = _roi_has_non_background(image, layout_profile.rois["historical_chart_region"], image_info)
        if not order_region_detected:
            errors.append("order_book_distribution_not_detected")
        if not chart_region_detected:
            errors.append("chart_region_not_detected")

        red_points = extract_red_price_points(image, image_info, layout_profile.rois["red_price_plot"], config)
        blue_points = extract_blue_volume_points(image, image_info, layout_profile.rois["blue_volume_plot"], config)
        red_detected = any(point.detected_pixel_y is not None for point in red_points)
        blue_detected = any(point.detected_pixel_y is not None for point in blue_points)
        if not red_detected:
            errors.append("price_series_not_detected")
        if not blue_detected:
            errors.append("volume_series_not_detected")

        left_mapping = None
        right_mapping = None
        chart_x, chart_y, _chart_width, chart_height = roi_to_pixels(
            layout_profile.rois["historical_chart_region"], image_info
        )
        try:
            left_mapping = build_axis_mapping(
                ocr_fields.get("left_axis_labels", _empty_evidence("left_axis_labels")),
                axis_name="left",
                axis_pixel_height=chart_height,
                max_residual_px=config.axis_mapping_max_residual_px,
                require_non_negative=False,
            )
        except AxisMappingError as exc:
            errors.append(exc.code)
        try:
            right_mapping = build_axis_mapping(
                ocr_fields.get("right_axis_labels", _empty_evidence("right_axis_labels")),
                axis_name="right",
                axis_pixel_height=chart_height,
                max_residual_px=config.axis_mapping_max_residual_px,
                require_non_negative=True,
            )
        except AxisMappingError as exc:
            errors.append(exc.code)

    if left_mapping is None or right_mapping is None:
        errors.append("chart_numeric_mapping_unavailable")
    mapped_red = tuple(
        _map_value(point, left_mapping, chart_y=chart_y) for point in red_points
    )
    mapped_blue = tuple(
        _map_volume(point, right_mapping, chart_y=chart_y) for point in blue_points
    )
    errors.extend(_out_of_range_errors(mapped_red, left_mapping, value_key="estimated_value"))
    errors.extend(_out_of_range_errors(mapped_blue, right_mapping, value_key="estimated_volume"))
    return HistoryRecognitionResult(
        item_name=item_name,
        image_info=image_info,
        layout_match=True,
        time_range="one_month" if _has_month_hint(ocr_fields.get("time_axis_labels")) else None,
        order_book_distribution_region_detected=order_region_detected,
        historical_chart_region_detected=chart_region_detected,
        left_axis_raw_labels=_raw_lines(ocr_fields.get("left_axis_labels")),
        right_axis_raw_labels=_raw_lines(ocr_fields.get("right_axis_labels")),
        time_axis_raw_labels=_raw_lines(ocr_fields.get("time_axis_labels")),
        price_series_color="red" if red_detected else None,
        price_series_axis="left" if red_detected else None,
        volume_series_color="blue" if blue_detected else None,
        volume_series_axis="right" if blue_detected else None,
        price_series_estimates=mapped_red,
        volume_series_estimates=mapped_blue,
        left_axis_mapping=left_mapping,
        right_axis_mapping=right_mapping,
        raw_ocr=ocr_fields,
        warnings=tuple(stable_issue_codes(warnings)),
        errors=tuple(stable_issue_codes(list(dict.fromkeys(errors)))),
    )


def extract_red_price_points(
    image: Image.Image, image_info: ImageInfo, roi: object, config: PairedCutConfig
) -> tuple[ChartEstimatePoint, ...]:
    return _extract_series_points(
        image=image,
        image_info=image_info,
        roi=roi,
        config=config,
        predicate=lambda r, g, b: (
            config.red_mask.min_r <= r <= config.red_mask.max_r
            and config.red_mask.min_g <= g <= config.red_mask.max_g
            and config.red_mask.min_b <= b <= config.red_mask.max_b
            and r - max(g, b) >= config.red_mask.min_delta
        ),
        method=config.red_extraction_method,
        use_upper_boundary=True,
    )


def extract_blue_volume_points(
    image: Image.Image, image_info: ImageInfo, roi: object, config: PairedCutConfig
) -> tuple[ChartEstimatePoint, ...]:
    return _extract_series_points(
        image=image,
        image_info=image_info,
        roi=roi,
        config=config,
        predicate=lambda r, g, b: (
            config.blue_mask.min_r <= r <= config.blue_mask.max_r
            and config.blue_mask.min_g <= g <= config.blue_mask.max_g
            and config.blue_mask.min_b <= b <= config.blue_mask.max_b
            and b - max(r, g) >= config.blue_mask.min_delta
        ),
        method=config.blue_extraction_method,
        use_upper_boundary=False,
    )


def _extract_series_points(
    *,
    image: Image.Image,
    image_info: ImageInfo,
    roi: object,
    config: PairedCutConfig,
    predicate: RGBPredicate,
    method: str,
    use_upper_boundary: bool,
) -> tuple[ChartEstimatePoint, ...]:
    x0, y0, width, height = roi_to_pixels(roi, image_info)
    pixels = image.load()
    points: list[ChartEstimatePoint] = []
    for normalized_x in config.sample_normalized_x:
        local_x = int((Decimal(width - 1) * normalized_x).to_integral_value())
        image_x = x0 + local_x
        y_candidates: list[int] = []
        for dx in (-1, 0, 1):
            sample_x = min(max(x0, image_x + dx), x0 + width - 1)
            for local_y in range(height):
                r, g, b = pixels[sample_x, y0 + local_y]
                if predicate(r, g, b):
                    y_candidates.append(local_y)
        if not y_candidates:
            points.append(
                ChartEstimatePoint(
                    normalized_x=normalized_x,
                    pixel_x=image_x,
                    detected_pixel_y=None,
                    normalized_y=None,
                    extraction_method=method,
                )
            )
            continue
        local_y = min(y_candidates) if use_upper_boundary else sorted(y_candidates)[len(y_candidates) // 2]
        points.append(
            ChartEstimatePoint(
                normalized_x=normalized_x,
                pixel_x=image_x,
                detected_pixel_y=y0 + local_y,
                normalized_y=Decimal(local_y) / Decimal(max(1, height - 1)),
                extraction_method=method,
            )
        )
    return tuple(points)


def _map_value(point: ChartEstimatePoint, mapping: object | None, *, chart_y: int) -> ChartEstimatePoint:
    if mapping is None or point.detected_pixel_y is None:
        return point
    value = mapping.value_for_pixel_y(point.detected_pixel_y - chart_y)
    return ChartEstimatePoint(
        normalized_x=point.normalized_x,
        pixel_x=point.pixel_x,
        detected_pixel_y=point.detected_pixel_y,
        normalized_y=point.normalized_y,
        estimated_value=value,
        extraction_method=point.extraction_method,
    )


def _map_volume(point: ChartEstimatePoint, mapping: object | None, *, chart_y: int) -> ChartEstimatePoint:
    if mapping is None or point.detected_pixel_y is None:
        return point
    value = mapping.value_for_pixel_y(point.detected_pixel_y - chart_y)
    return ChartEstimatePoint(
        normalized_x=point.normalized_x,
        pixel_x=point.pixel_x,
        detected_pixel_y=point.detected_pixel_y,
        normalized_y=point.normalized_y,
        estimated_volume=value,
        extraction_method=point.extraction_method,
    )


def _out_of_range_errors(points: tuple[ChartEstimatePoint, ...], mapping: object | None, *, value_key: str) -> list[str]:
    if mapping is None:
        return []
    for point in points:
        value = point.estimated_value if value_key == "estimated_value" else point.estimated_volume
        if value is not None and (value < mapping.min_value or value > mapping.max_value):
            return ["chart_value_out_of_axis_range"]
    return []


def _roi_has_non_background(image: Image.Image, roi: object, image_info: ImageInfo) -> bool:
    x0, y0, width, height = roi_to_pixels(roi, image_info)
    pixels = image.load()
    sampled = 0
    non_background = 0
    step_x = max(1, width // 40)
    step_y = max(1, height // 30)
    for x in range(x0, x0 + width, step_x):
        for y in range(y0, y0 + height, step_y):
            sampled += 1
            r, g, b = pixels[x, y]
            if max(abs(r - 255), abs(g - 255), abs(b - 255)) > 20:
                non_background += 1
    return sampled > 0 and non_background / sampled > 0.01


def _empty_evidence(field_name: str) -> OcrFieldEvidence:
    return OcrFieldEvidence(field_name=field_name, raw_text="", confidence=None)


def _raw_lines(evidence: OcrFieldEvidence | None) -> tuple[str, ...]:
    if evidence is None:
        return ()
    if evidence.lines:
        return tuple(line.text for line in evidence.lines if line.text)
    return tuple(line.strip() for line in evidence.raw_text.splitlines() if line.strip())


def _has_month_hint(evidence: OcrFieldEvidence | None) -> bool:
    text = "" if evidence is None else evidence.raw_text.lower()
    return "month" in text or "30" in text or "月" in text
