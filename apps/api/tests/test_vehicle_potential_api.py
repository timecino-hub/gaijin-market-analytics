from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from api.clock import get_utc_clock
from api.db.session import get_session
from api.main import app
from api.routers import vehicle_potential as potential_router
from api.services.vehicle_potential import VehiclePotentialItem
from fastapi.testclient import TestClient
from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.supply_cycles import SupplyPoolEvent
from gaijin_market_analytics.vehicle_potential import evaluate_vehicle_potential
from sqlalchemy import create_engine, text

VEHICLE = "Kungstiger"
EXTERNAL_KEY = "id50268_kungstiger_potential"

# Long enough to clear VehiclePotentialConfig.minimum_history_days (120).
HISTORY_DAYS = 200


def _seed(
    database_url: str,
    *,
    external_key: str = EXTERNAL_KEY,
    name: str = VEHICLE,
    days: int = HISTORY_DAYS,
    tail_price: str | None = None,
    price: str = "100",
) -> tuple[int, datetime]:
    """A vehicle with `days` of daily history ending yesterday."""

    as_of = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = as_of - timedelta(days=days)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            item_id = int(
                connection.execute(
                    text(
                        "INSERT INTO items (external_key, name, category, is_active) "
                        "VALUES (:k, :n, 'vehicle', true) RETURNING id"
                    ),
                    {"k": external_key, "n": name},
                ).scalar_one()
            )
            import_id = int(
                connection.execute(
                    text(
                        "INSERT INTO historical_trade_imports "
                        "(item_id, pairing_id, source_url_safe, source_schema_version, "
                        " source_captured_at, source_series_sha256, "
                        " point_count_1h, point_count_1d) "
                        "VALUES (:i, :pair, :url, 'gaijin_trade_history_v1', :c, :s, "
                        " 0, 0) RETURNING id"
                    ),
                    {
                        "i": item_id,
                        "pair": f"vehicle-potential-{external_key}",
                        "url": f"https://example.invalid/{external_key}",
                        "s": "0" * 64,
                        "c": datetime.now(UTC),
                    },
                ).scalar_one()
            )
            for index in range(days):
                day = start + timedelta(days=index)
                value = (
                    tail_price
                    if tail_price is not None and index == days - 1
                    else price
                )
                connection.execute(
                    text(
                        "INSERT INTO historical_trade_buckets "
                        "(item_id, source_import_id, granularity, bucket_start_utc, "
                        " bucket_duration_seconds, vwap_price_raw, price_scale, "
                        " reported_vwap_price, reported_trade_volume, price_semantics, "
                        " volume_semantics, source_schema_version) "
                        "VALUES (:i, :m, '1d', :s, 86400, :raw, 10000, :p, 5, "
                        " 'bucket_volume_weighted_average_trade_price', "
                        " 'reported_trade_volume_unknown_unit', 'gaijin_trade_history_v1')"
                    ),
                    {
                        "i": item_id,
                        "m": import_id,
                        "s": day,
                        "raw": int(Decimal(value) * 10000),
                        "p": value,
                    },
                )
    finally:
        engine.dispose()
    return item_id, as_of


