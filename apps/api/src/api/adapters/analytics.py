from datetime import UTC, datetime

from gaijin_market_analytics.contracts import MarketObservation

from api.db.models import MarketSnapshot, OrderBookObservation


def market_snapshot_to_observation(
    snapshot: MarketSnapshot,
    order_book_observation: OrderBookObservation | None = None,
) -> MarketObservation:
    return MarketObservation(
        observed_at=_to_utc(snapshot.observed_at),
        best_ask=snapshot.best_ask,
        best_bid=snapshot.best_bid,
        ask_count=snapshot.ask_count,
        bid_count=snapshot.bid_count,
        estimated_volume=snapshot.estimated_volume,
        observation_key=str(snapshot.id),
        observed_ask_quantity=(
            order_book_observation.observed_ask_quantity
            if order_book_observation is not None
            else None
        ),
        observed_bid_quantity=(
            order_book_observation.observed_bid_quantity
            if order_book_observation is not None
            else None
        ),
        quantity_semantics=(
            order_book_observation.quantity_semantics
            if order_book_observation is not None
            else None
        ),
        source_type=(
            order_book_observation.source_type
            if order_book_observation is not None
            else None
        ),
        review_status=(
            order_book_observation.review_status
            if order_book_observation is not None
            else None
        ),
    )


def market_snapshots_to_observations(
    snapshots: list[MarketSnapshot],
) -> tuple[MarketObservation, ...]:
    return tuple(market_snapshot_to_observation(snapshot) for snapshot in snapshots)


def market_snapshot_rows_to_observations(
    rows: list[tuple[MarketSnapshot, OrderBookObservation | None]],
) -> tuple[MarketObservation, ...]:
    return tuple(
        market_snapshot_to_observation(snapshot, order_book_observation)
        for snapshot, order_book_observation in rows
    )


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
