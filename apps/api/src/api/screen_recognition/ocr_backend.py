from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from api.screen_recognition.contracts import (
    LayoutProfile,
    OcrBoundingBox,
    OcrFieldEvidence,
    OcrLineEvidence,
    OcrResult,
    OcrWordEvidence,
)
from api.screen_recognition.json_util import dump_json_file
from api.screen_recognition.ocr_batch import BATCH_SCHEMA_VERSION, PreparedOcrRequest, prepare_windows_ocr_batch
from api.screen_recognition.preprocessing import preprocessing_metadata


class OcrBackendError(RuntimeError):
    pass


class OcrBackendNotConfiguredError(OcrBackendError):
    pass


class OcrBackendTimeoutError(OcrBackendError):
    pass


@dataclass(frozen=True)
class OcrInvocation:
    image_path: Path
    layout_profile: LayoutProfile
    debug_artifacts_dir: Path | None


class ScreenshotRecognizer(ABC):
    backend_name: str
    backend_version: str
    test_scope: str

    @abstractmethod
    def recognize(self, invocation: OcrInvocation) -> OcrResult:
        raise NotImplementedError


class NotConfiguredRecognizer(ScreenshotRecognizer):
    backend_name = "not-configured"
    backend_version = "0.0.0"
    test_scope = "end_to_end"

    def recognize(self, invocation: OcrInvocation) -> OcrResult:
        raise OcrBackendNotConfiguredError("No image OCR backend is configured.")


class SidecarRecognizer(ScreenshotRecognizer):
    backend_name = "sidecar"
    backend_version = "1.0.0"
    test_scope = "parser_only"

    def recognize(self, invocation: OcrInvocation) -> OcrResult:
        sidecar = invocation.image_path.with_suffix(".ocr.txt")
        if not sidecar.is_file():
            raise OcrBackendError("Sidecar OCR text file is missing.")
        fields = _parse_sidecar_text(sidecar.read_text(encoding="utf-8-sig"))
        return OcrResult(
            backend_name=self.backend_name,
            backend_version=self.backend_version,
            fields={
                name: OcrFieldEvidence(
                    field_name=name,
                    raw_text=text,
                    confidence=None,
                    confidence_source="unavailable",
                )
                for name, text in fields.items()
            },
            warnings=(),
        )


