from datetime import datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.contracts import MarketObservation, observation_sort_key
from gaijin_market_analytics.enums import AnalysisHorizon
from gaijin_market_analytics.exceptions import ContractValidationError


def select_horizon_observations(
    observations: tuple[MarketObservation, ...],
    as_of: datetime,
    horizon: AnalysisHorizon,
) -> tuple[MarketObservation, ...]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ContractValidationError("as_of must be timezone-aware.")
    window_start = as_of.astimezone(as_of.tzinfo) - horizon_delta(horizon)
    selected: list[MarketObservation] = []
    for observation in observations:
        if observation.observed_at > as_of:
            raise ContractValidationError("observations must not be later than as_of.")
        if window_start <= observation.observed_at <= as_of:
            selected.append(observation)
    return tuple(sorted(selected, key=observation_sort_key))


def coverage_ratio(
    observations: tuple[MarketObservation, ...],
    horizon: AnalysisHorizon,
) -> Decimal:
    if len(observations) < 2:
        return Decimal("0")
    ordered = tuple(sorted(observations, key=observation_sort_key))
    span = ordered[-1].observed_at - ordered[0].observed_at
    horizon_span = horizon_delta(horizon)
    if span.total_seconds() <= 0:
        return Decimal("0")
    ratio = Decimal(str(span.total_seconds())) / Decimal(str(horizon_span.total_seconds()))
    return min(Decimal("1"), max(Decimal("0"), ratio))


def horizon_delta(horizon: AnalysisHorizon) -> timedelta:
    return timedelta(days=horizon.value)
