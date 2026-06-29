from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.contracts import MarketObservation
from gaijin_market_analytics.enums import AnalysisHorizon
from gaijin_market_analytics.exceptions import ContractValidationError
from gaijin_market_analytics.horizons import coverage_ratio, select_horizon_observations


AS_OF = datetime(2026, 6, 29, tzinfo=UTC)


def obs(days_back: int, key: str) -> MarketObservation:
    return MarketObservation(
        observed_at=AS_OF - timedelta(days=days_back),
        best_ask=Decimal("10"),
        best_bid=Decimal("9"),
        ask_count=1,
        bid_count=1,
        estimated_volume=None,
        observation_key=key,
    )


@pytest.mark.parametrize(
    ("horizon", "inside_days", "outside_days"),
    [
        (AnalysisHorizon.DAYS_7, 7, 8),
        (AnalysisHorizon.DAYS_30, 30, 31),
        (AnalysisHorizon.DAYS_90, 90, 91),
        (AnalysisHorizon.DAYS_180, 180, 181),
    ],
)
def test_horizon_window_is_inclusive_and_excludes_outside_data(
    horizon: AnalysisHorizon,
    inside_days: int,
    outside_days: int,
) -> None:
    selected = select_horizon_observations((obs(outside_days, "outside"), obs(inside_days, "in")), AS_OF, horizon)

    assert [item.observation_key for item in selected] == ["in"]


def test_each_horizon_is_independently_selected() -> None:
    observations = (obs(10, "ten"), obs(40, "forty"), obs(100, "hundred"))

    assert [item.observation_key for item in select_horizon_observations(observations, AS_OF, AnalysisHorizon.DAYS_30)] == ["ten"]
    assert [item.observation_key for item in select_horizon_observations(observations, AS_OF, AnalysisHorizon.DAYS_90)] == ["forty", "ten"]


def test_future_data_is_rejected() -> None:
    future = MarketObservation(
        observed_at=AS_OF + timedelta(seconds=1),
        best_ask=Decimal("10"),
        best_bid=Decimal("9"),
        ask_count=1,
        bid_count=1,
        estimated_volume=None,
    )
    with pytest.raises(ContractValidationError):
        select_horizon_observations((future,), AS_OF, AnalysisHorizon.DAYS_7)


def test_coverage_ratio_uses_first_to_last_span_and_clamps() -> None:
    observations = (obs(7, "start"), obs(0, "end"))

    assert coverage_ratio(observations, AnalysisHorizon.DAYS_7) == Decimal("1")
    assert coverage_ratio((obs(1, "only"),), AnalysisHorizon.DAYS_7) == Decimal("0")
