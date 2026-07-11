"""Strict, offline-only adapter for Gaijin historical response JSON.

This module intentionally has no API, database, browser, or network dependency.
It converts explicitly supplied local files directly into Analytics contracts.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path, PurePath, PureWindowsPath
from typing import NoReturn

from gaijin_market_analytics.backtesting.trade_evidence_contracts import (
    HistoricalTradeBucket,
    HistoricalTradeGranularity,
    ItemHistoricalTradeHistory,
)


OFFLINE_MANIFEST_SCHEMA_VERSION = "round6_offline_history_manifest_v1"
SOURCE_SCHEMA_VERSION = "gaijin_trade_history_v1"
PRICE_SCALE = 10_000
MAX_POINTS_PER_SERIES = 20_000
MIN_TIMESTAMP = 1_500_000_000
MAX_TIMESTAMP = 4_102_444_800
MAX_PRICE_RAW = 10_000_000_000_000
MAX_REPORTED_VOLUME = 2_000_000_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class OfflineHistoryValidationError(ValueError):
    """Stable, structured validation failure without raw input disclosure."""

    def __init__(
        self,
        code: str,
        *,
        sample_key: str | None = None,
        field: str | None = None,
        index: int | None = None,
    ) -> None:
        self.code = code
        self.sample_key = sample_key
        self.field = field
        self.index = index
        parts = [code]
        if sample_key is not None:
            parts.append(f"sample={sample_key}")
        if field is not None:
            parts.append(f"field={field}")
        if index is not None:
            parts.append(f"index={index}")
        super().__init__(";".join(parts))


@dataclass(frozen=True, slots=True)
class OfflineHistorySample:
    offline_item_id: int
    sample_key: str
    display_name: str
    file: str
    expected_sha256: str
    item_key: str | None = None
    source_url: str | None = None
    captured_at: str | None = None


@dataclass(frozen=True, slots=True)
class OfflineHistoryManifest:
    schema_version: str
    samples: tuple[OfflineHistorySample, ...]
    manifest_path: Path
    input_root: Path


@dataclass(frozen=True, slots=True)
class OfflineHistorySampleData:
    spec: OfflineHistorySample
    source_file: Path
    source_file_sha256: str
    history: ItemHistoricalTradeHistory


@dataclass(frozen=True, slots=True)
class OfflineHistoryDataset:
    manifest: OfflineHistoryManifest
    samples: tuple[OfflineHistorySampleData, ...]
    source_manifest_sha256: str
    typed_trade_dataset_sha256: str

    @property
    def histories(self) -> tuple[ItemHistoricalTradeHistory, ...]:
        return tuple(sample.history for sample in self.samples)


def load_offline_history_manifest(
    manifest_path: str | Path,
    *,
    input_root: str | Path | None = None,
) -> OfflineHistoryManifest:
    path = Path(manifest_path).resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail("manifest_file_not_found")
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("manifest_json_invalid")
    if not isinstance(value, dict):
        _fail("manifest_top_level_not_object")
    _require_exact_keys(value, {"schema_version", "samples"}, "manifest")
    if value.get("schema_version") != OFFLINE_MANIFEST_SCHEMA_VERSION:
        _fail("manifest_schema_version_unsupported", field="schema_version")
    raw_samples = value.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        _fail("manifest_samples_required", field="samples")
    root = Path(input_root).resolve() if input_root is not None else path.parent
    samples = tuple(_parse_manifest_sample(raw, index) for index, raw in enumerate(raw_samples))
    _validate_manifest_uniqueness(samples)
    for sample in samples:
        _resolve_sample_path(root, sample)
    return OfflineHistoryManifest(
        schema_version=OFFLINE_MANIFEST_SCHEMA_VERSION,
        samples=samples,
        manifest_path=path,
        input_root=root,
    )


def load_offline_history_dataset(
    manifest_path: str | Path,
    *,
    input_root: str | Path | None = None,
) -> OfflineHistoryDataset:
    manifest = load_offline_history_manifest(manifest_path, input_root=input_root)
    samples = tuple(_load_sample(manifest, sample) for sample in manifest.samples)
    ordered = tuple(sorted(samples, key=lambda value: value.spec.offline_item_id))
    return OfflineHistoryDataset(
        manifest=manifest,
        samples=ordered,
        source_manifest_sha256=source_manifest_sha256(manifest),
        typed_trade_dataset_sha256=typed_trade_dataset_sha256(
            tuple(sample.history for sample in ordered)
        ),
    )


def source_manifest_sha256(manifest: OfflineHistoryManifest) -> str:
    payload = {
        "schema_version": manifest.schema_version,
        "samples": [
            {
                "offline_item_id": sample.offline_item_id,
                "sample_key": sample.sample_key,
                "display_name": sample.display_name,
                "file": sample.file.replace("\\", "/"),
                "expected_sha256": sample.expected_sha256,
                "item_key": sample.item_key,
                "source_url": sample.source_url,
                "captured_at": sample.captured_at,
            }
            for sample in sorted(manifest.samples, key=_sample_sort_key)
        ],
    }
    return _sha256_json(payload)


def typed_trade_dataset_sha256(
    histories: tuple[ItemHistoricalTradeHistory, ...],
) -> str:
    payload = []
    for history in sorted(histories, key=lambda value: value.item_id):
        payload.append(
            {
                "item_id": history.item_id,
                "buckets": [
                    {
                        "granularity": bucket.granularity.value,
                        "bucket_start_utc": bucket.bucket_start_utc.isoformat(),
                        "bucket_duration_seconds": bucket.bucket_duration_seconds,
                        "reported_vwap_price": _decimal_text(bucket.reported_vwap_price),
                        "reported_trade_volume": bucket.reported_trade_volume,
                        "price_semantics": bucket.price_semantics,
                        "volume_semantics": bucket.volume_semantics,
                        "source_schema_version": bucket.source_schema_version,
                    }
                    for bucket in sorted(
                        history.buckets,
                        key=lambda value: (
                            value.granularity.value,
                            value.bucket_start_utc,
                        ),
                    )
                ],
            }
        )
    return _sha256_json(payload)


def _parse_manifest_sample(value: object, index: int) -> OfflineHistorySample:
    if not isinstance(value, dict):
        _fail("manifest_sample_not_object", field="samples", index=index)
    allowed = {
        "offline_item_id",
        "sample_key",
        "display_name",
        "file",
        "expected_sha256",
        "item_key",
        "source_url",
        "captured_at",
    }
    required = {
        "offline_item_id",
        "sample_key",
        "display_name",
        "file",
        "expected_sha256",
    }
    unexpected = set(value) - allowed
    missing = required - set(value)
    if unexpected:
        _fail("manifest_sample_unexpected_field", field=sorted(unexpected)[0], index=index)
    if missing:
        _fail("manifest_sample_missing_field", field=sorted(missing)[0], index=index)
    item_id = value["offline_item_id"]
    if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0:
        _fail("manifest_offline_item_id_invalid", field="offline_item_id", index=index)
    strings: dict[str, str] = {}
    for field_name in ("sample_key", "display_name", "file", "expected_sha256"):
        field_value = value[field_name]
        if not isinstance(field_value, str) or not field_value.strip():
            _fail("manifest_string_invalid", field=field_name, index=index)
        strings[field_name] = field_value.strip()
    if not _SHA256_RE.fullmatch(strings["expected_sha256"]):
        _fail("manifest_sha256_invalid", field="expected_sha256", index=index)
    optional: dict[str, str | None] = {}
    for field_name in ("item_key", "source_url", "captured_at"):
        field_value = value.get(field_name)
        if field_value is not None and (
            not isinstance(field_value, str) or not field_value.strip()
        ):
            _fail("manifest_optional_string_invalid", field=field_name, index=index)
        optional[field_name] = field_value.strip() if isinstance(field_value, str) else None
    return OfflineHistorySample(
        offline_item_id=item_id,
        sample_key=strings["sample_key"],
        display_name=strings["display_name"],
        file=strings["file"],
        expected_sha256=strings["expected_sha256"],
        item_key=optional["item_key"],
        source_url=optional["source_url"],
        captured_at=optional["captured_at"],
    )


def _validate_manifest_uniqueness(samples: tuple[OfflineHistorySample, ...]) -> None:
    for attribute, code in (
        ("offline_item_id", "manifest_duplicate_offline_item_id"),
        ("sample_key", "manifest_duplicate_sample_key"),
        ("file", "manifest_duplicate_file"),
    ):
        values = [getattr(sample, attribute) for sample in samples]
        if len(values) != len(set(values)):
            _fail(code, field=attribute)


def _resolve_sample_path(root: Path, sample: OfflineHistorySample) -> Path:
    raw = sample.file
    if Path(raw).is_absolute() or PureWindowsPath(raw).is_absolute():
        _fail("manifest_absolute_path_forbidden", sample_key=sample.sample_key, field="file")
    pure = PurePath(raw.replace("\\", "/"))
    if ".." in pure.parts:
        _fail("manifest_path_escape", sample_key=sample.sample_key, field="file")
    resolved = (root / Path(*pure.parts)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _fail("manifest_path_escape", sample_key=sample.sample_key, field="file")
    if not resolved.is_file():
        _fail("sample_file_not_found", sample_key=sample.sample_key, field="file")
    return resolved


def _load_sample(
    manifest: OfflineHistoryManifest,
    sample: OfflineHistorySample,
) -> OfflineHistorySampleData:
    path = _resolve_sample_path(manifest.input_root, sample)
    encoded = path.read_bytes()
    actual_sha256 = hashlib.sha256(encoded).hexdigest()
    if actual_sha256 != sample.expected_sha256:
        _fail("sample_sha256_mismatch", sample_key=sample.sample_key)
    try:
        raw = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("sample_json_invalid", sample_key=sample.sample_key)
    history = _parse_raw_response(raw, sample)
    return OfflineHistorySampleData(
        spec=sample,
        source_file=path,
        source_file_sha256=actual_sha256,
        history=history,
    )


def _parse_raw_response(
    value: object,
    sample: OfflineHistorySample,
) -> ItemHistoricalTradeHistory:
    if not isinstance(value, dict):
        _fail("raw_top_level_not_object", sample_key=sample.sample_key)
    _require_exact_keys(value, {"response"}, "raw", sample.sample_key)
    response = value.get("response")
    if not isinstance(response, dict):
        _fail("raw_response_missing", sample_key=sample.sample_key, field="response")
    _require_exact_keys(
        response,
        {"success", "1h", "1d"},
        "response",
        sample.sample_key,
    )
    if response.get("success") is not True:
        _fail("raw_response_success_false", sample_key=sample.sample_key, field="success")
    one_hour = _parse_series(response.get("1h"), "1h", sample)
    one_day = _parse_series(response.get("1d"), "1d", sample)
    if not one_hour and not one_day:
        _fail("raw_series_empty", sample_key=sample.sample_key)
    buckets = tuple(
        _to_bucket(sample.offline_item_id, HistoricalTradeGranularity.HOUR_1, point)
        for point in one_hour
    ) + tuple(
        _to_bucket(sample.offline_item_id, HistoricalTradeGranularity.DAY_1, point)
        for point in one_day
    )
    return ItemHistoricalTradeHistory(item_id=sample.offline_item_id, buckets=buckets)


def _parse_series(
    value: object,
    label: str,
    sample: OfflineHistorySample,
) -> tuple[tuple[int, int, int], ...]:
    if not isinstance(value, list):
        _fail("raw_series_not_array", sample_key=sample.sample_key, field=label)
    if len(value) > MAX_POINTS_PER_SERIES:
        _fail("raw_series_too_long", sample_key=sample.sample_key, field=label)
    duration = 3_600 if label == "1h" else 86_400
    points: list[tuple[int, int, int]] = []
    previous_timestamp: int | None = None
    for index, raw_point in enumerate(value):
        if not isinstance(raw_point, list) or len(raw_point) != 3:
            _fail("raw_point_malformed", sample_key=sample.sample_key, field=label, index=index)
        if any(isinstance(item, bool) or not isinstance(item, int) for item in raw_point):
            _fail("raw_point_integer_required", sample_key=sample.sample_key, field=label, index=index)
        timestamp, price_raw, reported_volume = raw_point
        if not MIN_TIMESTAMP <= timestamp <= MAX_TIMESTAMP:
            _fail("raw_timestamp_out_of_range", sample_key=sample.sample_key, field=label, index=index)
        if not 0 < price_raw <= MAX_PRICE_RAW:
            _fail("raw_price_out_of_range", sample_key=sample.sample_key, field=label, index=index)
        if not 0 < reported_volume <= MAX_REPORTED_VOLUME:
            _fail("raw_volume_out_of_range", sample_key=sample.sample_key, field=label, index=index)
        if timestamp % duration != 0:
            _fail("raw_timestamp_not_aligned", sample_key=sample.sample_key, field=label, index=index)
        if previous_timestamp is not None:
            if timestamp == previous_timestamp:
                _fail("raw_timestamp_duplicate", sample_key=sample.sample_key, field=label, index=index)
            if timestamp < previous_timestamp:
                _fail("raw_timestamp_not_sorted", sample_key=sample.sample_key, field=label, index=index)
        previous_timestamp = timestamp
        points.append((timestamp, price_raw, reported_volume))
    return tuple(points)


def _to_bucket(
    item_id: int,
    granularity: HistoricalTradeGranularity,
    point: tuple[int, int, int],
) -> HistoricalTradeBucket:
    timestamp, price_raw, reported_volume = point
    return HistoricalTradeBucket(
        item_id=item_id,
        granularity=granularity,
        bucket_start_utc=datetime.fromtimestamp(timestamp, UTC),
        bucket_duration_seconds=granularity.duration_seconds,
        reported_vwap_price=Decimal(price_raw) / Decimal(PRICE_SCALE),
        reported_trade_volume=reported_volume,
        source_schema_version=SOURCE_SCHEMA_VERSION,
    )


def _require_exact_keys(
    value: dict[object, object],
    expected: set[str],
    scope: str,
    sample_key: str | None = None,
) -> None:
    actual = set(value)
    unexpected = actual - expected
    missing = expected - actual
    if unexpected:
        _fail(
            f"{scope}_unexpected_field",
            sample_key=sample_key,
            field=str(sorted(unexpected, key=str)[0]),
        )
    if missing:
        _fail(
            f"{scope}_missing_field",
            sample_key=sample_key,
            field=sorted(missing)[0],
        )


def _sample_sort_key(sample: OfflineHistorySample) -> tuple[object, ...]:
    return (
        sample.offline_item_id,
        sample.sample_key,
        sample.display_name,
        sample.file.replace("\\", "/"),
        sample.expected_sha256,
    )


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _fail(
    code: str,
    *,
    sample_key: str | None = None,
    field: str | None = None,
    index: int | None = None,
) -> NoReturn:
    raise OfflineHistoryValidationError(
        code,
        sample_key=sample_key,
        field=field,
        index=index,
    )
