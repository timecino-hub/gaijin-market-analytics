from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from gaijin_market_analytics.backtesting.contracts import (
    BacktestCaseResult,
    BacktestCaseStatus,
    BacktestConfig,
    BacktestResult,
    BacktestSkipReason,
    BacktestSummary,
)
from gaijin_market_analytics.contracts import AnalysisRequest, MarketObservation
from gaijin_market_analytics.enums import AnalysisHorizon, AnalysisStatus
from gaijin_market_analytics.exceptions import AnalyticsError
from gaijin_market_analytics.fees import FeePolicy
from gaijin_market_analytics.market_rules import MarketRules
from gaijin_market_analytics.registry import StrategyRegistry
from gaijin_market_analytics.strategies.base import AnalysisStrategy
from gaijin_market_analytics.backtesting.evaluation import FutureEvaluation, evaluate_future_window


_HORIZONS = {
    7: AnalysisHorizon.DAYS_7,
    30: AnalysisHorizon.DAYS_30,
    90: AnalysisHorizon.DAYS_90,
    180: AnalysisHorizon.DAYS_180,
}


def generate_cutoffs(config: BacktestConfig) -> tuple:
    cutoffs = []
    current = config.start_at
    step = timedelta(days=config.cadence_days)
    while current <= config.end_at:
        cutoffs.append(current)
        current = current + step
    return tuple(cutoffs)


def run_backtest(
    *,
    observations: tuple[MarketObservation, ...],
    config: BacktestConfig,
    fee_policy: FeePolicy,
    market_rules: MarketRules,
    strategy: AnalysisStrategy | None = None,
    registry: StrategyRegistry | None = None,
) -> BacktestResult:
    resolved_strategy = _resolve_strategy(config, strategy, registry)
    ordered = _prepare_observations(observations)
    ordered_times = tuple(observation.observed_at for observation in ordered)
    dataset_max_observed_at = ordered[-1].observed_at if ordered else None

    cases = []
    for cutoff in generate_cutoffs(config):
        lookback_start = cutoff - timedelta(days=config.lookback_horizon_days)
        lookback_left = bisect_left(ordered_times, lookback_start)
        lookback_right = bisect_right(ordered_times, cutoff)
        future_right = bisect_right(ordered_times, cutoff + timedelta(days=config.forward_horizon_days))
        future_left = bisect_right(ordered_times, cutoff)
        lookback = ordered[lookback_left:lookback_right]
        future = ordered[future_left:future_right]
        cases.append(
            _run_case(
                cutoff=cutoff,
                lookback=lookback,
                future=future,
                dataset_max_observed_at=dataset_max_observed_at,
                config=config,
                fee_policy=fee_policy,
                market_rules=market_rules,
                strategy=resolved_strategy,
            )
        )

    case_results = tuple(cases)
    return BacktestResult(
        config=config,
        summary=_summarize(config, market_rules, case_results),
        cases=case_results,
    )


def _resolve_strategy(
    config: BacktestConfig,
    strategy: AnalysisStrategy | None,
    registry: StrategyRegistry | None,
) -> AnalysisStrategy:
    if strategy is not None:
        return strategy
    if registry is None:
        raise AnalyticsError("A strategy or explicit registry must be provided.")
    return registry.get(config.strategy_name, config.strategy_version)


