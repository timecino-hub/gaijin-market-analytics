from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, localcontext
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps

from api.screen_recognition.contracts import LayoutProfile
from api.screen_recognition.preprocessing import (
    DEFAULT_OCR_PREPROCESSING_VARIANTS,
    MAX_NORMALIZED_ROI_PIXELS,
    OcrPreprocessingVariant,
)


BATCH_SCHEMA_VERSION = "windows-ocr-batch-v1"
SYSTEM_DRAWING_BATCH_SCHEMA_VERSION = "windows-ocr-system-drawing-batch-v1"


@dataclass(frozen=True)
class PreparedOcrRequest:
    request_id: str
    field_name: str
    pipeline_name: str
    image_path: Path
    width: int
    height: int
    fingerprint: str
    physical_request_id: str
    deduplicated_preprocessing: bool
    preprocessing_descriptor_hash: str | None = None

    def to_manifest_json(self) -> dict[str, Any]:
        return {
            "request_id": self.physical_request_id,
            "image_path": str(self.image_path),
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class PreparedOcrBatch:
    manifest: dict[str, Any]
    logical_requests: tuple[PreparedOcrRequest, ...]
    diagnostics: dict[str, Any]


class _PreprocessingCache:
    def __init__(self) -> None:
        self.crops: dict[str, Image.Image] = {}
        self.resized: dict[tuple[str, int, int, int], Image.Image] = {}
        self.grayscale: dict[tuple[str, int, int, int], Image.Image] = {}
        self.autocontrast: dict[tuple[str, int, int, int], Image.Image] = {}
        self.threshold: dict[tuple[str, int, int, int, int], Image.Image] = {}
        self.inverted: dict[tuple[str, int, int, int, int | None, bool], Image.Image] = {}
        self.counts = {
            "roi_crop_count": 0,
            "resize_count": 0,
            "grayscale_count": 0,
            "autocontrast_count": 0,
            "threshold_count": 0,
            "invert_count": 0,
        }


def prepare_windows_ocr_batch(
    *,
    image_path: Path,
    layout_profile: LayoutProfile,
    temp_dir: Path,
    variants: Iterable[OcrPreprocessingVariant] = DEFAULT_OCR_PREPROCESSING_VARIANTS,
) -> PreparedOcrBatch:
    started = time.perf_counter()
    selected_variants = tuple(variants)
    temp_dir.mkdir(parents=True, exist_ok=True)
    cache = _PreprocessingCache()
    fields: dict[str, dict[str, Any]] = {}
    logical_requests: list[PreparedOcrRequest] = []
    physical_requests: dict[str, PreparedOcrRequest] = {}
    timings = {
        "image_decode_ms": 0,
        "layout_ms": 0,
        "roi_crop_ms": 0,
        "shared_preprocessing_ms": 0,
        "image_encode_ms": 0,
        "batch_manifest_ms": 0,
    }

    decode_started = time.perf_counter()
    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
    timings["image_decode_ms"] = _elapsed_ms(decode_started)

    layout_started = time.perf_counter()
    width, height = image.size
    resolved_rois = {
        field_name: _resolve_roi_tuple(roi.to_json(), width, height)
        for field_name, roi in sorted(layout_profile.rois.items())
    }
    timings["layout_ms"] = _elapsed_ms(layout_started)

    for field_name, box in resolved_rois.items():
        crop_started = time.perf_counter()
        crop = _crop_once(image, field_name, box, cache)
        timings["roi_crop_ms"] += _elapsed_ms(crop_started)
        if not _region_has_ink(crop):
            fields[field_name] = {
                "blank_roi_fast_path": True,
                "pipeline_count_attempted": 0,
                "pipeline_count_completed": 0,
                "selected_pipeline": None,
                "duration_ms": 0,
                "width": crop.width,
                "height": crop.height,
            }
            continue
        fields[field_name] = {
            "blank_roi_fast_path": False,
            "pipeline_count_attempted": len(selected_variants),
            "pipeline_count_completed": 0,
            "selected_pipeline": None,
            "duration_ms": 0,
            "width": crop.width,
            "height": crop.height,
        }
        for variant_ordinal, variant in enumerate(selected_variants):
            request_id = _logical_request_id(
                ordinal=len(logical_requests) + 1,
                field_name=field_name,
                pipeline_name=variant.name,
                pipeline_ordinal=variant_ordinal,
            )
            preprocess_started = time.perf_counter()
            prepared = _prepared_variant(crop, field_name, variant, cache)
            timings["shared_preprocessing_ms"] += _elapsed_ms(preprocess_started)
            encode_started = time.perf_counter()
            image_file, fingerprint = _write_prepared_image(
                prepared,
                temp_dir=temp_dir,
                request_id=request_id,
            )
            timings["image_encode_ms"] += _elapsed_ms(encode_started)
            existing = physical_requests.get(fingerprint)
            physical_request_id = request_id if existing is None else existing.physical_request_id
            request = PreparedOcrRequest(
                request_id=request_id,
                field_name=field_name,
                pipeline_name=variant.name,
                image_path=image_file if existing is None else existing.image_path,
                width=prepared.width,
                height=prepared.height,
                fingerprint=fingerprint[:16],
                physical_request_id=physical_request_id,
                deduplicated_preprocessing=existing is not None,
            )
            logical_requests.append(request)
            if existing is None:
                physical_requests[fingerprint] = request
            elif image_file != existing.image_path:
                image_file.unlink(missing_ok=True)

    manifest_started = time.perf_counter()
    physical_manifest_requests = [
        request.to_manifest_json()
        for request in physical_requests.values()
    ]
    manifest = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "requests": physical_manifest_requests,
    }
    timings["batch_manifest_ms"] = _elapsed_ms(manifest_started)
    diagnostics = {
        "preprocessing_mode": "python_batch_v1",
        "recognition_image_decode_count": 1,
        "recognition_roi_resolve_count": len(resolved_rois),
        "logical_pipeline_request_count": len(logical_requests),
        "unique_prepared_image_count": len(physical_requests),
        "deduplicated_ocr_request_count": len(logical_requests) - len(physical_requests),
        "prepared_image_write_count": len(physical_requests),
        "prepared_image_read_count": len(physical_requests),
        "winrt_decoder_count": len(physical_requests),
        "software_bitmap_count": len(physical_requests),
        "ocr_engine_initialization_count": 1 if physical_requests else 0,
        "prepared_image_fingerprints": sorted(
            {request.fingerprint for request in logical_requests}
        ),
        "fields": fields,
        "python_batch_timings_ms": timings,
        "python_preprocessing_total_ms": _elapsed_ms(started),
        **cache.counts,
    }
    return PreparedOcrBatch(
        manifest=manifest,
        logical_requests=tuple(logical_requests),
        diagnostics=diagnostics,
    )


