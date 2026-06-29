from decimal import Decimal

from gaijin_market_analytics.contracts import MarketObservation, observation_sort_key


def valid_price(value: Decimal | None) -> Decimal | None:
    if value is None or value <= Decimal("0"):
        return None
    return value


def filter_valid_prices(values: tuple[Decimal | None, ...]) -> tuple[Decimal, ...]:
    return tuple(value for value in (valid_price(value) for value in values) if value is not None)


def decimal_mean(values: tuple[Decimal | None, ...]) -> Decimal | None:
    filtered = filter_valid_prices(values)
    if not filtered:
        return None
    return sum(filtered, Decimal("0")) / Decimal(len(filtered))


def decimal_median(values: tuple[Decimal | None, ...]) -> Decimal | None:
    filtered = tuple(sorted(filter_valid_prices(values)))
    if not filtered:
        return None
    middle = len(filtered) // 2
    if len(filtered) % 2 == 1:
        return filtered[middle]
    return (filtered[middle - 1] + filtered[middle]) / Decimal("2")


def decimal_median_absolute_deviation(values: tuple[Decimal | None, ...]) -> Decimal | None:
    median = decimal_median(values)
    if median is None:
        return None
    deviations = tuple(abs(value - median) for value in filter_valid_prices(values))
    return _decimal_median_allowing_zero(deviations)


def spread_absolute(ask: Decimal | None, bid: Decimal | None) -> Decimal | None:
    if valid_price(ask) is None or valid_price(bid) is None:
        return None
    return ask - bid


def spread_ratio(ask: Decimal | None, bid: Decimal | None) -> Decimal | None:
    absolute = spread_absolute(ask, bid)
    if absolute is None or valid_price(bid) is None:
        return None
    return absolute / bid


def latest_valid_ask(observations: tuple[MarketObservation, ...]) -> Decimal | None:
    for observation in reversed(tuple(sorted(observations, key=observation_sort_key))):
        price = valid_price(observation.best_ask)
        if price is not None:
            return price
    return None


def latest_valid_bid(observations: tuple[MarketObservation, ...]) -> Decimal | None:
    for observation in reversed(tuple(sorted(observations, key=observation_sort_key))):
        price = valid_price(observation.best_bid)
        if price is not None:
            return price
    return None


def latest_observation_with_valid_price(
    observations: tuple[MarketObservation, ...],
) -> MarketObservation | None:
    for observation in reversed(tuple(sorted(observations, key=observation_sort_key))):
        if valid_price(observation.best_ask) is not None or valid_price(observation.best_bid) is not None:
            return observation
    return None


def _decimal_median_allowing_zero(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")
