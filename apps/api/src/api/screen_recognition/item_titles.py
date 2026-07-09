from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from api.screen_recognition.price_cells import (
    PriceCellDetectionError,
    _column_groups,
    _detect_action_buttons,
    _neutral_text_mask,
    _projection_bands,
)
from api.screen_recognition.roi import PixelRoi


ITEM_TITLE_PROFILE_VERSION_V2 = "button-anchored-item-title-v2"


class ItemTitleDetectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ItemTitleDetection:
    roi: PixelRoi
    diagnostics: dict[str, Any]
    warnings: tuple[str, ...] = ()


def detect_item_title_roi_v2(image_path: Path) -> ItemTitleDetection:
    """Locate only the item-name text inside the market title bar.

    The detector reuses the already validated action-button geometry to infer the
    title-band scale and vertical position. Within that band it identifies the
    stable sequence ``icon | War Thunder | item title | link icon`` and crops only
    the item-title columns. No OCR text, market slug, or expected item name is used
    to locate the region.
    """

    with Image.open(image_path) as opened:
        image = opened.convert("RGB")

    try:
        bid_button, ask_button = _detect_action_buttons(
            image,
            allow_active_sell_button=True,
        )
    except PriceCellDetectionError as exc:
        raise ItemTitleDetectionError(
            f"item_title_{exc.code}",
            "Action-button anchors required for item-title detection were not found.",
        ) from exc

    image_width, image_height = image.size
    button_width = bid_button.width
    button_height = bid_button.height

    search_x0 = max(0, round(bid_button.x - button_width * 0.45))
    search_x1 = min(image_width, round(bid_button.x + button_width * 2.30))
    search_y0 = max(0, round(bid_button.y - button_height * 5.50))
    search_y1 = min(image_height, round(bid_button.y - button_height * 4.10))
    if search_x1 <= search_x0 or search_y1 <= search_y0:
        raise ItemTitleDetectionError(
            "item_title_search_region_invalid",
            "The inferred title search region is empty.",
        )

    text_mask = _neutral_text_mask(image)
    row_bands = _projection_bands(
        text_mask,
        x0=search_x0,
        x1=search_x1,
        y0=search_y0,
        y1=search_y1,
        axis="row",
        min_count=max(4, round(button_width * 0.008)),
        max_gap=max(1, round(button_height * 0.03)),
    )
    min_band_height = max(3, round(button_height * 0.18))
    max_band_height = max(min_band_height, round(button_height * 0.65))
    plausible_bands = tuple(
        band
        for band in row_bands
        if min_band_height <= band[1] - band[0] <= max_band_height
    )
    if not plausible_bands:
        raise ItemTitleDetectionError(
            "item_title_row_not_detected",
            "No plausible title text row was found above the action buttons.",
        )

    expected_center = bid_button.y - button_height * 4.75
    selected_band = min(
        plausible_bands,
        key=lambda band: abs(((band[0] + band[1]) / 2) - expected_center),
    )
    if abs(((selected_band[0] + selected_band[1]) / 2) - expected_center) > button_height * 0.80:
        raise ItemTitleDetectionError(
            "item_title_row_geometry_invalid",
            "The detected title row is too far from the expected button-relative position.",
        )

    groups = _column_groups(
        text_mask,
        x0=search_x0,
        x1=search_x1,
        y0=selected_band[0],
        y1=selected_band[1],
        max_gap=max(2, round(button_height * 0.08)),
    )
    if len(groups) < 4:
        raise ItemTitleDetectionError(
            "item_title_groups_not_detected",
            "Expected icon, game label, item title, and link-icon groups in the title row.",
        )

    # The first two groups are the game icon and the stable "War Thunder" label;
    # the last group is the link icon. Everything in between is the item title.
    title_start = groups[2][0]
    title_end = groups[-2][1]
    title_width = title_end - title_start
    if title_width < max(12, round(button_height * 0.75)):
        raise ItemTitleDetectionError(
            "item_title_group_too_narrow",
            "The inferred item-title text span is too narrow.",
        )
    if title_width > round(button_width * 1.65):
        raise ItemTitleDetectionError(
            "item_title_group_too_wide",
            "The inferred item-title text span is implausibly wide.",
        )

    desired_pad_x = max(3, round(button_height * 0.06))
    left_gap = max(0, title_start - groups[1][1])
    right_gap = max(0, groups[-1][0] - title_end)
    pad_left = min(desired_pad_x, max(2, left_gap // 2))
    pad_right = min(desired_pad_x, max(2, right_gap // 2))
    pad_y = max(3, round(button_height * 0.08))

    x0 = max(0, title_start - pad_left)
    y0 = max(0, selected_band[0] - pad_y)
    x1 = min(image_width, title_end + pad_right)
    y1 = min(image_height, selected_band[1] + pad_y)
    if x1 <= x0 or y1 <= y0:
        raise ItemTitleDetectionError(
            "item_title_roi_invalid",
            "The detected item-title ROI is empty.",
        )

    roi = PixelRoi(x=x0, y=y0, width=x1 - x0, height=y1 - y0)
    diagnostics = {
        "profile_version": ITEM_TITLE_PROFILE_VERSION_V2,
        "anchor_detection": "price_cell_action_buttons_v4",
        "row_detection": "neutral_text_projection",
        "group_selection": "between_game_label_and_link_icon",
        "anchors": {
            "bid_button": bid_button.to_json(),
            "ask_button": ask_button.to_json(),
        },
        "search_region": {
            "x": search_x0,
            "y": search_y0,
            "width": search_x1 - search_x0,
            "height": search_y1 - search_y0,
        },
        "row_bands": [list(band) for band in row_bands],
        "plausible_row_bands": [list(band) for band in plausible_bands],
        "selected_row_band": list(selected_band),
        "column_groups": [list(group) for group in groups],
        "title_group_count": max(0, len(groups) - 3),
        "roi": roi.to_json(),
        "fallback_used": False,
    }
    return ItemTitleDetection(roi=roi, diagnostics=diagnostics)
