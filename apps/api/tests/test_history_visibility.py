"""Real SQL checks using synthetic history, without a production database."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from api.clock import get_utc_clock
from api.db.models import Item
from api.db.session import get_session
from api.routers.data_status import router as data_status_router
from api.routers.items import _item_summary
from api.schemas.items import HistorySummaryResponse
from api.services.history_summary import HistorySummary, load_history_summaries
from api.services.items import CatalogOrderBookData, ItemWithLatestSnapshot
from fastapi import FastAPI
from fastapi.testclient import TestClient
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
from sqlalchemy.pool import StaticPool


class VisibilityDatabase:
    """Use the service's actual SQL with an async adapter over local SQLite."""

    def __init__(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Item.__table__.create(self.engine)
        metadata = MetaData()
        self.history = Table(
            "historical_trade_buckets",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("item_id", Integer),
            Column("granularity", String),
            Column("bucket_start_utc", DateTime),
            Column("reported_vwap_price", Numeric(18, 4)),
        )
        self.imports = Table(
            "historical_trade_imports",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("source_captured_at", DateTime),
            Column("imported_at", DateTime),
        )
        metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.statements = []

    def seed(self, *, items=(), buckets=(), captures=()) -> None:
        with self.engine.begin() as connection:
            if items:
                connection.execute(
                    Item.__table__.insert(),
                    [
                        {
                            "id": item_id,
                            "external_key": f"synthetic-visibility-{item_id}",
                            "name": f"Synthetic vehicle {item_id}",
                            "category": "UNKNOWN",
                        }
                        for item_id in items
                    ],
                )
            if buckets:
                connection.execute(self.history.insert(), buckets)
            if captures:
                connection.execute(self.imports.insert(), captures)

    async def execute(self, statement):
        self.statements.append(statement)
        return self.session.execute(statement)

    def close(self) -> None:
        self.session.close()
        self.engine.dispose()


@pytest.fixture
def database() -> Iterator[VisibilityDatabase]:
    database = VisibilityDatabase()
    yield database
    database.close()


def _bucket(item_id: int, day: datetime, price: str = "12.3401", *, granularity="1d"):
    return {
        "item_id": item_id,
        "granularity": granularity,
        "bucket_start_utc": day,
        "reported_vwap_price": Decimal(price),
    }


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _request_status(database: VisibilityDatabase, now: datetime):
    app = FastAPI()
    app.include_router(data_status_router)
    app.dependency_overrides[get_session] = lambda: database
    app.dependency_overrides[get_utc_clock] = lambda: lambda: now
    with TestClient(app) as client:
        response = client.get("/api/v1/data-status")
    assert response.status_code == 200
    assert len(database.statements) == 1
    return response.json()


def test_summary_scopes_items_and_complete_utc_days_without_using_hourly_prices(
    database,
):
    # Tokyo's October 8 is still October 7 UTC. October 7 is not a complete day.
    now = datetime(2026, 10, 8, 0, 30, tzinfo=timezone(timedelta(hours=9)))
    day = datetime(2026, 10, 6, tzinfo=UTC)
    database.seed(
        buckets=[
            _bucket(1, day - timedelta(days=1), "9.0000"),
            _bucket(1, day, "12.3401"),
            _bucket(1, day, "999.9999", granularity="1h"),
            _bucket(1, day + timedelta(days=1), "555.0000"),
            _bucket(1, day + timedelta(days=2), "777.0000"),
            _bucket(2, day, "0.0001"),
            _bucket(3, day, "88.8800"),
        ]
    )

    summaries = asyncio.run(
        load_history_summaries(database, item_ids=[1, 2, 4], as_of=now)
    )

    assert len(database.statements) == 1
    assert set(summaries) == {1, 2}
    assert summaries[1].daily_count == 2
    assert _utc(summaries[1].first_day) == day - timedelta(days=1)
    assert _utc(summaries[1].latest_day) == day
    assert summaries[1].latest_daily_average == Decimal("12.3401")
    assert summaries[1].latest_daily_average.as_tuple().exponent == -4
    assert summaries[2].daily_count == 1
    assert summaries[2].latest_daily_average == Decimal("0.0001")


@pytest.mark.parametrize(
    ("now", "latest_day", "count"),
    [
        (datetime(2026, 10, 7, 23, 59, 59, 999999, tzinfo=UTC), 6, 1),
        (datetime(2026, 10, 8, tzinfo=UTC), 7, 2),
    ],
)
def test_summary_includes_a_daily_bucket_only_when_its_day_finishes(
    database, now, latest_day, count
):
    database.seed(
        buckets=[
            _bucket(1, datetime(2026, 10, 6, tzinfo=UTC), "10.0000"),
            _bucket(1, datetime(2026, 10, 7, tzinfo=UTC), "20.0000"),
        ]
    )
    summaries = asyncio.run(load_history_summaries(database, item_ids=[1], as_of=now))
    assert summaries[1].daily_count == count
    assert _utc(summaries[1].latest_day) == datetime(2026, 10, latest_day, tzinfo=UTC)
    assert summaries[1].latest_daily_average == Decimal(
        "10.0000" if count == 1 else "20.0000"
    )


def test_no_daily_history_has_no_summary_and_empty_selection_does_not_query(database):
    now = datetime(2026, 10, 8, tzinfo=UTC)
    assert asyncio.run(load_history_summaries(database, item_ids=[], as_of=now)) == {}
    assert database.statements == []
    database.seed(
        buckets=[
            _bucket(1, now - timedelta(days=1), granularity="1h"),
            _bucket(1, now),
            _bucket(1, now + timedelta(days=1)),
        ]
    )
    assert (
        asyncio.run(load_history_summaries(database, item_ids=[1, 2], as_of=now)) == {}
    )
    assert len(database.statements) == 1


def test_summary_rejects_timezone_free_cutoff(database):
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(
            load_history_summaries(
                database,
                item_ids=[1],
                as_of=datetime(2026, 10, 8),  # noqa: DTZ001
            )
        )
    assert database.statements == []


@pytest.mark.parametrize("price", ["12.3401", "0.0001", "1E-8", "99999999999999.9999"])
def test_history_price_serializes_as_exact_decimal_and_does_not_create_a_quote(price):
    day = datetime(2026, 9, 21, tzinfo=UTC)
    reading = HistorySummary(120, day - timedelta(days=119), day, Decimal(price))
    model = HistorySummaryResponse.model_validate(reading, from_attributes=True)
    assert model.model_dump(mode="json")["latest_daily_average"] == format(
        Decimal(price), "f"
    )
    item = Item(
        id=1,
        external_key="synthetic-visibility-1",
        name="Synthetic vehicle",
        category="UNKNOWN",
        is_active=True,
        created_at=day,
        updated_at=day,
    )
    result = _item_summary(
        ItemWithLatestSnapshot(
            item=item,
            latest_snapshot=None,
            current_order_book=CatalogOrderBookData(status="no_capture"),
            history_summary=reading,
        )
    ).model_dump(mode="json")
    assert result["history_summary"]["basis"] == "SOURCE_DAILY_TRADE_AVERAGE"
    assert result["history_summary"]["latest_daily_average"] == format(
        Decimal(price), "f"
    )
    assert result["latest_snapshot"] is None
    assert result["current_order_book_status"] == "no_capture"
    assert result["current_order_book"] is None


def test_status_counts_daily_history_and_uses_source_capture_time_without_future_records(
    database,
):
    now = datetime(2026, 10, 8, 12, tzinfo=UTC)
    day = now.replace(hour=0)
    captured = datetime(2026, 9, 21, 12, 43, tzinfo=UTC)
    database.seed(
        items=[1, 2, 3],
        buckets=[
            _bucket(1, day - timedelta(days=3)),
            _bucket(1, day - timedelta(days=1)),
            _bucket(2, day - timedelta(days=1)),
            _bucket(2, day - timedelta(days=1), granularity="1h"),
            _bucket(2, day),
            _bucket(2, day + timedelta(days=1)),
        ],
        captures=[
            {"source_captured_at": captured, "imported_at": now},
            {
                "source_captured_at": captured - timedelta(days=1),
                "imported_at": now + timedelta(days=1),
            },
            {"source_captured_at": now + timedelta(seconds=1), "imported_at": now},
        ],
    )

    result = _request_status(database, now.astimezone(timezone(timedelta(hours=9))))

    assert result["item_count"] == 3
    assert result["historical_bucket_count"] == 6
    assert result["daily_record_count"] == 3
    assert _utc(
        datetime.fromisoformat(result["latest_complete_day"])
    ) == day - timedelta(days=1)
    assert _utc(datetime.fromisoformat(result["latest_capture_at"])) == captured
    assert _utc(datetime.fromisoformat(result["reference_as_of"])) == day
    assert result["price_age_days"] == 0
    assert result["stale"] is False


@pytest.mark.parametrize(
    ("elapsed", "age", "stale"),
    [
        (timedelta(days=14), 14, False),
        (timedelta(days=14, hours=23, minutes=59, seconds=59), 14, False),
        (timedelta(days=15), 15, True),
    ],
)
def test_status_preserves_existing_whole_day_14_day_freshness_boundary(
    database, elapsed, age, stale
):
    day = datetime(2026, 9, 21, tzinfo=UTC)
    database.seed(items=[1], buckets=[_bucket(1, day)])
    result = _request_status(database, day + timedelta(days=1) + elapsed)
    assert result["price_age_days"] == age
    assert result["stale"] is stale
    assert _utc(datetime.fromisoformat(result["reference_as_of"])) == day + timedelta(
        days=1
    )


def test_status_without_complete_daily_or_capture_history_retains_null_values(database):
    now = datetime(2026, 10, 8, 12, tzinfo=UTC)
    day = now.replace(hour=0)
    database.seed(
        items=[1],
        buckets=[
            _bucket(1, day),
            _bucket(1, day - timedelta(days=1), granularity="1h"),
        ],
        captures=[
            {"source_captured_at": now + timedelta(seconds=1), "imported_at": now}
        ],
    )
    result = _request_status(database, now)
    assert result["item_count"] == 1
    assert result["historical_bucket_count"] == 2
    assert result["daily_record_count"] == 0
    assert result["latest_complete_day"] is None
    assert result["latest_capture_at"] is None
    assert result["reference_as_of"] is None
    assert result["price_age_days"] is None
    assert result["stale"] is True


def test_empty_database_status_is_not_a_synthetic_price(database):
    result = _request_status(database, datetime(2026, 10, 8, tzinfo=UTC))
    assert result == {
        "item_count": 0,
        "historical_bucket_count": 0,
        "daily_record_count": 0,
        "latest_complete_day": None,
        "latest_capture_at": None,
        "reference_as_of": None,
        "price_age_days": None,
        "stale": True,
    }
