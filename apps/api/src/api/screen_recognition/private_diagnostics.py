from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import mean, median
from typing import Any

from api.screen_recognition.ocr_candidates import select_price_candidate


SCHEMA_VERSION = "screen-recognition-private-diagnostics/1.0.0"
SPLIT_VERSION = "fixture-sha256-greedy-coverage-v1"
VALIDATION_SIZE = 8

PRICE_FIELDS = {
    "best_bid": "best_bid",
    "bid_levels": "best_bid",
    "best_ask": "best_ask",
    "ask_levels": "best_ask",
}


def build_anonymous_diagnostics(private_report: dict[str, Any]) -> dict[str, Any]:
    results = [item for item in private_report.get("results") or [] if isinstance(item, dict)]
    split = _stable_split(results)
    groups = {"overall": results, "tuning": split["tuning_results"], "validation": split["validation_results"]}
    pipeline = _pipeline_summary(results)
    helper = _helper_timing_summary(results)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_schema_version": private_report.get("schema_version"),
        "fixture_count": len(results),
        "reviewed_count": sum(1 for item in results if item.get("ground_truth_status") == "reviewed"),
        "split": {
            "algorithm": SPLIT_VERSION,
            "tuning_count": len(split["tuning_results"]),
            "validation_count": len(split["validation_results"]),
            "coverage": {
                "tuning": _coverage(split["tuning_results"]),
                "validation": _coverage(split["validation_results"]),
            },
            "assignments": split["assignments"],
        },
        "accuracy": {name: _accuracy_summary(items) for name, items in groups.items()},
        "accuracy_by_dimension": {
            name: _accuracy_by_dimension(items) for name, items in groups.items()
        },
        "pipeline_diagnostics": pipeline,
        "helper_timing": helper,
        "notes": [
            "This report is anonymous: it omits paths, screenshots, raw OCR text, and concrete prices.",
            "Per-pipeline parse/exact statistics are selected-output based because the frozen baseline report does not store raw text for every OCR attempt.",
            "Timing and non-empty statistics include every recorded pipeline attempt.",
        ],
    }


def load_anonymous_diagnostics(private_report_path: Path) -> dict[str, Any]:
    return build_anonymous_diagnostics(json.loads(private_report_path.read_text(encoding="utf-8-sig")))


