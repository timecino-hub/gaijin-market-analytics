from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.manual_context import ResolvedManualMarketContext
from gaijin_market_analytics.post_rerelease import (
    PostReReleaseConfig,
    estimate_post_rerelease_reference,
    evaluate_post_rerelease_outcome,
    first_post_event_trade_proxy,
    sale_proceeds,
)
from gaijin_market_analytics.supply_cycle_recovery import (
    SupplyRecoveryConfig,
    analyze_item_supply_recovery,
    build_supply_recovery_profile,
    measure_supply_event_recovery,
)
from gaijin_market_analytics.supply_cycles import SupplyPoolEvent, SupplyPoolMembership


def daily(item_id: int, day: int, price: str, volume: int = 1) -> HistoricalTradeBucketEvidence:
    return HistoricalTradeBucketEvidence(
        item_id=item_id,
        granularity="1d",
        bucket_start_utc=datetime(2026, 1, day, tzinfo=UTC),
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal(price),
        reported_trade_volume=volume,
    )


def event(key: str, open_day: int, close_day: int, *, known_day: int = 1) -> SupplyPoolEvent:
    return SupplyPoolEvent(
        event_key=key,
        family="SUMMER",
        known_at=datetime(2026, 1, known_day, tzinfo=UTC),
        opens_at=datetime(2026, 1, open_day, tzinfo=UTC),
        closes_at=datetime(2026, 1, close_day, tzinfo=UTC),
    )


def test_recovery_checkpoint_is_point_in_time_safe_against_future_mutation():
    e = event("return", 5, 9)
    history = tuple(
        daily(1, day, "100" if day < 5 else "60" if day < 9 else "70")
        for day in range(1, 16)
    )
    cfg = SupplyRecoveryConfig(checkpoint_horizons_days=(3,), recovery_fractions=(Decimal("0.5"),))
    as_of = datetime(2026, 1, 14, tzinfo=UTC)
    baseline = measure_supply_event_recovery(
        event=e, historical_buckets=history, as_of=as_of, config=cfg
    )
    future = daily(1, 16, "999", 999)
    changed = measure_supply_event_recovery(
        event=e,
        historical_buckets=(*history, future),
        as_of=as_of,
        config=cfg,
    )
    assert baseline == changed
    checkpoint = baseline.checkpoint(3)
    assert checkpoint is not None
    assert checkpoint.available is True
    assert checkpoint.price == Decimal("70")


def test_checkpoint_is_unavailable_until_a_completed_day_after_target():
    e = event("return", 5, 9)
    history = tuple(daily(2, day, "100") for day in range(1, 15))
    cfg = SupplyRecoveryConfig(checkpoint_horizons_days=(3,))
    before = measure_supply_event_recovery(
        event=e,
        historical_buckets=history,
        as_of=datetime(2026, 1, 12, 23, tzinfo=UTC),
        config=cfg,
    )
    after = measure_supply_event_recovery(
        event=e,
        historical_buckets=history,
        as_of=datetime(2026, 1, 14, tzinfo=UTC),
        config=cfg,
    )
    assert before.checkpoint(3).available is False  # type: ignore[union-attr]
    assert after.checkpoint(3).available is True  # type: ignore[union-attr]
    assert (
        after.checkpoint(3).observed_at  # type: ignore[union-attr]
        == datetime(2026, 1, 13, tzinfo=UTC)
    )


def test_next_same_family_event_truncates_previous_recovery():
    e = event("return", 5, 9)
    history = tuple(daily(3, day, str(50 + day)) for day in range(1, 22))
    cfg = SupplyRecoveryConfig(checkpoint_horizons_days=(3, 8))
    recovery = measure_supply_event_recovery(
        event=e,
        historical_buckets=history,
        as_of=datetime(2026, 1, 22, tzinfo=UTC),
        next_same_family_event_opens_at=datetime(2026, 1, 15, tzinfo=UTC),
        config=cfg,
    )
    assert recovery.recovery_cutoff_at == datetime(2026, 1, 15, tzinfo=UTC)
    assert recovery.checkpoint(3).available is True  # type: ignore[union-attr]
    assert recovery.checkpoint(8).available is False  # type: ignore[union-attr]
    assert "recovery_truncated_at_next_same_family_event" in recovery.explanation_codes


