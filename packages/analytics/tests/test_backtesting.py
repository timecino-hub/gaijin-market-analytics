from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.backtesting import (
    BacktestCaseStatus,
    BacktestConfig,
    BacktestSkipReason,
    generate_cutoffs,
    run_backtest,
)
from gaijin_market_analytics.contracts import AnalysisRequest, AnalysisResult, MarketObservation
from gaijin_market_analytics.enums import AnalysisHorizon, AnalysisStatus, ReasonCode
from gaijin_market_analytics.exceptions import ContractValidationError
from gaijin_market_analytics.fees import GAIJIN_MARKET_FEE_POLICY_V1
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1


BASE = datetime(2026, 1, 10, tzinfo=UTC)


def obs(
    at: datetime,
    *,
    ask: str | None = "10.00",
    bid: str | None = "9.00",
    key: str | None = None,
) -> MarketObservation:
    return MarketObservation(
        observed_at=at,
        best_ask=Decimal(ask) if ask is not None else None,
        best_bid=Decimal(bid) if bid is not None else None,
        ask_count=20,
        bid_count=20,
        estimated_volume=Decimal("100"),
        observation_key=key,
    )


def config(**overrides: object) -> BacktestConfig:
    values = {
        "strategy_name": "spy",
        "strategy_version": "1.0.0",
        "lookback_horizon_days": 7,
        "forward_horizon_days": 7,
        "start_at": BASE,
        "end_at": BASE,
        "cadence_days": 7,
        "minimum_snapshot_count": 1,
        "maximum_snapshot_age_hours": 24 * 8,
    }
    values.update(overrides)
    return BacktestConfig(**values)


class SpyStrategy:
    strategy_name = "spy"
    strategy_version = "1.0.0"
    feature_version = "spy_features_v1"

    def __init__(self) -> None:
        self.requests: list[AnalysisRequest] = []

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        self.requests.append(request)
        return AnalysisResult(
            item_id=request.item_id,
            horizon=request.horizon,
            as_of=request.as_of,
            status=AnalysisStatus.OK,
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            feature_version=self.feature_version,
            observation_count=len(request.observations),
            first_observation_at=request.observations[0].observed_at if request.observations else None,
            last_observation_at=request.observations[-1].observed_at if request.observations else None,
            current_ask=Decimal("10.00"),
            current_bid=Decimal("9.00"),
            reference_sell_price=Decimal("11.00"),
            sale_proceeds=Decimal("9.35"),
            fee_amount=Decimal("1.65"),
            gross_profit=Decimal("1.00"),
            net_profit=Decimal("-0.65"),
            net_roi=Decimal("-0.065"),
            break_even_sell_price=Decimal("11.77"),
            break_even_reachable=True,
            maximum_listing_price=GAIJIN_MARKET_RULES_V1.maximum_listing_price,
            maximum_sale_proceeds=GAIJIN_MARKET_RULES_V1.maximum_sale_proceeds,
            maximum_net_profit=Decimal("1690.00"),
            spread_absolute=Decimal("1.00"),
            spread_ratio=Decimal("0.1111111111111111111111111111"),
            median_bid=Decimal("9.00"),
            median_ask=Decimal("10.00"),
            price_volatility=Decimal("0"),
            liquidity_score=Decimal("100"),
            risk_score=Decimal("0"),
            confidence_score=Decimal("100"),
            fee_policy_name=GAIJIN_MARKET_FEE_POLICY_V1.name,
            fee_policy_version=GAIJIN_MARKET_FEE_POLICY_V1.version,
            nominal_fee_rate=GAIJIN_MARKET_FEE_POLICY_V1.nominal_rate,
            currency_quantum=GAIJIN_MARKET_FEE_POLICY_V1.currency_quantum,
            proceeds_rounding=GAIJIN_MARKET_FEE_POLICY_V1.proceeds_rounding,
            market_rules_name=GAIJIN_MARKET_RULES_V1.name,
            market_rules_version=GAIJIN_MARKET_RULES_V1.version,
            reason_codes=(ReasonCode.ANALYSIS_COMPLETED,),
        )


def run_with_spy(
    observations: tuple[MarketObservation, ...],
    backtest_config: BacktestConfig | None = None,
) -> tuple[SpyStrategy, object]:
    strategy = SpyStrategy()
    result = run_backtest(
        observations=observations,
        config=backtest_config or config(),
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        strategy=strategy,
    )
    return strategy, result


def test_backtest_config_requires_aware_utc_datetimes() -> None:
    with pytest.raises(ContractValidationError):
        config(start_at=datetime(2026, 1, 1), end_at=BASE)


