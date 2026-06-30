from __future__ import annotations

import json
import os
import subprocess
import tempfile
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


class OcrBackendError(RuntimeError):
    pass


class OcrBackendNotConfiguredError(OcrBackendError):
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
    backend_version = "windows-media-ocr-v1"
    test_scope = "end_to_end"

    def __init__(self, *, timeout_seconds: int = 60) -> None:
        self._timeout_seconds = timeout_seconds

    def recognize(self, invocation: OcrInvocation) -> OcrResult:
        if os.name != "nt":
            raise OcrBackendNotConfiguredError("windows-ocr requires Windows.")
        script_path = Path(__file__).with_name("windows_ocr.ps1")
        if not script_path.is_file():
            raise OcrBackendNotConfiguredError("windows-ocr helper script is missing.")
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
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                    "-InputJson",
                    str(input_path),
                    "-OutputJson",
                    str(output_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
            )
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
        return OcrResult(
            backend_name=self.backend_name,
            backend_version=self.backend_version,
            fields=fields,
            warnings=tuple(sorted(code for code in warnings if code != "ocr_confidence_unavailable")),
        )


def get_recognizer(name: str) -> ScreenshotRecognizer:
    if name == "windows-ocr":
        return WindowsOcrRecognizer()
    if name == "sidecar":
        return SidecarRecognizer()
    if name in {"not-configured", "none"}:
        return NotConfiguredRecognizer()
    raise OcrBackendNotConfiguredError(f"Unknown OCR backend: {name}")


def windows_ocr_preprocessing_metadata() -> dict[str, Any]:
    return {
        "crop_rois": True,
        "scale_factor": 3,
        "grayscale": False,
        "contrast_enhancement": False,
        "binarization": False,
        "sharpen": False,
        "source_image_modified": False,
        "runtime_model_download": False,
    }


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
