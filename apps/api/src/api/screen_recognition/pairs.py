from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from api.screen_recognition.config import PairedCutConfig
from api.screen_recognition.contracts import GroundTruthEntry, ScreenContract
from api.screen_recognition.ground_truth import GroundTruthInvalidError
from api.screen_recognition.history_contracts import HistoryExpectedContract
from api.screen_recognition.image_io import ensure_safe_filename, is_allowed_image_filename, list_image_files


PAIR_FILENAME_RE = re.compile(r"^(?P<sample_id>\d{3})(?P<history>_1)?\.(?P<ext>png|jpg|jpeg)$", re.IGNORECASE)


@dataclass(frozen=True)
class PairFileSet:
    sample_id: str
    current_filename: str | None
    history_filename: str | None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class PairedGroundTruthEntry:
    sample_id: str
    split: str
    item_key: str
    item_name: str
    current: GroundTruthEntry
    history: HistoryExpectedContract

    def to_json(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "split": self.split,
            "item_key": self.item_key,
            "item_name": self.item_name,
            "current": self.current.expected.to_json()
            | {
                "filename": self.current.filename,
                "expected_status": self.current.expected_status,
            },
            "history": self.history.to_json(),
        }


def scan_paired_images(images_dir: Path) -> tuple[list[PairFileSet], list[str]]:
    current: dict[str, str] = {}
    history: dict[str, str] = {}
    errors_by_id: dict[str, list[str]] = {}
    global_errors: list[str] = []
    for image in list_image_files(images_dir):
        match = PAIR_FILENAME_RE.match(image.name)
        if match is None:
            global_errors.append("pair_invalid_filename")
            continue
        sample_id = match.group("sample_id")
        bucket = history if match.group("history") else current
        duplicate_code = "pair_duplicate_history_image" if match.group("history") else "pair_duplicate_current_image"
        if sample_id in bucket:
            errors_by_id.setdefault(sample_id, []).append(duplicate_code)
        else:
            bucket[sample_id] = image.name
    sample_ids = sorted(set(current) | set(history) | set(errors_by_id))
    pairs: list[PairFileSet] = []
    for sample_id in sample_ids:
        errors = list(errors_by_id.get(sample_id, []))
        if sample_id not in current:
            errors.append("pair_current_image_missing")
        if sample_id not in history:
            errors.append("pair_history_image_missing")
        pairs.append(
            PairFileSet(
                sample_id=sample_id,
                current_filename=current.get(sample_id),
                history_filename=history.get(sample_id),
                errors=tuple(errors),
            )
        )
    return pairs, global_errors


def make_paired_ground_truth_template(
    images_dir: Path, *, config: PairedCutConfig
) -> tuple[list[dict[str, Any]], list[str]]:
    pairs, global_errors = scan_paired_images(images_dir)
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        rows.append(
            {
                "sample_id": pair.sample_id,
                "split": config.split_for(pair.sample_id),
                "item_key": None,
                "item_name": None,
                "current": {
                    "filename": pair.current_filename,
                    "expected_status": "ok",
                    "best_bid": None,
                    "best_ask": None,
                    "total_bid_quantity": None,
                    "total_ask_quantity": None,
                    "bid_levels": [],
                    "ask_levels": [],
                },
                "history": {
                    "filename": pair.history_filename,
                    "expected_status": "ok",
                    "time_range": None,
                    "price_series": {"display_color": None, "axis": None},
                    "volume_series": {"display_color": None, "axis": None},
                    "left_axis_range": {"min": None, "max": None},
                    "right_axis_range": {"min": None, "max": None},
                    "left_axis_labels": [],
                    "right_axis_labels": [],
                    "time_axis_labels": [],
                    "sampled_points": [],
                },
                "pair_errors": list(pair.errors),
            }
        )
    return rows, global_errors


def load_paired_ground_truth(path: Path) -> list[PairedGroundTruthEntry]:
    entries: list[PairedGroundTruthEntry] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if _contains_float(payload):
            raise GroundTruthInvalidError(f"line {line_number}: Decimal values must be strings")
        entry = _parse_paired_entry(payload, line_number)
        if entry.sample_id in seen:
            raise GroundTruthInvalidError(f"line {line_number}: duplicate sample_id")
        seen.add(entry.sample_id)
        entries.append(entry)
    if not entries:
        raise GroundTruthInvalidError("paired ground truth is empty")
    return entries


