from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.contracts import MarketObservation
from gaijin_market_analytics.statistics import (
    decimal_mean,
    decimal_median,
    decimal_median_absolute_deviation,
    filter_valid_prices,
    latest_valid_ask,
    latest_valid_bid,
    spread_absolute,
    spread_ratio,
)


def observation(offset: int, ask: Decimal | None, bid: Decimal | None) -> MarketObservation:
    return MarketObservation(
        observed_at=datetime(2026, 6, 29, tzinfo=UTC) + timedelta(minutes=offset),
        best_ask=ask,
        best_bid=bid,
        ask_count=1,
        bid_count=1,
        estimated_volume=None,
    )


def test_decimal_median_odd_and_even_counts() -> None:
    assert decimal_median((Decimal("3"), Decimal("1"), Decimal("2"))) == Decimal("2")
    assert decimal_median((Decimal("4"), Decimal("2"))) == Decimal("3")


def test_empty_statistics_return_none() -> None:
    assert decimal_median(()) is None
    assert decimal_mean(()) is None
    assert decimal_median_absolute_deviation(()) is None


def test_none_and_invalid_prices_are_filtered() -> None:
    assert filter_valid_prices((None, Decimal("0"), Decimal("-1"), Decimal("2.5"))) == (
        Decimal("2.5"),
    )


def test_mean_and_robust_volatility_use_decimal() -> None:
    values = (Decimal("10"), Decimal("11"), Decimal("14"))

    assert decimal_mean(values) == Decimal("35") / Decimal("3")
    assert decimal_median_absolute_deviation(values) == Decimal("1")


def test_spread_absolute_and_ratio() -> None:
    assert spread_absolute(Decimal("11"), Decimal("10")) == Decimal("1")
    assert spread_ratio(Decimal("11"), Decimal("10")) == Decimal("0.1")


def test_latest_valid_ask_and_bid_ignore_invalid_prices() -> None:
    observations = (
        observation(0, Decimal("10"), Decimal("9")),
        observation(1, Decimal("0"), Decimal("-1")),
    )

    assert latest_valid_ask(observations) == Decimal("10")
    assert latest_valid_bid(observations) == Decimal("9")
