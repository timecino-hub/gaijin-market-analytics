"""The candidate strategy is a read-only, clocked decimal calculation."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.clock import get_utc_clock
from api.db.session import get_session
from api.main import app

PATH = "/api/v1/general-strategy"
NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def strategy_client() -> Iterator[TestClient]:
    previous = app.dependency_overrides.copy()

    def refuse_database() -> None:
        raise AssertionError("The general-strategy endpoint must not access a database.")

    app.dependency_overrides[get_utc_clock] = lambda: lambda: NOW
    app.dependency_overrides[get_session] = refuse_database
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _no_floats(value: Any) -> None:
    assert not isinstance(value, float)
    if isinstance(value, dict):
        for member in value.values():
            _no_floats(member)
    elif isinstance(value, list):
        for member in value:
            _no_floats(member)


def test_rule_is_available_without_prices_or_database(strategy_client: TestClient) -> None:
    response = strategy_client.get(PATH)
    assert response.status_code == 200
    body = response.json()
    assert body["research_only"] is True
    assert body["automated_trading"] is False
    assert body["fee_rate"] == "0.15"
    assert body["as_of"] == "2026-10-01T00:00:00Z"
    assert body["version"]
    assert body["exit"]["status"] == "NOT_EVALUATED"
    assert body["examples"]
    _no_floats(body)


def test_buy_thresholds_keep_decimal_strings(strategy_client: TestClient) -> None:
    response = strategy_client.get(PATH, params={
        "baseline": "100",
        "observation_price": "74.99",
        "quoted_cost": "55",
        "event_closes_on": "2026-10-03",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["entry"]["observation_threshold"] == "75.00"
    assert body["entry"]["buy_cost_ceiling"] == "55.00"
    assert body["entry"]["alternative_buy_cost_ceiling"] == "60.00"
    assert body["entry"]["observation_on"] == "2026-09-30"
    assert body["entry"]["quote_check_on"] == "2026-10-01"
    _no_floats(body)


@pytest.mark.parametrize("days,expected", [
    (149, "TARGET50"),
    (150, "TARGET40"),
    (179, "TARGET40"),
    (180, "REVIEW_REQUIRED"),
    (250, "REVIEW_REQUIRED"),
])
def test_elapsed_time_changes_offer_and_then_requests_review(
    strategy_client: TestClient, days: int, expected: str,
) -> None:
    bought = datetime(2026, 1, 1, tzinfo=UTC)
    response = strategy_client.get(PATH, params={
        "entry_cost": "50",
        "bought_at": bought.isoformat(),
        "as_of": (bought + timedelta(days=days)).isoformat(),
    })
    assert response.status_code == 200
    body = response.json()
    assert body["exit"]["status"] == expected
    assert body["exit"]["preferred_offer"] == "88.24"
    assert body["exit"]["minimum_offer"] == "82.36"
    _no_floats(body)


def test_timezone_offsets_are_equivalent(strategy_client: TestClient) -> None:
    params = {"entry_cost": "50", "bought_at": "2026-05-04T00:00:00Z"}
    utc = strategy_client.get(PATH, params=params | {"as_of": "2026-10-01T00:00:00Z"})
    japan = strategy_client.get(PATH, params=params | {"as_of": "2026-10-01T09:00:00+09:00"})
    assert utc.status_code == japan.status_code == 200
    assert utc.json() == japan.json()


@pytest.mark.parametrize("changed,expected", [
    ({}, "PRICE_CONDITIONS_SATISFIED"),
    ({"quoted_cost": "55.01"}, "COST_CAP_EXCEEDED"),
    ({"observation_price": "75.01"}, "OBSERVATION_GATE_FAILED"),
    ({"as_of": "2026-09-30T23:59:59Z"}, "OBSERVATION_NOT_COMPLETE"),
    ({"as_of": "2026-10-02T00:00:00Z"}, "ENTRY_WINDOW_CLOSED"),
])
def test_buy_evaluation_respects_cost_gate_and_completed_observation(
    strategy_client: TestClient, changed: dict[str, str], expected: str,
) -> None:
    response = strategy_client.get(PATH, params={
        "baseline": "100", "observation_price": "75", "quoted_cost": "55",
        "event_closes_on": "2026-10-03",
    } | changed)
    assert response.status_code == 200
    assert response.json()["entry"]["status"] == expected
    assert response.json()["entry"]["fill_claim"] is False


@pytest.mark.parametrize("params", [
    {"event_closes_on": "0001-01-01"},
    {"entry_cost": "50", "bought_at": "9999-12-31T00:00:00Z", "as_of": "9999-12-31T00:00:00Z"},
])
def test_dates_that_cannot_support_policy_offsets_return_400(
    strategy_client: TestClient, params: dict[str, str],
) -> None:
    response = strategy_client.get(PATH, params=params)
    assert response.status_code == 400


def test_large_price_and_high_fee_still_return_string_prices(strategy_client: TestClient) -> None:
    response = strategy_client.get(PATH, params={
        "entry_cost": "1000000000000", "bought_at": "2026-01-01T00:00:00Z",
        "fee_rate": "0.9999999999999999999999999999",
    })
    assert response.status_code == 200
    assert isinstance(response.json()["exit"]["preferred_offer"], str)
    _no_floats(response.json())


@pytest.mark.parametrize("params,code", [
    ({"baseline": "0"}, "invalid_baseline"),
    ({"baseline": "-1"}, "invalid_baseline"),
    ({"baseline": "NaN"}, "invalid_baseline"),
    ({"baseline": "Infinity"}, "invalid_baseline"),
    ({"baseline": "1e1000000"}, "invalid_baseline"),
    ({"baseline": "1000000000001"}, "invalid_baseline"),
    ({"baseline": "1e-1000000"}, "invalid_baseline"),
    ({"baseline": "9" * 65}, "invalid_baseline"),
    ({"observation_price": "-Infinity"}, "invalid_observation_price"),
    ({"quoted_cost": "text"}, "invalid_quoted_cost"),
    ({"fee_rate": "NaN"}, "invalid_fee_rate"),
    ({"fee_rate": "1"}, "invalid_fee_rate"),
    ({"fee_rate": "-0.1"}, "invalid_fee_rate"),
    ({"fee_rate": "1e-1000000"}, "invalid_fee_rate"),
    ({"as_of": "garbage"}, "invalid_as_of"),
    ({"as_of": "2026-10-01T00:00:00"}, "as_of_timezone_required"),
    ({"as_of": "0001-01-01T00:00:00+09:00"}, "invalid_as_of"),
    ({"event_closes_on": "2026-02-30"}, "invalid_event_closes_on"),
    ({"event_closes_on": "20261003"}, "invalid_event_closes_on"),
    ({"entry_cost": "50"}, "incomplete_purchase"),
    ({"bought_at": "2026-01-01T00:00:00Z"}, "incomplete_purchase"),
    ({"entry_cost": "50", "bought_at": "bad"}, "invalid_bought_at"),
    ({"entry_cost": "50", "bought_at": "2026-01-01T00:00:00"}, "bought_at_timezone_required"),
    ({"entry_cost": "50", "bought_at": "2026-10-01T00:00:01Z"}, "bought_at_in_future"),
])
def test_bad_inputs_return_structured_400(
    strategy_client: TestClient, params: dict[str, str], code: str,
) -> None:
    response = strategy_client.get(PATH, params=params)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == code


def test_zero_fee_is_a_valid_explicit_assumption(strategy_client: TestClient) -> None:
    response = strategy_client.get(PATH, params={
        "entry_cost": "50", "bought_at": "2026-01-01T00:00:00Z", "fee_rate": "0",
    })
    assert response.status_code == 200
    assert response.json()["exit"]["preferred_offer"] == "75.00"
    assert response.json()["exit"]["minimum_offer"] == "70.00"


def test_openapi_adds_only_a_read_method(strategy_client: TestClient) -> None:
    response = strategy_client.get("/openapi.json")
    assert response.status_code == 200
    definition = response.json()["paths"][PATH]
    assert set(definition) == {"get"}
    params = {value["name"]: value for value in definition["get"]["parameters"]}
    assert set(params) == {
        "baseline", "observation_price", "quoted_cost", "event_closes_on",
        "entry_cost", "bought_at", "fee_rate", "as_of",
    }
    assert strategy_client.post(PATH, json={"baseline": "100"}).status_code == 405
