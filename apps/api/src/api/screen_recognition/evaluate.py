from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TextIO

from api.screen_recognition.contracts import ImageInfo, OcrResult, ScreenContract
from api.screen_recognition.image_io import ImageReadError, read_image_info
from api.screen_recognition.layouts import LayoutUnsupportedError, get_layout_profile, validate_layout_match
from api.screen_recognition.ocr_backend import (
    OcrBackendError,
    OcrBackendNotConfiguredError,
    OcrBackendTimeoutError,
    OcrInvocation,
    WindowsOcrRecognizer,
    get_recognizer,
    windows_ocr_preprocessing_metadata,
)
from api.screen_recognition.ocr_candidates import (
    PRICE_SELECTION_POLICY_PRICE_CELLS_V3,
)
from api.screen_recognition.parser import parse_ocr_contract
from api.screen_recognition.private_diagnostics import load_anonymous_diagnostics
from api.screen_recognition.roi import RoiValidationError, resolve_roi_pixels


PRIVATE_FIXTURE_MESSAGE = "no private evaluation fixtures found"
SCHEMA_VERSION = "screen-recognition-private-evaluation/1.2.0"
GROUND_TRUTH_SCHEMA_VERSION = "screen-recognition-private-ground-truth/1.0.0"
DEFAULT_OCR_TIMEOUT_SECONDS = 60
GROUND_TRUTH_FILENAME = "ground-truth.csv"
DIAGNOSTICS_PRIVATE_REPORT_SUFFIX = ".private.json"
DIAGNOSTICS_HTML_SUFFIX = ".diagnostics.html"
GROUND_TRUTH_FIELDS = [
    "schema_version",
    "fixture_id",
    "browser",
    "declared_zoom",
    "sample_label",
    "expected_best_bid",
    "expected_best_ask",
    "expected_bid_count",
    "expected_ask_count",
    "expected_top_bid_values",
    "expected_top_ask_values",
    "notes",
    "reviewed",
]
PRICE_STATUS_VALUES = {"not_visible", "not_applicable"}
ACCURACY_METRICS = (
    "ground_truth_not_reviewed",
    "reviewed_count",
    "best_bid_exact_match",
    "best_ask_exact_match",
    "both_exact_match",
    "bid_missing",
    "ask_missing",
    "bid_wrong_value",
    "ask_wrong_value",
    "false_confident_bid",
    "false_confident_ask",
    "requires_review_true_positive",
    "requires_review_false_negative",
)


@dataclass(frozen=True)
class EvaluationFile:
    path: Path
    metadata_path: Path
    browser: str | None
    declared_zoom: str | None
    sample_label: str
    metadata: dict[str, Any] | None
    metadata_error: str | None


@dataclass(frozen=True)
class EvaluationOptions:
    input_dir: Path
    output_path: Path
    layout_profile_name: str
    ocr_backend_name: str
    pretty: bool = False
    quiet: bool = False
    verbose: bool = False
    dry_run: bool = False
    profile: bool = False
    limit: int | None = None
    only_browser: str | None = None
    only_zoom: str | None = None
    only_sample: str | None = None
    fail_fast: bool = False
    ocr_timeout_seconds: int = DEFAULT_OCR_TIMEOUT_SECONDS
    ground_truth_path: Path | None = None
    private_report_path: Path | None = None
    diagnostics_path: Path | None = None


@dataclass(frozen=True)
class TemplateResult:
    path: Path
    discovered_fixture_count: int
    existing_row_count: int
    appended_row_count: int
    stale_row_count: int


@dataclass(frozen=True)
class GroundTruthValue:
    decimal: Decimal | None
    status: str | None = None


@dataclass(frozen=True)
class GroundTruthRow:
    fixture_id: str
    browser: str | None
    declared_zoom: str | None
    sample_label: str | None
    expected_best_bid: GroundTruthValue
    expected_best_ask: GroundTruthValue
    expected_bid_count: int | None
    expected_ask_count: int | None
    expected_top_bid_values: tuple[GroundTruthValue, ...]
    expected_top_ask_values: tuple[GroundTruthValue, ...]
    notes: str
    reviewed: bool


@dataclass(frozen=True)
class GroundTruthLoadResult:
    rows: dict[str, GroundTruthRow]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    reviewed_count: int
    not_reviewed_count: int


class EvaluationInterrupted(KeyboardInterrupt):
    pass


class GroundTruthCsvError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = message.split(":", 1)[0]


