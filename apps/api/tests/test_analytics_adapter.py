from datetime import UTC, datetime, timezone, timedelta
from decimal import Decimal

from gaijin_market_analytics.contracts import MarketObservation

from api.adapters.analytics import market_snapshot_to_observation
from api.db.models import MarketSnapshot


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
