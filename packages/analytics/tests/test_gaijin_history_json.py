from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gaijin_market_analytics.backtesting import HistoricalTradeGranularity
from gaijin_market_analytics.datasets.gaijin_history_json import (
    OFFLINE_MANIFEST_SCHEMA_VERSION,
    OfflineHistoryValidationError,
    load_offline_history_dataset,
)


BASE = 1_609_459_200


def raw_response(
    *,
    one_hour: list[list[object]] | None = None,
    one_day: list[list[object]] | None = None,
) -> dict[str, object]:
    return {
        "response": {
            "success": True,
            "1h": one_hour if one_hour is not None else [[BASE, 123_456, 5]],
            "1d": one_day if one_day is not None else [[BASE, 123_456, 5]],
        }
    }


def write_dataset(
    tmp_path: Path,
    raw: object,
    *,
    expected_sha256: str | None = None,
    sample_overrides: dict[str, object] | None = None,
    manifest_overrides: dict[str, object] | None = None,
):
    sample_path = tmp_path / "sample.json"
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
    sample_path.write_bytes(encoded)
    sample = {
        "offline_item_id": 600001,
        "sample_key": "sample-a",
        "display_name": "Sample A",
        "file": "sample.json",
        "expected_sha256": expected_sha256 or hashlib.sha256(encoded).hexdigest(),
    }
    sample.update(sample_overrides or {})
    manifest = {
        "schema_version": OFFLINE_MANIFEST_SCHEMA_VERSION,
        "samples": [sample],
    }
    manifest.update(manifest_overrides or {})
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, load_offline_history_dataset


def assert_code(tmp_path: Path, raw: object, code: str, **kwargs) -> None:
    manifest_path, loader = write_dataset(tmp_path, raw, **kwargs)
    with pytest.raises(OfflineHistoryValidationError) as exc_info:
        loader(manifest_path)
    assert exc_info.value.code == code
    assert "[[" not in str(exc_info.value)


def test_valid_raw_response_converts_exactly_to_typed_history(tmp_path: Path) -> None:
    manifest_path, loader = write_dataset(tmp_path, raw_response())
    dataset = loader(manifest_path)
    history = dataset.histories[0]
    assert history.item_id == 600001
    assert len(history.buckets) == 2
    hourly = next(
        bucket
        for bucket in history.buckets
        if bucket.granularity is HistoricalTradeGranularity.HOUR_1
    )
    assert str(hourly.reported_vwap_price) == "12.3456"
    assert hourly.reported_trade_volume == 5
    assert hourly.bucket_start_utc.tzinfo is not None
    assert hourly.price_semantics == "bucket_volume_weighted_average_trade_price"
    assert hourly.volume_semantics == "reported_trade_volume_unknown_unit"


def test_missing_response_is_rejected(tmp_path: Path) -> None:
    assert_code(tmp_path, {}, "raw_missing_field")


def test_success_false_is_rejected(tmp_path: Path) -> None:
    raw = raw_response()
    raw["response"]["success"] = False
    assert_code(tmp_path, raw, "raw_response_success_false")


def test_unexpected_top_level_field_is_rejected(tmp_path: Path) -> None:
    raw = raw_response()
    raw["extra"] = 1
    assert_code(tmp_path, raw, "raw_unexpected_field")


def test_unexpected_response_field_is_rejected(tmp_path: Path) -> None:
    raw = raw_response()
    raw["response"]["extra"] = 1
    assert_code(tmp_path, raw, "response_unexpected_field")


def test_malformed_triple_is_rejected(tmp_path: Path) -> None:
    assert_code(
        tmp_path,
        raw_response(one_hour=[[BASE, 1]]),
        "raw_point_malformed",
    )


@pytest.mark.parametrize("value", [True, False])
def test_bool_is_not_accepted_as_integer(tmp_path: Path, value: bool) -> None:
    assert_code(
        tmp_path,
        raw_response(one_hour=[[BASE, 1, value]]),
        "raw_point_integer_required",
    )


@pytest.mark.parametrize("timestamp", [1_499_999_999, 4_102_448_400])
def test_timestamp_range_is_enforced(tmp_path: Path, timestamp: int) -> None:
    aligned = timestamp - timestamp % 3_600
    assert_code(
        tmp_path,
        raw_response(one_hour=[[aligned, 1, 1]]),
        "raw_timestamp_out_of_range",
    )


@pytest.mark.parametrize("price", [0, -1, 10_000_000_000_001])
def test_price_range_is_enforced(tmp_path: Path, price: int) -> None:
    assert_code(
        tmp_path,
        raw_response(one_hour=[[BASE, price, 1]]),
        "raw_price_out_of_range",
    )


@pytest.mark.parametrize("volume", [0, -1, 2_000_000_001])
def test_volume_range_is_enforced(tmp_path: Path, volume: int) -> None:
    assert_code(
        tmp_path,
        raw_response(one_hour=[[BASE, 1, volume]]),
        "raw_volume_out_of_range",
    )


