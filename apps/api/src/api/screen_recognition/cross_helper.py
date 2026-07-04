from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from api.screen_recognition.contracts import LayoutProfile
from api.screen_recognition.evaluate import _discover_files, _fixture_id
from api.screen_recognition.json_util import dump_json_file
from api.screen_recognition.layouts import get_layout_profile
from api.screen_recognition.ocr_backend import (
    OcrBackendError,
    _run_windows_helper,
    _windows_helper_command,
    windows_ocr_preprocessing_metadata,
)
from api.screen_recognition.ocr_batch import PreparedOcrRequest, prepare_windows_ocr_batch
from api.screen_recognition.preprocessing import DEFAULT_OCR_PREPROCESSING_VARIANTS, OcrPreprocessingVariant


LEGACY_EXPORT_SCHEMA_VERSION = "windows-ocr-legacy-prepared-export-v1"
PREPARED_ONLY_SCHEMA_VERSION = "windows-ocr-prepared-only-v1"
SAFE_REPORT_SCHEMA_VERSION = "screen-recognition-cross-helper/1.0.0"
PRIVATE_REPORT_SCHEMA_VERSION = "screen-recognition-cross-helper-private/1.0.0"


@dataclass(frozen=True)
class PreparedImageExport:
    request_id: str
    fixture_id: str
    prepared_source: str
    field_name: str
    pipeline_name: str
    image_path: Path
    width: int
    height: int
    mode: str
    encoded_format: str
    encoded_sha256: str
    pixel_sha256: str
    alpha: str
    dpi: tuple[float | None, float | None]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run private legacy/batch OCR cross-helper diagnostics.")
    parser.add_argument("--input", required=True, help="Ignored private fixture directory.")
    parser.add_argument("--output-dir", required=True, help="Ignored private cross-helper artifact directory.")
    parser.add_argument("--layout-profile", default="gaijin-market-desktop-v1")
    parser.add_argument("--field", default="best_ask")
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--fixture-id", action="append", default=[])
    parser.add_argument("--legacy-report", help="Optional previous legacy safe report for fixture selection.")
    parser.add_argument("--batch-report", help="Optional previous batch safe report for fixture selection.")
    parser.add_argument("--ocr-timeout-seconds", type=int, default=90)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    _validate_private_output_dir(output_dir)
    report = run_cross_helper_experiment(
        input_dir=Path(args.input),
        output_dir=output_dir,
        layout_profile_name=args.layout_profile,
        field_name=args.field,
        limit=max(1, args.limit),
        fixture_ids=tuple(args.fixture_id),
        legacy_report_path=Path(args.legacy_report) if args.legacy_report else None,
        batch_report_path=Path(args.batch_report) if args.batch_report else None,
        ocr_timeout_seconds=args.ocr_timeout_seconds,
        pretty=args.pretty,
    )
    print(
        "cross-helper diagnostics complete: "
        f"fixtures={report['summary']['fixture_count']} "
        f"prepared={report['summary']['prepared_image_count']} "
        f"conclusion={report['summary']['primary_conclusion']} "
        f"safe_report={_redacted_path(Path(report['safe_report_path']))}",
        file=sys.stderr,
        flush=True,
    )


