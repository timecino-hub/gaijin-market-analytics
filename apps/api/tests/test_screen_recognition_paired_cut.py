from __future__ import annotations

import io
import json
import socket
import stat
import subprocess
import sys
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from api.screen_recognition.axis_mapping import AxisMappingError, build_axis_mapping
from api.screen_recognition.config import PairedCutConfig, default_paired_cut_config
from api.screen_recognition.contracts import (
    ImageInfo,
    LayoutProfile,
    NormalizedRoi,
    OcrBoundingBox,
    OcrFieldEvidence,
    OcrLineEvidence,
    OcrResult,
)
from api.screen_recognition.history_analysis import (
    analyze_history_image,
    extract_blue_volume_points,
    extract_red_price_points,
)
from api.screen_recognition.image_io import UnsafeArchiveError, safe_extract_images_zip, scan_safe_images_zip
from api.screen_recognition.layouts import get_layout_profile
from api.screen_recognition.ocr_backend import OcrInvocation
from api.screen_recognition.paired_runner import PairedCutRunConfig, run_paired_cut
from api.screen_recognition.pairs import make_paired_ground_truth_template, scan_paired_images


def test_pair_scanning_reports_missing_duplicates_and_invalid_names(tmp_path: Path) -> None:
    _write_png(tmp_path / "001.png")
    _write_png(tmp_path / "001.jpg")
    _write_png(tmp_path / "002_1.png")
    _write_png(tmp_path / "bad-name.png")

    pairs, global_errors = scan_paired_images(tmp_path)

    by_id = {pair.sample_id: pair for pair in pairs}
    assert "pair_duplicate_current_image" in by_id["001"].errors
    assert "pair_history_image_missing" in by_id["001"].errors
    assert "pair_current_image_missing" in by_id["002"].errors
    assert global_errors == ["pair_invalid_filename"]


def test_init_paired_template_uses_nulls_not_ocr_answers(tmp_path: Path) -> None:
    _write_png(tmp_path / "001.png")
    _write_png(tmp_path / "001_1.png")

    rows, errors = make_paired_ground_truth_template(
        tmp_path, config=default_paired_cut_config()
    )

    assert errors == []
    assert rows[0]["sample_id"] == "001"
    assert rows[0]["split"] == "calibration"
    assert rows[0]["item_key"] is None
    assert rows[0]["current"]["best_bid"] is None
    assert rows[0]["history"]["price_series"]["display_color"] is None


def test_history_layout_profile_has_required_rois() -> None:
    profile = get_layout_profile("gaijin-market-history-v1")

    assert {
        "item_name",
        "order_book_distribution_region",
        "historical_chart_region",
        "left_axis_labels",
        "right_axis_labels",
        "time_axis_labels",
        "red_price_plot",
        "blue_volume_plot",
        "legend",
    }.issubset(profile.rois)


def test_axis_mapping_accepts_two_ticks_and_rejects_single_or_nonmonotonic() -> None:
    mapping = build_axis_mapping(
        _axis_evidence([("20", 0), ("10", 100)]),
        axis_name="left",
        axis_pixel_height=100,
        max_residual_px=Decimal("1"),
        require_non_negative=False,
    )
    assert mapping.value_for_pixel_y(50) == Decimal("15.0")

    with pytest.raises(AxisMappingError) as single:
        build_axis_mapping(
            _axis_evidence([("20", 0)]),
            axis_name="left",
            axis_pixel_height=100,
            max_residual_px=Decimal("1"),
            require_non_negative=False,
        )
    assert single.value.code == "left_axis_unreadable"

    with pytest.raises(AxisMappingError):
        build_axis_mapping(
            _axis_evidence([("20", 0), ("10", 50), ("15", 100)]),
            axis_name="left",
            axis_pixel_height=100,
            max_residual_px=Decimal("1"),
            require_non_negative=False,
        )


def test_axis_mapping_rejects_large_residual() -> None:
    with pytest.raises(AxisMappingError) as exc:
        build_axis_mapping(
            _axis_evidence([("30", 0), ("20", 80), ("10", 100)]),
            axis_name="left",
            axis_pixel_height=100,
            max_residual_px=Decimal("1"),
            require_non_negative=False,
        )
    assert exc.value.code == "chart_numeric_mapping_unavailable"


