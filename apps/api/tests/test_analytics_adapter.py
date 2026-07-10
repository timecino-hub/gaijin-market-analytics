from datetime import UTC, datetime, timezone, timedelta
from decimal import Decimal

from gaijin_market_analytics.contracts import MarketObservation

from api.adapters.analytics import market_snapshot_to_observation
from api.db.models import MarketSnapshot, OrderBookObservation


def test_market_snapshot_maps_to_plain_market_observation() -> None:
    snapshot = MarketSnapshot(
        id=123,
        item_id=456,
        observed_at=datetime(2026, 6, 29, 8, tzinfo=timezone(timedelta(hours=8))),
        best_ask=Decimal("12.340000"),
        best_bid=Decimal("11.110000"),
        ask_count=3,
        bid_count=2,
        estimated_volume=Decimal("44.500000"),
    )

    observation = market_snapshot_to_observation(snapshot)

    assert isinstance(observation, MarketObservation)
    assert observation.observed_at == datetime(2026, 6, 29, tzinfo=UTC)
    assert observation.best_ask == Decimal("12.340000")
    assert observation.best_bid == Decimal("11.110000")
    assert observation.ask_count == 3
    assert observation.bid_count == 2
    assert observation.estimated_volume == Decimal("44.500000")
    assert observation.observation_key == "123"


def test_reviewed_order_book_observation_maps_without_overwriting_snapshot_counts() -> None:
    snapshot = MarketSnapshot(
        id=123,
        item_id=456,
        observed_at=datetime(2026, 6, 29, tzinfo=UTC),
        best_ask=Decimal("12.340000"),
        best_bid=Decimal("11.110000"),
        ask_count=None,
        bid_count=None,
        estimated_volume=None,
    )
    reviewed = OrderBookObservation(
        id=789,
        market_snapshot_id=123,
        screen_review_import_id=321,
        observed_ask_quantity=7,
        observed_bid_quantity=5,
        quantity_semantics="screenshot_display_quantity",
        source_type="screen_review",
        source_version="screen_review_candidate_v1",
        review_status="confirmed_with_edits",
        created_at=datetime(2026, 6, 29, tzinfo=UTC),
    )

    observation = market_snapshot_to_observation(snapshot, reviewed)

    assert observation.ask_count is None
    assert observation.bid_count is None
    assert observation.observed_ask_quantity == 7
    assert observation.observed_bid_quantity == 5
    assert observation.quantity_semantics == "screenshot_display_quantity"
    assert observation.source_type == "screen_review"
    assert observation.review_status == "confirmed_with_edits"
