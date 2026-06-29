import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from api.backtesting_cli import _build_parser, _json_default, _parse_datetime


def test_cli_datetime_parser_requires_timezone() -> None:
    with pytest.raises(Exception):
        _parse_datetime("2026-01-01T00:00:00")

    assert _parse_datetime("2026-01-01T08:00:00+08:00") == datetime(2026, 1, 1, tzinfo=UTC)
    assert _parse_datetime("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)


def test_cli_rejects_fee_rate_argument() -> None:
    parser = _build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(
            [
                "--item-id",
                "1",
                "--lookback-horizon",
                "7",
                "--forward-horizon",
                "7",
                "--start",
                "2026-01-01T00:00:00Z",
                "--end",
                "2026-01-08T00:00:00Z",
                "--cadence-days",
                "7",
                "--fee-rate",
                "0.10",
            ]
        )

    assert exc.value.code == 2


def test_cli_json_serializes_decimal_datetime_bool_and_none_stably() -> None:
    payload = {
        "decimal": Decimal("1.2300"),
        "datetime": datetime(2026, 1, 1, tzinfo=UTC),
        "bool": True,
        "none": None,
    }

    decoded = json.loads(json.dumps(payload, default=_json_default, sort_keys=True))

    assert decoded == {
        "bool": True,
        "datetime": "2026-01-01T00:00:00Z",
        "decimal": "1.2300",
        "none": None,
    }


def test_compact_and_pretty_json_have_equivalent_content() -> None:
    payload = {"metadata": {"item_id": 1}, "summary": {"rate": Decimal("1")}, "cases": []}

    compact = json.dumps(payload, default=_json_default, sort_keys=True)
    pretty = json.dumps(payload, default=_json_default, indent=2, sort_keys=True)

    assert json.loads(compact) == json.loads(pretty)
