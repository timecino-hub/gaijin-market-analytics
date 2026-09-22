from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
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
                        "raw": int(float(value) * 10000),
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
    assert Decimal(row["current_price"]) == Decimal("50")
    assert Decimal(row["price_percentile"]) == Decimal(1) / Decimal(HISTORY_DAYS)
    assert Decimal(row["median_price"]) == Decimal("100")
    assert row["history_day_count"] == HISTORY_DAYS
    assert row["research_only"] is True
    assert row["version"] == "1.0.0"


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
    cheap_id, as_of = _seed(migrated_database, external_key="vp-cheap", name="Cheap One", tail_price="10")
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
