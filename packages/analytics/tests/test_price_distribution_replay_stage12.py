from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG

# Two vehicles that the bundled catalog places in the same recurring craft
# family across several years, so the cohort produces both item-local prior
# events and independent cross-item family evidence without any private data.
ITEMS = (
    (5101, "Object 279"),
    (5102, "SU-76D"),
)
SERIES_START = datetime(2022, 1, 1, tzinfo=UTC)
AS_OF = datetime(2026, 7, 1, tzinfo=UTC)
# Distinct post-event plateaus so the observed recovery ratios are not all equal.
PLATEAUS = (Decimal("90"), Decimal("105"), Decimal("85"), Decimal("110"), Decimal("95"))
BASE_PRICE = Decimal("100")
ACTIVE_PRICE = Decimal("60")
RECOVERY_DAYS = 30


def _member_events(item_id: int, item_name: str):
    facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(item_id=item_id, item_name=item_name)
    member_keys = {value.event_key for value in facts.memberships}
    return tuple(
        sorted(
            (value for value in facts.events if value.event_key in member_keys),
            key=lambda value: value.opens_at,
        )
    )


def _price_and_volume(day: datetime, events) -> tuple[Decimal, int]:
    previous = None
    previous_index = -1
    for index, event in enumerate(events):
        if event.opens_at <= day:
            previous = event
            previous_index = index
    if previous is None:
        return BASE_PRICE, 2
    if day < previous.closes_at:
        return ACTIVE_PRICE, 10
    plateau = PLATEAUS[previous_index % len(PLATEAUS)]
    elapsed = (day - previous.closes_at).days
    if elapsed >= RECOVERY_DAYS:
        return plateau, 2
    progress = Decimal(elapsed) / Decimal(RECOVERY_DAYS)
    return ACTIVE_PRICE + (plateau - ACTIVE_PRICE) * progress, 3


def _daily_points(events) -> list[list[int]]:
    points: list[list[int]] = []
    day = SERIES_START
    while day < AS_OF:
        price, volume = _price_and_volume(day, events)
        points.append([int(day.timestamp()), int(price * Decimal("10000")), volume])
        day += timedelta(days=1)
    return points


def _write_manifest(tmp_path: Path) -> Path:
    entries = []
    for item_id, item_name in ITEMS:
        events = _member_events(item_id, item_name)
        assert len(events) >= 3, f"{item_name} needs several catalog events"
        history = tmp_path / f"history-{item_id}.json"
        history.write_text(
            json.dumps(
                {
                    "schema_version": "gaijin_trade_history_v1",
                    "item_key": f"synthetic-{item_id}",
                    "series": {"1d": _daily_points(events), "1h": []},
                    "source_url": "https://example.invalid/synthetic",
                }
            ),
            encoding="utf-8",
        )
        entries.append(
            {
                "item_id": item_id,
                "item_name": item_name,
                "history_path": history.name,
                "as_of": AS_OF.isoformat().replace("+00:00", "Z"),
                "role": "calibration",
            }
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"schema_version": "named_recovery_cohort_manifest_v1", "items": entries}
        ),
        encoding="utf-8",
    )
    return manifest


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("price-distribution-replay")
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


def test_replay_publishes_item_local_and_family_price_distributions(report):
    assert report["schema_version"] == "named_recovery_cohort_report_v14"
    decisions = report["decisions"]
    assert decisions
    item_local = [
        value for value in decisions if value["price_distribution_item_local"]["available"]
    ]
    family = [
        value for value in decisions if value["price_distribution_family"]["available"]
    ]
    assert item_local, "expected at least one usable item-local distribution"
    assert family, "expected at least one usable cross-item family distribution"
    sample = item_local[0]["price_distribution_item_local"]
    assert sample["basis"] == "PRE_EVENT_MEDIAN"
    assert sample["sample_count"] >= 2
    assert sample["support"]
    assert sample["probability_net_profit_positive"] is not None


def test_family_distribution_never_contains_the_target_item(report):
    for value in report["decisions"]:
        forecast = value["price_distribution_family"]
        assert forecast["basis"] == "ENTRY_PROXY"
        if forecast["available"]:
            assert forecast["item_local_sample_count"] == 0
            assert forecast["family_sample_count"] == forecast["sample_count"]


def test_price_distribution_metrics_score_against_the_binary_signal(report):
    metrics = report["metrics"]["price_distribution"]
    assert metrics["decision_count"] == len(report["decisions"])
    for name in ("item_local", "family_cross_item"):
        block = metrics[name]
        assert sum(block["status_counts"].values()) == metrics["decision_count"]
        assert sum(block["probability_integral_transform_histogram"].values()) == block[
            "scored_count"
        ]
    scored = metrics["item_local"]["scored_count"]
    assert scored > 0
    assert metrics["item_local"]["mean_distribution_brier"] is not None
    assert metrics["item_local"]["mean_binary_signal_brier"] is not None


def test_scored_distributions_stay_inside_their_own_evidence_window(report):
    for value in report["decisions"]:
        for forecast_key, outcome_key in (
            ("price_distribution_item_local", "price_distribution_outcome_item_local"),
            ("price_distribution_family", "price_distribution_outcome_family"),
        ):
            outcome = value[outcome_key]
            if outcome is None:
                continue
            forecast = value[forecast_key]
            assert outcome["prediction_as_of"] == forecast["prediction_as_of"]
            assert outcome["outcome_observed_at"] > outcome["prediction_as_of"]
            pit = Decimal(outcome["probability_integral_transform"])
            assert Decimal("0") <= pit <= Decimal("1")