@pytest.mark.parametrize(
    "overrides",
    [
        {"lookback_horizon_days": 8},
        {"forward_horizon_days": 8},
        {"cadence_days": 0},
        {"start_at": BASE + timedelta(days=1), "end_at": BASE},
    ],
)
def test_backtest_config_rejects_invalid_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ContractValidationError):
        config(**overrides)


def test_generate_cutoffs_are_stable_and_do_not_exceed_end() -> None:
    cutoffs = generate_cutoffs(config(start_at=BASE, end_at=BASE + timedelta(days=15), cadence_days=7))

    assert cutoffs == (
        BASE,
        BASE + timedelta(days=7),
        BASE + timedelta(days=14),
    )


def test_strategy_receives_only_lookback_observations_and_cutoff_is_not_future() -> None:
    cutoff_observation = obs(BASE, bid="10.00", key="cutoff")
    future_observation = obs(BASE + timedelta(seconds=1), bid="12.00", key="future")
    spy, result = run_with_spy(
        (
            obs(BASE - timedelta(days=7), key="window-start"),
            cutoff_observation,
            future_observation,
            obs(BASE + timedelta(days=7), bid="13.00", key="future-end"),
        )
    )

    received = spy.requests[0].observations
    assert all(observation.observed_at <= BASE for observation in received)
    assert cutoff_observation.observed_at in {observation.observed_at for observation in received}
    assert result.cases[0].future_observation_count == 2
    assert result.cases[0].terminal_bid == Decimal("13.00")


def test_duplicate_times_with_missing_keys_use_stable_input_order() -> None:
    same_time = BASE + timedelta(days=1)
    _, first = run_with_spy(
        (
            obs(BASE, key="cutoff"),
            obs(same_time, bid="12.00"),
            obs(same_time, bid="13.00"),
            obs(BASE + timedelta(days=7), bid="14.00"),
        )
    )
    _, second = run_with_spy(
        (
            obs(BASE, key="cutoff"),
            obs(same_time, bid="12.00"),
            obs(same_time, bid="13.00"),
            obs(BASE + timedelta(days=7), bid="14.00"),
        )
    )

    assert first == second
    assert first.cases[0].maximum_future_bid == Decimal("14.00")


def test_future_window_incomplete_is_distinct_from_empty_complete_window() -> None:
    _, incomplete = run_with_spy((obs(BASE, key="cutoff"), obs(BASE + timedelta(days=1), key="partial")))
    _, empty_complete = run_with_spy(
        (
            obs(BASE, key="cutoff"),
            obs(BASE + timedelta(days=8), key="after-window"),
        )
    )

    assert incomplete.cases[0].status == BacktestCaseStatus.FUTURE_DATA_UNAVAILABLE
    assert BacktestSkipReason.FUTURE_WINDOW_INCOMPLETE in incomplete.cases[0].skip_reasons
    assert BacktestSkipReason.NO_FUTURE_OBSERVATIONS not in incomplete.cases[0].skip_reasons
    assert empty_complete.cases[0].status == BacktestCaseStatus.FUTURE_DATA_UNAVAILABLE
    assert BacktestSkipReason.NO_FUTURE_OBSERVATIONS in empty_complete.cases[0].skip_reasons
    assert BacktestSkipReason.FUTURE_WINDOW_INCOMPLETE not in empty_complete.cases[0].skip_reasons


def test_future_bid_metrics_use_only_valid_market_bids() -> None:
    _, result = run_with_spy(
        (
            obs(BASE, key="cutoff"),
            obs(BASE + timedelta(days=1), bid="0", key="zero"),
            obs(BASE + timedelta(days=2), bid="12.00", key="valid-1"),
            obs(BASE + timedelta(days=3), bid="2000.01", key="cap"),
            obs(BASE + timedelta(days=4), bid=None, key="none"),
            obs(BASE + timedelta(days=7), bid="13.00", key="valid-2"),
        )
    )

    case = result.cases[0]
    assert case.future_observation_count == 5
    assert case.future_valid_bid_count == 2
    assert case.terminal_bid == Decimal("13.00")
    assert case.maximum_future_bid == Decimal("13.00")
    assert case.minimum_future_bid == Decimal("12.00")


def test_no_valid_future_bid_keeps_nullable_price_metrics() -> None:
    _, result = run_with_spy(
        (
            obs(BASE, key="cutoff"),
            obs(BASE + timedelta(days=1), bid="0", key="zero"),
            obs(BASE + timedelta(days=2), bid="2000.01", key="cap"),
            obs(BASE + timedelta(days=7), bid=None, key="none"),
        )
    )

    case = result.cases[0]
    assert case.future_valid_bid_count == 0
    assert case.terminal_bid is None
    assert case.maximum_future_bid is None
    assert BacktestSkipReason.NO_VALID_FUTURE_BID in case.skip_reasons


