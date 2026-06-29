from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from gaijin_market_analytics.contracts import AnalysisRequest, AnalysisResult, MarketObservation
from gaijin_market_analytics.enums import AnalysisStatus, ReasonCode
from gaijin_market_analytics.registry import StrategyRegistry
from sqlalchemy import create_engine, inspect, text

from api.analytics_registry import get_strategy_registry
from api.clock import get_utc_clock
from api.config import Settings, get_settings
from api.db.models import MarketSnapshot
from api.routers import analysis as analysis_router


def _insert_item(
    database_url: str,
    *,
    external_key: str = "synthetic-analysis-item",
    name: str = "Synthetic Analysis Item",
) -> int:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            return int(
                conn.execute(
                    text(
                        """
                        INSERT INTO items (external_key, name, category)
                        VALUES (:external_key, :name, 'vehicle')
                        RETURNING id
                        """
                    ),
                    {"external_key": external_key, "name": name},
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
    ask_count: int | None = 20,
    bid_count: int | None = 20,
    estimated_volume: str | None = "100.000000",
) -> int:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            return int(
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
                            :ask_count,
                            :bid_count,
                            :estimated_volume
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "item_id": item_id,
                        "observed_at": observed_at,
                        "best_ask": Decimal(best_ask),
                        "best_bid": Decimal(best_bid) if best_bid is not None else None,
                        "ask_count": ask_count,
                        "bid_count": bid_count,
                        "estimated_volume": (
                            Decimal(estimated_volume) if estimated_volume is not None else None
                        ),
                    },
                ).scalar_one()
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


def _analysis_url(item_id: int, *, horizon: int = 7, fee_rate: str = "0.10", as_of: str) -> str:
    return (
        f"/api/v1/items/{item_id}/analysis"
        f"?horizon={horizon}&fee_rate={fee_rate}&as_of={as_of}"
    )


