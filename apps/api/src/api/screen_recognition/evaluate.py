from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from api.screen_recognition.contracts import ImageInfo
from api.screen_recognition.image_io import ImageReadError, read_image_info
from api.screen_recognition.layouts import LayoutUnsupportedError, get_layout_profile, validate_layout_match
from api.screen_recognition.ocr_backend import (
    OcrBackendError,
    OcrBackendNotConfiguredError,
    OcrBackendTimeoutError,
    OcrInvocation,
    get_recognizer,
    windows_ocr_preprocessing_metadata,
)
from api.screen_recognition.parser import parse_ocr_contract
from api.screen_recognition.roi import RoiValidationError, resolve_roi_pixels


PRIVATE_FIXTURE_MESSAGE = "no private evaluation fixtures found"
SCHEMA_VERSION = "screen-recognition-private-evaluation/1.1.0"
DEFAULT_OCR_TIMEOUT_SECONDS = 60


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
    limit: int | None = None
    only_browser: str | None = None
    only_zoom: str | None = None
    only_sample: str | None = None
    fail_fast: bool = False
    ocr_timeout_seconds: int = DEFAULT_OCR_TIMEOUT_SECONDS


class EvaluationInterrupted(KeyboardInterrupt):
    pass


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
    parser.add_argument("--input", required=True, help="Ignored private fixture directory.")
    parser.add_argument("--output", required=True, help="Ignored private report JSON path.")
    parser.add_argument("--layout-profile", default="gaijin-market-desktop-v1")
    parser.add_argument("--ocr-backend", default="windows-ocr")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=_positive_int)
    parser.add_argument("--only-browser", choices=("edge", "chrome"))
    parser.add_argument("--only-zoom")
    parser.add_argument("--only-sample")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--ocr-timeout-seconds", type=_positive_int, default=DEFAULT_OCR_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    if args.quiet and args.verbose:
        parser.error("--quiet and --verbose cannot be used together")

    options = EvaluationOptions(
        input_dir=Path(args.input),
        output_path=Path(args.output),
        layout_profile_name=args.layout_profile,
        ocr_backend_name=args.ocr_backend,
        pretty=args.pretty,
        quiet=args.quiet,
        verbose=args.verbose,
        dry_run=args.dry_run,
        limit=args.limit,
        only_browser=args.only_browser,
        only_zoom=args.only_zoom,
        only_sample=args.only_sample,
        fail_fast=args.fail_fast,
        ocr_timeout_seconds=args.ocr_timeout_seconds,
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
    limit: int | None = None,
    only_browser: str | None = None,
    only_zoom: str | None = None,
    only_sample: str | None = None,
    fail_fast: bool = False,
    ocr_timeout_seconds: int = DEFAULT_OCR_TIMEOUT_SECONDS,
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
            limit=limit,
            only_browser=only_browser,
            only_zoom=only_zoom,
            only_sample=only_sample,
            fail_fast=fail_fast,
            ocr_timeout_seconds=ocr_timeout_seconds,
        ),
        progress_stream=progress_stream,
    )


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
    progress.detail("directory discovery: running")

    files = _discover_files(options.input_dir)
    png_count = len(files)
    metadata_count = _count_metadata_files(options.input_dir)
    progress.info(f"Discovered {png_count} PNG fixtures")
    progress.info(f"Discovered {metadata_count} metadata files")

    files = _apply_filters(files, options)
    if options.limit is not None:
        files = files[: options.limit]
    progress.detail(f"metadata parsing: matched {len(files)} selected fixtures")

    report = _new_report(options, fixture_count=len(files), discovered_png_count=png_count, metadata_count=metadata_count)
    if not files:
        report["message"] = PRIVATE_FIXTURE_MESSAGE
        report["summary"]["total_duration_ms"] = int((time.perf_counter() - started) * 1000)
        _write_report_atomic(options.output_path, report, pretty=options.pretty)
        progress.summary(
            "screen recognition evaluation complete: fixtures=0 processed=0 requires_review=0"
        )
        return report

    progress.detail("layout profile: loading")
    profile = get_layout_profile(options.layout_profile_name)
    recognizer = None
    if not options.dry_run:
        progress.detail("ocr backend: configuring")
        recognizer = get_recognizer(options.ocr_backend_name, timeout_seconds=options.ocr_timeout_seconds)

    processed = 0
    try:
        for index, item in enumerate(files, start=1):
            progress.info(f"Processing {index}/{len(files)}: {_display_name(item)}")
            result = _evaluate_file(
                item,
                input_dir=options.input_dir,
                profile=profile,
                recognizer=recognizer,
                dry_run=options.dry_run,
                progress=progress,
            )
            processed += 1
            report["results"].append(result)
            if result["requires_review"]:
                report["summary"]["requires_review_count"] += 1
            error_label = result["error_code"] or "none"
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
        _write_report_atomic(_partial_report_path(options.output_path), report, pretty=options.pretty)
        progress.summary("Evaluation interrupted by user")
        progress.summary(f"Processed {processed}/{len(files)} fixtures")
        raise EvaluationInterrupted() from exc

    report["summary"]["processed_count"] = processed
    report["summary"]["total_duration_ms"] = int((time.perf_counter() - started) * 1000)
    if options.dry_run:
        report["message"] = f"dry-run complete: {processed} fixtures would be processed"
    _write_report_atomic(options.output_path, report, pretty=options.pretty)
    return report


