"""Exercise the batched SQL reads against a small, synthetic local database."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from api.db.models import Item
from api.services.historical_evidence import load_historical_evidence
from api.services.strategy_evidence import build_strategy_evidence
from api.services.vehicle_potential import (
    VehiclePotentialServiceError,
    list_vehicle_potential,
)
from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.strategy_evidence import (
    evaluate_event_outcomes,
    summarise_strategy_evidence,
)
from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG
from gaijin_market_analytics.vehicle_potential import evaluate_vehicle_potential
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
)
from sqlalchemy.orm import Session


class DatabaseSession:
    """Run real SQL through the async service interface, without a DB server."""

    def __init__(self) -> None:
        self.engine = create_engine("sqlite://")
        Item.__table__.create(self.engine)
        self.history = Table(
            "historical_trade_buckets",
            MetaData(),
            Column("item_id", Integer),
            Column("granularity", String),
            Column("bucket_start_utc", DateTime),
            Column("bucket_duration_seconds", Integer),
            Column("reported_vwap_price", Numeric(18, 4)),
            Column("reported_trade_volume", Integer),
        )
        self.history.create(self.engine)
        self.session = Session(self.engine)
        self.statements = []

    def seed(self, *, items: list[dict], buckets: list[dict]) -> None:
        with self.engine.begin() as connection:
            if items:
                connection.execute(Item.__table__.insert(), items)
            if buckets:
                connection.execute(self.history.insert(), buckets)

    async def execute(self, statement):
        self.statements.append(statement)
        return self.session.execute(statement)

    async def scalars(self, statement):
        self.statements.append(statement)
        return self.session.scalars(statement)

    def close(self) -> None:
        self.session.close()
        self.engine.dispose()


@pytest.fixture
def database() -> Iterator[DatabaseSession]:
    database = DatabaseSession()
    yield database
    database.close()


def _item(item_id: int, name: str = "Unbound vehicle", *, active: bool = True):
    return {
        "id": item_id,
        "external_key": f"synthetic-history-{item_id}",
        "name": name,
        "category": "vehicle",
        "is_active": active,
    }


def _bucket(
    item_id: int,
    start: datetime,
    price: str = "12.3400",
    *,
    granularity: str = "1d",
    volume: int = 5,
):
    return {
        "item_id": item_id,
        "granularity": granularity,
        "bucket_start_utc": start,
        "bucket_duration_seconds": 3600 if granularity == "1h" else 86400,
        "reported_vwap_price": Decimal(price),
        "reported_trade_volume": volume,
    }


def _reference_buckets(buckets: list[dict], item_id: int, as_of: datetime):
    return tuple(
        HistoricalTradeBucketEvidence(
            item_id=item_id,
            granularity=row["granularity"],
            bucket_start_utc=row["bucket_start_utc"],
            bucket_duration=timedelta(seconds=row["bucket_duration_seconds"]),
            vwap_price=row["reported_vwap_price"],
            reported_trade_volume=row["reported_trade_volume"],
        )
        for row in sorted(buckets, key=lambda row: row["bucket_start_utc"])
        if row["item_id"] == item_id
        and as_of - timedelta(days=3650) <= row["bucket_start_utc"] <= as_of
    )


def _events(item_id: int, name: str):
    facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(item_id=item_id, item_name=name)
    member_keys = {membership.event_key for membership in facts.memberships}
    return tuple(
        event
        for event in facts.events
        if facts.match_status == "MATCHED" and event.event_key in member_keys
    )


def test_loader_keeps_exact_bounds_and_separates_items_in_one_query(database):
    as_of = datetime(2026, 9, 22, 12, 34, 56, 123456, tzinfo=UTC)
    lower = as_of - timedelta(days=3650)
    buckets = [
        _bucket(2, as_of - timedelta(days=1), "98.7600"),
        _bucket(1, lower - timedelta(microseconds=1), "2000"),
        _bucket(1, lower),
        _bucket(1, as_of, "12.3401", granularity="1h"),
        _bucket(1, as_of + timedelta(microseconds=1), "2000"),
        _bucket(9, as_of - timedelta(days=1), "2000"),
    ]
    database.seed(items=[], buckets=buckets)

    result = asyncio.run(
        load_historical_evidence(database, item_ids=(2, 1, 3), as_of=as_of)
    )

    assert len(database.statements) == 1
    assert set(result) == {1, 2}
    assert result[1] == _reference_buckets(buckets, 1, as_of)
    assert result[2] == _reference_buckets(buckets, 2, as_of)
    assert result[1][0].vwap_price.as_tuple().exponent == -4
    assert result[1][1].bucket_start_utc == as_of
    assert result[1][1].bucket_duration == timedelta(hours=1)
    assert all(bucket.bucket_start_utc.tzinfo is UTC for bucket in result[1])
    assert list(database.statements[0].selected_columns.keys()) == [
        "item_id",
        "granularity",
        "bucket_start_utc",
        "bucket_duration_seconds",
        "reported_vwap_price",
        "reported_trade_volume",
    ]


def test_empty_loader_selection_does_not_query(database):
    assert (
        asyncio.run(
            load_historical_evidence(
                database, item_ids=(), as_of=datetime(2026, 9, 22, tzinfo=UTC)
            )
        )
        == {}
    )
    assert database.statements == []


@pytest.mark.parametrize("item_count", [1, 4])
def test_vehicle_query_count_does_not_grow_with_the_catalog(database, item_count):
    database.seed(
        items=[_item(item_id) for item_id in range(1, item_count + 1)], buckets=[]
    )

    readings = asyncio.run(
        list_vehicle_potential(
            session=database, as_of=datetime(2026, 9, 22, tzinfo=UTC)
        )
    )

    assert len(readings) == item_count
    assert len(database.statements) == 2
    assert all(reading.reading.state == "INSUFFICIENT_HISTORY" for reading in readings)


@pytest.mark.parametrize(
    "as_of",
    [
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 9, 22, 12, tzinfo=UTC),
        datetime(2026, 12, 1, tzinfo=UTC),
    ],
)
def test_mixed_vehicle_and_strategy_readings_match_per_item_evidence(database, as_of):
    items = [
        _item(1, "Kungstiger"),
        _item(2, "Unbound vehicle"),
        _item(3, "Kungstiger", active=False),
        _item(4, "Su-8"),
    ]
    entry_day = datetime(2026, 5, 7, tzinfo=UTC)
    start = datetime(2026, 3, 1, tzinfo=UTC)
    buckets = []
    for item in items:
        for day_index in range(276):
            day = start + timedelta(days=day_index)
            price = "100" if day < entry_day else "50" if day == entry_day else "90"
            buckets.append(_bucket(item["id"], day, price))
        # The complete hourly evidence must continue to replace its daily row.
        buckets.append(_bucket(item["id"], entry_day, "45", granularity="1h"))
    database.seed(items=items, buckets=list(reversed(buckets)))

    potentials = asyncio.run(list_vehicle_potential(session=database, as_of=as_of))
    assert len(database.statements) == 2
    assert {value.item_id for value in potentials} == {1, 2, 4}
    for value in potentials:
        assert value.reading == evaluate_vehicle_potential(
            item_id=value.item_id,
            as_of=as_of,
            events=_events(value.item_id, value.item_name),
            historical_buckets=_reference_buckets(buckets, value.item_id, as_of),
        )

    database.statements.clear()
    ledger = asyncio.run(build_strategy_evidence(session=database, as_of=as_of))
    expected_outcomes = tuple(
        outcome
        for item in items
        if item["is_active"]
        for outcome in evaluate_event_outcomes(
            item_id=item["id"],
            as_of=as_of,
            events=_events(item["id"], item["name"]),
            historical_buckets=_reference_buckets(buckets, item["id"], as_of),
        )
    )
    assert len(database.statements) == 2
    assert ledger.outcomes == expected_outcomes
    assert ledger.evidence == summarise_strategy_evidence(
        as_of=as_of, outcomes=expected_outcomes
    )
    assert ledger.item_names == {1: "Kungstiger", 2: "Unbound vehicle", 4: "Su-8"}
    assert ledger.unmatched_item_count == 1
    assert database.statements[1].compile().params["item_id_1"] == [1, 4]


def test_strategy_with_no_catalog_matches_does_not_read_price_history(database):
    database.seed(items=[_item(1)], buckets=[])
    ledger = asyncio.run(
        build_strategy_evidence(
            session=database, as_of=datetime(2026, 9, 22, tzinfo=UTC)
        )
    )
    assert ledger.unmatched_item_count == 1
    assert ledger.outcomes == ()
    assert len(database.statements) == 1


def test_missing_single_vehicle_retains_its_error_without_history_query(database):
    with pytest.raises(VehiclePotentialServiceError) as error:
        asyncio.run(
            list_vehicle_potential(
                session=database,
                item_id=999,
                as_of=datetime(2026, 9, 22, tzinfo=UTC),
            )
        )
    assert error.value.code == "vehicle_potential_item_not_found"
    assert len(database.statements) == 1


def test_empty_catalog_does_not_read_price_history(database):
    as_of = datetime(2026, 9, 22, tzinfo=UTC)
    assert asyncio.run(list_vehicle_potential(session=database, as_of=as_of)) == []
    assert len(database.statements) == 1
    database.statements.clear()
    ledger = asyncio.run(build_strategy_evidence(session=database, as_of=as_of))
    assert ledger.outcomes == ()
    assert ledger.unmatched_item_count == 0
    assert len(database.statements) == 1
