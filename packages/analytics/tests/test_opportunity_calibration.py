from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.backtesting import (
    CalibrationCaseStatus,
    CalibrationExitReason,
    CalibrationSplit,
    ItemMarketHistory,
    OpportunityCalibrationConfig,
    TemporalSplitConfig,
    run_opportunity_calibration,
)
from gaijin_market_analytics.contracts import MarketObservation
from gaijin_market_analytics.exceptions import ContractValidationError
from gaijin_market_analytics.fees import GAIJIN_MARKET_FEE_POLICY_V1
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1
from gaijin_market_analytics.opportunities import (
    OpportunityScoreConfig,
    OpportunityScoreResult,
    OpportunityScoreV1,
)
from gaijin_market_analytics.strategies.rule_based_v1 import RuleBasedV1


BASE = datetime(2026, 1, 8, tzinfo=UTC)


def observation(
    at: datetime,
    *,
    ask: str = "10.00",
    bid: str = "13.00",
    key: str | None = None,
) -> MarketObservation:
    return MarketObservation(
        observed_at=at,
        best_ask=Decimal(ask),
        best_bid=Decimal(bid),
        ask_count=20,
        bid_count=20,
        estimated_volume=Decimal("100"),
        observation_key=key,
    )


def config(**overrides: object) -> OpportunityCalibrationConfig:
    values = {
        "lookback_horizon_days": 7,
        "forward_horizon_days": (7,),
        "start_at": BASE,
        "end_at": BASE + timedelta(days=2),
        "cadence_days": 1,
        "temporal_splits": TemporalSplitConfig(
            train_end_at=BASE,
            validation_end_at=BASE + timedelta(days=1),
        ),
        "maximum_snapshot_age_hours": 24,
        "minimum_snapshot_count": 2,
        "minimum_entry_quote_observations": 2,
        "minimum_exit_quote_observations": 2,
        "maximum_terminal_snapshot_age_hours": 24,
    }
    values.update(overrides)
    return OpportunityCalibrationConfig(**values)


def standard_history(
    *,
    item_id: int = 1,
    future: tuple[MarketObservation, ...],
    previous_ask: str = "10.00",
) -> ItemMarketHistory:
    lookback = (
        observation(BASE - timedelta(days=7), key="window-start"),
        observation(
            BASE - timedelta(days=1),
            ask=previous_ask,
            key="entry-confirmation",
        ),
        observation(BASE, key="cutoff"),
    )
    tail = (observation(BASE + timedelta(days=9), bid="12.00", key="dataset-tail"),)
    return ItemMarketHistory(
        item_id=item_id,
        observations=lookback + future + tail,
    )


def run_single(
    history: ItemMarketHistory,
    *,
    calibration_config: OpportunityCalibrationConfig | None = None,
):
    return run_opportunity_calibration(
        histories=(history,),
        config=calibration_config or config(cadence_days=30),
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
    )


def test_config_requires_time_splits_horizons_and_strict_score_bins() -> None:
    with pytest.raises(ContractValidationError, match="test split"):
        config(
            temporal_splits=TemporalSplitConfig(
                train_end_at=BASE,
                validation_end_at=BASE + timedelta(days=2),
            )
        )
    with pytest.raises(ContractValidationError, match="forward_horizon_days"):
        config(forward_horizon_days=(8,))
    with pytest.raises(ContractValidationError, match="strictly increasing"):
        config(score_bin_edges=(Decimal("0"), Decimal("50"), Decimal("50"), Decimal("100")))


def test_walk_forward_analysis_never_receives_future_observations_and_labels_splits() -> None:
    class RecordingRuleBased(RuleBasedV1):
        def __init__(self) -> None:
            super().__init__()
            self.requests = []

        def analyze(self, request):
            self.requests.append(request)
            return super().analyze(request)

    strategy = RecordingRuleBased()
    history = ItemMarketHistory(
        item_id=1,
        observations=tuple(
            observation(BASE - timedelta(days=7) + timedelta(days=day), key=f"day-{day}")
            for day in range(17)
        ),
    )
    result = run_opportunity_calibration(
        histories=(history,),
        config=config(),
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=strategy,
    )

    assert [case.split for case in result.cases] == [
        CalibrationSplit.TRAIN,
        CalibrationSplit.VALIDATION,
        CalibrationSplit.TEST,
    ]
    assert len(strategy.requests) == 3
    for request in strategy.requests:
        assert all(value.observed_at <= request.as_of for value in request.observations)


