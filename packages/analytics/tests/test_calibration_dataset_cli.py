from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from gaijin_market_analytics.calibration_dataset_cli import main
from gaijin_market_analytics.datasets.combined_calibration import (
    load_combined_calibration_dataset,
)
from gaijin_market_analytics.datasets.market_history_export import (
    MarketHistoryQualityTier,
    MarketHistorySourceRecord,
    build_market_history_export,
    market_history_export_json_bytes,
)


OBSERVED_AT = datetime(2026, 1, 3, 12, tzinfo=UTC)


def write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    raw = {
        "response": {
            "success": True,
            "1h": [
                [int(datetime(2026, 1, 2, 10, tzinfo=UTC).timestamp()), 125000, 3],
                [int(datetime(2026, 1, 3, 12, tzinfo=UTC).timestamp()), 126000, 2],
            ],
            "1d": [],
        }
    }
    raw_path = tmp_path / "sample.json"
    raw_path.write_text(
        json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "round6_offline_history_manifest_v1",
        "samples": [
            {
                "offline_item_id": 9001,
                "sample_key": "sample-one",
                "display_name": "Sample One",
                "file": "sample.json",
                "expected_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                "item_key": "item-one",
                "source_url": "https://example.invalid/item-one",
                "captured_at": "2026-01-04T00:00:00Z",
            }
        ],
    }
    manifest_path = tmp_path / "history-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    record = MarketHistorySourceRecord(
        item_id=101,
        item_external_key="item-one",
        item_name="Item One",
        item_category="vehicle",
        item_rarity=None,
        item_is_active=True,
        observed_at=OBSERVED_AT,
        best_bid=Decimal("12.00"),
        best_ask=Decimal("13.00"),
        bid_count=None,
        ask_count=None,
        estimated_volume=None,
        observed_bid_quantity=4,
        observed_ask_quantity=5,
        quantity_semantics="screenshot_display_quantity",
        source_type="screen_review",
        source_version="screen_review_candidate_v1",
        review_status="confirmed",
        source_review_id="one",
        candidate_version="screen_review_candidate_v1",
        candidate_sha256="1" * 64,
        quality_tier=MarketHistoryQualityTier.BROWSER_CAPTURED,
        observed_at_source="browser_capture",
        incomplete_quantities_acknowledged=False,
        edited_fields=(),
        capture_schema_version="point_in_time_capture_v1",
        client_capture_id="00000000-0000-4000-8000-000000000001",
        capture_started_at=OBSERVED_AT,
        captured_at=OBSERVED_AT,
        page_identity={
            "origin": "https://trade.gaijin.net",
            "market_path": "/market/1067/item-one",
            "item_key": "item-one",
        },
        capture_sha256="2" * 64,
        extension_version="1.0.0",
        imported_at=OBSERVED_AT,
        page_item_key_matches_database_item=True,
    )
    market_payload = build_market_history_export(
        [record],
        database_schema_revision="20260711_0004",
    )
    market_path = tmp_path / "market-history.json"
    market_path.write_bytes(market_history_export_json_bytes(market_payload))

    mapping = {
        "schema_version": "round6_item_mapping_v1",
        "mappings": [
            {
                "offline_sample_key": "sample-one",
                "database_item_id": 101,
                "database_external_key": "item-one",
                "confirmed_by_user": True,
            }
        ],
    }
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    return manifest_path, market_path, mapping_path


def cli_args(
    manifest: Path,
    market: Path,
    mapping: Path,
    output: Path,
    *extra: str,
) -> list[str]:
    return [
        "--history-manifest",
        str(manifest),
        "--market-history-export",
        str(market),
        "--item-mapping",
        str(mapping),
        "--output",
        str(output),
        *extra,
    ]


def test_cli_builds_loadable_deterministic_dataset(
    tmp_path: Path, capsys
) -> None:
    manifest, market, mapping = write_inputs(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert main(cli_args(manifest, market, mapping, first)) == 0
    first_stdout = capsys.readouterr().out
    assert "combined_calibration_ok" in first_stdout
    assert main(cli_args(manifest, market, mapping, second)) == 0
    capsys.readouterr()

    assert first.read_bytes() == second.read_bytes()
    loaded = load_combined_calibration_dataset(first)
    assert len(loaded.market_histories) == 1
    assert len(loaded.historical_trade_histories) == 1
    assert loaded.availability_by_observation_key[
        "review:one"
    ].available_bucket_keys == ("1h:2026-01-02T10:00:00Z",)
    assert not list(tmp_path.glob(".*.tmp"))


def test_cli_pretty_output_keeps_fingerprints(tmp_path: Path, capsys) -> None:
    manifest, market, mapping = write_inputs(tmp_path)
    compact = tmp_path / "compact.json"
    pretty = tmp_path / "pretty.json"

    assert main(cli_args(manifest, market, mapping, compact)) == 0
    capsys.readouterr()
    assert main(cli_args(manifest, market, mapping, pretty, "--pretty")) == 0
    capsys.readouterr()

    compact_loaded = load_combined_calibration_dataset(compact)
    pretty_loaded = load_combined_calibration_dataset(pretty)
    assert compact.read_bytes() != pretty.read_bytes()
    assert (
        compact_loaded.combined_source_fingerprint
        == pretty_loaded.combined_source_fingerprint
    )
    assert (
        compact_loaded.combined_typed_fingerprint
        == pretty_loaded.combined_typed_fingerprint
    )


def test_cli_reports_combined_mapping_error_without_partial_output(
    tmp_path: Path, capsys
) -> None:
    manifest, market, mapping = write_inputs(tmp_path)
    mapping_payload = json.loads(mapping.read_text())
    mapping_payload["mappings"][0]["offline_sample_key"] = "missing"
    mapping.write_text(json.dumps(mapping_payload), encoding="utf-8")
    output = tmp_path / "combined.json"

    assert main(cli_args(manifest, market, mapping, output)) == 4
    captured = capsys.readouterr()
    assert "combined_dataset_error:combined_mapping_historical_sample_missing" in captured.err
    assert not output.exists()


def test_cli_reports_history_validation_error(tmp_path: Path, capsys) -> None:
    manifest, market, mapping = write_inputs(tmp_path)
    manifest_payload = json.loads(manifest.read_text())
    manifest_payload["samples"][0]["expected_sha256"] = "0" * 64
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")

    assert main(cli_args(manifest, market, mapping, tmp_path / "out.json")) == 2
    captured = capsys.readouterr()
    assert "combined_history_error:sample_sha256_mismatch" in captured.err
