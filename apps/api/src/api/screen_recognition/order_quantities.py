from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from api.screen_recognition.price_cells import (
    PriceCellDetectionError,
    _detect_action_buttons,
    _detect_side,
    _neutral_text_mask,
)
from api.screen_recognition.roi import PixelRoi


ORDER_QUANTITY_PROFILE_VERSION_V1 = "button-anchored-order-quantities-v1"
ORDER_QUANTITY_PROFILE_VERSION_V2 = "button-anchored-order-quantities-v2"


class OrderQuantityDetectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _QuantitySideDetection:
    summary_quantity: PixelRoi
    summary_quantity_group: tuple[int, int]
    summary_line: PixelRoi
    first_row_quantity: PixelRoi
    first_row_quantity_group: tuple[int, int]


@dataclass(frozen=True)
class OrderQuantityDetection:
    rois: dict[str, PixelRoi]
    diagnostics: dict[str, Any]
    warnings: tuple[str, ...] = ()


def detect_order_quantity_rois_v1(image_path: Path) -> OrderQuantityDetection:
    """Locate reviewed order-count quantity cells from the existing button anchors.

    This detector deliberately mirrors the price-cell geometry rather than using a
    fixed normalized ROI.  It extracts only the compact total count in the summary
    sentence (for example ``正在购买: 42 从 4.22 更低``) and the first row quantity
    for diagnostics.  The parser still treats these quantities as Review-only
    evidence in the corresponding candidate backend.
    """

    return _detect_order_quantity_rois(
        image_path,
        profile_version=ORDER_QUANTITY_PROFILE_VERSION_V1,
        include_summary_line_rois=False,
        compact_pad_x_scale=0.06,
        compact_pad_y_scale=0.08,
    )


def detect_order_quantity_rois_v2(image_path: Path) -> OrderQuantityDetection:
    """Locate reviewed order-count quantity cells with a label-anchored fallback.

    v1 proved the geometry safe but missed several small one- and two-digit
    quantities when Windows OCR saw a narrow compact digit crop as blank.  v2
    keeps the same compact quantity cells, makes their padding less tight, and
    also OCRs the full summary sentence under each action button.  The parser can
    then use the explicit ``正在购买`` / ``正在出售`` label as a second reviewed
    source while continuing to fail closed on conflicts.
    """

    return _detect_order_quantity_rois(
        image_path,
        profile_version=ORDER_QUANTITY_PROFILE_VERSION_V2,
        include_summary_line_rois=True,
        compact_pad_x_scale=0.32,
        compact_pad_y_scale=0.20,
    )


def _detect_order_quantity_rois(
    image_path: Path,
    *,
    profile_version: str,
    include_summary_line_rois: bool,
    compact_pad_x_scale: float,
    compact_pad_y_scale: float,
) -> OrderQuantityDetection:
    try:
        with Image.open(image_path) as opened:
            image = opened.convert("RGB")
        bid_button, ask_button = _detect_action_buttons(
            image,
            allow_active_sell_button=True,
        )
        text_mask = _neutral_text_mask(image)
        bid_price_side = _detect_side(
            text_mask,
            bid_button,
            image.size,
            side="bid",
            merge_fragmented_summary=True,
            expanded_leading_padding=True,
        )
        ask_price_side = _detect_side(
            text_mask,
            ask_button,
            image.size,
            side="ask",
            merge_fragmented_summary=True,
            expanded_leading_padding=True,
        )
    except PriceCellDetectionError as exc:
        raise OrderQuantityDetectionError(exc.code, exc.message) from exc

    bid = _detect_quantity_side(
        bid_price_side,
        side="bid",
        image_width=image.width,
        image_height=image.height,
        compact_pad_x_scale=compact_pad_x_scale,
        compact_pad_y_scale=compact_pad_y_scale,
    )
    ask = _detect_quantity_side(
        ask_price_side,
        side="ask",
        image_width=image.width,
        image_height=image.height,
        compact_pad_x_scale=compact_pad_x_scale,
        compact_pad_y_scale=compact_pad_y_scale,
    )
    rois = {
        "total_bid_quantity": bid.summary_quantity,
        "total_ask_quantity": ask.summary_quantity,
    }
    if include_summary_line_rois:
        rois.update(
            {
                "total_bid_quantity_summary": bid.summary_line,
                "total_ask_quantity_summary": ask.summary_line,
            }
        )
    diagnostics = {
        "profile_version": profile_version,
        "anchor_detection": "button_fill_color_gray_or_active_green",
        "quantity_group_selection": {
            "summary": "numeric_group_before_connector_and_price",
            "summary_line": "full_label_anchored_sentence_roi",
            "first_level": "left_group_before_first_level_price",
        },
        "compact_padding": {
            "x_scale_from_button_height": compact_pad_x_scale,
            "y_scale_from_button_height": compact_pad_y_scale,
        },
        "sides": {
            "bid": _side_diagnostics(bid),
            "ask": _side_diagnostics(ask),
        },
        "rois": {name: roi.to_json() for name, roi in sorted(rois.items())},
        "fallback_used": False,
    }
    return OrderQuantityDetection(rois=rois, diagnostics=diagnostics)


