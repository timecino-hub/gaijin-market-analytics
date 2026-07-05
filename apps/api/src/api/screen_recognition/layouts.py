from __future__ import annotations

from decimal import Decimal

from api.screen_recognition.contracts import ImageInfo, LayoutProfile, NormalizedRoi
from api.screen_recognition.roi import resolve_roi_pixels


class LayoutUnsupportedError(ValueError):
    pass


GAIJIN_MARKET_DESKTOP_V1 = LayoutProfile(
    name="gaijin-market-desktop-v1",
    version="1.2.0",
    min_width=900,
    min_height=600,
    min_aspect_ratio=Decimal("1.20"),
    max_aspect_ratio=Decimal("2.40"),
    rois={
        "item_name": NormalizedRoi(Decimal("0.13"), Decimal("0.015"), Decimal("0.42"), Decimal("0.085")),
        "best_bid": NormalizedRoi(Decimal("0.225"), Decimal("0.735"), Decimal("0.145"), Decimal("0.065")),
        "best_ask": NormalizedRoi(Decimal("0.745"), Decimal("0.735"), Decimal("0.145"), Decimal("0.065")),
        "total_bid_quantity": NormalizedRoi(Decimal("0.18"), Decimal("0.64"), Decimal("0.09"), Decimal("0.075")),
        "total_ask_quantity": NormalizedRoi(Decimal("0.68"), Decimal("0.64"), Decimal("0.09"), Decimal("0.075")),
        "total_bid_quantity_summary": NormalizedRoi(
            Decimal("0.10"), Decimal("0.585"), Decimal("0.25"), Decimal("0.14")
        ),
        "total_ask_quantity_summary": NormalizedRoi(
            Decimal("0.60"), Decimal("0.585"), Decimal("0.25"), Decimal("0.14")
        ),
        "bid_levels": NormalizedRoi(Decimal("0.155"), Decimal("0.72"), Decimal("0.25"), Decimal("0.23")),
        "ask_levels": NormalizedRoi(Decimal("0.695"), Decimal("0.72"), Decimal("0.25"), Decimal("0.23")),
    },
)

GAIJIN_MARKET_HISTORY_V1 = LayoutProfile(
    name="gaijin-market-history-v1",
    version="1.0.0",
    min_width=900,
    min_height=600,
    min_aspect_ratio=Decimal("1.20"),
    max_aspect_ratio=Decimal("2.40"),
    rois={
        "item_name": NormalizedRoi(Decimal("0.02"), Decimal("0.02"), Decimal("0.58"), Decimal("0.09")),
        "order_book_distribution_region": NormalizedRoi(
            Decimal("0.04"), Decimal("0.12"), Decimal("0.92"), Decimal("0.33")
        ),
        "historical_chart_region": NormalizedRoi(
            Decimal("0.08"), Decimal("0.50"), Decimal("0.84"), Decimal("0.38")
        ),
        "left_axis_labels": NormalizedRoi(Decimal("0.01"), Decimal("0.50"), Decimal("0.08"), Decimal("0.38")),
        "right_axis_labels": NormalizedRoi(Decimal("0.92"), Decimal("0.50"), Decimal("0.07"), Decimal("0.38")),
        "time_axis_labels": NormalizedRoi(Decimal("0.08"), Decimal("0.88"), Decimal("0.84"), Decimal("0.09")),
        "red_price_plot": NormalizedRoi(Decimal("0.08"), Decimal("0.50"), Decimal("0.84"), Decimal("0.38")),
        "blue_volume_plot": NormalizedRoi(Decimal("0.08"), Decimal("0.50"), Decimal("0.84"), Decimal("0.38")),
        "legend": NormalizedRoi(Decimal("0.70"), Decimal("0.46"), Decimal("0.22"), Decimal("0.08")),
    },
)

LAYOUT_PROFILES = {
    GAIJIN_MARKET_DESKTOP_V1.name: GAIJIN_MARKET_DESKTOP_V1,
    GAIJIN_MARKET_HISTORY_V1.name: GAIJIN_MARKET_HISTORY_V1,
}


def get_layout_profile(name: str) -> LayoutProfile:
    try:
        return LAYOUT_PROFILES[name]
    except KeyError as exc:
        raise LayoutUnsupportedError(f"Unknown layout profile: {name}") from exc


def validate_layout_match(profile: LayoutProfile, image_info: ImageInfo) -> None:
    if image_info.width < profile.min_width or image_info.height < profile.min_height:
        raise LayoutUnsupportedError("Image resolution is below the layout profile minimum.")
    aspect_ratio = Decimal(image_info.width) / Decimal(image_info.height)
    if aspect_ratio < profile.min_aspect_ratio or aspect_ratio > profile.max_aspect_ratio:
        raise LayoutUnsupportedError("Image aspect ratio does not match the layout profile.")


def roi_to_pixels(roi: NormalizedRoi, image_info: ImageInfo) -> tuple[int, int, int, int]:
    return resolve_roi_pixels(roi, image_info).as_tuple()
