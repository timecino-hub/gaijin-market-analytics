from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.market_history_export_cli import (
    _build_parser,
    _positive_item_id,
    _summary,
    _write_atomic,
)


def test_cli_defaults_to_primary_strict_profile(tmp_path: Path) -> None:
    args = _build_parser().parse_args(["--output", str(tmp_path / "export.json")])
    assert args.profile == "point_in_time_primary"
    assert args.allow_exclusions is False
    assert args.item_id is None


def test_cli_item_id_must_be_positive() -> None:
    with pytest.raises(Exception):
        _positive_item_id("0")
    assert _positive_item_id("42") == 42


def test_atomic_write_replaces_destination_without_temp_file(tmp_path: Path) -> None:
    destination = tmp_path / "export.json"
    destination.write_bytes(b"old")
    _write_atomic(destination, b"new\n")
    assert destination.read_bytes() == b"new\n"
    assert list(tmp_path.glob(".export.json.*.tmp")) == []


def test_summary_contains_no_database_credentials(tmp_path: Path) -> None:
    payload = {
        "schema_version": "round6_market_history_export_v1",
        "export_profile": "point_in_time_primary",
        "source_fingerprint": "1" * 64,
        "typed_history_fingerprint": "2" * 64,
        "diagnostics": {"included_record_count": 1, "excluded_record_count": 0},
    }
    summary = _summary(payload, tmp_path / "export.json")
    encoded = json.dumps(summary)
    assert summary["read_only"] is True
    assert "database_url" not in encoded.lower()
    assert "password" not in encoded.lower()
