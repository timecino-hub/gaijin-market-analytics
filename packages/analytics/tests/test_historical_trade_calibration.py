from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.backtesting import (
    HistoricalTradeBucket,
    HistoricalTradeGranularity,
    ItemHistoricalTradeHistory,
    ItemMarketHistory,
    OpportunityCalibrationConfig,
    TemporalSplitConfig,
    TradeEvidenceConfig,
    TradeEvidenceLevel,
    TradeEvidenceSegmentDimension,
    TradeEvidenceView,
    TradeMarketPhase,
    run_opportunity_calibration,
)
from gaijin_market_analytics.contracts import MarketObservation
from gaijin_market_analytics.exceptions import ContractValidationError
from gaijin_market_analytics.fees import GAIJIN_MARKET_FEE_POLICY_V1
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1
from gaijin_market_analytics.strategies.rule_based_v1 import RuleBasedV1


BASE = datetime(2026, 1, 8, tzinfo=UTC)


def observation(
    at: datetime,
    *,
    ask: str = "10.00",
    bid: str = "13.00",
    key: str,
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


def market_history(
    *,
    item_id: int = 1,
    target_quotes: bool = False,
) -> ItemMarketHistory:
    future = (
        observation(
            BASE + timedelta(days=1),
            bid="13.00" if target_quotes else "12.00",
            key="future-1",
        ),
        observation(
            BASE + timedelta(days=2),
            bid="13.00" if target_quotes else "12.00",
            key="future-2",
        ),
        observation(BASE + timedelta(days=7), bid="12.00", key="terminal"),
        observation(BASE + timedelta(days=9), bid="12.00", key="tail"),
    )
    return ItemMarketHistory(
        item_id=item_id,
        observations=(
            observation(BASE - timedelta(days=7), key="window-start"),
            observation(BASE - timedelta(days=1), key="entry-confirmation"),
            observation(BASE, key="cutoff"),
            *future,
        ),
    )


def calibration_config() -> OpportunityCalibrationConfig:
    return OpportunityCalibrationConfig(
        lookback_horizon_days=7,
        forward_horizon_days=(7,),
        start_at=BASE,
        end_at=BASE + timedelta(days=2),
        cadence_days=30,
        temporal_splits=TemporalSplitConfig(
            train_end_at=BASE,
            validation_end_at=BASE + timedelta(days=1),
        ),
        maximum_snapshot_age_hours=24,
        minimum_snapshot_count=2,
        minimum_entry_quote_observations=2,
        minimum_exit_quote_observations=2,
        maximum_terminal_snapshot_age_hours=24,
    )


def bucket(
    start: datetime,
    price: str,
    volume: int,
    *,
    item_id: int = 1,
    granularity: HistoricalTradeGranularity = HistoricalTradeGranularity.HOUR_1,
) -> HistoricalTradeBucket:
    return HistoricalTradeBucket(
        item_id=item_id,
        granularity=granularity,
        bucket_start_utc=start,
        bucket_duration_seconds=granularity.duration_seconds,
        reported_vwap_price=Decimal(price),
        reported_trade_volume=volume,
    )


def run_with_trade(
    buckets: tuple[HistoricalTradeBucket, ...],
    *,
    target_quotes: bool = False,
    evidence_config: TradeEvidenceConfig | None = None,
):
    history = market_history(target_quotes=target_quotes)
    return run_opportunity_calibration(
        histories=(history,),
        config=calibration_config(),
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
        historical_trade_histories=(
            ItemHistoricalTradeHistory(item_id=1, buckets=buckets),
        ),
        trade_evidence_config=evidence_config,
    )


def test_historical_trade_bucket_requires_aligned_completed_contract() -> None:
    with pytest.raises(ContractValidationError, match="align"):
        bucket(BASE + timedelta(minutes=1), "13.00", 1)
    with pytest.raises(ContractValidationError, match="duration"):
        HistoricalTradeBucket(
            item_id=1,
            granularity=HistoricalTradeGranularity.HOUR_1,
            bucket_start_utc=BASE,
            bucket_duration_seconds=86_400,
            reported_vwap_price=Decimal("13.00"),
            reported_trade_volume=1,
        )
    with pytest.raises(ContractValidationError, match="price semantics"):
        HistoricalTradeBucket(
            item_id=1,
            granularity=HistoricalTradeGranularity.HOUR_1,
            bucket_start_utc=BASE,
            bucket_duration_seconds=3_600,
            reported_vwap_price=Decimal("13.00"),
            reported_trade_volume=1,
            price_semantics="close_price",
        )


def test_future_evidence_uses_only_buckets_wholly_after_cutoff_and_before_horizon() -> None:
    result = run_with_trade(
        (
            bucket(BASE - timedelta(hours=1), "20.00", 10),
            bucket(BASE + timedelta(days=7), "20.00", 10),
        )
    )
    evidence = result.trade_evidence
    assert evidence is not None
    case = evidence.cases[0]

    assert case.selected_trade_bucket_count == 0
    assert case.trade_vwap_support is False
    assert case.strongest_evidence == TradeEvidenceLevel.UNRESOLVED


def test_hourly_buckets_take_precedence_over_daily_bucket_for_the_same_utc_day() -> None:
    result = run_with_trade(
        (
            bucket(
                BASE + timedelta(days=1),
                "20.00",
                10,
                granularity=HistoricalTradeGranularity.DAY_1,
            ),
            bucket(BASE + timedelta(days=1), "12.00", 1),
        )
    )
    case = result.trade_evidence.cases[0]  # type: ignore[union-attr]

    assert case.selected_hourly_bucket_count == 1
    assert case.selected_daily_fallback_count == 0
    assert case.trade_vwap_support is False


def test_daily_bucket_is_used_only_as_fallback_when_hourly_data_is_absent() -> None:
    result = run_with_trade(
        (
            bucket(
                BASE + timedelta(days=1),
                "13.50",
                3,
                granularity=HistoricalTradeGranularity.DAY_1,
            ),
        )
    )
    case = result.trade_evidence.cases[0]  # type: ignore[union-attr]

    assert case.selected_hourly_bucket_count == 0
    assert case.selected_daily_fallback_count == 1
    assert case.trade_volume_support is True
    assert case.trade_support_granularity == HistoricalTradeGranularity.DAY_1
    assert case.trade_support_at == BASE + timedelta(days=2)


def test_trade_vwap_and_volume_support_are_distinct_and_never_credit_extra_upside() -> None:
    result = run_with_trade(
        (
            bucket(BASE + timedelta(days=1), "13.50", 1),
            bucket(BASE + timedelta(days=1, hours=1), "20.00", 3),
        )
    )
    case = result.trade_evidence.cases[0]  # type: ignore[union-attr]

    assert case.trade_vwap_support is True
    assert case.trade_volume_support is True
    assert case.strongest_evidence == TradeEvidenceLevel.TRADE_VOLUME_SUPPORT
    assert case.trade_support_vwap == Decimal("20.00")
    assert case.supported_sale_proceeds == Decimal("11.05")
    assert case.supported_net_profit == Decimal("1.05")
    assert case.supported_net_roi == Decimal("0.105")


def test_quote_trade_and_combined_views_are_reported_separately() -> None:
    result = run_with_trade(
        (bucket(BASE + timedelta(days=1), "13.50", 3),),
        target_quotes=True,
    )
    evidence = result.trade_evidence
    assert evidence is not None
    case = evidence.cases[0]
    assert case.quote_touch is True
    assert case.trade_vwap_support is True
    assert case.combined_support is True

    all_train = {
        cohort.view: cohort
        for cohort in evidence.cohorts
        if cohort.split is None and cohort.forward_horizon_days == 7
    }
    assert all_train[TradeEvidenceView.QUOTE_ONLY].supported_case_count == 1
    assert all_train[TradeEvidenceView.TRADE_SUPPORTED].supported_case_count == 1
    assert all_train[TradeEvidenceView.COMBINED].supported_case_count == 1


def test_trade_evidence_fingerprints_cover_trade_data_and_configuration() -> None:
    first = run_with_trade((bucket(BASE + timedelta(days=1), "13.50", 2),))
    second = run_with_trade((bucket(BASE + timedelta(days=1), "13.60", 2),))
    reconfigured = run_with_trade(
        (bucket(BASE + timedelta(days=1), "13.50", 2),),
        evidence_config=TradeEvidenceConfig(minimum_reported_trade_volume=3),
    )

    assert first.dataset_sha256 == second.dataset_sha256
    assert first.configuration_sha256 == second.configuration_sha256
    assert first.trade_evidence is not None
    assert second.trade_evidence is not None
    assert reconfigured.trade_evidence is not None
    assert first.trade_evidence.dataset_sha256 != second.trade_evidence.dataset_sha256
    assert (
        first.trade_evidence.configuration_sha256
        != reconfigured.trade_evidence.configuration_sha256
    )


def test_no_trade_input_preserves_the_original_quote_only_result_contract() -> None:
    history = market_history()
    result = run_opportunity_calibration(
        histories=(history,),
        config=calibration_config(),
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
    )

    assert result.engine_version == "1.0.0"
    assert result.execution_policy_version == "1.0.0"
    assert result.trade_evidence is None


def test_market_phase_is_point_in_time_and_future_shock_does_not_leak_backward() -> None:
    prior = (
        bucket(
            BASE - timedelta(days=4),
            "10.00",
            2,
            granularity=HistoricalTradeGranularity.DAY_1,
        ),
        bucket(
            BASE - timedelta(days=3),
            "10.00",
            2,
            granularity=HistoricalTradeGranularity.DAY_1,
        ),
        bucket(
            BASE - timedelta(days=2),
            "10.00",
            2,
            granularity=HistoricalTradeGranularity.DAY_1,
        ),
        bucket(
            BASE - timedelta(days=1),
            "5.00",
            10,
            granularity=HistoricalTradeGranularity.DAY_1,
        ),
    )
    future_shock_only = prior[:-1] + (
        bucket(
            BASE + timedelta(days=1),
            "5.00",
            10,
            granularity=HistoricalTradeGranularity.DAY_1,
        ),
    )

    shock_case = run_with_trade(prior).trade_evidence.cases[0]  # type: ignore[union-attr]
    future_evidence = run_with_trade(future_shock_only).trade_evidence
    assert future_evidence is not None
    future_case = future_evidence.cases[0]

    assert shock_case.market_phase == TradeMarketPhase.SUPPLY_SHOCK
    assert future_case.market_phase == TradeMarketPhase.NORMAL


def test_segments_include_score_liquidity_and_point_in_time_market_phase() -> None:
    evidence = run_with_trade(
        (bucket(BASE + timedelta(days=1), "13.50", 3),)
    ).trade_evidence
    assert evidence is not None
    dimensions = {
        segment.dimension
        for segment in evidence.segments
        if segment.split is None
        and segment.forward_horizon_days == 7
        and segment.view is TradeEvidenceView.TRADE_SUPPORTED
    }

    assert dimensions == {
        TradeEvidenceSegmentDimension.SCORE_BIN,
        TradeEvidenceSegmentDimension.LIQUIDITY_BAND,
        TradeEvidenceSegmentDimension.MARKET_PHASE,
    }


def test_missing_trade_history_is_explicitly_unresolved_not_an_error() -> None:
    result = run_opportunity_calibration(
        histories=(market_history(),),
        config=calibration_config(),
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
        trade_evidence_config=TradeEvidenceConfig(),
    )
    evidence = result.trade_evidence
    assert evidence is not None
    assert evidence.cases[0].strongest_evidence == TradeEvidenceLevel.UNRESOLVED
    assert evidence.cases[0].market_phase == TradeMarketPhase.UNAVAILABLE
