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
from api.screen_recognition.ocr_batch import (
    BATCH_SCHEMA_VERSION,
    PreparedOcrRequest,
    prepare_system_drawing_ocr_batch_manifest,
    prepare_windows_ocr_batch,
)
from api.screen_recognition.ocr_candidates import normalize_numeric_ocr_token
from api.screen_recognition.preprocessing import preprocessing_metadata
from api.screen_recognition.price_cells import (
    PRICE_CELL_PROFILE_VERSION,
    PRICE_CELL_PROFILE_VERSION_V3,
    PRICE_CELL_PROFILE_VERSION_V4,
    PriceCellDetectionError,
    detect_price_cell_rois,
    detect_price_cell_rois_v3,
    detect_price_cell_rois_v4,
)


SYSTEM_DRAWING_CASCADE_V1_FIELD_VARIANTS: dict[str, tuple[str, ...]] = {
    "best_bid": ("gray_3x", "binary_4x"),
    "bid_levels": ("gray_3x", "binary_4x"),
    "best_ask": ("gray_3x", "binary_4x"),
    "ask_levels": ("gray_3x", "binary_4x"),
}


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
    system_drawing_backend_version = "windows-media-ocr-system-drawing-batch-v1"
    system_drawing_lockbits_backend_version = "windows-media-ocr-system-drawing-lockbits-v1"
    system_drawing_pixel_loop_backend_version = "windows-media-ocr-system-drawing-pixel-loop-v1"
    system_drawing_cascade_backend_version = "windows-media-ocr-system-drawing-cascade-v1"
    price_cells_backend_version = "windows-media-ocr-price-cells-v2"
    price_cells_v3_backend_version = "windows-media-ocr-price-cells-v3"
    price_cells_v4_backend_version = "windows-media-ocr-price-cells-v4"
    test_scope = "end_to_end"

    def __init__(
        self,
        *,
        timeout_seconds: int = 60,
        legacy_mode: bool = False,
        system_drawing_batch_mode: bool = False,
        system_drawing_pixel_implementation: str = "lockbits-v1",
        system_drawing_field_variant_plan: dict[str, tuple[str, ...]] | None = None,
        price_cell_mode: bool = False,
        price_cell_profile_version: str = PRICE_CELL_PROFILE_VERSION,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._legacy_mode = legacy_mode
        self._system_drawing_batch_mode = system_drawing_batch_mode
        self._system_drawing_pixel_implementation = system_drawing_pixel_implementation
        self._system_drawing_field_variant_plan = system_drawing_field_variant_plan
        self._price_cell_mode = price_cell_mode
        self._price_cell_profile_version = price_cell_profile_version
        if system_drawing_batch_mode:
            if price_cell_mode:
                if price_cell_profile_version == PRICE_CELL_PROFILE_VERSION_V4:
                    self.backend_version = self.price_cells_v4_backend_version
                elif price_cell_profile_version == PRICE_CELL_PROFILE_VERSION_V3:
                    self.backend_version = self.price_cells_v3_backend_version
                else:
                    self.backend_version = self.price_cells_backend_version
            elif system_drawing_field_variant_plan is not None:
                self.backend_version = self.system_drawing_cascade_backend_version
            elif system_drawing_pixel_implementation == "legacy-pixel-loop":
                self.backend_version = self.system_drawing_pixel_loop_backend_version
            else:
                self.backend_version = self.system_drawing_lockbits_backend_version

    def recognize(self, invocation: OcrInvocation) -> OcrResult:
        if os.name != "nt":
            raise OcrBackendNotConfiguredError("windows-ocr requires Windows.")
        script_path = Path(__file__).with_name("windows_ocr.ps1")
        if not script_path.is_file():
            raise OcrBackendNotConfiguredError("windows-ocr helper script is missing.")
        if self._system_drawing_batch_mode:
            return self._recognize_system_drawing_batch(invocation, script_path)
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
                backend_version=self.backend_version,
            )
            if invocation.debug_artifacts_dir is not None:
                _copy_debug_prepared_images(prepared.logical_requests, invocation.debug_artifacts_dir)
            return result

    def _recognize_system_drawing_batch(self, invocation: OcrInvocation, script_path: Path) -> OcrResult:
        with tempfile.TemporaryDirectory(prefix="cut20-ocr-system-drawing-batch-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            pixel_rois: dict[str, tuple[int, int, int, int]] | None = None
            preparation_warnings: tuple[str, ...] = ()
            additional_diagnostics: dict[str, Any] | None = None
            preprocessing_mode = "system_drawing_batch_v1"
            if self._price_cell_mode:
                preprocessing_mode = self._price_cell_profile_version
                if self._price_cell_profile_version == PRICE_CELL_PROFILE_VERSION_V4:
                    detector = detect_price_cell_rois_v4
                elif self._price_cell_profile_version == PRICE_CELL_PROFILE_VERSION_V3:
                    detector = detect_price_cell_rois_v3
                else:
                    detector = detect_price_cell_rois
                try:
                    detection = detector(invocation.image_path)
                except PriceCellDetectionError as exc:
                    preparation_warnings = (
                        "price_cell_anchor_fallback",
                        exc.code,
                    )
                    additional_diagnostics = {
                        "price_cell_detection": {
                            "profile_version": self._price_cell_profile_version,
                            "fallback_used": True,
                            "error_code": exc.code,
                        }
                    }
                else:
                    pixel_rois = {
                        field_name: roi.as_tuple()
                        for field_name, roi in detection.rois.items()
                    }
                    preparation_warnings = detection.warnings
                    additional_diagnostics = {
                        "price_cell_detection": detection.diagnostics
                    }
            prepared = prepare_system_drawing_ocr_batch_manifest(
                image_path=invocation.image_path,
                layout_profile=invocation.layout_profile,
                field_variant_names=self._system_drawing_field_variant_plan,
                pixel_rois=pixel_rois,
                preprocessing_mode=preprocessing_mode,
                additional_diagnostics=additional_diagnostics,
                preparation_warnings=preparation_warnings,
            )
            input_payload = {
                **prepared.manifest,
                "pixel_implementation": self._system_drawing_pixel_implementation,
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
                reason = stderr[-1] if stderr else "windows-ocr system-drawing batch failed"
                raise OcrBackendError(reason[:240])
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise OcrBackendError("windows-ocr system-drawing batch did not produce valid JSON output.") from exc
            return _batch_payload_to_ocr_result(
                payload,
                prepared.logical_requests,
                prepared.diagnostics,
                helper_duration_ms=helper_duration_ms,
                backend_version=self.backend_version,
            )

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
    if name in {
        "windows-ocr-system-drawing-batch",
        "windows-ocr-system-drawing-lockbits",
        "candidate-system-drawing-batch-v1",
        "candidate-system-drawing-lockbits-v1",
    }:
        return WindowsOcrRecognizer(
            timeout_seconds=timeout_seconds,
            system_drawing_batch_mode=True,
            system_drawing_pixel_implementation="lockbits-v1",
        )
    if name in {"windows-ocr-system-drawing-pixel-loop", "candidate-system-drawing-pixel-loop-v1"}:
        return WindowsOcrRecognizer(
            timeout_seconds=timeout_seconds,
            system_drawing_batch_mode=True,
            system_drawing_pixel_implementation="legacy-pixel-loop",
        )
    if name == "candidate-system-drawing-cascade-v1":
        return WindowsOcrRecognizer(
            timeout_seconds=timeout_seconds,
            system_drawing_batch_mode=True,
            system_drawing_pixel_implementation="lockbits-v1",
            system_drawing_field_variant_plan=SYSTEM_DRAWING_CASCADE_V1_FIELD_VARIANTS,
        )
    if name in {"candidate-price-cells-v1", "candidate-price-cells-v2"}:
        return WindowsOcrRecognizer(
            timeout_seconds=timeout_seconds,
            system_drawing_batch_mode=True,
            system_drawing_pixel_implementation="lockbits-v1",
            system_drawing_field_variant_plan=SYSTEM_DRAWING_CASCADE_V1_FIELD_VARIANTS,
            price_cell_mode=True,
            price_cell_profile_version=PRICE_CELL_PROFILE_VERSION,
        )
    if name == "candidate-price-cells-v3":
        return WindowsOcrRecognizer(
            timeout_seconds=timeout_seconds,
            system_drawing_batch_mode=True,
            system_drawing_pixel_implementation="lockbits-v1",
            system_drawing_field_variant_plan=SYSTEM_DRAWING_CASCADE_V1_FIELD_VARIANTS,
            price_cell_mode=True,
            price_cell_profile_version=PRICE_CELL_PROFILE_VERSION_V3,
        )
    if name == "candidate-price-cells-v4":
        return WindowsOcrRecognizer(
            timeout_seconds=timeout_seconds,
            system_drawing_batch_mode=True,
            system_drawing_pixel_implementation="lockbits-v1",
            system_drawing_field_variant_plan=SYSTEM_DRAWING_CASCADE_V1_FIELD_VARIANTS,
            price_cell_mode=True,
            price_cell_profile_version=PRICE_CELL_PROFILE_VERSION_V4,
        )
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
    backend_version: str = WindowsOcrRecognizer.backend_version,
) -> OcrResult:
    result_by_request_id, mapping_warnings, mapping_diagnostics = _batch_results_by_request_id(
        payload.get("results") or [],
        logical_requests,
    )
    fields: dict[str, OcrFieldEvidence] = {}
    warnings = set(payload.get("warnings") or [])
    warnings.update(mapping_warnings)
    warnings.update(str(code) for code in (python_diagnostics.get("preparation_warnings") or []))
    per_pipeline: list[dict[str, Any]] = []
    private_pipeline_attempts: list[dict[str, Any]] = []
    fields_diagnostics: dict[str, dict[str, Any]] = {
        str(name): dict(value)
        for name, value in (python_diagnostics.get("fields") or {}).items()
        if isinstance(value, dict)
    }
    grouped: dict[str, list[tuple[PreparedOcrRequest, dict[str, Any]]]] = {}
    for request in logical_requests:
        result = result_by_request_id.get(request.physical_request_id, {})
        request_warnings = _batch_response_metadata_warnings(request, result)
        warnings.update(request_warnings)
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
            preprocessing_timing = (
                result.get("preprocessing_timing")
                if isinstance(result.get("preprocessing_timing"), dict)
                else {}
            )
            error_code = result.get("error_code")
            response_request_id = result.get("request_id")
            lines_payload = result.get("lines") or []
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
                    "draw_resize_ms": _safe_int(preprocessing_timing.get("draw_resize_ms")) or 0,
                    "pixel_read_ms": _safe_int(preprocessing_timing.get("pixel_read_ms")) or 0,
                    "histogram_ms": _safe_int(preprocessing_timing.get("histogram_ms")) or 0,
                    "grayscale_ms": _safe_int(preprocessing_timing.get("grayscale_ms")) or 0,
                    "autocontrast_ms": _safe_int(preprocessing_timing.get("autocontrast_ms")) or 0,
                    "threshold_ms": _safe_int(preprocessing_timing.get("threshold_ms")) or 0,
                    "invert_ms": _safe_int(preprocessing_timing.get("invert_ms")) or 0,
                    "pixel_write_ms": _safe_int(preprocessing_timing.get("pixel_write_ms")) or 0,
                    "pixel_transform_ms": _safe_int(preprocessing_timing.get("pixel_transform_ms")) or 0,
                    "encode_ms": _safe_int(preprocessing_timing.get("encode_ms")) or 0,
                    "pixel_implementation": preprocessing_timing.get("pixel_implementation"),
                    "produced_text": bool(text.strip()),
                    "selected": False,
                    "request_id": request.request_id,
                    "physical_request_id": request.physical_request_id,
                    "response_request_id": None if response_request_id is None else str(response_request_id),
                    "response_received": request.physical_request_id in result_by_request_id,
                    "prepared_image_fingerprint": request.fingerprint,
                    "preprocessing_descriptor_hash": request.preprocessing_descriptor_hash,
                    "deduplicated_preprocessing": request.deduplicated_preprocessing,
                    "error_code": error_code,
                }
            )
            private_pipeline_attempts.append(
                {
                    "field_name": field_name,
                    "pipeline_name": request.pipeline_name,
                    "request_id": request.request_id,
                    "physical_request_id": request.physical_request_id,
                    "response_request_id": None if response_request_id is None else str(response_request_id),
                    "response_received": request.physical_request_id in result_by_request_id,
                    "raw_text": text,
                    "line_texts": [
                        str(line.get("text") or "")
                        for line in lines_payload
                        if isinstance(line, dict)
                    ],
                    "selected": False,
                    "error_code": error_code,
                }
            )
        if best_request is None or best_result is None:
            continue
        for item in per_pipeline:
            if item["request_id"] == best_request.request_id:
                item["selected"] = True
        for item in private_pipeline_attempts:
            if item["request_id"] == best_request.request_id:
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
        "private_pipeline_attempts": private_pipeline_attempts,
        "batch_response_mapping": mapping_diagnostics,
        "early_exit_used": False,
    }
    return OcrResult(
        backend_name=WindowsOcrRecognizer.backend_name,
        backend_version=backend_version,
        fields=fields,
        warnings=tuple(sorted(code for code in warnings if code != "ocr_confidence_unavailable")),
        diagnostics=merged_diagnostics,
    )