class WindowsOcrRecognizer(ScreenshotRecognizer):
    backend_name = "windows-ocr"
    backend_version = "windows-media-ocr-batch-v1"
    test_scope = "end_to_end"

    def __init__(self, *, timeout_seconds: int = 60, legacy_mode: bool = False) -> None:
        self._timeout_seconds = timeout_seconds
        self._legacy_mode = legacy_mode

    def recognize(self, invocation: OcrInvocation) -> OcrResult:
        if os.name != "nt":
            raise OcrBackendNotConfiguredError("windows-ocr requires Windows.")
        script_path = Path(__file__).with_name("windows_ocr.ps1")
        if not script_path.is_file():
            raise OcrBackendNotConfiguredError("windows-ocr helper script is missing.")
        if not self._legacy_mode:
            return self._recognize_batch(invocation, script_path)
        return self._recognize_legacy(invocation, script_path)

    def _recognize_batch(self, invocation: OcrInvocation, script_path: Path) -> OcrResult:
        with tempfile.TemporaryDirectory(prefix="cut20-ocr-batch-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            prepared = prepare_windows_ocr_batch(
                image_path=invocation.image_path,
                layout_profile=invocation.layout_profile,
                temp_dir=temp_dir / "prepared",
            )
            input_payload = {
                **prepared.manifest,
                "debug_artifacts_dir": (
                    None
                    if invocation.debug_artifacts_dir is None
                    else str(invocation.debug_artifacts_dir)
                ),
            }
            input_path = temp_dir / "input.json"
            output_path = temp_dir / "output.json"
            dump_json_file(input_path, input_payload)
            command = _windows_helper_command(script_path, input_path, output_path)
            helper_started = time.perf_counter()
            completed = _run_windows_helper(command, timeout_seconds=self._timeout_seconds)
            helper_duration_ms = int((time.perf_counter() - helper_started) * 1000)
            if completed.returncode != 0:
                stderr = (completed.stderr or "").strip().splitlines()
                reason = stderr[-1] if stderr else "windows-ocr failed"
                raise OcrBackendError(reason[:240])
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise OcrBackendError("windows-ocr did not produce valid JSON output.") from exc
            result = _batch_payload_to_ocr_result(
                payload,
                prepared.logical_requests,
                prepared.diagnostics,
                helper_duration_ms=helper_duration_ms,
            )
            if invocation.debug_artifacts_dir is not None:
                _copy_debug_prepared_images(prepared.logical_requests, invocation.debug_artifacts_dir)
            return result

    def _recognize_legacy(self, invocation: OcrInvocation, script_path: Path) -> OcrResult:
        input_payload = {
            "image_path": str(invocation.image_path),
            "rois": {
                name: roi.to_json()
                for name, roi in sorted(invocation.layout_profile.rois.items())
            },
            "debug_artifacts_dir": (
                None
                if invocation.debug_artifacts_dir is None
                else str(invocation.debug_artifacts_dir)
            ),
            "preprocessing": windows_ocr_preprocessing_metadata(),
        }
        with tempfile.TemporaryDirectory(prefix="cut20-ocr-") as temp_dir:
            input_path = Path(temp_dir) / "input.json"
            output_path = Path(temp_dir) / "output.json"
            dump_json_file(input_path, input_payload)
            command = _windows_helper_command(script_path, input_path, output_path)
            helper_started = time.perf_counter()
            completed = _run_windows_helper(command, timeout_seconds=self._timeout_seconds)
            helper_duration_ms = int((time.perf_counter() - helper_started) * 1000)
            if completed.returncode != 0:
                stderr = (completed.stderr or "").strip().splitlines()
                reason = stderr[-1] if stderr else "windows-ocr failed"
                raise OcrBackendError(reason[:240])
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise OcrBackendError("windows-ocr did not produce valid JSON output.") from exc
        fields: dict[str, OcrFieldEvidence] = {}
        warnings = set(payload.get("warnings") or [])
        for name, value in (payload.get("fields") or {}).items():
            confidence = value.get("confidence")
            parsed_confidence = None if confidence is None else Decimal(str(confidence))
            field_warnings = tuple(value.get("warnings") or ())
            fields[name] = OcrFieldEvidence(
                field_name=name,
                raw_text=value.get("raw_text") or "",
                confidence=parsed_confidence,
                confidence_source=value.get("confidence_source") or "unavailable",
                bounding_box=_parse_box(value.get("bounding_box")),
                lines=tuple(_parse_lines(value.get("lines") or [])),
                warnings=field_warnings,
            )
        diagnostics = payload.get("diagnostics") or {}
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        helper_total_ms = _safe_int(diagnostics.get("helper_total_duration_ms"))
        process_startup_ms = (
            max(0, helper_duration_ms - helper_total_ms)
            if helper_total_ms is not None
            else None
        )
        diagnostics = {
            **diagnostics,
            "preprocessing_mode": "powershell_legacy",
            "powershell_process_count": 1,
            "python_observed_helper_duration_ms": helper_duration_ms,
            "powershell_process_startup_overhead_ms": process_startup_ms,
        }
        return OcrResult(
            backend_name=self.backend_name,
            backend_version=self.backend_version,
            fields=fields,
            warnings=tuple(sorted(code for code in warnings if code != "ocr_confidence_unavailable")),
            diagnostics=diagnostics,
        )


def get_recognizer(name: str, *, timeout_seconds: int = 60) -> ScreenshotRecognizer:
    if name == "windows-ocr":
        return WindowsOcrRecognizer(timeout_seconds=timeout_seconds)
    if name == "windows-ocr-legacy":
        return WindowsOcrRecognizer(timeout_seconds=timeout_seconds, legacy_mode=True)
    if name == "sidecar":
        return SidecarRecognizer()
    if name in {"not-configured", "none"}:
        return NotConfiguredRecognizer()
    raise OcrBackendNotConfiguredError(f"Unknown OCR backend: {name}")


def _windows_helper_command(script_path: Path, input_path: Path, output_path: Path) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-InputJson",
        str(input_path),
        "-OutputJson",
        str(output_path),
    ]


