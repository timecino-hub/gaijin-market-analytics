from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

from api.screen_recognition.roi import PixelRoi


PRICE_CELL_PROFILE_VERSION = "button-anchored-price-cells-v2"


class PriceCellDetectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _Box:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def to_json(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class PriceCellDetection:
    rois: dict[str, PixelRoi]
    diagnostics: dict[str, Any]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _SideDetection:
    button: _Box
    summary_band: tuple[int, int]
    header_band: tuple[int, int]
    first_row_band: tuple[int, int]
    summary_groups: tuple[tuple[int, int], ...]
    first_row_groups: tuple[tuple[int, int], ...]
    summary_price: PixelRoi
    first_row_price: PixelRoi
    summary_leading_fragment_merged: bool


def detect_price_cell_rois(image_path: Path) -> PriceCellDetection:
    """Locate compact bid/ask price cells from the two large action buttons.

    The detector is deliberately geometric and deterministic. It does not OCR the
    screenshot and does not use expected prices. The red buy button provides the
    primary anchor; the gray sell button must align with it. Text-row projections
    below each button then isolate the summary price and the first order-book row.
    """

    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
    bid_button, ask_button = _detect_action_buttons(image)
    text_mask = _neutral_text_mask(image)
    bid = _detect_side(text_mask, bid_button, image.size, side="bid")
    ask = _detect_side(text_mask, ask_button, image.size, side="ask")

    rois = {
        "best_bid": bid.summary_price,
        "bid_levels": bid.first_row_price,
        "best_ask": ask.summary_price,
        "ask_levels": ask.first_row_price,
    }
    diagnostics = {
        "profile_version": PRICE_CELL_PROFILE_VERSION,
        "anchor_detection": "button_fill_color",
        "row_detection": "neutral_text_projection",
        "price_group_selection": {
            "summary": "third_group_from_right",
            "first_level": "rightmost_group",
        },
        "anchors": {
            "bid_button": bid.button.to_json(),
            "ask_button": ask.button.to_json(),
        },
        "sides": {
            "bid": _side_diagnostics(bid),
            "ask": _side_diagnostics(ask),
        },
        "rois": {name: roi.to_json() for name, roi in sorted(rois.items())},
        "fallback_used": False,
    }
    return PriceCellDetection(rois=rois, diagnostics=diagnostics)


def _side_diagnostics(value: _SideDetection) -> dict[str, Any]:
    return {
        "summary_band": list(value.summary_band),
        "header_band": list(value.header_band),
        "first_row_band": list(value.first_row_band),
        "summary_group_count": len(value.summary_groups),
        "first_row_group_count": len(value.first_row_groups),
        "summary_price_roi": value.summary_price.to_json(),
        "first_row_price_roi": value.first_row_price.to_json(),
        "summary_leading_fragment_merged": value.summary_leading_fragment_merged,
    }


def _detect_action_buttons(image: Image.Image) -> tuple[_Box, _Box]:
    width, height = image.size
    red_mask = _color_range_mask(
        image,
        red=(175, 210),
        green=(30, 65),
        blue=(35, 75),
    )
    red_button = _largest_button_band(
        red_mask,
        x0=0,
        x1=width,
        y0=0,
        y1=height,
        min_row_pixels=max(80, width // 20),
        code="buy_button_not_detected",
    )

    gray_mask = _gray_button_mask(image)
    vertical_margin = max(4, red_button.height // 4)
    gray_button = _largest_button_band(
        gray_mask,
        x0=0,
        x1=max(1, red_button.x),
        y0=max(0, red_button.y - vertical_margin),
        y1=min(height, red_button.bottom + vertical_margin),
        min_row_pixels=max(80, width // 20),
        code="sell_button_not_detected",
    )

    if gray_button.right >= red_button.x:
        raise PriceCellDetectionError("button_geometry_invalid", "Action buttons overlap or are reversed.")
    if abs(_center_y(gray_button) - _center_y(red_button)) > max(gray_button.height, red_button.height) // 3:
        raise PriceCellDetectionError("button_geometry_invalid", "Action buttons are not vertically aligned.")
    if not _ratio_close(gray_button.width, red_button.width, tolerance=0.30):
        raise PriceCellDetectionError("button_geometry_invalid", "Action button widths are inconsistent.")
    if not _ratio_close(gray_button.height, red_button.height, tolerance=0.30):
        raise PriceCellDetectionError("button_geometry_invalid", "Action button heights are inconsistent.")
    return gray_button, red_button


def _detect_side(
    text_mask: Image.Image,
    button: _Box,
    image_size: tuple[int, int],
    *,
    side: str,
) -> _SideDetection:
    image_width, image_height = image_size
    x_margin = round(button.width * 0.15)
    x0 = max(0, button.x - x_margin)
    x1 = min(image_width, button.right + x_margin)
    y0 = min(image_height, button.bottom + max(2, round(button.height * 0.05)))
    y1 = min(image_height, button.bottom + round(button.height * 4.0))
    row_bands = _projection_bands(
        text_mask,
        x0=x0,
        x1=x1,
        y0=y0,
        y1=y1,
        axis="row",
        min_count=max(4, round(button.width * 0.006)),
        max_gap=max(1, round(button.height * 0.02)),
    )
    min_band_height = max(3, round(button.height * 0.08))
    max_band_height = max(min_band_height, round(button.height * 0.45))
    row_bands = tuple(
        band for band in row_bands if min_band_height <= band[1] - band[0] <= max_band_height
    )
    if len(row_bands) < 3:
        raise PriceCellDetectionError(
            f"{side}_price_rows_not_detected",
            f"Expected summary, header, and first price row below the {side} button.",
        )
    summary_band, header_band, first_row_band = row_bands[:3]
    merge_gap = max(3, round(button.height * 0.05))
    summary_groups = _column_groups(
        text_mask,
        x0=x0,
        x1=x1,
        y0=summary_band[0],
        y1=summary_band[1],
        max_gap=merge_gap,
    )
    first_row_groups = _column_groups(
        text_mask,
        x0=x0,
        x1=x1,
        y0=first_row_band[0],
        y1=first_row_band[1],
        max_gap=merge_gap,
    )
    if len(summary_groups) < 3:
        raise PriceCellDetectionError(
            f"{side}_summary_price_not_detected",
            f"The {side} summary row does not contain enough separated text groups.",
        )
    if len(first_row_groups) < 2:
        raise PriceCellDetectionError(
            f"{side}_first_level_price_not_detected",
            f"The {side} first level row does not contain quantity and price groups.",
        )

    summary_group = summary_groups[-3]
    summary_leading_fragment_merged = False
    if len(summary_groups) >= 4:
        previous_group = summary_groups[-4]
        if _should_merge_summary_leading_fragment(previous_group, summary_group, button):
            summary_group = (previous_group[0], summary_group[1])
            summary_leading_fragment_merged = True
    first_row_group = first_row_groups[-1]
    _validate_price_group(summary_group, button, side=side, source="summary")
    _validate_price_group(first_row_group, button, side=side, source="first_level")

    pad_x = max(4, round(button.height * 0.08))
    pad_y = max(3, round(button.height * 0.08))
    summary_price = _padded_roi(
        summary_group,
        summary_band,
        pad_x=pad_x,
        pad_y=pad_y,
        image_width=image_width,
        image_height=image_height,
    )
    first_row_price = _padded_roi(
        first_row_group,
        first_row_band,
        pad_x=pad_x,
        pad_y=pad_y,
        image_width=image_width,
        image_height=image_height,
    )
    return _SideDetection(
        button=button,
        summary_band=summary_band,
        header_band=header_band,
        first_row_band=first_row_band,
        summary_groups=summary_groups,
        first_row_groups=first_row_groups,
        summary_price=summary_price,
        first_row_price=first_row_price,
        summary_leading_fragment_merged=summary_leading_fragment_merged,
    )


def _should_merge_summary_leading_fragment(
    previous_group: tuple[int, int],
    price_group: tuple[int, int],
    button: _Box,
) -> bool:
    """Merge a detached leading digit without absorbing the preceding label.

    At some 90% browser zooms the narrow glyph ``1`` is separated from the
    remaining price by the projection grouping.  Normal label/currency groups
    are materially wider, so the merge stays bounded to a small fragment with a
    small inter-group gap.
    """

    fragment_width = previous_group[1] - previous_group[0]
    gap = price_group[0] - previous_group[1]
    maximum_fragment_width = max(8, round(button.height * 0.11))
    maximum_gap = max(5, round(button.height * 0.07))
    return 2 <= fragment_width <= maximum_fragment_width and 0 <= gap <= maximum_gap


def _validate_price_group(
    group: tuple[int, int],
    button: _Box,
    *,
    side: str,
    source: str,
) -> None:
    group_width = group[1] - group[0]
    center = (group[0] + group[1]) / 2
    relative_center = (center - button.x) / button.width
    if group_width < max(8, round(button.width * 0.08)):
        raise PriceCellDetectionError(
            f"{side}_{source}_price_group_too_narrow",
            f"The detected {side} {source} price group is too narrow.",
        )
    if not 0.30 <= relative_center <= 0.90:
        raise PriceCellDetectionError(
            f"{side}_{source}_price_group_outside_column",
            f"The detected {side} {source} price group is outside the expected price column.",
        )


def _largest_button_band(
    mask: Image.Image,
    *,
    x0: int,
    x1: int,
    y0: int,
    y1: int,
    min_row_pixels: int,
    code: str,
) -> _Box:
    bands = _projection_bands(
        mask,
        x0=x0,
        x1=x1,
        y0=y0,
        y1=y1,
        axis="row",
        min_count=min_row_pixels,
        max_gap=1,
    )
    candidates: list[_Box] = []
    for band in bands:
        column_bands = _projection_bands(
            mask,
            x0=x0,
            x1=x1,
            y0=band[0],
            y1=band[1],
            axis="column",
            min_count=max(5, (band[1] - band[0]) // 3),
            max_gap=2,
        )
        if not column_bands:
            continue
        column_band = max(column_bands, key=lambda value: value[1] - value[0])
        local = mask.crop((column_band[0], band[0], column_band[1], band[1])).getbbox()
        if local is None:
            continue
        candidate = _Box(
            x=column_band[0] + local[0],
            y=band[0] + local[1],
            width=local[2] - local[0],
            height=local[3] - local[1],
        )
        if candidate.width >= max(80, mask.width // 10) and candidate.height >= 20:
            candidates.append(candidate)
    if not candidates:
        raise PriceCellDetectionError(code, "The expected action button color block was not found.")
    return max(candidates, key=lambda value: value.width * value.height)


def _projection_bands(
    mask: Image.Image,
    *,
    x0: int,
    x1: int,
    y0: int,
    y1: int,
    axis: str,
    min_count: int,
    max_gap: int,
) -> tuple[tuple[int, int], ...]:
    crop = mask.crop((x0, y0, x1, y1))
    width, height = crop.size
    data = crop.tobytes()
    if axis == "row":
        counts = [data[index * width : (index + 1) * width].count(255) for index in range(height)]
        offset = y0
    elif axis == "column":
        counts = [0] * width
        for row in range(height):
            row_data = data[row * width : (row + 1) * width]
            for column, value in enumerate(row_data):
                if value == 255:
                    counts[column] += 1
        offset = x0
    else:
        raise ValueError(f"Unsupported projection axis: {axis}")
    active = [count >= min_count for count in counts]
    return _active_bands(active, offset=offset, max_gap=max_gap)


def _column_groups(
    mask: Image.Image,
    *,
    x0: int,
    x1: int,
    y0: int,
    y1: int,
    max_gap: int,
) -> tuple[tuple[int, int], ...]:
    raw = _projection_bands(
        mask,
        x0=x0,
        x1=x1,
        y0=y0,
        y1=y1,
        axis="column",
        min_count=1,
        max_gap=max_gap,
    )
    return tuple(group for group in raw if group[1] - group[0] >= 2)


def _active_bands(
    active: list[bool],
    *,
    offset: int,
    max_gap: int,
) -> tuple[tuple[int, int], ...]:
    bands: list[tuple[int, int]] = []
    start: int | None = None
    last_active: int | None = None
    for index, is_active in enumerate(active):
        if is_active:
            if start is None:
                start = index
            last_active = index
            continue
        if start is not None and last_active is not None and index - last_active > max_gap:
            bands.append((offset + start, offset + last_active + 1))
            start = None
            last_active = None
    if start is not None and last_active is not None:
        bands.append((offset + start, offset + last_active + 1))
    return tuple(bands)


def _padded_roi(
    group: tuple[int, int],
    band: tuple[int, int],
    *,
    pad_x: int,
    pad_y: int,
    image_width: int,
    image_height: int,
) -> PixelRoi:
    left = max(0, group[0] - pad_x)
    top = max(0, band[0] - pad_y)
    right = min(image_width, group[1] + pad_x)
    bottom = min(image_height, band[1] + pad_y)
    if right - left < 4 or bottom - top < 4:
        raise PriceCellDetectionError("price_cell_too_small", "The detected price cell is too small.")
    return PixelRoi(x=left, y=top, width=right - left, height=bottom - top)


def _color_range_mask(
    image: Image.Image,
    *,
    red: tuple[int, int],
    green: tuple[int, int],
    blue: tuple[int, int],
) -> Image.Image:
    r, g, b = image.split()
    return _combine_masks(
        _channel_range_mask(r, red[0], red[1]),
        _channel_range_mask(g, green[0], green[1]),
        _channel_range_mask(b, blue[0], blue[1]),
    )


def _gray_button_mask(image: Image.Image) -> Image.Image:
    r, g, b = image.split()
    return _combine_masks(
        _channel_range_mask(r, 58, 82),
        _channel_range_mask(g, 60, 85),
        _channel_range_mask(b, 64, 90),
        _difference_mask(r, g, 12),
        _difference_mask(g, b, 12),
        _difference_mask(r, b, 18),
    )


def _neutral_text_mask(image: Image.Image) -> Image.Image:
    r, g, b = image.split()
    return _combine_masks(
        _channel_range_mask(r, 85, 255),
        _channel_range_mask(g, 85, 255),
        _channel_range_mask(b, 85, 255),
        _difference_mask(r, g, 50),
        _difference_mask(g, b, 50),
        _difference_mask(r, b, 50),
    )


def _channel_range_mask(channel: Image.Image, lower: int, upper: int) -> Image.Image:
    table = [255 if lower <= value <= upper else 0 for value in range(256)]
    return channel.point(table, mode="L")


def _difference_mask(left: Image.Image, right: Image.Image, maximum: int) -> Image.Image:
    difference = ImageChops.difference(left, right)
    table = [255 if value <= maximum else 0 for value in range(256)]
    return difference.point(table, mode="L")


def _combine_masks(*masks: Image.Image) -> Image.Image:
    if not masks:
        raise ValueError("At least one mask is required.")
    combined = masks[0]
    for mask in masks[1:]:
        combined = ImageChops.multiply(combined, mask)
    return combined


def _center_y(value: _Box) -> int:
    return value.y + value.height // 2


def _ratio_close(left: int, right: int, *, tolerance: float) -> bool:
    maximum = max(left, right)
    if maximum <= 0:
        return False
    return abs(left - right) / maximum <= tolerance