def _batch_results_by_request_id(
    response_results: Any,
    logical_requests: tuple[PreparedOcrRequest, ...],
) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, Any]]:
    expected_ids = tuple(dict.fromkeys(request.physical_request_id for request in logical_requests))
    expected_set = set(expected_ids)
    logical_request_ids = [request.request_id for request in logical_requests]
    duplicate_logical_request_ids = sorted(
        request_id
        for request_id in set(logical_request_ids)
        if logical_request_ids.count(request_id) > 1
    )
    results: list[dict[str, Any]] = [
        item for item in response_results if isinstance(item, dict)
    ] if isinstance(response_results, list) else []
    result_by_request_id: dict[str, dict[str, Any]] = {}
    response_counts: dict[str, int] = {}
    unknown_response_ids: list[str] = []
    for item in results:
        raw_request_id = item.get("request_id")
        if raw_request_id is None:
            unknown_response_ids.append("<missing>")
            continue
        request_id = str(raw_request_id)
        response_counts[request_id] = response_counts.get(request_id, 0) + 1
        if request_id not in expected_set:
            unknown_response_ids.append(request_id)
            continue
        if request_id not in result_by_request_id:
            result_by_request_id[request_id] = item

    duplicate_response_ids = sorted(
        request_id for request_id, count in response_counts.items() if count > 1
    )
    missing_response_ids = sorted(
        request_id for request_id in expected_ids if request_id not in result_by_request_id
    )
    warnings: set[str] = set()
    if duplicate_logical_request_ids:
        warnings.add("duplicate_request_id")
    if duplicate_response_ids:
        warnings.add("duplicate_response")
    if missing_response_ids:
        warnings.add("missing_response")
    if unknown_response_ids:
        warnings.add("unknown_response")
    diagnostics = {
        "mapping_key": "request_id",
        "logical_request_count": len(logical_requests),
        "physical_request_count": len(expected_ids),
        "response_count": len(results),
        "duplicate_request_ids": duplicate_logical_request_ids,
        "duplicate_response_ids": duplicate_response_ids,
        "missing_response_ids": missing_response_ids,
        "unknown_response_ids": sorted(set(unknown_response_ids)),
        "valid": not warnings,
    }
    return result_by_request_id, warnings, diagnostics


def _batch_response_metadata_warnings(
    request: PreparedOcrRequest,
    result: dict[str, Any],
) -> set[str]:
    warnings: set[str] = set()
    field_name = result.get("field_name")
    if field_name is not None and str(field_name) != request.field_name:
        warnings.add("response_field_mismatch")
    region_name = result.get("region")
    if region_name is not None and str(region_name) != request.field_name:
        warnings.add("response_region_mismatch")
    pipeline_name = result.get("pipeline_name")
    if pipeline_name is not None and str(pipeline_name) != request.pipeline_name:
        warnings.add("response_pipeline_mismatch")
    return warnings


def _score_recognized_text(field_name: str, text: str) -> int:
    clean = text.strip() if text else ""
    if not clean:
        return 0
    score = 10
    lower_name = field_name.lower()
    if any(token in lower_name for token in ("bid", "ask", "price", "levels")):
        import re

        normalized, _corrections, _contains_decimal = normalize_numeric_ocr_token(clean)
        if re.search(r"\d+\.\d{1,2}(?:\+)?", normalized):
            score += 40
        elif re.search(r"\d+", normalized):
            score += 20
        if len(re.findall(r"\d+", normalized)) > 1 and "." not in normalized:
            score -= 10
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
