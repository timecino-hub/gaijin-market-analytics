"""Exact-request reuse and normal-import invalidation, with synthetic fixtures."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from api.clock import get_utc_clock
from api.db.models import Item
from api.db.session import get_session
from api.main import app
from api.routers import strategy_evidence, vehicle_potential
from api.services.reading_cache import (
    BoundedReadingCache,
    ReadingRevision,
    get_reading_revision,
)
from api.services.strategy_evidence import StrategyEvidenceLedger
from api.services.vehicle_potential import (
    VehiclePotentialItem,
    VehiclePotentialServiceError,
)
from fastapi.testclient import TestClient
from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.strategy_evidence import summarise_strategy_evidence
from gaijin_market_analytics.vehicle_potential import evaluate_vehicle_potential
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    Table,
    create_engine,
)

NOW = datetime(2026, 9, 22, tzinfo=UTC)


def test_cached_results_are_copies_and_default_capacity_is_eight():
    cache = BoundedReadingCache()
    source = {"prices": [Decimal("12.3400")]}
    calls = 0

    async def compute():
        nonlocal calls
        calls += 1
        return source

    async def exercise():
        first = await cache.get_or_compute(0, compute)
        first["prices"].append(Decimal(999))
        assert await cache.get_or_compute(0, compute) == source
        for key in range(1, 9):
            await cache.get_or_compute(key, compute)
        assert calls == 9
        await cache.get_or_compute(8, compute)
        assert calls == 9
        await cache.get_or_compute(0, compute)
        assert calls == 10

    asyncio.run(exercise())
    assert source == {"prices": [Decimal("12.3400")]}


def test_cache_expires_at_the_deadline_and_hits_do_not_extend_it():
    clock = [0.0]
    cache = BoundedReadingCache(clock=lambda: clock[0])
    calls = 0

    async def compute():
        nonlocal calls
        calls += 1
        return calls

    async def exercise():
        assert await cache.get_or_compute("same", compute) == 1
        clock[0] = 3599.0
        assert await cache.get_or_compute("same", compute) == 1
        clock[0] = 3600.0
        assert await cache.get_or_compute("same", compute) == 2

    asyncio.run(exercise())


def test_concurrent_same_keys_share_work_and_receive_independent_copies():
    cache = BoundedReadingCache()
    calls = 0

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def compute():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"rows": [1]}

        first = asyncio.create_task(cache.get_or_compute("same", compute))
        await started.wait()
        second = asyncio.create_task(cache.get_or_compute("same", compute))
        await asyncio.sleep(0)
        release.set()
        first_result, second_result = await asyncio.gather(first, second)
        first_result["rows"].append(2)
        assert second_result == {"rows": [1]}
        assert await cache.get_or_compute("same", compute) == {"rows": [1]}

    asyncio.run(exercise())
    assert calls == 1


def test_cancelled_waiter_does_not_cancel_shared_computation():
    cache = BoundedReadingCache()

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def compute():
            started.set()
            await release.wait()
            return 7

        owner = asyncio.create_task(cache.get_or_compute("same", compute))
        await started.wait()
        waiter = asyncio.create_task(cache.get_or_compute("same", compute))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        assert await owner == 7
        assert await cache.get_or_compute("same", compute) == 7

    asyncio.run(exercise())


def test_failed_work_is_shared_with_waiters_but_never_cached():
    cache = BoundedReadingCache()
    calls = 0

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def fail():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            raise ValueError("synthetic failure")

        owner = asyncio.create_task(cache.get_or_compute("same", fail))
        await started.wait()
        waiter = asyncio.create_task(cache.get_or_compute("same", fail))
        await asyncio.sleep(0)
        release.set()
        errors = await asyncio.gather(owner, waiter, return_exceptions=True)
        assert all(isinstance(error, ValueError) for error in errors)
        assert calls == 1
        with pytest.raises(ValueError, match="synthetic failure"):
            await cache.get_or_compute("same", fail)
        assert calls == 2

    asyncio.run(exercise())


def test_revision_detects_old_price_correction_without_new_market_date_or_row_count():
    engine = create_engine("sqlite://")
    Item.__table__.create(engine)
    metadata = MetaData()
    imports = Table(
        "historical_trade_imports",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("imported_at", DateTime),
    )
    buckets = Table(
        "historical_trade_buckets",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("bucket_start_utc", DateTime),
        Column("reported_vwap_price", Numeric(18, 4)),
    )
    metadata.create_all(engine)

    class RevisionSession:
        def __init__(self, connection):
            self.connection = connection
            self.calls = 0

        async def execute(self, statement):
            self.calls += 1
            return self.connection.execute(statement)

        def get_bind(self):
            return engine

    try:
        with engine.begin() as connection:
            connection.execute(
                Item.__table__.insert(),
                {
                    "id": 1,
                    "external_key": "synthetic-revision",
                    "name": "Synthetic",
                    "category": "vehicle",
                    "updated_at": NOW,
                },
            )
            connection.execute(imports.insert(), {"id": 1, "imported_at": NOW})
            connection.execute(
                buckets.insert(),
                [
                    {
                        "id": 1,
                        "bucket_start_utc": NOW - timedelta(days=2),
                        "reported_vwap_price": Decimal(10),
                    },
                    {
                        "id": 2,
                        "bucket_start_utc": NOW - timedelta(days=1),
                        "reported_vwap_price": Decimal(20),
                    },
                ],
            )
            session = RevisionSession(connection)
            before = asyncio.run(get_reading_revision(session))
            original_dates = connection.execute(buckets.select()).all()
            connection.execute(
                buckets.update()
                .where(buckets.c.id == 1)
                .values(reported_vwap_price=Decimal(11))
            )
            # Even an equal import timestamp is distinguished by the new id.
            connection.execute(imports.insert(), {"id": 2, "imported_at": NOW})
            after = asyncio.run(get_reading_revision(session))
            revised_dates = connection.execute(buckets.select()).all()
            assert len(revised_dates) == len(original_dates)
            assert max(row.bucket_start_utc for row in revised_dates) == max(
                row.bucket_start_utc for row in original_dates
            )
            assert before.item_count == after.item_count == 1
            assert before.latest_imported_at == after.latest_imported_at
            assert before != after
            connection.execute(
                Item.__table__.update()
                .where(Item.id == 1)
                .values(updated_at=NOW + timedelta(seconds=1))
            )
            assert asyncio.run(get_reading_revision(session)) != after
            assert session.calls == 3
    finally:
        engine.dispose()


@pytest.fixture
def cached_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, dict[str, Any]]]:
    previous = app.dependency_overrides.copy()
    marker = object()
    state: dict[str, Any] = {
        "revision": ReadingRevision(marker, 1, NOW, 1, NOW),
        "potential_calls": [],
        "strategy_calls": [],
        "price": Decimal(50),
        "fail": False,
    }

    async def fake_session():
        yield marker

    async def fake_potential(**kwargs):
        state["potential_calls"].append(kwargs)
        if state["fail"]:
            raise VehiclePotentialServiceError("synthetic_error", "Synthetic error")
        moment = kwargs["as_of"]
        reading = evaluate_vehicle_potential(
            item_id=1,
            as_of=moment,
            config=kwargs["config"],
            historical_buckets=tuple(
                HistoricalTradeBucketEvidence(
                    item_id=1,
                    granularity="1d",
                    bucket_start_utc=NOW - timedelta(days=200 - index),
                    bucket_duration=timedelta(days=1),
                    vwap_price=state["price"] if index == 199 else Decimal(100),
                    reported_trade_volume=5,
                )
                for index in range(200)
            ),
        )
        return [
            VehiclePotentialItem(
                1,
                "synthetic-cache",
                "Synthetic",
                "vehicle",
                "NOT_MATCHED",
                None,
                reading,
            )
        ]

    async def fake_strategy(**kwargs):
        state["strategy_calls"].append(kwargs)
        return StrategyEvidenceLedger(
            summarise_strategy_evidence(
                as_of=kwargs["as_of"], outcomes=(), config=kwargs["config"]
            ),
            (),
            {},
            0,
        )

    monkeypatch.setattr(vehicle_potential, "_cache", BoundedReadingCache())
    monkeypatch.setattr(strategy_evidence, "_cache", BoundedReadingCache())
    monkeypatch.setattr(vehicle_potential, "list_vehicle_potential", fake_potential)
    monkeypatch.setattr(strategy_evidence, "build_strategy_evidence", fake_strategy)
    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_utc_clock] = lambda: lambda: NOW
    app.dependency_overrides[get_reading_revision] = lambda: state["revision"]
    try:
        with TestClient(app) as client:
            yield client, state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_potential_http_cache_uses_exact_time_filters_configuration_and_item(
    cached_client, monkeypatch
):
    client, state = cached_client
    url = "/api/v1/vehicle-potential"
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200
    assert len(state["potential_calls"]) == 1
    for params in (
        {"as_of": (NOW + timedelta(microseconds=1)).isoformat()},
        {"ranked_only": "true"},
        {"include_inactive": "true"},
    ):
        assert client.get(url, params=params).status_code == 200
    assert len(state["potential_calls"]) == 4
    from gaijin_market_analytics.vehicle_potential import VehiclePotentialConfig

    monkeypatch.setattr(
        vehicle_potential,
        "VehiclePotentialConfig",
        lambda: VehiclePotentialConfig(history_days=365),
    )
    assert client.get(url).status_code == 200
    assert len(state["potential_calls"]) == 5
    assert client.get(url + "/1").status_code == 200
    assert client.get(url + "/1").status_code == 200
    assert client.get(url + "/2").status_code == 200
    assert len(state["potential_calls"]) == 7


def test_potential_revision_and_database_changes_invalidate_the_http_cache(
    cached_client,
):
    client, state = cached_client
    url = "/api/v1/vehicle-potential/1"
    original = client.get(url).json()
    state["price"] = Decimal(60)
    state["revision"] = ReadingRevision(state["revision"].database_bind, 2, NOW, 1, NOW)
    revised = client.get(url).json()
    assert original["current_price"] == "50"
    assert revised["current_price"] == "60"
    assert len(state["potential_calls"]) == 2
    state["revision"] = ReadingRevision(object(), 2, NOW, 1, NOW)
    assert client.get(url).status_code == 200
    assert len(state["potential_calls"]) == 3


def test_failed_http_readings_are_not_cached(cached_client):
    client, state = cached_client
    state["fail"] = True
    assert client.get("/api/v1/vehicle-potential").status_code == 400
    assert client.get("/api/v1/vehicle-potential").status_code == 400
    assert len(state["potential_calls"]) == 2
    state["fail"] = False
    assert client.get("/api/v1/vehicle-potential").status_code == 200
    assert len(state["potential_calls"]) == 3


def test_strategy_cache_keeps_exact_time_and_budget_representation(cached_client):
    client, state = cached_client
    url = "/api/v1/strategy-evidence"
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200
    assert len(state["strategy_calls"]) == 1
    assert client.get(url, params={"budget": "200"}).json()["budget"]["budget"] == "200"
    assert (
        client.get(url, params={"budget": "100.00"}).json()["budget"]["budget"]
        == "100.00"
    )
    assert (
        client.get(
            url, params={"as_of": (NOW + timedelta(microseconds=1)).isoformat()}
        ).status_code
        == 200
    )
    assert len(state["strategy_calls"]) == 4
    state["revision"] = ReadingRevision(state["revision"].database_bind, 2, NOW, 1, NOW)
    assert client.get(url).status_code == 200
    assert len(state["strategy_calls"]) == 5
