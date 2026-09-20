from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from api.config import Settings, get_settings

AS_OF = datetime(2026, 6, 29, tzinfo=UTC)


def _insert_item(
    database_url: str,
    *,
    external_key: str,
    name: str,
    category: str = "vehicle",
    rarity: str | None = None,
    is_active: bool = True,
) -> int:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            return int(
                conn.execute(
                    text(
                        """
                        INSERT INTO items (external_key, name, category, rarity, is_active)
                        VALUES (:external_key, :name, :category, :rarity, :is_active)
                        RETURNING id
                        """
                    ),
                    {
                        "external_key": external_key,
                        "name": name,
                        "category": category,
                        "rarity": rarity,
                        "is_active": is_active,
                    },
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _insert_snapshot(
    database_url: str,
    *,
    item_id: int,
    observed_at: datetime,
    best_ask: str,
    best_bid: str,
    ask_count: int | None = 20,
    bid_count: int | None = 20,
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
                            bid_count
                        )
                        VALUES (
                            :item_id,
                            :observed_at,
                            :best_ask,
                            :best_bid,
                            :ask_count,
                            :bid_count
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "item_id": item_id,
                        "observed_at": observed_at,
                        "best_ask": Decimal(best_ask),
                        "best_bid": Decimal(best_bid),
                        "ask_count": ask_count,
                        "bid_count": bid_count,
                    },
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _insert_reviewed_quantity(
    database_url: str,
    *,
    item_id: int,
    snapshot_id: int,
    review_id: str,
    bid_quantity: int,
    ask_quantity: int,
) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            review_import_id = int(
                conn.execute(
                    text(
                        """
                        INSERT INTO screen_review_imports (
                            review_id,
                            item_id,
                            market_snapshot_id,
                            review_status,
                            candidate_version,
                            candidate_sha256,
                            total_bid_quantity,
                            total_ask_quantity,
                            candidate_payload,
                            source_metadata
                        )
                        VALUES (
                            :review_id,
                            :item_id,
                            :snapshot_id,
                            'confirmed',
                            'screen_review_candidate_v1',
                            :candidate_sha256,
                            :bid_quantity,
                            :ask_quantity,
                            '{}'::jsonb,
                            '{}'::jsonb
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "review_id": review_id,
                        "item_id": item_id,
                        "snapshot_id": snapshot_id,
                        "candidate_sha256": f"{snapshot_id:064x}"[-64:],
                        "bid_quantity": bid_quantity,
                        "ask_quantity": ask_quantity,
                    },
                ).scalar_one()
            )
            conn.execute(
                text(
                    """
                    INSERT INTO order_book_observations (
                        market_snapshot_id,
                        screen_review_import_id,
                        observed_bid_quantity,
                        observed_ask_quantity,
                        quantity_semantics,
                        source_type,
                        source_version,
                        review_status
                    )
                    VALUES (
                        :snapshot_id,
                        :review_import_id,
                        :bid_quantity,
                        :ask_quantity,
                        'screenshot_display_quantity',
                        'screen_review',
                        'screen_review_candidate_v1',
                        'confirmed'
                    )
                    """
                ),
                {
                    "snapshot_id": snapshot_id,
                    "review_import_id": review_import_id,
                    "bid_quantity": bid_quantity,
                    "ask_quantity": ask_quantity,
                },
            )
    finally:
        engine.dispose()


def _seed_opportunity(
    database_url: str,
    *,
    external_key: str,
    name: str,
    current_ask: str,
    current_bid: str,
    quantity: int = 20,
    category: str = "vehicle",
    rarity: str | None = None,
    is_active: bool = True,
) -> int:
    item_id = _insert_item(
        database_url,
        external_key=external_key,
        name=name,
        category=category,
        rarity=rarity,
        is_active=is_active,
    )
    for days_back in (7, 3):
        _insert_snapshot(
            database_url,
            item_id=item_id,
            observed_at=AS_OF - timedelta(days=days_back),
            best_ask="11",
            best_bid="10",
        )
    latest_snapshot_id = _insert_snapshot(
        database_url,
        item_id=item_id,
        observed_at=AS_OF,
        best_ask=current_ask,
        best_bid=current_bid,
    )
    _insert_reviewed_quantity(
        database_url,
        item_id=item_id,
        snapshot_id=latest_snapshot_id,
        review_id=f"review-{external_key}",
        bid_quantity=quantity,
        ask_quantity=quantity,
    )
    return item_id


def _url(**overrides: str) -> str:
    values = {
        "horizon": "7",
        "as_of": "2026-06-29T00:00:00Z",
    }
    values.update(overrides)
    query = "&".join(f"{key}={value}" for key, value in values.items())
    return f"/api/v1/opportunities?{query}"


def test_empty_opportunity_ranking_has_stable_metadata(client: TestClient) -> None:
    response = client.get(_url())

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["evaluated_total"] == 0
    assert body["eligible_total"] == 0
    assert body["effective_inputs"]["horizon"] == 7
    assert body["effective_inputs"]["maximum_snapshot_age_seconds"] == 7 * 24 * 60 * 60
    assert body["strategy_version"] == "1.0.0"
    assert body["feature_version"] == "opportunity_features_v1"


def test_opportunity_ranking_orders_scores_and_paginates_deterministically(
    client: TestClient,
    migrated_database: str,
) -> None:
    alpha_id = _seed_opportunity(
        migrated_database,
        external_key="alpha",
        name="Alpha",
        current_ask="5",
        current_bid="4.5",
        quantity=30,
    )
    beta_id = _seed_opportunity(
        migrated_database,
        external_key="beta",
        name="Beta",
        current_ask="6.5",
        current_bid="6",
        quantity=15,
    )
    _seed_opportunity(
        migrated_database,
        external_key="loss",
        name="Loss",
        current_ask="12",
        current_bid="11",
        quantity=50,
    )

    first_page = client.get(_url(page_size="1"))
    second_page = client.get(_url(page="2", page_size="1"))

    assert first_page.status_code == 200
    first_body = first_page.json()
    assert first_body["evaluated_total"] == 3
    assert first_body["eligible_total"] == 2
    assert first_body["total"] == 2
    assert first_body["total_pages"] == 2
    assert first_body["items"][0]["item_id"] == alpha_id
    assert first_body["items"][0]["rank"] == 1
    assert first_body["items"][0]["liquidity_source"] == "reviewed_screenshot_quantity"
    assert first_body["items"][0]["latest_observed_bid_quantity"] == 30

    assert second_page.status_code == 200
    assert second_page.json()["items"][0]["item_id"] == beta_id
    assert second_page.json()["items"][0]["rank"] == 2


def test_opportunity_filters_and_ineligible_diagnostics(
    client: TestClient,
    migrated_database: str,
) -> None:
    _seed_opportunity(
        migrated_database,
        external_key="vehicle-common",
        name="Vehicle Common",
        current_ask="5",
        current_bid="4.5",
        category="vehicle",
        rarity="common",
    )
    inactive_id = _seed_opportunity(
        migrated_database,
        external_key="inactive-rare",
        name="Inactive Rare",
        current_ask="5",
        current_bid="4.5",
        category="coupon",
        rarity="rare",
        is_active=False,
    )
    loss_id = _seed_opportunity(
        migrated_database,
        external_key="loss-rare",
        name="Loss Rare",
        current_ask="12",
        current_bid="11",
        category="coupon",
        rarity="rare",
    )

    default_response = client.get(_url(category="coupon", rarity="rare"))
    diagnostics_response = client.get(
        _url(
            category="coupon",
            rarity="rare",
            include_inactive="true",
            eligible_only="false",
            search="rare",
        )
    )

    assert default_response.status_code == 200
    assert default_response.json()["items"] == []

    assert diagnostics_response.status_code == 200
    body = diagnostics_response.json()
    assert body["filters"] == {
        "eligible_only": False,
        "minimum_score": "0.00",
        "search": "rare",
        "category": "coupon",
        "rarity": "rare",
        "include_inactive": True,
    }
    assert {item["item_id"] for item in body["items"]} == {inactive_id, loss_id}
    assert body["items"][0]["eligible"] is True
    assert body["items"][-1]["eligible"] is False


def test_minimum_score_and_future_data_are_respected(
    client: TestClient,
    migrated_database: str,
) -> None:
    eligible_id = _seed_opportunity(
        migrated_database,
        external_key="eligible-now",
        name="Eligible Now",
        current_ask="5",
        current_bid="4.5",
    )
    future_id = _insert_item(
        migrated_database,
        external_key="future-only",
        name="Future Only",
    )
    _insert_snapshot(
        migrated_database,
        item_id=future_id,
        observed_at=AS_OF + timedelta(seconds=1),
        best_ask="1",
        best_bid="10",
    )

    response = client.get(_url(min_score="99.99", eligible_only="false"))
    normal = client.get(_url())

    assert response.status_code == 200
    assert all(Decimal(item["score"]) >= Decimal("99.99") for item in response.json()["items"])
    assert normal.status_code == 200
    assert [item["item_id"] for item in normal.json()["items"]] == [eligible_id]
    assert normal.json()["evaluated_total"] == 1


def test_invalid_opportunity_query_parameters_use_stable_errors(client: TestClient) -> None:
    cases = (
        (_url(horizon="8"), "invalid_horizon"),
        (_url(page="0"), "invalid_pagination"),
        (_url(eligible_only="yes"), "invalid_boolean"),
        (_url(min_score="101"), "invalid_min_score"),
        (_url(as_of="2026-06-29T00:00:00"), "invalid_as_of"),
        (_url(fee_rate="0.10"), "fee_rate_not_configurable"),
    )

    for url, code in cases:
        response = client.get(url)
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == code


def test_candidate_limit_returns_safe_error(
    client: TestClient,
    migrated_database: str,
) -> None:
    _seed_opportunity(
        migrated_database,
        external_key="candidate-a",
        name="Candidate A",
        current_ask="5",
        current_bid="4.5",
    )
    _seed_opportunity(
        migrated_database,
        external_key="candidate-b",
        name="Candidate B",
        current_ask="5",
        current_bid="4.5",
    )

    from api.main import app

    app.dependency_overrides[get_settings] = lambda: Settings(
        DATABASE_URL=migrated_database,
        ANALYTICS_OPPORTUNITY_MAX_CANDIDATES=1,
    )
    try:
        response = client.get(_url())
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "opportunity_scope_too_large"


def test_empty_ranking_response_builder_and_openapi_do_not_require_database() -> None:
    from gaijin_market_analytics.enums import AnalysisHorizon

    from api.main import app
    from api.routers.opportunities import _ranking_response
    from api.services.opportunities import OpportunityRankingServiceResult

    response = _ranking_response(
        OpportunityRankingServiceResult(
            items=(),
            page=1,
            page_size=25,
            total=0,
            total_pages=0,
            evaluated_total=0,
            eligible_total=0,
            as_of=AS_OF,
            horizon=AnalysisHorizon.DAYS_7,
            maximum_snapshot_age_hours=24,
            minimum_snapshot_count=3,
            minimum_score=Decimal("0.00"),
            eligible_only=True,
            include_inactive=False,
            search=None,
            category=None,
            rarity=None,
        )
    )

    body = response.model_dump(mode="json")
    assert body["items"] == []
    assert body["effective_inputs"]["fee_policy"]["nominal_fee_rate"] == "0.15"
    assert body["effective_inputs"]["market_rules"]["maximum_listing_price"] == "2000.00"
    assert "/api/v1/opportunities" in app.openapi()["paths"]