def prepare_system_drawing_ocr_batch_manifest(
    *,
    image_path: Path,
    layout_profile: LayoutProfile,
    variants: Iterable[OcrPreprocessingVariant] = DEFAULT_OCR_PREPROCESSING_VARIANTS,
    field_variant_names: dict[str, tuple[str, ...]] | None = None,
) -> PreparedOcrBatch:
    started = time.perf_counter()
    selected_variants = tuple(variants)
    fields: dict[str, dict[str, Any]] = {}
    logical_requests: list[PreparedOcrRequest] = []
    manifest_requests: list[dict[str, Any]] = []
    timings = {
        "image_metadata_decode_ms": 0,
        "layout_ms": 0,
        "batch_manifest_ms": 0,
    }

    decode_started = time.perf_counter()
    with Image.open(image_path) as opened:
        source_width, source_height = opened.size
    timings["image_metadata_decode_ms"] = _elapsed_ms(decode_started)

    layout_started = time.perf_counter()
    resolved_rois = {
        field_name: _resolve_roi_tuple_decimal(roi.to_json(), source_width, source_height)
        for field_name, roi in sorted(layout_profile.rois.items())
    }
    timings["layout_ms"] = _elapsed_ms(layout_started)

    manifest_started = time.perf_counter()
    variants_by_name = {variant.name: variant for variant in selected_variants}
    for field_name, box in resolved_rois.items():
        x, y, width, height = box
        field_variants = selected_variants
        if field_variant_names is not None:
            field_variants = tuple(
                variants_by_name[name]
                for name in field_variant_names.get(field_name, ())
                if name in variants_by_name
            )
        fields[field_name] = {
            "blank_roi_fast_path": False,
            "pipeline_count_attempted": len(field_variants),
            "pipeline_count_completed": 0,
            "selected_pipeline": None,
            "duration_ms": 0,
            "width": width,
            "height": height,
        }
        for variant_ordinal, variant in enumerate(field_variants):
            request_id = _logical_request_id(
                ordinal=len(logical_requests) + 1,
                field_name=field_name,
                pipeline_name=variant.name,
                pipeline_ordinal=variant_ordinal,
            )
            target_width, target_height = _target_dimensions(width, height, variant.scale_factor)
            descriptor = {
                "version": "system-drawing-batch-v1",
                "source_image_path": str(image_path),
                "source_width": source_width,
                "source_height": source_height,
                "field_name": field_name,
                "region": field_name,
                "pipeline_name": variant.name,
                "crop": {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                },
                "target": {
                    "width": target_width,
                    "height": target_height,
                    "max_pixels": MAX_NORMALIZED_ROI_PIXELS,
                },
                "preprocessing": variant.to_json(),
                "drawing": {
                    "bitmap_constructor": "System.Drawing.Bitmap(width,height)",
                    "graphics_factory": "Graphics.FromImage",
                    "clear_color": "White",
                    "interpolation_mode": "HighQualityBicubic",
                    "smoothing_mode": "HighQuality",
                    "pixel_offset_mode": "HighQuality",
                    "graphics_unit": "Pixel",
                    "encoder": "PNG",
                    "pixel_preprocessing": "legacy_getpixel_setpixel_gray_autocontrast_threshold_invert",
                },
            }
            descriptor_hash = _stable_descriptor_hash(descriptor)
            request = PreparedOcrRequest(
                request_id=request_id,
                field_name=field_name,
                pipeline_name=variant.name,
                image_path=image_path,
                width=target_width,
                height=target_height,
                fingerprint=descriptor_hash[:16],
                physical_request_id=request_id,
                deduplicated_preprocessing=False,
                preprocessing_descriptor_hash=descriptor_hash,
            )
            logical_requests.append(request)
            manifest_requests.append(
                {
                    "request_id": request_id,
                    "field_name": field_name,
                    "region": field_name,
                    "pipeline_name": variant.name,
                    "logical_ordinal": len(logical_requests),
                    "crop": descriptor["crop"],
                    "target": descriptor["target"],
                    "preprocessing": variant.to_json(),
                    "preprocessing_descriptor": descriptor,
                    "preprocessing_descriptor_hash": descriptor_hash,
                }
            )

    manifest = {
        "schema_version": SYSTEM_DRAWING_BATCH_SCHEMA_VERSION,
        "source_image_path": str(image_path),
        "requests": manifest_requests,
    }
    timings["batch_manifest_ms"] = _elapsed_ms(manifest_started)
    diagnostics = {
        "preprocessing_mode": "system_drawing_batch_v1",
        "recognition_image_decode_count": 0,
        "recognition_image_metadata_decode_count": 1,
        "recognition_roi_resolve_count": len(resolved_rois),
        "logical_pipeline_request_count": len(logical_requests),
        "unique_prepared_image_count": len(logical_requests),
        "deduplicated_ocr_request_count": 0,
        "prepared_image_write_count": len(logical_requests),
        "prepared_image_read_count": len(logical_requests),
        "winrt_decoder_count": len(logical_requests),
        "software_bitmap_count": len(logical_requests),
        "ocr_engine_initialization_count": 1 if logical_requests else 0,
        "prepared_image_fingerprints": sorted(
            {request.fingerprint for request in logical_requests}
        ),
        "preprocessing_descriptor_hashes": [
            request.preprocessing_descriptor_hash for request in logical_requests
        ],
        "fields": fields,
        "python_batch_timings_ms": timings,
        "python_preprocessing_total_ms": _elapsed_ms(started),
        "roi_crop_count": 0,
        "resize_count": 0,
        "grayscale_count": 0,
        "autocontrast_count": 0,
        "threshold_count": 0,
        "invert_count": 0,
    }
    return PreparedOcrBatch(
        manifest=manifest,
        logical_requests=tuple(logical_requests),
        diagnostics=diagnostics,
    )


