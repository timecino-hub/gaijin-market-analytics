from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from gaijin_market_analytics.family_prior_generalization import (
    audit_family_prior_generalization,
)
from gaijin_market_analytics.hierarchical_recovery import (
    HistoricalReturnOutcomeEvidence,
    estimate_family_recovery_prior,
)


def dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def evidence(
    *,
    item_id: int,
    event_key: str,
    observed_day: int,
    roi: str,
    gross: str,
) -> HistoricalReturnOutcomeEvidence:
    return HistoricalReturnOutcomeEvidence(
        item_id=item_id,
        family="HISTORY_LUNAR",
        event_key=event_key,
        horizon_days=90,
        prediction_as_of=dt(1),
        outcome_observed_at=dt(observed_day),
        entry_proxy_price=Decimal("100"),
        exit_proxy_price=Decimal(gross) * Decimal("100"),
        gross_exit_entry_ratio=Decimal(gross),
        realized_net_roi=Decimal(roi),
    )


def test_family_prior_excludes_all_target_item_history() -> None:
    result = estimate_family_recovery_prior(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=(
            evidence(item_id=9, event_key="target-old-a", observed_day=2, roi="2", gross="4"),
            evidence(item_id=9, event_key="target-old-b", observed_day=3, roi="2", gross="4"),
            evidence(item_id=1, event_key="other-a", observed_day=4, roi="-0.2", gross="0.9"),
            evidence(item_id=2, event_key="other-b", observed_day=5, roi="-0.2", gross="0.9"),
        ),
    )

    assert result.prior_example_count == 2
    assert result.distinct_prior_item_count == 2
    assert result.profitable_prior_example_count == 0
    assert result.family_support_positive is False
    assert "target_item_history_excluded_from_family_prior" in result.explanation_codes


def test_target_item_history_does_not_create_generalization_breadth() -> None:
    values = (
        evidence(item_id=9, event_key="target-old-a", observed_day=2, roi="0.3", gross="1.6"),
        evidence(item_id=9, event_key="target-old-b", observed_day=3, roi="0.3", gross="1.6"),
        evidence(item_id=1, event_key="old-a", observed_day=4, roi="0.3", gross="1.6"),
        evidence(item_id=2, event_key="old-a", observed_day=5, roi="0.3", gross="1.6"),
        evidence(item_id=1, event_key="old-b", observed_day=6, roi="0.3", gross="1.6"),
        evidence(item_id=2, event_key="old-b", observed_day=7, roi="0.3", gross="1.6"),
    )
    audit = audit_family_prior_generalization(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=values,
    )

    assert audit.distinct_prior_item_count == 2
    assert audit.generalization_ready is False
    assert audit.status == "INSUFFICIENT_ITEM_BREADTH"


def test_named_recovery_tool_accepts_gaijin_trade_history_v1(tmp_path: Path) -> None:
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps(
            {
                "captured_at": "2026-09-20T00:00:00Z",
                "item_key": "unmatched-test-item",
                "schema_version": "gaijin_trade_history_v1",
                "series": {
                    "1d": [[1756684800, 1000000, 1]],
                    "1h": [[1756771200, 1100000, 1]],
                },
                "source_url": "https://trade.gaijin.net/market/1067/unmatched-test-item",
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
                        "item_id": 999,
                        "item_name": "definitely-unmatched-stage8-item",
                        "history_path": history.name,
                        "as_of": "2026-09-20T00:00:00Z",
                        "role": "negative_control",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out.json"
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
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "named_recovery_cohort_report_v5"
    source = report["items"][0]["source"]
    assert source["history_source_schema"] == "gaijin_trade_history_v1"
    assert source["source_item_key"] == "unmatched-test-item"
    assert source["point_count_total"] == 2
