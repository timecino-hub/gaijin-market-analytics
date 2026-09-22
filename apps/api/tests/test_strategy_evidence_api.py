"""The general strategy's own ledger, served over HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

# Bound in the supply-pool catalog to a single event: opens 2026-04-09 11:00Z,
# closes 2026-05-06 11:00Z. The entry is the first UTC day that *starts* after
# the close, so it is the 7th and not the 6th — the 6th was already trading
# while the event was still open.
VEHICLE = "Kungstiger"
ENTRY_DAY = datetime(2026, 5, 7, tzinfo=UTC)
HISTORY_START = datetime(2026, 3, 1, tzinfo=UTC)
# 2026-05-07 + 180 days is 2026-11-03, so this clock leaves the horizon closed.
SETTLED_AS_OF = datetime(2026, 12, 1, tzinfo=UTC)
# Before the horizon closes: the same event, still running.
PENDING_AS_OF = datetime(2026, 8, 1, tzinfo=UTC)


def _seed(
    database_url: str,
    *,
    external_key: str = "strategy-kungstiger",
    name: str = VEHICLE,
    entry_price: str = "50",
    baseline_price: str = "100",
    after_price: str = "100",
    through: datetime = SETTLED_AS_OF,
) -> int:
    """A vehicle whose price drops on the first full day after its event closes."""

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
                        "pair": f"strategy-{external_key}",
                        "url": f"https://example.invalid/{external_key}",
                        "s": "0" * 64,
                        "c": datetime.now(UTC),
                    },
                ).scalar_one()
            )
            day = HISTORY_START
            while day < through:
                if day < ENTRY_DAY:
                    value = baseline_price
                elif day == ENTRY_DAY:
                    value = entry_price
                else:
                    value = after_price
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
                day += timedelta(days=1)
    finally:
        engine.dispose()
    return item_id


def _get(client: TestClient, as_of: datetime = SETTLED_AS_OF) -> dict:
    response = client.get(
        "/api/v1/strategy-evidence", params={"as_of": as_of.isoformat()}
    )
    assert response.status_code == 200
    return response.json()


def _horizon(body: dict, days: int) -> dict:
    return next(value for value in body["horizons"] if value["horizon_days"] == days)


def test_a_settled_event_reports_its_entry_and_its_realized_return(
    client: TestClient, migrated_database: str
):
    item_id = _seed(migrated_database)

    body = _get(client)
    row = next(value for value in body["events"] if value["item_id"] == item_id)

    assert row["state"] == "EVALUATED"
    assert Decimal(row["pre_event_baseline"]) == Decimal("100")
    assert Decimal(row["entry_price"]) == Decimal("50")
    assert Decimal(row["drawdown_ratio"]) == Decimal("0.5")
    assert row["selected"] is True
    assert row["horizon_state"] == "SETTLED"
    # Bought at 50, sold at 100, minus the 15% sale fee.
    assert Decimal(row["net_roi"]) == Decimal("0.7")
    assert body["version"] == "1.0.0"
    assert body["research_only"] is True


def test_the_selected_and_rejected_halves_add_up_to_the_evaluated_events(
    client: TestClient, migrated_database: str
):
    _seed(migrated_database)
    # A shallow drawdown: the same event, barely moved.
    _seed(migrated_database, external_key="strategy-shallow", entry_price="95")

    body = _get(client)

    assert body["evaluated_event_count"] == 2
    assert body["selected_event_count"] == 1
    assert body["rejected_event_count"] == 1
    horizon = _horizon(body, 180)
    assert horizon["all_events"]["sample_size"] == 2
    assert (
        horizon["selected"]["sample_size"] + horizon["rejected"]["sample_size"]
        == horizon["all_events"]["sample_size"]
    )


def test_every_summary_carries_the_vehicle_count_behind_its_events(
    client: TestClient, migrated_database: str
):
    _seed(migrated_database)
    _seed(migrated_database, external_key="strategy-second", entry_price="40")

    horizon = _horizon(_get(client), 180)

    assert horizon["all_events"]["sample_size"] == 2
    assert horizon["all_events"]["vehicle_count"] == 2
    assert horizon["selected"]["vehicle_count"] == 2


def test_an_unfinished_horizon_is_counted_rather_than_dropped(
    client: TestClient, migrated_database: str
):
    _seed(migrated_database, through=PENDING_AS_OF)

    body = _get(client, as_of=PENDING_AS_OF)
    horizon = _horizon(body, 180)

    assert horizon["pending_count"] == 1
    assert horizon["all_events"]["sample_size"] == 0
    assert horizon["all_events"]["median"] is None
    # The shorter horizons have closed, so the event is present, not missing.
    assert _horizon(body, 60)["all_events"]["sample_size"] == 1
    row = next(value for value in body["events"] if value["state"] == "EVALUATED")
    assert row["horizon_state"] == "PENDING"
    assert row["net_roi"] is None
    assert (
        "strategy_evidence_some_events_have_not_finished_their_horizon"
        in body["explanation_codes"]
    )


def test_the_exit_frontier_scores_every_rule_on_the_same_events(
    client: TestClient, migrated_database: str
):
    _seed(migrated_database)

    frontier = _get(client)["exit_frontier"]
    kinds = {value["kind"] for value in frontier}

    assert kinds == {"FIXED_HOLD", "TAKE_PROFIT", "SPLIT_HALF"}
    assert frontier[0]["kind"] == "FIXED_HOLD"
    assert frontier[0]["target_net_roi"] is None
    assert {value["summary"]["sample_size"] for value in frontier} == {1}
    # A filled offer realizes exactly its target, whatever the market did after.
    taken = next(
        value
        for value in frontier
        if value["kind"] == "TAKE_PROFIT" and value["target_net_roi"] == "0.25"
    )
    assert Decimal(taken["hit_ratio"]) == Decimal("1")
    assert Decimal(taken["summary"]["median"]) == Decimal("0.25")


def test_a_vehicle_the_catalog_cannot_bind_is_counted_not_hidden(
    client: TestClient, migrated_database: str
):
    _seed(migrated_database)
    _seed(
        migrated_database,
        external_key="strategy-unbound",
        name="Nothing The Catalog Knows",
    )

    body = _get(client)

    assert body["unmatched_item_count"] == 1
    assert body["vehicle_count"] == 1
    assert all(value["item_name"] == VEHICLE for value in body["events"])


def test_an_event_without_a_pre_event_baseline_is_reported_as_such(
    client: TestClient, migrated_database: str
):
    # History starts after the event opened, so there is no window to read.
    _seed(migrated_database)
    engine = create_engine(migrated_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM historical_trade_buckets "
                    "WHERE bucket_start_utc < :cut"
                ),
                {"cut": datetime(2026, 4, 20, tzinfo=UTC)},
            )
    finally:
        engine.dispose()

    body = _get(client)
    row = body["events"][0]

    assert row["state"] == "NO_PRE_EVENT_BASELINE"
    assert row["pre_event_baseline"] is None
    assert row["drawdown_ratio"] is None
    assert row["selected"] is False
    assert body["evaluated_event_count"] == 0
    assert body["no_baseline_count"] == 1


def test_an_empty_cohort_answers_with_zeroes_rather_than_an_error(
    client: TestClient, migrated_database: str
):
    body = _get(client)

    assert body["completed_event_count"] == 0
    assert body["vehicle_count"] == 0
    assert body["events"] == []
    assert all(value["all_events"]["sample_size"] == 0 for value in body["horizons"])
    assert all(value["summary"]["sample_size"] == 0 for value in body["exit_frontier"])


def test_a_malformed_as_of_is_refused(client: TestClient, migrated_database: str):
    response = client.get("/api/v1/strategy-evidence", params={"as_of": "not-a-date"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_as_of"