def _resolve_roi_tuple(roi: dict[str, str], width: int, height: int) -> tuple[int, int, int, int]:
    x = int(float(roi["x"]) * width)
    y = int(float(roi["y"]) * height)
    w = max(1, int(float(roi["width"]) * width))
    h = max(1, int(float(roi["height"]) * height))
    if x + w > width:
        w = width - x
    if y + h > height:
        h = height - y
    return x, y, w, h


def _resolve_roi_tuple_decimal(roi: dict[str, str], width: int, height: int) -> tuple[int, int, int, int]:
    with localcontext() as context:
        context.prec = 29
        x = int(_to_dotnet_decimal(roi["x"]) * Decimal(width))
        y = int(_to_dotnet_decimal(roi["y"]) * Decimal(height))
        w = max(1, int(_to_dotnet_decimal(roi["width"]) * Decimal(width)))
        h = max(1, int(_to_dotnet_decimal(roi["height"]) * Decimal(height)))
    if x + w > width:
        w = width - x
    if y + h > height:
        h = height - y
    return x, y, w, h


def _to_dotnet_decimal(value: str) -> Decimal:
    # PowerShell casts normalized ROI strings to System.Decimal before Floor.
    return Decimal(str(value)).quantize(Decimal("1e-28"), rounding=ROUND_DOWN)