def _evaluate_file(
    item: EvaluationFile,
    *,
    input_dir: Path,
    profile: Any,
    recognizer: Any,
    dry_run: bool,
    progress: Progress,
) -> dict[str, Any]:
    started = time.perf_counter()
    timings: dict[str, int] = {}
    warnings: list[str] = []
    errors: list[str] = []
    error_stage: str | None = None
    image_info: ImageInfo | None = None

    if item.metadata_error:
        errors.append(item.metadata_error)
        error_stage = "metadata"

    if not item.metadata_path.is_file() and "metadata_missing" not in errors:
        errors.append("metadata_missing")
        error_stage = error_stage or "metadata"

    try:
        progress.detail("decode: running")
        decode_started = time.perf_counter()
        image_info = read_image_info(item.path)
        timings["decode_ms"] = int((time.perf_counter() - decode_started) * 1000)
        progress.detail(f"decode: done {timings['decode_ms']}ms")

        progress.detail("roi: running")
        roi_started = time.perf_counter()
        validate_layout_match(profile, image_info)
        for roi in profile.rois.values():
            resolved = resolve_roi_pixels(roi, image_info)
            warnings.extend(resolved.warnings)
        timings["roi_ms"] = int((time.perf_counter() - roi_started) * 1000)
        progress.detail(f"roi: done {timings['roi_ms']}ms")

        variant_count = len(windows_ocr_preprocessing_metadata().get("variants") or [])
        progress.detail(f"preprocess: {variant_count} pipelines")
        if dry_run:
            progress.detail("ocr: skipped for dry-run")
        else:
            progress.detail("ocr: running")
            ocr_started = time.perf_counter()
            ocr_result = recognizer.recognize(OcrInvocation(item.path, profile, None))
            timings["ocr_ms"] = int((time.perf_counter() - ocr_started) * 1000)
            progress.detail(f"ocr: done {timings['ocr_ms']}ms")

            progress.detail("result parsing: running")
            parse_started = time.perf_counter()
            _contract, parse_warnings, parse_errors = parse_ocr_contract(ocr_result.fields, item_key=None)
            timings["parse_ms"] = int((time.perf_counter() - parse_started) * 1000)
            warnings.extend(ocr_result.warnings)
            warnings.extend(parse_warnings)
            errors.extend(parse_errors)
            progress.detail(f"result parsing: done {timings['parse_ms']}ms")
    except ImageReadError:
        errors.append("image_decode_failed")
        error_stage = error_stage or "decode"
    except LayoutUnsupportedError:
        errors.append("layout_not_supported")
        error_stage = error_stage or "roi"
    except RoiValidationError as exc:
        errors.append(exc.code)
        error_stage = error_stage or "roi"
    except OcrBackendTimeoutError:
        errors.append("ocr_timeout")
        error_stage = error_stage or "ocr"
    except OcrBackendNotConfiguredError:
        errors.append("image_recognizer_not_configured")
        error_stage = error_stage or "ocr"
    except OcrBackendError:
        errors.append("ocr_process_failed")
        error_stage = error_stage or "ocr"
    except (json.JSONDecodeError, ValueError, TypeError):
        errors.append("ocr_output_invalid")
        error_stage = error_stage or "result_parse"

    timings["total_ms"] = int((time.perf_counter() - started) * 1000)
    unique_errors = sorted(set(errors))
    unique_warnings = sorted(set(warnings))
    status = "dry_run" if dry_run and not unique_errors else "ok"
    if unique_errors:
        status = "failed"
    elif unique_warnings:
        status = "requires_review"
    return {
        "fixture_id": _fixture_id(item, input_dir),
        "browser": item.browser,
        "declared_zoom": item.declared_zoom,
        "sample_label": item.sample_label,
        "status": status,
        "error_code": unique_errors[0] if unique_errors else None,
        "error_stage": error_stage,
        "requires_review": bool(unique_errors or unique_warnings),
        "ocr_skipped": dry_run,
        "warnings": unique_warnings,
        "errors": unique_errors,
        "image_format": None if image_info is None else image_info.format,
        "layout_profile": profile.name,
        "timings": timings,
    }


def _discover_files(input_dir: Path) -> list[EvaluationFile]:
    if not input_dir.is_dir():
        return []
    files: list[EvaluationFile] = []
    for index, path in enumerate(sorted(input_dir.rglob("*.png")), start=1):
        if not path.is_file():
            continue
        metadata_path = path.with_suffix(".json")
        metadata, metadata_error = _read_metadata(metadata_path)
        relative_parts = path.relative_to(input_dir).parts
        path_browser = relative_parts[0] if len(relative_parts) >= 3 else None
        path_zoom = relative_parts[1] if len(relative_parts) >= 3 else None
        browser = _safe_metadata_text(metadata, ("browser", "browser_name")) or path_browser
        zoom = _safe_metadata_text(metadata, ("zoom", "browser_zoom", "declared_zoom")) or path_zoom
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


def _count_metadata_files(input_dir: Path) -> int:
    if not input_dir.is_dir():
        return 0
    return sum(1 for path in input_dir.rglob("*.json") if path.is_file() and path.with_suffix(".png").is_file())


def _apply_filters(files: list[EvaluationFile], options: EvaluationOptions) -> list[EvaluationFile]:
    selected = files
    if options.only_browser is not None:
        selected = [item for item in selected if (item.browser or "").lower() == options.only_browser.lower()]
    if options.only_zoom is not None:
        selected = [item for item in selected if str(item.declared_zoom or "") == options.only_zoom]
    if options.only_sample is not None:
        selected = [item for item in selected if item.sample_label == options.only_sample]
    return selected


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


def _new_report(
    options: EvaluationOptions,
    *,
    fixture_count: int,
    discovered_png_count: int,
    metadata_count: int,
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
            "metadata_count": metadata_count,
            "fixture_count": fixture_count,
            "processed_count": 0,
            "requires_review_count": 0,
            "total_duration_ms": None,
        },
    }


def _write_report_atomic(output_path: Path, report: dict[str, Any], *, pretty: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f"{output_path.name}.tmp")
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


if __name__ == "__main__":
    main()
