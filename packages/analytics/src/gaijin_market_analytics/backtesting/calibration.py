from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.backtesting.calibration_contracts import (
    CalibrationCaseStatus,
    CalibrationExitReason,
    CalibrationSkipReason,
    CalibrationSplit,
    ItemMarketHistory,
    OpportunityCalibrationCase,
    OpportunityCalibrationConfig,
    OpportunityCalibrationResult,
)
from gaijin_market_analytics.backtesting.calibration_fingerprints import (
    configuration_sha256,
    dataset_sha256,
)
from gaijin_market_analytics.backtesting.calibration_statistics import (
    build_calibration_statistics,
)
from gaijin_market_analytics.backtesting.trade_evidence import (
    build_trade_evidence_calibration,
)
from gaijin_market_analytics.backtesting.trade_evidence_contracts import (
    ItemHistoricalTradeHistory,
    TradeEvidenceConfig,
)
from gaijin_market_analytics.contracts import AnalysisRequest, AnalysisResult, MarketObservation
from gaijin_market_analytics.enums import AnalysisHorizon, AnalysisStatus
from gaijin_market_analytics.exceptions import ContractValidationError
from gaijin_market_analytics.fees import FeePolicy, calculate_sale_proceeds
from gaijin_market_analytics.market_rules import MarketRules, is_valid_market_price
from gaijin_market_analytics.opportunities import OpportunityScoreResult, OpportunityScoreV1
from gaijin_market_analytics.strategies.base import AnalysisStrategy


_HORIZONS = {
    7: AnalysisHorizon.DAYS_7,
    30: AnalysisHorizon.DAYS_30,
    90: AnalysisHorizon.DAYS_90,
    180: AnalysisHorizon.DAYS_180,
}
CALIBRATION_ENGINE_NAME = "opportunity_calibration"
CALIBRATION_ENGINE_VERSION = "1.0.0"
EXECUTION_POLICY_NAME = "repeated_quote_one_unit"
EXECUTION_POLICY_VERSION = "1.0.0"


def generate_calibration_cutoffs(
    config: OpportunityCalibrationConfig,
) -> tuple[datetime, ...]:
    cutoffs = []
    current = config.start_at
    step = timedelta(days=config.cadence_days)
    while current <= config.end_at:
        cutoffs.append(current)
        current += step
    return tuple(cutoffs)


def run_opportunity_calibration(
    *,
    histories: tuple[ItemMarketHistory, ...],
    config: OpportunityCalibrationConfig,
    fee_policy: FeePolicy,
    market_rules: MarketRules,
    analysis_strategy: AnalysisStrategy,
    opportunity_scorer: OpportunityScoreV1 | None = None,
    historical_trade_histories: tuple[ItemHistoricalTradeHistory, ...] = (),
    trade_evidence_config: TradeEvidenceConfig | None = None,
) -> OpportunityCalibrationResult:
    """Walk forward through item histories and calibrate OpportunityScoreV1.

    Every score is produced from observations at or before the cutoff. Future
    observations are passed only to the execution evaluator. Entry and exit
    confirmations are quote-based proxies; they are not claims of real fills.
    """

    item_ids = [history.item_id for history in histories]
    if len(item_ids) != len(set(item_ids)):
        raise ContractValidationError("Calibration histories require unique item_id values.")
    scorer = opportunity_scorer or OpportunityScoreV1()
    cases: list[OpportunityCalibrationCase] = []
    cutoffs = generate_calibration_cutoffs(config)

    for history in sorted(histories, key=lambda value: value.item_id):
        ordered = history.observations
        ordered_times = tuple(observation.observed_at for observation in ordered)
        dataset_max_observed_at = ordered[-1].observed_at if ordered else None
        for cutoff in cutoffs:
            lookback_start = cutoff - timedelta(days=config.lookback_horizon_days)
            lookback = ordered[
                bisect_left(ordered_times, lookback_start):bisect_right(ordered_times, cutoff)
            ]
            analysis = analysis_strategy.analyze(
                AnalysisRequest(
                    item_id=history.item_id,
                    horizon=_HORIZONS[config.lookback_horizon_days],
                    as_of=cutoff,
                    observations=lookback,
                    fee_policy=fee_policy,
                    market_rules=market_rules,
                    maximum_snapshot_age=timedelta(
                        hours=config.maximum_snapshot_age_hours
                    ),
                    minimum_snapshot_count=config.minimum_snapshot_count,
                )
            )
            opportunity = scorer.score(
                analysis=analysis,
                observations=lookback,
                maximum_snapshot_age=timedelta(
                    hours=config.maximum_snapshot_age_hours
                ),
                minimum_snapshot_count=config.minimum_snapshot_count,
            )
            entry_price = analysis.current_ask
            entry_confirmation_count = _entry_confirmation_count(
                lookback,
                entry_price,
                market_rules,
            )
            entry_executed = (
                analysis.status == AnalysisStatus.OK
                and entry_price is not None
                and entry_confirmation_count
                >= config.minimum_entry_quote_observations
            )

            for forward_horizon_days in config.forward_horizon_days:
                future_window_end = cutoff + timedelta(days=forward_horizon_days)
                future = ordered[
                    bisect_right(ordered_times, cutoff):bisect_right(
                        ordered_times,
                        future_window_end,
                    )
                ]
                complete = (
                    dataset_max_observed_at is not None
                    and dataset_max_observed_at >= future_window_end
                )
                cases.append(
                    _evaluate_case(
                        item_id=history.item_id,
                        cutoff=cutoff,
                        split=config.temporal_splits.split_for(cutoff),
                        forward_horizon_days=forward_horizon_days,
                        future_window_end=future_window_end,
                        lookback=lookback,
                        future=future,
                        complete=complete,
                        analysis=analysis,
                        opportunity=opportunity,
                        entry_price=entry_price,
                        entry_confirmation_count=entry_confirmation_count,
                        entry_executed=entry_executed,
                        config=config,
                        fee_policy=fee_policy,
                        market_rules=market_rules,
                    )
                )

    case_results = tuple(cases)
    cohorts, score_bins, correlations = build_calibration_statistics(
        case_results,
        config,
    )
    base_dataset_sha256 = dataset_sha256(histories)
    base_configuration_sha256 = configuration_sha256(
        config=config,
        analysis_strategy=analysis_strategy,
        opportunity_scorer=scorer,
        fee_policy=fee_policy,
        market_rules=market_rules,
    )
    evidence = None
    if historical_trade_histories or trade_evidence_config is not None:
        evidence = build_trade_evidence_calibration(
            market_histories=histories,
            trade_histories=historical_trade_histories,
            cases=case_results,
            calibration_config=config,
            trade_evidence_config=trade_evidence_config or TradeEvidenceConfig(),
            fee_policy=fee_policy,
            base_configuration_sha256=base_configuration_sha256,
        )
    return OpportunityCalibrationResult(
        config=config,
        engine_name=CALIBRATION_ENGINE_NAME,
        engine_version=CALIBRATION_ENGINE_VERSION,
        execution_policy_name=EXECUTION_POLICY_NAME,
        execution_policy_version=EXECUTION_POLICY_VERSION,
        fee_policy_name=fee_policy.name,
        fee_policy_version=fee_policy.version,
        market_rules_name=market_rules.name,
        market_rules_version=market_rules.version,
        dataset_sha256=base_dataset_sha256,
        configuration_sha256=base_configuration_sha256,
        cases=case_results,
        cohorts=cohorts,
        score_bins=score_bins,
        component_correlations=correlations,
        trade_evidence=evidence,
    )