def _seed_full_window(database_url: str, *, horizon: int, as_of: datetime) -> int:
    item_id = _insert_item(
        database_url,
        external_key=f"synthetic-analysis-{horizon}",
        name=f"Synthetic Analysis {horizon}",
    )
    start = as_of - timedelta(days=horizon)
    middle = as_of - timedelta(days=max(1, horizon // 2))
    for observed_at, ask, bid in (
        (start, "10.000000", "9.000000"),
        (middle, "11.000000", "10.000000"),
        (as_of, "12.000000", "11.000000"),
    ):
        _insert_snapshot(
            database_url,
            item_id=item_id,
            observed_at=observed_at.isoformat().replace("+00:00", "Z"),
            best_ask=ask,
            best_bid=bid,
        )
    return item_id


def test_api_can_import_local_analytics_package() -> None:
    import gaijin_market_analytics

    assert gaijin_market_analytics.AnalysisHorizon.DAYS_7.value == 7


@pytest.mark.parametrize("horizon", [7, 30, 90, 180])
def test_supported_horizons_succeed(
    client: TestClient,
    migrated_database: str,
    horizon: int,
) -> None:
    as_of = datetime(2026, 6, 29, tzinfo=UTC)
    item_id = _seed_full_window(migrated_database, horizon=horizon, as_of=as_of)

    response = client.get(_analysis_url(item_id, horizon=horizon, as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["effective_inputs"]["horizon"] == horizon
    assert body["observation_count"] == 3
    assert body["strategy_name"] == "rule_based"
    assert body["strategy_version"] == "1.0.0"
    assert body["feature_version"] == "market_features_v1"


def test_query_window_uses_inclusive_bounds_and_excludes_outside_data(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database)
    for observed_at, ask in (
        ("2026-06-21T23:59:59Z", "1.000000"),
        ("2026-06-22T00:00:00Z", "2.000000"),
        ("2026-06-25T00:00:00Z", "3.000000"),
        ("2026-06-29T00:00:00Z", "4.000000"),
        ("2026-06-29T00:00:01Z", "5.000000"),
    ):
        _insert_snapshot(migrated_database, item_id=item_id, observed_at=observed_at, best_ask=ask)

    response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 200
    body = response.json()
    assert body["observation_count"] == 3
    assert body["first_observation_at"] == "2026-06-22T00:00:00Z"
    assert body["last_observation_at"] == "2026-06-29T00:00:00Z"
    assert body["current_ask"] == "4.000000"


def test_as_of_with_timezone_is_converted_to_utc(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _seed_full_window(
        migrated_database,
        horizon=7,
        as_of=datetime(2026, 6, 29, tzinfo=UTC),
    )

    response = client.get(
        _analysis_url(item_id, as_of="2026-06-29T08:00:00%2B08:00", fee_rate="0.125")
    )

    assert response.status_code == 200
    body = response.json()
    assert body["effective_inputs"]["as_of"] == "2026-06-29T00:00:00Z"
    assert body["effective_inputs"]["fee_rate"] == "0.125"


def test_missing_as_of_uses_overridable_utc_clock_only_when_needed(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _seed_full_window(
        migrated_database,
        horizon=7,
        as_of=datetime(2026, 6, 29, tzinfo=UTC),
    )

    def fixed_clock() -> datetime:
        return datetime(2026, 6, 29, tzinfo=UTC)

    from api.main import app

    app.dependency_overrides[get_utc_clock] = lambda: fixed_clock
    try:
        response = client.get(f"/api/v1/items/{item_id}/analysis?horizon=7&fee_rate=0")
    finally:
        app.dependency_overrides.pop(get_utc_clock, None)

    assert response.status_code == 200
    body = response.json()
    assert body["effective_inputs"]["as_of"] == "2026-06-29T00:00:00Z"
    assert body["effective_inputs"]["fee_rate"] == "0"


def test_explicit_as_of_is_not_overwritten_by_clock(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _seed_full_window(
        migrated_database,
        horizon=7,
        as_of=datetime(2026, 6, 29, tzinfo=UTC),
    )

    def wrong_clock() -> datetime:
        raise AssertionError("clock should not be called when as_of is explicit")

    from api.main import app

    app.dependency_overrides[get_utc_clock] = lambda: wrong_clock
    try:
        response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))
    finally:
        app.dependency_overrides.pop(get_utc_clock, None)

    assert response.status_code == 200
    assert response.json()["effective_inputs"]["as_of"] == "2026-06-29T00:00:00Z"


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("fee_rate=0.1&as_of=2026-06-29T00:00:00Z", "invalid_horizon"),
        ("horizon=8&fee_rate=0.1&as_of=2026-06-29T00:00:00Z", "invalid_horizon"),
        ("horizon=7&as_of=2026-06-29T00:00:00Z", "invalid_fee_rate"),
        ("horizon=7&fee_rate=-0.1&as_of=2026-06-29T00:00:00Z", "invalid_fee_rate"),
        ("horizon=7&fee_rate=1&as_of=2026-06-29T00:00:00Z", "invalid_fee_rate"),
        ("horizon=7&fee_rate=abc&as_of=2026-06-29T00:00:00Z", "invalid_fee_rate"),
        ("horizon=7&fee_rate=0.1&as_of=2026-06-29T00:00:00", "invalid_as_of"),
    ],
)
def test_invalid_query_parameters_use_stable_business_errors(
    client: TestClient,
    migrated_database: str,
    query: str,
    code: str,
) -> None:
    item_id = _insert_item(migrated_database, external_key=f"synthetic-{code}", name=code)

    response = client.get(f"/api/v1/items/{item_id}/analysis?{query}")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == code


def test_missing_item_returns_404(client: TestClient) -> None:
    response = client.get(
        "/api/v1/items/999999/analysis"
        "?horizon=7&fee_rate=0.10&as_of=2026-06-29T00:00:00Z"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "item_not_found",
            "message": "The requested item was not found.",
        }
    }


def test_empty_snapshots_return_http_200_insufficient_data(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-empty-analysis", name="Empty")

    response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_data"
    assert "insufficient_snapshots" in body["reason_codes"]
    assert body["observation_count"] == 0


def test_insufficient_snapshot_count_returns_http_200(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-two-snapshots", name="Two")
    _insert_snapshot(migrated_database, item_id=item_id, observed_at="2026-06-22T00:00:00Z")
    _insert_snapshot(migrated_database, item_id=item_id, observed_at="2026-06-29T00:00:00Z")

    response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_data"
    assert "insufficient_snapshots" in response.json()["reason_codes"]


def test_insufficient_time_coverage_returns_http_200(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-low-coverage", name="Coverage")
    for minute in range(3):
        _insert_snapshot(
            migrated_database,
            item_id=item_id,
            observed_at=f"2026-06-28T23:5{minute}:00Z",
        )

    response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_data"
    assert "insufficient_time_coverage" in response.json()["reason_codes"]


def test_stale_latest_snapshot_returns_http_200(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-stale", name="Stale")
    for observed_at in (
        "2026-06-22T00:00:00Z",
        "2026-06-24T00:00:00Z",
        "2026-06-27T22:59:00Z",
    ):
        _insert_snapshot(migrated_database, item_id=item_id, observed_at=observed_at)

    response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 200
    assert response.json()["status"] == "no_recent_market"
    assert "stale_latest_snapshot" in response.json()["reason_codes"]


def test_no_valid_bid_returns_http_200_no_valid_price(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-no-bid", name="No Bid")
    for observed_at in (
        "2026-06-22T00:00:00Z",
        "2026-06-24T00:00:00Z",
        "2026-06-29T00:00:00Z",
    ):
        _insert_snapshot(
            migrated_database,
            item_id=item_id,
            observed_at=observed_at,
            best_bid=None,
        )

    response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_valid_price"
    assert "no_current_bid" in body["reason_codes"]


def test_no_valid_ask_strategy_result_returns_http_200(
    client: TestClient,
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-no-ask", name="No Ask")
    _insert_snapshot(migrated_database, item_id=item_id, observed_at="2026-06-29T00:00:00Z")

    def observations_without_ask(snapshots: list[MarketSnapshot]) -> tuple[MarketObservation, ...]:
        snapshot = snapshots[0]
        return (
            MarketObservation(
                observed_at=snapshot.observed_at,
                best_ask=None,
                best_bid=snapshot.best_bid,
                ask_count=snapshot.ask_count,
                bid_count=snapshot.bid_count,
                estimated_volume=snapshot.estimated_volume,
                observation_key=str(snapshot.id),
            ),
        )

    monkeypatch.setattr(
        "api.services.analysis.market_snapshots_to_observations",
        observations_without_ask,
    )

    response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "insufficient_data"
    assert "no_current_ask" in body["reason_codes"]


def test_decimal_fields_serialize_as_strings_or_null(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _seed_full_window(
        migrated_database,
        horizon=7,
        as_of=datetime(2026, 6, 29, tzinfo=UTC),
    )

    response = client.get(_analysis_url(item_id, fee_rate="0.075", as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 200
    body = response.json()
    assert body["effective_inputs"]["fee_rate"] == "0.075"
    for field in (
        "current_ask",
        "current_bid",
        "reference_sell_price",
        "gross_profit",
        "net_profit",
        "net_roi",
        "break_even_sell_price",
        "spread_absolute",
        "spread_ratio",
        "median_bid",
        "median_ask",
        "price_volatility",
        "liquidity_score",
        "risk_score",
        "confidence_score",
    ):
        assert body[field] is None or isinstance(body[field], str)
        assert not isinstance(body[field], float)


def test_none_decimal_fields_remain_null(client: TestClient, migrated_database: str) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-null-decimals", name="Nulls")

    response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 200
    body = response.json()
    assert body["current_ask"] is None
    assert body["reference_sell_price"] is None


def test_reason_codes_order_is_stable(client: TestClient, migrated_database: str) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-reasons", name="Reasons")

    first = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z")).json()
    second = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z")).json()

    assert first["reason_codes"] == second["reason_codes"]


def test_orm_objects_are_not_passed_to_strategy(
    client: TestClient,
    migrated_database: str,
) -> None:
    captured: dict[str, Any] = {}

    class CapturingStrategy:
        strategy_name = "rule_based"
        strategy_version = "1.0.0"
        feature_version = "market_features_v1"

        def analyze(self, request: AnalysisRequest) -> AnalysisResult:
            captured["observations"] = request.observations
            return AnalysisResult(
                item_id=request.item_id,
                horizon=request.horizon,
                as_of=request.as_of,
                status=AnalysisStatus.INSUFFICIENT_DATA,
                strategy_name=self.strategy_name,
                strategy_version=self.strategy_version,
                feature_version=self.feature_version,
                observation_count=len(request.observations),
                first_observation_at=None,
                last_observation_at=None,
                current_ask=None,
                current_bid=None,
                reference_sell_price=None,
                gross_profit=None,
                net_profit=None,
                net_roi=None,
                break_even_sell_price=None,
                spread_absolute=None,
                spread_ratio=None,
                median_bid=None,
                median_ask=None,
                price_volatility=None,
                liquidity_score=None,
                risk_score=None,
                confidence_score=None,
                reason_codes=(ReasonCode.INSUFFICIENT_SNAPSHOTS,),
            )

    registry = StrategyRegistry()
    registry.register(CapturingStrategy())
    item_id = _insert_item(migrated_database, external_key="synthetic-capture", name="Capture")
    snapshot_id = _insert_snapshot(
        migrated_database,
        item_id=item_id,
        observed_at="2026-06-29T00:00:00Z",
    )

    from api.main import app

    app.dependency_overrides[get_strategy_registry] = lambda: registry
    try:
        response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))
    finally:
        app.dependency_overrides.pop(get_strategy_registry, None)

    assert response.status_code == 200
    observations = captured["observations"]
    assert all(isinstance(observation, MarketObservation) for observation in observations)
    assert all(not isinstance(observation, MarketSnapshot) for observation in observations)
    assert observations[0].observation_key == str(snapshot_id)


def test_strategy_not_available_returns_stable_error(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-no-strategy", name="No Strategy")

    from api.main import app

    app.dependency_overrides[get_strategy_registry] = lambda: StrategyRegistry()
    try:
        response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))
    finally:
        app.dependency_overrides.pop(get_strategy_registry, None)

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "strategy_not_available"


def test_contract_input_error_returns_stable_error(
    client: TestClient,
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-contract-error", name="Contract")
    _insert_snapshot(migrated_database, item_id=item_id, observed_at="2026-06-29T00:00:00Z")

    def future_observations(snapshots: list[MarketSnapshot]) -> tuple[MarketObservation, ...]:
        return (
            MarketObservation(
                observed_at=datetime(2026, 6, 30, tzinfo=UTC),
                best_ask=Decimal("1"),
                best_bid=Decimal("1"),
                ask_count=1,
                bid_count=1,
                estimated_volume=None,
                observation_key="future",
            ),
        )

    monkeypatch.setattr("api.services.analysis.market_snapshots_to_observations", future_observations)

    response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "analysis_input_error"


def test_invalid_runtime_analytics_configuration_returns_stable_error(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-bad-config", name="Bad Config")
    bad_settings = Settings.model_construct(
        app_env="development",
        database_url=migrated_database,
        cors_allowed_origins="http://localhost:3000",
        analytics_maximum_snapshot_age_hours=0,
        analytics_minimum_snapshot_count=3,
    )

    from api.main import app

    app.dependency_overrides[get_settings] = lambda: bad_settings
    try:
        response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "invalid_analytics_configuration"


def test_settings_boundary_rejects_invalid_analytics_configuration() -> None:
    with pytest.raises(Exception):
        Settings(ANALYTICS_MAXIMUM_SNAPSHOT_AGE_HOURS=0, _env_file=None)

    with pytest.raises(Exception):
        Settings(ANALYTICS_MINIMUM_SNAPSHOT_COUNT=0, _env_file=None)


def test_analysis_request_does_not_write_database(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _seed_full_window(
        migrated_database,
        horizon=7,
        as_of=datetime(2026, 6, 29, tzinfo=UTC),
    )
    before = _counts(migrated_database)

    response = client.get(_analysis_url(item_id, as_of="2026-06-29T00:00:00Z"))

    assert response.status_code == 200
    assert _counts(migrated_database) == before
    assert "analysis_results" not in _table_names(migrated_database)


def test_openapi_contains_analysis_route(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/items/{item_id}/analysis" in response.json()["paths"]


def test_parse_fee_rate_does_not_accept_float_only_artifacts() -> None:
    parsed = analysis_router._parse_fee_rate("0.1000000000000000000000000001")

    assert parsed == Decimal("0.1000000000000000000000000001")