def test_recovery_threshold_measures_longer_half_recovery():
    e = event("return", 5, 9)
    prices = {
        1: "100", 2: "100", 3: "100", 4: "100",
        5: "80", 6: "60", 7: "50", 8: "55",
        9: "55", 10: "60", 11: "70", 12: "76",
    }
    history = tuple(daily(4, day, price) for day, price in prices.items())
    recovery = measure_supply_event_recovery(
        event=e,
        historical_buckets=history,
        as_of=datetime(2026, 1, 13, tzinfo=UTC),
        config=SupplyRecoveryConfig(
            checkpoint_horizons_days=(1,),
            recovery_fractions=(Decimal("0.5"),),
        ),
    )
    threshold = recovery.thresholds[0]
    assert recovery.pre_price_median == Decimal("100")
    assert recovery.trough_price == Decimal("50")
    assert threshold.target_price == Decimal("75")
    assert threshold.reached is True
    assert threshold.first_recovered_at == datetime(2026, 1, 12, tzinfo=UTC)
    assert threshold.days_from_trough == Decimal("5")


def test_profile_uses_only_available_checkpoint_ratios():
    e1 = event("a", 5, 9)
    e2 = SupplyPoolEvent(
        event_key="b",
        family="SUMMER",
        known_at=datetime(2026, 2, 1, tzinfo=UTC),
        opens_at=datetime(2026, 2, 5, tzinfo=UTC),
        closes_at=datetime(2026, 2, 9, tzinfo=UTC),
    )
    h1 = tuple(daily(5, day, "100" if day < 9 else "80") for day in range(1, 15))
    r1 = measure_supply_event_recovery(
        event=e1,
        historical_buckets=h1,
        as_of=datetime(2026, 1, 15, tzinfo=UTC),
        config=SupplyRecoveryConfig(checkpoint_horizons_days=(3,)),
    )
    # Second event deliberately has no history and contributes nothing.
    r2 = measure_supply_event_recovery(
        event=e2,
        historical_buckets=(),
        as_of=datetime(2026, 2, 15, tzinfo=UTC),
        config=SupplyRecoveryConfig(checkpoint_horizons_days=(3,)),
    )
    profile = build_supply_recovery_profile(
        (r1, r2), config=SupplyRecoveryConfig(checkpoint_horizons_days=(3,))
    )
    checkpoint = profile.checkpoint(3)
    assert checkpoint is not None
    assert checkpoint.contributing_event_count == 1
    assert checkpoint.median_pre_price_ratio == Decimal("0.8")


def test_item_analysis_excludes_account_only_membership():
    e1 = event("tradeable", 5, 9)
    e2 = SupplyPoolEvent(
        event_key="account",
        family="SUMMER",
        known_at=datetime(2026, 1, 10, tzinfo=UTC),
        opens_at=datetime(2026, 1, 15, tzinfo=UTC),
        closes_at=datetime(2026, 1, 19, tzinfo=UTC),
    )
    memberships = (
        SupplyPoolMembership(
            event_key="tradeable", item_id=6, known_at=e1.known_at,
            coupon_mode="DIRECT_OR_COUPON",
        ),
        SupplyPoolMembership(
            event_key="account", item_id=6, known_at=e2.known_at,
            coupon_mode="ACCOUNT_ONLY",
        ),
    )
    history = tuple(daily(6, day, "100") for day in range(1, 22))
    analysis = analyze_item_supply_recovery(
        item_id=6,
        events=(e1, e2),
        memberships=memberships,
        historical_buckets=history,
        as_of=datetime(2026, 1, 22, tzinfo=UTC),
        config=SupplyRecoveryConfig(checkpoint_horizons_days=(3,)),
    )
    assert [value.event_key for value in analysis.recoveries] == ["tradeable"]


