from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from api.screen_recognition.comparison import compare_contracts
from api.screen_recognition.config import PairedCutConfig, default_paired_cut_config, git_metadata
from api.screen_recognition.contracts import CUT_RUNNER_VERSION, CutStatus, SampleResult, ScreenContract, stable_issue_codes
from api.screen_recognition.history_analysis import analyze_history_image
from api.screen_recognition.history_contracts import HistoryRecognitionResult
from api.screen_recognition.image_io import ImageReadError, list_image_files, read_image_info
from api.screen_recognition.layouts import LayoutUnsupportedError, get_layout_profile, validate_layout_match
from api.screen_recognition.ocr_backend import OcrBackendError, OcrInvocation, get_recognizer, windows_ocr_preprocessing_metadata
from api.screen_recognition.pairs import PairedGroundTruthEntry, load_paired_ground_truth
from api.screen_recognition.reporting import write_outputs
from api.screen_recognition.runner import CutRunConfig, _process_entry


@dataclass(frozen=True)
class PairedCutRunConfig:
    images_dir: Path
    ground_truth_path: Path
    output_dir: Path
    current_layout_name: str = "gaijin-market-desktop-v1"
    history_layout_name: str = "gaijin-market-history-v1"
    ocr_backend_name: str = "windows-ocr"
    strict: bool = False
    pretty: bool = False
    fail_fast: bool = False
    run_id: str | None = None
    debug_artifacts: bool = False
    paired_config: PairedCutConfig = default_paired_cut_config()


@dataclass(frozen=True)
class PairedSampleResult:
    sample_id: str
    split: str
    current_result: SampleResult | None
    history_result: HistoryRecognitionResult | None
    pair_consistency: dict[str, Any]
    status: CutStatus
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    processing_duration_ms: int

    def to_json(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "split": self.split,
            "current_result": None if self.current_result is None else self.current_result.to_json(),
            "history_result": None if self.history_result is None else self.history_result.to_json(),
            "pair_consistency": self.pair_consistency,
            "status": self.status.value,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "processing_duration_ms": self.processing_duration_ms,
        }


@dataclass(frozen=True)
class PairedCutRunResult:
    run_metadata: dict[str, Any]
    current_summary: dict[str, Any]
    history_summary: dict[str, Any]
    pair_summary: dict[str, Any]
    results: tuple[PairedSampleResult, ...]


def run_paired_cut(config: PairedCutRunConfig) -> PairedCutRunResult:
    started_at = datetime.now(UTC)
    run_id = config.run_id or f"paired-cut20-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    entries = load_paired_ground_truth(config.ground_truth_path)
    current_profile = get_layout_profile(config.current_layout_name)
    history_profile = get_layout_profile(config.history_layout_name)
    recognizer = get_recognizer(config.ocr_backend_name)
    paired_config = config.paired_config
    results: list[PairedSampleResult] = []
    for entry in entries:
        result = _process_pair(
            entry=entry,
            run_config=config,
            paired_config=paired_config,
            run_id=run_id,
            current_profile=current_profile,
            history_profile=history_profile,
        )
        results.append(result)
        if config.fail_fast and result.status not in {CutStatus.PASSED, CutStatus.PASSED_WITH_WARNING}:
            break
    current_summary = summarize_current(results)
    history_summary = summarize_history(results)
    pair_summary = summarize_pairs(results)
    finished_at = datetime.now(UTC)
    metadata = {
        "run_id": run_id,
        "runner_version": CUT_RUNNER_VERSION,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "test_scope": recognizer.test_scope,
        "ocr_backend": {
            "name": recognizer.backend_name,
            "version": recognizer.backend_version,
            "runs_locally": True,
            "runtime_network_access": False,
            "runtime_model_download": False,
        },
        "current_layout": current_profile.to_json(),
        "history_layout": history_profile.to_json(),
        "preprocessing": windows_ocr_preprocessing_metadata(),
        "paired_config": paired_config.to_json(),
        "config_sha256": paired_config.sha256(),
        "git": git_metadata(),
        "files_found": len(list_image_files(config.images_dir)),
        "database_access": False,
        "network_access": False,
        "candidate_csv_supported": False,
        "debug_artifacts_enabled": config.debug_artifacts,
    }
    write_paired_outputs(
        output_dir=config.output_dir,
        run_metadata=metadata,
        results=results,
        current_summary=current_summary,
        history_summary=history_summary,
        pair_summary=pair_summary,
        pretty=config.pretty,
    )
    return PairedCutRunResult(
        run_metadata=metadata,
        current_summary=current_summary,
        history_summary=history_summary,
        pair_summary=pair_summary,
        results=tuple(results),
    )