def _parse_paired_entry(payload: dict[str, Any], line_number: int) -> PairedGroundTruthEntry:
    sample_id = _required_string(payload, "sample_id", line_number)
    split = _required_string(payload, "split", line_number)
    if split not in {"calibration", "evaluation"}:
        raise GroundTruthInvalidError(f"line {line_number}: split must be explicit")
    item_key = _required_string(payload, "item_key", line_number)
    item_name = _required_string(payload, "item_name", line_number)
    current_payload = _required_object(payload, "current", line_number)
    history_payload = _required_object(payload, "history", line_number)
    current_filename = _required_string(current_payload, "filename", line_number)
    history_filename = _required_string(history_payload, "filename", line_number)
    _validate_filename(current_filename, line_number)
    _validate_filename(history_filename, line_number)
    current_contract = ScreenContract(
        item_key=item_key,
        item_key_source="ground_truth_manifest",
        item_name=item_name,
        best_bid=_required_decimal(current_payload, "best_bid", line_number),
        best_ask=_required_decimal(current_payload, "best_ask", line_number),
        total_bid_quantity=_optional_int(current_payload, "total_bid_quantity", line_number),
        total_ask_quantity=_optional_int(current_payload, "total_ask_quantity", line_number),
    )
    current = GroundTruthEntry(
        sample_id=sample_id,
        filename=current_filename,
        expected_status=str(current_payload.get("expected_status") or "passed"),
        item_key=item_key,
        expected=current_contract,
    )
    price_series = _required_object(history_payload, "price_series", line_number)
    volume_series = _required_object(history_payload, "volume_series", line_number)
    history = HistoryExpectedContract(
        filename=history_filename,
        expected_status=str(history_payload.get("expected_status") or "ok"),
        time_range=_required_string(history_payload, "time_range", line_number),
        price_series_color=_required_string(price_series, "display_color", line_number),
        price_series_axis=_required_string(price_series, "axis", line_number),
        volume_series_color=_required_string(volume_series, "display_color", line_number),
        volume_series_axis=_required_string(volume_series, "axis", line_number),
        left_axis_min=_range_decimal(history_payload, "left_axis_range", "min", line_number),
        left_axis_max=_range_decimal(history_payload, "left_axis_range", "max", line_number),
        right_axis_min=_range_decimal(history_payload, "right_axis_range", "min", line_number),
        right_axis_max=_range_decimal(history_payload, "right_axis_range", "max", line_number),
        sampled_points=tuple(history_payload.get("sampled_points") or ()),
    )
    return PairedGroundTruthEntry(
        sample_id=sample_id,
        split=split,
        item_key=item_key,
        item_name=item_name,
        current=current,
        history=history,
    )


def _contains_float(value: Any) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_float(child) for child in value)
    return False


def _validate_filename(filename: str, line_number: int) -> None:
    try:
        ensure_safe_filename(filename)
    except ValueError as exc:
        raise GroundTruthInvalidError(f"line {line_number}: unsafe filename") from exc
    if not is_allowed_image_filename(filename):
        raise GroundTruthInvalidError(f"line {line_number}: image must be PNG/JPEG")


def _required_string(payload: dict[str, Any], field: str, line_number: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GroundTruthInvalidError(f"line {line_number}: {field} is required")
    return value.strip()


def _required_object(payload: dict[str, Any], field: str, line_number: int) -> dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise GroundTruthInvalidError(f"line {line_number}: {field} must be an object")
    return value


def _required_decimal(payload: dict[str, Any], field: str, line_number: int) -> Decimal:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GroundTruthInvalidError(f"line {line_number}: {field} Decimal string is required")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise GroundTruthInvalidError(f"line {line_number}: invalid Decimal for {field}") from exc
    if not parsed.is_finite():
        raise GroundTruthInvalidError(f"line {line_number}: {field} must be finite")
    return parsed


def _range_decimal(payload: dict[str, Any], range_field: str, field: str, line_number: int) -> Decimal | None:
    range_payload = payload.get(range_field)
    if not isinstance(range_payload, dict):
        return None
    value = range_payload.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise GroundTruthInvalidError(f"line {line_number}: {range_field}.{field} must be a string")
    return Decimal(value)


def _optional_int(payload: dict[str, Any], field: str, line_number: int) -> int | None:
    value = payload.get(field)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise GroundTruthInvalidError(f"line {line_number}: {field} must be an integer")
    return value