def test_a_vehicle_is_placed_against_its_own_price_history(
    client: TestClient, migrated_database: str
):
    item_id, as_of = _seed(migrated_database, tail_price="50")

    response = client.get(
        "/api/v1/vehicle-potential", params={"as_of": as_of.isoformat()}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["history_days"] == 730
    row = next(value for value in body["items"] if value["item_id"] == item_id)
    assert row["state"] == "RANKED"
    assert Decimal(row["current_price"]) == Decimal(50)
    assert Decimal(row["price_percentile"]) == Decimal(1) / Decimal(HISTORY_DAYS)
    assert Decimal(row["median_price"]) == Decimal(100)
    assert row["history_day_count"] == HISTORY_DAYS
    assert row["research_only"] is True
    assert row["version"] == "1.1.0"


def test_too_little_history_is_reported_rather_than_scored(
    client: TestClient, migrated_database: str
):
    item_id, as_of = _seed(
        migrated_database, external_key="vp-thin", name="Thin History", days=30
    )

    row = next(
        value
        for value in client.get(
            "/api/v1/vehicle-potential", params={"as_of": as_of.isoformat()}
        ).json()["items"]
        if value["item_id"] == item_id
    )

    assert row["state"] == "INSUFFICIENT_HISTORY"
    assert row["price_percentile"] is None
    assert row["current_price"] is None
    assert "vehicle_potential_not_enough_trading_history" in row["explanation_codes"]


def test_the_list_is_ordered_cheapest_against_its_own_history_first(
    client: TestClient, migrated_database: str
):
    cheap_id, as_of = _seed(
        migrated_database, external_key="vp-cheap", name="Cheap One", tail_price="10"
    )
    _seed(migrated_database, external_key="vp-dear", name="Dear One", tail_price="900")

    items = client.get(
        "/api/v1/vehicle-potential",
        params={"as_of": as_of.isoformat(), "ranked_only": "true"},
    ).json()["items"]

    assert items[0]["item_id"] == cheap_id
    percentiles = [Decimal(value["price_percentile"]) for value in items]
    assert percentiles == sorted(percentiles)


def test_an_unreadable_vehicle_never_leads_the_ranking(
    client: TestClient, migrated_database: str
):
    thin_id, as_of = _seed(
        migrated_database, external_key="vp-thin2", name="Thin Two", days=20
    )
    cheap_id, _ = _seed(
        migrated_database, external_key="vp-cheap2", name="Cheap Two", tail_price="10"
    )

    items = client.get(
        "/api/v1/vehicle-potential", params={"as_of": as_of.isoformat()}
    ).json()["items"]
    order = [value["item_id"] for value in items]

    assert order.index(cheap_id) < order.index(thin_id)


def test_ranked_only_filters_out_the_unreadable(
    client: TestClient, migrated_database: str
):
    thin_id, as_of = _seed(
        migrated_database, external_key="vp-thin3", name="Thin Three", days=20
    )

    body = client.get(
        "/api/v1/vehicle-potential",
        params={"as_of": as_of.isoformat(), "ranked_only": "true"},
    ).json()

    assert thin_id not in {value["item_id"] for value in body["items"]}
    assert all(value["state"] == "RANKED" for value in body["items"])
    assert body["ranked_count"] == body["item_count"]


def test_event_aggregates_always_travel_with_their_sample_size(
    client: TestClient, migrated_database: str
):
    item_id, as_of = _seed(migrated_database, external_key="vp-events", name=VEHICLE)

    row = next(
        value
        for value in client.get(
            "/api/v1/vehicle-potential", params={"as_of": as_of.isoformat()}
        ).json()["items"]
        if value["item_id"] == item_id
    )

    assert "completed_event_count" in row
    assert "closed_outcome_count" in row
    # An aggregate is only ever present alongside a count that supports it.
    if row["median_net_roi"] is not None:
        assert row["closed_outcome_count"] > 0
    if row["median_drawdown_ratio"] is not None:
        assert row["completed_event_count"] > 0


def test_a_single_vehicle_can_be_fetched(client: TestClient, migrated_database: str):
    item_id, as_of = _seed(migrated_database, external_key="vp-one", name="One")

    response = client.get(
        f"/api/v1/vehicle-potential/{item_id}", params={"as_of": as_of.isoformat()}
    )

    assert response.status_code == 200
    assert response.json()["item_id"] == item_id
    assert response.json()["state"] == "RANKED"


def test_an_unknown_vehicle_is_a_404(client: TestClient, migrated_database: str):
    response = client.get("/api/v1/vehicle-potential/999999")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "vehicle_potential_item_not_found"


def test_a_malformed_as_of_is_rejected(client: TestClient, migrated_database: str):
    response = client.get("/api/v1/vehicle-potential", params={"as_of": "not-a-date"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_as_of"


def test_a_vehicle_the_catalog_does_not_know_still_gets_a_price_reading(
    client: TestClient, migrated_database: str
):
    item_id, as_of = _seed(
        migrated_database,
        external_key="vp-unbound",
        name="Not A Catalogued Vehicle",
        tail_price="50",
    )

    row = next(
        value
        for value in client.get(
            "/api/v1/vehicle-potential", params={"as_of": as_of.isoformat()}
        ).json()["items"]
        if value["item_id"] == item_id
    )

    assert row["catalog_match_status"] != "MATCHED"
    assert row["state"] == "RANKED"
    assert row["price_percentile"] is not None
    assert row["completed_event_count"] == 0


# Synthetic service fixtures exercise HTTP serialization without a database.
CONTRACT_NOW = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def potential_contract_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    previous = app.dependency_overrides.copy()
    session_marker = object()

    def make_item(
        item_id: int,
        *,
        days: int = 400,
        last_day_age: int = 0,
        event_age: int = 300,
        missing_endpoint: bool = False,
        price: str = "100",
        tail_price: str = "50",
    ) -> VehiclePotentialItem:
        end = CONTRACT_NOW - timedelta(days=last_day_age)
        start = end - timedelta(days=days)
        close = CONTRACT_NOW - timedelta(days=event_age)
        endpoint = close + timedelta(days=180)
        buckets = tuple(
            HistoricalTradeBucketEvidence(
                item_id=item_id,
                granularity="1d",
                bucket_start_utc=start + timedelta(days=index),
                bucket_duration=timedelta(days=1),
                vwap_price=Decimal(tail_price if index == days - 1 else price),
                reported_trade_volume=5,
            )
            for index in range(days)
            if not (missing_endpoint and start + timedelta(days=index) == endpoint)
        )
        event = SupplyPoolEvent(
            event_key=f"synthetic-{item_id}",
            family="synthetic",
            known_at=close - timedelta(days=20),
            opens_at=close - timedelta(days=10),
            closes_at=close,
        )
        return VehiclePotentialItem(
            item_id=item_id,
            external_key=f"synthetic-potential-{item_id}",
            item_name=f"Synthetic Vehicle {item_id}",
            category="vehicle",
            catalog_match_status="MATCHED",
            matched_catalog_name=f"Synthetic Vehicle {item_id}",
            reading=evaluate_vehicle_potential(
                item_id=item_id,
                as_of=CONTRACT_NOW,
                historical_buckets=buckets,
                events=(event,),
            ),
        )

    items = [
        make_item(1, missing_endpoint=True),
        make_item(2, days=30, event_age=10),
        make_item(3, last_day_age=30, event_age=20),
        make_item(4),
        make_item(5, price="117.64718", tail_price="100.0001"),
    ]

    async def fake_list(**kwargs: Any) -> list[VehiclePotentialItem]:
        assert kwargs["session"] is session_marker
        assert kwargs["as_of"] == CONTRACT_NOW
        selected = items
        if kwargs.get("item_id") is not None:
            selected = [
                value for value in selected if value.item_id == kwargs["item_id"]
            ]
        if kwargs.get("ranked_only"):
            selected = [value for value in selected if value.reading.state == "RANKED"]
        return selected

    async def fake_session() -> Any:
        yield session_marker

    monkeypatch.setattr(potential_router, "list_vehicle_potential", fake_list)
    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_utc_clock] = lambda: lambda: CONTRACT_NOW
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _assert_no_floats(value: Any) -> None:
    assert not isinstance(value, float)
    if isinstance(value, dict):
        for member in value.values():
            _assert_no_floats(member)
    elif isinstance(value, list):
        for member in value:
            _assert_no_floats(member)


def test_target_reference_serializes_money_as_exact_decimal_strings(
    potential_contract_client: TestClient,
) -> None:
    response = potential_contract_client.get("/api/v1/vehicle-potential/1")

    assert response.status_code == 200
    row = response.json()
    target = row["target_reference"]
    assert target["cost_basis"] == "LATEST_COMPLETED_DAY_AVERAGE_PROXY"
    assert target["reference_cost"] == "50"
    assert target["fee_rate"] == "0.15"
    assert target["preferred_offer"] == "88.24"
    assert target["minimum_offer"] == "82.36"
    assert target["median_net_roi"] == "0.70"
    assert target["median_supports_preferred"] is True
    assert target["median_supports_minimum"] is True
    assert target["preferred_within_market_cap"] is True
    assert target["minimum_within_market_cap"] is True
    assert row["recent_trading_day_count"] == 30
    assert row["version"] == "1.1.0"
    assert row["research_only"] is True
    _assert_no_floats(row)


@pytest.mark.parametrize(
    "item_id,state", [(2, "INSUFFICIENT_HISTORY"), (3, "NO_CURRENT_PRICE")]
)
def test_unreadable_states_keep_target_reference_unavailable(
    potential_contract_client: TestClient, item_id: int, state: str
) -> None:
    response = potential_contract_client.get(f"/api/v1/vehicle-potential/{item_id}")

    assert response.status_code == 200
    row = response.json()
    assert row["state"] == state
    assert row["target_reference"] is None
    assert row["recent_trading_day_count"] is None
    _assert_no_floats(row)


def test_event_statuses_preserve_missing_price_and_unfinished_horizon(
    potential_contract_client: TestClient,
) -> None:
    response = potential_contract_client.get("/api/v1/vehicle-potential")

    assert response.status_code == 200
    rows = {row["item_id"]: row for row in response.json()["items"]}
    assert rows[1]["events"][0]["outcome_status"] == "MISSING_EXIT_PRICE"
    assert rows[2]["events"][0]["outcome_status"] == "PENDING_HORIZON"
    assert rows[3]["events"][0]["outcome_status"] == "NO_ENTRY_PRICE"
    assert rows[4]["events"][0]["outcome_status"] == "OBSERVED"
    for item_id in (1, 2, 3):
        record = rows[item_id]["events"][0]
        assert record["outcome_closed"] is False
        assert record["exit_price"] is None
        assert record["net_roi"] is None
        assert rows[item_id]["closed_outcome_count"] == 0
    assert rows[4]["events"][0]["outcome_closed"] is True
    assert rows[4]["closed_outcome_count"] == 1
    assert rows[1]["events"][0]["entry_price_day"] == (
        CONTRACT_NOW - timedelta(days=300)
    ).isoformat().replace("+00:00", "Z")
    assert rows[3]["events"][0]["entry_price_day"] is None


def test_small_median_return_remains_a_plain_decimal_string(
    potential_contract_client: TestClient,
) -> None:
    response = potential_contract_client.get("/api/v1/vehicle-potential/5")

    assert response.status_code == 200
    target = response.json()["target_reference"]
    for name in (
        "reference_cost",
        "fee_rate",
        "preferred_offer",
        "minimum_offer",
        "median_net_roi",
    ):
        assert "e" not in target[name].lower()
    assert Decimal(target["median_net_roi"]) < Decimal(0)
    assert Decimal(target["median_net_roi"]) > Decimal("-0.000001")


def test_openapi_declares_reference_money_strings_and_outcome_statuses(
    potential_contract_client: TestClient,
) -> None:
    response = potential_contract_client.get("/openapi.json")
    assert response.status_code == 200
    schemas = response.json()["components"]["schemas"]
    target = schemas["VehicleTargetReferenceResponse"]["properties"]
    for name in (
        "reference_cost",
        "fee_rate",
        "preferred_offer",
        "minimum_offer",
        "median_net_roi",
    ):
        assert target[name]["type"] == "string"
    assert schemas["VehicleEventRecordResponse"]["properties"]["outcome_status"][
        "enum"
    ] == ["OBSERVED", "PENDING_HORIZON", "MISSING_EXIT_PRICE", "NO_ENTRY_PRICE"]