def _run_case(
    *,
    cutoff,
    lookback: tuple[MarketObservation, ...],
    future: tuple[MarketObservation, ...],
    dataset_max_observed_at,
    config: BacktestConfig,
    fee_policy: FeePolicy,
    market_rules: MarketRules,
    strategy: AnalysisStrategy,
) -> BacktestCaseResult:
    request = AnalysisRequest(
        item_id=1,
        horizon=_HORIZONS[config.lookback_horizon_days],
        as_of=cutoff,
        observations=lookback,
        fee_policy=fee_policy,
        market_rules=market_rules,
        maximum_snapshot_age=timedelta(hours=config.maximum_snapshot_age_hours),
        minimum_snapshot_count=config.minimum_snapshot_count,
    )
    analysis = strategy.analyze(request)
    future_window_end = cutoff + timedelta(days=config.forward_horizon_days)
    complete = (
        dataset_max_observed_at is not None and dataset_max_observed_at >= future_window_end
    )
    evaluation = evaluate_future_window(
        cutoff=cutoff,
        future_observations=future,
        analysis=analysis,
        fee_policy=fee_policy,
        market_rules=market_rules,
    )
    skip_reasons = list(evaluation.skip_reasons)
    status = BacktestCaseStatus.EVALUATED
    if analysis.status != AnalysisStatus.OK:
        status = BacktestCaseStatus.ANALYSIS_UNAVAILABLE
        skip_reasons.append(BacktestSkipReason.ANALYSIS_STATUS_NOT_OK)
    elif config.require_complete_forward_window and not complete:
        status = BacktestCaseStatus.FUTURE_DATA_UNAVAILABLE
        skip_reasons.append(BacktestSkipReason.FUTURE_WINDOW_INCOMPLETE)
    elif not future:
        status = BacktestCaseStatus.FUTURE_DATA_UNAVAILABLE
        skip_reasons.append(BacktestSkipReason.NO_FUTURE_OBSERVATIONS)

    return _case_from_evaluation(
        cutoff=cutoff,
        status=status,
        skip_reasons=tuple(skip_reasons),
        config=config,
        market_rules=market_rules,
        evaluation=evaluation,
    )


def _case_from_evaluation(
    *,
    cutoff,
    status: BacktestCaseStatus,
    skip_reasons: tuple[BacktestSkipReason, ...],
    config: BacktestConfig,
    market_rules: MarketRules,
    evaluation: FutureEvaluation,
) -> BacktestCaseResult:
    analysis = evaluation.analysis
    return BacktestCaseResult(
        cutoff_as_of=cutoff,
        status=status,
        skip_reasons=tuple(dict.fromkeys(skip_reasons)),
        strategy_name=analysis.strategy_name,
        strategy_version=analysis.strategy_version,
        market_rules_name=market_rules.name,
        market_rules_version=market_rules.version,
        lookback_horizon_days=config.lookback_horizon_days,
        forward_horizon_days=config.forward_horizon_days,
        analysis_status=analysis.status,
        analysis_reason_codes=analysis.reason_codes,
        observation_count=analysis.observation_count,
        current_ask=analysis.current_ask,
        current_bid=analysis.current_bid,
        reference_sell_price=analysis.reference_sell_price,
        break_even_sell_price=analysis.break_even_sell_price,
        break_even_reachable=analysis.break_even_reachable,
        net_profit=analysis.net_profit,
        net_roi=analysis.net_roi,
        risk_score=analysis.risk_score,
        liquidity_score=analysis.liquidity_score,
        confidence_score=analysis.confidence_score,
        future_observation_count=evaluation.future_observation_count,
        future_valid_bid_count=evaluation.future_valid_bid_count,
        terminal_bid=evaluation.terminal_bid,
        maximum_future_bid=evaluation.maximum_future_bid,
        minimum_future_bid=evaluation.minimum_future_bid,
        reference_reached=evaluation.reference_reached,
        break_even_reached=evaluation.break_even_reached,
        positive_terminal_return=evaluation.positive_terminal_return,
        terminal_sale_proceeds=evaluation.terminal_sale_proceeds,
        terminal_net_profit=evaluation.terminal_net_profit,
        terminal_net_roi=evaluation.terminal_net_roi,
        maximum_sale_proceeds_in_window=evaluation.maximum_sale_proceeds_in_window,
        maximum_net_profit_in_window=evaluation.maximum_net_profit_in_window,
        maximum_net_roi_in_window=evaluation.maximum_net_roi_in_window,
        reference_price_error=evaluation.reference_price_error,
        absolute_reference_price_error=evaluation.absolute_reference_price_error,
        time_to_reference_seconds=evaluation.time_to_reference_seconds,
        time_to_break_even_seconds=evaluation.time_to_break_even_seconds,
    )


def _prepare_observations(
    observations: tuple[MarketObservation, ...],
) -> tuple[MarketObservation, ...]:
    normalized = []
    for index, observation in enumerate(observations):
        key = observation.observation_key
        stable_key = key if key is not None else f"input_index:{index:012d}"
        normalized.append((observation.observed_at, stable_key, index, replace(observation, observation_key=stable_key)))
    return tuple(item[3] for item in sorted(normalized, key=lambda item: (item[0], item[1], item[2])))