def _recovery_for_estimator(
    *, key: str, ratio: str, observed_at: datetime, closes_at: datetime
):
    e = SupplyPoolEvent(
        event_key=key,
        family="SUMMER",
        known_at=closes_at - timedelta(days=10),
        opens_at=closes_at - timedelta(days=4),
        closes_at=closes_at,
    )
    start = e.opens_at - timedelta(days=4)
    buckets = []
    # Full PRE at 100 and ACTIVE trough at 50.
    for index in range(4):
        buckets.append(HistoricalTradeBucketEvidence(
            item_id=7,
            granularity="1d",
            bucket_start_utc=start + timedelta(days=index),
            bucket_duration=timedelta(days=1),
            vwap_price=Decimal("100"),
            reported_trade_volume=1,
        ))
    for index in range(4, 8):
        buckets.append(HistoricalTradeBucketEvidence(
            item_id=7,
            granularity="1d",
            bucket_start_utc=start + timedelta(days=index),
            bucket_duration=timedelta(days=1),
            vwap_price=Decimal("50"),
            reported_trade_volume=1,
        ))
    # Put the exact 3d checkpoint ratio on a completed UTC day.
    target_day = e.closes_at + timedelta(days=3)
    buckets.append(HistoricalTradeBucketEvidence(
        item_id=7,
        granularity="1d",
        bucket_start_utc=target_day,
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal("100") * Decimal(ratio),
        reported_trade_volume=1,
    ))
    return measure_supply_event_recovery(
        event=e,
        historical_buckets=tuple(buckets),
        as_of=observed_at,
        config=SupplyRecoveryConfig(checkpoint_horizons_days=(3,)),
    )


def _current_recovery(current: SupplyPoolEvent):
    start = current.opens_at - timedelta(days=4)
    buckets = tuple(
        HistoricalTradeBucketEvidence(
            item_id=7,
            granularity="1d",
            bucket_start_utc=start + timedelta(days=index),
            bucket_duration=timedelta(days=1),
            vwap_price=Decimal("100") if index < 4 else Decimal("50"),
            reported_trade_volume=1,
        )
        for index in range(8)
    )
    return measure_supply_event_recovery(
        event=current,
        historical_buckets=buckets,
        as_of=current.closes_at + timedelta(days=1),
        config=SupplyRecoveryConfig(checkpoint_horizons_days=(3,)),
    )


def test_estimator_uses_prior_events_only_and_applies_fee_rounding():
    prior = _recovery_for_estimator(
        key="prior",
        ratio="0.80",
        closes_at=datetime(2025, 12, 9, tzinfo=UTC),
        observed_at=datetime(2025, 12, 20, tzinfo=UTC),
    )
    current = event("current", 20, 24)
    estimate = estimate_post_rerelease_reference(
        event=current,
        prediction_as_of=datetime(2026, 1, 26, tzinfo=UTC),
        horizon_days=3,
        entry_proxy_price=Decimal("60"),
        current_recovery=_current_recovery(current),
        prior_recoveries=(prior,),
    )
    assert estimate.prior_event_count == 1
    assert estimate.predicted_recovery_ratio == Decimal("0.80")
    assert estimate.reference_sell_price == Decimal("80.00")
    assert estimate.sale_proceeds == Decimal("68.00")
    assert estimate.net_profit == Decimal("8.00")
    assert estimate.research_signal_positive is True
    assert sale_proceeds(Decimal("10.019"), Decimal("0.15"), Decimal("0.01")) == Decimal("8.51")


def test_estimator_reweights_prior_recoveries_by_operator_context_comparability():
    low_ratio_prior = _recovery_for_estimator(
        key="low-context-prior",
        ratio="0.80",
        closes_at=datetime(2025, 11, 9, tzinfo=UTC),
        observed_at=datetime(2025, 11, 20, tzinfo=UTC),
    )
    matching_prior = _recovery_for_estimator(
        key="matching-prior",
        ratio="1.20",
        closes_at=datetime(2025, 12, 9, tzinfo=UTC),
        observed_at=datetime(2025, 12, 20, tzinfo=UTC),
    )
    current = event("current", 20, 24)
    at = datetime(2026, 1, 26, tzinfo=UTC)
    current_context = ResolvedManualMarketContext(
        item_id=7,
        effective_at=at,
        knowledge_as_of=at,
        strength_index=Decimal("1.50"),
        attention_index=Decimal("1"),
        source_effective_at=at,
        source_known_at=at,
        visible_observation_count=1,
        defaulted=False,
    )
    low_context = ResolvedManualMarketContext(
        item_id=7,
        effective_at=datetime(2025, 11, 12, tzinfo=UTC),
        knowledge_as_of=at,
        strength_index=Decimal("0.50"),
        attention_index=Decimal("1"),
        source_effective_at=datetime(2025, 11, 1, tzinfo=UTC),
        source_known_at=datetime(2025, 11, 1, tzinfo=UTC),
        visible_observation_count=1,
        defaulted=False,
    )
    matching_context = ResolvedManualMarketContext(
        item_id=7,
        effective_at=datetime(2025, 12, 12, tzinfo=UTC),
        knowledge_as_of=at,
        strength_index=Decimal("1.50"),
        attention_index=Decimal("1"),
        source_effective_at=datetime(2025, 12, 1, tzinfo=UTC),
        source_known_at=datetime(2025, 12, 1, tzinfo=UTC),
        visible_observation_count=1,
        defaulted=False,
    )
    estimate = estimate_post_rerelease_reference(
        event=current,
        prediction_as_of=at,
        horizon_days=3,
        entry_proxy_price=Decimal("60"),
        current_recovery=_current_recovery(current),
        prior_recoveries=(low_ratio_prior, matching_prior),
        current_context=current_context,
        prior_context_by_event={
            low_ratio_prior.event_key: low_context,
            matching_prior.event_key: matching_context,
        },
    )
    assert estimate.prior_event_count == 2
    assert estimate.predicted_recovery_ratio == Decimal("1.20")
    assert estimate.mean_prior_context_comparability < Decimal("1")
    assert estimate.effective_prior_event_weight < Decimal("2")
    assert "manual_context_weighted_prior_recovery" in estimate.explanation_codes


