from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

# The catalog places this vehicle in recurring craft events, so a history
# written around one of them produces a real signal rather than a stub.
VEHICLE = "Kungstiger"
EXTERNAL_KEY = "id50268_kungstiger_sweden"


def _catalog_event():
    from gaijin_market_analytics.supply_pool_catalog_v1 import (
        DEFAULT_SUPPLY_POOL_CATALOG as catalog,
    )

    facts = catalog.bind_item(item_id=1, item_name=VEHICLE)
    keys = {value.event_key for value in facts.memberships}
    events = sorted(
        (value for value in facts.events if value.event_key in keys),
        key=lambda value: value.opens_at,
    )
    assert events, "the bundled catalog should know this vehicle"
    return events[-1]


def _seed(database_url: str, *, post_price: str) -> tuple[int, datetime]:
    """One item whose history brackets its latest catalogued event."""

    event = _catalog_event()
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            item_id = int(
                connection.execute(
                    text(
                        "INSERT INTO items (external_key, name, category, is_active) "
                        "VALUES (:k, :n, 'vehicle', true) RETURNING id"
                    ),
                    {"k": EXTERNAL_KEY, "n": VEHICLE},
                ).scalar_one()
            )
            import_id = int(
                connection.execute(
                    text(
                        "INSERT INTO historical_trade_imports "
                        "(item_id, source_schema_version, source_series_sha256, "
                        " captured_at, point_count_1d, point_count_1h) "
                        "VALUES (:i, 'gaijin_trade_history_v1', :s, :c, 0, 0) RETURNING id"
                    ),
                    {
                        "i": item_id,
                        "s": "0" * 64,
                        "c": datetime.now(UTC),
                    },
                ).scalar_one()
            )
            duration = event.closes_at - event.opens_at
            rows = []
            for index in range(14):
                rows.append((event.opens_at - duration + timedelta(days=index), "100"))
            for index in range(3):
                rows.append((event.closes_at + timedelta(days=index), post_price))
            for start, price in rows:
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
                        "s": start,
                        "raw": int(float(price) * 10000),
                        "p": price,
                    },
                )
    finally:
        engine.dispose()
    return item_id, event.closes_at + timedelta(days=2)


def test_a_deep_drawdown_is_published_as_an_entry_candidate(client: TestClient, migrated_database: str):
    item_id, as_of = _seed(migrated_database, post_price="60")

    response = client.get(
        "/api/v1/postevent-entry", params={"as_of": as_of.isoformat()}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["policy"] == "ANNOTATE"
    row = next(value for value in body["items"] if value["item_id"] == item_id)
    assert row["state"] == "ENTRY_CANDIDATE"
    assert row["is_entry_candidate"] is True
    assert row["drawdown_ratio"] == "0.6000"
    assert row["take_profit_price"] is not None
    assert row["hold_until"] is not None


def test_a_shallow_drawdown_is_published_without_being_a_candidate(
    client: TestClient, migrated_database: str
):
    item_id, as_of = _seed(migrated_database, post_price="95")

    response = client.get(
        "/api/v1/postevent-entry", params={"as_of": as_of.isoformat()}
    )

    row = next(value for value in response.json()["items"] if value["item_id"] == item_id)
    assert row["state"] == "SHALLOW_DRAWDOWN"
    assert row["is_entry_candidate"] is False
    assert row["take_profit_price"] is None
    assert "postevent_entry_drawdown_gate_failed" in row["explanation_codes"]


def test_the_response_never_claims_a_fill_and_carries_its_caveat(
    client: TestClient, migrated_database: str
):
    item_id, as_of = _seed(migrated_database, post_price="60")

    row = next(
        value
        for value in client.get(
            "/api/v1/postevent-entry", params={"as_of": as_of.isoformat()}
        ).json()["items"]
        if value["item_id"] == item_id
    )

    assert row["research_only"] is True
    assert row["fill_claim"] is False
    assert (
        "postevent_entry_evidence_is_descriptive_not_walk_forward_validated"
        in row["explanation_codes"]
    )


def test_candidates_only_filters_the_list(client: TestClient, migrated_database: str):
    item_id, as_of = _seed(migrated_database, post_price="95")

    response = client.get(
        "/api/v1/postevent-entry",
        params={"as_of": as_of.isoformat(), "candidates_only": "true"},
    )

    assert response.status_code == 200
    assert all(value["is_entry_candidate"] for value in response.json()["items"])
    assert item_id not in {value["item_id"] for value in response.json()["items"]}


def test_a_single_item_can_be_fetched(client: TestClient, migrated_database: str):
    item_id, as_of = _seed(migrated_database, post_price="60")

    response = client.get(
        f"/api/v1/postevent-entry/{item_id}", params={"as_of": as_of.isoformat()}
    )

    assert response.status_code == 200
    assert response.json()["item_id"] == item_id
    assert response.json()["state"] == "ENTRY_CANDIDATE"


def test_an_unknown_item_is_a_404(client: TestClient, migrated_database: str):
    response = client.get("/api/v1/postevent-entry/999999")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "postevent_entry_item_not_found"


def test_a_malformed_as_of_is_rejected(client: TestClient, migrated_database: str):
    response = client.get("/api/v1/postevent-entry", params={"as_of": "not-a-date"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_as_of"


def test_an_item_the_catalog_does_not_know_is_reported_not_omitted(
    client: TestClient, migrated_database: str
):
    engine = create_engine(migrated_database)
    try:
        with engine.begin() as connection:
            item_id = int(
                connection.execute(
                    text(
                        "INSERT INTO items (external_key, name, category, is_active) "
                        "VALUES ('unknown-vehicle', 'Not A Catalogued Vehicle', "
                        "'vehicle', true) RETURNING id"
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()

    row = next(
        value
        for value in client.get("/api/v1/postevent-entry").json()["items"]
        if value["item_id"] == item_id
    )

    assert row["catalog_match_status"] != "MATCHED"
    assert row["state"] is None
    assert row["is_entry_candidate"] is False
