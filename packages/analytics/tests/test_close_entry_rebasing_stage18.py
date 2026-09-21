"""Stage 18 — state tables scored under both entry conventions.

Every state table in the replay has always priced a *checkpoint* entry: buy at
the price on the day the checkpoint falls, hold sixty days. On the real cohort
that entry loses at all four checkpoints, so a state label judged only against
it is being ranked by degrees of losing.

The same exits are therefore also priced from the entry the decisions use, the
first completed day after the event closed. Nothing about the exit changes,
which is what these tests pin.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from test_price_distribution_replay_stage12 import _write_manifest


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("close-entry-rebasing")
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


def scored(report: dict) -> list[dict]:
    return [
        value
        for value in report["scarcity_rebuild_examples"]
        if value["forward_60d_outcome"]["outcome_available"]
    ]


def test_every_checkpoint_row_carries_both_entry_conventions(report) -> None:
    rows = report["scarcity_rebuild_examples"]

    assert rows
    for row in rows:
        assert "forward_60d_outcome" in row
        assert "forward_60d_outcome_from_close" in row


def test_rebasing_keeps_the_exit_day_and_the_proceeds(report) -> None:
    """The only thing that may differ between the two is the entry."""

    rows = scored(report)

    assert rows, "expected at least one scored checkpoint"
    for row in rows:
        checkpoint = row["forward_60d_outcome"]
        close = row["forward_60d_outcome_from_close"]
        assert close["exit_proxy_price"] == checkpoint["exit_proxy_price"]
        assert close["sale_proceeds"] == checkpoint["sale_proceeds"]
        assert (
            "same_exit_day_and_proceeds_as_the_checkpoint_entry_outcome"
            in close["explanation_codes"]
        )


def test_the_close_entry_return_is_arithmetic_on_that_shared_exit(report) -> None:
    rows = [
        row
        for row in scored(report)
        if row["forward_60d_outcome_from_close"]["outcome_available"]
    ]

    assert rows
    for row in rows:
        close = row["forward_60d_outcome_from_close"]
        entry = Decimal(close["entry_proxy_price"])
        proceeds = Decimal(close["sale_proceeds"])
        assert Decimal(close["net_profit"]) == proceeds - entry
        assert Decimal(close["net_roi"]) == (proceeds - entry) / entry


def test_the_two_entries_are_actually_different_prices(report) -> None:
    """Otherwise the comparison would be vacuous on this fixture."""

    rows = [
        row
        for row in scored(report)
        if row["forward_60d_outcome_from_close"]["outcome_available"]
        and row["checkpoint_horizon_days"] > 30
    ]

    assert rows
    assert any(
        row["forward_60d_outcome_from_close"]["entry_proxy_price"]
        != row["forward_60d_outcome"]["entry_proxy_price"]
        for row in rows
    )


def test_the_state_tables_report_both_conventions(report) -> None:
    for block in ("scarcity_rebuild", "absorption_exit"):
        rows = report["metrics"][block]["state_metrics"]
        assert rows
        for row in rows:
            assert "median_forward_net_roi" in row
            assert "close_entry_median_net_roi" in row
            assert "forward_profitability_support_share" in row
            assert "close_entry_support_share" in row


def test_the_absorption_policy_counterfactual_reports_both(report) -> None:
    rows = report["metrics"]["absorption_exit"]["policy_metrics"]

    assert rows
    for row in rows:
        assert "close_entry_median_net_roi" in row
        assert "close_entry_support_share" in row


def test_an_unavailable_close_entry_is_reported_rather_than_dropped(report) -> None:
    """A row with no post-close price still appears, marked unavailable."""

    for row in report["scarcity_rebuild_examples"]:
        close = row["forward_60d_outcome_from_close"]
        if close["outcome_available"]:
            continue
        assert close["net_roi"] is None
        assert close["explanation_codes"]
