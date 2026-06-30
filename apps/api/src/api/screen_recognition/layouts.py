from __future__ import annotations

from decimal import Decimal

from api.screen_recognition.contracts import ImageInfo, LayoutProfile, NormalizedRoi


class LayoutUnsupportedError(ValueError):
    pass


GAIJIN_MARKET_DESKTOP_V1 = LayoutProfile(
    name="gaijin-market-desktop-v1",
    version="1.0.0",
    min_width=900,
    min_height=600,
    min_aspect_ratio=Decimal("1.20"),
    max_aspect_ratio=Decimal("2.40"),
    rois={
        "item_name": NormalizedRoi(Decimal("0.02"), Decimal("0.02"), Decimal("0.58"), Decimal("0.10")),
        "best_bid": NormalizedRoi(Decimal("0.04"), Decimal("0.15"), Decimal("0.20"), Decimal("0.11")),
        "best_ask": NormalizedRoi(Decimal("0.25"), Decimal("0.15"), Decimal("0.20"), Decimal("0.11")),
        "total_bid_quantity": NormalizedRoi(Decimal("0.04"), Decimal("0.27"), Decimal("0.20"), Decimal("0.09")),
        "total_ask_quantity": NormalizedRoi(Decimal("0.25"), Decimal("0.27"), Decimal("0.20"), Decimal("0.09")),
        "bid_levels": NormalizedRoi(Decimal("0.02"), Decimal("0.36"), Decimal("0.46"), Decimal("0.56")),
        "ask_levels": NormalizedRoi(Decimal("0.52"), Decimal("0.36"), Decimal("0.46"), Decimal("0.56")),
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
    x = int((Decimal(image_info.width) * roi.x).to_integral_value(rounding="ROUND_FLOOR"))
    y = int((Decimal(image_info.height) * roi.y).to_integral_value(rounding="ROUND_FLOOR"))
    width = int((Decimal(image_info.width) * roi.width).to_integral_value(rounding="ROUND_FLOOR"))
    height = int((Decimal(image_info.height) * roi.height).to_integral_value(rounding="ROUND_FLOOR"))
    width = max(1, min(width, image_info.width - x))
    height = max(1, min(height, image_info.height - y))
    return x, y, width, height
