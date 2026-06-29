from datetime import UTC, datetime

from gaijin_market_analytics.contracts import MarketObservation

from api.db.models import MarketSnapshot


def market_snapshot_to_observation(snapshot: MarketSnapshot) -> MarketObservation:
    return MarketObservation(
        observed_at=_to_utc(snapshot.observed_at),
        best_ask=snapshot.best_ask,
        best_bid=snapshot.best_bid,
        ask_count=snapshot.ask_count,
        bid_count=snapshot.bid_count,
        estimated_volume=snapshot.estimated_volume,
        observation_key=str(snapshot.id),
    )


def market_snapshots_to_observations(
    snapshots: list[MarketSnapshot],
) -> tuple[MarketObservation, ...]:
    return tuple(market_snapshot_to_observation(snapshot) for snapshot in snapshots)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
