from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.screen_recognition.contracts import ImageInfo
from api.screen_recognition.image_io import ImageReadError, is_allowed_image_filename, read_image_info
from api.screen_recognition.layouts import LayoutUnsupportedError, get_layout_profile, validate_layout_match
from api.screen_recognition.ocr_backend import OcrBackendError, OcrBackendNotConfiguredError, OcrInvocation, get_recognizer
from api.screen_recognition.parser import parse_ocr_contract
from api.screen_recognition.roi import RoiValidationError, resolve_roi_pixels


PRIVATE_FIXTURE_MESSAGE = "no private evaluation fixtures found"


@dataclass(frozen=True)
class EvaluationFile:
    path: Path
    browser: str | None
    declared_zoom: str | None


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate local private screen-recognition fixtures.")
    parser.add_argument("--input", required=True, help="Ignored private fixture directory.")
    parser.add_argument("--output", required=True, help="Ignored private report JSON path.")
    parser.add_argument("--layout-profile", default="gaijin-market-desktop-v1")
    parser.add_argument("--ocr-backend", default="windows-ocr")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    report = evaluate_private_fixtures(
        input_dir=Path(args.input),
        output_path=Path(args.output),
        layout_profile_name=args.layout_profile,
        ocr_backend_name=args.ocr_backend,
        pretty=args.pretty,
    )
    print(
        f"screen recognition evaluation complete: fixtures={report['summary']['fixture_count']} "
        f"requires_review={report['summary']['requires_review_count']}",
        file=sys.stderr,
    )