def _detect_quantity_side(
    price_side: Any,
    *,
    side: str,
    image_width: int,
    image_height: int,
    compact_pad_x_scale: float,
    compact_pad_y_scale: float,
) -> _QuantitySideDetection:
    summary_quantity_group, summary_price_start = _summary_quantity_group(
        price_side, side=side
    )
    first_row_quantity_group = _first_row_quantity_group(price_side, side=side)
    compact_pad_x = max(3, round(price_side.button.height * compact_pad_x_scale))
    compact_pad_y = max(3, round(price_side.button.height * compact_pad_y_scale))
    return _QuantitySideDetection(
        summary_quantity=_padded_summary_quantity_roi(
            summary_quantity_group,
            price_side.summary_band,
            price_start=summary_price_start,
            pad_x=compact_pad_x,
            pad_y=compact_pad_y,
            image_width=image_width,
            image_height=image_height,
            error_code=f"{side}_summary_quantity_too_small",
        ),
        summary_quantity_group=summary_quantity_group,
        summary_line=_summary_line_roi(
            price_side,
            image_width=image_width,
            image_height=image_height,
        ),
        first_row_quantity=_padded_group_roi(
            first_row_quantity_group,
            price_side.first_row_band,
            pad_x=compact_pad_x,
            pad_y=compact_pad_y,
            image_width=image_width,
            image_height=image_height,
            error_code=f"{side}_first_row_quantity_too_small",
        ),
        first_row_quantity_group=first_row_quantity_group,
    )


def _summary_quantity_group(
    price_side: Any, *, side: str
) -> tuple[tuple[int, int], int]:
    groups = tuple(price_side.summary_groups)
    if len(groups) < 5:
        raise OrderQuantityDetectionError(
            f"{side}_summary_quantity_group_not_detected",
            f"The {side} summary row does not contain enough text groups.",
        )
    price_roi = price_side.summary_price
    price_left = price_roi.x
    price_right = price_roi.x + price_roi.width
    price_groups = [
        group
        for group in groups
        if price_left <= (group[0] + group[1]) / 2 <= price_right
    ]
    if not price_groups:
        raise OrderQuantityDetectionError(
            f"{side}_summary_price_group_not_aligned",
            f"The {side} summary price ROI does not align with text groups.",
        )
    price_start = min(group[0] for group in price_groups)
    preceding = [group for group in groups if group[1] <= price_start]
    if len(preceding) < 2:
        raise OrderQuantityDetectionError(
            f"{side}_summary_quantity_group_not_detected",
            f"The {side} summary quantity group was not found before the price.",
        )
    quantity_group = preceding[-2]
    _validate_quantity_group(quantity_group, price_side.button, side=side, source="summary")
    return quantity_group, price_start


