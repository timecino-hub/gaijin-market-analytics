from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from gaijin_market_analytics.backtesting import (
    HistoricalTradeBucket,
    HistoricalTradeGranularity,
    ItemHistoricalTradeHistory,
    ItemMarketHistory,
)
from gaijin_market_analytics.calibration.evaluation import (
    load_calibration_evaluation_dataset,
)
from gaijin_market_analytics.calibration_evaluation_cli import main
from gaijin_market_analytics.contracts import MarketObservation
from gaijin_market_analytics.datasets.combined_calibration import (
    build_combined_calibration_dataset,
    combined_calibration_json_bytes,
)
from gaijin_market_analytics.datasets.gaijin_history_json import (
    OfflineHistoryDataset,
    OfflineHistoryManifest,
    OfflineHistorySample,
    OfflineHistorySampleData,
)
from gaijin_market_analytics.datasets.market_history_export import (
    ItemMappingEntry,
    ItemMappingManifest,
    LoadedMarketHistoryDataset,
    MarketHistoryExportProfile,
)


BASE = datetime(2026, 1, 1, 12, tzinfo=UTC)


def write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    observations = tuple(
        MarketObservation(
            observed_at=BASE + timedelta(days=index),
            best_bid=Decimal(str(12 + index)),
            best_ask=Decimal(str(13 + index)),
            bid_count=None,
            ask_count=None,
            estimated_volume=None,
            observation_key=f"review:{index}",
            observed_bid_quantity=4 + index,
            observed_ask_quantity=5 + index,
            quantity_semantics="screenshot_display_quantity",
            source_type="screen_review",
            review_status="confirmed",
        )
        for index in range(4)
    )
    market = LoadedMarketHistoryDataset(
        histories=(ItemMarketHistory(item_id=101, observations=observations),),
        provenance_by_key={
            f"review:{index}": {
                "source_review_id": str(index),
                "candidate_version": "screen_review_candidate_v1",
                "candidate_sha256": f"{index + 1:x}" * 64,
                "client_capture_id": f"00000000-0000-4000-8000-{index + 1:012d}",
                "capture_schema_version": "point_in_time_capture_v1",
                "capture_started_at": (BASE + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
                "captured_at": (BASE + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
                "final_observed_at": (BASE + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
                "observed_at_source": "browser_capture",
                "page_identity": {
                    "origin": "https://trade.gaijin.net",
                    "market_path": "/market/1067/item-one",
                    "item_key": "item-one",
                },
                "capture_sha256": f"{index + 5:x}" * 64,
                "extension_version": "1.0.0",
                "incomplete_quantities_acknowledged": False,
                "edited_fields": [],
                "quality_tier": "browser_captured",
                "imported_at": (BASE + timedelta(days=index, minutes=1)).isoformat().replace("+00:00", "Z"),
                "page_item_key_matches_database_item": True,
            }
            for index in range(4)
        },
        item_identity_by_id={101: {"external_key": "item-one"}},
        source_fingerprint="1" * 64,
        typed_history_fingerprint="2" * 64,
        export_profile=MarketHistoryExportProfile.POINT_IN_TIME_REVIEWED,
        item_mapping_fingerprint=None,
        diagnostics={},
    )
    buckets = tuple(
        HistoricalTradeBucket(
            item_id=9001,
            granularity=HistoricalTradeGranularity.HOUR_1,
            bucket_start_utc=datetime(2026, 1, 1 + index, 10, tzinfo=UTC),
            bucket_duration_seconds=3600,
            reported_vwap_price=Decimal(str(12.5 + index)),
            reported_trade_volume=2 + index,
        )
        for index in range(3)
    )
    sample = OfflineHistorySample(
        offline_item_id=9001,
        sample_key="sample-one",
        display_name="Synthetic Sample",
        file="sample.json",
        expected_sha256="a" * 64,
        item_key="item-one",
    )
    historical = OfflineHistoryDataset(
        manifest=OfflineHistoryManifest(
            schema_version="round6_offline_history_manifest_v1",
            samples=(sample,),
            manifest_path=tmp_path / "manifest.json",
            input_root=tmp_path,
        ),
        samples=(
            OfflineHistorySampleData(
                spec=sample,
                source_file=tmp_path / "sample.json",
                source_file_sha256="a" * 64,
                history=ItemHistoricalTradeHistory(item_id=9001, buckets=buckets),
            ),
        ),
        source_manifest_sha256="3" * 64,
        typed_trade_dataset_sha256="4" * 64,
    )
    mapping = ItemMappingManifest(
        schema_version="round6_item_mapping_v1",
        mappings=(ItemMappingEntry("sample-one", 101, "item-one", True),),
    )
    combined = build_combined_calibration_dataset(
        historical_dataset=historical,
        market_dataset=market,
        item_mapping=mapping,
    )
    combined_path = tmp_path / "combined.json"
    combined_path.write_bytes(combined_calibration_json_bytes(combined))
    config = {
        "schema_version": "round6_walk_forward_config_v1",
        "target_horizon_seconds": 129600,
        "minimum_train_timestamps": 2,
        "test_timestamp_count": 1,
        "step_timestamp_count": 2,
        "embargo_seconds": 0,
        "included_quality_tiers": ["browser_captured"],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return combined_path, config_path


def args(combined: Path, config: Path, output: Path, *extra: str) -> list[str]:
    return [
        "--combined-dataset",
        str(combined),
        "--config",
        str(config),
        "--output",
        str(output),
        *extra,
    ]


def test_cli_writes_deterministic_loadable_output(tmp_path: Path, capsys) -> None:
    combined, config = write_inputs(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert main(args(combined, config, first)) == 0
    stdout = capsys.readouterr().out
    assert "calibration_evaluation_ok" in stdout
    assert str(tmp_path) not in stdout
    assert main(args(combined, config, second)) == 0
    capsys.readouterr()
    assert first.read_bytes() == second.read_bytes()
    loaded = load_calibration_evaluation_dataset(first)
    assert len(loaded.examples) == 4
    assert len(loaded.folds) == 1
    assert not list(tmp_path.glob(".*.tmp"))


def test_cli_pretty_output_preserves_fingerprints(tmp_path: Path, capsys) -> None:
    combined, config = write_inputs(tmp_path)
    compact = tmp_path / "compact.json"
    pretty = tmp_path / "pretty.json"
    assert main(args(combined, config, compact)) == 0
    capsys.readouterr()
    assert main(args(combined, config, pretty, "--pretty")) == 0
    capsys.readouterr()
    assert compact.read_bytes() != pretty.read_bytes()
    assert load_calibration_evaluation_dataset(compact).fingerprints == load_calibration_evaluation_dataset(pretty).fingerprints


def test_cli_invalid_config_does_not_leave_partial_output(tmp_path: Path, capsys) -> None:
    combined, config = write_inputs(tmp_path)
    value = json.loads(config.read_text())
    value["step_timestamp_count"] = 0
    config.write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "out.json"
    assert main(args(combined, config, output)) == 3
    captured = capsys.readouterr()
    assert "calibration_evaluation_error:calibration_config_step_timestamp_count_invalid" in captured.err
    assert not output.exists()


def test_cli_invalid_combined_dataset_is_reported(tmp_path: Path, capsys) -> None:
    combined, config = write_inputs(tmp_path)
    combined.write_text("{}", encoding="utf-8")
    output = tmp_path / "out.json"
    assert main(args(combined, config, output)) == 2
    captured = capsys.readouterr()
    assert "calibration_combined_error:" in captured.err
    assert not output.exists()
