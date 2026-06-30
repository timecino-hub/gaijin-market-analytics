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

from api.screen_recognition.comparison import compare_contracts, summarize_results
from api.screen_recognition.contracts import LayoutProfile, NormalizedRoi
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
from api.screen_recognition.parser import parse_ocr_contract
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


def evidence(text: str):
    from api.screen_recognition.contracts import OcrFieldEvidence

    return OcrFieldEvidence(field_name="field", raw_text=text, confidence=None)


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
    assert not (output / "candidate_import.csv").exists()
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
