"""Stage 16 — the cohort replay's walk-forward calibration block.

Reuses the stage 12 replay's synthetic two-vehicle cohort rather than building
a second one: the walk-forward block is computed from exactly those price
distribution forecasts, so a separate fixture would be a second definition of
the same cohort that could drift away from the one under test.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_price_distribution_replay_stage12 import _write_manifest


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("walk-forward-replay")
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


def test_the_replay_publishes_a_walk_forward_calibration_block(report) -> None:
    block = report["metrics"]["walk_forward_calibration"]

    assert set(block) == {"note", "item_local", "family_cross_item"}
    for source in ("item_local", "family_cross_item"):
        assert set(block[source]) == {
            "resolved_forecast_count",
            "shipped_defaults",
            "relaxed_thresholds",
        }


def test_every_resolved_forecast_is_replayed_exactly_once(report) -> None:
    block = report["metrics"]["walk_forward_calibration"]

    for source in ("item_local", "family_cross_item"):
        count = block[source]["resolved_forecast_count"]
        assert count > 0, f"{source} produced no closed labels to replay"
        for configuration in ("shipped_defaults", "relaxed_thresholds"):
            assert block[source][configuration]["trial_count"] == count


def test_the_shipped_defaults_refuse_on_a_two_vehicle_cohort(report) -> None:
    """Production thresholds cannot fire here, and say so rather than firing.

    The cohort holds two vehicles and ``minimum_distinct_items`` is three, so
    no decision instant in this replay ever has a calibration sample spread
    across enough items. A zero here is the correct answer, not a gap.
    """

    block = report["metrics"]["walk_forward_calibration"]

    for source in ("item_local", "family_cross_item"):
        shipped = block[source]["shipped_defaults"]
        assert shipped["calibrated_trial_count"] == 0
        assert shipped["mean_applied_factor"] is None
        assert (
            shipped["raw_uniformity_criterion"]
            == shipped["calibrated_uniformity_criterion"]
        )
        for interval in shipped["interval_coverage"]:
            assert interval["raw_covered_count"] == interval["calibrated_covered_count"]


def test_the_relaxed_configuration_reports_both_criteria(report) -> None:
    block = report["metrics"]["walk_forward_calibration"]

    for source in ("item_local", "family_cross_item"):
        relaxed = block[source]["relaxed_thresholds"]
        assert relaxed["raw_uniformity_criterion"] is not None
        assert relaxed["calibrated_uniformity_criterion"] is not None
        assert relaxed["status_counts"]
        assert [
            value["nominal_coverage"] for value in relaxed["interval_coverage"]
        ] == ["0.50", "0.80"]


def test_the_block_states_that_no_row_scores_itself(report) -> None:
    note = report["metrics"]["walk_forward_calibration"]["note"]

    assert "never contributes to the fit that scores it" in note