def run_cross_helper_experiment(
    *,
    input_dir: Path,
    output_dir: Path,
    layout_profile_name: str = "gaijin-market-desktop-v1",
    field_name: str = "best_ask",
    limit: int = 4,
    fixture_ids: tuple[str, ...] = (),
    legacy_report_path: Path | None = None,
    batch_report_path: Path | None = None,
    ocr_timeout_seconds: int = 90,
    pretty: bool = False,
) -> dict[str, Any]:
    _validate_private_output_dir(output_dir)
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    legacy_dir = output_dir / "legacy-prepared"
    pillow_dir = output_dir / "pillow-prepared"
    results_dir = output_dir / "results"
    tmp_root = output_dir / "tmp"
    for directory in (legacy_dir, pillow_dir, results_dir, tmp_root):
        directory.mkdir(parents=True, exist_ok=True)

    files = _discover_files(input_dir)
    by_id = {_fixture_id(item, input_dir): item for item in files}
    legacy_report_results = _load_report_results(legacy_report_path)
    batch_report_results = _load_report_results(batch_report_path)
    selected_ids = _select_fixture_ids(
        files=files,
        input_dir=input_dir,
        explicit_fixture_ids=fixture_ids,
        limit=limit,
        legacy_report_path=legacy_report_path,
        batch_report_path=batch_report_path,
    )
    profile = get_layout_profile(layout_profile_name)
    field_profile = _single_field_profile(profile, field_name)
    safe_results: list[dict[str, Any]] = []
    private_results: list[dict[str, Any]] = []
    prepared_count = 0
    png_bmp_results: list[dict[str, Any]] = []

    try:
        for fixture_id in selected_ids:
            item = by_id[fixture_id]
            legacy_exports = export_legacy_prepared_images(
                image_path=item.path,
                layout_profile=field_profile,
                fixture_id=fixture_id,
                output_dir=legacy_dir,
                temp_root=tmp_root,
                timeout_seconds=ocr_timeout_seconds,
            )
            pillow_exports = export_pillow_prepared_images(
                image_path=item.path,
                layout_profile=field_profile,
                fixture_id=fixture_id,
                output_dir=pillow_dir,
            )
            prepared_count += len(legacy_exports) + len(pillow_exports)
            for source, exports in (("legacy", legacy_exports), ("pillow", pillow_exports)):
                if not exports:
                    continue
                legacy_payload = recognize_prepared_images(
                    exports,
                    consumer="legacy",
                    temp_root=tmp_root,
                    timeout_seconds=ocr_timeout_seconds,
                )
                batch_payload = recognize_prepared_images(
                    exports,
                    consumer="batch",
                    temp_root=tmp_root,
                    timeout_seconds=ocr_timeout_seconds,
                )
                source_safe, source_private = _compare_consumer_payloads(
                    fixture_id=fixture_id,
                    field_name=field_name,
                    prepared_source=source,
                    exports=exports,
                    legacy_payload=legacy_payload,
                    batch_payload=batch_payload,
                )
                safe_results.extend(source_safe)
                private_results.extend(source_private)
                png_bmp_results.extend(
                    run_png_bmp_controls(
                        fixture_id=fixture_id,
                        exports=exports[:1],
                        output_dir=results_dir,
                        temp_root=tmp_root,
                        timeout_seconds=ocr_timeout_seconds,
                    )
                )
    finally:
        if tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)

    reference_outcomes = _reference_outcomes(
        selected_ids=selected_ids,
        legacy_results=legacy_report_results,
        batch_results=batch_report_results,
    )
    conclusion = _classify_primary_conclusion(safe_results, reference_outcomes)
    safe_report = {
        "schema_version": SAFE_REPORT_SCHEMA_VERSION,
        "fixture_count": len(selected_ids),
        "field_name": field_name,
        "layout_profile": layout_profile_name,
        "summary": {
            "fixture_count": len(selected_ids),
            "prepared_image_count": prepared_count,
            "comparison_count": len(safe_results),
            "same_hash_confirmed": all(item["same_physical_file_hash"] for item in safe_results),
            "legacy_source_consumer_same": _all_source_consumer_same(safe_results, "legacy"),
            "pillow_source_consumer_same": _all_source_consumer_same(safe_results, "pillow"),
            "legacy_vs_pillow_same": _legacy_vs_pillow_same(safe_results),
            "png_bmp_control_count": len(png_bmp_results),
            "png_bmp_status_same": all(item["ocr_status_same"] for item in png_bmp_results),
            "primary_conclusion": conclusion,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
        "results": safe_results,
        "png_bmp_controls": [_safe_png_bmp_result(item) for item in png_bmp_results],
        "reference_outcomes": reference_outcomes,
        "mapping_audit": audit_batch_response_mapping([], []),
    }
    private_report = {
        "schema_version": PRIVATE_REPORT_SCHEMA_VERSION,
        "safe_report_schema_version": SAFE_REPORT_SCHEMA_VERSION,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "selected_fixture_ids": selected_ids,
        "results": private_results,
        "png_bmp_controls": png_bmp_results,
    }
    safe_path = results_dir / "cross-helper.report.json"
    private_path = results_dir / "cross-helper.report.private.json"
    html_path = results_dir / "cross-helper.diagnostics.html"
    _write_json(safe_path, safe_report, pretty=pretty)
    _write_json(private_path, private_report, pretty=pretty)
    _write_html(html_path, safe_report)
    safe_report["safe_report_path"] = str(safe_path)
    safe_report["private_report_path"] = str(private_path)
    safe_report["diagnostics_html_path"] = str(html_path)
    _write_json(safe_path, safe_report, pretty=pretty)
    return safe_report


def export_legacy_prepared_images(
    *,
    image_path: Path,
    layout_profile: LayoutProfile,
    fixture_id: str,
    output_dir: Path,
    temp_root: Path,
    timeout_seconds: int,
) -> tuple[PreparedImageExport, ...]:
    script_path = Path(__file__).with_name("windows_ocr.ps1")
    with tempfile.TemporaryDirectory(prefix="cross-helper-legacy-export-", dir=temp_root) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        input_path = temp_dir / "input.json"
        output_path = temp_dir / "output.json"
        dump_json_file(
            input_path,
            {
                "schema_version": LEGACY_EXPORT_SCHEMA_VERSION,
                "image_path": str(image_path),
                "request_identity": fixture_id,
                "output_dir": str(output_dir),
                "rois": {name: roi.to_json() for name, roi in sorted(layout_profile.rois.items())},
                "preprocessing": windows_ocr_preprocessing_metadata(),
            },
        )
        completed = _run_windows_helper(
            _windows_helper_command(script_path, input_path, output_path),
            timeout_seconds=timeout_seconds,
        )
        if completed.returncode != 0:
            raise OcrBackendError((completed.stderr or "legacy prepared export failed").strip()[:240])
        payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
    return tuple(
        _prepared_export_from_path(
            fixture_id=fixture_id,
            prepared_source="legacy",
            request_id=str(item["request_id"]),
            field_name=str(item["field_name"]),
            pipeline_name=str(item["pipeline_name"]),
            image_path=Path(str(item["image_path"])),
        )
        for item in payload.get("exports") or []
    )


def export_pillow_prepared_images(
    *,
    image_path: Path,
    layout_profile: LayoutProfile,
    fixture_id: str,
    output_dir: Path,
    variants: Iterable[OcrPreprocessingVariant] = DEFAULT_OCR_PREPROCESSING_VARIANTS,
) -> tuple[PreparedImageExport, ...]:
    temp_dir = output_dir / f"{_safe_segment(fixture_id)}.tmp"
    batch = prepare_windows_ocr_batch(
        image_path=image_path,
        layout_profile=layout_profile,
        temp_dir=temp_dir,
        variants=variants,
    )
    exports: list[PreparedImageExport] = []
    try:
        seen: set[Path] = set()
        for request in batch.logical_requests:
            if request.image_path in seen:
                continue
            seen.add(request.image_path)
            sha = _file_sha256(request.image_path)
            target = (
                output_dir
                / f"{_safe_segment(fixture_id)}.{_safe_segment(request.field_name)}."
                f"{_safe_segment(request.pipeline_name)}.{sha[:16]}.png"
            )
            if not target.exists():
                shutil.copyfile(request.image_path, target)
            exports.append(
                _prepared_export_from_path(
                    fixture_id=fixture_id,
                    prepared_source="pillow",
                    request_id=f"{fixture_id}:{request.field_name}:{request.pipeline_name}",
                    field_name=request.field_name,
                    pipeline_name=request.pipeline_name,
                    image_path=target,
                )
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return tuple(exports)


def recognize_prepared_images(
    exports: tuple[PreparedImageExport, ...] | list[PreparedImageExport],
    *,
    consumer: str,
    temp_root: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    script_path = Path(__file__).with_name("windows_ocr.ps1")
    with tempfile.TemporaryDirectory(prefix=f"cross-helper-{consumer}-", dir=temp_root) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        input_path = temp_dir / "input.json"
        output_path = temp_dir / "output.json"
        dump_json_file(
            input_path,
            {
                "schema_version": PREPARED_ONLY_SCHEMA_VERSION,
                "consumer_mode": consumer,
                "requests": [
                    {
                        "request_id": export.request_id,
                        "image_path": str(export.image_path),
                        "sha256": export.encoded_sha256,
                    }
                    for export in exports
                ],
            },
        )
        completed = _run_windows_helper(
            _windows_helper_command(script_path, input_path, output_path),
            timeout_seconds=timeout_seconds,
        )
        if completed.returncode != 0:
            raise OcrBackendError((completed.stderr or f"{consumer} prepared OCR failed").strip()[:240])
        return json.loads(output_path.read_text(encoding="utf-8-sig"))


def run_png_bmp_controls(
    *,
    fixture_id: str,
    exports: tuple[PreparedImageExport, ...] | list[PreparedImageExport],
    output_dir: Path,
    temp_root: Path,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    for export in exports:
        with Image.open(export.image_path) as opened:
            image = opened.copy()
        png_path = output_dir / f"{_safe_segment(export.request_id)}.control.png"
        bmp_path = output_dir / f"{_safe_segment(export.request_id)}.control.bmp"
        image.save(png_path, format="PNG")
        image.save(bmp_path, format="BMP")
        png_export = _prepared_export_from_path(
            fixture_id=fixture_id,
            prepared_source=f"{export.prepared_source}-png-control",
            request_id=f"{export.request_id}:png",
            field_name=export.field_name,
            pipeline_name=export.pipeline_name,
            image_path=png_path,
        )
        bmp_export = _prepared_export_from_path(
            fixture_id=fixture_id,
            prepared_source=f"{export.prepared_source}-bmp-control",
            request_id=f"{export.request_id}:bmp",
            field_name=export.field_name,
            pipeline_name=export.pipeline_name,
            image_path=bmp_path,
        )
        payload = recognize_prepared_images(
            (png_export, bmp_export),
            consumer="batch",
            temp_root=temp_root,
            timeout_seconds=timeout_seconds,
        )
        by_id = _payload_results_by_id(payload)
        png_summary = _ocr_summary(by_id.get(png_export.request_id, {}))
        bmp_summary = _ocr_summary(by_id.get(bmp_export.request_id, {}))
        controls.append(
            {
                "fixture_id": fixture_id,
                "prepared_source": export.prepared_source,
                "field_name": export.field_name,
                "pipeline_name": export.pipeline_name,
                "pixel_hash_same": png_export.pixel_sha256 == bmp_export.pixel_sha256,
                "png": _private_image_record(png_export),
                "bmp": _private_image_record(bmp_export),
                "ocr_status_same": png_summary["status"] == bmp_summary["status"],
                "ocr_structure_same": _safe_structure(png_summary) == _safe_structure(bmp_summary),
                "png_ocr": png_summary,
                "bmp_ocr": bmp_summary,
            }
        )
    return controls


def audit_batch_response_mapping(
    logical_requests: Iterable[PreparedOcrRequest] | Iterable[dict[str, Any]],
    response_results: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    request_ids: list[str] = []
    logical_request_ids: list[str] = []
    physical_request_mode = False
    for request in logical_requests:
        physical_request_id = getattr(request, "physical_request_id", None)
        logical_request_id = getattr(request, "request_id", None)
        if physical_request_id is not None:
            physical_request_mode = True
            request_ids.append(str(physical_request_id))
            logical_request_ids.append(str(logical_request_id))
            continue
        if isinstance(request, dict):
            request_ids.append(str(request.get("request_id")))
            continue
    response_ids = [str(result.get("request_id")) for result in response_results if isinstance(result, dict)]
    expected_request_ids = list(dict.fromkeys(request_ids)) if physical_request_mode else request_ids
    request_counts = Counter(logical_request_ids if physical_request_mode else request_ids)
    response_counts = Counter(response_ids)
    duplicate_request_ids = sorted(request_id for request_id, count in request_counts.items() if count > 1)
    duplicate_response_ids = sorted(request_id for request_id, count in response_counts.items() if count > 1)
    missing_response_ids = sorted(set(expected_request_ids) - set(response_ids))
    unknown_response_ids = sorted(set(response_ids) - set(expected_request_ids))
    return {
        "mapping_key": "request_id",
        "request_count": len(expected_request_ids),
        "logical_request_count": len(request_ids),
        "response_count": len(response_ids),
        "duplicate_request_ids": duplicate_request_ids,
        "duplicate_response_ids": duplicate_response_ids,
        "missing_response_ids": missing_response_ids,
        "unknown_response_ids": unknown_response_ids,
        "valid": not (
            duplicate_request_ids
            or duplicate_response_ids
            or missing_response_ids
            or unknown_response_ids
        ),
    }


def _compare_consumer_payloads(
    *,
    fixture_id: str,
    field_name: str,
    prepared_source: str,
    exports: tuple[PreparedImageExport, ...],
    legacy_payload: dict[str, Any],
    batch_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    legacy_by_id = _payload_results_by_id(legacy_payload)
    batch_by_id = _payload_results_by_id(batch_payload)
    safe: list[dict[str, Any]] = []
    private: list[dict[str, Any]] = []
    for export in exports:
        legacy_result = legacy_by_id.get(export.request_id, {})
        batch_result = batch_by_id.get(export.request_id, {})
        legacy_summary = _ocr_summary(legacy_result)
        batch_summary = _ocr_summary(batch_result)
        status_same = legacy_summary["status"] == batch_summary["status"]
        structure_same = _safe_structure(legacy_summary) == _safe_structure(batch_summary)
        text_same = str(legacy_result.get("raw_text") or "") == str(batch_result.get("raw_text") or "")
        safe.append(
            {
                "fixture_id": fixture_id,
                "region": field_name,
                "pipeline": export.pipeline_name,
                "prepared_source": prepared_source,
                "same_physical_file_hash": export.encoded_sha256 == _file_sha256(export.image_path),
                "input": _safe_image_record(export),
                "legacy_consumer": _safe_structure(legacy_summary),
                "batch_consumer": _safe_structure(batch_summary),
                "ocr_status_same": status_same,
                "ocr_structure_same": structure_same,
                "ocr_text_same_private": text_same,
                "consumer_config_same": _consumer_config_same(legacy_payload, batch_payload),
            }
        )
        private.append(
            {
                "fixture_id": fixture_id,
                "region": field_name,
                "pipeline": export.pipeline_name,
                "prepared_source": prepared_source,
                "input": _private_image_record(export),
                "legacy_consumer": legacy_summary,
                "batch_consumer": batch_summary,
                "legacy_raw": legacy_result,
                "batch_raw": batch_result,
                "ocr_status_same": status_same,
                "ocr_structure_same": structure_same,
                "ocr_text_same": text_same,
            }
        )
    return safe, private


def _prepared_export_from_path(
    *,
    fixture_id: str,
    prepared_source: str,
    request_id: str,
    field_name: str,
    pipeline_name: str,
    image_path: Path,
) -> PreparedImageExport:
    with Image.open(image_path) as image:
        mode = image.mode
        encoded_format = str(image.format or image_path.suffix.lstrip(".")).upper()
        width, height = image.size
        dpi = image.info.get("dpi") or (None, None)
        alpha = _alpha_summary(image)
        pixel_hash = _pixel_sha256(image)
    return PreparedImageExport(
        request_id=request_id,
        fixture_id=fixture_id,
        prepared_source=prepared_source,
        field_name=field_name,
        pipeline_name=pipeline_name,
        image_path=image_path,
        width=width,
        height=height,
        mode=mode,
        encoded_format=encoded_format,
        encoded_sha256=_file_sha256(image_path),
        pixel_sha256=pixel_hash,
        alpha=alpha,
        dpi=(None if dpi[0] is None else float(dpi[0]), None if dpi[1] is None else float(dpi[1])),
    )


def _ocr_summary(result: dict[str, Any]) -> dict[str, Any]:
    raw_text = str(result.get("raw_text") or "")
    lines = result.get("lines") if isinstance(result.get("lines"), list) else []
    categories = Counter(_char_category(char) for char in raw_text)
    return {
        "status": "error" if result.get("error_code") else ("empty" if not raw_text.strip() else "non_empty"),
        "error_code": result.get("error_code"),
        "empty": not bool(raw_text.strip()),
        "token_count": len(raw_text.split()),
        "line_count": len(lines),
        "character_categories": dict(sorted(categories.items())),
        "line_word_counts": [
            len(line.get("words") or []) for line in lines if isinstance(line, dict)
        ],
        "timing": result.get("timing") if isinstance(result.get("timing"), dict) else {},
        "decoder": result.get("decoder") if isinstance(result.get("decoder"), dict) else {},
        "raw_text": raw_text,
    }


def _safe_structure(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": summary["status"],
        "error_code": summary["error_code"],
        "empty": summary["empty"],
        "token_count": summary["token_count"],
        "line_count": summary["line_count"],
        "character_categories": summary["character_categories"],
        "line_word_counts": summary["line_word_counts"],
        "decoder": summary["decoder"],
        "duration_ms": int((summary.get("timing") or {}).get("total_ms") or 0),
    }


def _safe_image_record(export: PreparedImageExport) -> dict[str, Any]:
    return {
        "width": export.width,
        "height": export.height,
        "mode": export.mode,
        "format": export.encoded_format,
        "encoded_sha256": export.encoded_sha256,
        "pixel_sha256": export.pixel_sha256,
        "alpha": export.alpha,
        "dpi": list(export.dpi),
    }


def _private_image_record(export: PreparedImageExport) -> dict[str, Any]:
    return {
        **_safe_image_record(export),
        "path": str(export.image_path),
        "request_id": export.request_id,
    }


def _safe_png_bmp_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "fixture_id": item["fixture_id"],
        "prepared_source": item["prepared_source"],
        "field_name": item["field_name"],
        "pipeline_name": item["pipeline_name"],
        "pixel_hash_same": item["pixel_hash_same"],
        "png": _without_path(item["png"]),
        "bmp": _without_path(item["bmp"]),
        "ocr_status_same": item["ocr_status_same"],
        "ocr_structure_same": item["ocr_structure_same"],
    }


def _without_path(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in {"path", "request_id"}}


def _payload_results_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("request_id")): item
        for item in payload.get("results") or []
        if isinstance(item, dict)
    }


def _single_field_profile(profile: LayoutProfile, field_name: str) -> LayoutProfile:
    if field_name not in profile.rois:
        raise ValueError(f"Unknown OCR field: {field_name}")
    return LayoutProfile(
        name=profile.name,
        version=profile.version,
        min_width=profile.min_width,
        min_height=profile.min_height,
        min_aspect_ratio=profile.min_aspect_ratio,
        max_aspect_ratio=profile.max_aspect_ratio,
        rois={field_name: profile.rois[field_name]},
    )


def _select_fixture_ids(
    *,
    files: list[Any],
    input_dir: Path,
    explicit_fixture_ids: tuple[str, ...],
    limit: int,
    legacy_report_path: Path | None,
    batch_report_path: Path | None,
) -> list[str]:
    known_ids = [_fixture_id(item, input_dir) for item in files]
    if explicit_fixture_ids:
        selected = [fixture_id for fixture_id in explicit_fixture_ids if fixture_id in set(known_ids)]
        return selected[:limit]
    legacy_results = _load_report_results(legacy_report_path)
    batch_results = _load_report_results(batch_report_path)
    selected: list[str] = []

    def add_matching(predicate) -> None:
        for item in files:
            fixture_id = _fixture_id(item, input_dir)
            if fixture_id in selected:
                continue
            legacy = legacy_results.get(fixture_id, {})
            batch = batch_results.get(fixture_id, {})
            if predicate(item, legacy, batch):
                selected.append(fixture_id)
                return

    add_matching(lambda _item, legacy, batch: _ask_exact(legacy) and not _ask_exact(batch))
    add_matching(lambda item, _legacy, _batch: str(item.browser or "").lower() == "chrome")
    add_matching(lambda item, _legacy, _batch: str(item.browser or "").lower() == "edge")
    add_matching(lambda _item, legacy, batch: not _ask_exact(legacy) and not _ask_exact(batch))
    add_matching(lambda _item, legacy, batch: _ask_exact(legacy) == _ask_exact(batch))
    for fixture_id in known_ids:
        if len(selected) >= limit:
            break
        if fixture_id not in selected:
            selected.append(fixture_id)
    return selected[:limit]


def _load_report_results(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(item.get("fixture_id")): item
        for item in payload.get("results") or []
        if isinstance(item, dict) and item.get("fixture_id")
    }


def _ask_exact(result: dict[str, Any]) -> bool:
    accuracy = result.get("accuracy") if isinstance(result.get("accuracy"), dict) else {}
    return bool(accuracy.get("best_ask_exact_match"))


def _classify_primary_conclusion(
    results: list[dict[str, Any]],
    reference_outcomes: list[dict[str, Any]],
) -> str:
    if not results:
        return "U"
    if any(not item["ocr_text_same_private"] for item in results):
        return "H"
    if any(item.get("legacy_batch_final_status_differs") for item in reference_outcomes):
        return "M"
    if not _legacy_vs_pillow_same(results):
        return "P"
    return "U"


def _reference_outcomes(
    *,
    selected_ids: list[str],
    legacy_results: dict[str, dict[str, Any]],
    batch_results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for fixture_id in selected_ids:
        legacy = legacy_results.get(fixture_id, {})
        batch = batch_results.get(fixture_id, {})
        legacy_accuracy = legacy.get("accuracy") if isinstance(legacy.get("accuracy"), dict) else {}
        batch_accuracy = batch.get("accuracy") if isinstance(batch.get("accuracy"), dict) else {}
        legacy_ask = bool(legacy_accuracy.get("best_ask_exact_match"))
        batch_ask = bool(batch_accuracy.get("best_ask_exact_match"))
        legacy_bid = bool(legacy_accuracy.get("best_bid_exact_match"))
        batch_bid = bool(batch_accuracy.get("best_bid_exact_match"))
        outcomes.append(
            {
                "fixture_id": fixture_id,
                "legacy_best_ask_exact": legacy_ask,
                "batch_best_ask_exact": batch_ask,
                "legacy_best_bid_exact": legacy_bid,
                "batch_best_bid_exact": batch_bid,
                "legacy_error_code": legacy.get("error_code"),
                "batch_error_code": batch.get("error_code"),
                "legacy_preprocessing_pipeline": legacy.get("preprocessing_pipeline"),
                "batch_preprocessing_pipeline": batch.get("preprocessing_pipeline"),
                "legacy_batch_final_status_differs": (
                    legacy_ask != batch_ask
                    or legacy_bid != batch_bid
                    or legacy.get("error_code") != batch.get("error_code")
                ),
            }
        )
    return outcomes


def _all_source_consumer_same(results: list[dict[str, Any]], source: str) -> bool:
    relevant = [item for item in results if item["prepared_source"] == source]
    return bool(relevant) and all(item["ocr_text_same_private"] for item in relevant)


def _legacy_vs_pillow_same(results: list[dict[str, Any]]) -> bool:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in results:
        key = (str(item["fixture_id"]), str(item["pipeline"]))
        if item["prepared_source"] == "legacy":
            by_key[key] = item
    compared = 0
    for item in results:
        if item["prepared_source"] != "pillow":
            continue
        legacy = by_key.get((str(item["fixture_id"]), str(item["pipeline"])))
        if legacy is None:
            continue
        compared += 1
        if legacy["legacy_consumer"] != item["legacy_consumer"]:
            return False
    return compared > 0


def _consumer_config_same(legacy_payload: dict[str, Any], batch_payload: dict[str, Any]) -> bool:
    legacy_diag = legacy_payload.get("diagnostics") if isinstance(legacy_payload.get("diagnostics"), dict) else {}
    batch_diag = batch_payload.get("diagnostics") if isinstance(batch_payload.get("diagnostics"), dict) else {}
    return legacy_diag.get("ocr_language_source") == batch_diag.get("ocr_language_source")


def _write_html(path: Path, safe_report: dict[str, Any]) -> None:
    rows = []
    for item in safe_report["results"]:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item['fixture_id']))}</td>"
            f"<td>{html.escape(str(item['prepared_source']))}</td>"
            f"<td>{html.escape(str(item['pipeline']))}</td>"
            f"<td>{html.escape(str(item['ocr_status_same']))}</td>"
            f"<td>{html.escape(str(item['ocr_structure_same']))}</td>"
            "</tr>"
        )
    document = (
        "<!doctype html><meta charset=\"utf-8\"><title>Cross-helper diagnostics</title>"
        "<h1>Cross-helper diagnostics</h1>"
        f"<p>Primary conclusion: {html.escape(str(safe_report['summary']['primary_conclusion']))}</p>"
        "<table><thead><tr><th>Fixture</th><th>Prepared</th><th>Pipeline</th>"
        "<th>Status same</th><th>Structure same</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    path.write_text(document, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any], *, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True),
        encoding="utf-8",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pixel_sha256(image: Image.Image) -> str:
    normalized = image.convert("RGBA")
    return hashlib.sha256(
        f"{normalized.mode}:{normalized.width}x{normalized.height}:".encode("utf-8")
        + normalized.tobytes()
    ).hexdigest()


def _alpha_summary(image: Image.Image) -> str:
    if image.mode in {"RGBA", "LA"}:
        alpha = image.getchannel("A")
        extrema = alpha.getextrema()
        if extrema == (255, 255):
            return "opaque"
        return "alpha"
    if image.mode == "P" and "transparency" in image.info:
        return "palette-transparency"
    return "none"


def _char_category(char: str) -> str:
    if char.isdigit():
        return "digit"
    if char.isalpha():
        return "letter"
    if char.isspace():
        return "space"
    if char in ".,:;+-/\\()[]{}":
        return "punctuation"
    return "other"


def _safe_segment(text: str) -> str:
    clean = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in text)
    clean = clean.strip("-")
    return (clean or "unknown")[:64]


def _validate_private_output_dir(path: Path) -> None:
    parts = {part.lower() for part in path.parts}
    if "artifacts" not in parts or "private" not in parts:
        raise ValueError("cross-helper output must be under ignored artifacts/private")


def _redacted_path(path: Path) -> str:
    parts = path.parts
    if "artifacts" in parts:
        index = parts.index("artifacts")
        return str(Path(*parts[index:]))
    return path.name


if __name__ == "__main__":
    main()
