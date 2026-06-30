from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from api.screen_recognition.contracts import GroundTruthEntry, PriceLevel, ScreenContract
from api.screen_recognition.image_io import ensure_safe_filename, is_allowed_image_filename


class GroundTruthInvalidError(ValueError):
    pass


PRICE_FIELDS = {"best_bid", "best_ask", "exact_price", "price_lower_bound"}


def load_ground_truth(path: Path) -> list[GroundTruthEntry]:
    entries: list[GroundTruthEntry] = []
    seen_sample_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GroundTruthInvalidError(f"line {line_number}: invalid JSON") from exc
        if _contains_float(payload):
            raise GroundTruthInvalidError(f"line {line_number}: price fields must not be JSON floats")
        entry = _parse_entry(payload, line_number)
        if entry.sample_id in seen_sample_ids:
            raise GroundTruthInvalidError(f"line {line_number}: duplicate sample_id")
        seen_sample_ids.add(entry.sample_id)
        entries.append(entry)
    if not entries:
        raise GroundTruthInvalidError("ground truth is empty")
    return entries


def make_ground_truth_template(images: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, image in enumerate(sorted(images, key=lambda path: path.name), start=1):
        ensure_safe_filename(image.name)
        if not is_allowed_image_filename(image.name):
            continue
        rows.append(
            {
                "sample_id": f"cut_{index:03d}",
                "filename": image.name,
                "expected_status": "passed",
                "item_key": "",
                "item_name": "",
                "best_bid": "",
                "best_ask": "",
                "total_bid_quantity": None,
                "total_ask_quantity": None,
                "bid_levels": [],
                "ask_levels": [],
            }
        )
    return rows


def _parse_entry(payload: dict[str, Any], line_number: int) -> GroundTruthEntry:
    sample_id = _required_string(payload, "sample_id", line_number)
    filename = _required_string(payload, "filename", line_number)
    try:
        ensure_safe_filename(filename)
    except ValueError as exc:
        raise GroundTruthInvalidError(f"line {line_number}: filename must be a safe basename") from exc
    if not is_allowed_image_filename(filename):
        raise GroundTruthInvalidError(f"line {line_number}: filename must be PNG/JPEG")
    expected_status = payload.get("expected_status") or "passed"
    if not isinstance(expected_status, str):
        raise GroundTruthInvalidError(f"line {line_number}: expected_status must be a string")
    item_key = _optional_non_empty_string(payload, "item_key", line_number)
    item_name = _optional_non_empty_string(payload, "item_name", line_number)
    expected = ScreenContract(
        item_key=item_key,
        item_key_source="ground_truth_manifest" if item_key else None,
        item_name=item_name,
        best_bid=_optional_decimal(payload, "best_bid", line_number),
        best_ask=_optional_decimal(payload, "best_ask", line_number),
        total_bid_quantity=_optional_int(payload, "total_bid_quantity", line_number),
        total_ask_quantity=_optional_int(payload, "total_ask_quantity", line_number),
        bid_levels=tuple(_parse_levels(payload.get("bid_levels"), line_number, "bid_levels")),
        ask_levels=tuple(_parse_levels(payload.get("ask_levels"), line_number, "ask_levels")),
    )
    return GroundTruthEntry(
        sample_id=sample_id,
        filename=filename,
        expected_status=expected_status,
        item_key=item_key,
        expected=expected,
    )


def _parse_levels(value: Any, line_number: int, field: str) -> list[PriceLevel]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise GroundTruthInvalidError(f"line {line_number}: {field} must be a list")
    levels: list[PriceLevel] = []
    for index, raw_level in enumerate(value):
        if not isinstance(raw_level, dict):
            raise GroundTruthInvalidError(f"line {line_number}: {field}[{index}] must be an object")
        exact_price = _optional_decimal(raw_level, "exact_price", line_number)
        price_lower_bound = _optional_decimal(raw_level, "price_lower_bound", line_number)
        raw_display_price = raw_level.get("raw_display_price")
        if raw_display_price is None:
            raw_display_price = (
                str(exact_price) if exact_price is not None else str(price_lower_bound or "")
            )
        if not isinstance(raw_display_price, str):
            raise GroundTruthInvalidError(
                f"line {line_number}: {field}[{index}].raw_display_price must be a string"
            )
        quantity = _optional_int(raw_level, "quantity", line_number)
        aggregation_type = raw_level.get("aggregation_type")
        if aggregation_type is not None and not isinstance(aggregation_type, str):
            raise GroundTruthInvalidError(
                f"line {line_number}: {field}[{index}].aggregation_type must be a string"
            )
        levels.append(
            PriceLevel(
                exact_price=exact_price,
                price_lower_bound=price_lower_bound,
                lower_bound_inclusive=raw_level.get("lower_bound_inclusive"),
                aggregation_type=aggregation_type,
                quantity=quantity,
                raw_display_price=raw_display_price,
                raw_quantity=None if quantity is None else str(quantity),
            )
        )
    return levels


def _contains_float(value: Any) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_float(child) for child in value)
    return False


def _required_string(payload: dict[str, Any], field: str, line_number: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GroundTruthInvalidError(f"line {line_number}: {field} is required")
    return value.strip()


def _optional_non_empty_string(payload: dict[str, Any], field: str, line_number: int) -> str | None:
    value = payload.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise GroundTruthInvalidError(f"line {line_number}: {field} must be a string")
    return value


def _optional_decimal(payload: dict[str, Any], field: str, line_number: int) -> Decimal | None:
    value = payload.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise GroundTruthInvalidError(f"line {line_number}: {field} must be a string Decimal")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise GroundTruthInvalidError(f"line {line_number}: {field} is not a valid Decimal") from exc
    if not parsed.is_finite():
        raise GroundTruthInvalidError(f"line {line_number}: {field} must be finite")
    return parsed


def _optional_int(payload: dict[str, Any], field: str, line_number: int) -> int | None:
    value = payload.get(field)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise GroundTruthInvalidError(f"line {line_number}: {field} must be an integer")
    if value < 0:
        raise GroundTruthInvalidError(f"line {line_number}: {field} must be non-negative")
    return value