def test_red_area_upper_envelope_not_center_and_top_region_does_not_pollute() -> None:
    image, info, roi = _chart_image()
    draw = ImageDraw.Draw(image)
    draw.rectangle((300, 40, 360, 90), fill=(220, 20, 20))
    draw.rectangle((580, 500, 620, 700), fill=(220, 20, 20))

    points = extract_red_price_points(image, info, roi, default_paired_cut_config())

    middle = points[1]
    assert middle.detected_pixel_y == 500
    assert middle.extraction_method == "red_area_upper_envelope"
    assert middle.exact is False
    assert middle.source == "chart_estimate"


def test_blue_line_median_y_and_breaks_remain_null() -> None:
    image, info, roi = _chart_image()
    draw = ImageDraw.Draw(image)
    draw.line((340, 610, 356, 610), fill=(20, 80, 230), width=5)
    draw.line((844, 650, 860, 650), fill=(20, 80, 230), width=5)

    points = extract_blue_volume_points(image, info, roi, default_paired_cut_config())

    assert points[0].detected_pixel_y is not None
    assert points[1].detected_pixel_y is None
    assert points[2].detected_pixel_y is not None
    assert all(point.exact is False for point in points)


def test_history_analysis_keeps_levels_separate_and_refuses_numeric_without_axes(tmp_path: Path) -> None:
    image_path = tmp_path / "001_1.png"
    image, info, _roi = _chart_image()
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 130, 980, 300), outline=(0, 0, 0), width=3)
    draw.rectangle((580, 500, 620, 700), fill=(220, 20, 20))
    draw.line((340, 610, 356, 610), fill=(20, 80, 230), width=5)
    image.save(image_path)

    result = analyze_history_image(
        image_path=image_path,
        image_info=info,
        layout_profile=get_layout_profile("gaijin-market-history-v1"),
        ocr_fields={
            "item_name": OcrFieldEvidence("item_name", "Synthetic Alpha", None),
            "left_axis_labels": OcrFieldEvidence("left_axis_labels", "20", None),
            "right_axis_labels": OcrFieldEvidence("right_axis_labels", "", None),
            "time_axis_labels": OcrFieldEvidence("time_axis_labels", "one month", None),
        },
        config=default_paired_cut_config(),
    )

    assert result.order_book_distribution_region_detected
    assert result.historical_chart_region_detected
    assert result.price_series_color == "red"
    assert result.volume_series_color == "blue"
    assert "chart_numeric_mapping_unavailable" in result.errors
    assert result.price_series_estimates[1].estimated_value is None


def test_paired_run_with_fake_ocr_outputs_reports_and_no_csv_or_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    images = tmp_path / "images"
    output = tmp_path / "output"
    images.mkdir()
    _write_current_image(images / "001.png")
    _write_history_image(images / "001_1.png")
    ground_truth = tmp_path / "paired_ground_truth.jsonl"
    ground_truth.write_text(json.dumps(_paired_row()) + "\n", encoding="utf-8")

    class FakeRecognizer:
        backend_name = "fake-local"
        backend_version = "1"
        test_scope = "end_to_end"

        def recognize(self, invocation: OcrInvocation) -> OcrResult:
            if invocation.image_path.name.endswith("_1.png"):
                fields = {
                    "item_name": OcrFieldEvidence("item_name", "Synthetic Alpha", None),
                    "left_axis_labels": _axis_evidence([("20", 0), ("10", 304)], field_name="left_axis_labels"),
                    "right_axis_labels": _axis_evidence([("100", 0), ("0", 304)], field_name="right_axis_labels"),
                    "time_axis_labels": OcrFieldEvidence("time_axis_labels", "one month", None),
                }
            else:
                fields = {
                    "item_name": OcrFieldEvidence("item_name", "Synthetic Alpha", None),
                    "best_bid": OcrFieldEvidence("best_bid", "12.34", None),
                    "best_ask": OcrFieldEvidence("best_ask", "13.00", None),
                    "total_bid_quantity": OcrFieldEvidence("total_bid_quantity", "5", None),
                    "total_ask_quantity": OcrFieldEvidence("total_ask_quantity", "7", None),
                }
            return OcrResult("fake-local", "1", fields)

    import api.screen_recognition.paired_runner as paired_runner
    import api.screen_recognition.runner as current_runner

    monkeypatch.setattr(paired_runner, "get_recognizer", lambda _name: FakeRecognizer())
    monkeypatch.setattr(current_runner, "get_recognizer", lambda _name: FakeRecognizer())
    result = run_paired_cut(
        PairedCutRunConfig(
            images_dir=images,
            ground_truth_path=ground_truth,
            output_dir=output,
            ocr_backend_name="fake-local",
        )
    )

    assert result.run_metadata["database_access"] is False
    assert result.run_metadata["network_access"] is False
    assert result.run_metadata["candidate_csv_supported"] is False
    assert result.pair_summary["total_pairs"] == 1
    assert (output / "paired_results.jsonl").is_file()
    assert not (output / "candidate_import.csv").exists()
    assert result.results[0].history_result.price_series_estimates[1].source == "chart_estimate"