def test_no_prior_event_means_no_research_signal_not_zero_score():
    current = event("current", 20, 24)
    estimate = estimate_post_rerelease_reference(
        event=current,
        prediction_as_of=datetime(2026, 1, 26, tzinfo=UTC),
        horizon_days=30,
        entry_proxy_price=Decimal("60"),
        current_recovery=_current_recovery(current),
        prior_recoveries=(),
    )
    assert estimate.predicted_recovery_ratio is None
    assert estimate.reference_sell_price is None
    assert estimate.net_roi is None
    assert estimate.research_signal_positive is False
    assert estimate.explanation_codes == ("insufficient_prior_return_recovery_history",)


def test_future_target_changes_outcome_but_cannot_change_prediction():
    prior = _recovery_for_estimator(
        key="prior",
        ratio="0.80",
        closes_at=datetime(2025, 12, 9, tzinfo=UTC),
        observed_at=datetime(2025, 12, 20, tzinfo=UTC),
    )
    current = event("current", 20, 24)
    estimate = estimate_post_rerelease_reference(
        event=current,
        prediction_as_of=datetime(2026, 1, 26, tzinfo=UTC),
        horizon_days=3,
        entry_proxy_price=Decimal("60"),
        current_recovery=_current_recovery(current),
        prior_recoveries=(prior,),
    )
    low = HistoricalTradeBucketEvidence(
        item_id=7, granularity="1d",
        bucket_start_utc=datetime(2026, 1, 27, tzinfo=UTC),
        bucket_duration=timedelta(days=1), vwap_price=Decimal("50"),
        reported_trade_volume=1,
    )
    high = HistoricalTradeBucketEvidence(
        item_id=7, granularity="1d",
        bucket_start_utc=datetime(2026, 1, 27, tzinfo=UTC),
        bucket_duration=timedelta(days=1), vwap_price=Decimal("120"),
        reported_trade_volume=1,
    )
    low_outcome = evaluate_post_rerelease_outcome(
        estimate=estimate,
        event=current,
        historical_buckets=(low,),
        evaluation_as_of=datetime(2026, 1, 29, tzinfo=UTC),
    )
    high_outcome = evaluate_post_rerelease_outcome(
        estimate=estimate,
        event=current,
        historical_buckets=(high,),
        evaluation_as_of=datetime(2026, 1, 29, tzinfo=UTC),
    )
    assert estimate.reference_sell_price == Decimal("80.00")
    assert low_outcome.exit_proxy_price == Decimal("50")
    assert high_outcome.exit_proxy_price == Decimal("120")
    assert low_outcome.realized_net_roi < Decimal("0")
    assert high_outcome.realized_net_roi > Decimal("0")


def test_first_post_event_trade_proxy_requires_completed_day():
    e = event("current", 5, 9)
    point = daily(8, 9, "60")
    assert first_post_event_trade_proxy(
        event=e,
        historical_buckets=(point,),
        as_of=datetime(2026, 1, 9, 23, tzinfo=UTC),
    ) is None
    observed = first_post_event_trade_proxy(
        event=e,
        historical_buckets=(point,),
        as_of=datetime(2026, 1, 10, tzinfo=UTC),
    )
    assert observed == (datetime(2026, 1, 10, tzinfo=UTC), Decimal("60"))