def _crop_once(
    image: Image.Image,
    field_name: str,
    box: tuple[int, int, int, int],
    cache: _PreprocessingCache,
) -> Image.Image:
    if field_name not in cache.crops:
        x, y, width, height = box
        cache.crops[field_name] = image.crop((x, y, x + width, y + height))
        cache.counts["roi_crop_count"] += 1
    return cache.crops[field_name]


def _prepared_variant(
    crop: Image.Image,
    field_name: str,
    variant: OcrPreprocessingVariant,
    cache: _PreprocessingCache,
) -> Image.Image:
    resized = _resize(crop, field_name, variant, cache)
    needs_pixels = variant.autocontrast or variant.binary_threshold is not None or variant.invert
    if not needs_pixels:
        return resized
    image = _grayscale(resized, field_name, variant, cache)
    if variant.autocontrast:
        image = _autocontrast(image, field_name, variant, cache)
    if variant.binary_threshold is not None:
        image = _threshold(image, field_name, variant, cache)
    if variant.invert:
        image = _invert(image, field_name, variant, cache)
    return image


def _resize(
    crop: Image.Image,
    field_name: str,
    variant: OcrPreprocessingVariant,
    cache: _PreprocessingCache,
) -> Image.Image:
    width, height = _target_size(crop, variant.scale_factor)
    key = (field_name, variant.scale_factor, width, height)
    if key not in cache.resized:
        cache.resized[key] = crop.resize((width, height), Image.Resampling.LANCZOS)
        cache.counts["resize_count"] += 1
    return cache.resized[key]