def test_config_sha_is_stable_and_split_is_explicit() -> None:
    config = default_paired_cut_config()
    assert config.sha256() == default_paired_cut_config().sha256()
    assert config.split_for("001") == "calibration"
    assert config.split_for("005") == "evaluation"
    assert config.split_for("021") is None


def test_sampled_point_tolerance_and_null_rate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    images = tmp_path / "images"
    output = tmp_path / "output"
    images.mkdir()
    _write_current_image(images / "001.png")
    _write_history_image(images / "001_1.png")
    row = _paired_row()
    row["history"]["sampled_points"] = [
        {
            "normalized_x": "0.50",
            "expected_price": "17.00",
            "price_tolerance": "5.00",
            "expected_volume": "50",
            "volume_tolerance": "80",
        }
    ]
    ground_truth = tmp_path / "paired_ground_truth.jsonl"
    ground_truth.write_text(json.dumps(row) + "\n", encoding="utf-8")

    class FakeRecognizer:
        backend_name = "fake-local"
        backend_version = "1"
        test_scope = "end_to_end"

        def recognize(self, invocation: OcrInvocation) -> OcrResult:
            if invocation.image_path.name.endswith("_1.png"):
                fields = {
                    "item_name": OcrFieldEvidence("item_name", "Synthetic Alpha", None),
                    "left_axis_labels": _axis_evidence([("20", 0), ("10", 304)], field_name="left_axis_labels"),
                    "right_axis_labels": _axis_evidence([("100", 0), ("0", 304)], field_name="right_axis_labels"),
                    "time_axis_labels": OcrFieldEvidence("time_axis_labels", "one month", None),
                }
            else:
                fields = {
                    "item_name": OcrFieldEvidence("item_name", "Synthetic Alpha", None),
                    "best_bid": OcrFieldEvidence("best_bid", "12.34", None),
                    "best_ask": OcrFieldEvidence("best_ask", "13.00", None),
                }
            return OcrResult("fake-local", "1", fields)

    import api.screen_recognition.paired_runner as paired_runner
    import api.screen_recognition.runner as current_runner

    monkeypatch.setattr(paired_runner, "get_recognizer", lambda _name: FakeRecognizer())
    monkeypatch.setattr(current_runner, "get_recognizer", lambda _name: FakeRecognizer())

    result = run_paired_cut(
        PairedCutRunConfig(
            images_dir=images,
            ground_truth_path=ground_truth,
            output_dir=output,
            ocr_backend_name="fake-local",
        )
    )

    assert result.history_summary["sampled_price_points_evaluable_count"] == 1
    assert result.history_summary["sampled_price_points_within_tolerance_rate"] in {"0", "1"}


def test_no_sampled_points_rate_is_null() -> None:
    from api.screen_recognition.paired_runner import summarize_history

    assert summarize_history([])["sampled_price_points_within_tolerance_rate"] is None