def test_two_consecutive_target_quotes_are_required_and_fee_rounding_is_exact() -> None:
    history = standard_history(
        future=(
            observation(BASE + timedelta(days=1), bid="13.00", key="target-1"),
            observation(BASE + timedelta(days=2), bid="13.00", key="target-2"),
            observation(BASE + timedelta(days=7), bid="12.00", key="terminal"),
        )
    )

    case = run_single(history).cases[0]

    assert case.status == CalibrationCaseStatus.EVALUATED
    assert case.exit_reason == CalibrationExitReason.TARGET_CONFIRMED
    assert case.exit_at == BASE + timedelta(days=2)
    assert case.exit_price == Decimal("13.00")
    assert case.exit_confirmation_count == 2
    assert case.sale_proceeds == Decimal("11.05")
    assert case.net_profit == Decimal("1.05")
    assert case.net_roi == Decimal("0.105")


def test_single_target_spike_is_rejected_and_terminal_quote_is_used() -> None:
    history = standard_history(
        future=(
            observation(BASE + timedelta(days=1), bid="13.00", key="spike"),
            observation(BASE + timedelta(days=2), bid="12.00", key="reset"),
            observation(BASE + timedelta(days=7), bid="12.00", key="terminal"),
        )
    )

    case = run_single(history).cases[0]

    assert case.exit_reason == CalibrationExitReason.TERMINAL_LIQUIDATION
    assert case.exit_confirmation_count == 1
    assert case.exit_price == Decimal("12.00")
    assert case.sale_proceeds == Decimal("10.20")
    assert case.net_profit == Decimal("0.20")
    assert case.net_roi == Decimal("0.02")


def test_entry_requires_repeated_recent_quote_without_using_future_asks() -> None:
    history = standard_history(
        previous_ask="11.00",
        future=(
            observation(BASE + timedelta(days=1), ask="9.00", bid="13.00", key="future-cheap"),
            observation(BASE + timedelta(days=7), bid="13.00", key="terminal"),
        ),
    )

    case = run_single(history).cases[0]

    assert case.status == CalibrationCaseStatus.ENTRY_UNAVAILABLE
    assert case.entry_confirmation_count == 1
    assert case.entry_executed is False
    assert case.exit_executed is False


def test_stale_terminal_bid_does_not_create_a_fictitious_liquidation() -> None:
    history = standard_history(
        future=(
            observation(BASE + timedelta(days=1), bid="12.00", key="old-bid"),
            observation(BASE + timedelta(days=7), ask="10.00", bid="0", key="window-end"),
        )
    )

    case = run_single(
        history,
        calibration_config=config(
            cadence_days=30,
            maximum_terminal_snapshot_age_hours=24,
        ),
    ).cases[0]

    assert case.status == CalibrationCaseStatus.EXIT_UNAVAILABLE
    assert case.exit_executed is False
    assert "terminal_bid_too_old" in {value.value for value in case.skip_reasons}


class FixedScore:
    strategy_name = "opportunity_score"
    strategy_version = "test"
    feature_version = "test_features"

    def score(self, *, analysis, observations, maximum_snapshot_age, minimum_snapshot_count):
        score = {1: Decimal("20"), 2: Decimal("50"), 3: Decimal("80")}[analysis.item_id]
        return OpportunityScoreResult(
            item_id=analysis.item_id,
            as_of=analysis.as_of,
            eligible=True,
            score=score,
            raw_score=score,
            profitability_score=Decimal("50"),
            liquidity_score=score,
            stability_score=score,
            data_confidence_score=score,
            freshness_score=score,
            risk_penalty=Decimal("0"),
            liquidity_source="snapshot_counts",
            quantity_observation_count=len(observations),
            latest_observed_bid_quantity=None,
            latest_observed_ask_quantity=None,
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            feature_version=self.feature_version,
            explanation_codes=("synthetic_fixed_score",),
        )