def _run_windows_helper(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        raise OcrBackendTimeoutError(f"windows-ocr timed out after {timeout_seconds} seconds.") from exc
    except KeyboardInterrupt:
        _terminate_process_tree(process)
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return
    process.kill()


def windows_ocr_preprocessing_metadata() -> dict[str, Any]:
    metadata = preprocessing_metadata()
    metadata["selection_rule"] = "field_format_then_non_empty_without_confidence"
    metadata["execution_boundary"] = BATCH_SCHEMA_VERSION
    return metadata


def _batch_payload_to_ocr_result(
    payload: dict[str, Any],
    logical_requests: tuple[PreparedOcrRequest, ...],
    python_diagnostics: dict[str, Any],
    *,
    helper_duration_ms: int,
) -> OcrResult:
    result_by_request_id = {
        str(item.get("request_id")): item
        for item in payload.get("results") or []
        if isinstance(item, dict)
    }
    fields: dict[str, OcrFieldEvidence] = {}
    warnings = set(payload.get("warnings") or [])
    per_pipeline: list[dict[str, Any]] = []
    fields_diagnostics: dict[str, dict[str, Any]] = {
        str(name): dict(value)
        for name, value in (python_diagnostics.get("fields") or {}).items()
        if isinstance(value, dict)
    }
    grouped: dict[str, list[tuple[PreparedOcrRequest, dict[str, Any]]]] = {}
    for request in logical_requests:
        result = result_by_request_id.get(request.physical_request_id, {})
        grouped.setdefault(request.field_name, []).append((request, result))
    for field_name, field_info in fields_diagnostics.items():
        if field_info.get("blank_roi_fast_path"):
            fields[field_name] = OcrFieldEvidence(
                field_name=field_name,
                raw_text="",
                confidence=None,
                confidence_source="unavailable",
                bounding_box=OcrBoundingBox(
                    x=Decimal("0"),
                    y=Decimal("0"),
                    width=Decimal(str(field_info.get("width") or 0)),
                    height=Decimal(str(field_info.get("height") or 0)),
                ),
                lines=(),
                warnings=("ocr_confidence_unavailable", "preprocessing_pipeline:blank_roi_fast_path"),
            )
    for field_name, request_results in grouped.items():
        best_request: PreparedOcrRequest | None = None
        best_result: dict[str, Any] | None = None
        best_score = -9999
        field_started = time.perf_counter()
        for request, result in request_results:
            text = str(result.get("raw_text") or "")
            score = _score_recognized_text(field_name, text)
            if score > best_score:
                best_score = score
                best_request = request
                best_result = result
            timing = result.get("timing") if isinstance(result.get("timing"), dict) else {}
            error_code = result.get("error_code")
            per_pipeline.append(
                {
                    "field_name": field_name,
                    "pipeline_name": request.pipeline_name,
                    "duration_ms": _safe_int(timing.get("total_ms")) or 0,
                    "ocr_total_ms": _safe_int(timing.get("total_ms")) or 0,
                    "engine_initialization_ms": 0,
                    "ocr_execution_ms": _safe_int(timing.get("recognize_ms")) or 0,
                    "image_open_ms": _safe_int(timing.get("image_open_ms")) or 0,
                    "bitmap_decode_ms": _safe_int(timing.get("bitmap_decode_ms")) or 0,
                    "serialization_ms": _safe_int(timing.get("serialization_ms")) or 0,
                    "dispose_ms": _safe_int(timing.get("dispose_ms")) or 0,
                    "produced_text": bool(text.strip()),
                    "selected": False,
                    "request_id": request.request_id,
                    "physical_request_id": request.physical_request_id,
                    "prepared_image_fingerprint": request.fingerprint,
                    "deduplicated_preprocessing": request.deduplicated_preprocessing,
                    "error_code": error_code,
                }
            )
        if best_request is None or best_result is None:
            continue
        for item in per_pipeline:
            if item["field_name"] == field_name and item["pipeline_name"] == best_request.pipeline_name:
                item["selected"] = True
        field_elapsed_ms = int((time.perf_counter() - field_started) * 1000)
        field_diag = fields_diagnostics.setdefault(field_name, {})
        field_diag["pipeline_count_completed"] = len(request_results)
        field_diag["selected_pipeline"] = best_request.pipeline_name
        field_diag["duration_ms"] = field_elapsed_ms
        fields[field_name] = OcrFieldEvidence(
            field_name=field_name,
            raw_text=str(best_result.get("raw_text") or ""),
            confidence=None,
            confidence_source="unavailable",
            bounding_box=OcrBoundingBox(
                x=Decimal("0"),
                y=Decimal("0"),
                width=Decimal(str(best_request.width)),
                height=Decimal(str(best_request.height)),
            ),
            lines=tuple(_parse_lines(best_result.get("lines") or [])),
            warnings=("ocr_confidence_unavailable", f"preprocessing_pipeline:{best_request.pipeline_name}"),
        )
    diagnostics = payload.get("diagnostics") or {}
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    helper_total_ms = _safe_int(diagnostics.get("helper_total_duration_ms"))
    process_startup_ms = (
        max(0, helper_duration_ms - helper_total_ms)
        if helper_total_ms is not None
        else None
    )
    merged_diagnostics = {
        **python_diagnostics,
        **diagnostics,
        "fields": fields_diagnostics,
        "powershell_process_count": 1,
        "python_observed_helper_duration_ms": helper_duration_ms,
        "powershell_process_startup_overhead_ms": process_startup_ms,
        "pipeline_count_attempted": int(python_diagnostics.get("logical_pipeline_request_count") or 0),
        "pipeline_count_completed": len(per_pipeline),
        "per_pipeline_duration_ms": per_pipeline,
        "early_exit_used": False,
    }
    return OcrResult(
        backend_name=WindowsOcrRecognizer.backend_name,
        backend_version=WindowsOcrRecognizer.backend_version,
        fields=fields,
        warnings=tuple(sorted(code for code in warnings if code != "ocr_confidence_unavailable")),
        diagnostics=merged_diagnostics,
    )


def _score_recognized_text(field_name: str, text: str) -> int:
    clean = text.strip() if text else ""
    if not clean:
        return 0
    score = 10
    lower_name = field_name.lower()
    if any(token in lower_name for token in ("bid", "ask", "price", "levels")):
        import re

        if re.search(r"\d+[\.,]\d{1,2}", clean):
            score += 40
        elif re.search(r"\d+", clean):
            score += 20
        if re.search(r"[A-Za-z\u4e00-\u9fff]", clean):
            score -= 5
    elif "quantity" in lower_name:
        import re

        if re.search(r"\b\d+\b", clean):
            score += 30
        if re.search(r"\d+[\.,]\d+", clean):
            score -= 15
    elif len(clean) > 2:
        score += 20
    return score


def _copy_debug_prepared_images(
    requests: tuple[PreparedOcrRequest, ...],
    debug_dir: Path,
) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    copied: set[Path] = set()
    for request in requests:
        if request.image_path in copied:
            continue
        copied.add(request.image_path)
        target = debug_dir / f"{request.field_name}.{request.pipeline_name}.{request.fingerprint}.png"
        target.write_bytes(request.image_path.read_bytes())


def _parse_sidecar_text(content: str) -> dict[str, str]:
    stripped = content.strip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        payload = json.loads(stripped)
        return {str(key): "" if value is None else str(value) for key, value in payload.items()}
    fields: dict[str, list[str]] = {}
    current = "full_text"
    fields[current] = []
    for line in content.splitlines():
        clean = line.strip()
        if clean.startswith("[") and clean.endswith("]") and len(clean) > 2:
            current = clean[1:-1].strip()
            fields.setdefault(current, [])
            continue
        fields.setdefault(current, []).append(line)
    return {name: "\n".join(lines).strip() for name, lines in fields.items()}


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_box(value: dict[str, Any] | None) -> OcrBoundingBox | None:
    if not value:
        return None
    return OcrBoundingBox(
        x=Decimal(str(value.get("x", "0"))),
        y=Decimal(str(value.get("y", "0"))),
        width=Decimal(str(value.get("width", "0"))),
        height=Decimal(str(value.get("height", "0"))),
    )


def _parse_lines(values: list[dict[str, Any]]) -> list[OcrLineEvidence]:
    lines: list[OcrLineEvidence] = []
    for index, value in enumerate(values):
        words = tuple(
            OcrWordEvidence(
                text=str(word.get("text") or ""),
                order=int(word.get("order", word_index)),
                bounding_box=_parse_box(word.get("bounding_box")),
            )
            for word_index, word in enumerate(value.get("words") or [])
        )
        lines.append(
            OcrLineEvidence(
                text=str(value.get("text") or ""),
                order=int(value.get("order", index)),
                bounding_box=_parse_box(value.get("bounding_box")),
                words=words,
            )
        )
    return lines