def test_zip_security_rejects_absolute_traversal_and_non_images(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("/abs.png", b"bad")
    with pytest.raises(Exception):
        safe_extract_images_zip(archive, tmp_path / "out")

    archive2 = tmp_path / "bad2.zip"
    with zipfile.ZipFile(archive2, "w") as zf:
        zf.writestr("nested/deeper/001.png", b"bad")
    with pytest.raises(Exception):
        safe_extract_images_zip(archive2, tmp_path / "out2")

    archive3 = tmp_path / "bad3.zip"
    with zipfile.ZipFile(archive3, "w") as zf:
        zf.writestr("001.txt", "bad")
    with pytest.raises(Exception):
        safe_extract_images_zip(archive3, tmp_path / "out3")


def test_zip_security_accepts_root_level_cut20_images(tmp_path: Path) -> None:
    archive = tmp_path / "root.zip"
    _write_pair_zip(archive, wrapper=None)

    scan = scan_safe_images_zip(archive)
    extracted = safe_extract_images_zip(archive, tmp_path / "out")

    assert scan.layout == "root"
    assert scan.wrapper_dir is None
    assert len(scan.members) == 40
    assert extracted.image_count == 40
    assert (tmp_path / "out" / "001.png").is_file()
    assert (tmp_path / "out" / "020_1.png").is_file()


def test_zip_security_accepts_single_top_level_wrapper_and_strips_it(tmp_path: Path) -> None:
    archive = tmp_path / "wrapped.zip"
    _write_pair_zip(archive, wrapper="pics")

    scan = scan_safe_images_zip(archive)
    extracted = safe_extract_images_zip(archive, tmp_path / "out")

    assert scan.layout == "single_top_level_directory"
    assert scan.wrapper_dir == "pics"
    assert extracted.image_count == 40
    assert (tmp_path / "out" / "001.png").is_file()
    assert not (tmp_path / "out" / "pics").exists()


@pytest.mark.parametrize(
    ("archive_name", "member_name"),
    [
        ("nested.zip", "pics/other/001.png"),
        ("top_traversal.zip", "../001.png"),
        ("wrapped_traversal.zip", "pics/../001.png"),
        ("windows_drive.zip", "C:/001.png"),
        ("archive.zip", "pics/archive.zip"),
        ("script.zip", "pics/run.exe"),
        ("text.zip", "pics/readme.txt"),
    ],
)
def test_zip_security_rejects_unsafe_member_names(tmp_path: Path, archive_name: str, member_name: str) -> None:
    archive = tmp_path / archive_name
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member_name, _png_bytes())

    with pytest.raises(UnsafeArchiveError):
        scan_safe_images_zip(archive)


def test_zip_security_rejects_multiple_top_level_dirs(tmp_path: Path) -> None:
    archive = tmp_path / "multi.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a/001.png", _png_bytes())
        zf.writestr("b/001_1.png", _png_bytes())

    with pytest.raises(UnsafeArchiveError):
        scan_safe_images_zip(archive)


def test_zip_security_rejects_root_and_wrapper_mixed_images(tmp_path: Path) -> None:
    archive = tmp_path / "mixed.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("001.png", _png_bytes())
        zf.writestr("pics/001_1.png", _png_bytes())

    with pytest.raises(UnsafeArchiveError):
        scan_safe_images_zip(archive)


def test_zip_security_rejects_symlink_members(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    link_info = zipfile.ZipInfo("pics/001.png")
    link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(link_info, "target.png")

    with pytest.raises(UnsafeArchiveError):
        scan_safe_images_zip(archive)


def test_zip_security_rejects_case_insensitive_duplicate_filenames(tmp_path: Path) -> None:
    archive = tmp_path / "dupe.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("pics/001.png", _png_bytes())
        zf.writestr("pics/001.PNG", _png_bytes())

    with pytest.raises(UnsafeArchiveError):
        scan_safe_images_zip(archive)


def test_zip_extraction_requires_empty_output_directory(tmp_path: Path) -> None:
    archive = tmp_path / "root.zip"
    _write_pair_zip(archive, wrapper=None)
    output = tmp_path / "out"
    output.mkdir()
    (output / "existing.txt").write_text("no overwrite", encoding="utf-8")

    with pytest.raises(UnsafeArchiveError):
        safe_extract_images_zip(archive, output)


def test_cut20_pair_scan_accepts_complete_twenty_pairs(tmp_path: Path) -> None:
    for sample_id in range(1, 21):
        _write_png(tmp_path / f"{sample_id:03d}.png")
        _write_png(tmp_path / f"{sample_id:03d}_1.png")

    pairs, global_errors = scan_paired_images(tmp_path)

    assert global_errors == []
    assert len(pairs) == 20
    assert all(not pair.errors for pair in pairs)


def test_cut20_pair_scan_reports_missing_history_image(tmp_path: Path) -> None:
    _write_png(tmp_path / "001.png")

    pairs, global_errors = scan_paired_images(tmp_path)

    assert global_errors == []
    assert pairs[0].sample_id == "001"
    assert "pair_history_image_missing" in pairs[0].errors


def test_no_network_during_paired_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))
    assert default_paired_cut_config().to_json()["current_layout_name"] == "gaijin-market-desktop-v1"