def test_score_calibration_reports_monotonic_bins_correlation_and_portfolio_metrics() -> None:
    histories = []
    for item_id, terminal_bid in ((1, "9.00"), (2, "12.00"), (3, "15.00")):
        histories.append(
            ItemMarketHistory(
                item_id=item_id,
                observations=(
                    observation(BASE - timedelta(days=7), bid="100.00", key="start"),
                    observation(BASE - timedelta(days=1), bid="100.00", key="confirm"),
                    observation(BASE, bid="100.00", key="cutoff"),
                    observation(
                        BASE + timedelta(days=7),
                        bid=terminal_bid,
                        key="terminal",
                    ),
                ),
            )
        )
    calibration_config = OpportunityCalibrationConfig(
        lookback_horizon_days=7,
        forward_horizon_days=(7,),
        start_at=BASE,
        end_at=BASE + timedelta(days=30),
        cadence_days=31,
        temporal_splits=TemporalSplitConfig(
            train_end_at=BASE,
            validation_end_at=BASE + timedelta(days=15),
        ),
        maximum_snapshot_age_hours=24,
        minimum_snapshot_count=2,
        minimum_entry_quote_observations=2,
        minimum_exit_quote_observations=2,
        maximum_terminal_snapshot_age_hours=24,
    )

    result = run_opportunity_calibration(
        histories=tuple(histories),
        config=calibration_config,
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
        opportunity_scorer=FixedScore(),  # type: ignore[arg-type]
    )
    cohort = next(
        value
        for value in result.cohorts
        if value.split is None and value.forward_horizon_days == 7
    )

    assert cohort.realized_trade_count == 3
    assert cohort.score_roi_spearman == Decimal("1.0000")
    assert cohort.profitability_baseline_spearman is None
    assert cohort.adjacent_bin_monotonicity_rate == Decimal("1.0000")
    assert cohort.peak_concurrent_entry_cost == Decimal("30.00")
    assert cohort.ending_equity == Decimal("30.60")
    assert cohort.maximum_drawdown == Decimal("2.35")
    assert cohort.maximum_drawdown_rate == Decimal("0.07833333333333333333333333333")
    assert "higher_scores_associated_with_higher_returns" in cohort.assessment_codes


def test_missing_bid_breaks_target_confirmation_streak() -> None:
    history = standard_history(
        future=(
            observation(BASE + timedelta(days=1), bid="13.00", key="target-1"),
            MarketObservation(
                observed_at=BASE + timedelta(days=2),
                best_ask=Decimal("10.00"),
                best_bid=None,
                ask_count=20,
                bid_count=20,
                estimated_volume=Decimal("100"),
                observation_key="missing-bid",
            ),
            observation(BASE + timedelta(days=3), bid="13.00", key="target-2"),
            observation(BASE + timedelta(days=7), bid="12.00", key="terminal"),
        )
    )

    case = run_single(history).cases[0]

    assert case.exit_reason == CalibrationExitReason.TERMINAL_LIQUIDATION
    assert case.exit_confirmation_count == 1


def test_multiple_holding_horizons_are_evaluated_from_the_same_point_in_time_score() -> None:
    history = ItemMarketHistory(
        item_id=1,
        observations=(
            observation(BASE - timedelta(days=7), key="start"),
            observation(BASE - timedelta(days=1), key="confirm"),
            observation(BASE, key="cutoff"),
            observation(BASE + timedelta(days=7), bid="12.00", key="day-7"),
            observation(BASE + timedelta(days=30), bid="14.00", key="day-30"),
        ),
    )
    calibration_config = OpportunityCalibrationConfig(
        lookback_horizon_days=7,
        forward_horizon_days=(30, 7),
        start_at=BASE,
        end_at=BASE + timedelta(days=40),
        cadence_days=41,
        temporal_splits=TemporalSplitConfig(
            train_end_at=BASE,
            validation_end_at=BASE + timedelta(days=20),
        ),
        maximum_snapshot_age_hours=24,
        minimum_snapshot_count=2,
        minimum_entry_quote_observations=2,
        minimum_exit_quote_observations=2,
        maximum_terminal_snapshot_age_hours=24,
    )

    result = run_opportunity_calibration(
        histories=(history,),
        config=calibration_config,
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
    )

    assert result.config.forward_horizon_days == (7, 30)
    assert [case.forward_horizon_days for case in result.cases] == [7, 30]
    assert result.cases[0].score == result.cases[1].score
    assert result.cases[0].cutoff_as_of == result.cases[1].cutoff_as_of


