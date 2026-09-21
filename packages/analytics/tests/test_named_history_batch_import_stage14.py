from __future__ import annotations

import csv
import importlib.util
import json
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def _load_tool():
    path = Path("tools/named_history_batch_import.py").resolve()
    spec = importlib.util.spec_from_file_location("named_history_batch_import", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()

AS_OF = datetime(2026, 9, 21, tzinfo=UTC)
CAPTURED_AT = "2026-09-20T00:00:00Z"
KUNGSTIGER_KEY = "id50268_kungstiger_sweden"


def _daily_points(start: datetime, count: int) -> list[list[int]]:
    base = int(start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    base -= base % 86400
    return [[base + 86400 * index, 1_000_000 + index, 3] for index in range(count)]


def _capture(
    directory: Path,
    *,
    name: str = "kungstiger.json",
    item_key: str = KUNGSTIGER_KEY,
    start: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    days: int = 260,
    source_url: str | None = None,
    captured_at: str = CAPTURED_AT,
    series: dict | None = None,
    drop_field: str | None = None,
    extra_field: bool = False,
) -> Path:
    payload = {
        "schema_version": "gaijin_trade_history_v1",
        "item_key": item_key,
        "source_url": source_url
        or f"https://trade.gaijin.net/market/1067/{item_key}",
        "captured_at": captured_at,
        "series": series
        if series is not None
        else {"1d": _daily_points(start, days), "1h": []},
    }
    if drop_field:
        payload.pop(drop_field)
    if extra_field:
        payload["operator_note"] = "unexpected"
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _config_dir(directory: Path) -> Path:
    # Kept out of the capture directory: the planner treats every *.json there
    # as a capture, and a stray control file would be reported as invalid.
    path = directory / "config"
    path.mkdir(exist_ok=True)
    return path


def _binding(directory: Path, entries: list[dict]) -> Path:
    path = _config_dir(directory) / "binding.json"
    path.write_text(
        json.dumps(
            {"schema_version": "named_history_binding_v1", "bindings": entries}
        ),
        encoding="utf-8",
    )
    return path


def _existing(directory: Path, entries: list[dict]) -> Path:
    path = _config_dir(directory) / "existing-items.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _plan(tmp_path: Path, *, binding=None, existing=None) -> dict:
    return tool.build_plan(
        capture_dir=tmp_path,
        binding_path=binding,
        existing_items_path=existing,
        as_of=AS_OF,
    )


def test_bound_capture_with_an_existing_item_is_ready(tmp_path: Path):
    _capture(tmp_path)
    binding = _binding(
        tmp_path, [{"item_key": KUNGSTIGER_KEY, "vehicle_name": "Kungstiger"}]
    )
    existing = _existing(
        tmp_path, [{"id": 2, "external_key": KUNGSTIGER_KEY, "name": "Kungstiger"}]
    )
    report = _plan(tmp_path, binding=binding, existing=existing)
    assert report["schema_version"] == "named_history_batch_plan_v1"
    assert report["ready_count"] == 1
    capture = report["captures"][0]
    assert capture["verdict"] == "READY"
    assert capture["bound_vehicle_name"] == "Kungstiger"
    assert capture["existing_item_id"] == 2
    assert capture["existing_item_name_binds"] is True
    assert capture["fully_usable_event_count"] == 1
    assert capture["covers_required_start"] is True
    # Nobody has reviewed which supply channels this vehicle receives, so the
    # capture is usable but its coverage is not claimed to be complete.
    assert capture["supply_channel_coverage"] == "PARTIALLY_CATALOGUED"
    assert capture["supply_channels_reviewed"] is False
    assert capture["explanation_codes"] == ("supply_channels_not_reviewed",)


def test_capture_without_a_binding_is_never_bound_automatically(tmp_path: Path):
    _capture(tmp_path)
    report = _plan(tmp_path)
    capture = report["captures"][0]
    assert capture["verdict"] == "BINDING_REQUIRED"
    assert capture["bound_vehicle_name"] is None
    assert "Kungstiger" in capture["suggested_vehicle_names"]
    assert (
        "operator_binding_to_catalogued_vehicle_required"
        in capture["explanation_codes"]
    )


def test_binding_to_a_vehicle_outside_the_reissue_catalog_is_flagged(tmp_path: Path):
    _capture(tmp_path)
    binding = _binding(
        tmp_path,
        [{"item_key": KUNGSTIGER_KEY, "vehicle_name": "Not A Catalogued Vehicle"}],
    )
    capture = _plan(tmp_path, binding=binding)["captures"][0]
    assert capture["verdict"] == "NO_REISSUE_CATALOG_BINDING"
    assert (
        "named_history_not_bound_to_supply_catalog" in capture["explanation_codes"]
    )


def test_history_that_misses_the_baseline_window_is_not_ready(tmp_path: Path):
    _capture(tmp_path, start=datetime(2026, 6, 1, tzinfo=UTC), days=100)
    binding = _binding(
        tmp_path, [{"item_key": KUNGSTIGER_KEY, "vehicle_name": "Kungstiger"}]
    )
    capture = _plan(tmp_path, binding=binding)["captures"][0]
    assert capture["verdict"] == "NO_USABLE_EVENT"
    assert capture["covers_required_start"] is False
    assert (
        "named_history_starts_after_first_pre_event_baseline"
        in capture["explanation_codes"]
    )


def test_an_existing_item_whose_name_cannot_bind_is_called_out(tmp_path: Path):
    _capture(tmp_path)
    binding = _binding(
        tmp_path, [{"item_key": KUNGSTIGER_KEY, "vehicle_name": "Kungstiger"}]
    )
    # Mirrors the production row whose name is the operator's own label.
    existing = _existing(
        tmp_path, [{"id": 5, "external_key": KUNGSTIGER_KEY, "name": "M8"}]
    )
    capture = _plan(tmp_path, binding=binding, existing=existing)["captures"][0]
    assert capture["existing_item_name_binds"] is False
    assert (
        "existing_item_name_does_not_bind_to_catalog"
        in capture["explanation_codes"]
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"drop_field": "captured_at"}, "capture_field_mismatch"),
        ({"extra_field": True}, "capture_field_mismatch"),
        (
            {"source_url": "https://trade.gaijin.net/market/9999/slug"},
            "source_url_not_a_supported_market_item_page",
        ),
        (
            {"source_url": "http://trade.gaijin.net/market/1067/slug"},
            "source_url_not_a_supported_market_item_page",
        ),
        (
            {"source_url": "https://evil.example/market/1067/slug"},
            "source_url_not_a_supported_market_item_page",
        ),
        ({"captured_at": "2099-01-01T00:00:00Z"}, "captured_at_in_future"),
        ({"captured_at": "2026-09-20T00:00:00"}, "captured_at_must_be_timezone_aware"),
        ({"series": {"1d": [], "1h": []}}, "series_empty"),
        ({"series": {"1d": []}}, "series_must_contain_exactly_1h_and_1d"),
        (
            {"series": {"1d": [[1767225600, 100, 1], [1767139200, 100, 1]], "1h": []}},
            "series_1d_not_sorted",
        ),
        (
            {"series": {"1d": [[1767139200, 100, 1], [1767139200, 100, 1]], "1h": []}},
            "series_1d_duplicate_timestamp",
        ),
        (
            {"series": {"1d": [[1767139201, 100, 1]], "1h": []}},
            "series_1d_timestamp_not_aligned",
        ),
        (
            {"series": {"1d": [[1767139200, 0, 1]], "1h": []}},
            "series_1d_price_out_of_range",
        ),
        (
            {"series": {"1d": [[1767139200, 100, 0]], "1h": []}},
            "series_1d_volume_out_of_range",
        ),
        (
            {"series": {"1d": [[1767139200, 100]], "1h": []}},
            "series_1d_point_shape_invalid",
        ),
        (
            {"series": {"1d": [[1767139200, 100, True]], "1h": []}},
            "series_1d_point_must_be_integers",
        ),
    ],
)
def test_invalid_captures_are_rejected_before_any_upload(
    tmp_path: Path, kwargs, expected
):
    _capture(tmp_path, **kwargs)
    capture = _plan(tmp_path)["captures"][0]
    assert capture["verdict"] == "INVALID_DOCUMENT"
    assert any(expected in code for code in capture["explanation_codes"])