def _process_pair(
    *,
    entry: PairedGroundTruthEntry,
    run_config: PairedCutRunConfig,
    paired_config: PairedCutConfig,
    run_id: str,
    current_profile: object,
    history_profile: object,
) -> PairedSampleResult:
    started = time.perf_counter()
    errors: list[str] = []
    warnings: list[str] = []
    current_result = _process_entry(
        config=CutRunConfig(
            images_dir=run_config.images_dir,
            ground_truth_path=run_config.ground_truth_path,
            output_dir=run_config.output_dir,
            layout_profile_name=run_config.current_layout_name,
            ocr_backend_name=run_config.ocr_backend_name,
            debug_artifacts=run_config.debug_artifacts,
        ),
        entry=entry.current,
        run_id=run_id,
        profile=current_profile,
        recognizer_name=run_config.ocr_backend_name,
    )
    history_result = _process_history_entry(
        entry=entry,
        run_config=run_config,
        paired_config=paired_config,
        run_id=run_id,
        history_profile=history_profile,
    )
    errors.extend(current_result.errors)
    errors.extend(history_result.errors)
    warnings.extend(current_result.warnings)
    warnings.extend(history_result.warnings)
    pair_consistency = _pair_consistency(entry, current_result, history_result)
    if not pair_consistency["item_key_match"]:
        errors.append("pair_item_identity_mismatch")
    if not pair_consistency["item_name_match"]:
        errors.append("pair_name_mismatch")
    stable_errors = tuple(stable_issue_codes(errors))
    stable_warnings = tuple(stable_issue_codes(warnings))
    status = CutStatus.PASSED
    if "unexpected_exception" in stable_errors:
        status = CutStatus.UNEXPECTED_ERROR
    elif stable_errors:
        status = CutStatus.FAILED
    elif stable_warnings:
        status = CutStatus.PASSED_WITH_WARNING
    return PairedSampleResult(
        sample_id=entry.sample_id,
        split=entry.split,
        current_result=current_result,
        history_result=history_result,
        pair_consistency=pair_consistency,
        status=status,
        warnings=stable_warnings,
        errors=stable_errors,
        processing_duration_ms=int((time.perf_counter() - started) * 1000),
    )


def _process_history_entry(
    *,
    entry: PairedGroundTruthEntry,
    run_config: PairedCutRunConfig,
    paired_config: PairedCutConfig,
    run_id: str,
    history_profile: object,
) -> HistoryRecognitionResult:
    image_path = run_config.images_dir / entry.history.filename
    try:
        image_info = read_image_info(image_path)
        validate_layout_match(history_profile, image_info)
        recognizer = get_recognizer(run_config.ocr_backend_name)
        debug_dir = (
            run_config.output_dir / "debug_artifacts" / run_id / entry.sample_id / "history"
            if run_config.debug_artifacts
            else None
        )
        ocr_result = recognizer.recognize(
            OcrInvocation(image_path=image_path, layout_profile=history_profile, debug_artifacts_dir=debug_dir)
        )
        result = analyze_history_image(
            image_path=image_path,
            image_info=image_info,
            layout_profile=history_profile,
            ocr_fields=ocr_result.fields,
            config=paired_config,
        )
        return _compare_history_expected(entry, result)
    except ImageReadError:
        return _empty_history_result(("image_unreadable",))
    except LayoutUnsupportedError:
        return _empty_history_result(("unsupported_layout",))
    except OcrBackendError:
        return _empty_history_result(("ocr_backend_error",))
    except Exception:
        return _empty_history_result(("unexpected_exception",))


def _compare_history_expected(
    entry: PairedGroundTruthEntry, result: HistoryRecognitionResult
) -> HistoryRecognitionResult:
    errors = list(result.errors)
    expected = entry.history
    if expected.price_series_color and result.price_series_color != expected.price_series_color:
        errors.append("price_series_not_detected")
    if expected.volume_series_color and result.volume_series_color != expected.volume_series_color:
        errors.append("volume_series_not_detected")
    if expected.price_series_axis and result.price_series_axis != expected.price_series_axis:
        errors.append("price_series_wrong_axis")
    if expected.volume_series_axis and result.volume_series_axis != expected.volume_series_axis:
        errors.append("volume_series_wrong_axis")
    if result.price_series_axis == "right" or result.volume_series_axis == "left":
        errors.append("price_volume_series_swapped")
    sampled_comparisons = _compare_sampled_points(expected.sampled_points, result)
    return HistoryRecognitionResult(
        **{
            **result.__dict__,
            "sampled_point_comparisons": sampled_comparisons,
            "errors": tuple(stable_issue_codes(errors)),
        }
    )


