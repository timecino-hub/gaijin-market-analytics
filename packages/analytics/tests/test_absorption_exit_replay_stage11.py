from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# The Nuclear Football is the only tradeable supply event in the bundled catalog
# that contains this vehicle, so the synthetic series below exercises exactly one
# completed event without needing any private named history.
ITEM_NAME = "Kungstiger"
OPENS_AT = datetime(2026, 4, 9, 11, tzinfo=UTC)
CLOSES_AT = datetime(2026, 5, 6, 11, tzinfo=UTC)
SERIES_START = datetime(2026, 3, 1, tzinfo=UTC)
AS_OF = datetime(2026, 7, 1, tzinfo=UTC)


def _daily_points() -> list[list[int]]:
    points: list[list[int]] = []
    day = SERIES_START
    while day < AS_OF:
        if day + timedelta(days=1) <= OPENS_AT:
            price, volume = 100, 2
        elif day < CLOSES_AT:
            price, volume = 60, 10
        else:
            settled_from = CLOSES_AT + timedelta(days=10)
            price = 70 if day >= settled_from else 62
            volume = 2 if day >= settled_from else 4
        points.append([int(day.timestamp()), price * 10000, volume])
        day += timedelta(days=1)
    return points


def _write_manifest(tmp_path: Path) -> Path:
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps(
            {
                "schema_version": "gaijin_trade_history_v1",
                "item_key": "synthetic-absorption-exit-fixture",
                "series": {"1d": _daily_points(), "1h": []},
                "source_url": "https://example.invalid/synthetic",
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "named_recovery_cohort_manifest_v1",
                "items": [
                    {
                        "item_id": 4242,
                        "item_name": ITEM_NAME,
                        "history_path": history.name,
                        "as_of": AS_OF.isoformat().replace("+00:00", "Z"),
                        "role": "calibration",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _run(tmp_path: Path) -> dict:
    manifest = _write_manifest(tmp_path)
    output = tmp_path / "report.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("packages/analytics/src").resolve())
    subprocess.run(
        [
            sys.executable,
            "tools/named_recovery_cohort_replay.py",
            str(manifest),
            "--output",
            str(output),
        ],
        check=True,
        env=env,
    )
    return json.loads(output.read_text(encoding="utf-8"))


def test_replay_reports_absorption_exit_for_a_matched_named_item(tmp_path: Path):
    report = _run(tmp_path)
    assert report["schema_version"] == "named_recovery_cohort_report_v14"
    item = report["items"][0]
    assert item["catalog_match_status"] == "MATCHED"
    current = item["current_absorption_exit"]
    assert current["event_key"] == "spring_2026_nuclear_football"
    assert current["state"] == "ABSORPTION_EXIT_CONFIRMED"
    assert current["legacy_absorption_active"] is False
    assert current["activity_axis"] == "SETTLED"
    assert current["price_axis"] == "SETTLED"


def test_replay_absorption_metrics_compare_every_withholding_policy(tmp_path: Path):
    report = _run(tmp_path)
    metrics = report["metrics"]["absorption_exit"]
    assert metrics["example_count"] == len(report["scarcity_rebuild_examples"])
    policies = [row["policy"] for row in metrics["policy_metrics"]]
    assert policies == [
        "LEGACY_ONLY",
        "EVIDENCE_EXTENDS_LEGACY",
        "EVIDENCE_REPLACES_LEGACY",
    ]
    for row in metrics["policy_metrics"]:
        assert (
            row["withheld_example_count"] + row["published_example_count"]
            == metrics["example_count"]
        )
    assert sum(metrics["legacy_agreement"].values()) == metrics["example_count"]
    for example in report["scarcity_rebuild_examples"]:
        assert example["absorption_exit"]["as_of"] == example["snapshot"]["as_of"]


def test_replay_absorption_snapshots_never_read_past_their_own_as_of(tmp_path: Path):
    report = _run(tmp_path)
    for example in report["scarcity_rebuild_examples"]:
        snapshot = example["absorption_exit"]
        assert snapshot["as_of"] <= report["items"][0]["source"]["as_of"]
        assert snapshot["event_closes_at"] <= snapshot["as_of"]
