import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from gaijin_market_analytics.backtesting import BacktestConfig
from sqlalchemy import create_engine, inspect, text

from api.analytics_registry import get_strategy_registry
from api.config import Settings
from api.db.session import async_session_factory
from api.services.backtesting import BacktestDataNotFoundError, ItemBacktestService
from api.services.items import ItemNotFoundError


def _insert_item(database_url: str, *, external_key: str) -> int:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            return int(
                conn.execute(
                    text(
                        """
                        INSERT INTO items (external_key, name, category)
                        VALUES (:external_key, :external_key, 'vehicle')
                        RETURNING id
                        """
                    ),
                    {"external_key": external_key},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _insert_snapshot(
    database_url: str,
    *,
    item_id: int,
    observed_at: str,
    best_ask: str = "10.000000",
    best_bid: str | None = "9.000000",
) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO market_snapshots (
                        item_id,
                        observed_at,
                        best_ask,
                        best_bid,
                        ask_count,
                        bid_count,
                        estimated_volume
                    )
                    VALUES (
                        :item_id,
                        :observed_at,
                        :best_ask,
                        :best_bid,
                        20,
                        20,
                        100.000000
                    )
                    """
                ),
                {
                    "item_id": item_id,
                    "observed_at": observed_at,
                    "best_ask": Decimal(best_ask),
                    "best_bid": Decimal(best_bid) if best_bid is not None else None,
                },
            )
    finally:
        engine.dispose()


def _counts(database_url: str) -> tuple[int, int]:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            item_count = int(conn.execute(text("SELECT count(*) FROM items")).scalar_one())
            snapshot_count = int(
                conn.execute(text("SELECT count(*) FROM market_snapshots")).scalar_one()
            )
            return item_count, snapshot_count
    finally:
        engine.dispose()


def _table_names(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _settings(database_url: str) -> Settings:
    return Settings.model_construct(
        app_env="development",
        database_url=database_url,
        cors_allowed_origins="http://localhost:3000",
        analytics_maximum_snapshot_age_hours=24 * 8,
        analytics_minimum_snapshot_count=1,
    )


def _config() -> BacktestConfig:
    return BacktestConfig(
        strategy_name="rule_based",
        strategy_version="1.0.0",
        lookback_horizon_days=7,
        forward_horizon_days=7,
        start_at=datetime(2026, 1, 10, tzinfo=UTC),
        end_at=datetime(2026, 1, 10, tzinfo=UTC),
        cadence_days=7,
        maximum_snapshot_age_hours=24 * 8,
        minimum_snapshot_count=1,
    )


async def _run_service(database_url: str, item_id: int):
    async with async_session_factory() as session:
        service = ItemBacktestService(session, _settings(database_url), get_strategy_registry())
        return await service.backtest_item(item_id=item_id, config=_config())


def test_backtesting_service_queries_only_requested_item_and_bounded_range(
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-backtest")
    other_item_id = _insert_item(migrated_database, external_key="synthetic-other")
    for observed_at, bid in (
        ("2026-01-02T00:00:00Z", "100.000000"),
        ("2026-01-03T00:00:00Z", "8.000000"),
        ("2026-01-10T00:00:00Z", "10.000000"),
        ("2026-01-17T00:00:00Z", "13.000000"),
        ("2026-01-18T00:00:00Z", "200.000000"),
    ):
        _insert_snapshot(migrated_database, item_id=item_id, observed_at=observed_at, best_bid=bid)
    _insert_snapshot(
        migrated_database,
        item_id=other_item_id,
        observed_at="2026-01-17T00:00:00Z",
        best_bid="999.000000",
    )

    result = asyncio.run(_run_service(migrated_database, item_id))

    case = result.result.cases[0]
    assert result.item.id == item_id
    assert case.observation_count == 2
    assert case.future_observation_count == 1
    assert case.terminal_bid == Decimal("13.000000")


def test_backtesting_service_distinguishes_missing_item_and_no_snapshots(
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-empty-backtest")

    with pytest.raises(ItemNotFoundError):
        asyncio.run(_run_service(migrated_database, 999999))

    with pytest.raises(BacktestDataNotFoundError):
        asyncio.run(_run_service(migrated_database, item_id))


def test_backtesting_service_does_not_write_results_or_create_tables(
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-backtest-readonly")
    for observed_at in (
        "2026-01-03T00:00:00Z",
        "2026-01-10T00:00:00Z",
        "2026-01-17T00:00:00Z",
    ):
        _insert_snapshot(migrated_database, item_id=item_id, observed_at=observed_at)
    before = _counts(migrated_database)

    response = asyncio.run(_run_service(migrated_database, item_id))

    assert response.result.summary.total_case_count == 1
    assert _counts(migrated_database) == before
    tables = _table_names(migrated_database)
    assert "backtest_results" not in tables
    assert "analysis_results" not in tables


def test_openapi_has_no_backtest_route(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = set(response.json()["paths"])
    assert not any("backtest" in path for path in paths)
