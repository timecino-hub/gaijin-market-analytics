from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from PIL import Image, ImageFilter, ImageOps


MAX_PREPROCESSING_PIPELINES = 5
MAX_NORMALIZED_ROI_PIXELS = 2_000_000


@dataclass(frozen=True)
class OcrPreprocessingVariant:
    name: str
    scale_factor: int
    grayscale: bool = True
    autocontrast: bool = False
    sharpen: bool = False
    binary_threshold: int | None = None
    invert: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "scale_factor": self.scale_factor,
            "grayscale": self.grayscale,
            "autocontrast": self.autocontrast,
            "sharpen": self.sharpen,
            "binary_threshold": self.binary_threshold,
            "invert": self.invert,
        }


DEFAULT_OCR_PREPROCESSING_VARIANTS: tuple[OcrPreprocessingVariant, ...] = (
    OcrPreprocessingVariant("gray_3x", 3, grayscale=True),
    OcrPreprocessingVariant("gray_autocontrast_4x", 4, grayscale=True, autocontrast=True),
    OcrPreprocessingVariant("binary_4x", 4, grayscale=True, autocontrast=True, binary_threshold=170),
    OcrPreprocessingVariant("inverted_binary_4x", 4, grayscale=True, autocontrast=True, binary_threshold=170, invert=True),
)


def normalize_ocr_roi(
    image: Image.Image,
    variant: OcrPreprocessingVariant,
    *,
    max_pixels: int = MAX_NORMALIZED_ROI_PIXELS,
) -> Image.Image:
    if variant.scale_factor < 1:
        raise ValueError("scale_factor must be positive.")
    width = int(image.width) * variant.scale_factor
    height = int(image.height) * variant.scale_factor
    if width <= 0 or height <= 0:
        raise ValueError("ROI image must be non-empty.")
    if width * height > max_pixels:
        scale = (max_pixels / (width * height)) ** 0.5
        width = max(1, int(width * scale))
        height = max(1, int(height * scale))
    normalized = image.resize((width, height), Image.Resampling.BICUBIC)
    if variant.grayscale:
        normalized = ImageOps.grayscale(normalized)
    if variant.autocontrast:
        normalized = ImageOps.autocontrast(normalized)
    if variant.sharpen:
        normalized = normalized.filter(ImageFilter.SHARPEN)
    if variant.binary_threshold is not None:
        if normalized.mode != "L":
            normalized = ImageOps.grayscale(normalized)
        normalized = normalized.point(lambda value: 255 if value >= variant.binary_threshold else 0, "1").convert("L")
    if variant.invert:
        if normalized.mode != "L":
            normalized = ImageOps.grayscale(normalized)
        normalized = ImageOps.invert(normalized)
    return normalized


def build_ocr_preprocessing_variants(
    image: Image.Image,
    variants: Iterable[OcrPreprocessingVariant] = DEFAULT_OCR_PREPROCESSING_VARIANTS,
) -> dict[str, Image.Image]:
    selected = tuple(variants)[:MAX_PREPROCESSING_PIPELINES]
    return {variant.name: normalize_ocr_roi(image, variant) for variant in selected}


def preprocessing_metadata() -> dict[str, object]:
    return {
        "crop_rois": True,
        "source_image_modified": False,
        "runtime_model_download": False,
        "max_pipeline_count": MAX_PREPROCESSING_PIPELINES,
        "max_normalized_roi_pixels": MAX_NORMALIZED_ROI_PIXELS,
        "variants": [variant.to_json() for variant in DEFAULT_OCR_PREPROCESSING_VARIANTS],
    }
