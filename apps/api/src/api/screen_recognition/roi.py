from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from api.screen_recognition.contracts import ImageInfo, NormalizedRoi


class RoiValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PixelRoi:
    x: int
    y: int
    width: int
    height: int
    warnings: tuple[str, ...] = ()

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height

    def to_json(self) -> dict[str, object]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "warnings": list(self.warnings),
        }


def resolve_roi_pixels(
    roi: NormalizedRoi,
    image_info: ImageInfo,
    *,
    min_width: int = 4,
    min_height: int = 4,
    min_aspect_ratio: Decimal | None = Decimal("0.05"),
    max_aspect_ratio: Decimal | None = Decimal("20"),
    allow_clamp: bool = True,
) -> PixelRoi:
    if image_info.width <= 0 or image_info.height <= 0:
        raise RoiValidationError("image_dimensions_invalid", "Image dimensions must be positive.")
    _validate_normalized_number(roi.x, "x")
    _validate_normalized_number(roi.y, "y")
    _validate_normalized_number(roi.width, "width")
    _validate_normalized_number(roi.height, "height")

    x1 = _floor(Decimal(image_info.width) * roi.x)
    y1 = _floor(Decimal(image_info.height) * roi.y)
    x2 = _floor(Decimal(image_info.width) * (roi.x + roi.width))
    y2 = _floor(Decimal(image_info.height) * (roi.y + roi.height))

    warnings: list[str] = []
    if x1 < 0 or y1 < 0 or x2 > image_info.width or y2 > image_info.height:
        if not allow_clamp:
            raise RoiValidationError("roi_out_of_bounds", "ROI extends outside the image.")
        warnings.append("roi_out_of_bounds_clamped")
    x1 = max(0, min(x1, image_info.width))
    y1 = max(0, min(y1, image_info.height))
    x2 = max(0, min(x2, image_info.width))
    y2 = max(0, min(y2, image_info.height))

    width = x2 - x1
    height = y2 - y1
    if width <= 0 or height <= 0:
        raise RoiValidationError("roi_too_small", "ROI is empty or below one pixel after scaling.")
    if width < min_width or height < min_height:
        raise RoiValidationError("roi_too_small", "ROI is below the minimum OCR size.")
    if min_aspect_ratio is not None or max_aspect_ratio is not None:
        aspect_ratio = Decimal(width) / Decimal(height)
        if min_aspect_ratio is not None and aspect_ratio < min_aspect_ratio:
            raise RoiValidationError("roi_aspect_ratio_invalid", "ROI aspect ratio is too narrow.")
        if max_aspect_ratio is not None and aspect_ratio > max_aspect_ratio:
            raise RoiValidationError("roi_aspect_ratio_invalid", "ROI aspect ratio is too wide.")
    return PixelRoi(x=x1, y=y1, width=width, height=height, warnings=tuple(warnings))


def _validate_normalized_number(value: Decimal, name: str) -> None:
    if not value.is_finite():
        raise RoiValidationError("roi_invalid", f"ROI {name} must be finite.")
    if name in {"width", "height"} and value <= 0:
        raise RoiValidationError("roi_too_small", f"ROI {name} must be positive.")


def _floor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding="ROUND_FLOOR"))