def evaluate_private_fixtures(
    *,
    input_dir: Path,
    output_path: Path,
    layout_profile_name: str,
    ocr_backend_name: str,
    pretty: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    files = _discover_files(input_dir)
    report: dict[str, Any] = {
        "schema_version": "screen-recognition-private-evaluation/1.0.0",
        "input_exists": input_dir.exists(),
        "private_paths_recorded": False,
        "database_access": False,
        "network_access": False,
        "layout_profile": layout_profile_name,
        "ocr_backend": ocr_backend_name,
        "message": None,
        "results": [],
        "summary": {
            "fixture_count": len(files),
            "requires_review_count": 0,
            "total_duration_ms": None,
        },
    }
    if not files:
        report["message"] = PRIVATE_FIXTURE_MESSAGE
        report["summary"]["total_duration_ms"] = int((time.perf_counter() - started) * 1000)
        _write_report(output_path, report, pretty=pretty)
        return report

    profile = get_layout_profile(layout_profile_name)
    recognizer = get_recognizer(ocr_backend_name)
    results = []
    for item in files:
        result = _evaluate_file(item, input_dir=input_dir, profile=profile, recognizer_name=recognizer.backend_name)
        results.append(result)
        if result["requires_review"]:
            report["summary"]["requires_review_count"] += 1
    report["results"] = results
    report["summary"]["total_duration_ms"] = int((time.perf_counter() - started) * 1000)
    _write_report(output_path, report, pretty=pretty)
    return report


def _evaluate_file(
    item: EvaluationFile,
    *,
    input_dir: Path,
    profile: Any,
    recognizer_name: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    timings: dict[str, int] = {}
    warnings: list[str] = []
    errors: list[str] = []
    image_info: ImageInfo | None = None
    raw_item_name = None
    bid_values: list[str] = []
    ask_values: list[str] = []
    quantity_values: list[int] = []
    bid_detected_count = 0
    ask_detected_count = 0
    bid_valid_count = 0
    ask_valid_count = 0
    layout_status = "not_run"

    try:
        decode_started = time.perf_counter()
        image_info = read_image_info(item.path)
        timings["decode_ms"] = int((time.perf_counter() - decode_started) * 1000)
        roi_started = time.perf_counter()
        validate_layout_match(profile, image_info)
        for roi in profile.rois.values():
            resolved = resolve_roi_pixels(roi, image_info)
            warnings.extend(resolved.warnings)
        layout_status = "ok"
        timings["roi_ms"] = int((time.perf_counter() - roi_started) * 1000)
        ocr_started = time.perf_counter()
        recognizer = get_recognizer(recognizer_name)
        ocr_result = recognizer.recognize(OcrInvocation(item.path, profile, None))
        timings["ocr_ms"] = int((time.perf_counter() - ocr_started) * 1000)
        parse_started = time.perf_counter()
        contract, parse_warnings, parse_errors = parse_ocr_contract(ocr_result.fields, item_key=None)
        timings["parse_ms"] = int((time.perf_counter() - parse_started) * 1000)
        warnings.extend(ocr_result.warnings)
        warnings.extend(parse_warnings)
        errors.extend(parse_errors)
        raw_item_name = ocr_result.fields.get("item_name").raw_text if ocr_result.fields.get("item_name") else None
        bid_values = [str(level.exact_price) for level in contract.bid_levels if level.exact_price is not None]
        ask_values = [str(level.exact_price) for level in contract.ask_levels if level.exact_price is not None]
        bid_detected_count = len(contract.bid_levels)
        ask_detected_count = len(contract.ask_levels)
        bid_valid_count = len(bid_values)
        ask_valid_count = len(ask_values)
        quantity_values = [
            value
            for value in (contract.total_bid_quantity, contract.total_ask_quantity)
            if value is not None
        ]
        if contract.best_bid is not None:
            bid_values.insert(0, str(contract.best_bid))
            bid_valid_count += 1
        if contract.best_ask is not None:
            ask_values.insert(0, str(contract.best_ask))
            ask_valid_count += 1
    except ImageReadError:
        errors.append("image_unreadable")
        layout_status = "image_unreadable"
    except LayoutUnsupportedError:
        errors.append("layout_not_supported")
        layout_status = "layout_not_supported"
    except RoiValidationError as exc:
        errors.append(exc.code)
        layout_status = exc.code
    except OcrBackendNotConfiguredError:
        errors.append("image_recognizer_not_configured")
    except OcrBackendError:
        errors.append("ocr_backend_error")

    timings["total_ms"] = int((time.perf_counter() - started) * 1000)
    return {
        "filename": _safe_relative_filename(item.path, input_dir),
        "browser": item.browser,
        "declared_zoom": item.declared_zoom,
        "image_width": None if image_info is None else image_info.width,
        "image_height": None if image_info is None else image_info.height,
        "layout_profile": profile.name,
        "roi_detection_status": layout_status,
        "bid_detected_count": bid_detected_count,
        "ask_detected_count": ask_detected_count,
        "bid_valid_count": bid_valid_count,
        "ask_valid_count": ask_valid_count,
        "bid_values": bid_values,
        "ask_values": ask_values,
        "quantity_values": quantity_values,
        "item_name_raw": raw_item_name,
        "warnings": sorted(set(warnings)),
        "errors": sorted(set(errors)),
        "requires_review": bool(warnings or errors),
        "timings": timings,
    }


def _discover_files(input_dir: Path) -> list[EvaluationFile]:
    if not input_dir.is_dir():
        return []
    files: list[EvaluationFile] = []
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file() or not is_allowed_image_filename(path.name):
            continue
        relative_parts = path.relative_to(input_dir).parts
        browser = relative_parts[0] if len(relative_parts) >= 3 else None
        zoom = relative_parts[1] if len(relative_parts) >= 3 else None
        files.append(EvaluationFile(path=path, browser=browser, declared_zoom=zoom))
    return files


def _safe_relative_filename(path: Path, input_dir: Path) -> str:
    try:
        parts = path.relative_to(input_dir).parts
    except ValueError:
        parts = (path.name,)
    return "/".join(parts)


def _write_report(output_path: Path, report: dict[str, Any], *, pretty: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