def _empty_history_result(errors: tuple[str, ...]) -> HistoryRecognitionResult:
    return HistoryRecognitionResult(
        item_name=None,
        image_info=None,
        layout_match=False,
        time_range=None,
        order_book_distribution_region_detected=False,
        historical_chart_region_detected=False,
        left_axis_raw_labels=(),
        right_axis_raw_labels=(),
        time_axis_raw_labels=(),
        price_series_color=None,
        price_series_axis=None,
        volume_series_color=None,
        volume_series_axis=None,
        price_series_estimates=(),
        volume_series_estimates=(),
        sampled_point_comparisons=(),
        errors=errors,
    )


def _pair_consistency(
    entry: PairedGroundTruthEntry, current: SampleResult, history: HistoryRecognitionResult
) -> dict[str, Any]:
    current_name = current.recognized.item_name
    history_name = history.item_name
    expected_name = entry.item_name
    return {
        "item_key": entry.item_key,
        "item_key_match": current.recognized.item_key == entry.item_key,
        "current_item_name": current_name,
        "history_item_name": history_name,
        "expected_item_name": expected_name,
        "item_name_match": (
            current_name == expected_name
            and (history_name is None or history_name == expected_name)
        ),
    }


def summarize_current(results: list[PairedSampleResult]) -> dict[str, Any]:
    current_results = [result.current_result for result in results if result.current_result is not None]
    return {
        "current_images_processed": len(current_results),
        "best_bid_match_count": _field_pass_count(current_results, "best_bid"),
        "best_ask_match_count": _field_pass_count(current_results, "best_ask"),
        "item_name_match_count": _field_pass_count(current_results, "item_name"),
        "quantity_match_count": _field_pass_count(current_results, "total_bid_quantity")
        + _field_pass_count(current_results, "total_ask_quantity"),
        "current_hard_errors": sum(1 for result in current_results for code in result.errors),
        "by_split": _split_counts(results, lambda result: result.current_result is not None),
    }


def summarize_history(results: list[PairedSampleResult]) -> dict[str, Any]:
    history_results = [result.history_result for result in results if result.history_result is not None]
    sampled_price_evaluable = sum(
        1
        for result in history_results
        for comparison in result.sampled_point_comparisons
        if comparison.get("price_evaluable")
    )
    sampled_volume_evaluable = sum(
        1
        for result in history_results
        for comparison in result.sampled_point_comparisons
        if comparison.get("volume_evaluable")
    )
    sampled_price_passed = sum(
        1
        for result in history_results
        for comparison in result.sampled_point_comparisons
        if comparison.get("price_evaluable") and comparison.get("price_passed")
    )
    sampled_volume_passed = sum(
        1
        for result in history_results
        for comparison in result.sampled_point_comparisons
        if comparison.get("volume_evaluable") and comparison.get("volume_passed")
    )
    return {
        "history_images_processed": len(history_results),
        "chart_region_detected": sum(1 for result in history_results if result.historical_chart_region_detected),
        "order_book_distribution_detected": sum(1 for result in history_results if result.order_book_distribution_region_detected),
        "left_axis_readable": sum(1 for result in history_results if result.left_axis_mapping is not None),
        "right_axis_readable": sum(1 for result in history_results if result.right_axis_mapping is not None),
        "time_axis_readable": sum(1 for result in history_results if result.time_axis_raw_labels),
        "red_price_series_detected": sum(1 for result in history_results if result.price_series_color == "red"),
        "blue_volume_series_detected": sum(1 for result in history_results if result.volume_series_color == "blue"),
        "correct_price_axis_assignment": sum(1 for result in history_results if result.price_series_axis == "left"),
        "correct_volume_axis_assignment": sum(1 for result in history_results if result.volume_series_axis == "right"),
        "sampled_price_points_evaluable_count": sampled_price_evaluable,
        "sampled_price_points_within_tolerance_count": sampled_price_passed,
        "sampled_price_points_within_tolerance_rate": _rate(sampled_price_passed, sampled_price_evaluable),
        "sampled_volume_points_evaluable_count": sampled_volume_evaluable,
        "sampled_volume_points_within_tolerance_count": sampled_volume_passed,
        "sampled_volume_points_within_tolerance_rate": _rate(sampled_volume_passed, sampled_volume_evaluable),
        "history_hard_errors": sum(1 for result in history_results for code in result.errors),
        "by_split": _split_counts(results, lambda result: result.history_result is not None),
    }


def summarize_pairs(results: list[PairedSampleResult]) -> dict[str, Any]:
    return {
        "total_pairs": len(results),
        "complete_pairs": sum(1 for result in results if result.current_result and result.history_result),
        "missing_pairs": sum(1 for result in results if not result.current_result or not result.history_result),
        "item_identity_matches": sum(1 for result in results if result.pair_consistency.get("item_key_match")),
        "item_name_matches": sum(1 for result in results if result.pair_consistency.get("item_name_match")),
        "combined_passed": sum(1 for result in results if result.status == CutStatus.PASSED),
        "combined_warnings": sum(1 for result in results if result.status == CutStatus.PASSED_WITH_WARNING),
        "combined_failed": sum(1 for result in results if result.status == CutStatus.FAILED),
        "unexpected_errors": sum(1 for result in results if result.status == CutStatus.UNEXPECTED_ERROR),
        "by_split": _split_counts(results, lambda _result: True),
    }