def test_unsorted_timestamps_are_rejected(tmp_path: Path) -> None:
    assert_code(
        tmp_path,
        raw_response(one_hour=[[BASE + 3_600, 1, 1], [BASE, 1, 1]]),
        "raw_timestamp_not_sorted",
    )


def test_duplicate_timestamps_are_rejected(tmp_path: Path) -> None:
    assert_code(
        tmp_path,
        raw_response(one_hour=[[BASE, 1, 1], [BASE, 2, 2]]),
        "raw_timestamp_duplicate",
    )


def test_hourly_alignment_is_enforced(tmp_path: Path) -> None:
    assert_code(
        tmp_path,
        raw_response(one_hour=[[BASE + 1, 1, 1]]),
        "raw_timestamp_not_aligned",
    )


def test_daily_alignment_is_enforced(tmp_path: Path) -> None:
    assert_code(
        tmp_path,
        raw_response(one_day=[[BASE + 3_600, 1, 1]]),
        "raw_timestamp_not_aligned",
    )


def test_sha_mismatch_is_rejected(tmp_path: Path) -> None:
    assert_code(
        tmp_path,
        raw_response(),
        "sample_sha256_mismatch",
        expected_sha256="0" * 64,
    )


@pytest.mark.parametrize(
    ("field", "code", "value"),
    [
        ("offline_item_id", "manifest_duplicate_offline_item_id", 600001),
        ("sample_key", "manifest_duplicate_sample_key", "sample-a"),
        ("file", "manifest_duplicate_file", "sample.json"),
    ],
)
def test_manifest_uniqueness_is_enforced(
    tmp_path: Path, field: str, code: str, value: object
) -> None:
    manifest_path, _ = write_dataset(tmp_path, raw_response())
    manifest = json.loads(manifest_path.read_text())
    second = dict(manifest["samples"][0])
    second.update(
        {
            "offline_item_id": 600002,
            "sample_key": "sample-b",
            "display_name": "Sample B",
            "file": "sample-b.json",
        }
    )
    (tmp_path / "sample-b.json").write_bytes((tmp_path / "sample.json").read_bytes())
    second[field] = value
    manifest["samples"].append(second)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(OfflineHistoryValidationError) as exc_info:
        load_offline_history_dataset(manifest_path)
    assert exc_info.value.code == code


def test_manifest_path_escape_is_rejected(tmp_path: Path) -> None:
    assert_code(
        tmp_path,
        raw_response(),
        "manifest_path_escape",
        sample_overrides={"file": "../sample.json"},
    )


def test_manifest_absolute_path_is_rejected(tmp_path: Path) -> None:
    assert_code(
        tmp_path,
        raw_response(),
        "manifest_absolute_path_forbidden",
        sample_overrides={"file": str((tmp_path / "sample.json").resolve())},
    )


def test_fingerprints_are_deterministic_and_manifest_order_independent(
    tmp_path: Path,
) -> None:
    first_path, _ = write_dataset(tmp_path, raw_response())
    manifest = json.loads(first_path.read_text())
    encoded = (tmp_path / "sample.json").read_bytes()
    (tmp_path / "sample-b.json").write_bytes(encoded)
    second = {
        **manifest["samples"][0],
        "offline_item_id": 600002,
        "sample_key": "sample-b",
        "display_name": "Sample B",
        "file": "sample-b.json",
    }
    manifest["samples"].append(second)
    first_path.write_text(json.dumps(manifest), encoding="utf-8")
    forward = load_offline_history_dataset(first_path)
    manifest["samples"].reverse()
    first_path.write_text(json.dumps(manifest), encoding="utf-8")
    reversed_dataset = load_offline_history_dataset(first_path)
    assert forward.source_manifest_sha256 == reversed_dataset.source_manifest_sha256
    assert forward.typed_trade_dataset_sha256 == reversed_dataset.typed_trade_dataset_sha256


def test_missing_and_null_provenance_have_same_source_fingerprint(tmp_path: Path) -> None:
    manifest_path, _ = write_dataset(tmp_path, raw_response())
    missing = load_offline_history_dataset(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["samples"][0].update(
        {"item_key": None, "source_url": None, "captured_at": None}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    explicit_null = load_offline_history_dataset(manifest_path)
    assert missing.source_manifest_sha256 == explicit_null.source_manifest_sha256
    assert missing.typed_trade_dataset_sha256 == explicit_null.typed_trade_dataset_sha256


@pytest.mark.parametrize("field", ["item_key", "source_url", "captured_at"])
def test_provenance_changes_only_source_fingerprint(
    tmp_path: Path, field: str
) -> None:
    manifest_path, _ = write_dataset(tmp_path, raw_response())
    baseline = load_offline_history_dataset(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["samples"][0][field] = "  provenance-value  "
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    changed = load_offline_history_dataset(manifest_path)
    assert changed.source_manifest_sha256 != baseline.source_manifest_sha256
    assert changed.typed_trade_dataset_sha256 == baseline.typed_trade_dataset_sha256