def test_dataset_and_configuration_hashes_are_deterministic() -> None:
    first = standard_history(
        item_id=1,
        future=(
            observation(BASE + timedelta(days=7), bid="12.00", key="terminal"),
        ),
    )
    second = standard_history(
        item_id=2,
        future=(
            observation(BASE + timedelta(days=7), bid="14.00", key="terminal"),
        ),
    )
    calibration_config = config(cadence_days=30)

    forward = run_opportunity_calibration(
        histories=(first, second),
        config=calibration_config,
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
    )
    reversed_input = run_opportunity_calibration(
        histories=(second, first),
        config=calibration_config,
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
    )

    assert forward.dataset_sha256 == reversed_input.dataset_sha256
    assert forward.configuration_sha256 == reversed_input.configuration_sha256
    assert len(forward.dataset_sha256) == 64
    assert len(forward.configuration_sha256) == 64
    assert forward.engine_version == "1.0.0"
    assert forward.execution_policy_version == "1.0.0"


def test_incomplete_future_window_is_explicit_even_when_partial_evaluation_is_allowed() -> None:
    history = ItemMarketHistory(
        item_id=1,
        observations=(
            observation(BASE - timedelta(days=7), key="start"),
            observation(BASE - timedelta(days=1), key="confirm"),
            observation(BASE, key="cutoff"),
            observation(BASE + timedelta(days=1), bid="12.00", key="partial"),
        ),
    )
    result = run_single(
        history,
        calibration_config=config(
            cadence_days=30,
            require_complete_forward_window=False,
        ),
    )
    case = result.cases[0]
    cohort = next(
        value
        for value in result.cohorts
        if value.split is None and value.forward_horizon_days == 7
    )

    assert case.future_window_complete is False
    assert case.status == CalibrationCaseStatus.EXIT_UNAVAILABLE
    assert cohort.complete_future_case_count == 0


def test_configuration_hash_covers_opportunity_score_parameters() -> None:
    history = standard_history(
        future=(
            observation(BASE + timedelta(days=7), bid="12.00", key="terminal"),
        ),
    )
    calibration_config = config(cadence_days=30)
    default = run_opportunity_calibration(
        histories=(history,),
        config=calibration_config,
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
        opportunity_scorer=OpportunityScoreV1(),
    )
    reweighted = run_opportunity_calibration(
        histories=(history,),
        config=calibration_config,
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
        opportunity_scorer=OpportunityScoreV1(
            OpportunityScoreConfig(
                profitability_weight=Decimal("40"),
                liquidity_weight=Decimal("20"),
            )
        ),
    )

    assert default.dataset_sha256 == reweighted.dataset_sha256
    assert default.configuration_sha256 != reweighted.configuration_sha256


def test_calibration_does_not_merge_alternative_holding_horizons_into_one_cohort() -> None:
    history = ItemMarketHistory(
        item_id=1,
        observations=(
            observation(BASE - timedelta(days=7), key="start"),
            observation(BASE - timedelta(days=1), key="confirm"),
            observation(BASE, key="cutoff"),
            observation(BASE + timedelta(days=7), bid="12.00", key="day-7"),
            observation(BASE + timedelta(days=30), bid="14.00", key="day-30"),
        ),
    )
    calibration_config = OpportunityCalibrationConfig(
        lookback_horizon_days=7,
        forward_horizon_days=(7, 30),
        start_at=BASE,
        end_at=BASE + timedelta(days=40),
        cadence_days=41,
        temporal_splits=TemporalSplitConfig(
            train_end_at=BASE,
            validation_end_at=BASE + timedelta(days=20),
        ),
        maximum_snapshot_age_hours=24,
        minimum_snapshot_count=2,
    )
    result = run_opportunity_calibration(
        histories=(history,),
        config=calibration_config,
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
    )

    assert all(cohort.forward_horizon_days is not None for cohort in result.cohorts)
    assert {(cohort.split, cohort.forward_horizon_days) for cohort in result.cohorts} == {
        (None, 7),
        (None, 30),
        (CalibrationSplit.TRAIN, 7),
        (CalibrationSplit.TRAIN, 30),
        (CalibrationSplit.VALIDATION, 7),
        (CalibrationSplit.VALIDATION, 30),
        (CalibrationSplit.TEST, 7),
        (CalibrationSplit.TEST, 30),
    }