def _summarize(
    config: BacktestConfig,
    market_rules: MarketRules,
    cases: tuple[BacktestCaseResult, ...],
) -> BacktestSummary:
    reference_evaluable = tuple(
        case for case in cases if _is_formal(case) and case.reference_sell_price is not None
    )
    break_even_evaluable = tuple(case for case in cases if _is_formal(case) and case.current_ask is not None)
    terminal_return_evaluable = tuple(
        case for case in cases if _is_formal(case) and case.current_ask is not None and case.terminal_bid is not None
    )
    absolute_errors = tuple(
        case.absolute_reference_price_error
        for case in reference_evaluable
        if case.absolute_reference_price_error is not None
    )
    return BacktestSummary(
        strategy_name=config.strategy_name,
        strategy_version=config.strategy_version,
        market_rules_name=market_rules.name,
        market_rules_version=market_rules.version,
        lookback_horizon_days=config.lookback_horizon_days,
        forward_horizon_days=config.forward_horizon_days,
        start_at=config.start_at,
        end_at=config.end_at,
        cadence_days=config.cadence_days,
        require_complete_forward_window=config.require_complete_forward_window,
        total_case_count=len(cases),
        analysis_ok_case_count=sum(1 for case in cases if case.analysis_status == AnalysisStatus.OK),
        evaluated_case_count=sum(1 for case in cases if case.status == BacktestCaseStatus.EVALUATED),
        analysis_unavailable_count=sum(1 for case in cases if case.status == BacktestCaseStatus.ANALYSIS_UNAVAILABLE),
        future_data_unavailable_count=sum(1 for case in cases if case.status == BacktestCaseStatus.FUTURE_DATA_UNAVAILABLE),
        reference_evaluable_count=len(reference_evaluable),
        reference_reached_count=sum(1 for case in reference_evaluable if case.reference_reached is True),
        reference_reach_rate=_rate(sum(1 for case in reference_evaluable if case.reference_reached is True), len(reference_evaluable)),
        break_even_evaluable_count=len(break_even_evaluable),
        break_even_reached_count=sum(1 for case in break_even_evaluable if case.break_even_reached is True),
        break_even_reach_rate=_rate(sum(1 for case in break_even_evaluable if case.break_even_reached is True), len(break_even_evaluable)),
        terminal_return_evaluable_count=len(terminal_return_evaluable),
        positive_terminal_return_count=sum(1 for case in terminal_return_evaluable if case.positive_terminal_return is True),
        positive_terminal_return_rate=_rate(sum(1 for case in terminal_return_evaluable if case.positive_terminal_return is True), len(terminal_return_evaluable)),
        median_terminal_net_roi=_median_decimal(tuple(case.terminal_net_roi for case in terminal_return_evaluable)),
        median_maximum_net_roi=_median_decimal(tuple(case.maximum_net_roi_in_window for case in terminal_return_evaluable)),
        mean_absolute_reference_price_error=_mean_decimal(absolute_errors),
        median_absolute_reference_price_error=_median_decimal(absolute_errors),
        median_time_to_reference_seconds=_median_int(tuple(case.time_to_reference_seconds for case in reference_evaluable)),
        median_time_to_break_even_seconds=_median_int(tuple(case.time_to_break_even_seconds for case in break_even_evaluable)),
    )


def _is_formal(case: BacktestCaseResult) -> bool:
    return case.status == BacktestCaseStatus.EVALUATED


def _rate(count: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return Decimal(count) / Decimal(denominator)


def _median_decimal(values: tuple[Decimal | None, ...]) -> Decimal | None:
    filtered = tuple(sorted(value for value in values if value is not None))
    if not filtered:
        return None
    middle = len(filtered) // 2
    if len(filtered) % 2 == 1:
        return filtered[middle]
    return (filtered[middle - 1] + filtered[middle]) / Decimal("2")


def _mean_decimal(values: tuple[Decimal | None, ...]) -> Decimal | None:
    filtered = tuple(value for value in values if value is not None)
    if not filtered:
        return None
    return sum(filtered, Decimal("0")) / Decimal(len(filtered))


def _median_int(values: tuple[int | None, ...]) -> int | None:
    filtered = tuple(sorted(value for value in values if value is not None))
    if not filtered:
        return None
    middle = len(filtered) // 2
    if len(filtered) % 2 == 1:
        return filtered[middle]
    return (filtered[middle - 1] + filtered[middle]) // 2