def _first_row_quantity_group(price_side: Any, *, side: str) -> tuple[int, int]:
    groups = tuple(price_side.first_row_groups)
    if len(groups) < 2:
        raise OrderQuantityDetectionError(
            f"{side}_first_row_quantity_group_not_detected",
            f"The {side} first order row does not contain quantity and price groups.",
        )
    quantity_group = groups[-2]
    _validate_quantity_group(quantity_group, price_side.button, side=side, source="first_row")
    return quantity_group


def _validate_quantity_group(
    group: tuple[int, int],
    button: Any,
    *,
    side: str,
    source: str,
) -> None:
    width = group[1] - group[0]
    center = (group[0] + group[1]) / 2
    relative_center = (center - button.x) / button.width
    if width < max(4, round(button.width * 0.012)):
        raise OrderQuantityDetectionError(
            f"{side}_{source}_quantity_group_too_narrow",
            f"The detected {side} {source} quantity group is too narrow.",
        )
    if not 0.20 <= relative_center <= 0.62:
        raise OrderQuantityDetectionError(
            f"{side}_{source}_quantity_group_outside_column",
            f"The detected {side} {source} quantity group is outside the expected quantity column.",
        )


def _padded_summary_quantity_roi(
    group: tuple[int, int],
    band: tuple[int, int],
    *,
    price_start: int,
    pad_x: int,
    pad_y: int,
    image_width: int,
    image_height: int,
    error_code: str,
) -> PixelRoi:
    # Give Windows OCR a little phrase context (for example ``15 从`` or
    # ``81 为``) but clamp before the summary price.  Narrow digit-only crops
    # are frequently returned as blank for one- and two-digit quantities.
    left = max(0, group[0] - pad_x)
    top = max(0, band[0] - pad_y)
    right = min(image_width, max(group[1] + pad_x, price_start - 1))
    if price_start > group[1]:
        right = min(right, price_start - 1)
    bottom = min(image_height, band[1] + pad_y)
    if right - left < 4 or bottom - top < 4:
        raise OrderQuantityDetectionError(error_code, "The detected quantity cell is too small.")
    return PixelRoi(x=left, y=top, width=right - left, height=bottom - top)


def _padded_group_roi(
    group: tuple[int, int],
    band: tuple[int, int],
    *,
    pad_x: int,
    pad_y: int,
    image_width: int,
    image_height: int,
    error_code: str,
) -> PixelRoi:
    left = max(0, group[0] - pad_x)
    top = max(0, band[0] - pad_y)
    right = min(image_width, group[1] + pad_x)
    bottom = min(image_height, band[1] + pad_y)
    if right - left < 4 or bottom - top < 4:
        raise OrderQuantityDetectionError(error_code, "The detected quantity cell is too small.")
    return PixelRoi(x=left, y=top, width=right - left, height=bottom - top)


def _summary_line_roi(
    price_side: Any,
    *,
    image_width: int,
    image_height: int,
) -> PixelRoi:
    x_margin = round(price_side.button.width * 0.15)
    x0 = max(0, price_side.button.x - x_margin)
    x1 = min(image_width, price_side.button.right + x_margin)
    pad_y = max(4, round(price_side.button.height * 0.12))
    top = max(0, price_side.summary_band[0] - pad_y)
    bottom = min(image_height, price_side.summary_band[1] + pad_y)
    if x1 - x0 < 16 or bottom - top < 8:
        raise OrderQuantityDetectionError(
            "summary_line_quantity_too_small",
            "The detected quantity summary line is too small.",
        )
    return PixelRoi(x=x0, y=top, width=x1 - x0, height=bottom - top)


def _side_diagnostics(value: _QuantitySideDetection) -> dict[str, Any]:
    return {
        "summary_quantity_group": list(value.summary_quantity_group),
        "summary_quantity_roi": value.summary_quantity.to_json(),
        "summary_line_roi": value.summary_line.to_json(),
        "first_row_quantity_group": list(value.first_row_quantity_group),
        "first_row_quantity_roi": value.first_row_quantity.to_json(),
    }
