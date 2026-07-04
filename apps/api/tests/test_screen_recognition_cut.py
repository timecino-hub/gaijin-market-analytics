from __future__ import annotations

import json
import hashlib
import os
import socket
import struct
import subprocess
import sys
import zlib
import zipfile
import csv
from io import StringIO
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

from api.screen_recognition.comparison import compare_contracts, summarize_results
from api.screen_recognition.config import CurrentCutConfig, stable_config_sha256
from api.screen_recognition.contracts import LayoutProfile, NormalizedRoi, OcrResult
from api.screen_recognition.contracts import ScreenContract
from api.screen_recognition.cross_helper import (
    PREPARED_ONLY_SCHEMA_VERSION,
    _compare_consumer_payloads,
    _prepared_export_from_path,
    _safe_png_bmp_result,
    _validate_private_output_dir,
    audit_batch_response_mapping,
    export_legacy_prepared_images,
    export_pillow_prepared_images,
    recognize_prepared_images,
)
from api.screen_recognition.evaluate import (
    PRIVATE_FIXTURE_MESSAGE,
    create_private_ground_truth_template,
    evaluate_private_fixtures,
    regenerate_diagnostics_from_private_report,
)
from api.screen_recognition.ground_truth import GroundTruthInvalidError, load_ground_truth
from api.screen_recognition.image_io import (
    ImageReadError,
    read_image_info,
    safe_extract_images_zip,
)
from api.screen_recognition.json_util import dump_json_file
from api.screen_recognition.layouts import (
    LayoutUnsupportedError,
    get_layout_profile,
    roi_to_pixels,
    validate_layout_match,
)
from api.screen_recognition.ocr_backend import (
    OcrBackendError,
    OcrBackendTimeoutError,
    OcrInvocation,
    WindowsOcrRecognizer,
    _run_windows_helper,
    _windows_helper_command,
)
from api.screen_recognition.ocr_candidates import (
    FIELD_OCR_PIPELINES,
    PRICE_SELECTION_ORDER,
    normalize_numeric_ocr_token,
    parse_quantity_candidate,
    select_price_candidate,
    select_quantity_candidate,
)
from api.screen_recognition.parser import normalize_item_name, parse_ocr_contract
from api.screen_recognition.preprocessing import (
    DEFAULT_OCR_PREPROCESSING_VARIANTS,
    MAX_NORMALIZED_ROI_PIXELS,
    OcrPreprocessingVariant,
    build_ocr_preprocessing_variants,
)
from api.screen_recognition.ocr_batch import (
    BATCH_SCHEMA_VERSION,
    SYSTEM_DRAWING_BATCH_SCHEMA_VERSION,
    PreparedOcrRequest,
    _resolve_roi_tuple,
    prepare_system_drawing_ocr_batch_manifest,
    prepare_windows_ocr_batch,
)
from api.screen_recognition.private_diagnostics import build_anonymous_diagnostics
from api.screen_recognition.roi import RoiValidationError, resolve_roi_pixels
from api.screen_recognition.runner import CutRunConfig, run_cut