def test_unreadable_capture_is_recorded_not_raised(tmp_path: Path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    capture = _plan(tmp_path)["captures"][0]
    assert capture["verdict"] == "INVALID_DOCUMENT"
    assert capture["explanation_codes"][0].startswith("unreadable_capture")


def test_item_catalog_csv_omits_items_that_already_exist(tmp_path: Path):
    _capture(tmp_path, name="kungstiger.json")
    _capture(
        tmp_path,
        name="merkava.json",
        item_key="merkava_mk3d_usa",
        start=datetime(2022, 1, 1, tzinfo=UTC),
        days=1300,
    )
    binding = _binding(
        tmp_path,
        [
            {"item_key": KUNGSTIGER_KEY, "vehicle_name": "Kungstiger"},
            {"item_key": "merkava_mk3d_usa", "vehicle_name": "Merkava Mk.3D (USA)"},
        ],
    )
    existing = _existing(
        tmp_path, [{"id": 2, "external_key": KUNGSTIGER_KEY, "name": "Kungstiger"}]
    )
    report = _plan(tmp_path, binding=binding, existing=existing)
    output = tmp_path / "items.csv"
    written = tool.write_item_catalog_csv(report, output)
    assert written == 1
    rows = list(csv.DictReader(output.read_text(encoding="utf-8").splitlines()))
    assert [row["external_key"] for row in rows] == ["merkava_mk3d_usa"]
    assert rows[0]["name"] == "Merkava Mk.3D (USA)"
    assert rows[0]["category"] == "vehicle"
    assert rows[0]["is_active"] == "true"
    assert output.read_text(encoding="utf-8").splitlines()[0] == (
        "external_key,name,category,rarity,is_active"
    )


def test_manifest_only_contains_items_the_database_already_has(tmp_path: Path):
    _capture(tmp_path, name="kungstiger.json")
    _capture(
        tmp_path,
        name="merkava.json",
        item_key="merkava_mk3d_usa",
        start=datetime(2022, 1, 1, tzinfo=UTC),
        days=1300,
    )
    binding = _binding(
        tmp_path,
        [
            {"item_key": KUNGSTIGER_KEY, "vehicle_name": "Kungstiger"},
            {
                "item_key": "merkava_mk3d_usa",
                "vehicle_name": "Merkava Mk.3D (USA)",
                "role": "diagnostic",
            },
        ],
    )
    existing = _existing(
        tmp_path, [{"id": 2, "external_key": KUNGSTIGER_KEY, "name": "Kungstiger"}]
    )
    report = _plan(tmp_path, binding=binding, existing=existing)
    manifest_path = tmp_path / "manifest.json"
    count = tool.write_cohort_manifest(report, manifest_path, capture_dir=tmp_path)
    assert count == 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "named_recovery_cohort_manifest_v1"
    entry = manifest["items"][0]
    assert entry == {
        "item_id": 2,
        "item_name": "Kungstiger",
        "history_path": "kungstiger.json",
        "as_of": "2026-09-21T00:00:00Z",
        "role": "calibration",
    }


def test_manifest_refuses_to_invent_item_identities(tmp_path: Path):
    _capture(tmp_path)
    binding = _binding(
        tmp_path, [{"item_key": KUNGSTIGER_KEY, "vehicle_name": "Kungstiger"}]
    )
    report = _plan(tmp_path, binding=binding)
    with pytest.raises(tool.BatchImportError):
        tool.write_cohort_manifest(
            report, tmp_path / "manifest.json", capture_dir=tmp_path
        )


def test_binding_file_contract_is_enforced(tmp_path: Path):
    bad = _config_dir(tmp_path) / "bad.json"
    bad.write_text(json.dumps({"schema_version": "other"}), encoding="utf-8")
    with pytest.raises(tool.BatchImportError):
        tool.build_plan(
            capture_dir=tmp_path,
            binding_path=bad,
            existing_items_path=None,
            as_of=AS_OF,
        )
    duplicate = _binding(
        tmp_path,
        [
            {"item_key": "a", "vehicle_name": "Kungstiger"},
            {"item_key": "a", "vehicle_name": "Kungstiger"},
        ],
    )
    with pytest.raises(tool.BatchImportError):
        tool.build_plan(
            capture_dir=tmp_path,
            binding_path=duplicate,
            existing_items_path=None,
            as_of=AS_OF,
        )
    bad_role = _binding(
        tmp_path, [{"item_key": "a", "vehicle_name": "Kungstiger", "role": "buy"}]
    )
    with pytest.raises(tool.BatchImportError):
        tool.build_plan(
            capture_dir=tmp_path,
            binding_path=bad_role,
            existing_items_path=None,
            as_of=AS_OF,
        )


def _fake_import_script(directory: Path) -> Path:
    """A stand-in for deploy/import-approved-history.sh.

    It succeeds with a JSON summary, except for a file whose name contains
    "bad", where it fails the way the real importer does on a rejected
    document.
    """

    if sys.platform == "win32":
        path = directory / "fake-import.bat"
        path.write_text(
            "@echo off\r\n"
            'echo %1 | findstr /C:"bad" >nul\r\n'
            "if %errorlevel%==0 (\r\n"
            "  echo import_error 1>&2\r\n"
            "  exit /b 3\r\n"
            ")\r\n"
            'echo {"mode":"dry_run","database_written":false}\r\n'
            "exit /b 0\r\n",
            encoding="utf-8",
        )
        return path
    path = directory / "fake-import.sh"
    path.write_text(
        "#!/bin/sh\n"
        'case "$1" in *bad*) echo import_error >&2; exit 3;; esac\n'
        "echo '{\"mode\":\"dry_run\",\"database_written\":false}'\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_batch_import_records_each_file_and_keeps_going(tmp_path: Path):
    inbox = tmp_path / "history"
    inbox.mkdir()
    (inbox / "good-one.json").write_text("{}", encoding="utf-8")
    (inbox / "bad-two.json").write_text("{}", encoding="utf-8")
    (inbox / "good-three.json").write_text("{}", encoding="utf-8")
    script = _fake_import_script(tmp_path)

    ledger = tool.run_batch_import(
        intake_history_dir=inbox, script=script, write=False
    )
    assert ledger["schema_version"] == "named_history_batch_ledger_v1"
    assert ledger["mode"] == "dry_run"
    assert ledger["file_count"] == 3
    assert ledger["succeeded_count"] == 2
    assert ledger["failed_count"] == 1
    failed = next(row for row in ledger["results"] if not row["succeeded"])
    assert failed["file"] == "bad-two.json"
    assert failed["exit_code"] == 3
    assert failed["summary"] is None
    succeeded = next(row for row in ledger["results"] if row["succeeded"])
    assert succeeded["summary"]["mode"] == "dry_run"


def test_batch_import_requires_an_existing_inbox(tmp_path: Path):
    with pytest.raises(tool.BatchImportError):
        tool.run_batch_import(
            intake_history_dir=tmp_path / "missing",
            script=tmp_path / "script.sh",
            write=False,
        )


def test_a_negative_control_belongs_in_the_cohort_precisely_because_it_cannot_bind(
    tmp_path: Path,
):
    _capture(tmp_path, name="control.json", item_key="items_t10a_ussr")
    binding = _binding(
        tmp_path,
        [
            {
                "item_key": "items_t10a_ussr",
                "vehicle_name": "Т-10А (USSR)",
                "role": "negative_control",
            }
        ],
    )
    existing = _existing(
        tmp_path,
        [{"id": 3, "external_key": "items_t10a_ussr", "name": "Т-10А (USSR)"}],
    )
    report = _plan(tmp_path, binding=binding, existing=existing)
    capture = report["captures"][0]
    assert capture["verdict"] == "READY"
    assert capture["catalog_match_status"] == "UNMATCHED"
    assert "negative_control_not_expected_to_bind" in capture["explanation_codes"]

    manifest_path = tmp_path / "manifest.json"
    assert tool.write_cohort_manifest(report, manifest_path, capture_dir=tmp_path) == 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["items"][0]["role"] == "negative_control"

    # A control still never creates a database item on its own.
    csv_path = tmp_path / "items.csv"
    assert tool.write_item_catalog_csv(report, csv_path) == 0


def test_a_negative_control_that_does_bind_is_flagged(tmp_path: Path):
    _capture(tmp_path, name="control.json")
    binding = _binding(
        tmp_path,
        [
            {
                "item_key": KUNGSTIGER_KEY,
                "vehicle_name": "Kungstiger",
                "role": "negative_control",
            }
        ],
    )
    capture = _plan(tmp_path, binding=binding)["captures"][0]
    assert capture["verdict"] == "READY"
    assert (
        "negative_control_unexpectedly_binds_to_catalog"
        in capture["explanation_codes"]
    )


def test_a_still_open_event_is_distinguished_from_a_short_history(tmp_path: Path):
    # The Summer 2026 event opens 2026-08-27 and closes 2026-09-25, so on
    # 2026-09-21 a vehicle whose only event is that one has nothing to measure.
    _capture(
        tmp_path,
        name="ebr.json",
        item_key="id50145_e_b_r__1963_france",
        start=datetime(2021, 11, 24, tzinfo=UTC),
        days=1700,
    )
    binding = _binding(
        tmp_path,
        [
            {
                "item_key": "id50145_e_b_r__1963_france",
                "vehicle_name": "E.B.R. (1963)",
            }
        ],
    )
    capture = _plan(tmp_path, binding=binding)["captures"][0]
    assert capture["verdict"] == "EVENT_NOT_COMPLETED"
    assert capture["tradeable_event_count"] == 1
    assert capture["fully_usable_event_count"] == 0
    assert capture["covers_required_start"] is True