def write_paired_outputs(
    *,
    output_dir: Path,
    run_metadata: dict[str, Any],
    results: list[PairedSampleResult],
    current_summary: dict[str, Any],
    history_summary: dict[str, Any],
    pair_summary: dict[str, Any],
    pretty: bool,
) -> None:
    from api.screen_recognition.json_util import dump_json_file, dumps_json

    output_dir.mkdir(parents=True, exist_ok=True)
    dump_json_file(output_dir / "run_metadata.json", run_metadata, pretty=pretty)
    dump_json_file(output_dir / "current_summary.json", current_summary, pretty=pretty)
    dump_json_file(output_dir / "history_summary.json", history_summary, pretty=pretty)
    dump_json_file(output_dir / "pair_summary.json", pair_summary, pretty=pretty)
    (output_dir / "paired_results.jsonl").write_text(
        "".join(dumps_json(result.to_json(), pretty=False) + "\n" for result in results),
        encoding="utf-8",
    )
    report_lines = [
        "# Screen Recognition Paired CUT-20 Report",
        "",
        f"- Run ID: `{run_metadata['run_id']}`",
        f"- OCR backend: `{run_metadata['ocr_backend']['name']}`",
        f"- Current layout: `{run_metadata['current_layout']['name']}`",
        f"- History layout: `{run_metadata['history_layout']['name']}`",
        f"- Config SHA-256: `{run_metadata['config_sha256']}`",
        "",
        "## Current Screenshot OCR",
        "",
        *[f"- `{key}`: `{value}`" for key, value in sorted(current_summary.items())],
        "",
        "## History Chart Structure And Estimates",
        "",
        *[f"- `{key}`: `{value}`" for key, value in sorted(history_summary.items())],
        "",
        "## Pair Consistency",
        "",
        *[f"- `{key}`: `{value}`" for key, value in sorted(pair_summary.items())],
    ]
    (output_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")


def _field_pass_count(results: list[SampleResult], field_name: str) -> int:
    return sum(
        1
        for result in results
        for comparison in result.field_comparisons
        if comparison.field_name == field_name and comparison.evaluable and comparison.passed
    )


def _split_counts(results: list[PairedSampleResult], predicate: object) -> dict[str, int]:
    return {
        split: sum(1 for result in results if result.split == split and predicate(result))
        for split in ("calibration", "evaluation")
    }


def _compare_sampled_points(
    expected_points: tuple[dict[str, Any], ...], result: HistoryRecognitionResult
) -> tuple[dict[str, Any], ...]:
    comparisons: list[dict[str, Any]] = []
    price_by_x = {point.normalized_x: point for point in result.price_series_estimates}
    volume_by_x = {point.normalized_x: point for point in result.volume_series_estimates}
    for expected in expected_points:
        normalized_x = Decimal(str(expected.get("normalized_x")))
        price_point = price_by_x.get(normalized_x)
        volume_point = volume_by_x.get(normalized_x)
        expected_price = _optional_decimal(expected.get("expected_price"))
        price_tolerance = _optional_decimal(expected.get("price_tolerance")) or Decimal("0")
        expected_volume = _optional_decimal(expected.get("expected_volume"))
        volume_tolerance = _optional_decimal(expected.get("volume_tolerance")) or Decimal("0")
        estimated_price = None if price_point is None else price_point.estimated_value
        estimated_volume = None if volume_point is None else volume_point.estimated_volume
        price_evaluable = expected_price is not None
        volume_evaluable = expected_volume is not None
        price_passed = (
            None
            if not price_evaluable
            else estimated_price is not None and abs(estimated_price - expected_price) <= price_tolerance
        )
        volume_passed = (
            None
            if not volume_evaluable
            else estimated_volume is not None and abs(estimated_volume - expected_volume) <= volume_tolerance
        )
        comparisons.append(
            {
                "normalized_x": str(normalized_x),
                "price_evaluable": price_evaluable,
                "price_passed": price_passed,
                "expected_price": None if expected_price is None else str(expected_price),
                "estimated_price": None if estimated_price is None else str(estimated_price),
                "price_tolerance": str(price_tolerance),
                "volume_evaluable": volume_evaluable,
                "volume_passed": volume_passed,
                "expected_volume": None if expected_volume is None else str(expected_volume),
                "estimated_volume": None if estimated_volume is None else str(estimated_volume),
                "volume_tolerance": str(volume_tolerance),
            }
        )
    return tuple(comparisons)


def _optional_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _rate(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return str(Decimal(numerator) / Decimal(denominator))