def test_daily_bucket_straddling_non_midnight_event_boundary_is_not_misclassified():
    e = SupplyPoolEvent(
        event_key="utc-11-boundary",
        family="SUMMER",
        known_at=datetime(2026, 1, 1, tzinfo=UTC),
        opens_at=datetime(2026, 1, 5, 11, tzinfo=UTC),
        closes_at=datetime(2026, 1, 9, 11, tzinfo=UTC),
    )
    history = tuple(daily(9, day, "100" if day <= 5 else "50") for day in range(1, 14))
    recovery = measure_supply_event_recovery(
        event=e,
        historical_buckets=history,
        as_of=datetime(2026, 1, 14, tzinfo=UTC),
        config=SupplyRecoveryConfig(checkpoint_horizons_days=(1,)),
    )
    # Jan 5 spans 00:00-24:00 and crosses the 11:00 open, so it is neither PRE nor ACTIVE.
    assert recovery.pre_observed_day_count == 3  # Jan 2-4; Jan 1 is before PRE start 11:00.
    assert recovery.trough_at == datetime(2026, 1, 6, tzinfo=UTC)


def test_prior_checkpoint_observed_after_current_open_cannot_leak_into_prediction():
    current = event("current", 20, 24)
    leaked = _recovery_for_estimator(
        key="prior",
        ratio="2.00",
        closes_at=datetime(2026, 1, 17, tzinfo=UTC),
        observed_at=datetime(2026, 1, 23, tzinfo=UTC),
    )
    estimate = estimate_post_rerelease_reference(
        event=current,
        prediction_as_of=datetime(2026, 1, 26, tzinfo=UTC),
        horizon_days=3,
        entry_proxy_price=Decimal("60"),
        current_recovery=_current_recovery(current),
        prior_recoveries=(leaked,),
    )
    assert estimate.prior_event_count == 0
    assert estimate.reference_sell_price is None


def test_insufficient_pre_baseline_is_excluded_from_recovery_profile_and_estimator():
    prior_event = event("thin-prior", 5, 9)
    # Only one clean PRE day is present, below the default minimum of three.
    history = (
        daily(10, 4, "100"),
        daily(10, 5, "70"),
        daily(10, 6, "60"),
        daily(10, 7, "50"),
        daily(10, 8, "55"),
        daily(10, 12, "80"),
    )
    thin = measure_supply_event_recovery(
        event=prior_event,
        historical_buckets=history,
        as_of=datetime(2026, 1, 14, tzinfo=UTC),
        config=SupplyRecoveryConfig(checkpoint_horizons_days=(3,)),
    )
    assert thin.profile_eligible is False
    profile = build_supply_recovery_profile(
        (thin,), config=SupplyRecoveryConfig(checkpoint_horizons_days=(3,))
    )
    assert profile.checkpoint(3).contributing_event_count == 0  # type: ignore[union-attr]

    current = event("current-late", 20, 24)
    estimate = estimate_post_rerelease_reference(
        event=current,
        prediction_as_of=datetime(2026, 1, 26, tzinfo=UTC),
        horizon_days=3,
        entry_proxy_price=Decimal("60"),
        current_recovery=_current_recovery(current),
        prior_recoveries=(thin,),
    )
    assert estimate.prior_event_count == 0
    assert estimate.reference_sell_price is None


def test_single_mismatched_prior_is_not_enough_context_comparable_history():
    prior = _recovery_for_estimator(
        key="prior",
        ratio="1.20",
        closes_at=datetime(2025, 12, 9, tzinfo=UTC),
        observed_at=datetime(2025, 12, 20, tzinfo=UTC),
    )
    current = event("current", 20, 24)
    at = datetime(2026, 1, 26, tzinfo=UTC)
    current_context = ResolvedManualMarketContext(
        item_id=7,
        effective_at=at,
        knowledge_as_of=at,
        strength_index=Decimal("2"),
        attention_index=Decimal("1"),
        source_effective_at=at,
        source_known_at=at,
        visible_observation_count=1,
        defaulted=False,
    )
    estimate = estimate_post_rerelease_reference(
        event=current,
        prediction_as_of=at,
        horizon_days=3,
        entry_proxy_price=Decimal("60"),
        current_recovery=_current_recovery(current),
        prior_recoveries=(prior,),
        current_context=current_context,
    )
    assert estimate.prior_event_count == 1
    assert estimate.effective_prior_event_weight == Decimal("0.5")
    assert estimate.predicted_recovery_ratio is None
    assert estimate.research_signal_positive is False
    assert estimate.explanation_codes == (
        "insufficient_context_comparable_prior_return_history",
    )