def _evaluate_case(
    *,
    item_id: int,
    cutoff: datetime,
    split: CalibrationSplit,
    forward_horizon_days: int,
    future_window_end: datetime,
    lookback: tuple[MarketObservation, ...],
    future: tuple[MarketObservation, ...],
    complete: bool,
    analysis: AnalysisResult,
    opportunity: OpportunityScoreResult,
    entry_price: Decimal | None,
    entry_confirmation_count: int,
    entry_executed: bool,
    config: OpportunityCalibrationConfig,
    fee_policy: FeePolicy,
    market_rules: MarketRules,
) -> OpportunityCalibrationCase:
    skip_reasons: list[CalibrationSkipReason] = []
    status = CalibrationCaseStatus.EVALUATED
    if analysis.status != AnalysisStatus.OK:
        status = CalibrationCaseStatus.ANALYSIS_UNAVAILABLE
        skip_reasons.append(CalibrationSkipReason.ANALYSIS_STATUS_NOT_OK)
    elif config.require_complete_forward_window and not complete:
        status = CalibrationCaseStatus.FUTURE_DATA_UNAVAILABLE
        skip_reasons.append(CalibrationSkipReason.FUTURE_WINDOW_INCOMPLETE)
    elif not future:
        status = CalibrationCaseStatus.FUTURE_DATA_UNAVAILABLE
        skip_reasons.append(CalibrationSkipReason.NO_FUTURE_OBSERVATIONS)
    elif entry_price is None:
        status = CalibrationCaseStatus.ENTRY_UNAVAILABLE
        skip_reasons.append(CalibrationSkipReason.MISSING_ENTRY_ASK)
    elif not entry_executed:
        status = CalibrationCaseStatus.ENTRY_UNAVAILABLE
        skip_reasons.append(CalibrationSkipReason.ENTRY_QUOTE_NOT_CONFIRMED)

    target_exit_price = analysis.reference_sell_price
    if target_exit_price is None:
        skip_reasons.append(CalibrationSkipReason.MISSING_TARGET_PRICE)
    valid_future_bids = tuple(
        (observation.observed_at, observation.best_bid)
        for observation in future
        if is_valid_market_price(observation.best_bid, market_rules)
    )
    if future and not valid_future_bids:
        skip_reasons.append(CalibrationSkipReason.NO_VALID_FUTURE_BID)

    exit_reason = None
    exit_at = None
    exit_price = None
    exit_confirmation_count = 0
    if status not in {
        CalibrationCaseStatus.ANALYSIS_UNAVAILABLE,
        CalibrationCaseStatus.FUTURE_DATA_UNAVAILABLE,
        CalibrationCaseStatus.ENTRY_UNAVAILABLE,
    }:
        (
            exit_at,
            exit_confirmation_count,
        ) = _confirmed_target_exit(
            future,
            target_exit_price,
            config.minimum_exit_quote_observations,
            market_rules,
        )
        if exit_at is not None and target_exit_price is not None:
            exit_reason = CalibrationExitReason.TARGET_CONFIRMED
            exit_price = target_exit_price
        elif not config.force_terminal_liquidation:
            status = CalibrationCaseStatus.EXIT_UNAVAILABLE
            skip_reasons.append(
                CalibrationSkipReason.TERMINAL_LIQUIDATION_DISABLED
            )
        elif valid_future_bids:
            terminal_at, terminal_bid = valid_future_bids[-1]
            terminal_age = future_window_end - terminal_at
            if terminal_age <= timedelta(
                hours=config.maximum_terminal_snapshot_age_hours
            ):
                exit_reason = CalibrationExitReason.TERMINAL_LIQUIDATION
                exit_at = terminal_at
                exit_price = terminal_bid
            else:
                status = CalibrationCaseStatus.EXIT_UNAVAILABLE
                skip_reasons.append(CalibrationSkipReason.TERMINAL_BID_TOO_OLD)
        else:
            status = CalibrationCaseStatus.EXIT_UNAVAILABLE

    exit_executed = (
        status == CalibrationCaseStatus.EVALUATED
        and entry_executed
        and exit_at is not None
        and exit_price is not None
    )
    sale_proceeds = (
        calculate_sale_proceeds(exit_price, fee_policy)
        if exit_executed and exit_price is not None
        else None
    )
    net_profit = (
        sale_proceeds - entry_price
        if sale_proceeds is not None and entry_price is not None
        else None
    )
    net_roi = (
        net_profit / entry_price
        if net_profit is not None and entry_price is not None
        else None
    )
    holding_seconds = (
        int((exit_at - cutoff).total_seconds())
        if exit_executed and exit_at is not None
        else None
    )
    return OpportunityCalibrationCase(
        item_id=item_id,
        cutoff_as_of=cutoff,
        split=split,
        forward_horizon_days=forward_horizon_days,
        status=status,
        skip_reasons=tuple(skip_reasons),
        analysis_status=analysis.status,
        analysis_strategy_name=analysis.strategy_name,
        analysis_strategy_version=analysis.strategy_version,
        opportunity_strategy_name=opportunity.strategy_name,
        opportunity_strategy_version=opportunity.strategy_version,
        opportunity_feature_version=opportunity.feature_version,
        opportunity_eligible=opportunity.eligible,
        score=opportunity.score,
        profitability_score=opportunity.profitability_score,
        liquidity_score=opportunity.liquidity_score,
        stability_score=opportunity.stability_score,
        data_confidence_score=opportunity.data_confidence_score,
        freshness_score=opportunity.freshness_score,
        risk_penalty=opportunity.risk_penalty,
        observation_count=len(lookback),
        future_observation_count=len(future),
        future_window_complete=complete,
        entry_at=cutoff if entry_executed else None,
        entry_price=entry_price,
        entry_confirmation_count=entry_confirmation_count,
        entry_executed=entry_executed,
        target_exit_price=target_exit_price,
        exit_reason=exit_reason,
        exit_at=exit_at,
        exit_price=exit_price,
        exit_confirmation_count=exit_confirmation_count,
        exit_executed=exit_executed,
        holding_seconds=holding_seconds,
        sale_proceeds=sale_proceeds,
        net_profit=net_profit,
        net_roi=net_roi,
        positive_return=(net_profit > Decimal("0")) if net_profit is not None else None,
    )


def _entry_confirmation_count(
    lookback: tuple[MarketObservation, ...],
    entry_price: Decimal | None,
    market_rules: MarketRules,
) -> int:
    if entry_price is None:
        return 0
    count = 0
    for observation in reversed(lookback):
        ask = observation.best_ask
        if not is_valid_market_price(ask, market_rules):
            break
        if ask <= entry_price:
            count += 1
            continue
        break
    return count


def _confirmed_target_exit(
    future_observations: tuple[MarketObservation, ...],
    target_exit_price: Decimal | None,
    required_confirmations: int,
    market_rules: MarketRules,
) -> tuple[datetime | None, int]:
    if target_exit_price is None:
        return None, 0
    streak = 0
    maximum_streak = 0
    for observation in future_observations:
        bid = observation.best_bid
        if is_valid_market_price(bid, market_rules) and bid >= target_exit_price:
            streak += 1
            maximum_streak = max(maximum_streak, streak)
            if streak >= required_confirmations:
                return observation.observed_at, streak
        else:
            streak = 0
    return None, maximum_streak