def write_png(path: Path, width: int = 1200, height: int = 800) -> None:
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def write_ground_truth(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def valid_row(filename: str = "sample.png", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "sample_id": "cut_001",
        "filename": filename,
        "expected_status": "passed",
        "item_key": "admin-alpha",
        "item_name": "Synthetic Alpha",
        "best_bid": "12.34",
        "best_ask": "13.00",
        "total_bid_quantity": 5,
        "total_ask_quantity": 7,
        "bid_levels": [
            {"exact_price": "12.34", "quantity": 2, "raw_display_price": "12.34"},
            {"exact_price": "11.00", "quantity": 3, "raw_display_price": "11.00"},
        ],
        "ask_levels": [
            {"exact_price": "13.00", "quantity": 7, "raw_display_price": "13.00"},
        ],
    }
    row.update(overrides)
    return row


def write_sidecar(path: Path, **overrides: str) -> None:
    payload = {
        "item_name": "Synthetic Alpha",
        "best_bid": "12.34",
        "best_ask": "13.00",
        "total_bid_quantity": "5",
        "total_ask_quantity": "7",
        "bid_levels": "12.34 2\n11.00 3",
        "ask_levels": "13.00 7",
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_ground_truth_jsonl_loads_and_rejects_float_prices(tmp_path: Path) -> None:
    gt = tmp_path / "ground_truth.jsonl"
    write_ground_truth(gt, [valid_row()])
    entries = load_ground_truth(gt)
    assert entries[0].expected.best_bid == Decimal("12.34")

    write_ground_truth(gt, [valid_row(best_bid=12.34)])
    with pytest.raises(GroundTruthInvalidError):
        load_ground_truth(gt)


def test_duplicate_sample_id_and_path_traversal_are_rejected(tmp_path: Path) -> None:
    gt = tmp_path / "ground_truth.jsonl"
    write_ground_truth(gt, [valid_row(), valid_row()])
    with pytest.raises(GroundTruthInvalidError):
        load_ground_truth(gt)

    write_ground_truth(gt, [valid_row(filename="../sample.png")])
    with pytest.raises(GroundTruthInvalidError):
        load_ground_truth(gt)


def test_layout_profile_and_normalized_roi_conversion() -> None:
    profile = get_layout_profile("gaijin-market-desktop-v1")
    info = read_image_info_from_dimensions("sample.png", 1200, 800)
    validate_layout_match(profile, info)
    assert roi_to_pixels(NormalizedRoi(Decimal("0.1"), Decimal("0.2"), Decimal("0.3"), Decimal("0.4")), info) == (
        120,
        160,
        360,
        320,
    )

    with pytest.raises(LayoutUnsupportedError):
        validate_layout_match(profile, read_image_info_from_dimensions("tiny.png", 300, 200))


def test_current_layout_rois_match_real_screenshot_dimensions() -> None:
    profile = get_layout_profile("gaijin-market-desktop-v1")
    dimensions = [(1743, 1024), (1318, 740), (1294, 763), (1302, 756)]
    for width, height in dimensions:
        info = read_image_info_from_dimensions("sample.png", width, height)
        validate_layout_match(profile, info)
        pixels = {name: roi_to_pixels(roi, info) for name, roi in profile.rois.items()}

        for x, y, roi_width, roi_height in pixels.values():
            assert x >= 0
            assert y >= 0
            assert x + roi_width <= width
            assert y + roi_height <= height

        item_x, item_y, _item_width, item_height = pixels["item_name"]
        bid_x, bid_y, _bid_width, _bid_height = pixels["best_bid"]
        ask_x, ask_y, _ask_width, _ask_height = pixels["best_ask"]
        bid_qty_x, bid_qty_y, _bid_qty_width, _bid_qty_height = pixels["total_bid_quantity"]
        ask_qty_x, ask_qty_y, _ask_qty_width, _ask_qty_height = pixels["total_ask_quantity"]
        bid_summary_x, bid_summary_y, bid_summary_width, bid_summary_height = pixels[
            "total_bid_quantity_summary"
        ]
        ask_summary_x, ask_summary_y, ask_summary_width, ask_summary_height = pixels[
            "total_ask_quantity_summary"
        ]

        assert item_x > int(width * Decimal("0.10"))
        assert item_y < int(height * Decimal("0.05"))
        assert item_y + item_height < bid_qty_y
        assert bid_y > int(height * Decimal("0.70"))
        assert ask_y > int(height * Decimal("0.70"))
        assert bid_x < int(width * Decimal("0.40"))
        assert ask_x > int(width * Decimal("0.65"))
        assert bid_qty_y < bid_y
        assert ask_qty_y < ask_y
        assert bid_summary_y < bid_y
        assert ask_summary_y < ask_y
        assert bid_summary_x < int(width * Decimal("0.40"))
        assert ask_summary_x > int(width * Decimal("0.55"))
        assert bid_summary_x + bid_summary_width < ask_summary_x
        assert bid_summary_y + bid_summary_height <= bid_y
        assert ask_summary_y + ask_summary_height <= ask_y


def test_roi_validation_clamps_bounds_and_rejects_bad_regions() -> None:
    info = read_image_info_from_dimensions("sample.png", 1200, 800)
    clamped = resolve_roi_pixels(
        NormalizedRoi(Decimal("0.9"), Decimal("0.9"), Decimal("0.2"), Decimal("0.2")),
        info,
    )
    assert clamped.as_tuple() == (1080, 720, 120, 80)
    assert clamped.warnings == ("roi_out_of_bounds_clamped",)

    with pytest.raises(RoiValidationError) as tiny:
        resolve_roi_pixels(
            NormalizedRoi(Decimal("0.1"), Decimal("0.1"), Decimal("0.001"), Decimal("0.001")),
            info,
        )
    assert tiny.value.code == "roi_too_small"

    with pytest.raises(RoiValidationError) as extreme:
        resolve_roi_pixels(
            NormalizedRoi(Decimal("0.1"), Decimal("0.1"), Decimal("0.001"), Decimal("0.8")),
            info,
            min_width=1,
            min_height=1,
        )
    assert extreme.value.code == "roi_aspect_ratio_invalid"


def test_ocr_preprocessing_variants_are_bounded_across_synthetic_scales() -> None:
    base = Image.new("RGB", (160, 48), "white")
    scales = (Decimal("0.80"), Decimal("0.90"), Decimal("1.00"), Decimal("1.10"), Decimal("1.25"))
    for scale in scales:
        scaled = base.resize(
            (
                int((Decimal(base.width) * scale).to_integral_value()),
                int((Decimal(base.height) * scale).to_integral_value()),
            )
        )
        variants = build_ocr_preprocessing_variants(scaled)
        assert tuple(variants) == tuple(variant.name for variant in DEFAULT_OCR_PREPROCESSING_VARIANTS)
        assert len(variants) <= 5
        for image in variants.values():
            assert image.width > 0
            assert image.height > 0
            assert image.width * image.height <= MAX_NORMALIZED_ROI_PIXELS


def test_python_batch_preprocessing_reuses_roi_and_shared_steps(tmp_path: Path) -> None:
    image = tmp_path / "source.png"
    source = Image.new("RGB", (120, 60), "white")
    for x in range(10, 40):
        for y in range(10, 30):
            source.putpixel((x, y), (0, 0, 0))
    for x in range(70, 100):
        for y in range(20, 45):
            source.putpixel((x, y), (0, 0, 0))
    source.save(image)
    profile = LayoutProfile(
        name="two-fields",
        version="1",
        min_width=1,
        min_height=1,
        min_aspect_ratio=Decimal("0.1"),
        max_aspect_ratio=Decimal("10"),
        rois={
            "best_bid": NormalizedRoi(Decimal("0"), Decimal("0"), Decimal("0.5"), Decimal("1")),
            "best_ask": NormalizedRoi(Decimal("0.5"), Decimal("0"), Decimal("0.5"), Decimal("1")),
        },
    )

    batch = prepare_windows_ocr_batch(image_path=image, layout_profile=profile, temp_dir=tmp_path / "prepared")
    diagnostics = batch.diagnostics

    assert batch.manifest["schema_version"] == BATCH_SCHEMA_VERSION
    assert diagnostics["recognition_image_decode_count"] == 1
    assert diagnostics["recognition_roi_resolve_count"] == 2
    assert diagnostics["roi_crop_count"] == 2
    assert diagnostics["resize_count"] == 4
    assert diagnostics["grayscale_count"] == 2
    assert diagnostics["autocontrast_count"] == 2
    assert diagnostics["threshold_count"] == 2
    assert diagnostics["invert_count"] == 2
    assert diagnostics["logical_pipeline_request_count"] == 8
    assert diagnostics["unique_prepared_image_count"] == 8
    assert diagnostics["prepared_image_write_count"] == 8


def test_python_batch_logical_request_ids_include_field_pipeline_and_ordinal(tmp_path: Path) -> None:
    image = tmp_path / "source.png"
    source = Image.new("RGB", (80, 40), "white")
    for x in range(5, 45):
        for y in range(5, 25):
            source.putpixel((x, y), (0, 0, 0))
    source.save(image)
    profile = LayoutProfile(
        name="semantic-request-ids",
        version="1",
        min_width=1,
        min_height=1,
        min_aspect_ratio=Decimal("0.1"),
        max_aspect_ratio=Decimal("10"),
        rois={"best_ask": NormalizedRoi(Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1"))},
    )
    variants = (
        OcrPreprocessingVariant("same_pipeline", 1, grayscale=False),
        OcrPreprocessingVariant("same_pipeline", 1, grayscale=False),
    )

    batch = prepare_windows_ocr_batch(
        image_path=image,
        layout_profile=profile,
        temp_dir=tmp_path / "prepared",
        variants=variants,
    )

    request_ids = [request.request_id for request in batch.logical_requests]
    assert request_ids == [
        "r0001__best_ask__same_pipeline__p00",
        "r0002__best_ask__same_pipeline__p01",
    ]
    assert batch.logical_requests[0].physical_request_id == request_ids[0]
    assert batch.logical_requests[1].physical_request_id == request_ids[0]
    assert len(batch.manifest["requests"]) == 1


def test_batch_roi_crop_boundaries_and_alpha_flatten_match_legacy_shape(tmp_path: Path) -> None:
    image = tmp_path / "alpha-source.png"
    source = Image.new("RGBA", (11, 7), (255, 255, 255, 255))
    source.putpixel((9, 5), (0, 0, 0, 255))
    source.putpixel((10, 6), (0, 0, 0, 255))
    source.putpixel((3, 2), (0, 0, 0, 128))
    source.save(image)
    profile = LayoutProfile(
        name="edge-roi",
        version="1",
        min_width=1,
        min_height=1,
        min_aspect_ratio=Decimal("0.1"),
        max_aspect_ratio=Decimal("10"),
        rois={"best_ask": NormalizedRoi(Decimal("0.27"), Decimal("0.28"), Decimal("0.68"), Decimal("0.58"))},
    )

    box = _resolve_roi_tuple(profile.rois["best_ask"].to_json(), 11, 7)
    batch = prepare_windows_ocr_batch(
        image_path=image,
        layout_profile=profile,
        temp_dir=tmp_path / "prepared",
        variants=(DEFAULT_OCR_PREPROCESSING_VARIANTS[0],),
    )
    request = batch.logical_requests[0]
    prepared = Image.open(request.image_path)

    assert box == (2, 1, 7, 4)
    assert request.width == 21
    assert request.height == 12
    assert prepared.mode == "RGB"
    assert prepared.getextrema()[0][0] >= 0
    assert _flatten_alpha_on_white(source.crop((3, 2, 4, 3))).getpixel((0, 0)) == (127, 127, 127)


def test_legacy_grayscale_autocontrast_threshold_and_invert_math() -> None:
    image = Image.new("RGBA", (6, 1))
    pixels = [
        (0, 0, 0, 255),
        (255, 255, 255, 255),
        (255, 0, 0, 255),
        (0, 255, 0, 255),
        (0, 0, 255, 255),
        (0, 0, 0, 128),
    ]
    image.putdata(pixels)

    grayscale = _legacy_grayscale(image)
    assert list(grayscale.getdata()) == [0, 255, 76, 150, 29, 127]

    contrast_source = Image.new("L", (5, 1))
    contrast_source.putdata([10, 20, 95, 169, 170])
    contrasted = _legacy_autocontrast(contrast_source)
    assert list(contrasted.getdata()) == [0, 16, 135, 253, 255]

    thresholded = contrasted.point([255 if value >= 170 else 0 for value in range(256)], "L")
    inverted = ImageChops.invert(thresholded)
    assert list(thresholded.getdata()) == [0, 0, 0, 255, 255]
    assert list(inverted.getdata()) == [255, 255, 255, 0, 0]


def test_batch_prepared_pixels_document_lanczos_and_bicubic_delta(tmp_path: Path) -> None:
    image = tmp_path / "source.png"
    source = _synthetic_pixel_equivalence_image()
    source.save(image)
    profile = _pixel_equivalence_profile(source.width, source.height)
    variant = DEFAULT_OCR_PREPROCESSING_VARIANTS[0]

    batch = prepare_windows_ocr_batch(
        image_path=image,
        layout_profile=profile,
        temp_dir=tmp_path / "prepared",
        variants=(variant,),
    )
    prepared = Image.open(batch.logical_requests[0].image_path).convert("RGB")
    x, y, width, height = _resolve_roi_tuple(profile.rois["best_ask"].to_json(), source.width, source.height)
    crop = source.convert("RGB").crop((x, y, x + width, y + height))
    target = (width * variant.scale_factor, height * variant.scale_factor)
    lanczos = crop.resize(target, Image.Resampling.LANCZOS).convert("RGB")
    bicubic = crop.resize(target, Image.Resampling.BICUBIC).convert("RGB")

    assert _pixel_delta_stats(prepared, lanczos)["different_pixel_count"] == 0
    assert _pixel_delta_stats(prepared, bicubic)["different_pixel_count"] > 0


@pytest.mark.skipif(os.name != "nt", reason="System.Drawing pixel reference requires Windows")
def test_batch_prepared_pixels_are_close_to_system_drawing_reference(tmp_path: Path) -> None:
    image = tmp_path / "source.png"
    source = _synthetic_pixel_equivalence_image()
    source.save(image)
    profile = _pixel_equivalence_profile(source.width, source.height)
    variant = DEFAULT_OCR_PREPROCESSING_VARIANTS[2]
    x, y, width, height = _resolve_roi_tuple(profile.rois["best_ask"].to_json(), source.width, source.height)
    reference = tmp_path / "legacy-reference.png"
    _write_system_drawing_reference(
        source_path=image,
        output_path=reference,
        box=(x, y, width, height),
        variant=variant,
    )

    batch = prepare_windows_ocr_batch(
        image_path=image,
        layout_profile=profile,
        temp_dir=tmp_path / "prepared",
        variants=(variant,),
    )
    prepared = Image.open(batch.logical_requests[0].image_path)
    legacy = Image.open(reference)
    stats = _pixel_delta_stats(legacy, prepared, decimal_region=(width * 4 - 18, 6, 18, 18))

    assert stats["width"] == width * variant.scale_factor
    assert stats["height"] == height * variant.scale_factor
    assert stats["actual_mode"] == "L"
    assert stats["reference_mode"] in {"RGB", "RGBA"}
    assert stats["different_pixel_count"] <= 350
    assert stats["max_channel_difference"] <= 255
    assert stats["mean_absolute_difference"] <= 3
    assert stats["decimal_point_region_different_pixel_count"] <= 10


def test_system_drawing_batch_manifest_uses_semantic_requests_and_descriptors(tmp_path: Path) -> None:
    image = tmp_path / "source.png"
    source = Image.new("RGB", (80, 40), "white")
    for x in range(5, 45):
        for y in range(5, 25):
            source.putpixel((x, y), (0, 0, 0))
    source.save(image)
    profile = LayoutProfile(
        name="system-drawing-manifest",
        version="1",
        min_width=1,
        min_height=1,
        min_aspect_ratio=Decimal("0.1"),
        max_aspect_ratio=Decimal("10"),
        rois={
            "best_ask": NormalizedRoi(Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1")),
        },
    )
    variants = (
        OcrPreprocessingVariant("gray_3x", 3, grayscale=True),
        OcrPreprocessingVariant("binary_4x", 4, grayscale=True, autocontrast=True, binary_threshold=170),
    )

    batch = prepare_system_drawing_ocr_batch_manifest(
        image_path=image,
        layout_profile=profile,
        variants=variants,
    )

    assert batch.manifest["schema_version"] == SYSTEM_DRAWING_BATCH_SCHEMA_VERSION
    assert batch.manifest["source_image_path"] == str(image)
    assert [request.request_id for request in batch.logical_requests] == [
        "r0001__best_ask__gray_3x__p00",
        "r0002__best_ask__binary_4x__p01",
    ]
    assert [request.physical_request_id for request in batch.logical_requests] == [
        "r0001__best_ask__gray_3x__p00",
        "r0002__best_ask__binary_4x__p01",
    ]
    manifest_requests = batch.manifest["requests"]
    assert [item["request_id"] for item in manifest_requests] == [
        "r0001__best_ask__gray_3x__p00",
        "r0002__best_ask__binary_4x__p01",
    ]
    assert manifest_requests[0]["crop"] == {"x": 0, "y": 0, "width": 80, "height": 40}
    assert manifest_requests[0]["target"] == {
        "width": 240,
        "height": 120,
        "max_pixels": MAX_NORMALIZED_ROI_PIXELS,
    }
    descriptor_hashes = [
        request.preprocessing_descriptor_hash for request in batch.logical_requests
    ]
    assert all(item and len(item) == 64 for item in descriptor_hashes)
    assert descriptor_hashes == [
        item["preprocessing_descriptor_hash"] for item in manifest_requests
    ]
    assert len(set(descriptor_hashes)) == len(descriptor_hashes)
    assert batch.diagnostics["preprocessing_mode"] == "system_drawing_batch_v1"
    assert batch.diagnostics["deduplicated_ocr_request_count"] == 0


@pytest.mark.skipif(os.name != "nt", reason="System.Drawing pixel reference requires Windows")
def test_system_drawing_batch_prepared_pixels_match_legacy_export(tmp_path: Path) -> None:
    image = tmp_path / "source.png"
    source = _synthetic_pixel_equivalence_image()
    source.save(image)
    profile = LayoutProfile(
        name="system-drawing-pixel-equivalence",
        version="1",
        min_width=1,
        min_height=1,
        min_aspect_ratio=Decimal("0.1"),
        max_aspect_ratio=Decimal("10"),
        rois={
            "best_ask": NormalizedRoi(
                Decimal(3) / Decimal(source.width),
                Decimal(5) / Decimal(source.height),
                Decimal(67) / Decimal(source.width),
                Decimal(31) / Decimal(source.height),
            ),
            "ask_levels": NormalizedRoi(
                Decimal(1) / Decimal(source.width),
                Decimal(3) / Decimal(source.height),
                Decimal(69) / Decimal(source.width),
                Decimal(34) / Decimal(source.height),
            ),
        },
    )
    legacy_dir = tmp_path / "legacy"
    system_dir = tmp_path / "system-drawing"
    temp_root = tmp_path / "tmp"
    legacy_dir.mkdir()
    system_dir.mkdir()
    temp_root.mkdir()

    legacy_exports = export_legacy_prepared_images(
        image_path=image,
        layout_profile=profile,
        fixture_id="synthetic-system-drawing",
        output_dir=legacy_dir,
        temp_root=temp_root,
        timeout_seconds=30,
    )
    batch = prepare_system_drawing_ocr_batch_manifest(
        image_path=image,
        layout_profile=profile,
    )
    input_path = tmp_path / "system-drawing-input.json"
    output_path = tmp_path / "system-drawing-output.json"
    script_path = Path(__file__).parents[1] / "src" / "api" / "screen_recognition" / "windows_ocr.ps1"
    dump_json_file(
        input_path,
        {
            **batch.manifest,
            "debug_artifacts_dir": str(system_dir),
            "skip_ocr": True,
        },
    )

    completed = _run_windows_helper(
        _windows_helper_command(script_path, input_path, output_path),
        timeout_seconds=30,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
    assert payload["schema_version"] == SYSTEM_DRAWING_BATCH_SCHEMA_VERSION
    assert payload["diagnostics"]["source_image_open_count"] == 1
    assert payload["diagnostics"]["ocr_engine_initialization_count"] == 0
    assert payload["diagnostics"]["ocr_invocation_count"] == 0
    assert payload["diagnostics"]["prepared_image_count"] == len(batch.logical_requests)

    legacy_by_key = {
        (export.field_name, export.pipeline_name): export
        for export in legacy_exports
    }
    system_by_key = {
        (str(result["field_name"]), str(result["pipeline_name"])): result
        for result in payload["results"]
    }

    assert set(legacy_by_key) == set(system_by_key)
    assert {
        ("best_ask", "gray_3x"),
        ("ask_levels", "gray_3x"),
        ("best_ask", "gray_autocontrast_4x"),
        ("best_ask", "binary_4x"),
        ("best_ask", "inverted_binary_4x"),
    }.issubset(set(system_by_key))
    for key, legacy in legacy_by_key.items():
        prepared = system_by_key[key]["prepared"]
        assert prepared is not None
        system_path = Path(str(prepared["image_path"]))
        assert system_path.is_file()
        assert prepared["encoded_sha256"] == legacy.encoded_sha256
        assert _decoded_pixel_identity(system_path) == _decoded_pixel_identity(legacy.image_path)


@pytest.mark.skipif(os.name != "nt", reason="System.Drawing LockBits reference requires Windows")
def test_system_drawing_lockbits_matches_pixel_loop_across_synthetic_edges(tmp_path: Path) -> None:
    cases = _lockbits_equivalence_sources()
    for case_name, source in cases:
        image = tmp_path / f"{case_name}.png"
        source.save(image)
        profile = LayoutProfile(
            name=f"lockbits-{case_name}",
            version="1",
            min_width=1,
            min_height=1,
            min_aspect_ratio=Decimal("0.1"),
            max_aspect_ratio=Decimal("20"),
            rois={
                "best_ask": NormalizedRoi(Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1")),
            },
        )
        batch = prepare_system_drawing_ocr_batch_manifest(
            image_path=image,
            layout_profile=profile,
        )

        pixel_loop = _run_system_drawing_prepared_export(
            tmp_path=tmp_path,
            case_name=case_name,
            batch_manifest=batch.manifest,
            pixel_implementation="legacy-pixel-loop",
        )
        lockbits = _run_system_drawing_prepared_export(
            tmp_path=tmp_path,
            case_name=case_name,
            batch_manifest=batch.manifest,
            pixel_implementation="lockbits-v1",
        )

        assert lockbits["diagnostics"]["pixel_implementation"] == "lockbits-v1"
        assert lockbits["diagnostics"]["csharp_type_compile_count"] == 1
        assert lockbits["diagnostics"]["lockbits_count"] > 0
        assert lockbits["diagnostics"]["unlockbits_count"] == lockbits["diagnostics"]["lockbits_count"]
        assert pixel_loop["diagnostics"]["get_pixel_call_count"] > 0
        assert lockbits["diagnostics"]["get_pixel_call_count"] == pixel_loop["diagnostics"]["get_pixel_call_count"]
        assert lockbits["diagnostics"]["set_pixel_call_count"] == pixel_loop["diagnostics"]["set_pixel_call_count"]

        pixel_loop_by_key = {
            (str(result["field_name"]), str(result["pipeline_name"])): result
            for result in pixel_loop["results"]
        }
        lockbits_by_key = {
            (str(result["field_name"]), str(result["pipeline_name"])): result
            for result in lockbits["results"]
        }

        assert set(lockbits_by_key) == set(pixel_loop_by_key)
        for key, reference in pixel_loop_by_key.items():
            candidate = lockbits_by_key[key]
            reference_prepared = reference["prepared"]
            candidate_prepared = candidate["prepared"]
            assert candidate_prepared["encoded_sha256"] == reference_prepared["encoded_sha256"]
            assert _decoded_pixel_identity(Path(candidate_prepared["image_path"])) == _decoded_pixel_identity(
                Path(reference_prepared["image_path"])
            )


def test_windows_ocr_batch_contract_maps_results_by_request_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import api.screen_recognition.ocr_backend as backend_module

    image = tmp_path / "source.png"
    source = Image.new("RGB", (80, 40), "white")
    for x in range(5, 45):
        for y in range(5, 25):
            source.putpixel((x, y), (0, 0, 0))
    source.save(image)
    profile = LayoutProfile(
        name="one-field",
        version="1",
        min_width=1,
        min_height=1,
        min_aspect_ratio=Decimal("0.1"),
        max_aspect_ratio=Decimal("10"),
        rois={"best_bid": NormalizedRoi(Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1"))},
    )
    captured: dict[str, object] = {}

    def fake_run(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        input_path = Path(command[command.index("-InputJson") + 1])
        output_path = Path(command[command.index("-OutputJson") + 1])
        manifest = json.loads(input_path.read_text(encoding="utf-8"))
        captured["manifest"] = manifest
        requests = list(reversed(manifest["requests"]))
        output_path.write_text(
            json.dumps(
                {
                    "schema_version": BATCH_SCHEMA_VERSION,
                    "warnings": ["ocr_confidence_unavailable"],
                    "diagnostics": {
                        "mode": "batch_v1",
                        "helper_total_duration_ms": 10,
                        "ocr_invocation_count": len(manifest["requests"]),
                        "actual_ocr_invocation_count": len(manifest["requests"]),
                        "ocr_engine_initialization_count": 1,
                        "ocr_engine_initialization_total_ms": 3,
                        "total_ocr_duration_ms": 7,
                    },
                    "results": [
                        {
                            "request_id": request["request_id"],
                            "raw_text": "12.34" if index == 0 else "",
                            "lines": [],
                            "timing": {
                                "total_ms": 1,
                                "image_open_ms": 0,
                                "bitmap_decode_ms": 0,
                                "recognize_ms": 1,
                                "serialization_ms": 0,
                                "dispose_ms": 0,
                            },
                            "error_code": None,
                        }
                        for index, request in enumerate(requests)
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(backend_module, "_run_windows_helper", fake_run)

    result = WindowsOcrRecognizer(timeout_seconds=20).recognize(
        OcrInvocation(image_path=image, layout_profile=profile, debug_artifacts_dir=None)
    )

    assert captured["manifest"]["schema_version"] == BATCH_SCHEMA_VERSION
    assert len(captured["manifest"]["requests"]) == 4
    assert result.fields["best_bid"].raw_text == "12.34"
    assert result.diagnostics["powershell_process_count"] == 1
    assert result.diagnostics["ocr_engine_initialization_count"] == 1
    assert result.diagnostics["ocr_engine_initialization_total_ms"] == 3
    assert result.diagnostics["pipeline_count_attempted"] == 4
    assert result.diagnostics["ocr_invocation_count"] == 4


def test_batch_result_mapping_reports_missing_unknown_and_duplicate_responses(tmp_path: Path) -> None:
    from api.screen_recognition.ocr_backend import _batch_payload_to_ocr_result

    requests = (
        batch_request(tmp_path, "r0001", "best_ask", "gray_3x", "p1"),
        batch_request(tmp_path, "r0002", "best_ask", "gray_autocontrast_4x", "p2"),
    )
    payload = {
        "warnings": ["ocr_confidence_unavailable"],
        "diagnostics": {"helper_total_duration_ms": 1},
        "results": [
            batch_response("p2", "13.00"),
            batch_response("unknown", "99.00"),
            batch_response("p2", "88.00"),
        ],
    }

    result = _batch_payload_to_ocr_result(
        payload,
        requests,
        batch_python_diagnostics(requests),
        helper_duration_ms=2,
    )

    assert result.fields["best_ask"].raw_text == "13.00"
    assert {"missing_response", "unknown_response", "duplicate_response"}.issubset(
        set(result.warnings)
    )
    mapping = result.diagnostics["batch_response_mapping"]
    assert mapping["valid"] is False
    assert mapping["missing_response_ids"] == ["p1"]
    assert mapping["unknown_response_ids"] == ["unknown"]
    assert mapping["duplicate_response_ids"] == ["p2"]


def test_batch_result_mapping_reports_duplicate_logical_request_ids(tmp_path: Path) -> None:
    from api.screen_recognition.ocr_backend import _batch_payload_to_ocr_result

    requests = (
        batch_request(tmp_path, "duplicate", "best_ask", "gray_3x", "p1"),
        batch_request(tmp_path, "duplicate", "best_ask", "gray_autocontrast_4x", "p2"),
    )

    result = _batch_payload_to_ocr_result(
        {
            "warnings": ["ocr_confidence_unavailable"],
            "diagnostics": {"helper_total_duration_ms": 1},
            "results": [batch_response("p1", ""), batch_response("p2", "13.00")],
        },
        requests,
        batch_python_diagnostics(requests),
        helper_duration_ms=2,
    )

    assert "duplicate_request_id" in result.warnings
    assert result.diagnostics["batch_response_mapping"]["duplicate_request_ids"] == ["duplicate"]


def test_batch_result_mapping_reports_response_metadata_mismatch(tmp_path: Path) -> None:
    from api.screen_recognition.ocr_backend import _batch_payload_to_ocr_result

    requests = (batch_request(tmp_path, "r0001", "best_ask", "gray_3x", "p1"),)

    result = _batch_payload_to_ocr_result(
        {
            "warnings": ["ocr_confidence_unavailable"],
            "diagnostics": {"helper_total_duration_ms": 1},
            "results": [
                batch_response(
                    "p1",
                    "13.00",
                    field_name="ask_levels",
                    region="ask_levels",
                    pipeline_name="gray_autocontrast_4x",
                )
            ],
        },
        requests,
        batch_python_diagnostics(requests),
        helper_duration_ms=2,
    )

    assert {
        "response_field_mismatch",
        "response_region_mismatch",
        "response_pipeline_mismatch",
    }.issubset(set(result.warnings))


def test_batch_result_mapping_allows_one_physical_response_for_multiple_logical_requests(
    tmp_path: Path,
) -> None:
    from api.screen_recognition.ocr_backend import _batch_payload_to_ocr_result

    requests = (
        batch_request(tmp_path, "r0001", "best_ask", "gray_3x", "shared"),
        batch_request(tmp_path, "r0002", "ask_levels", "gray_3x", "shared"),
    )
    result = _batch_payload_to_ocr_result(
        {
            "warnings": ["ocr_confidence_unavailable"],
            "diagnostics": {"helper_total_duration_ms": 1},
            "results": [batch_response("shared", "13.00")],
        },
        requests,
        batch_python_diagnostics(requests),
        helper_duration_ms=2,
    )

    assert result.fields["best_ask"].raw_text == "13.00"
    assert result.fields["ask_levels"].raw_text == "13.00"
    assert result.warnings == ()
    mapping = result.diagnostics["batch_response_mapping"]
    assert mapping["logical_request_count"] == 2
    assert mapping["physical_request_count"] == 1
    assert mapping["valid"] is True


def test_batch_pipeline_selection_uses_logical_request_identity_for_ties(
    tmp_path: Path,
) -> None:
    from api.screen_recognition.ocr_backend import _batch_payload_to_ocr_result

    requests = (
        batch_request(tmp_path, "r0001", "best_ask", "same_pipeline", "p1"),
        batch_request(tmp_path, "r0002", "best_ask", "same_pipeline", "p2"),
    )
    result = _batch_payload_to_ocr_result(
        {
            "warnings": ["ocr_confidence_unavailable"],
            "diagnostics": {"helper_total_duration_ms": 1},
            "results": [batch_response("p1", ""), batch_response("p2", "13.00")],
        },
        requests,
        batch_python_diagnostics(requests),
        helper_duration_ms=2,
    )

    selected = [
        item["request_id"]
        for item in result.diagnostics["per_pipeline_duration_ms"]
        if item["selected"]
    ]
    assert selected == ["r0002"]
    assert result.diagnostics["fields"]["best_ask"]["selected_pipeline"] == "same_pipeline"


def batch_request(
    tmp_path: Path,
    request_id: str,
    field_name: str,
    pipeline_name: str,
    physical_request_id: str,
) -> PreparedOcrRequest:
    image_path = tmp_path / f"{request_id}.png"
    image_path.write_bytes(b"synthetic prepared image")
    return PreparedOcrRequest(
        request_id=request_id,
        field_name=field_name,
        pipeline_name=pipeline_name,
        image_path=image_path,
        width=10,
        height=5,
        fingerprint=f"hash-{request_id}",
        physical_request_id=physical_request_id,
        deduplicated_preprocessing=physical_request_id != request_id,
    )


def batch_response(request_id: str, raw_text: str, **metadata: object) -> dict[str, object]:
    response: dict[str, object] = {
        "request_id": request_id,
        "raw_text": raw_text,
        "lines": [],
        "timing": {
            "total_ms": 1,
            "image_open_ms": 0,
            "bitmap_decode_ms": 0,
            "recognize_ms": 1,
            "serialization_ms": 0,
            "dispose_ms": 0,
        },
        "error_code": None,
    }
    response.update(metadata)
    return response


def batch_python_diagnostics(
    requests: tuple[PreparedOcrRequest, ...],
) -> dict[str, object]:
    fields = {
        request.field_name: {
            "blank_roi_fast_path": False,
            "pipeline_count_attempted": 0,
            "pipeline_count_completed": 0,
            "selected_pipeline": None,
            "duration_ms": 0,
            "width": request.width,
            "height": request.height,
        }
        for request in requests
    }
    return {
        "logical_pipeline_request_count": len(requests),
        "fields": fields,
    }


def test_cross_helper_prepared_only_consumers_use_same_physical_file_and_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import api.screen_recognition.cross_helper as cross_helper

    image = tmp_path / "prepared.png"
    Image.new("L", (16, 10), 255).save(image)
    export = _prepared_export_from_path(
        fixture_id="fixture-safe",
        prepared_source="legacy",
        request_id="fixture-safe:best_ask:gray_3x",
        field_name="best_ask",
        pipeline_name="gray_3x",
        image_path=image,
    )
    captured: list[dict[str, object]] = []

    def fake_run(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        input_path = Path(command[command.index("-InputJson") + 1])
        output_path = Path(command[command.index("-OutputJson") + 1])
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        captured.append(payload)
        assert payload["schema_version"] == PREPARED_ONLY_SCHEMA_VERSION
        assert payload["requests"][0]["image_path"] == str(image)
        assert payload["requests"][0]["sha256"] == export.encoded_sha256
        output_path.write_text(
            json.dumps(
                {
                    "schema_version": PREPARED_ONLY_SCHEMA_VERSION,
                    "consumer_mode": payload["consumer_mode"],
                    "warnings": ["ocr_confidence_unavailable"],
                    "diagnostics": {
                        "mode": f"prepared_only_{payload['consumer_mode']}",
                        "ocr_language_source": "user_profile_languages",
                    },
                    "results": [
                        {
                            "request_id": export.request_id,
                            "raw_text": "12.34",
                            "lines": [{"text": "12.34", "words": [{"text": "12.34"}]}],
                            "timing": {"total_ms": 1},
                            "decoder": {"bitmap_pixel_format": "Gray8", "bitmap_alpha_mode": "Ignore"},
                            "error_code": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cross_helper, "_run_windows_helper", fake_run)

    legacy = recognize_prepared_images((export,), consumer="legacy", temp_root=tmp_path, timeout_seconds=10)
    batch = recognize_prepared_images((export,), consumer="batch", temp_root=tmp_path, timeout_seconds=10)

    assert [payload["consumer_mode"] for payload in captured] == ["legacy", "batch"]
    assert legacy["results"][0]["raw_text"] == batch["results"][0]["raw_text"]


def test_cross_helper_pillow_export_does_not_mutate_prepared_input_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    image = Image.new("RGB", (40, 20), "white")
    for x in range(5, 20):
        for y in range(4, 12):
            image.putpixel((x, y), (0, 0, 0))
    image.save(source)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    profile = LayoutProfile(
        name="one-field",
        version="1",
        min_width=1,
        min_height=1,
        min_aspect_ratio=Decimal("0.1"),
        max_aspect_ratio=Decimal("10"),
        rois={"best_ask": NormalizedRoi(Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1"))},
    )

    exports = export_pillow_prepared_images(
        image_path=source,
        layout_profile=profile,
        fixture_id="fixture-safe",
        output_dir=tmp_path / "pillow",
        variants=(DEFAULT_OCR_PREPROCESSING_VARIANTS[0],),
    )

    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    assert len(exports) == 1
    assert exports[0].encoded_sha256 == hashlib.sha256(exports[0].image_path.read_bytes()).hexdigest()


def test_cross_helper_safe_report_omits_raw_ocr_text_paths_and_prices(tmp_path: Path) -> None:
    image = tmp_path / "prepared.png"
    Image.new("L", (16, 10), 255).save(image)
    export = _prepared_export_from_path(
        fixture_id="fixture-safe",
        prepared_source="legacy",
        request_id="fixture-safe:best_ask:gray_3x",
        field_name="best_ask",
        pipeline_name="gray_3x",
        image_path=image,
    )
    payload = {
        "diagnostics": {"ocr_language_source": "user_profile_languages"},
        "results": [
            {
                "request_id": export.request_id,
                "raw_text": "12.34",
                "lines": [{"text": "12.34", "words": [{"text": "12.34"}]}],
                "timing": {"total_ms": 1},
                "decoder": {"bitmap_pixel_format": "Gray8"},
                "error_code": None,
            }
        ],
    }

    safe, private = _compare_consumer_payloads(
        fixture_id="fixture-safe",
        field_name="best_ask",
        prepared_source="legacy",
        exports=(export,),
        legacy_payload=payload,
        batch_payload=payload,
    )

    safe_text = json.dumps(safe, sort_keys=True)
    assert "12.34" not in safe_text
    assert str(image) not in safe_text
    assert "raw_text" not in safe_text
    assert private[0]["legacy_raw"]["raw_text"] == "12.34"


def test_cross_helper_png_bmp_safe_metadata_distinguishes_pixel_and_encoded_hash(
    tmp_path: Path,
) -> None:
    image = Image.new("RGB", (10, 8), "white")
    image.putpixel((3, 4), (0, 0, 0))
    png = tmp_path / "control.png"
    bmp = tmp_path / "control.bmp"
    image.save(png, format="PNG")
    image.save(bmp, format="BMP")

    png_export = _prepared_export_from_path(
        fixture_id="fixture-safe",
        prepared_source="legacy-png-control",
        request_id="r:png",
        field_name="best_ask",
        pipeline_name="gray_3x",
        image_path=png,
    )
    bmp_export = _prepared_export_from_path(
        fixture_id="fixture-safe",
        prepared_source="legacy-bmp-control",
        request_id="r:bmp",
        field_name="best_ask",
        pipeline_name="gray_3x",
        image_path=bmp,
    )

    assert png_export.pixel_sha256 == bmp_export.pixel_sha256
    assert png_export.encoded_sha256 != bmp_export.encoded_sha256
    safe = _safe_png_bmp_result(
        {
            "fixture_id": "fixture-safe",
            "prepared_source": "legacy",
            "field_name": "best_ask",
            "pipeline_name": "gray_3x",
            "pixel_hash_same": True,
            "png": {
                "path": str(png),
                "request_id": png_export.request_id,
                "width": png_export.width,
                "height": png_export.height,
                "mode": png_export.mode,
                "format": png_export.encoded_format,
                "encoded_sha256": png_export.encoded_sha256,
                "pixel_sha256": png_export.pixel_sha256,
                "alpha": png_export.alpha,
                "dpi": list(png_export.dpi),
            },
            "bmp": {
                "path": str(bmp),
                "request_id": bmp_export.request_id,
                "width": bmp_export.width,
                "height": bmp_export.height,
                "mode": bmp_export.mode,
                "format": bmp_export.encoded_format,
                "encoded_sha256": bmp_export.encoded_sha256,
                "pixel_sha256": bmp_export.pixel_sha256,
                "alpha": bmp_export.alpha,
                "dpi": list(bmp_export.dpi),
            },
            "ocr_status_same": True,
            "ocr_structure_same": True,
        }
    )
    assert "path" not in safe["png"]
    assert safe["pixel_hash_same"] is True


def test_cross_helper_request_mapping_audit_detects_order_duplicates_missing_and_unknown() -> None:
    audit = audit_batch_response_mapping(
        [{"request_id": "r2"}, {"request_id": "r1"}, {"request_id": "r1"}],
        [{"request_id": "r2"}, {"request_id": "r3"}, {"request_id": "r3"}],
    )

    assert audit["mapping_key"] == "request_id"
    assert audit["duplicate_request_ids"] == ["r1"]
    assert audit["duplicate_response_ids"] == ["r3"]
    assert audit["missing_response_ids"] == ["r1"]
    assert audit["unknown_response_ids"] == ["r3"]
    assert audit["valid"] is False


def test_cross_helper_request_mapping_audit_allows_shared_physical_request(tmp_path: Path) -> None:
    requests = (
        batch_request(tmp_path, "r0001", "best_ask", "gray_3x", "shared"),
        batch_request(tmp_path, "r0002", "ask_levels", "gray_3x", "shared"),
    )

    audit = audit_batch_response_mapping(requests, [{"request_id": "shared"}])

    assert audit["request_count"] == 1
    assert audit["logical_request_count"] == 2
    assert audit["duplicate_request_ids"] == []
    assert audit["missing_response_ids"] == []
    assert audit["unknown_response_ids"] == []
    assert audit["valid"] is True


def test_cross_helper_private_output_must_stay_under_ignored_private_artifacts(tmp_path: Path) -> None:
    _validate_private_output_dir(Path("artifacts/private/screen-recognition-evaluation/cross-helper"))
    with pytest.raises(ValueError):
        _validate_private_output_dir(tmp_path)

    ignore_text = Path(__file__).parents[3].joinpath(".gitignore").read_text(encoding="utf-8")
    assert "artifacts/private/" in ignore_text


def read_image_info_from_dimensions(filename: str, width: int, height: int):
    from api.screen_recognition.contracts import ImageInfo

    return ImageInfo(filename=filename, width=width, height=height, format="png")


def _synthetic_pixel_equivalence_image() -> Image.Image:
    scale = 4
    width, height = 73, 41
    image = Image.new("RGBA", (width * scale, height * scale), (22, 26, 34, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((36 * scale, 0, width * scale - 1, height * scale - 1), fill=(238, 241, 244, 255))
    draw.rounded_rectangle((5 * scale, 7 * scale, 67 * scale, 29 * scale), radius=2 * scale, fill=(35, 38, 45, 255))
    draw.line((9 * scale, 11 * scale, 9 * scale, 25 * scale), fill=(245, 246, 248, 255), width=1 * scale)
    draw.line((17 * scale, 11 * scale, 27 * scale, 11 * scale), fill=(246, 246, 246, 255), width=1 * scale)
    draw.line((27 * scale, 11 * scale, 27 * scale, 18 * scale), fill=(246, 246, 246, 255), width=1 * scale)
    draw.line((17 * scale, 18 * scale, 27 * scale, 18 * scale), fill=(246, 246, 246, 255), width=1 * scale)
    draw.line((17 * scale, 18 * scale, 17 * scale, 25 * scale), fill=(246, 246, 246, 255), width=1 * scale)
    draw.line((17 * scale, 25 * scale, 27 * scale, 25 * scale), fill=(246, 246, 246, 255), width=1 * scale)
    draw.ellipse((32 * scale, 24 * scale, 34 * scale, 26 * scale), fill=(250, 250, 250, 255))
    draw.line((39 * scale, 11 * scale, 48 * scale, 25 * scale), fill=(15, 18, 22, 255), width=1 * scale)
    draw.line((48 * scale, 11 * scale, 39 * scale, 25 * scale), fill=(15, 18, 22, 255), width=1 * scale)
    draw.ellipse((66 * scale, 25 * scale, 68 * scale, 27 * scale), fill=(255, 255, 255, 255))
    draw.rectangle((52 * scale, 31 * scale, 55 * scale, 35 * scale), fill=(169, 169, 169, 255))
    draw.rectangle((56 * scale, 31 * scale, 59 * scale, 35 * scale), fill=(170, 170, 170, 255))
    draw.rectangle((60 * scale, 31 * scale, 63 * scale, 35 * scale), fill=(171, 171, 171, 255))
    draw.rectangle((2 * scale, 32 * scale, 15 * scale, 38 * scale), fill=(255, 255, 255, 96))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _pixel_equivalence_profile(width: int, height: int) -> LayoutProfile:
    return LayoutProfile(
        name="pixel-equivalence",
        version="1",
        min_width=1,
        min_height=1,
        min_aspect_ratio=Decimal("0.1"),
        max_aspect_ratio=Decimal("10"),
        rois={
            "best_ask": NormalizedRoi(
                Decimal(3) / Decimal(width),
                Decimal(5) / Decimal(height),
                Decimal(67) / Decimal(width),
                Decimal(31) / Decimal(height),
            )
        },
    )


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )


def _flatten_alpha_on_white(image: Image.Image) -> Image.Image:
    if not _has_alpha(image):
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    background.alpha_composite(rgba)
    return background.convert("RGB")


def _legacy_grayscale(image: Image.Image) -> Image.Image:
    return _flatten_alpha_on_white(image).convert("L", matrix=(0.299, 0.587, 0.114, 0))


def _legacy_autocontrast(image: Image.Image) -> Image.Image:
    grayscale = image if image.mode == "L" else _legacy_grayscale(image)
    min_gray, max_gray = grayscale.getextrema()
    if max_gray <= min_gray:
        return grayscale
    scale = 255.0 / (max_gray - min_gray)
    return grayscale.point(
        [max(0, min(255, round((value - min_gray) * scale))) for value in range(256)],
        "L",
    )


def _pixel_delta_stats(
    reference: Image.Image,
    actual: Image.Image,
    *,
    decimal_region: tuple[int, int, int, int] | None = None,
) -> dict[str, object]:
    reference_rgb = reference.convert("RGB")
    actual_rgb = actual.convert("RGB")
    assert reference_rgb.size == actual_rgb.size
    diff = ImageChops.difference(reference_rgb, actual_rgb)
    stat = ImageStat.Stat(diff)
    channel_count = len(stat.sum)
    pixel_count = reference_rgb.width * reference_rgb.height
    different = sum(1 for pixel in diff.getdata() if pixel != (0, 0, 0))
    max_diff = max(channel_max for channel in diff.getextrema() for channel_max in channel)
    mean_abs = sum(stat.sum) / max(1, pixel_count * channel_count)
    reference_l = reference_rgb.convert("L")
    actual_l = actual_rgb.convert("L")
    decimal_different = 0
    if decimal_region is not None:
        x, y, width, height = decimal_region
        decimal_diff = ImageChops.difference(
            reference_rgb.crop((x, y, x + width, y + height)),
            actual_rgb.crop((x, y, x + width, y + height)),
        )
        decimal_different = sum(1 for pixel in decimal_diff.getdata() if pixel != (0, 0, 0))
    actual_values = list(actual_l.getdata())
    return {
        "width": actual.width,
        "height": actual.height,
        "reference_mode": reference.mode,
        "actual_mode": actual.mode,
        "different_pixel_count": different,
        "max_channel_difference": max_diff,
        "mean_absolute_difference": mean_abs,
        "black_pixel_ratio": actual_values.count(0) / max(1, len(actual_values)),
        "white_pixel_ratio": actual_values.count(255) / max(1, len(actual_values)),
        "alpha_behavior": "opaque_rgb" if actual.mode == "RGB" else actual.mode,
        "decimal_point_region_different_pixel_count": decimal_different,
    }


def _decoded_pixel_identity(path: Path) -> dict[str, object]:
    with Image.open(path) as opened:
        image = opened.copy()
    return {
        "mode": image.mode,
        "size": image.size,
        "alpha": _has_alpha(image),
        "dpi": image.info.get("dpi"),
        "pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
    }


def _run_system_drawing_prepared_export(
    *,
    tmp_path: Path,
    case_name: str,
    batch_manifest: dict[str, object],
    pixel_implementation: str,
) -> dict[str, object]:
    output_dir = tmp_path / f"{case_name}-{pixel_implementation}"
    output_dir.mkdir()
    input_path = tmp_path / f"{case_name}-{pixel_implementation}.input.json"
    output_path = tmp_path / f"{case_name}-{pixel_implementation}.output.json"
    script_path = Path(__file__).parents[1] / "src" / "api" / "screen_recognition" / "windows_ocr.ps1"
    dump_json_file(
        input_path,
        {
            **batch_manifest,
            "debug_artifacts_dir": str(output_dir),
            "pixel_implementation": pixel_implementation,
            "skip_ocr": True,
        },
    )
    completed = _run_windows_helper(
        _windows_helper_command(script_path, input_path, output_path),
        timeout_seconds=30,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
    assert payload["schema_version"] == SYSTEM_DRAWING_BATCH_SCHEMA_VERSION
    return payload


def _lockbits_equivalence_sources() -> list[tuple[str, Image.Image]]:
    rgb_odd = Image.new("RGB", (17, 13), "white")
    draw = ImageDraw.Draw(rgb_odd)
    draw.rectangle((1, 1, 15, 11), fill=(20, 24, 30))
    draw.line((2, 10, 14, 2), fill=(245, 246, 248), width=1)
    draw.point((9, 7), fill=(248, 248, 248))

    rgba_alpha = Image.new("RGBA", (19, 11), (255, 255, 255, 255))
    alpha_draw = ImageDraw.Draw(rgba_alpha, "RGBA")
    alpha_draw.rectangle((0, 0, 18, 10), fill=(30, 35, 42, 255))
    alpha_draw.rectangle((2, 2, 8, 8), fill=(255, 255, 255, 128))
    alpha_draw.rectangle((10, 2, 16, 8), fill=(255, 255, 255, 0))
    alpha_draw.line((1, 9, 17, 1), fill=(248, 248, 248, 192), width=1)

    single_pixel = Image.new("RGB", (1, 1), (169, 170, 171))

    constant_gray = Image.new("RGB", (9, 7), (170, 170, 170))

    threshold_edges = Image.new("RGB", (15, 5), "white")
    threshold_pixels = [(168, 168, 168), (169, 169, 169), (170, 170, 170), (171, 171, 171), (172, 172, 172)]
    for x in range(threshold_edges.width):
        for y, color in enumerate(threshold_pixels):
            threshold_edges.putpixel((x, y), color)

    antialias_decimal = _synthetic_pixel_equivalence_image()

    light_background = Image.new("RGB", (23, 15), (241, 243, 245))
    light_draw = ImageDraw.Draw(light_background)
    light_draw.text((2, 1), "12.3", fill=(15, 18, 22))
    light_draw.point((18, 11), fill=(12, 12, 12))

    dark_background = Image.new("RGB", (23, 15), (24, 27, 33))
    dark_draw = ImageDraw.Draw(dark_background)
    dark_draw.text((2, 1), "12.3", fill=(246, 247, 249))
    dark_draw.point((18, 11), fill=(252, 252, 252))

    return [
        ("rgb-odd", rgb_odd),
        ("rgba-alpha", rgba_alpha),
        ("single-pixel", single_pixel),
        ("constant-gray", constant_gray),
        ("threshold-edges", threshold_edges),
        ("antialias-decimal", antialias_decimal),
        ("light-background", light_background),
        ("dark-background", dark_background),
    ]


def _write_system_drawing_reference(
    *,
    source_path: Path,
    output_path: Path,
    box: tuple[int, int, int, int],
    variant,
) -> None:
    x, y, width, height = box
    script_path = output_path.with_suffix(".ps1")
    script_path.write_text(
        r'''
param(
  [Parameter(Mandatory=$true)][string]$SourcePath,
  [Parameter(Mandatory=$true)][string]$OutputPath,
  [Parameter(Mandatory=$true)][int]$X,
  [Parameter(Mandatory=$true)][int]$Y,
  [Parameter(Mandatory=$true)][int]$Width,
  [Parameter(Mandatory=$true)][int]$Height,
  [Parameter(Mandatory=$true)][int]$ScaleFactor,
  [Parameter(Mandatory=$true)][int]$AutocontrastValue,
  [Parameter(Mandatory=$true)][int]$BinaryThreshold,
  [Parameter(Mandatory=$true)][int]$InvertValue
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
$Autocontrast = $AutocontrastValue -ne 0
$Invert = $InvertValue -ne 0
function ApplyPixelPreprocessing($Bitmap) {
  $needsPixels = $Autocontrast -or $BinaryThreshold -ge 0 -or $Invert
  if (-not $needsPixels) { return }
  $threshold = $null
  if ($BinaryThreshold -ge 0) { $threshold = $BinaryThreshold }
  $minGray = 255
  $maxGray = 0
  if ($Autocontrast) {
    for ($py = 0; $py -lt $Bitmap.Height; $py++) {
      for ($px = 0; $px -lt $Bitmap.Width; $px++) {
        $color = $Bitmap.GetPixel($px, $py)
        $gray = [int](($color.R * 0.299) + ($color.G * 0.587) + ($color.B * 0.114))
        if ($gray -lt $minGray) { $minGray = $gray }
        if ($gray -gt $maxGray) { $maxGray = $gray }
      }
    }
  }
  for ($py = 0; $py -lt $Bitmap.Height; $py++) {
    for ($px = 0; $px -lt $Bitmap.Width; $px++) {
      $color = $Bitmap.GetPixel($px, $py)
      $gray = [int](($color.R * 0.299) + ($color.G * 0.587) + ($color.B * 0.114))
      if ($Autocontrast -and $maxGray -gt $minGray) {
        $gray = [int][Math]::Round((($gray - $minGray) * 255.0) / ($maxGray - $minGray))
      }
      if ($null -ne $threshold) {
        if ($gray -ge $threshold) { $gray = 255 } else { $gray = 0 }
      }
      if ($Invert) { $gray = 255 - $gray }
      $Bitmap.SetPixel($px, $py, [System.Drawing.Color]::FromArgb($gray, $gray, $gray))
    }
  }
}
$source = [System.Drawing.Bitmap]::FromFile($SourcePath)
$targetWidth = [Math]::Max(1, $Width * $ScaleFactor)
$targetHeight = [Math]::Max(1, $Height * $ScaleFactor)
$crop = New-Object System.Drawing.Bitmap $targetWidth, $targetHeight
$graphics = [System.Drawing.Graphics]::FromImage($crop)
$graphics.Clear([System.Drawing.Color]::White)
$graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
$graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$destRect = New-Object System.Drawing.Rectangle 0, 0, $targetWidth, $targetHeight
$srcRect = New-Object System.Drawing.Rectangle $X, $Y, $Width, $Height
$graphics.DrawImage($source, $destRect, $srcRect, [System.Drawing.GraphicsUnit]::Pixel)
$graphics.Dispose()
ApplyPixelPreprocessing $crop
$crop.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
$crop.Dispose()
$source.Dispose()
''',
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-SourcePath",
            str(source_path),
            "-OutputPath",
            str(output_path),
            "-X",
            str(x),
            "-Y",
            str(y),
            "-Width",
            str(width),
            "-Height",
            str(height),
            "-ScaleFactor",
            str(variant.scale_factor),
            "-AutocontrastValue",
            "1" if variant.autocontrast else "0",
            "-BinaryThreshold",
            str(-1 if variant.binary_threshold is None else variant.binary_threshold),
            "-InvertValue",
            "1" if variant.invert else "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_image_format_whitelist_and_zip_safety(tmp_path: Path) -> None:
    image = tmp_path / "sample.png"
    write_png(image)
    assert read_image_info(image).width == 1200

    bad = tmp_path / "sample.gif"
    bad.write_bytes(b"GIF89a")
    with pytest.raises(ImageReadError):
        read_image_info(bad)

    archive = tmp_path / "safe.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(image, "sample.png")
    extracted = safe_extract_images_zip(archive, tmp_path / "out")
    assert extracted.image_count == 1

    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as zf:
        zf.writestr("../evil.png", b"nope")
    with pytest.raises(Exception):
        safe_extract_images_zip(traversal, tmp_path / "bad")

    executable = tmp_path / "exe.zip"
    with zipfile.ZipFile(executable, "w") as zf:
        zf.writestr("run.ps1", "Write-Host bad")
    with pytest.raises(Exception):
        safe_extract_images_zip(executable, tmp_path / "bad2")


@pytest.mark.skipif(os.name != "nt", reason="JPEG smoke uses Windows System.Drawing")
def test_jpeg_dimensions_are_supported(tmp_path: Path) -> None:
    image = tmp_path / "sample.jpg"
    _draw_text_image_with_powershell(image, "JPEG", "Jpeg")

    info = read_image_info(image)

    assert info.width == 420
    assert info.height == 120
    assert info.format == "jpeg"


def test_parser_distinguishes_aggregate_price_and_validates_market_cap() -> None:
    contract, warnings, errors = parse_ocr_contract(
        {
            "item_name": evidence("Synthetic Alpha"),
            "best_bid": evidence("12.34"),
            "best_ask": evidence("13.00"),
            "total_bid_quantity": evidence("3"),
            "total_ask_quantity": evidence("7"),
            "bid_levels": evidence("89.00+ 3"),
            "ask_levels": evidence("2000.01 7"),
        },
        item_key="admin-alpha",
    )
    assert warnings == []
    assert contract.bid_levels[0].exact_price is None
    assert contract.bid_levels[0].price_lower_bound == Decimal("89.00")
    assert contract.bid_levels[0].aggregation_type == "greater_than_or_equal"
    assert "price_above_market_cap" in errors


def test_parser_keeps_missing_quantities_null() -> None:
    contract, _warnings, errors = parse_ocr_contract(
        {
            "item_name": evidence("Synthetic Alpha"),
            "best_bid": evidence("12.34"),
            "best_ask": evidence("13.00"),
            "total_bid_quantity": evidence(""),
            "total_ask_quantity": evidence(""),
        },
        item_key="admin-alpha",
    )

    assert contract.total_bid_quantity is None
    assert contract.total_ask_quantity is None
    assert "total_bid_quantity_mismatch" in errors
    assert "total_ask_quantity_mismatch" in errors


def test_field_specific_ocr_pipeline_contract_is_explicit_and_stable() -> None:
    assert FIELD_OCR_PIPELINES["item_name"] != FIELD_OCR_PIPELINES["price"]
    assert FIELD_OCR_PIPELINES["quantity"] != FIELD_OCR_PIPELINES["price"]
    assert PRICE_SELECTION_ORDER == (
        "independent_roi_agreement",
        "repeated_candidate_agreement",
        "single_explicit_decimal",
        "single_integer_price",
    )


def test_current_config_sha_is_stable_and_order_independent() -> None:
    config = CurrentCutConfig()
    payload = config.to_json()
    reordered = dict(reversed(list(payload.items())))
    reordered["layout_profile"]["rois"] = dict(
        reversed(list(reordered["layout_profile"]["rois"].items()))
    )

    assert config.sha256() == CurrentCutConfig().sha256()
    assert stable_config_sha256(reordered) == config.sha256()
    assert payload["layout_profile"]["name"] == "gaijin-market-desktop-v1"
    assert payload["layout_profile"]["version"] == "1.2.0"


def test_current_config_sha_changes_for_roi_and_pipeline_changes() -> None:
    config = CurrentCutConfig()
    original = config.sha256()
    changed_roi = json.loads(json.dumps(config.to_json()))
    changed_roi["layout_profile"]["rois"]["best_bid"]["x"] = "0.226"
    changed_pipeline = json.loads(json.dumps(config.to_json()))
    changed_pipeline["price_pipeline"].append("experimental_variant")

    assert stable_config_sha256(changed_roi) != original
    assert stable_config_sha256(changed_pipeline) != original


def test_current_config_sha_excludes_run_metadata_and_private_inputs() -> None:
    payload = CurrentCutConfig().to_json()
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert "run_id" not in canonical
    assert "started_at" not in canonical
    assert "sample_id" not in canonical
    assert "ground_truth" not in canonical
    assert "C:\\" not in canonical
    assert ".png" not in canonical


def test_price_candidate_selection_agreement_conflict_and_decimal_rules() -> None:
    selected = select_price_candidate(
        field_name="best_bid", scalar_text="903 ， 01", first_level_text="价格 (GJN) 903 、 01"
    )
    assert selected.value == Decimal("903.01")
    assert selected.selection_reason == "independent_roi_agreement"

    conflict = select_price_candidate(
        field_name="best_bid", scalar_text="903.01", first_level_text="价格 (GJN) 904.01"
    )
    assert conflict.value is None
    assert "ocr_candidate_ambiguous" in conflict.errors

    single_decimal = select_price_candidate(field_name="best_ask", scalar_text="130.00")
    assert single_decimal.value == Decimal("130.00")
    assert single_decimal.selection_reason == "single_explicit_decimal"

    integer_price = select_price_candidate(field_name="best_bid", scalar_text="50")
    assert integer_price.value == Decimal("50")
    assert integer_price.selection_reason == "single_integer_price"

    unconfirmed = select_price_candidate(field_name="best_ask", scalar_text="13000")
    assert unconfirmed.value is None
    assert {"price_decimal_unconfirmed", "price_ocr_invalid", "best_ask_missing"}.issubset(set(unconfirmed.errors))
    assert unconfirmed.candidates[0].suggested_decimal_value == Decimal("130.00")
    assert unconfirmed.candidates[0].suggestion_reason == "possible_missing_decimal_point"

    missing_decimal = select_price_candidate(field_name="best_bid", scalar_text="1810")
    assert missing_decimal.value is None
    assert "price_decimal_unconfirmed" in missing_decimal.errors
    assert missing_decimal.candidates[0].suggested_decimal_value == Decimal("18.10")

    too_many_decimals = select_price_candidate(field_name="best_bid", scalar_text="12.345")
    assert too_many_decimals.value is None
    assert "price_ocr_invalid" in too_many_decimals.errors

    table = select_price_candidate(
        field_name="best_bid",
        scalar_text="50 、 00",
        first_level_text="数量 1 1 1 2 价格 (GJN) 50 、 00 44 、 67 44 、 60",
    )
    assert table.value == Decimal("50.00")
    assert table.selection_reason == "independent_roi_agreement"


def test_numeric_ocr_repairs_only_in_numeric_context() -> None:
    token, corrections, _explicit = normalize_numeric_ocr_token("I2O，5O")
    assert token == "120.50"
    assert "numeric_confusable_repaired" in corrections

    text_token, text_corrections, _explicit = normalize_numeric_ocr_token("SOLD OUT")
    assert text_token == "SOLDOUT"
    assert "numeric_confusable_repaired" not in text_corrections


def test_quantity_candidate_uses_single_summary_integer_only() -> None:
    assert parse_quantity_candidate("172")[0] == 172
    value, errors = parse_quantity_candidate("数量 1 1 1 价格 51.99")
    assert value is None
    assert "quantity_ocr_invalid" in errors


def test_quantity_candidate_uses_left_and_right_label_anchors() -> None:
    bid = select_quantity_candidate(
        field_name="total_bid_quantity",
        compact_evidence=evidence(""),
        summary_evidence=quantity_evidence(
            "total_bid_quantity_summary",
            line(
                "正在购买 121",
                box=(0, 0, 180, 30),
                words=[word("正在购买", 0, 0, 90, 30), word("121", 110, 0, 50, 30)],
            ),
        ),
        side="bid",
    )
    ask = select_quantity_candidate(
        field_name="total_ask_quantity",
        compact_evidence=evidence(""),
        summary_evidence=quantity_evidence(
            "total_ask_quantity_summary",
            line(
                "正在出售 172",
                box=(0, 0, 180, 30),
                words=[word("正在出售", 0, 0, 90, 30), word("172", 120, 0, 50, 30)],
            ),
        ),
        side="ask",
    )

    assert bid.value == 121
    assert bid.selection_reason == "single_label_anchored_quantity"
    assert ask.value == 172
    assert ask.selection_reason == "single_label_anchored_quantity"


def test_quantity_candidate_allows_number_below_label() -> None:
    selected = select_quantity_candidate(
        field_name="total_bid_quantity",
        compact_evidence=evidence(""),
        summary_evidence=quantity_evidence(
            "total_bid_quantity_summary",
            line("正在购买", order=0, box=(0, 0, 90, 30), words=[word("正在购买", 0, 0, 90, 30)]),
            line("251", order=1, box=(20, 38, 55, 30), words=[word("251", 20, 38, 55, 30)]),
        ),
        side="bid",
    )

    assert selected.value == 251


def test_quantity_candidate_rejects_table_prices_and_level_quantities() -> None:
    price_like = select_quantity_candidate(
        field_name="total_ask_quantity",
        compact_evidence=evidence(""),
        summary_evidence=quantity_evidence(
            "total_ask_quantity_summary",
            line(
                "正在出售 51.99 GJN",
                words=[word("正在出售", 0, 0, 90, 30), word("51", 100, 0, 30, 30)],
            ),
        ),
        side="ask",
    )
    assert price_like.value is None
    assert "quantity_candidate_looks_like_price" in price_like.errors

    table_level = select_quantity_candidate(
        field_name="total_ask_quantity",
        compact_evidence=evidence(""),
        summary_evidence=quantity_evidence(
            "total_ask_quantity_summary",
            line("价格 (GJN) 51.99", order=0, words=[word("51", 90, 0, 30, 30)]),
            line("1", order=1, words=[word("1", 20, 40, 10, 30)]),
        ),
        side="ask",
    )
    assert table_level.value is None
    assert "quantity_label_not_detected" in table_level.errors


def test_quantity_sources_agree_conflict_and_compact_artifact_handling() -> None:
    agreed = select_quantity_candidate(
        field_name="total_ask_quantity",
        compact_evidence=evidence("172"),
        summary_evidence=quantity_evidence(
            "total_ask_quantity_summary",
            line("正在出售 172", words=[word("正在出售", 0, 0, 90, 30), word("172", 100, 0, 50, 30)]),
        ),
        side="ask",
    )
    assert agreed.value == 172
    assert agreed.selection_reason == "independent_quantity_source_agreement"

    conflict = select_quantity_candidate(
        field_name="total_ask_quantity",
        compact_evidence=evidence("172"),
        summary_evidence=quantity_evidence(
            "total_ask_quantity_summary",
            line("正在出售 173", words=[word("正在出售", 0, 0, 90, 30), word("173", 100, 0, 50, 30)]),
        ),
        side="ask",
    )
    assert conflict.value is None
    assert "quantity_candidate_ambiguous" in conflict.errors

    artifact = select_quantity_candidate(
        field_name="total_ask_quantity",
        compact_evidence=evidence("172 力 5"),
        summary_evidence=None,
        side="ask",
    )
    assert artifact.value == 172


def test_quantity_summary_price_tail_does_not_override_compact_quantity() -> None:
    selected = select_quantity_candidate(
        field_name="total_ask_quantity",
        compact_evidence=evidence("52"),
        summary_evidence=quantity_evidence(
            "total_ask_quantity_summary",
            line("正在出售", order=0, words=[word("正在出售", 0, 0, 90, 30)]),
            line("力 135 O 0", order=1, words=[word("力", 0, 40, 20, 30), word("135", 70, 40, 60, 30)]),
        ),
        side="ask",
    )

    assert selected.value == 52


def test_quantity_candidate_missing_label_and_ocr_failure_do_not_return_zero() -> None:
    selected = select_quantity_candidate(
        field_name="total_bid_quantity",
        compact_evidence=evidence(""),
        summary_evidence=quantity_evidence(
            "total_bid_quantity_summary",
            line("没有要出售", words=[word("没有", 0, 0, 40, 30)]),
        ),
        side="bid",
    )

    assert selected.value is None
    assert "quantity_label_not_detected" in selected.errors


def test_parser_ocr_zero_stays_at_ocr_layer_but_manual_zero_domain_rule_remains() -> None:
    contract, _warnings, errors = parse_ocr_contract(
        {
            "item_name": evidence("Synthetic Alpha"),
            "best_bid": evidence("价格 (GJN) 0 ℃ 0"),
            "best_ask": evidence("13.00"),
            "total_bid_quantity": evidence("1"),
            "total_ask_quantity": evidence("1"),
        },
        item_key="admin-alpha",
    )
    assert contract.best_bid is None
    assert "price_ocr_invalid" in errors
    assert "non_positive_price" not in errors

    contract, _warnings, errors = parse_ocr_contract(
        {
            "item_name": evidence("Synthetic Alpha"),
            "best_bid": evidence("0"),
            "best_ask": evidence("13.00"),
        },
        item_key="admin-alpha",
    )
    assert contract.best_bid == Decimal("0")
    assert "non_positive_price" in errors


def test_item_name_safe_normalization_and_no_edit_distance_match() -> None:
    assert normalize_item_name("测 试 Mk.3D （ 甲 国 ）") == "测试 Mk.3D(甲国)"
    expected = valid_row(item_name="Synthetic-10A（甲国）")
    recognized = normalize_item_name("Synthetic-IOA（甲国）")
    assert normalize_item_name(expected["item_name"]) != recognized


def evidence(text: str):
    from api.screen_recognition.contracts import OcrFieldEvidence

    return OcrFieldEvidence(field_name="field", raw_text=text, confidence=None)


def quantity_evidence(field_name: str, *lines: object):
    from api.screen_recognition.contracts import OcrFieldEvidence

    return OcrFieldEvidence(
        field_name=field_name,
        raw_text=" ".join(getattr(line, "text", "") for line in lines),
        confidence=None,
        lines=tuple(lines),
    )


def line(
    text: str,
    order: int = 0,
    box: tuple[int, int, int, int] = (0, 0, 200, 40),
    words: list[object] | None = None,
):
    from api.screen_recognition.contracts import OcrLineEvidence

    return OcrLineEvidence(
        text=text,
        order=order,
        bounding_box=ocr_box(*box),
        words=tuple(words or []),
    )


def word(text: str, x: int, y: int, width: int, height: int):
    from api.screen_recognition.contracts import OcrWordEvidence

    return OcrWordEvidence(text=text, order=0, bounding_box=ocr_box(x, y, width, height))


def ocr_box(x: int, y: int, width: int, height: int):
    from api.screen_recognition.contracts import OcrBoundingBox

    return OcrBoundingBox(
        x=Decimal(x),
        y=Decimal(y),
        width=Decimal(width),
        height=Decimal(height),
    )


def test_sidecar_runner_generates_parser_only_outputs_without_database_or_csv(tmp_path: Path) -> None:
    images = tmp_path / "images"
    output = tmp_path / "output"
    images.mkdir()
    write_png(images / "sample.png")
    write_sidecar(images / "sample.ocr.txt")
    gt = tmp_path / "ground_truth.jsonl"
    write_ground_truth(gt, [valid_row()])

    result = run_cut(
        CutRunConfig(
            images_dir=images,
            ground_truth_path=gt,
            output_dir=output,
            layout_profile_name="gaijin-market-desktop-v1",
            ocr_backend_name="sidecar",
            pretty=True,
        )
    )

    assert result.run_metadata["test_scope"] == "parser_only"
    assert result.run_metadata["database_access"] is False
    assert result.run_metadata["network_access"] is False
    assert result.run_metadata["candidate_csv_supported"] is False
    assert result.run_metadata["config_sha256"] == stable_config_sha256(
        result.run_metadata["current_config"]
    )
    assert result.run_metadata["current_config"]["layout_profile"]["version"] == "1.2.0"
    assert len(result.run_metadata["config_sha256"]) == 64
    assert not (output / "candidate_import.csv").exists()
    effective_config = json.loads(
        (output / "effective_current_config.json").read_text(encoding="utf-8")
    )
    assert effective_config == result.run_metadata["current_config"]
    assert (output / "summary.json").is_file()
    assert (output / "report.md").is_file()
    assert result.summary["passed"] == 1


def test_not_configured_backend_is_structured(tmp_path: Path) -> None:
    images = tmp_path / "images"
    output = tmp_path / "output"
    images.mkdir()
    write_png(images / "sample.png")
    gt = tmp_path / "ground_truth.jsonl"
    write_ground_truth(gt, [valid_row()])

    result = run_cut(
        CutRunConfig(
            images_dir=images,
            ground_truth_path=gt,
            output_dir=output,
            layout_profile_name="gaijin-market-desktop-v1",
            ocr_backend_name="none",
        )
    )

    assert result.results[0].errors == ("image_recognizer_not_configured",)


def test_ocr_backend_exception_is_structured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class BrokenRecognizer:
        backend_name = "broken"
        backend_version = "1"
        test_scope = "end_to_end"

        def recognize(self, invocation: OcrInvocation):
            raise OcrBackendError("boom")

    import api.screen_recognition.runner as runner

    monkeypatch.setattr(runner, "get_recognizer", lambda _name: BrokenRecognizer())
    images = tmp_path / "images"
    output = tmp_path / "output"
    images.mkdir()
    write_png(images / "sample.png")
    gt = tmp_path / "ground_truth.jsonl"
    write_ground_truth(gt, [valid_row()])

    result = run_cut(
        CutRunConfig(
            images_dir=images,
            ground_truth_path=gt,
            output_dir=output,
            layout_profile_name="gaijin-market-desktop-v1",
            ocr_backend_name="broken",
        )
    )

    assert result.results[0].errors == ("ocr_backend_error",)


def test_item_key_only_comes_from_manifest_and_filename_is_not_answer(tmp_path: Path) -> None:
    images = tmp_path / "images"
    output = tmp_path / "output"
    images.mkdir()
    write_png(images / "filename_has_not_answer.png")
    write_sidecar(images / "filename_has_not_answer.ocr.txt", item_name="Wrong Name")
    gt = tmp_path / "ground_truth.jsonl"
    write_ground_truth(gt, [valid_row(filename="filename_has_not_answer.png")])

    result = run_cut(
        CutRunConfig(
            images_dir=images,
            ground_truth_path=gt,
            output_dir=output,
            layout_profile_name="gaijin-market-desktop-v1",
            ocr_backend_name="sidecar",
        )
    )

    recognized = result.results[0].recognized
    assert recognized.item_key == "admin-alpha"
    assert recognized.item_key_source == "ground_truth_manifest"
    assert recognized.item_name == "Wrong Name"
    assert "item_name_mismatch" in result.results[0].errors


def test_parser_only_and_end_to_end_statistics_are_not_mixed(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    write_png(images / "sample.png")
    write_sidecar(images / "sample.ocr.txt")
    gt = tmp_path / "ground_truth.jsonl"
    write_ground_truth(gt, [valid_row()])

    parser_result = run_cut(
        CutRunConfig(
            images_dir=images,
            ground_truth_path=gt,
            output_dir=tmp_path / "parser",
            layout_profile_name="gaijin-market-desktop-v1",
            ocr_backend_name="sidecar",
        )
    )
    end_to_end_result = run_cut(
        CutRunConfig(
            images_dir=images,
            ground_truth_path=gt,
            output_dir=tmp_path / "e2e",
            layout_profile_name="gaijin-market-desktop-v1",
            ocr_backend_name="none",
        )
    )

    assert parser_result.run_metadata["test_scope"] == "parser_only"
    assert end_to_end_result.run_metadata["test_scope"] == "end_to_end"
    assert parser_result.summary["processed_images"] == 1
    assert end_to_end_result.summary["processed_images"] == 1


def test_missing_and_extra_images_are_reported(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    write_png(images / "extra.png")
    gt = tmp_path / "ground_truth.jsonl"
    write_ground_truth(gt, [valid_row(filename="missing.png")])

    result = run_cut(
        CutRunConfig(
            images_dir=images,
            ground_truth_path=gt,
            output_dir=tmp_path / "out",
            layout_profile_name="gaijin-market-desktop-v1",
            ocr_backend_name="sidecar",
        )
    )

    assert result.summary["files_found"] == 1
    assert result.results[0].status.value == "unreadable"


def test_threshold_pass_and_fail() -> None:
    assert summarize_results(results=[], files_found=0, ground_truth_entries=0)["overall_status"] == "failed"


def test_cli_help_and_run_exit_codes(tmp_path: Path) -> None:
    images = tmp_path / "images"
    output = tmp_path / "output"
    images.mkdir()
    write_png(images / "sample.png")
    write_sidecar(images / "sample.ocr.txt", best_bid="1.00")
    gt = tmp_path / "ground_truth.jsonl"
    write_ground_truth(gt, [valid_row()])

    help_result = subprocess.run(
        [sys.executable, "-m", "api.screen_recognition_cut", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0

    failed = subprocess.run(
        [
            sys.executable,
            "-m",
            "api.screen_recognition_cut",
            "run",
            "--images-dir",
            str(images),
            "--ground-truth",
            str(gt),
            "--output-dir",
            str(output),
            "--ocr-backend",
            "sidecar",
            "--strict",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode == 3


def test_init_command_smoke(tmp_path: Path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    write_png(images / "a.png")
    write_png(images / "b.jpg")
    output = tmp_path / "ground_truth.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "api.screen_recognition_cut",
            "init",
            "--images-dir",
            str(images),
            "--output",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["filename"] for row in rows] == ["a.png", "b.jpg"]
    assert rows[0]["item_key"] == ""
    assert rows[0]["item_name"] == ""


def test_no_network_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", fail_socket)
    images = tmp_path / "images"
    images.mkdir()
    write_png(images / "sample.png")
    write_sidecar(images / "sample.ocr.txt")
    gt = tmp_path / "ground_truth.jsonl"
    write_ground_truth(gt, [valid_row()])
    result = run_cut(
        CutRunConfig(
            images_dir=images,
            ground_truth_path=gt,
            output_dir=tmp_path / "out",
            layout_profile_name="gaijin-market-desktop-v1",
            ocr_backend_name="sidecar",
        )
    )
    assert result.run_metadata["network_access"] is False


def test_private_evaluation_reports_empty_fixture_directory_without_private_paths(tmp_path: Path) -> None:
    report_path = tmp_path / "artifacts" / "private" / "screen-recognition-evaluation" / "report.json"
    report = evaluate_private_fixtures(
        input_dir=tmp_path / "artifacts" / "private" / "screen-recognition-evaluation",
        output_path=report_path,
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="none",
        pretty=True,
    )

    assert report["message"] == PRIVATE_FIXTURE_MESSAGE
    assert report["summary"]["fixture_count"] == 0
    assert report["private_paths_recorded"] is False
    assert report["database_access"] is False
    assert "Authorization" not in report_path.read_text(encoding="utf-8")


def write_private_fixture(root: Path, name: str = "sample-a", *, browser: str = "edge", zoom: str = "0.8") -> Path:
    fixture_dir = root / browser / zoom
    fixture_dir.mkdir(parents=True, exist_ok=True)
    image = fixture_dir / f"{name}.png"
    write_png(image)
    image.with_suffix(".json").write_text(
        json.dumps({"browser": browser, "browser_zoom": zoom, "sample_label": name}),
        encoding="utf-8",
    )
    return image


def read_private_ground_truth_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_private_ground_truth_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def diagnostics_card_count(path: Path) -> int:
    return path.read_text(encoding="utf-8").count('class="fixture-card"')


def synthetic_private_result(
    *,
    fixture_id: str,
    browser: str,
    zoom: str,
    sample: str,
    exact_bid: bool,
    exact_ask: bool,
    ask_wrong: bool = False,
) -> dict[str, object]:
    return {
        "fixture_id": fixture_id,
        "browser": browser,
        "declared_zoom": zoom,
        "sample_label": sample,
        "ground_truth_status": "reviewed",
        "requires_review": ask_wrong,
        "timings": {
            "total_ms": 1000,
            "image_decode_ms": 10,
            "roi_resolution_ms": 20,
            "ocr_ms": 900,
            "ocr_output_parsing_ms": 5,
        },
        "accuracy": {
            "best_bid_exact_match": exact_bid,
            "best_ask_exact_match": exact_ask,
            "both_exact_match": exact_bid and exact_ask,
            "bid_missing": False,
            "ask_missing": False,
            "bid_wrong_value": not exact_bid,
            "ask_wrong_value": ask_wrong or not exact_ask,
            "false_confident_bid": False,
            "false_confident_ask": False,
            "requires_review_false_negative": False,
        },
        "recognized": {
            "best_bid": "12.34" if exact_bid else "12.30",
            "best_ask": "13.00" if exact_ask else "13.50",
        },
        "profile": {
            "powershell_process_count": 1,
            "ocr_invocation_count": 2,
            "pipeline_count_attempted": 2,
            "pipeline_count_completed": 2,
            "total_ocr_duration_ms": 700,
            "ocr_engine_initialization_total_ms": 100,
            "ocr_execution_total_ms": 30,
            "powershell_process_startup_overhead_ms": 40,
            "per_pipeline_duration_ms": [
                {
                    "field_name": "best_bid",
                    "pipeline_name": "gray_3x",
                    "duration_ms": 300,
                    "produced_text": True,
                    "selected": True,
                },
                {
                    "field_name": "best_ask",
                    "pipeline_name": "gray_autocontrast_4x",
                    "duration_ms": 400,
                    "produced_text": True,
                    "selected": True,
                },
            ],
        },
        "raw_ocr": {
            "diagnostics": {"helper_total_duration_ms": 850},
            "fields": {
                "best_bid": {
                    "raw_text": "secret raw 12.34",
                    "warnings": ["preprocessing_pipeline:gray_3x"],
                },
                "best_ask": {
                    "raw_text": "secret raw 13.00",
                    "warnings": ["preprocessing_pipeline:gray_autocontrast_4x"],
                },
            },
        },
    }


def test_private_ground_truth_template_uses_paired_fixtures_without_prefilling_truth(tmp_path: Path) -> None:
    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a")
    write_private_fixture(input_dir, "sample-b", browser="chrome", zoom="1.0")
    (input_dir / "report.json").write_text("{}", encoding="utf-8")
    unpaired = input_dir / "diagnostics-crops" / "crop.png"
    unpaired.parent.mkdir()
    write_png(unpaired)
    output = input_dir / "ground-truth.csv"

    result = create_private_ground_truth_template(input_dir=input_dir, output_path=output)
    rows = read_private_ground_truth_rows(output)

    assert result.discovered_fixture_count == 2
    assert result.appended_row_count == 2
    assert len(rows) == 2
    assert {row["reviewed"] for row in rows} == {"false"}
    assert all(row["expected_best_bid"] == "" and row["expected_best_ask"] == "" for row in rows)
    assert all(not row["fixture_id"].endswith(".png") for row in rows)


def test_private_ground_truth_template_appends_without_overwriting_reviewed_rows(tmp_path: Path) -> None:
    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a")
    output = input_dir / "ground-truth.csv"
    create_private_ground_truth_template(input_dir=input_dir, output_path=output)
    rows = read_private_ground_truth_rows(output)
    rows[0]["expected_best_bid"] = "12.34"
    rows[0]["expected_best_ask"] = "13.00"
    rows[0]["notes"] = "human reviewed"
    rows[0]["reviewed"] = "true"
    write_private_ground_truth_rows(output, rows)
    original_first = output.read_text(encoding="utf-8").splitlines()[1]

    write_private_fixture(input_dir, "sample-b")
    result = create_private_ground_truth_template(input_dir=input_dir, output_path=output)
    updated = output.read_text(encoding="utf-8").splitlines()

    assert result.existing_row_count == 1
    assert result.appended_row_count == 1
    assert updated[1] == original_first
    rows_after = read_private_ground_truth_rows(output)
    assert rows_after[0]["expected_best_bid"] == "12.34"
    assert rows_after[1]["expected_best_bid"] == ""


def test_private_evaluation_help_does_not_initialize_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.screen_recognition.evaluate as evaluate_module

    monkeypatch.setattr(
        evaluate_module,
        "get_recognizer",
        lambda *_args, **_kwargs: pytest.fail("help should not initialize OCR"),
    )

    with pytest.raises(SystemExit) as exc:
        evaluate_module.main(["--help"])

    assert exc.value.code == 0


def test_private_evaluation_dry_run_skips_ocr_and_ignores_report_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import api.screen_recognition.evaluate as evaluate_module

    input_dir = tmp_path / "private"
    write_private_fixture(input_dir)
    (input_dir / "report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        evaluate_module,
        "get_recognizer",
        lambda *_args, **_kwargs: pytest.fail("dry-run should not initialize OCR"),
    )

    report = evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="windows-ocr",
        dry_run=True,
        verbose=True,
        progress_stream=StringIO(),
    )

    assert report["dry_run"] is True
    assert report["summary"]["fixture_count"] == 1
    assert report["summary"]["metadata_count"] == 1
    assert report["summary"]["processed_count"] == 1
    assert report["results"][0]["status"] == "dry_run"
    assert report["results"][0]["ocr_skipped"] is True
    assert report["results"][0]["requires_review"] is False
    assert "filename" not in report["results"][0]
    assert "item_name_raw" not in report["results"][0]
    assert not (input_dir / "report.json.tmp").exists()


def test_private_diagnostics_default_paths_are_output_scoped_and_render_all_fixtures(tmp_path: Path) -> None:
    input_dir = tmp_path / "private"
    for browser in ("edge", "chrome"):
        for index in range(10):
            write_private_fixture(input_dir, f"sample-{index}", browser=browser, zoom="0.8")

    report = evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="none",
        dry_run=True,
        progress_stream=StringIO(),
    )

    diagnostics_path = input_dir / "report.diagnostics.html"
    html = diagnostics_path.read_text(encoding="utf-8")
    crop_files = list((input_dir / "report.diagnostics").glob("*.png"))

    assert report["summary"]["processed_count"] == 20
    assert diagnostics_card_count(diagnostics_path) == 20
    assert len(crop_files) == 40
    assert len({path.name for path in crop_files}) == 40
    assert (input_dir / "report.private.json").is_file()
    assert not (input_dir / "diagnostics.html").exists()
    assert str(input_dir) not in html
    assert "browser-filter" in html
    assert "zoom-filter" in html
    assert "sample-filter" in html


def test_private_diagnostics_single_report_does_not_overwrite_full_report(tmp_path: Path) -> None:
    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a", browser="edge", zoom="0.8")
    write_private_fixture(input_dir, "sample-b", browser="edge", zoom="0.9")

    evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="none",
        dry_run=True,
        progress_stream=StringIO(),
    )
    full_diagnostics = input_dir / "report.diagnostics.html"
    assert diagnostics_card_count(full_diagnostics) == 2

    evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "single.report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="none",
        dry_run=True,
        limit=1,
        progress_stream=StringIO(),
    )

    assert diagnostics_card_count(full_diagnostics) == 2
    assert diagnostics_card_count(input_dir / "single.report.diagnostics.html") == 1
    assert (input_dir / "single.report.private.json").is_file()
    assert (input_dir / "single.report.diagnostics").is_dir()


def test_private_diagnostics_duplicate_labels_use_unique_preview_names(tmp_path: Path) -> None:
    input_dir = tmp_path / "private"
    first = write_private_fixture(input_dir, "file-a", browser="edge", zoom="0.8")
    second = write_private_fixture(input_dir, "file-b", browser="edge", zoom="0.8")
    for image in (first, second):
        image.with_suffix(".json").write_text(
            json.dumps({"browser": "edge", "browser_zoom": "0.8", "sample_label": "sample-a"}),
            encoding="utf-8",
        )

    evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="none",
        dry_run=True,
        progress_stream=StringIO(),
    )

    html = (input_dir / "report.diagnostics.html").read_text(encoding="utf-8")
    crop_names = [path.name for path in (input_dir / "report.diagnostics").glob("*.png")]

    assert diagnostics_card_count(input_dir / "report.diagnostics.html") == 2
    assert len(crop_names) == 4
    assert len(set(crop_names)) == 4
    assert html.count('id="edge-080-sample-a-') == 2


def test_private_evaluation_accuracy_false_confident_and_safe_report_privacy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import api.screen_recognition.evaluate as evaluate_module

    class FakeRecognizer:
        backend_name = "fake"

        def recognize(self, _invocation: OcrInvocation) -> OcrResult:
            return OcrResult(
                backend_name="fake",
                backend_version="1",
                fields={"best_bid": evidence("12.34"), "best_ask": evidence("14.00")},
                warnings=(),
                diagnostics={
                    "powershell_process_count": 1,
                    "ocr_invocation_count": 2,
                    "pipeline_count_attempted": 2,
                    "pipeline_count_completed": 2,
                    "early_exit_used": False,
                    "total_ocr_duration_ms": 20,
                    "per_pipeline_duration_ms": [
                        {"pipeline_name": "gray_3x", "duration_ms": 10, "selected": True}
                    ],
                },
            )

    def fake_parse(_fields: object, *, item_key: str | None):
        return (
            ScreenContract(best_bid=Decimal("12.34"), best_ask=Decimal("14.00")),
            [],
            [],
        )

    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a")
    gt_path = input_dir / "ground-truth.csv"
    create_private_ground_truth_template(input_dir=input_dir, output_path=gt_path)
    rows = read_private_ground_truth_rows(gt_path)
    rows[0]["expected_best_bid"] = "12.34"
    rows[0]["expected_best_ask"] = "13.00"
    rows[0]["reviewed"] = "true"
    write_private_ground_truth_rows(gt_path, rows)
    monkeypatch.setattr(evaluate_module, "get_recognizer", lambda *_args, **_kwargs: FakeRecognizer())
    monkeypatch.setattr(evaluate_module, "parse_ocr_contract", fake_parse)

    report = evaluate_module.evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="fake",
        ground_truth_path=gt_path,
        profile=True,
        progress_stream=StringIO(),
    )

    accuracy = report["accuracy"]["overall"]["all"]
    assert accuracy["reviewed_count"] == 1
    assert accuracy["best_bid_exact_match"] == 1
    assert accuracy["ask_wrong_value"] == 1
    assert accuracy["false_confident_ask"] == 1
    assert report["profile_summary"]["totals"]["powershell_process_count"] == 1
    safe_report = (input_dir / "report.json").read_text(encoding="utf-8")
    assert "13.00" not in safe_report
    assert "14.00" not in safe_report
    private_report = json.loads((input_dir / "report.private.json").read_text(encoding="utf-8"))
    assert private_report["accuracy_private"][0]["expected"]["expected_best_ask"] == "13.00"


def test_private_evaluation_reviewed_false_is_not_scored(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import api.screen_recognition.evaluate as evaluate_module

    class FakeRecognizer:
        backend_name = "fake"

        def recognize(self, _invocation: OcrInvocation) -> OcrResult:
            return OcrResult(backend_name="fake", backend_version="1", fields={}, warnings=())

    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a")
    gt_path = input_dir / "ground-truth.csv"
    create_private_ground_truth_template(input_dir=input_dir, output_path=gt_path)
    rows = read_private_ground_truth_rows(gt_path)
    rows[0]["expected_best_bid"] = "12.34"
    rows[0]["expected_best_ask"] = "13.00"
    rows[0]["reviewed"] = "false"
    write_private_ground_truth_rows(gt_path, rows)
    monkeypatch.setattr(evaluate_module, "get_recognizer", lambda *_args, **_kwargs: FakeRecognizer())
    monkeypatch.setattr(
        evaluate_module,
        "parse_ocr_contract",
        lambda _fields, *, item_key: (ScreenContract(best_bid=Decimal("12.34"), best_ask=Decimal("13.00")), [], []),
    )

    report = evaluate_module.evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="fake",
        ground_truth_path=gt_path,
        progress_stream=StringIO(),
    )

    assert report["results"][0]["ground_truth_status"] == "not_reviewed"
    assert report["accuracy"]["overall"]["all"]["reviewed_count"] == 0
    assert report["accuracy"]["overall"]["all"]["ground_truth_not_reviewed"] == 1


def test_private_diagnostics_failed_and_unreviewed_fixture_remains_visible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import api.screen_recognition.evaluate as evaluate_module

    class TimeoutRecognizer:
        backend_name = "fake"

        def recognize(self, _invocation: OcrInvocation) -> OcrResult:
            raise OcrBackendTimeoutError("timeout")

    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a")
    gt_path = input_dir / "ground-truth.csv"
    create_private_ground_truth_template(input_dir=input_dir, output_path=gt_path)
    monkeypatch.setattr(evaluate_module, "get_recognizer", lambda *_args, **_kwargs: TimeoutRecognizer())

    evaluate_module.evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="fake",
        ground_truth_path=gt_path,
        progress_stream=StringIO(),
    )

    html = (input_dir / "report.diagnostics.html").read_text(encoding="utf-8")
    assert diagnostics_card_count(input_dir / "report.diagnostics.html") == 1
    assert "ocr_timeout" in html
    assert "ground_truth=not_reviewed" in html


def test_private_diagnostics_missing_roi_preview_uses_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import api.screen_recognition.evaluate as evaluate_module

    profile = LayoutProfile(
        name="no-rois",
        version="1",
        min_width=1,
        min_height=1,
        min_aspect_ratio=Decimal("0.1"),
        max_aspect_ratio=Decimal("10"),
        rois={},
    )
    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a")
    monkeypatch.setattr(evaluate_module, "get_layout_profile", lambda _name: profile)

    evaluate_module.evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="no-rois",
        ocr_backend_name="none",
        dry_run=True,
        progress_stream=StringIO(),
    )

    html = (input_dir / "report.diagnostics.html").read_text(encoding="utf-8")
    assert diagnostics_card_count(input_dir / "report.diagnostics.html") == 1
    assert "unavailable" in html


def test_regenerate_private_diagnostics_from_report_does_not_initialize_ocr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import api.screen_recognition.evaluate as evaluate_module

    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a")
    write_private_fixture(input_dir, "sample-b")
    evaluate_module.evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="none",
        dry_run=True,
        progress_stream=StringIO(),
    )
    (input_dir / "report.diagnostics.html").unlink()
    monkeypatch.setattr(
        evaluate_module,
        "get_recognizer",
        lambda *_args, **_kwargs: pytest.fail("regeneration should not initialize OCR"),
    )

    result = regenerate_diagnostics_from_private_report(
        private_report_path=input_dir / "report.private.json",
    )

    assert result["fixture_count"] == 2
    assert diagnostics_card_count(input_dir / "report.diagnostics.html") == 2


def test_anonymous_private_report_diagnostics_keeps_split_covered_and_redacted() -> None:
    results = []
    for browser in ("edge", "chrome"):
        for zoom in ("0.8", "0.9", "1", "1.1", "1.25"):
            for sample in ("sample-a", "sample-b"):
                exact_bid = sample == "sample-a"
                exact_ask = browser == "edge"
                results.append(
                    synthetic_private_result(
                        fixture_id=f"{browser}/{zoom}/{sample}",
                        browser=browser,
                        zoom=zoom,
                        sample=sample,
                        exact_bid=exact_bid,
                        exact_ask=exact_ask,
                    )
                )

    diagnostics = build_anonymous_diagnostics(
        {"schema_version": "private-test", "results": results}
    )
    validation_coverage = diagnostics["split"]["coverage"]["validation"]
    tuning_coverage = diagnostics["split"]["coverage"]["tuning"]
    assert diagnostics["split"]["validation_count"] == 8
    assert diagnostics["split"]["tuning_count"] == 12
    assert validation_coverage["browser"] == ["chrome", "edge"]
    assert validation_coverage["declared_zoom"] == ["0.8", "0.9", "1", "1.1", "1.25"]
    assert validation_coverage["sample_label"] == ["sample-a", "sample-b"]
    assert tuning_coverage["browser"] == ["chrome", "edge"]
    assert tuning_coverage["declared_zoom"] == ["0.8", "0.9", "1", "1.1", "1.25"]
    assert tuning_coverage["sample_label"] == ["sample-a", "sample-b"]
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "C:\\" not in serialized
    assert "12.34" not in serialized
    assert "13.00" not in serialized
    assert "secret raw" not in serialized
    assert "selected_outputs_only" in serialized


def test_anonymous_private_report_diagnostics_classifies_pipeline_value() -> None:
    diagnostics = build_anonymous_diagnostics(
        {
            "schema_version": "private-test",
            "results": [
                synthetic_private_result(
                    fixture_id="edge/0.8/sample-a",
                    browser="edge",
                    zoom="0.8",
                    sample="sample-a",
                    exact_bid=True,
                    exact_ask=False,
                    ask_wrong=True,
                )
            ],
        }
    )

    pipelines = diagnostics["pipeline_diagnostics"]["pipelines"]
    bid_pipeline = next(
        item
        for item in pipelines
        if item["field_name"] == "best_bid" and item["pipeline_name"] == "gray_3x"
    )
    ask_pipeline = next(
        item
        for item in pipelines
        if item["field_name"] == "best_ask" and item["pipeline_name"] == "gray_autocontrast_4x"
    )
    assert bid_pipeline["attempt_count"] == 1
    assert bid_pipeline["selected_best_bid_exact_count"] == 1
    assert ask_pipeline["selected_wrong_value_count"] == 1
    assert (
        "best_ask::gray_autocontrast_4x"
        in diagnostics["pipeline_diagnostics"]["classifications"]["error_prone_selected_candidates"]
    )


def test_private_diagnostics_redacts_sensitive_markers_from_html(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import api.screen_recognition.evaluate as evaluate_module

    class SensitiveRecognizer:
        backend_name = "fake"

        def recognize(self, _invocation: OcrInvocation) -> OcrResult:
            return OcrResult(
                backend_name="fake",
                backend_version="1",
                fields={"best_bid": evidence("https://example.invalid Authorization token pairing")},
                warnings=(),
            )

    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a")
    monkeypatch.setattr(evaluate_module, "get_recognizer", lambda *_args, **_kwargs: SensitiveRecognizer())
    monkeypatch.setattr(
        evaluate_module,
        "parse_ocr_contract",
        lambda _fields, *, item_key: (ScreenContract(), [], ["best_bid_missing", "best_ask_missing"]),
    )

    evaluate_module.evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="fake",
        progress_stream=StringIO(),
    )

    html = (input_dir / "report.diagnostics.html").read_text(encoding="utf-8").lower()
    assert "https://example.invalid" not in html
    assert "authorization" not in html
    assert "token" not in html
    assert "pairing" not in html


def test_private_evaluation_limit_and_filters_process_one_fixture(tmp_path: Path) -> None:
    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a", browser="edge", zoom="0.8")
    write_private_fixture(input_dir, "sample-b", browser="chrome", zoom="1.0")

    report = evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="none",
        dry_run=True,
        limit=1,
        only_browser="edge",
        only_zoom="0.8",
        only_sample="sample-a",
        progress_stream=StringIO(),
    )

    assert report["summary"]["fixture_count"] == 1
    assert report["summary"]["processed_count"] == 1
    assert report["results"][0]["browser"] == "edge"
    assert report["results"][0]["declared_zoom"] == "0.8"
    assert report["results"][0]["sample_label"] == "sample-a"


def test_private_evaluation_progress_prints_flush(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import api.screen_recognition.evaluate as evaluate_module

    calls: list[dict[str, object]] = []

    def fake_print(*_args: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(evaluate_module, "print", fake_print, raising=False)
    input_dir = tmp_path / "private"
    write_private_fixture(input_dir)

    evaluate_module.evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="none",
        dry_run=True,
        quiet=False,
        verbose=True,
        progress_stream=StringIO(),
    )

    assert calls
    assert all(call.get("flush") is True for call in calls)


def test_private_evaluation_ocr_timeout_continues_and_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import api.screen_recognition.evaluate as evaluate_module

    class TimeoutThenSuccessRecognizer:
        backend_name = "fake"
        calls = 0

        def recognize(self, _invocation: OcrInvocation) -> OcrResult:
            self.calls += 1
            if self.calls == 1:
                raise OcrBackendTimeoutError("timeout")
            return OcrResult(backend_name="fake", backend_version="1", fields={}, warnings=())

    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a")
    write_private_fixture(input_dir, "sample-b")
    monkeypatch.setattr(evaluate_module, "get_recognizer", lambda *_args, **_kwargs: TimeoutThenSuccessRecognizer())

    report = evaluate_module.evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="fake",
        progress_stream=StringIO(),
    )

    assert report["summary"]["processed_count"] == 2
    assert report["results"][0]["error_code"] == "ocr_timeout"
    assert report["results"][0]["requires_review"] is True

    fail_fast_report = evaluate_module.evaluate_private_fixtures(
        input_dir=input_dir,
        output_path=input_dir / "report-fast.json",
        layout_profile_name="gaijin-market-desktop-v1",
        ocr_backend_name="fake",
        fail_fast=True,
        progress_stream=StringIO(),
    )

    assert fail_fast_report["summary"]["processed_count"] == 1
    assert fail_fast_report["results"][0]["error_code"] == "ocr_timeout"


def test_private_evaluation_ctrl_c_writes_partial_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import api.screen_recognition.evaluate as evaluate_module

    class InterruptedRecognizer:
        backend_name = "fake"

        def recognize(self, _invocation: OcrInvocation) -> OcrResult:
            raise KeyboardInterrupt

    input_dir = tmp_path / "private"
    write_private_fixture(input_dir, "sample-a")
    write_private_fixture(input_dir, "sample-b")
    output_path = input_dir / "report.json"
    monkeypatch.setattr(evaluate_module, "get_recognizer", lambda *_args, **_kwargs: InterruptedRecognizer())

    with pytest.raises(evaluate_module.EvaluationInterrupted):
        evaluate_module.evaluate_private_fixtures(
            input_dir=input_dir,
            output_path=output_path,
            layout_profile_name="gaijin-market-desktop-v1",
            ocr_backend_name="fake",
            progress_stream=StringIO(),
        )

    partial = json.loads((input_dir / "report.partial.json").read_text(encoding="utf-8"))
    assert partial["complete"] is False
    assert partial["interrupted"] is True
    assert partial["summary"]["processed_count"] == 0
    assert not output_path.exists()


def test_windows_helper_timeout_raises_and_process_exits() -> None:
    with pytest.raises(OcrBackendTimeoutError):
        _run_windows_helper(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_seconds=1,
        )


def test_private_artifacts_are_gitignored() -> None:
    root = Path(__file__).resolve().parents[3]
    assert "artifacts/private/" in (root / ".gitignore").read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="windows-ocr requires Windows")
def test_windows_ocr_backend_local_image_smoke(tmp_path: Path) -> None:
    image = tmp_path / "ocr.png"
    _draw_text_png_with_powershell(image, "12.34")
    profile = LayoutProfile(
        name="test-full-image",
        version="1",
        min_width=1,
        min_height=1,
        min_aspect_ratio=Decimal("0.1"),
        max_aspect_ratio=Decimal("10"),
        rois={"best_bid": NormalizedRoi(Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1"))},
    )
    result = WindowsOcrRecognizer(timeout_seconds=20).recognize(
        OcrInvocation(image_path=image, layout_profile=profile, debug_artifacts_dir=None)
    )
    text = result.fields["best_bid"].raw_text
    assert "12" in text and "34" in text
    assert result.fields["best_bid"].confidence is None


def _draw_text_png_with_powershell(path: Path, text: str) -> None:
    _draw_text_image_with_powershell(path, text, "Png")


def _draw_text_image_with_powershell(path: Path, text: str, image_format: str) -> None:
    command = (
        "Add-Type -AssemblyName System.Drawing; "
        "$bmp = New-Object System.Drawing.Bitmap 420,120; "
        "$g = [System.Drawing.Graphics]::FromImage($bmp); "
        "$g.Clear([System.Drawing.Color]::White); "
        "$font = New-Object System.Drawing.Font 'Arial', 48; "
        f"$g.DrawString('{text}', $font, [System.Drawing.Brushes]::Black, 20, 20); "
        f"$bmp.Save('{path}', [System.Drawing.Imaging.ImageFormat]::{image_format}); "
        "$g.Dispose(); $bmp.Dispose();"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("Could not generate Windows OCR smoke image.")