def test_incomplete_forward_window_can_be_evaluated_when_requirement_is_disabled() -> None:
    _, result = run_with_spy(
        (
            obs(BASE, key="cutoff"),
            obs(BASE + timedelta(days=1), bid="12.00", key="partial"),
        ),
        config(require_complete_forward_window=False),
    )

    assert result.cases[0].status == BacktestCaseStatus.EVALUATED
    assert BacktestSkipReason.FUTURE_WINDOW_INCOMPLETE not in result.cases[0].skip_reasons
    assert result.summary.terminal_return_evaluable_count == 1


def test_reference_and_break_even_reach_times_use_cutoff_or_first_future_bid() -> None:
    _, result = run_with_spy(
        (
            obs(BASE, key="cutoff"),
            obs(BASE + timedelta(days=2), bid="11.00", key="reference"),
            obs(BASE + timedelta(days=3), bid="12.00", key="break-even"),
            obs(BASE + timedelta(days=7), bid="12.50", key="terminal"),
        )
    )

    case = result.cases[0]
    assert case.reference_reached is True
    assert case.time_to_reference_seconds == 2 * 24 * 60 * 60
    assert case.break_even_reached is True
    assert case.time_to_break_even_seconds == 3 * 24 * 60 * 60


def test_reference_reached_at_cutoff_has_time_zero() -> None:
    class ReachedAtCutoffStrategy(SpyStrategy):
        def analyze(self, request: AnalysisRequest) -> AnalysisResult:
            return replace(super().analyze(request), current_bid=Decimal("11.00"))

    result = run_backtest(
        observations=(obs(BASE, key="cutoff"), obs(BASE + timedelta(days=7), bid="9.00")),
        config=config(),
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        strategy=ReachedAtCutoffStrategy(),
    )

    assert result.cases[0].reference_reached is True
    assert result.cases[0].time_to_reference_seconds == 0


def test_terminal_and_maximum_window_profit_roi_are_decimal() -> None:
    _, result = run_with_spy(
        (
            obs(BASE, key="cutoff"),
            obs(BASE + timedelta(days=1), bid="12.00", key="future"),
            obs(BASE + timedelta(days=7), bid="13.00", key="terminal"),
        )
    )

    case = result.cases[0]
    assert case.terminal_sale_proceeds == Decimal("11.05")
    assert case.terminal_net_profit == Decimal("1.05")
    assert case.terminal_net_roi == Decimal("0.105")
    assert case.maximum_net_profit_in_window == Decimal("1.05")
    assert case.maximum_net_roi_in_window == Decimal("0.105")
    assert isinstance(case.terminal_net_roi, Decimal)


def test_summary_denominators_exclude_incomplete_cases_and_zero_denominators_return_none() -> None:
    _, result = run_with_spy(
        (
            obs(BASE, key="cutoff-1"),
            obs(BASE + timedelta(days=7), bid="13.00", key="future-1"),
            obs(BASE + timedelta(days=8), key="cutoff-2"),
        ),
        config(start_at=BASE, end_at=BASE + timedelta(days=7), cadence_days=7),
    )
    _, unavailable = run_with_spy((obs(BASE, key="only"),), config())

    summary = result.summary
    assert summary.total_case_count == 2
    assert summary.terminal_return_evaluable_count == 1
    assert summary.positive_terminal_return_count == 1
    assert summary.positive_terminal_return_rate == Decimal("1")
    assert unavailable.summary.reference_evaluable_count == 0
    assert unavailable.summary.reference_reach_rate is None


def test_analysis_non_ok_keeps_case_but_excludes_formal_summary() -> None:
    class NonOkStrategy(SpyStrategy):
        def analyze(self, request: AnalysisRequest) -> AnalysisResult:
            result = super().analyze(request)
            return AnalysisResult(
                **{
                    **result.__dict__,
                    "status": AnalysisStatus.INSUFFICIENT_DATA,
                    "reason_codes": (ReasonCode.INSUFFICIENT_SNAPSHOTS,),
                }
            )

    result = run_backtest(
        observations=(obs(BASE, key="cutoff"), obs(BASE + timedelta(days=7), key="future")),
        config=config(strategy_name="spy", strategy_version="1.0.0"),
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        strategy=NonOkStrategy(),
    )

    assert result.cases[0].status == BacktestCaseStatus.ANALYSIS_UNAVAILABLE
    assert BacktestSkipReason.ANALYSIS_STATUS_NOT_OK in result.cases[0].skip_reasons
    assert result.summary.evaluated_case_count == 0
