from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


def _load_tool():
    path = Path("tools/named_cohort_candidates.py").resolve()
    spec = importlib.util.spec_from_file_location("named_cohort_candidates", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclass field resolution reads the module back out of sys.modules, so it
    # has to be registered before the module body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()

AS_OF = datetime(2026, 9, 21, tzinfo=UTC)


def test_catalog_vehicle_names_are_unique_and_sorted():
    names = tool.catalog_vehicle_names()
    assert names == tuple(sorted(set(names)))
    assert "Object 279" in names
    assert "Kungstiger" in names


def test_a_vehicle_with_one_event_has_no_walk_forward_capacity():
    summary = tool.summarize_vehicle(vehicle_name="Kungstiger", as_of=AS_OF)
    assert summary.match_status == "MATCHED"
    assert summary.tradeable_event_count == 1
    assert summary.predictable_event_count == 0
    assert summary.walk_forward_outcome_capacity == 0
    assert summary.family_prior_contribution == 1


def test_a_repeatedly_reissued_vehicle_carries_capacity_across_horizons():
    summary = tool.summarize_vehicle(vehicle_name="Object 279", as_of=AS_OF)
    assert summary.completed_tradeable_event_count >= 4
    assert summary.predictable_event_count >= 3
    assert summary.walk_forward_outcome_capacity > 0
    assert dict(summary.capacity_by_horizon)[30] >= dict(summary.capacity_by_horizon)[180]


def test_capacity_is_point_in_time_and_grows_with_as_of():
    early = tool.summarize_vehicle(
        vehicle_name="Object 279", as_of=datetime(2023, 1, 1, tzinfo=UTC)
    )
    late = tool.summarize_vehicle(vehicle_name="Object 279", as_of=AS_OF)
    assert early.completed_tradeable_event_count < late.completed_tradeable_event_count
    assert early.walk_forward_outcome_capacity <= late.walk_forward_outcome_capacity


def test_account_only_returns_never_count_as_tradeable_supply():
    summary = tool.summarize_vehicle(vehicle_name="VL Myrsky II", as_of=AS_OF)
    assert summary.account_only_event_count > 0
    assert summary.tradeable_event_count == 0
    assert summary.walk_forward_outcome_capacity == 0
    assert summary.required_history_start_utc is None


def test_required_history_start_covers_the_pre_event_baseline_window():
    summary = tool.summarize_vehicle(vehicle_name="Object 279", as_of=AS_OF)
    assert summary.first_tradeable_opens_at is not None
    assert summary.required_history_start_utc is not None
    assert summary.required_history_start_utc < summary.first_tradeable_opens_at


def test_report_separates_the_current_cohort_from_new_candidates():
    report = tool.build_report(
        as_of=AS_OF,
        already_in_cohort=("Object 279", "Kungstiger"),
        limit=10,
    )
    assert report["schema_version"] == "named_cohort_candidate_report_v1"
    assert report["already_covered_count"] == 2
    assert report["current_cohort"]["vehicle_count"] == 2
    shortlisted = {value["vehicle_name"] for value in report["shortlist"]}
    assert "Object 279" not in shortlisted
    assert "Kungstiger" not in shortlisted


def test_recommended_batch_spreads_across_families():
    report = tool.build_report(as_of=AS_OF, per_family=2, limit=5)
    batch = report["recommended_batch"]["vehicles"]
    assert batch
    counts: dict[str, int] = {}
    for value in batch:
        counts[value["families"][0]] = counts.get(value["families"][0], 0) + 1
    assert max(counts.values()) <= 2
    assert len(counts) > 1
    assert all(value["walk_forward_outcome_capacity"] > 0 for value in batch)


def test_report_rejects_unsafe_arguments():
    with pytest.raises(tool.CandidateReportError):
        tool.build_report(as_of=AS_OF, per_family=0)
    with pytest.raises(tool.CandidateReportError):
        tool.build_report(as_of=AS_OF, horizons=(0,))
    with pytest.raises(tool.CandidateReportError):
        tool.build_report(as_of=datetime(2026, 9, 21))


def _write_history(directory: Path, *, name: str, start: datetime, days: int) -> str:
    from datetime import timedelta

    points = [
        [int((start + timedelta(days=offset)).timestamp()), 1000000, 2]
        for offset in range(days)
    ]
    path = directory / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "gaijin_trade_history_v1",
                "item_key": name,
                "series": {"1d": points, "1h": []},
            }
        ),
        encoding="utf-8",
    )
    return path.name


def _write_manifest(directory: Path, entries: list[dict]) -> Path:
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"schema_version": "named_recovery_cohort_manifest_v1", "items": entries}
        ),
        encoding="utf-8",
    )
    return manifest


def test_audit_flags_a_history_that_starts_after_the_pre_event_baseline(tmp_path: Path):
    late = _write_history(
        tmp_path, name="late", start=datetime(2026, 6, 1, tzinfo=UTC), days=60
    )
    manifest = _write_manifest(
        tmp_path,
        [
            {
                "item_id": 900,
                "item_name": "Object 279",
                "history_path": late,
                "as_of": "2026-09-21T00:00:00Z",
                "role": "calibration",
            }
        ],
    )
    report = tool.audit_manifest(manifest, as_of=AS_OF)
    assert report["schema_version"] == "named_cohort_coverage_audit_v1"
    item = report["items"][0]
    assert item["covers_required_start"] is False
    assert item["fully_usable_event_count"] == 0
    assert (
        "named_history_starts_after_first_pre_event_baseline"
        in item["explanation_codes"]
    )
    assert report["items_missing_required_start"] == ["Object 279"]


def test_audit_accepts_a_history_that_spans_every_pre_event_window(tmp_path: Path):
    full = _write_history(
        tmp_path, name="full", start=datetime(2022, 1, 1, tzinfo=UTC), days=1700
    )
    manifest = _write_manifest(
        tmp_path,
        [
            {
                "item_id": 901,
                "item_name": "Object 279",
                "history_path": full,
                "as_of": "2026-09-21T00:00:00Z",
                "role": "calibration",
            }
        ],
    )
    report = tool.audit_manifest(manifest, as_of=AS_OF)
    item = report["items"][0]
    assert item["match_status"] == "MATCHED"
    assert item["covers_required_start"] is True
    assert item["fully_usable_event_count"] >= 4
    assert not item["explanation_codes"]
    assert all(
        event["pre_window_covered"] and event["active_window_covered"]
        for event in item["events"]
    )


def test_audit_rejects_a_foreign_manifest_schema(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "other", "items": []}), encoding="utf-8")
    with pytest.raises(tool.CandidateReportError):
        tool.audit_manifest(manifest, as_of=AS_OF)