def _grayscale(
    image: Image.Image,
    field_name: str,
    variant: OcrPreprocessingVariant,
    cache: _PreprocessingCache,
) -> Image.Image:
    key = (field_name, variant.scale_factor, image.width, image.height)
    if key not in cache.grayscale:
        cache.grayscale[key] = ImageOps.grayscale(image)
        cache.counts["grayscale_count"] += 1
    return cache.grayscale[key]


def _autocontrast(
    image: Image.Image,
    field_name: str,
    variant: OcrPreprocessingVariant,
    cache: _PreprocessingCache,
) -> Image.Image:
    key = (field_name, variant.scale_factor, image.width, image.height)
    if key not in cache.autocontrast:
        cache.autocontrast[key] = ImageOps.autocontrast(image)
        cache.counts["autocontrast_count"] += 1
    return cache.autocontrast[key]


def _threshold(
    image: Image.Image,
    field_name: str,
    variant: OcrPreprocessingVariant,
    cache: _PreprocessingCache,
) -> Image.Image:
    assert variant.binary_threshold is not None
    key = (field_name, variant.scale_factor, image.width, image.height, variant.binary_threshold)
    if key not in cache.threshold:
        cache.threshold[key] = image.point(
            lambda value: 255 if value >= (variant.binary_threshold or 0) else 0,
            "1",
        ).convert("L")
        cache.counts["threshold_count"] += 1
    return cache.threshold[key]


def _invert(
    image: Image.Image,
    field_name: str,
    variant: OcrPreprocessingVariant,
    cache: _PreprocessingCache,
) -> Image.Image:
    key = (
        field_name,
        variant.scale_factor,
        image.width,
        image.height,
        variant.binary_threshold,
        variant.autocontrast,
    )
    if key not in cache.inverted:
        cache.inverted[key] = ImageOps.invert(image)
        cache.counts["invert_count"] += 1
    return cache.inverted[key]


def _target_size(image: Image.Image, scale_factor: int) -> tuple[int, int]:
    return _target_dimensions(image.width, image.height, scale_factor)


def _target_dimensions(source_width: int, source_height: int, scale_factor: int) -> tuple[int, int]:
    scale = max(1, scale_factor)
    width = max(1, source_width * scale)
    height = max(1, source_height * scale)
    if width * height > MAX_NORMALIZED_ROI_PIXELS:
        ratio = (MAX_NORMALIZED_ROI_PIXELS / (width * height)) ** 0.5
        width = max(1, int(width * ratio))
        height = max(1, int(height * ratio))
    return width, height


def _stable_descriptor_hash(value: dict[str, Any]) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _write_prepared_image(
    image: Image.Image,
    *,
    temp_dir: Path,
    request_id: str,
) -> tuple[Path, str]:
    path = temp_dir / f"{request_id}.png"
    image.save(path, format="PNG")
    data = path.read_bytes()
    return path, hashlib.sha256(data).hexdigest()


def _logical_request_id(
    *,
    ordinal: int,
    field_name: str,
    pipeline_name: str,
    pipeline_ordinal: int,
) -> str:
    return (
        f"r{ordinal:04d}__"
        f"{_safe_request_id_segment(field_name)}__"
        f"{_safe_request_id_segment(pipeline_name)}__"
        f"p{pipeline_ordinal:02d}"
    )


def _safe_request_id_segment(value: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in value.strip())
    safe = "_".join(part for part in safe.split("_") if part)
    return safe[:48] or "unknown"


def _region_has_ink(image: Image.Image) -> bool:
    step_x = max(1, image.width // 32)
    step_y = max(1, image.height // 32)
    rgb = image.convert("RGB")
    for y in range(0, image.height, step_y):
        for x in range(0, image.width, step_x):
            r, g, b = rgb.getpixel((x, y))
            if r < 245 or g < 245 or b < 245:
                return True
    return False


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