def test_cli_paired_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "api.screen_recognition_cut", "run-paired", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--history-layout" in completed.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="windows-ocr smoke requires Windows")
def test_windows_ocr_bounding_box_contract(tmp_path: Path) -> None:
    from api.screen_recognition.ocr_backend import WindowsOcrRecognizer

    image_path = tmp_path / "ocr.png"
    _write_text_image(image_path, "12.34")
    profile = LayoutProfile(
        name="ocr-test",
        version="1",
        min_width=1,
        min_height=1,
        min_aspect_ratio=Decimal("0.1"),
        max_aspect_ratio=Decimal("10"),
        rois={"field": NormalizedRoi(Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1"))},
    )

    result = WindowsOcrRecognizer(timeout_seconds=20).recognize(
        OcrInvocation(image_path=image_path, layout_profile=profile, debug_artifacts_dir=None)
    )

    field = result.fields["field"]
    assert field.confidence is None
    assert field.confidence_source == "unavailable"
    assert field.bounding_box is not None
    assert field.lines
    assert field.lines[0].words


def _axis_evidence(
    ticks: list[tuple[str, int]], *, field_name: str = "axis"
) -> OcrFieldEvidence:
    lines = tuple(
        OcrLineEvidence(
            text=text,
            order=index,
            bounding_box=OcrBoundingBox(Decimal("0"), Decimal(y - 5), Decimal("20"), Decimal("10")),
        )
        for index, (text, y) in enumerate(ticks)
    )
    return OcrFieldEvidence(
        field_name=field_name,
        raw_text="\n".join(text for text, _y in ticks),
        confidence=None,
        bounding_box=OcrBoundingBox(Decimal("0"), Decimal("0"), Decimal("50"), Decimal("100")),
        lines=lines,
    )


def _chart_image() -> tuple[Image.Image, ImageInfo, NormalizedRoi]:
    image = Image.new("RGB", (1200, 800), "white")
    info = ImageInfo("history.png", 1200, 800, "png")
    roi = get_layout_profile("gaijin-market-history-v1").rois["historical_chart_region"]
    return image, info, roi


def _write_png(path: Path) -> None:
    Image.new("RGB", (1200, 800), "white").save(path)


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 800), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _write_pair_zip(path: Path, *, wrapper: str | None) -> None:
    prefix = f"{wrapper}/" if wrapper else ""
    payload = _png_bytes()
    with zipfile.ZipFile(path, "w") as zf:
        if wrapper:
            zf.writestr(f"{wrapper}/", b"")
        for sample_id in range(1, 21):
            zf.writestr(f"{prefix}{sample_id:03d}.png", payload)
            zf.writestr(f"{prefix}{sample_id:03d}_1.png", payload)


def _write_current_image(path: Path) -> None:
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 30), "Synthetic Alpha", fill=(0, 0, 0))
    image.save(path)


def _write_history_image(path: Path) -> None:
    image, _info, _roi = _chart_image()
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 130, 980, 300), outline=(0, 0, 0), width=3)
    draw.rectangle((580, 500, 620, 700), fill=(220, 20, 20))
    draw.line((340, 610, 356, 610), fill=(20, 80, 230), width=5)
    image.save(path)


def _write_text_image(path: Path, text: str) -> None:
    image = Image.new("RGB", (420, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 35), text, fill=(0, 0, 0))
    image.save(path)


def _paired_row() -> dict[str, object]:
    return {
        "sample_id": "001",
        "split": "calibration",
        "item_key": "admin-alpha",
        "item_name": "Synthetic Alpha",
        "current": {
            "filename": "001.png",
            "expected_status": "passed",
            "best_bid": "12.34",
            "best_ask": "13.00",
            "total_bid_quantity": 5,
            "total_ask_quantity": 7,
            "bid_levels": [],
            "ask_levels": [],
        },
        "history": {
            "filename": "001_1.png",
            "expected_status": "ok",
            "time_range": "one_month",
            "price_series": {"display_color": "red", "axis": "left"},
            "volume_series": {"display_color": "blue", "axis": "right"},
            "left_axis_range": {"min": "10", "max": "20"},
            "right_axis_range": {"min": "0", "max": "100"},
            "sampled_points": [],
        },
    }
