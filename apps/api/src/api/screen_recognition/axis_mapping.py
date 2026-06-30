from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from api.screen_recognition.contracts import OcrFieldEvidence
from api.screen_recognition.history_contracts import AxisMapping


NUMBER_RE = re.compile(r"-?\d+(?:[\.,]\d+)?")


class AxisMappingError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_axis_mapping(
    evidence: OcrFieldEvidence,
    *,
    axis_name: str,
    axis_pixel_height: int,
    max_residual_px: Decimal,
    require_non_negative: bool,
) -> AxisMapping:
    ticks = _extract_ticks(evidence, axis_pixel_height=axis_pixel_height)
    if len(ticks) < 2:
        raise AxisMappingError(f"{axis_name}_axis_unreadable", "At least two tick labels are required.")
    values = [value for value, _pixel_y in ticks]
    if len(set(values)) != len(values):
        raise AxisMappingError(f"{axis_name}_axis_unreadable", "Duplicate tick labels are ambiguous.")
    if require_non_negative and any(value < 0 for value in values):
        raise AxisMappingError(f"{axis_name}_axis_unreadable", "Volume axis labels must be non-negative.")
    sorted_by_y = sorted(ticks, key=lambda tick: tick[1])
    ordered_values = [value for value, _pixel_y in sorted_by_y]
    increasing = all(left < right for left, right in zip(ordered_values, ordered_values[1:]))
    decreasing = all(left > right for left, right in zip(ordered_values, ordered_values[1:]))
    if not increasing and not decreasing:
        raise AxisMappingError(f"{axis_name}_axis_unreadable", "Tick values are not monotonic by y position.")

    n = Decimal(len(ticks))
    sum_y = sum(Decimal(pixel_y) for _value, pixel_y in ticks)
    sum_value = sum(value for value, _pixel_y in ticks)
    sum_y_value = sum(Decimal(pixel_y) * value for value, pixel_y in ticks)
    sum_y_squared = sum(Decimal(pixel_y) * Decimal(pixel_y) for _value, pixel_y in ticks)
    denominator = n * sum_y_squared - sum_y * sum_y
    if denominator == 0:
        raise AxisMappingError(f"{axis_name}_axis_unreadable", "Tick y positions are degenerate.")
    slope = (n * sum_y_value - sum_y * sum_value) / denominator
    intercept = (sum_value - slope * sum_y) / n
    max_residual = Decimal("0")
    if len(ticks) > 2:
        inverse_slope = Decimal("1") / slope if slope != 0 else None
        if inverse_slope is None:
            raise AxisMappingError(f"{axis_name}_axis_unreadable", "Axis slope is zero.")
        for value, pixel_y in ticks:
            predicted_y = (value - intercept) * inverse_slope
            residual = abs(predicted_y - Decimal(pixel_y))
            max_residual = max(max_residual, residual)
        if max_residual > max_residual_px:
            raise AxisMappingError("chart_numeric_mapping_unavailable", "Axis mapping residual is too large.")
    return AxisMapping(
        axis_name=axis_name,
        slope=slope,
        intercept=intercept,
        min_value=min(values),
        max_value=max(values),
        tick_count=len(ticks),
        max_residual_px=max_residual,
    )


def _extract_ticks(
    evidence: OcrFieldEvidence, *, axis_pixel_height: int
) -> list[tuple[Decimal, int]]:
    ticks: list[tuple[Decimal, int]] = []
    for line in evidence.lines:
        match = NUMBER_RE.search(line.text.replace(",", "."))
        if match is None or line.bounding_box is None:
            continue
        try:
            value = Decimal(match.group(0))
        except InvalidOperation:
            continue
        center_y = line.bounding_box.y + line.bounding_box.height / Decimal("2")
        if evidence.bounding_box is not None and evidence.bounding_box.height > 0:
            normalized_y = center_y / evidence.bounding_box.height
            pixel_y = int((normalized_y * Decimal(axis_pixel_height)).to_integral_value())
        else:
            pixel_y = int(center_y.to_integral_value())
        ticks.append((value, pixel_y))
    return ticks