def _stable_split(results: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(results, key=lambda item: _fixture_hash(item))
    validation: list[dict[str, Any]] = []
    remaining = list(ordered)
    dimensions = ("browser", "declared_zoom", "sample_label")
    for dimension in dimensions:
        labels = sorted({str(item.get(dimension) or "unknown") for item in ordered})
        for label in labels:
            if len(validation) >= VALIDATION_SIZE:
                break
            if any(str(item.get(dimension) or "unknown") == label for item in validation):
                continue
            candidate = next(
                (
                    item
                    for item in remaining
                    if str(item.get(dimension) or "unknown") == label
                    and _keeps_tuning_coverage(remaining, item, dimensions)
                ),
                None,
            )
            if candidate is not None:
                validation.append(candidate)
                remaining.remove(candidate)
    for item in list(remaining):
        if len(validation) >= VALIDATION_SIZE:
            break
        if _keeps_tuning_coverage(remaining, item, dimensions):
            validation.append(item)
            remaining.remove(item)
    while len(validation) < min(VALIDATION_SIZE, len(results)) and remaining:
        validation.append(remaining.pop(0))
    validation_ids = {_fixture_hash(item) for item in validation}
    tuning = [item for item in ordered if _fixture_hash(item) not in validation_ids]
    assignments = [
        {
            "fixture_hash": _fixture_hash(item)[:16],
            "set": "validation" if _fixture_hash(item) in validation_ids else "tuning",
            "browser": str(item.get("browser") or "unknown"),
            "declared_zoom": str(item.get("declared_zoom") or "unknown"),
            "sample_label": str(item.get("sample_label") or "unknown"),
        }
        for item in ordered
    ]
    return {
        "tuning_results": tuning,
        "validation_results": validation,
        "assignments": assignments,
    }


def _keeps_tuning_coverage(
    remaining: list[dict[str, Any]], candidate: dict[str, Any], dimensions: tuple[str, ...]
) -> bool:
    after = [item for item in remaining if item is not candidate]
    if not after:
        return True
    for dimension in dimensions:
        before_labels = {str(item.get(dimension) or "unknown") for item in remaining}
        after_labels = {str(item.get(dimension) or "unknown") for item in after}
        if not before_labels <= after_labels:
            return False
    return True


def _coverage(results: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "browser": sorted({str(item.get("browser") or "unknown") for item in results}),
        "declared_zoom": sorted({str(item.get("declared_zoom") or "unknown") for item in results}),
        "sample_label": sorted({str(item.get("sample_label") or "unknown") for item in results}),
    }


def _fixture_hash(item: dict[str, Any]) -> str:
    fixture_id = str(item.get("fixture_id") or "")
    return hashlib.sha256(fixture_id.encode("utf-8")).hexdigest()


def _accuracy_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    counters = Counter()
    for item in results:
        accuracy = item.get("accuracy")
        if item.get("ground_truth_status") != "reviewed" or not isinstance(accuracy, dict):
            continue
        counters["reviewed_count"] += 1
        for key in (
            "best_bid_exact_match",
            "best_ask_exact_match",
            "both_exact_match",
            "bid_missing",
            "ask_missing",
            "bid_wrong_value",
            "ask_wrong_value",
            "false_confident_bid",
            "false_confident_ask",
            "requires_review_false_negative",
        ):
            if accuracy.get(key):
                counters[key] += 1
    reviewed = int(counters["reviewed_count"])
    return {
        **{key: int(counters[key]) for key in sorted(counters)},
        "rates": {
            key: _rate(counters[key], reviewed)
            for key in (
                "best_bid_exact_match",
                "best_ask_exact_match",
                "both_exact_match",
            )
        },
    }


def _accuracy_by_dimension(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        "browser": defaultdict(list),
        "declared_zoom": defaultdict(list),
        "sample_label": defaultdict(list),
    }
    for item in results:
        for dimension, bucket in grouped.items():
            bucket[str(item.get(dimension) or "unknown")].append(item)
    return {
        dimension: {label: _accuracy_summary(items) for label, items in sorted(values.items())}
        for dimension, values in grouped.items()
    }


def _pipeline_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[tuple[str, str], dict[str, Any]] = {}
    output_hashes: dict[tuple[str, str], set[str]] = defaultdict(set)
    has_private_attempts = any(
        (((item.get("raw_ocr") or {}).get("diagnostics") or {}).get("private_pipeline_attempts"))
        for item in results
        if isinstance(item, dict)
    )
    for item in results:
        profile = item.get("profile") or {}
        accuracy = item.get("accuracy") if isinstance(item.get("accuracy"), dict) else {}
        recognized = item.get("recognized") if isinstance(item.get("recognized"), dict) else {}
        raw_fields = ((item.get("raw_ocr") or {}).get("fields") or {})
        selected_by_field = {
            field_name: _selected_pipeline_name(field_value)
            for field_name, field_value in raw_fields.items()
            if isinstance(field_value, dict)
        }
        for timing in profile.get("per_pipeline_duration_ms") or []:
            if not isinstance(timing, dict):
                continue
            field_name = str(timing.get("field_name") or "unknown")
            pipeline_name = str(timing.get("pipeline_name") or "unknown")
            key = (field_name, pipeline_name)
            entry = stats.setdefault(key, _new_pipeline_entry(field_name, pipeline_name))
            entry["attempt_count"] += 1
            if timing.get("produced_text"):
                entry["ocr_non_empty_count"] += 1
            duration = _int_or_none(timing.get("duration_ms"))
            if duration is not None:
                entry["durations_ms"].append(duration)
            if selected_by_field.get(field_name) == pipeline_name:
                entry["selected_count"] += 1
                raw_text = str((raw_fields.get(field_name) or {}).get("raw_text") or "")
                if raw_text.strip():
                    output_hashes[key].add(_text_hash(raw_text))
                _record_selected_value_stats(entry, field_name, recognized, accuracy)
        _record_private_attempt_stats(stats, item)
    completed = [_complete_pipeline_entry(entry, output_hashes[(entry["field_name"], entry["pipeline_name"])]) for entry in stats.values()]
    p75_duration = _percentile(
        [item["mean_duration_ms"] for item in completed if item["mean_duration_ms"] is not None],
        75,
    )
    duplicate_groups = _duplicate_groups(completed)
    classifications = _pipeline_classifications(completed, p75_duration)
    return {
        "scope": {
            "attempts_and_duration": "all_recorded_pipeline_attempts",
            "parse_valid_exact_wrong_and_duplicate": (
                "all_private_pipeline_attempts"
                if has_private_attempts
                else "selected_outputs_only"
            ),
        },
        "pipeline_count": len(completed),
        "pipelines": sorted(completed, key=lambda item: (item["field_name"], item["pipeline_name"])),
        "duplicate_groups": duplicate_groups,
        "classifications": classifications,
    }


def _new_pipeline_entry(field_name: str, pipeline_name: str) -> dict[str, Any]:
    return {
        "field_name": field_name,
        "pipeline_name": pipeline_name,
        "attempt_count": 0,
        "ocr_non_empty_count": 0,
        "attempt_parse_valid_count": 0,
        "attempt_exact_count": 0,
        "attempt_wrong_value_count": 0,
        "attempt_missing_count": 0,
        "selected_count": 0,
        "selected_parse_valid_count": 0,
        "selected_best_bid_exact_count": 0,
        "selected_best_ask_exact_count": 0,
        "selected_wrong_value_count": 0,
        "selected_false_confident_count": 0,
        "durations_ms": [],
    }


def _record_private_attempt_stats(
    stats: dict[tuple[str, str], dict[str, Any]],
    result: dict[str, Any],
) -> None:
    diagnostics = ((result.get("raw_ocr") or {}).get("diagnostics") or {})
    attempts = diagnostics.get("private_pipeline_attempts") or []
    if not isinstance(attempts, list):
        return
    expected = result.get("expected") if isinstance(result.get("expected"), dict) else {}
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        field_name = str(attempt.get("field_name") or "unknown")
        pipeline_name = str(attempt.get("pipeline_name") or "unknown")
        if field_name not in PRICE_FIELDS:
            continue
        key = (field_name, pipeline_name)
        entry = stats.setdefault(key, _new_pipeline_entry(field_name, pipeline_name))
        side_field = PRICE_FIELDS[field_name]
        scalar_text = str(attempt.get("raw_text") or "") if field_name == side_field else ""
        first_level_text = str(attempt.get("raw_text") or "") if field_name != side_field else None
        selected = select_price_candidate(
            field_name=side_field,
            scalar_text=scalar_text,
            first_level_text=first_level_text,
        )
        expected_value = _expected_decimal(expected.get(f"expected_{side_field}"))
        if selected.value is None:
            entry["attempt_missing_count"] += 1
            continue
        entry["attempt_parse_valid_count"] += 1
        if expected_value is None:
            entry["attempt_wrong_value_count"] += 1
        elif selected.value == expected_value:
            entry["attempt_exact_count"] += 1
        else:
            entry["attempt_wrong_value_count"] += 1


def _record_selected_value_stats(
    entry: dict[str, Any],
    field_name: str,
    recognized: dict[str, Any],
    accuracy: dict[str, Any],
) -> None:
    value_key = PRICE_FIELDS.get(field_name)
    if value_key is None:
        value_key = {
            "total_bid_quantity": "total_bid_quantity",
            "total_bid_quantity_summary": "total_bid_quantity",
            "total_ask_quantity": "total_ask_quantity",
            "total_ask_quantity_summary": "total_ask_quantity",
            "item_name": "item_name",
        }.get(field_name)
    value = recognized.get(value_key) if value_key else None
    if value not in (None, ""):
        entry["selected_parse_valid_count"] += 1
    if field_name in {"best_bid", "bid_levels"}:
        if accuracy.get("best_bid_exact_match"):
            entry["selected_best_bid_exact_count"] += 1
        if accuracy.get("bid_wrong_value"):
            entry["selected_wrong_value_count"] += 1
        if accuracy.get("false_confident_bid"):
            entry["selected_false_confident_count"] += 1
    if field_name in {"best_ask", "ask_levels"}:
        if accuracy.get("best_ask_exact_match"):
            entry["selected_best_ask_exact_count"] += 1
        if accuracy.get("ask_wrong_value"):
            entry["selected_wrong_value_count"] += 1
        if accuracy.get("false_confident_ask"):
            entry["selected_false_confident_count"] += 1


def _complete_pipeline_entry(entry: dict[str, Any], hashes: set[str]) -> dict[str, Any]:
    durations = entry.pop("durations_ms")
    return {
        **entry,
        "mean_duration_ms": round(mean(durations), 2) if durations else None,
        "median_duration_ms": round(median(durations), 2) if durations else None,
        "selected_output_hash_count": len(hashes),
        "selected_output_hashes": sorted(hashes),
    }


def _pipeline_classifications(
    pipelines: list[dict[str, Any]], high_duration_threshold_ms: float | None
) -> dict[str, list[str]]:
    return {
        "never_selected_parse_valid": [
            _pipeline_label(item)
            for item in pipelines
            if item["selected_count"] > 0 and item["selected_parse_valid_count"] == 0
        ],
        "never_selected_correct_price": [
            _pipeline_label(item)
            for item in pipelines
            if item["field_name"] in PRICE_FIELDS
            and item["selected_count"] > 0
            and item["selected_best_bid_exact_count"] + item["selected_best_ask_exact_count"] == 0
        ],
        "high_cost_no_selected_increment": [
            _pipeline_label(item)
            for item in pipelines
            if high_duration_threshold_ms is not None
            and item["mean_duration_ms"] is not None
            and item["mean_duration_ms"] >= high_duration_threshold_ms
            and item["selected_best_bid_exact_count"] + item["selected_best_ask_exact_count"] == 0
        ],
        "error_prone_selected_candidates": [
            _pipeline_label(item)
            for item in pipelines
            if item["selected_wrong_value_count"] > 0 or item["selected_false_confident_count"] > 0
        ],
    }


def _duplicate_groups(pipelines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    for item in pipelines:
        hashes = tuple(item["selected_output_hashes"])
        if hashes:
            grouped[(item["field_name"], hashes)].append(_pipeline_label(item))
    return [
        {"field_name": field_name, "pipeline_count": len(labels), "pipelines": sorted(labels)}
        for (field_name, _hashes), labels in sorted(grouped.items())
        if len(labels) > 1
    ]


def _helper_timing_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    durations = []
    for item in results:
        timings = item.get("timings") or {}
        profile = item.get("profile") or {}
        diagnostics = (item.get("raw_ocr") or {}).get("diagnostics") or {}
        fixture_total = _int_or_none(timings.get("total_ms"))
        if fixture_total is not None:
            durations.append(fixture_total)
            totals["fixture_total_ms"] += fixture_total
        for source_key, total_key in (
            ("image_decode_ms", "python_image_decode_ms"),
            ("roi_resolution_ms", "python_roi_resolution_ms"),
            ("ocr_output_parsing_ms", "python_ocr_output_parsing_ms"),
            ("ocr_ms", "python_observed_helper_ms"),
        ):
            value = _int_or_none(timings.get(source_key))
            if value is not None:
                totals[total_key] += value
        for source_key, total_key in (
            ("powershell_process_startup_overhead_ms", "powershell_startup_ms"),
            ("ocr_engine_initialization_total_ms", "ocr_engine_initialization_ms"),
            ("ocr_execution_total_ms", "windows_ocr_execution_ms"),
            ("total_ocr_duration_ms", "windows_recognize_image_total_ms"),
        ):
            value = _int_or_none(profile.get(source_key))
            if value is not None:
                totals[total_key] += value
        helper_total = _int_or_none(diagnostics.get("helper_total_duration_ms"))
        if helper_total is not None:
            totals["powershell_helper_total_ms"] += helper_total
        for source_key in (
            "logical_pipeline_request_count",
            "unique_prepared_image_count",
            "deduplicated_ocr_request_count",
            "prepared_image_write_count",
            "prepared_image_read_count",
            "ocr_engine_initialization_count",
            "python_preprocessing_total_ms",
            "ocr_invocation_count",
            "pipeline_count_attempted",
        ):
            value = _int_or_none(profile.get(source_key))
            if value is not None:
                totals[source_key] += value
        batch_timings = profile.get("python_batch_timings_ms") or {}
        if isinstance(batch_timings, dict):
            for source_key, total_key in (
                ("image_decode_ms", "batch_image_decode_ms"),
                ("layout_ms", "batch_layout_ms"),
                ("roi_crop_ms", "batch_roi_crop_ms"),
                ("shared_preprocessing_ms", "batch_shared_preprocessing_ms"),
                ("image_encode_ms", "batch_image_encode_ms"),
                ("batch_manifest_ms", "batch_manifest_ms"),
            ):
                value = _int_or_none(batch_timings.get(source_key))
                if value is not None:
                    totals[total_key] += value
        per_pipeline_total = sum(
            _int_or_none(item.get("duration_ms")) or 0
            for item in profile.get("per_pipeline_duration_ms") or []
            if isinstance(item, dict)
        )
        totals["pipeline_wall_time_ms"] += per_pipeline_total
    totals["windows_recognize_non_ocr_ms"] = max(
        0,
        totals["windows_recognize_image_total_ms"]
        - totals["ocr_engine_initialization_ms"]
        - totals["windows_ocr_execution_ms"],
    )
    totals["pipeline_preprocessing_and_io_ms"] = max(
        0, totals["pipeline_wall_time_ms"] - totals["windows_recognize_image_total_ms"]
    )
    totals["helper_outer_overhead_ms"] = max(
        0, totals["powershell_helper_total_ms"] - totals["pipeline_wall_time_ms"]
    )
    totals["fixture_non_ocr_ms"] = max(0, totals["fixture_total_ms"] - totals["windows_ocr_execution_ms"])
    return {
        "totals_ms": dict(sorted(totals.items())),
        "percent_of_fixture_total": {
            key: _rate(value, totals["fixture_total_ms"])
            for key, value in sorted(totals.items())
            if key.endswith("_ms")
        },
        "median_fixture_duration_ms": _percentile(durations, 50),
        "p95_fixture_duration_ms": _percentile(durations, 95),
        "interpretation": {
            "ocr_execution_ms": "Windows OCR RecognizeAsync time only.",
            "windows_recognize_non_ocr_ms": "WinRT file open/decoder/bitmap/serialization time inside RecognizeImage.",
            "pipeline_preprocessing_and_io_ms": "crop, resize, pixel preprocessing, PNG temp write, scoring, and per-pipeline glue.",
            "helper_outer_overhead_ms": "source load, ROI setup, JSON serialization, disposal, and helper bookkeeping outside per-pipeline timings.",
        },
    }


def _selected_pipeline_name(field_value: dict[str, Any]) -> str | None:
    for warning in field_value.get("warnings") or []:
        if isinstance(warning, str) and warning.startswith("preprocessing_pipeline:"):
            return warning.split(":", 1)[1]
    return None


def _text_hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _pipeline_label(item: dict[str, Any]) -> str:
    return f"{item['field_name']}::{item['pipeline_name']}"


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _expected_decimal(value: Any) -> Decimal | None:
    if value in (None, "", "not_visible", "not_applicable"):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _rate(numerator: Any, denominator: Any) -> str:
    try:
        top = Decimal(str(numerator))
        bottom = Decimal(str(denominator))
    except (InvalidOperation, ValueError):
        return "0"
    if bottom <= 0:
        return "0"
    return str((top / bottom).quantize(Decimal("0.0001")))


def _percentile(values: list[int | float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (percentile / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return round(float(ordered[lower] * (1 - weight) + ordered[upper] * weight), 2)
