from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from gaijin_market_analytics.datasets.gaijin_history_json import (
    OFFLINE_MANIFEST_SCHEMA_VERSION,
    load_offline_history_dataset,
)
from gaijin_market_analytics.datasets.history_inventory import (
    build_history_inventory,
    render_history_inventory_markdown,
)
from gaijin_market_analytics.offline_history_cli import main


DAY = 1_609_459_200


def dataset(
    tmp_path: Path,
    *,
    hourly: list[list[int]],
    daily: list[list[int]],
):
    raw = {"response": {"success": True, "1h": hourly, "1d": daily}}
    encoded = json.dumps(raw, separators=(",", ":")).encode()
    (tmp_path / "sample.json").write_bytes(encoded)
    manifest = {
        "schema_version": OFFLINE_MANIFEST_SCHEMA_VERSION,
        "samples": [
            {
                "offline_item_id": 600001,
                "sample_key": "sample-a",
                "display_name": "Sample A",
                "file": "sample.json",
                "expected_sha256": hashlib.sha256(encoded).hexdigest(),
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return load_offline_history_dataset(path), path


def test_overlap_compares_sparse_hourly_days_and_respects_vwap_tolerance(
    tmp_path: Path,
) -> None:
    value, _ = dataset(
        tmp_path,
        hourly=[
            [DAY, 100_000, 1],
            [DAY + 7_200, 100_004, 1],
        ],
        daily=[[DAY, 100_000, 2]],
    )
    inventory = build_history_inventory(value)
    overlap = inventory["overlap_consistency"][0]
    assert overlap["comparable_overlap_days"] == 1
    assert overlap["mismatch_days"] == 0
    assert overlap["maximum_vwap_difference_price_raw_units"] == "2"


def test_overlap_excludes_only_leading_partial_day(tmp_path: Path) -> None:
    value, _ = dataset(
        tmp_path,
        hourly=[
            [DAY + 3_600, 100_000, 1],
            [DAY + 86_400, 200_000, 1],
            [DAY + 86_400 + 7_200, 200_000, 1],
        ],
        daily=[
            [DAY, 100_000, 1],
            [DAY + 86_400, 200_000, 2],
        ],
    )
    overlap = build_history_inventory(value)["overlap_consistency"][0]
    assert overlap["leading_partial_day_excluded"] is True
    assert overlap["comparable_overlap_days"] == 1
    assert overlap["mismatch_days"] == 0


def test_vwap_difference_above_two_is_mismatch(tmp_path: Path) -> None:
    value, _ = dataset(
        tmp_path,
        hourly=[[DAY, 100_000, 1]],
        daily=[[DAY, 100_003, 1]],
    )
    overlap = build_history_inventory(value)["overlap_consistency"][0]
    assert overlap["vwap_mismatch_days"] == 1
    assert overlap["mismatch_days"] == 1


def test_hourly_priority_and_daily_fallback_are_non_overlapping(tmp_path: Path) -> None:
    value, _ = dataset(
        tmp_path,
        hourly=[[DAY, 100_000, 1], [DAY + 2 * 86_400, 100_000, 1]],
        daily=[[DAY, 100_000, 1], [DAY + 86_400, 100_000, 1]],
    )
    fallback = build_history_inventory(value)["fallback_coverage"][0]
    assert fallback == {
        "offline_item_id": 600001,
        "both_granularities_date_count": 1,
        "daily_only_date_count": 1,
        "hourly_only_date_count": 1,
        "one_hour_priority_date_count": 2,
        "one_day_fallback_date_count": 1,
    }


def test_inventory_quantiles_use_documented_decimal_interpolation(tmp_path: Path) -> None:
    value, _ = dataset(
        tmp_path,
        hourly=[],
        daily=[
            [DAY, 100_000, 1],
            [DAY + 86_400, 200_000, 2],
            [DAY + 2 * 86_400, 300_000, 4],
            [DAY + 3 * 86_400, 400_000, 8],
        ],
    )
    stats = next(
        value
        for value in build_history_inventory(value)["granularity_statistics"]
        if value["granularity"] == "1d"
    )
    assert stats["reported_volume_quantiles"]["p25"] == "1.75"
    assert stats["price_quantiles"]["median"] == "25"


def test_consecutive_day_change_above_twenty_percent_is_shock(tmp_path: Path) -> None:
    value, _ = dataset(
        tmp_path,
        hourly=[],
        daily=[[DAY, 100_000, 1], [DAY + 86_400, 125_000, 1]],
    )
    features = build_history_inventory(value)["heuristic_market_features"][0]
    assert features["consecutive_daily_pair_count"] == 1
    assert features["consecutive_daily_price_shock_candidate_count"] == 1
    assert features["maximum_consecutive_daily_price_relative_change"] == "0.25"


def test_two_day_gap_is_not_consecutive_daily_shock(tmp_path: Path) -> None:
    value, _ = dataset(
        tmp_path,
        hourly=[],
        daily=[[DAY, 100_000, 1], [DAY + 2 * 86_400, 150_000, 1]],
    )
    features = build_history_inventory(value)["heuristic_market_features"][0]
    assert features["consecutive_daily_pair_count"] == 0
    assert features["nonconsecutive_observed_pair_count"] == 1
    assert features["consecutive_daily_price_shock_candidate_count"] == 0
    assert features["daily_price_volatility_median_absolute_relative_change"] is None
    assert features["maximum_consecutive_daily_price_relative_change"] is None
    assert features["maximum_adjacent_observed_bucket_relative_change"] == "0.5"


def test_months_apart_change_is_excluded_from_daily_volatility(tmp_path: Path) -> None:
    value, _ = dataset(
        tmp_path,
        hourly=[],
        daily=[[DAY, 100_000, 1], [DAY + 90 * 86_400, 500_000, 1]],
    )
    features = build_history_inventory(value)["heuristic_market_features"][0]
    assert features["daily_price_volatility_median_absolute_relative_change"] is None
    assert features["consecutive_daily_price_shock_candidate_count"] == 0
    assert features["maximum_observed_daily_bucket_gap_seconds"] == 90 * 86_400


def test_daily_density_uses_inclusive_calendar_span(tmp_path: Path) -> None:
    value, _ = dataset(
        tmp_path,
        hourly=[],
        daily=[[DAY, 100_000, 1], [DAY + 2 * 86_400, 100_000, 1]],
    )
    features = build_history_inventory(value)["heuristic_market_features"][0]
    assert features["daily_calendar_span_days"] == 3
    assert features["observed_daily_bucket_count"] == 2
    assert features["observed_daily_bucket_density"] == "0.6666666666666666666666666667"


def test_report_contains_no_raw_history_sequence(tmp_path: Path) -> None:
    value, _ = dataset(
        tmp_path,
        hourly=[[DAY, 123_456, 7]],
        daily=[[DAY, 123_456, 7]],
    )
    report = render_history_inventory_markdown(build_history_inventory(value))
    assert str(DAY) not in report
    assert "123456" not in report
    assert "[[" not in report
    assert "reported_trade_volume_unknown_unit" in report


def test_cli_writes_detailed_files_without_database_or_raw_series(tmp_path: Path) -> None:
    _, manifest_path = dataset(
        tmp_path,
        hourly=[[DAY, 123_456, 7]],
        daily=[[DAY, 123_456, 7]],
    )
    output = tmp_path / "output"
    assert main(
        [
            "--manifest",
            str(manifest_path),
            "--output-dir",
            str(output),
            "--pretty",
        ]
    ) == 0
    payload = json.loads((output / "round6-json-inventory.json").read_text())
    assert payload["safety_confirmation"]["database_access"] is False
    encoded = (output / "round6-json-inventory.json").read_text()
    assert "[[" not in encoded
    assert (output / "ROUND6_JSON_INVENTORY.md").is_file()
