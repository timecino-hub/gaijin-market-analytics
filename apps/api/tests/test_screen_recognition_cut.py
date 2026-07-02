from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import zlib
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from api.screen_recognition.comparison import compare_contracts, summarize_results
from api.screen_recognition.config import CurrentCutConfig, stable_config_sha256
from api.screen_recognition.contracts import LayoutProfile, NormalizedRoi
from api.screen_recognition.evaluate import PRIVATE_FIXTURE_MESSAGE, evaluate_private_fixtures
from api.screen_recognition.ground_truth import GroundTruthInvalidError, load_ground_truth
from api.screen_recognition.image_io import (
    ImageReadError,
    read_image_info,
    safe_extract_images_zip,
)
from api.screen_recognition.layouts import (
    LayoutUnsupportedError,
    get_layout_profile,
    roi_to_pixels,
    validate_layout_match,
)
from api.screen_recognition.ocr_backend import (
    OcrBackendError,
    OcrInvocation,
    WindowsOcrRecognizer,
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
    build_ocr_preprocessing_variants,
)
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


def read_image_info_from_dimensions(filename: str, width: int, height: int):
    from api.screen_recognition.contracts import ImageInfo

    return ImageInfo(filename=filename, width=width, height=height, format="png")


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