class Progress:
    def __init__(self, *, quiet: bool, verbose: bool, stream: TextIO) -> None:
        self._quiet = quiet
        self._verbose = verbose
        self._stream = stream

    def info(self, message: str) -> None:
        if not self._quiet:
            print(message, file=self._stream, flush=True)

    def detail(self, message: str) -> None:
        if self._verbose and not self._quiet:
            print(f"  {message}", file=self._stream, flush=True)

    def summary(self, message: str) -> None:
        print(message, file=self._stream, flush=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate local private screen-recognition fixtures.")
    parser.add_argument("--input", help="Ignored private fixture directory.")
    parser.add_argument("--output", help="Ignored safe report JSON path.")
    parser.add_argument("--layout-profile", default="gaijin-market-desktop-v1")
    parser.add_argument("--ocr-backend", default="windows-ocr")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--create-ground-truth-template", action="store_true")
    parser.add_argument("--ground-truth", help="Ignored private ground-truth CSV path.")
    parser.add_argument("--private-report", help="Ignored private detailed report JSON path.")
    parser.add_argument("--diagnostics", help="Ignored private diagnostics HTML path.")
    parser.add_argument(
        "--diagnostics-from-private-report",
        help="Regenerate ignored private diagnostics HTML from an existing private report without OCR.",
    )
    parser.add_argument(
        "--diagnose-private-report",
        help="Write an anonymous JSON diagnostics report from an existing private report without OCR.",
    )
    parser.add_argument(
        "--diagnostic-report-output",
        help="Anonymous diagnostics JSON output path for --diagnose-private-report.",
    )
    parser.add_argument("--limit", type=_positive_int)
    parser.add_argument("--only-browser", choices=("edge", "chrome"))
    parser.add_argument("--only-zoom")
    parser.add_argument("--only-sample")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--ocr-timeout-seconds", type=_positive_int, default=DEFAULT_OCR_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    if args.quiet and args.verbose:
        parser.error("--quiet and --verbose cannot be used together")

    if args.diagnostics_from_private_report:
        diagnostics_path = Path(args.diagnostics) if args.diagnostics else None
        result = regenerate_diagnostics_from_private_report(
            private_report_path=Path(args.diagnostics_from_private_report),
            diagnostics_path=diagnostics_path,
        )
        if not args.quiet:
            print(
                "screen recognition diagnostics regenerated: "
                f"fixtures={result['fixture_count']} path={_redacted_path(Path(result['diagnostics_path']))}",
                file=sys.stderr,
                flush=True,
            )
        return

    if args.diagnose_private_report:
        diagnostic_output = (
            Path(args.diagnostic_report_output)
            if args.diagnostic_report_output
            else Path(args.diagnose_private_report).with_suffix(".anonymous-diagnostics.json")
        )
        diagnostics = load_anonymous_diagnostics(Path(args.diagnose_private_report))
        _write_report_atomic(diagnostic_output, diagnostics, pretty=args.pretty)
        if not args.quiet:
            print(
                "screen recognition anonymous diagnostics complete: "
                f"fixtures={diagnostics['fixture_count']} "
                f"pipelines={diagnostics['pipeline_diagnostics']['pipeline_count']} "
                f"path={_redacted_path(diagnostic_output)}",
                file=sys.stderr,
                flush=True,
            )
        return

    if not args.input:
        parser.error("--input is required unless an offline private-report mode is used")
    input_dir = Path(args.input)
    ground_truth_path = Path(args.ground_truth) if args.ground_truth else input_dir / GROUND_TRUTH_FILENAME

    if args.create_ground_truth_template:
        progress = Progress(quiet=args.quiet, verbose=args.verbose, stream=sys.stderr)
        result = create_private_ground_truth_template(input_dir=input_dir, output_path=ground_truth_path)
        progress.summary(
            "ground-truth template ready: "
            f"fixtures={result.discovered_fixture_count} "
            f"existing={result.existing_row_count} appended={result.appended_row_count} stale={result.stale_row_count}"
        )
        return

    if not args.output:
        parser.error("--output is required unless --create-ground-truth-template is used")

    output_path = Path(args.output)
    options = EvaluationOptions(
        input_dir=input_dir,
        output_path=output_path,
        layout_profile_name=args.layout_profile,
        ocr_backend_name=args.ocr_backend,
        pretty=args.pretty,
        quiet=args.quiet,
        verbose=args.verbose,
        dry_run=args.dry_run,
        profile=args.profile,
        limit=args.limit,
        only_browser=args.only_browser,
        only_zoom=args.only_zoom,
        only_sample=args.only_sample,
        fail_fast=args.fail_fast,
        ocr_timeout_seconds=args.ocr_timeout_seconds,
        ground_truth_path=ground_truth_path,
        private_report_path=Path(args.private_report) if args.private_report else _default_private_report_path(output_path),
        diagnostics_path=Path(args.diagnostics) if args.diagnostics else _default_diagnostics_path(output_path),
    )
    try:
        report = evaluate_private_fixtures_from_options(options)
    except EvaluationInterrupted:
        raise SystemExit(130) from None

    print(
        f"screen recognition evaluation complete: fixtures={report['summary']['fixture_count']} "
        f"processed={report['summary']['processed_count']} "
        f"requires_review={report['summary']['requires_review_count']}",
        file=sys.stderr,
        flush=True,
    )


def evaluate_private_fixtures(
    *,
    input_dir: Path,
    output_path: Path,
    layout_profile_name: str,
    ocr_backend_name: str,
    pretty: bool = False,
    quiet: bool = True,
    verbose: bool = False,
    dry_run: bool = False,
    profile: bool = False,
    limit: int | None = None,
    only_browser: str | None = None,
    only_zoom: str | None = None,
    only_sample: str | None = None,
    fail_fast: bool = False,
    ocr_timeout_seconds: int = DEFAULT_OCR_TIMEOUT_SECONDS,
    ground_truth_path: Path | None = None,
    private_report_path: Path | None = None,
    diagnostics_path: Path | None = None,
    progress_stream: TextIO | None = None,
) -> dict[str, Any]:
    return evaluate_private_fixtures_from_options(
        EvaluationOptions(
            input_dir=input_dir,
            output_path=output_path,
            layout_profile_name=layout_profile_name,
            ocr_backend_name=ocr_backend_name,
            pretty=pretty,
            quiet=quiet,
            verbose=verbose,
            dry_run=dry_run,
            profile=profile,
            limit=limit,
            only_browser=only_browser,
            only_zoom=only_zoom,
            only_sample=only_sample,
            fail_fast=fail_fast,
            ocr_timeout_seconds=ocr_timeout_seconds,
            ground_truth_path=ground_truth_path,
            private_report_path=private_report_path,
            diagnostics_path=diagnostics_path,
        ),
        progress_stream=progress_stream,
    )


def regenerate_diagnostics_from_private_report(
    *,
    private_report_path: Path,
    diagnostics_path: Path | None = None,
) -> dict[str, Any]:
    private_report = json.loads(private_report_path.read_text(encoding="utf-8"))
    input_dir = _private_report_input_dir(private_report, private_report_path)
    diagnostics_output = diagnostics_path or _diagnostics_path_from_private_report_path(private_report_path)
    _validate_path_within(input_dir, private_report_path)
    _validate_path_within(input_dir, diagnostics_output)
    layout_profile_name = _layout_profile_name_from_private_report(private_report)
    profile = get_layout_profile(layout_profile_name)
    results = private_report.get("results") or []
    if not isinstance(results, list):
        raise ValueError("private_report_invalid: results must be a list")
    _write_diagnostics_html(
        path=diagnostics_output,
        input_dir=input_dir,
        files=[],
        profile=profile,
        private_results=results,
    )
    return {
        "diagnostics_path": str(diagnostics_output.resolve()),
        "fixture_count": len(results),
    }


def evaluate_private_fixtures_from_options(
    options: EvaluationOptions,
    *,
    progress_stream: TextIO | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    progress = Progress(
        quiet=options.quiet,
        verbose=options.verbose,
        stream=progress_stream or sys.stderr,
    )
    progress.info(f"Evaluation input: {_redacted_path(options.input_dir)}")
    progress.detail("fixture discovery: running")

    discovery_started = time.perf_counter()
    files = _discover_files(options.input_dir)
    discovery_ms = int((time.perf_counter() - discovery_started) * 1000)
    png_count = len(files)
    json_counts = _classify_json_files(options.input_dir)
    metadata_count = json_counts["paired_metadata_count"]
    progress.info(f"Discovered {png_count} PNG fixtures")
    progress.info(
        "Classified JSON files: "
        f"paired_metadata={metadata_count} non_fixture={json_counts['non_fixture_json_count']}"
    )

    files = _apply_filters(files, options)
    if options.limit is not None:
        files = files[: options.limit]
    progress.detail(f"metadata parsing: matched {len(files)} selected fixtures")

    fixture_ids = {_fixture_id(item, options.input_dir) for item in files}
    ground_truth_path = options.ground_truth_path or options.input_dir / GROUND_TRUTH_FILENAME
    ground_truth = _load_private_ground_truth_if_present(ground_truth_path, fixture_ids)

    report = _new_report(
        options,
        fixture_count=len(files),
        discovered_png_count=png_count,
        metadata_count=metadata_count,
        json_counts=json_counts,
    )
    report["summary"]["stage_timings_ms"]["fixture_discovery"] = discovery_ms
    report["ground_truth"] = _ground_truth_report_summary(ground_truth_path, ground_truth)

    private_report = _new_private_report(options, report, ground_truth_path)
    if not files:
        report["message"] = PRIVATE_FIXTURE_MESSAGE
        report["summary"]["total_duration_ms"] = int((time.perf_counter() - started) * 1000)
        _write_report_atomic(options.output_path, report, pretty=options.pretty)
        _write_optional_private_outputs(options, private_report, [], None, pretty=options.pretty)
        progress.summary(
            "screen recognition evaluation complete: fixtures=0 processed=0 requires_review=0"
        )
        return report

    progress.detail("layout selection: loading")
    layout_started = time.perf_counter()
    profile = get_layout_profile(options.layout_profile_name)
    report["summary"]["stage_timings_ms"]["layout_selection"] = int((time.perf_counter() - layout_started) * 1000)
    recognizer = None
    if not options.dry_run:
        progress.detail("ocr backend: configuring")
        recognizer = get_recognizer(options.ocr_backend_name, timeout_seconds=options.ocr_timeout_seconds)

    processed = 0
    private_results: list[dict[str, Any]] = []
    try:
        for index, item in enumerate(files, start=1):
            progress.info(f"Processing {index}/{len(files)}: {_display_name(item)}")
            result, private_result = _evaluate_file(
                item,
                input_dir=options.input_dir,
                profile=profile,
                recognizer=recognizer,
                dry_run=options.dry_run,
                include_profile=options.profile,
                progress=progress,
            )
            processed += 1
            _apply_ground_truth(result, private_result, ground_truth.rows.get(result["fixture_id"]))
            report["results"].append(result)
            private_results.append(private_result)
            _accumulate_stage_timings(report, result)
            if result["requires_review"]:
                report["summary"]["requires_review_count"] += 1
            error_label = result["error_code"] or "none"
            if options.profile:
                profile_summary = result.get("profile") or {}
                progress.detail(
                    "profile: "
                    f"powershell_process_count={profile_summary.get('powershell_process_count', 0)} "
                    f"ocr_invocation_count={profile_summary.get('ocr_invocation_count', 0)} "
                    f"pipeline_count_attempted={profile_summary.get('pipeline_count_attempted', 0)}"
                )
            progress.info(
                f"Processed {index}/{len(files)}: {_display_name(item)} "
                f"status={result['status']} error={error_label} duration_ms={result['timings']['total_ms']}"
            )
            if options.fail_fast and result["error_code"]:
                progress.info("fail-fast: stopping after first fixture error")
                break
    except KeyboardInterrupt as exc:
        report["complete"] = False
        report["interrupted"] = True
        report["summary"]["processed_count"] = processed
        report["summary"]["total_duration_ms"] = int((time.perf_counter() - started) * 1000)
        private_report["complete"] = False
        private_report["interrupted"] = True
        private_report["results"] = private_results
        _write_report_atomic(_partial_report_path(options.output_path), report, pretty=options.pretty)
        if options.private_report_path is not None:
            _write_report_atomic(_partial_report_path(options.private_report_path), private_report, pretty=options.pretty)
        progress.summary("Evaluation interrupted by user")
        progress.summary(f"Processed {processed}/{len(files)} fixtures")
        raise EvaluationInterrupted() from exc

    report["summary"]["processed_count"] = processed
    report["summary"]["total_duration_ms"] = int((time.perf_counter() - started) * 1000)
    report["accuracy"] = _build_accuracy_summary(report["results"])
    report["missing_stage_distribution"] = _missing_stage_distribution(report["results"])
    report["profile_summary"] = _build_profile_summary(report["results"])
    if options.dry_run:
        report["message"] = f"dry-run complete: {processed} fixtures would be processed"

    private_report["results"] = private_results
    private_report["accuracy_private"] = _build_private_accuracy_details(private_results)
    private_report["profile_summary"] = report["profile_summary"]
    private_report["missing_stage_distribution"] = report["missing_stage_distribution"]
    _write_report_atomic(options.output_path, report, pretty=options.pretty)
    _write_optional_private_outputs(options, private_report, files[:processed], profile, pretty=options.pretty)
    return report


def create_private_ground_truth_template(*, input_dir: Path, output_path: Path) -> TemplateResult:
    files = _discover_files(input_dir)
    fixture_ids = {_fixture_id(item, input_dir) for item in files}
    rows_to_append = [_template_row(item, input_dir) for item in files]
    existing_ids: set[str] = set()
    existing_count = 0
    stale_count = 0
    if output_path.exists():
        with output_path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            missing = [field for field in GROUND_TRUTH_FIELDS if field not in fieldnames]
            if missing:
                raise GroundTruthCsvError(f"ground_truth_invalid: missing columns {', '.join(missing)}")
            for row in reader:
                existing_count += 1
                fixture_id = (row.get("fixture_id") or "").strip()
                if fixture_id:
                    existing_ids.add(fixture_id)
                    if fixture_id not in fixture_ids:
                        stale_count += 1
            append_rows = [row for row in rows_to_append if row["fixture_id"] not in existing_ids]
            if append_rows:
                with output_path.open("a", newline="", encoding="utf-8") as append_handle:
                    writer = csv.DictWriter(append_handle, fieldnames=fieldnames, extrasaction="ignore")
                    for row in append_rows:
                        writer.writerow(row)
            return TemplateResult(output_path, len(files), existing_count, len(append_rows), stale_count)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=GROUND_TRUTH_FIELDS)
        writer.writeheader()
        writer.writerows(rows_to_append)
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.replace(output_path)
    return TemplateResult(output_path, len(files), 0, len(rows_to_append), 0)


def _evaluate_file(
    item: EvaluationFile,
    *,
    input_dir: Path,
    profile: Any,
    recognizer: Any,
    dry_run: bool,
    include_profile: bool,
    progress: Progress,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    timings: dict[str, int] = {}
    warnings: list[str] = []
    errors: list[str] = []
    error_stage: str | None = None
    image_info: ImageInfo | None = None
    contract = ScreenContract()
    ocr_result: OcrResult | None = None
    stage_reasons: dict[str, str | None] = {"best_bid": None, "best_ask": None}
    roi_pixels: dict[str, tuple[int, int, int, int]] = {}

    if item.metadata_error:
        errors.append(item.metadata_error)
        error_stage = "metadata"

    if not item.metadata_path.is_file() and "metadata_missing" not in errors:
        errors.append("metadata_missing")
        error_stage = error_stage or "metadata"

    try:
        progress.detail("image decode: running")
        decode_started = time.perf_counter()
        image_info = read_image_info(item.path)
        timings["image_decode_ms"] = int((time.perf_counter() - decode_started) * 1000)
        progress.detail(f"image decode: done {timings['image_decode_ms']}ms")

        progress.detail("ROI resolution: running")
        roi_started = time.perf_counter()
        validate_layout_match(profile, image_info)
        for name, roi in profile.rois.items():
            resolved = resolve_roi_pixels(roi, image_info)
            roi_pixels[name] = resolved.as_tuple()
            warnings.extend(resolved.warnings)
        timings["roi_resolution_ms"] = int((time.perf_counter() - roi_started) * 1000)
        progress.detail(f"ROI resolution: done {timings['roi_resolution_ms']}ms")

        variant_count = len(windows_ocr_preprocessing_metadata().get("variants") or [])
        timings["preprocessing_pipeline_count"] = variant_count
        progress.detail(f"preprocessing pipelines configured: {variant_count}")
        if dry_run:
            progress.detail("ocr: skipped for dry-run")
        else:
            progress.detail("ocr: running")
            ocr_started = time.perf_counter()
            ocr_result = recognizer.recognize(OcrInvocation(item.path, profile, None))
            timings["ocr_ms"] = int((time.perf_counter() - ocr_started) * 1000)
            progress.detail(f"ocr: done {timings['ocr_ms']}ms")

            progress.detail("OCR output parsing: running")
            parse_started = time.perf_counter()
            if ocr_result.backend_version == WindowsOcrRecognizer.price_cells_v3_backend_version:
                contract, parse_warnings, parse_errors = parse_ocr_contract(
                    ocr_result.fields,
                    item_key=None,
                    price_selection_policy=PRICE_SELECTION_POLICY_PRICE_CELLS_V3,
                )
            else:
                contract, parse_warnings, parse_errors = parse_ocr_contract(
                    ocr_result.fields,
                    item_key=None,
                )
            timings["ocr_output_parsing_ms"] = int((time.perf_counter() - parse_started) * 1000)
            warnings.extend(ocr_result.warnings)
            warnings.extend(parse_warnings)
            errors.extend(parse_errors)
            stage_reasons["best_bid"] = _price_missing_stage_reason(
                "best_bid", "bid_levels", contract.best_bid, ocr_result, parse_errors
            )
            stage_reasons["best_ask"] = _price_missing_stage_reason(
                "best_ask", "ask_levels", contract.best_ask, ocr_result, parse_errors
            )
            progress.detail(f"OCR output parsing: done {timings['ocr_output_parsing_ms']}ms")
    except ImageReadError:
        errors.append("image_decode_failed")
        error_stage = error_stage or "image_decode"
    except LayoutUnsupportedError:
        errors.append("layout_not_supported")
        error_stage = error_stage or "roi_resolution"
    except RoiValidationError as exc:
        errors.append(exc.code)
        error_stage = error_stage or "roi_resolution"
    except OcrBackendTimeoutError:
        errors.append("ocr_timeout")
        error_stage = error_stage or "ocr_execution"
    except OcrBackendNotConfiguredError:
        errors.append("image_recognizer_not_configured")
        error_stage = error_stage or "ocr_execution"
    except OcrBackendError:
        errors.append("ocr_process_failed")
        error_stage = error_stage or "ocr_execution"
    except (json.JSONDecodeError, ValueError, TypeError):
        errors.append("ocr_output_invalid")
        error_stage = error_stage or "ocr_output_parsing"

    timings["total_ms"] = int((time.perf_counter() - started) * 1000)
    unique_errors = tuple(sorted(set(errors)))
    unique_warnings = tuple(sorted(set(warnings)))
    status = "dry_run" if dry_run and not unique_errors else "ok"
    if unique_errors:
        status = "failed"
    elif unique_warnings:
        status = "requires_review"

    profile_summary = _profile_from_ocr_result(ocr_result, timings, include_profile=include_profile)
    fixture_id = _fixture_id(item, input_dir)
    pipeline = _selected_pipeline_label(ocr_result)
    result = {
        "fixture_id": fixture_id,
        "browser": item.browser,
        "declared_zoom": item.declared_zoom,
        "sample_label": item.sample_label,
        "status": status,
        "error_code": unique_errors[0] if unique_errors else None,
        "error_stage": error_stage,
        "stage_reasons": {key: value for key, value in stage_reasons.items() if value is not None},
        "requires_review": bool(unique_errors or unique_warnings),
        "ocr_skipped": dry_run,
        "warnings": list(unique_warnings),
        "errors": list(unique_errors),
        "image_format": None if image_info is None else image_info.format,
        "layout_profile": profile.name,
        "preprocessing_pipeline": pipeline,
        "timings": timings,
        "profile": profile_summary if include_profile else None,
        "ground_truth_status": "not_loaded",
        "accuracy": None,
    }
    private_result = {
        **result,
        "image_path": str(item.path.resolve()),
        "metadata_path": str(item.metadata_path.resolve()),
        "image_info": None if image_info is None else image_info.to_json(),
        "roi_pixels": {name: list(value) for name, value in sorted(roi_pixels.items())},
        "recognized": contract.to_json(),
        "raw_ocr": {} if ocr_result is None else ocr_result.to_json(),
    }
    return result, private_result


def _discover_files(input_dir: Path) -> list[EvaluationFile]:
    if not input_dir.is_dir():
        return []
    files: list[EvaluationFile] = []
    for index, path in enumerate(sorted(input_dir.rglob("*.png")), start=1):
        if not path.is_file():
            continue
        metadata_path = path.with_suffix(".json")
        if not metadata_path.is_file():
            continue
        metadata, metadata_error = _read_metadata(metadata_path)
        relative_parts = path.relative_to(input_dir).parts
        path_browser = relative_parts[0] if len(relative_parts) >= 3 else None
        path_zoom = relative_parts[1] if len(relative_parts) >= 3 else None
        browser = _safe_metadata_text(metadata, ("browser", "browser_name")) or _safe_path_label(path_browser)
        zoom = _safe_metadata_text(metadata, ("zoom", "browser_zoom", "declared_zoom")) or _safe_path_label(path_zoom)
        sample_label = (
            _safe_metadata_text(metadata, ("sample_label", "sample_id", "fixture_label", "fixture_id", "capture_label"))
            or f"sample-{index:03d}"
        )
        files.append(
            EvaluationFile(
                path=path,
                metadata_path=metadata_path,
                browser=browser,
                declared_zoom=zoom,
                sample_label=sample_label,
                metadata=metadata,
                metadata_error=metadata_error,
            )
        )
    return files


def _read_metadata(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "metadata_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None, "metadata_invalid"
    if not isinstance(payload, dict):
        return None, "metadata_invalid"
    return payload, None


def _classify_json_files(input_dir: Path) -> dict[str, int]:
    if not input_dir.is_dir():
        return {"json_file_count": 0, "paired_metadata_count": 0, "non_fixture_json_count": 0}
    total = 0
    paired = 0
    for path in input_dir.rglob("*.json"):
        if not path.is_file():
            continue
        total += 1
        if path.with_suffix(".png").is_file():
            paired += 1
    return {
        "json_file_count": total,
        "paired_metadata_count": paired,
        "non_fixture_json_count": total - paired,
    }


def _apply_filters(files: list[EvaluationFile], options: EvaluationOptions) -> list[EvaluationFile]:
    selected = files
    if options.only_browser is not None:
        selected = [item for item in selected if (item.browser or "").lower() == options.only_browser.lower()]
    if options.only_zoom is not None:
        selected = [item for item in selected if str(item.declared_zoom or "") == options.only_zoom]
    if options.only_sample is not None:
        selected = [item for item in selected if item.sample_label == options.only_sample]
    return selected


def _load_private_ground_truth_if_present(path: Path, known_fixture_ids: set[str]) -> GroundTruthLoadResult:
    if not path.is_file():
        return GroundTruthLoadResult({}, (), ("ground_truth_missing",), 0, 0)
    try:
        return _load_private_ground_truth(path, known_fixture_ids)
    except GroundTruthCsvError as exc:
        return GroundTruthLoadResult({}, (), (exc.code,), 0, 0)


def _load_private_ground_truth(path: Path, known_fixture_ids: set[str]) -> GroundTruthLoadResult:
    rows: dict[str, GroundTruthRow] = {}
    warnings: list[str] = []
    reviewed_count = 0
    not_reviewed_count = 0
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [field for field in GROUND_TRUTH_FIELDS if field not in fieldnames]
        if missing:
            raise GroundTruthCsvError("ground_truth_invalid")
        for line_number, raw in enumerate(reader, start=2):
            fixture_id = (raw.get("fixture_id") or "").strip()
            if not fixture_id:
                raise GroundTruthCsvError("ground_truth_invalid")
            if fixture_id in rows:
                raise GroundTruthCsvError("ground_truth_duplicate")
            if fixture_id not in known_fixture_ids:
                warnings.append("ground_truth_fixture_unknown")
            reviewed = _parse_bool(raw.get("reviewed"), line_number)
            row = GroundTruthRow(
                fixture_id=fixture_id,
                browser=_blank_to_none(raw.get("browser")),
                declared_zoom=_blank_to_none(raw.get("declared_zoom")),
                sample_label=_blank_to_none(raw.get("sample_label")),
                expected_best_bid=_parse_ground_truth_price(raw.get("expected_best_bid"), line_number),
                expected_best_ask=_parse_ground_truth_price(raw.get("expected_best_ask"), line_number),
                expected_bid_count=_parse_optional_int(raw.get("expected_bid_count"), line_number),
                expected_ask_count=_parse_optional_int(raw.get("expected_ask_count"), line_number),
                expected_top_bid_values=_parse_price_list(raw.get("expected_top_bid_values"), line_number),
                expected_top_ask_values=_parse_price_list(raw.get("expected_top_ask_values"), line_number),
                notes=raw.get("notes") or "",
                reviewed=reviewed,
            )
            if reviewed:
                reviewed_count += 1
            else:
                not_reviewed_count += 1
            rows[fixture_id] = row
    return GroundTruthLoadResult(
        rows=rows,
        warnings=tuple(sorted(set(warnings))),
        errors=(),
        reviewed_count=reviewed_count,
        not_reviewed_count=not_reviewed_count,
    )


def _apply_ground_truth(
    result: dict[str, Any],
    private_result: dict[str, Any],
    row: GroundTruthRow | None,
) -> None:
    if row is None:
        result["ground_truth_status"] = "missing"
        private_result["ground_truth_status"] = "missing"
        return
    private_result["expected"] = _ground_truth_row_to_private_json(row)
    if not row.reviewed:
        result["ground_truth_status"] = "not_reviewed"
        private_result["ground_truth_status"] = "not_reviewed"
        return
    recognized = private_result.get("recognized") or {}
    actual_bid = _decimal_or_none(recognized.get("best_bid"))
    actual_ask = _decimal_or_none(recognized.get("best_ask"))
    bid = _compare_price(row.expected_best_bid, actual_bid)
    ask = _compare_price(row.expected_best_ask, actual_ask)
    any_error = bid["error"] or ask["error"]
    requires_review = bool(result["requires_review"])
    accuracy = {
        "best_bid_exact_match": bid["exact_match"],
        "best_ask_exact_match": ask["exact_match"],
        "both_exact_match": bid["exact_match"] is True and ask["exact_match"] is True,
        "bid_missing": bid["missing"],
        "ask_missing": ask["missing"],
        "bid_wrong_value": bid["wrong_value"],
        "ask_wrong_value": ask["wrong_value"],
        "false_confident_bid": bid["wrong_value"] and not requires_review,
        "false_confident_ask": ask["wrong_value"] and not requires_review,
        "requires_review_true_positive": any_error and requires_review,
        "requires_review_false_negative": any_error and not requires_review,
    }
    result["ground_truth_status"] = "reviewed"
    result["accuracy"] = accuracy
    private_result["ground_truth_status"] = "reviewed"
    private_result["accuracy"] = {
        **accuracy,
        "actual_best_bid": None if actual_bid is None else str(actual_bid),
        "actual_best_ask": None if actual_ask is None else str(actual_ask),
    }


def _compare_price(expected: GroundTruthValue, actual: Decimal | None) -> dict[str, bool | None]:
    if expected.decimal is None:
        return {
            "exact_match": None,
            "missing": False,
            "wrong_value": actual is not None and expected.status in PRICE_STATUS_VALUES,
            "error": actual is not None and expected.status in PRICE_STATUS_VALUES,
        }
    if actual is None:
        return {"exact_match": False, "missing": True, "wrong_value": False, "error": True}
    exact = actual == expected.decimal
    return {"exact_match": exact, "missing": False, "wrong_value": not exact, "error": not exact}


def _build_accuracy_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Counter[str]]] = {
        "overall": {"all": Counter()},
        "browser": {},
        "zoom": {},
        "sample_label": {},
        "layout_profile": {},
        "preprocessing_pipeline": {},
    }
    for result in results:
        accuracy = result.get("accuracy")
        if result.get("ground_truth_status") != "reviewed" or not isinstance(accuracy, dict):
            if result.get("ground_truth_status") == "not_reviewed":
                grouped["overall"]["all"]["ground_truth_not_reviewed"] += 1
            continue
        labels = {
            "overall": "all",
            "browser": str(result.get("browser") or "unknown"),
            "zoom": str(result.get("declared_zoom") or "unknown"),
            "sample_label": str(result.get("sample_label") or "unknown"),
            "layout_profile": str(result.get("layout_profile") or "unknown"),
            "preprocessing_pipeline": str(result.get("preprocessing_pipeline") or "none"),
        }
        for dimension, label in labels.items():
            counter = grouped[dimension].setdefault(label, Counter())
            counter["reviewed_count"] += 1
            for metric in ACCURACY_METRICS:
                if metric == "reviewed_count":
                    continue
                if accuracy.get(metric):
                    counter[metric] += 1
    return {
        dimension: {
            label: _complete_metric_counter(counter)
            for label, counter in sorted(values.items())
        }
        for dimension, values in grouped.items()
    }


def _complete_metric_counter(counter: Counter[str]) -> dict[str, int]:
    return {metric: int(counter.get(metric, 0)) for metric in ACCURACY_METRICS}


def _build_private_accuracy_details(private_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    details = []
    for result in private_results:
        if result.get("ground_truth_status") != "reviewed":
            continue
        details.append(
            {
                "fixture_id": result["fixture_id"],
                "expected": result.get("expected"),
                "recognized": {
                    "best_bid": (result.get("recognized") or {}).get("best_bid"),
                    "best_ask": (result.get("recognized") or {}).get("best_ask"),
                },
                "accuracy": result.get("accuracy"),
            }
        )
    return details


def _missing_stage_distribution(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counters = {"best_bid": Counter(), "best_ask": Counter()}
    for result in results:
        reasons = result.get("stage_reasons") or {}
        for field in ("best_bid", "best_ask"):
            reason = reasons.get(field)
            if reason:
                counters[field][reason] += 1
    return {field: dict(sorted(counter.items())) for field, counter in counters.items()}


def _build_profile_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    per_fixture = []
    durations = []
    for result in results:
        timings = result.get("timings") or {}
        if isinstance(timings.get("total_ms"), int):
            durations.append(timings["total_ms"])
        profile = result.get("profile") or {}
        for key in (
            "powershell_process_count",
            "ocr_invocation_count",
            "pipeline_count_attempted",
            "pipeline_count_completed",
            "logical_pipeline_request_count",
            "unique_prepared_image_count",
            "deduplicated_ocr_request_count",
            "prepared_image_write_count",
            "prepared_image_read_count",
            "ocr_engine_initialization_count",
            "python_preprocessing_total_ms",
            "total_ocr_duration_ms",
        ):
            value = profile.get(key)
            if isinstance(value, int):
                totals[key] += value
        per_fixture.append(
            {
                "fixture_id": result["fixture_id"],
                "powershell_process_count": int(profile.get("powershell_process_count") or 0),
                "ocr_invocation_count": int(profile.get("ocr_invocation_count") or 0),
                "pipeline_count_attempted": int(profile.get("pipeline_count_attempted") or 0),
                "pipeline_count_completed": int(profile.get("pipeline_count_completed") or 0),
                "early_exit_used": bool(profile.get("early_exit_used") or False),
                "total_fixture_duration_ms": timings.get("total_ms"),
            }
        )
    durations_sorted = sorted(durations)
    return {
        "totals": dict(totals),
        "median_fixture_duration_ms": _percentile(durations_sorted, 50),
        "p95_fixture_duration_ms": _percentile(durations_sorted, 95),
        "per_fixture": per_fixture,
    }


def _profile_from_ocr_result(
    ocr_result: OcrResult | None,
    timings: dict[str, int],
    *,
    include_profile: bool,
) -> dict[str, Any]:
    diagnostics = {} if ocr_result is None else dict(ocr_result.diagnostics)
    summary = {
        "powershell_process_count": int(diagnostics.get("powershell_process_count") or 0),
        "ocr_invocation_count": int(diagnostics.get("ocr_invocation_count") or 0),
        "pipeline_count_attempted": int(diagnostics.get("pipeline_count_attempted") or 0),
        "pipeline_count_completed": int(diagnostics.get("pipeline_count_completed") or 0),
        "early_exit_used": bool(diagnostics.get("early_exit_used") or False),
        "per_pipeline_duration_ms": diagnostics.get("per_pipeline_duration_ms") or [],
        "total_ocr_duration_ms": int(diagnostics.get("total_ocr_duration_ms") or 0),
        "total_fixture_duration_ms": timings.get("total_ms"),
        "powershell_process_startup_overhead_ms": diagnostics.get("powershell_process_startup_overhead_ms"),
        "ocr_engine_initialization_total_ms": int(
            diagnostics.get("ocr_engine_initialization_total_ms")
            or _sum_pipeline_metric(diagnostics, "engine_initialization_ms")
        ),
        "ocr_execution_total_ms": _sum_pipeline_metric(diagnostics, "ocr_execution_ms"),
        "logical_pipeline_request_count": int(diagnostics.get("logical_pipeline_request_count") or 0),
        "unique_prepared_image_count": int(diagnostics.get("unique_prepared_image_count") or 0),
        "deduplicated_ocr_request_count": int(diagnostics.get("deduplicated_ocr_request_count") or 0),
        "prepared_image_write_count": int(diagnostics.get("prepared_image_write_count") or 0),
        "prepared_image_read_count": int(diagnostics.get("prepared_image_read_count") or 0),
        "ocr_engine_initialization_count": int(diagnostics.get("ocr_engine_initialization_count") or 0),
        "python_preprocessing_total_ms": int(diagnostics.get("python_preprocessing_total_ms") or 0),
        "python_batch_timings_ms": diagnostics.get("python_batch_timings_ms") or {},
    }
    return summary if include_profile else {
        key: summary[key]
        for key in (
            "powershell_process_count",
            "ocr_invocation_count",
            "pipeline_count_attempted",
            "pipeline_count_completed",
            "early_exit_used",
            "total_ocr_duration_ms",
            "logical_pipeline_request_count",
            "unique_prepared_image_count",
            "deduplicated_ocr_request_count",
            "ocr_engine_initialization_count",
        )
    }


def _sum_pipeline_metric(diagnostics: dict[str, Any], key: str) -> int:
    total = 0
    for item in diagnostics.get("per_pipeline_duration_ms") or []:
        if isinstance(item, dict) and isinstance(item.get(key), int):
            total += item[key]
    return total


def _price_missing_stage_reason(
    field_name: str,
    levels_field: str,
    value: Decimal | None,
    ocr_result: OcrResult,
    parse_errors: list[str],
) -> str | None:
    if value is not None:
        return None
    prefix = "bid" if field_name == "best_bid" else "ask"
    evidence = ocr_result.fields.get(field_name)
    level_evidence = ocr_result.fields.get(levels_field)
    field_errors = set(parse_errors)
    field_warnings = set(evidence.warnings if evidence is not None else ())
    if any("blank_roi_fast_path" in warning for warning in field_warnings):
        return f"{prefix}_roi_empty"
    raw = "" if evidence is None else evidence.raw_text.strip()
    level_raw = "" if level_evidence is None else level_evidence.raw_text.strip()
    if not raw and not level_raw:
        return f"{prefix}_ocr_empty"
    if "price_decimal_unconfirmed" in field_errors or "price_ocr_invalid" in field_errors:
        return f"{prefix}_price_parse_failed"
    if "ocr_candidate_ambiguous" in field_errors:
        return f"{prefix}_candidates_rejected"
    if f"{field_name}_missing" in field_errors:
        return f"{prefix}_selection_failed"
    return f"{prefix}_schema_discarded"


def _selected_pipeline_label(ocr_result: OcrResult | None) -> str | None:
    if ocr_result is None:
        return None
    labels = []
    for evidence in ocr_result.fields.values():
        for warning in evidence.warnings:
            if warning.startswith("preprocessing_pipeline:"):
                labels.append(warning.split(":", 1)[1])
    if not labels:
        return None
    return "+".join(sorted(set(labels)))


def _write_optional_private_outputs(
    options: EvaluationOptions,
    private_report: dict[str, Any],
    files: list[EvaluationFile],
    profile: Any,
    *,
    pretty: bool,
) -> None:
    private_report_path = options.private_report_path or _default_private_report_path(options.output_path)
    diagnostics_path = options.diagnostics_path or _default_diagnostics_path(options.output_path)
    _write_report_atomic(private_report_path, private_report, pretty=pretty)
    if profile is not None:
        _write_diagnostics_html(
            path=diagnostics_path,
            input_dir=options.input_dir,
            files=files,
            profile=profile,
            private_results=private_report.get("results") or [],
        )


def _write_diagnostics_html(
    *,
    path: Path,
    input_dir: Path,
    files: list[EvaluationFile],
    profile: Any,
    private_results: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    crops_dir = _diagnostics_assets_dir(path)
    crops_dir.mkdir(parents=True, exist_ok=True)
    entries = _diagnostic_entries(files=files, private_results=private_results, input_dir=input_dir)
    summary = _diagnostics_summary([entry["result"] for entry in entries])
    browsers = sorted({str(entry["browser"] or "unknown") for entry in entries})
    zooms = sorted({str(entry["declared_zoom"] or "unknown") for entry in entries})
    samples = sorted({str(entry["sample_label"] or "unknown") for entry in entries})
    rows = []
    nav_links = []
    for entry in entries:
        result = entry["result"]
        anonymous_id = str(entry["anonymous_id"])
        image_path = entry["image_path"]
        crop_links = _write_roi_crops(
            image_path=image_path,
            input_dir=input_dir,
            profile=profile,
            crops_dir=crops_dir,
            anonymous_id=anonymous_id,
            roi_pixels=result.get("roi_pixels") if isinstance(result, dict) else None,
        )
        reviewed = str(result.get("ground_truth_status") == "reviewed").lower()
        status = str(result.get("status") or "unknown")
        browser = str(entry["browser"] or "unknown")
        zoom = str(entry["declared_zoom"] or "unknown")
        sample = str(entry["sample_label"] or "unknown")
        expected = result.get("expected") if isinstance(result.get("expected"), dict) else {}
        expected_fields_filled = _expected_fields_filled(expected)
        accuracy = result.get("accuracy") if reviewed == "true" and isinstance(result.get("accuracy"), dict) else None
        nav_links.append(f"<a href=\"#{html.escape(anonymous_id)}\">{html.escape(anonymous_id)}</a>")
        rows.append(
            f"<section id=\"{html.escape(anonymous_id)}\" class=\"fixture-card\" "
            f"data-browser=\"{html.escape(browser)}\" data-zoom=\"{html.escape(zoom)}\" "
            f"data-sample=\"{html.escape(sample)}\" data-reviewed=\"{reviewed}\" "
            f"data-status=\"{html.escape(status)}\">"
            f"<h2>{html.escape(anonymous_id)}</h2>"
            f"<p>browser={html.escape(browser)} "
            f"zoom={html.escape(zoom)} "
            f"sample={html.escape(sample)} "
            f"status={html.escape(status)} "
            f"ground_truth={html.escape(str(result.get('ground_truth_status', 'unknown')))}</p>"
            f"<p>expected_fields_filled={html.escape(str(expected_fields_filled).lower())} "
            f"exact_match={html.escape(_exact_match_label(accuracy))}</p>"
            f"<p>{_html_image(image_path, path.parent, 'fixture screenshot')}</p>"
            f"<p>bid ROI: {_html_link(crop_links.get('best_bid'), path.parent)} "
            f"ask ROI: {_html_link(crop_links.get('best_ask'), path.parent)}</p>"
            f"<pre>{html.escape(json.dumps(_diagnostic_result_summary(result), ensure_ascii=False, indent=2))}</pre>"
            "</section>"
        )
    filter_html = _diagnostics_filter_html(browsers=browsers, zooms=zooms, samples=samples)
    document = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Private Screen Recognition Diagnostics</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px;}"
        ".summary,.filters{border:1px solid #ccc;padding:12px;margin:12px 0;}"
        ".nav a{display:inline-block;margin:0 8px 8px 0;}"
        "section{border-top:1px solid #ccc;padding:16px 0;}"
        "section[hidden]{display:none;}"
        ".screenshot{max-width:960px;width:100%;height:auto;}"
        ".roi-preview{max-width:360px;width:48%;height:auto;border:1px solid #ddd;}"
        ".placeholder{display:inline-block;padding:10px;border:1px dashed #aaa;color:#555;}"
        "pre{white-space:pre-wrap;background:#f6f6f6;padding:12px;}</style>"
        "</head><body><h1>Private Screen Recognition Diagnostics</h1>"
        "<p>This ignored local report may contain private screenshot previews and OCR debug details.</p>"
        f"<div class=\"summary\">total={summary['total']} success={summary['success']} "
        f"failed={summary['failed']} reviewed={summary['reviewed']}</div>"
        + filter_html
        + "<p><button type=\"button\" id=\"prev-fixture\">Previous</button> "
        "<button type=\"button\" id=\"next-fixture\">Next</button></p>"
        + "<nav class=\"nav\">" + "".join(nav_links) + "</nav>"
        + "".join(rows)
        + _diagnostics_filter_script()
        + "</body></html>"
    )
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(document, encoding="utf-8")
    temp_path.replace(path)


def _diagnostic_entries(
    *,
    files: list[EvaluationFile],
    private_results: list[dict[str, Any]],
    input_dir: Path,
) -> list[dict[str, Any]]:
    result_by_id = {result.get("fixture_id"): result for result in private_results if isinstance(result, dict)}
    entries: list[dict[str, Any]] = []
    if files:
        for item in files:
            fixture_id = _fixture_id(item, input_dir)
            result = result_by_id.get(fixture_id, {})
            entries.append(
                {
                    "result": result,
                    "anonymous_id": _anonymous_fixture_id(
                        browser=item.browser,
                        declared_zoom=item.declared_zoom,
                        sample_label=item.sample_label,
                        stable_source=_relative_stable_source(item.path, input_dir),
                    ),
                    "image_path": item.path,
                    "browser": item.browser,
                    "declared_zoom": item.declared_zoom,
                    "sample_label": item.sample_label,
                }
            )
        return entries

    for index, result in enumerate(private_results, start=1):
        if not isinstance(result, dict):
            continue
        image_path = _private_result_image_path(result, input_dir)
        sample_label = result.get("sample_label") or f"sample-{index:03d}"
        entries.append(
            {
                "result": result,
                "anonymous_id": _anonymous_fixture_id(
                    browser=_string_or_none(result.get("browser")),
                    declared_zoom=_string_or_none(result.get("declared_zoom")),
                    sample_label=str(sample_label),
                    stable_source=_relative_stable_source(image_path, input_dir)
                    if image_path is not None
                    else str(result.get("fixture_id") or index),
                ),
                "image_path": image_path,
                "browser": result.get("browser"),
                "declared_zoom": result.get("declared_zoom"),
                "sample_label": sample_label,
            }
        )
    return entries


def _diagnostics_summary(results: list[dict[str, Any]]) -> dict[str, int]:
    total = len(results)
    failed = sum(1 for result in results if result.get("status") == "failed" or result.get("error_code"))
    reviewed = sum(1 for result in results if result.get("ground_truth_status") == "reviewed")
    return {
        "total": total,
        "success": total - failed,
        "failed": failed,
        "reviewed": reviewed,
    }


def _diagnostics_filter_html(*, browsers: list[str], zooms: list[str], samples: list[str]) -> str:
    return (
        "<div class=\"filters\">"
        f"browser={_select_html('browser-filter', browsers)} "
        f"zoom={_select_html('zoom-filter', zooms)} "
        f"sample={_select_html('sample-filter', samples)} "
        "<label><input type=\"checkbox\" id=\"unreviewed-filter\"> only unreviewed</label> "
        "<label><input type=\"checkbox\" id=\"failed-filter\"> only failed</label>"
        "</div>"
    )


def _select_html(element_id: str, values: list[str]) -> str:
    options = ["<option value=\"\">all</option>"]
    options.extend(
        f"<option value=\"{html.escape(value)}\">{html.escape(value)}</option>"
        for value in values
    )
    return f"<select id=\"{html.escape(element_id)}\">" + "".join(options) + "</select>"


def _diagnostics_filter_script() -> str:
    return (
        "<script>"
        "const cards=[...document.querySelectorAll('.fixture-card')];"
        "let current=0;"
        "function visible(){return cards.filter(c=>!c.hidden)}"
        "function applyFilters(){"
        "const b=document.getElementById('browser-filter').value;"
        "const z=document.getElementById('zoom-filter').value;"
        "const s=document.getElementById('sample-filter').value;"
        "const u=document.getElementById('unreviewed-filter').checked;"
        "const f=document.getElementById('failed-filter').checked;"
        "cards.forEach(c=>{c.hidden=!!((b&&c.dataset.browser!==b)||(z&&c.dataset.zoom!==z)||(s&&c.dataset.sample!==s)"
        "||(u&&c.dataset.reviewed==='true')||(f&&c.dataset.status!=='failed'));});"
        "current=0;"
        "}"
        "['browser-filter','zoom-filter','sample-filter','unreviewed-filter','failed-filter'].forEach(id=>"
        "document.getElementById(id).addEventListener('change',applyFilters));"
        "function jump(delta){const v=visible();if(!v.length)return;current=(current+delta+v.length)%v.length;location.hash=v[current].id;}"
        "document.getElementById('prev-fixture').addEventListener('click',()=>jump(-1));"
        "document.getElementById('next-fixture').addEventListener('click',()=>jump(1));"
        "</script>"
    )


def _resolved_roi_pixels(field: str, roi_pixels: Any, profile: Any, info: ImageInfo) -> tuple[int, int, int, int] | None:
    if isinstance(roi_pixels, dict):
        value = roi_pixels.get(field)
        if isinstance(value, list) and len(value) == 4 and all(isinstance(part, int) for part in value):
            return (value[0], value[1], value[2], value[3])
    roi = profile.rois.get(field)
    if roi is None:
        return None
    return resolve_roi_pixels(roi, info).as_tuple()


def _write_roi_crops(
    *,
    image_path: Path | None,
    input_dir: Path,
    profile: Any,
    crops_dir: Path,
    anonymous_id: str,
    roi_pixels: Any,
) -> dict[str, Path]:
    from PIL import Image

    links: dict[str, Path] = {}
    if image_path is None:
        return links
    try:
        _validate_path_within(input_dir, image_path)
        info = read_image_info(image_path)
        with Image.open(image_path) as image:
            for field in ("best_bid", "best_ask"):
                resolved_pixels = _resolved_roi_pixels(field, roi_pixels, profile, info)
                if resolved_pixels is None:
                    continue
                x, y, width, height = resolved_pixels
                crop_path = crops_dir / f"{anonymous_id}-{field}.png"
                image.crop((x, y, x + width, y + height)).save(crop_path)
                links[field] = crop_path
    except Exception:
        return links
    return links


def _diagnostic_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    return _redact_sensitive_markers({
        "status": result.get("status"),
        "error_code": result.get("error_code"),
        "error_stage": result.get("error_stage"),
        "stage_reasons": result.get("stage_reasons"),
        "requires_review": result.get("requires_review"),
        "ground_truth_status": result.get("ground_truth_status"),
        "expected_present": isinstance(result.get("expected"), dict),
        "timings": result.get("timings"),
        "profile": result.get("profile"),
        "accuracy": result.get("accuracy"),
        "raw_ocr": result.get("raw_ocr"),
    })


def _html_link(path: Path | None, html_dir: Path) -> str:
    if path is None:
        return "<span class=\"placeholder\">unavailable</span>"
    escaped = html.escape(_relative_href(path, html_dir))
    return f"<a href=\"{escaped}\"><img class=\"roi-preview\" src=\"{escaped}\" alt=\"{html.escape(path.name)}\"></a>"


def _html_image(path: Path | None, html_dir: Path, alt: str) -> str:
    if path is None:
        return "<span class=\"placeholder\">screenshot unavailable</span>"
    escaped = html.escape(_relative_href(path, html_dir))
    return f"<img class=\"screenshot\" src=\"{escaped}\" alt=\"{html.escape(alt)}\">"


def _relative_href(path: Path, html_dir: Path) -> str:
    return Path(os.path.relpath(path.resolve(), html_dir.resolve())).as_posix()


def _default_private_report_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.private{output_path.suffix or '.json'}")


def _default_diagnostics_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}{DIAGNOSTICS_HTML_SUFFIX}")


def _diagnostics_path_from_private_report_path(private_report_path: Path) -> Path:
    name = private_report_path.name
    if name.endswith(DIAGNOSTICS_PRIVATE_REPORT_SUFFIX):
        return private_report_path.with_name(f"{name[: -len(DIAGNOSTICS_PRIVATE_REPORT_SUFFIX)]}{DIAGNOSTICS_HTML_SUFFIX}")
    return private_report_path.with_name(f"{private_report_path.stem}{DIAGNOSTICS_HTML_SUFFIX}")


def _diagnostics_assets_dir(diagnostics_path: Path) -> Path:
    name = diagnostics_path.name
    if name.endswith(".html"):
        return diagnostics_path.with_name(name[:-5])
    return diagnostics_path.with_name(f"{name}.assets")


def _private_report_input_dir(private_report: dict[str, Any], private_report_path: Path) -> Path:
    value = private_report.get("input_dir")
    if isinstance(value, str) and value.strip():
        return Path(value)
    return private_report_path.parent


def _layout_profile_name_from_private_report(private_report: dict[str, Any]) -> str:
    results = private_report.get("results") or []
    if isinstance(results, list):
        for result in results:
            if isinstance(result, dict) and isinstance(result.get("layout_profile"), str):
                return result["layout_profile"]
    return "gaijin-market-desktop-v1"


def _private_result_image_path(result: dict[str, Any], input_dir: Path) -> Path | None:
    value = result.get("image_path")
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    try:
        _validate_path_within(input_dir, path)
    except ValueError:
        return None
    return path


def _validate_path_within(root: Path, path: Path) -> None:
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("private_report_invalid: referenced path is outside private artifacts directory") from exc


def _relative_stable_source(path: Path | None, input_dir: Path) -> str:
    if path is None:
        return "missing-image"
    try:
        return path.resolve().relative_to(input_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def _anonymous_fixture_id(
    *,
    browser: str | None,
    declared_zoom: str | None,
    sample_label: str,
    stable_source: str,
) -> str:
    parts = [
        _slug_part(browser or "unknown-browser"),
        _zoom_slug(declared_zoom),
        _slug_part(sample_label or "sample"),
    ]
    digest = hashlib.sha256(stable_source.encode("utf-8")).hexdigest()[:8]
    return "-".join(part for part in parts if part) + f"-{digest}"


def _slug_part(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    collapsed = "-".join(part for part in cleaned.split("-") if part)
    return collapsed[:48] or "unknown"


def _zoom_slug(value: str | None) -> str:
    if value is None:
        return "zoom-unknown"
    text = str(value).strip().lower()
    try:
        scaled = int((Decimal(text) * Decimal("100")).to_integral_value())
    except (InvalidOperation, ValueError):
        scaled = -1
    if 0 <= scaled <= 999:
        return f"{scaled:03d}"
    return _slug_part(text)


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _expected_fields_filled(expected: Any) -> bool:
    if not isinstance(expected, dict):
        return False
    keys = ("expected_best_bid", "expected_best_ask", "expected_bid_count", "expected_ask_count")
    return any(expected.get(key) not in (None, "", []) for key in keys)


def _exact_match_label(accuracy: dict[str, Any] | None) -> str:
    if accuracy is None:
        return "not_applicable"
    value = accuracy.get("both_exact_match")
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


def _redact_sensitive_markers(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_sensitive_markers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive_markers(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in ("http://", "https://", "authorization", "token", "pairing")):
            return "[redacted]"
    return value


def _safe_metadata_text(metadata: dict[str, Any] | None, keys: tuple[str, ...]) -> str | None:
    if metadata is None:
        return None
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if _is_safe_label(text):
            return text[:64]
    return None


def _safe_path_label(value: str | None) -> str | None:
    if value is None:
        return None
    return value if _is_safe_label(value) else None


def _is_safe_label(text: str) -> bool:
    if not text or len(text) > 128:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in ("http://", "https://", "data:", "authorization", "token", "pairing")):
        return False
    return all(char.isalnum() or char in {"-", "_", ".", " "} for char in text)


def _display_name(item: EvaluationFile) -> str:
    parts = [item.browser or "unknown-browser", f"zoom {item.declared_zoom or 'unknown'}", item.sample_label]
    return " / ".join(parts)


def _fixture_id(item: EvaluationFile, input_dir: Path) -> str:
    try:
        relative = item.path.relative_to(input_dir).as_posix()
    except ValueError:
        relative = item.path.name
    return f"fixture-{hashlib.sha256(relative.encode('utf-8')).hexdigest()[:12]}"


def _template_row(item: EvaluationFile, input_dir: Path) -> dict[str, str]:
    return {
        "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
        "fixture_id": _fixture_id(item, input_dir),
        "browser": item.browser or "",
        "declared_zoom": item.declared_zoom or "",
        "sample_label": item.sample_label,
        "expected_best_bid": "",
        "expected_best_ask": "",
        "expected_bid_count": "",
        "expected_ask_count": "",
        "expected_top_bid_values": "",
        "expected_top_ask_values": "",
        "notes": "",
        "reviewed": "false",
    }


def _new_report(
    options: EvaluationOptions,
    *,
    fixture_count: int,
    discovered_png_count: int,
    metadata_count: int,
    json_counts: dict[str, int],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "complete": True,
        "interrupted": False,
        "input_exists": options.input_dir.exists(),
        "private_paths_recorded": False,
        "database_access": False,
        "network_access": False,
        "layout_profile": options.layout_profile_name,
        "ocr_backend": options.ocr_backend_name,
        "dry_run": options.dry_run,
        "profile_enabled": options.profile,
        "ocr_timeout_seconds": options.ocr_timeout_seconds,
        "filters": {
            "limit": options.limit,
            "only_browser": options.only_browser,
            "only_zoom": options.only_zoom,
            "only_sample": options.only_sample,
        },
        "message": None,
        "results": [],
        "summary": {
            "discovered_png_count": discovered_png_count,
            "json_file_count": json_counts["json_file_count"],
            "metadata_count": metadata_count,
            "non_fixture_json_count": json_counts["non_fixture_json_count"],
            "fixture_count": fixture_count,
            "processed_count": 0,
            "requires_review_count": 0,
            "stage_timings_ms": {
                "fixture_discovery": 0,
                "metadata_parsing": 0,
                "image_decode": 0,
                "layout_selection": 0,
                "roi_resolution": 0,
                "ocr_execution": 0,
                "ocr_output_parsing": 0,
                "candidate_validation": 0,
                "report_serialization": 0,
            },
            "total_duration_ms": None,
        },
    }


def _new_private_report(
    options: EvaluationOptions,
    safe_report: dict[str, Any],
    ground_truth_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": f"{SCHEMA_VERSION}.private",
        "complete": safe_report["complete"],
        "interrupted": False,
        "private_paths_recorded": True,
        "database_access": False,
        "network_access": False,
        "input_dir": str(options.input_dir.resolve()),
        "ground_truth_path": str(ground_truth_path.resolve()),
        "safe_report_path": str(options.output_path.resolve()),
        "summary": safe_report["summary"],
        "ground_truth": safe_report["ground_truth"],
        "results": [],
    }


def _ground_truth_report_summary(path: Path, ground_truth: GroundTruthLoadResult) -> dict[str, Any]:
    if "ground_truth_missing" in ground_truth.errors:
        status = "missing"
    elif ground_truth.errors:
        status = "invalid"
    else:
        status = "loaded"
    return {
        "path_recorded": False,
        "filename": path.name,
        "status": status,
        "reviewed_count": ground_truth.reviewed_count,
        "not_reviewed_count": ground_truth.not_reviewed_count,
        "warnings": list(ground_truth.warnings),
        "errors": list(ground_truth.errors),
    }


def _accumulate_stage_timings(report: dict[str, Any], result: dict[str, Any]) -> None:
    stage_timings = report["summary"]["stage_timings_ms"]
    timings = result.get("timings") or {}
    stage_timings["image_decode"] += int(timings.get("image_decode_ms") or 0)
    stage_timings["roi_resolution"] += int(timings.get("roi_resolution_ms") or 0)
    stage_timings["ocr_execution"] += int(timings.get("ocr_ms") or 0)
    stage_timings["ocr_output_parsing"] += int(timings.get("ocr_output_parsing_ms") or 0)


def _write_report_atomic(output_path: Path, report: dict[str, Any], *, pretty: bool) -> None:
    serialization_started = time.perf_counter()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.tmp")
    payload = json.dumps(report, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True) + "\n"
    if isinstance(report.get("summary"), dict) and isinstance(report["summary"].get("stage_timings_ms"), dict):
        report["summary"]["stage_timings_ms"]["report_serialization"] = int(
            (time.perf_counter() - serialization_started) * 1000
        )
        payload = json.dumps(report, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True) + "\n"
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(output_path)
    except OSError as exc:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise OcrBackendError("report_write_failed") from exc


def _partial_report_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.partial{output_path.suffix}")


def _redacted_path(path: Path) -> str:
    return f"<private>/{path.name}" if path.name else "<private>"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parse_bool(value: str | None, line_number: int) -> bool:
    normalized = (value or "").strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise GroundTruthCsvError(f"ground_truth_invalid: line {line_number}: reviewed must be true or false")


def _parse_ground_truth_price(value: str | None, line_number: int) -> GroundTruthValue:
    text = (value or "").strip()
    if not text:
        return GroundTruthValue(None)
    if text in PRICE_STATUS_VALUES:
        return GroundTruthValue(None, status=text)
    parsed = _parse_decimal(text, line_number)
    if parsed < Decimal("0.01") or parsed > Decimal("2000.00"):
        raise GroundTruthCsvError(f"ground_truth_invalid: line {line_number}: price out of range")
    if parsed.as_tuple().exponent < -2:
        raise GroundTruthCsvError(f"ground_truth_invalid: line {line_number}: price has more than two decimals")
    return GroundTruthValue(parsed)


def _parse_price_list(value: str | None, line_number: int) -> tuple[GroundTruthValue, ...]:
    text = (value or "").strip()
    if not text:
        return ()
    return tuple(_parse_ground_truth_price(part.strip(), line_number) for part in text.split(";") if part.strip())


def _parse_decimal(value: str, line_number: int) -> Decimal:
    if any(marker in value.lower() for marker in ("nan", "inf")):
        raise GroundTruthCsvError(f"ground_truth_invalid: line {line_number}: price must be finite")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise GroundTruthCsvError(f"ground_truth_invalid: line {line_number}: invalid Decimal") from exc
    if not parsed.is_finite():
        raise GroundTruthCsvError(f"ground_truth_invalid: line {line_number}: price must be finite")
    return parsed


def _parse_optional_int(value: str | None, line_number: int) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    if not text.isdecimal():
        raise GroundTruthCsvError(f"ground_truth_invalid: line {line_number}: count must be an integer")
    return int(text)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _blank_to_none(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _ground_truth_row_to_private_json(row: GroundTruthRow) -> dict[str, Any]:
    return {
        "fixture_id": row.fixture_id,
        "expected_best_bid": _ground_truth_value_to_json(row.expected_best_bid),
        "expected_best_ask": _ground_truth_value_to_json(row.expected_best_ask),
        "expected_bid_count": row.expected_bid_count,
        "expected_ask_count": row.expected_ask_count,
        "expected_top_bid_values": [_ground_truth_value_to_json(value) for value in row.expected_top_bid_values],
        "expected_top_ask_values": [_ground_truth_value_to_json(value) for value in row.expected_top_ask_values],
        "reviewed": row.reviewed,
        "notes": row.notes,
    }


def _ground_truth_value_to_json(value: GroundTruthValue) -> str | None:
    if value.status is not None:
        return value.status
    if value.decimal is None:
        return None
    return str(value.decimal)


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * (percentile / 100)
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    if lower == upper:
        return values[lower]
    fraction = rank - lower
    return int(values[lower] + ((values[upper] - values[lower]) * fraction))


if __name__ == "__main__":
    main()
