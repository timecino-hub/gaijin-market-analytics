from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from gaijin_market_analytics.backtesting.contracts import BacktestSkipReason
from gaijin_market_analytics.contracts import AnalysisResult, MarketObservation, observation_sort_key
from gaijin_market_analytics.fees import FeePolicy, calculate_net_profit, calculate_net_roi, calculate_sale_proceeds
from gaijin_market_analytics.market_rules import MarketRules, is_valid_market_price


@dataclass(frozen=True, slots=True)
class FutureEvaluation:
    analysis: AnalysisResult
    future_observation_count: int
    future_valid_bid_count: int
    terminal_bid: Decimal | None
    maximum_future_bid: Decimal | None
    minimum_future_bid: Decimal | None
    reference_reached: bool | None
    break_even_reached: bool | None
    positive_terminal_return: bool | None
    terminal_sale_proceeds: Decimal | None
    terminal_net_profit: Decimal | None
    terminal_net_roi: Decimal | None
    maximum_sale_proceeds_in_window: Decimal | None
    maximum_net_profit_in_window: Decimal | None
    maximum_net_roi_in_window: Decimal | None
    reference_price_error: Decimal | None
    absolute_reference_price_error: Decimal | None
    time_to_reference_seconds: int | None
    time_to_break_even_seconds: int | None
    skip_reasons: tuple[BacktestSkipReason, ...]


def evaluate_future_window(
    *,
    cutoff: datetime,
    future_observations: tuple[MarketObservation, ...],
    analysis: AnalysisResult,
    fee_policy: FeePolicy,
    market_rules: MarketRules,
) -> FutureEvaluation:
    ordered_future = tuple(sorted(future_observations, key=observation_sort_key))
    valid_future_bids = tuple(
        (observation.observed_at, observation.best_bid)
        for observation in ordered_future
        if is_valid_market_price(observation.best_bid, market_rules)
    )
    bid_values = tuple(bid for _, bid in valid_future_bids)
    terminal_bid = bid_values[-1] if bid_values else None
    maximum_future_bid = max(bid_values) if bid_values else None
    minimum_future_bid = min(bid_values) if bid_values else None
    skip_reasons = []
    if ordered_future and not bid_values:
        skip_reasons.append(BacktestSkipReason.NO_VALID_FUTURE_BID)
    if analysis.current_ask is None:
        skip_reasons.append(BacktestSkipReason.MISSING_ENTRY_ASK)
    if analysis.reference_sell_price is None:
        skip_reasons.append(BacktestSkipReason.MISSING_REFERENCE_SELL_PRICE)

    reference_reached, time_to_reference_seconds = _reference_reach(
        cutoff=cutoff,
        current_bid=analysis.current_bid,
        reference_sell_price=analysis.reference_sell_price,
        valid_future_bids=valid_future_bids,
        market_rules=market_rules,
    )
    break_even_reached, time_to_break_even_seconds = _break_even_reach(
        cutoff=cutoff,
        current_bid=analysis.current_bid,
        entry_ask=analysis.current_ask,
        valid_future_bids=valid_future_bids,
        fee_policy=fee_policy,
        market_rules=market_rules,
    )
    terminal_sale_proceeds = calculate_sale_proceeds(terminal_bid, fee_policy) if terminal_bid is not None else None
    terminal_net_profit = (
        terminal_sale_proceeds - analysis.current_ask
        if terminal_sale_proceeds is not None and analysis.current_ask is not None
        else None
    )
    terminal_net_roi = (
        terminal_net_profit / analysis.current_ask
        if terminal_net_profit is not None and analysis.current_ask is not None
        else None
    )
    positive_terminal_return = terminal_net_profit > Decimal("0") if terminal_net_profit is not None else None
    maximum_sale_proceeds = (
        calculate_sale_proceeds(maximum_future_bid, fee_policy)
        if maximum_future_bid is not None
        else None
    )
    maximum_net_profit = (
        calculate_net_profit(analysis.current_ask, maximum_future_bid, fee_policy)
        if maximum_future_bid is not None and analysis.current_ask is not None
        else None
    )
    maximum_net_roi = (
        calculate_net_roi(analysis.current_ask, maximum_future_bid, fee_policy)
        if maximum_future_bid is not None and analysis.current_ask is not None
        else None
    )
    reference_price_error = (
        terminal_bid - analysis.reference_sell_price
        if terminal_bid is not None and analysis.reference_sell_price is not None
        else None
    )
    absolute_reference_price_error = (
        abs(reference_price_error) if reference_price_error is not None else None
    )
    return FutureEvaluation(
        analysis=analysis,
        future_observation_count=len(ordered_future),
        future_valid_bid_count=len(valid_future_bids),
        terminal_bid=terminal_bid,
        maximum_future_bid=maximum_future_bid,
        minimum_future_bid=minimum_future_bid,
        reference_reached=reference_reached,
        break_even_reached=break_even_reached,
        positive_terminal_return=positive_terminal_return,
        terminal_sale_proceeds=terminal_sale_proceeds,
        terminal_net_profit=terminal_net_profit,
        terminal_net_roi=terminal_net_roi,
        maximum_sale_proceeds_in_window=maximum_sale_proceeds,
        maximum_net_profit_in_window=maximum_net_profit,
        maximum_net_roi_in_window=maximum_net_roi,
        reference_price_error=reference_price_error,
        absolute_reference_price_error=absolute_reference_price_error,
        time_to_reference_seconds=time_to_reference_seconds,
        time_to_break_even_seconds=time_to_break_even_seconds,
        skip_reasons=tuple(dict.fromkeys(skip_reasons)),
    )


def _reference_reach(
    *,
    cutoff: datetime,
    current_bid: Decimal | None,
    reference_sell_price: Decimal | None,
    valid_future_bids: tuple[tuple[datetime, Decimal], ...],
    market_rules: MarketRules,
) -> tuple[bool | None, int | None]:
    if reference_sell_price is None:
        return None, None
    if is_valid_market_price(current_bid, market_rules) and current_bid >= reference_sell_price:
        return True, 0
    for observed_at, bid in valid_future_bids:
        if bid >= reference_sell_price:
            return True, _seconds_between(cutoff, observed_at)
    return False, None


def _break_even_reach(
    *,
    cutoff: datetime,
    current_bid: Decimal | None,
    entry_ask: Decimal | None,
    valid_future_bids: tuple[tuple[datetime, Decimal], ...],
    fee_policy: FeePolicy,
    market_rules: MarketRules,
) -> tuple[bool | None, int | None]:
    if entry_ask is None:
        return None, None
    if is_valid_market_price(current_bid, market_rules) and calculate_sale_proceeds(current_bid, fee_policy) >= entry_ask:
        return True, 0
    for observed_at, bid in valid_future_bids:
        if calculate_sale_proceeds(bid, fee_policy) >= entry_ask:
            return True, _seconds_between(cutoff, observed_at)
    return False, None


def _seconds_between(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds())
