from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.absorption_exit import (
    AbsorptionExitConfig,
    SupplyAbsorptionExitError,
    legacy_absorption_window_end,
    measure_supply_absorption_exit,
    resolve_absorption_withholding,
)
from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.opportunities_v2 import (
    CurrentMarketEvidence,
    OpportunityScoreV2,
    OpportunityV2Config,
    OpportunityV2ContractError,
)
from gaijin_market_analytics.supply_cycles import (
    SupplyPoolEvent,
    SupplyPoolMembership,
    derive_supply_cycle_features,
)


BASE = datetime(2026, 1, 1, tzinfo=UTC)
# Opens on offset 10, closes on offset 40, so the legacy fixed-duration window
# runs for another 30 days and ends on offset 70.
OPENS_OFFSET = 10
CLOSES_OFFSET = 40
LEGACY_END_OFFSET = 70


def _at(offset: int) -> datetime:
    return BASE + timedelta(days=offset)


def _event(family: str = "SUMMER") -> SupplyPoolEvent:
    return SupplyPoolEvent(
        event_key="event-a",
        family=family,  # type: ignore[arg-type]
        known_at=_at(0),
        opens_at=_at(OPENS_OFFSET),
        closes_at=_at(CLOSES_OFFSET),
    )


def _bucket(offset: int, price: str, volume: int) -> HistoricalTradeBucketEvidence:
    return HistoricalTradeBucketEvidence(
        item_id=1,
        granularity="1d",
        bucket_start_utc=_at(offset),
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal(price),
        reported_trade_volume=volume,
    )


def _baseline(
    *,
    active_price: str = "60",
    active_volume: int = 10,
) -> list[HistoricalTradeBucketEvidence]:
    """Ten pre-event days at 100/2 and ten active-event days at the shock level."""

    series = [_bucket(offset, "100", 2) for offset in range(0, 10)]
    series.extend(
        _bucket(offset, active_price, active_volume) for offset in range(10, 20)
    )
    return series


def _post(
    prices: tuple[str, ...],
    volumes: tuple[int, ...] | int = 2,
    *,
    offsets: tuple[int, ...] | None = None,
) -> list[HistoricalTradeBucketEvidence]:
    positions = offsets or tuple(
        range(CLOSES_OFFSET, CLOSES_OFFSET + len(prices))
    )
    if isinstance(volumes, int):
        volumes = (volumes,) * len(prices)
    return [
        _bucket(offset, price, volume)
        for offset, price, volume in zip(positions, prices, volumes, strict=True)
    ]


def _settled_series() -> list[HistoricalTradeBucketEvidence]:
    # Post-event: early trough, partial rebuild, then a flat, quiet plateau.
    prices = ("50", "52", "55", "60", "65") + ("70",) * 10
    volumes = (8, 6, 5, 4, 3) + (2,) * 10
    return _baseline() + _post(prices, volumes)


def test_quiet_flat_post_event_plateau_confirms_absorption_exit():
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=_settled_series(),
        as_of=_at(CLOSES_OFFSET + 15),
    )
    assert snapshot.state == "ABSORPTION_EXIT_CONFIRMED"
    assert snapshot.activity_axis == "SETTLED"
    assert snapshot.price_axis == "SETTLED"
    assert snapshot.absorption_active is False
    assert snapshot.price_shock_detected is True
    assert snapshot.activity_spike_detected is True
    assert snapshot.pre_observed_day_count == 10
    assert snapshot.active_observed_day_count == 10
    assert snapshot.post_observed_day_count == 15
    assert snapshot.dwell_calendar_span_days == 5
    assert snapshot.dwell_activity_excess_peak == Decimal("0")
    assert snapshot.trend_price_change_ratio == Decimal("0")
    assert snapshot.fresh_post_event_low is False


def test_exit_can_be_confirmed_before_the_legacy_window_expires():
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=_settled_series(),
        as_of=_at(CLOSES_OFFSET + 15),
    )
    assert snapshot.as_of < snapshot.legacy_absorption_ends_at
    assert snapshot.legacy_absorption_active is True
    assert snapshot.state == "ABSORPTION_EXIT_CONFIRMED"
    assert (
        "absorption_exit_earlier_than_legacy_fixed_duration"
        in snapshot.explanation_codes
    )


def test_still_declining_price_keeps_absorption_active_after_legacy_window():
    # The post-event low is old, but the recent trend is still falling.
    prices = ("50",) * 5 + ("90",) * 5 + ("75",) * 5
    series = _baseline() + _post(prices, 2)
    series.extend(_post(("75",) * 20, 2, offsets=tuple(range(55, 75))))
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=_at(LEGACY_END_OFFSET + 5),
    )
    assert snapshot.legacy_absorption_active is False
    assert snapshot.price_axis == "SETTLED"

    declining = _baseline() + _post(("50",) * 5 + ("90",) * 5 + ("75",) * 5, 2)
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=declining,
        as_of=_at(CLOSES_OFFSET + 15),
    )
    assert snapshot.price_axis == "NOT_SETTLED"
    assert snapshot.fresh_post_event_low is False
    assert snapshot.trend_price_change_ratio is not None
    assert snapshot.trend_price_change_ratio < Decimal("-0.03")
    assert snapshot.state == "ABSORPTION_ACTIVE"
    assert snapshot.absorption_active is True


def test_absorption_active_is_reported_after_the_legacy_window_expires():
    prices = ("90",) * 25 + (
        "85", "84", "83", "82", "81", "78", "77", "76", "75", "74",
    )
    series = _baseline() + _post(prices, 2)
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=_at(CLOSES_OFFSET + 35),
    )
    assert snapshot.legacy_absorption_active is False
    assert snapshot.state == "ABSORPTION_ACTIVE"
    assert (
        "absorption_still_active_after_legacy_fixed_duration"
        in snapshot.explanation_codes
    )


def test_fresh_post_event_low_blocks_exit_even_without_a_downtrend():
    prices = ("70",) * 10 + ("50",) + ("70",) * 4
    series = _baseline() + _post(prices, 2)
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=_at(CLOSES_OFFSET + 15),
    )
    assert snapshot.fresh_post_event_low is True
    assert snapshot.trend_price_change_ratio == Decimal("0")
    assert snapshot.price_axis == "NOT_SETTLED"
    assert snapshot.state == "ABSORPTION_ACTIVE"


def test_elevated_recent_activity_blocks_exit_even_with_a_stable_price():
    prices = ("50", "52", "55", "60", "65") + ("70",) * 10
    volumes = (8, 6, 5, 4, 3) + (2,) * 5 + (8,) * 5
    series = _baseline() + _post(prices, volumes)
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=_at(CLOSES_OFFSET + 15),
    )
    assert snapshot.price_axis == "SETTLED"
    assert snapshot.activity_axis == "NOT_SETTLED"
    assert snapshot.dwell_activity_excess_peak == Decimal("0.75")
    assert snapshot.state == "ABSORPTION_ACTIVE"


def test_absent_activity_spike_leaves_the_price_axis_in_charge():
    series = _baseline(active_volume=2) + _post(
        ("50", "52", "55", "60", "65") + ("70",) * 10, 2
    )
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=_at(CLOSES_OFFSET + 15),
    )
    assert snapshot.activity_spike_detected is False
    assert snapshot.activity_axis == "NOT_APPLICABLE"
    assert snapshot.price_shock_detected is True
    assert snapshot.state == "ABSORPTION_EXIT_CONFIRMED"


def test_no_measurable_shock_is_not_reported_as_absorption():
    series = _baseline(active_price="99", active_volume=2) + _post(("100",) * 15, 2)
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=_at(CLOSES_OFFSET + 15),
    )
    assert snapshot.clear_supply_shock_detected is False
    assert snapshot.state == "NO_CLEAR_SUPPLY_SHOCK"
    assert snapshot.absorption_active is False
    assert "no_clear_post_event_supply_shock_to_absorb" in snapshot.explanation_codes


def test_sparse_dwell_window_is_never_read_as_a_quiet_market():
    dense = tuple(range(CLOSES_OFFSET, CLOSES_OFFSET + 10))
    sparse = (60, 70, 80, 90, 100)
    series = _baseline() + _post(
        ("70",) * 15, 2, offsets=dense + sparse
    )
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=_at(102),
    )
    assert snapshot.post_observed_day_count == 15
    assert snapshot.dwell_calendar_span_days == 41
    assert snapshot.state == "INSUFFICIENT_EVIDENCE"
    assert "absorption_dwell_window_too_sparse" in snapshot.explanation_codes


def test_missing_post_event_days_are_not_filled_in():
    series = _baseline() + _post(("70",) * 9, 2)
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=_at(CLOSES_OFFSET + 9),
    )
    assert snapshot.post_observed_day_count == 9
    assert snapshot.state == "INSUFFICIENT_EVIDENCE"
    assert (
        "insufficient_post_event_absorption_evidence" in snapshot.explanation_codes
    )


def test_open_event_is_reported_as_not_completed():
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=_baseline(),
        as_of=_at(CLOSES_OFFSET - 1),
    )
    assert snapshot.state == "EVENT_NOT_COMPLETED"
    assert snapshot.absorption_active is None
    assert snapshot.days_since_event_close is None


def test_unknown_event_is_not_visible_before_its_own_known_at():
    event = SupplyPoolEvent(
        event_key="event-a",
        family="SUMMER",
        known_at=_at(CLOSES_OFFSET),
        opens_at=_at(OPENS_OFFSET),
        closes_at=_at(CLOSES_OFFSET),
    )
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=event,
        historical_buckets=_settled_series(),
        as_of=_at(CLOSES_OFFSET - 5),
    )
    assert snapshot.state == "INSUFFICIENT_EVIDENCE"
    assert "supply_event_not_known_as_of" in snapshot.explanation_codes


def test_next_same_family_event_supersedes_the_measurement():
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=_settled_series(),
        as_of=_at(CLOSES_OFFSET + 15),
        next_same_family_event_opens_at=_at(CLOSES_OFFSET + 10),
    )
    assert snapshot.state == "SUPERSEDED_BY_NEXT_SUPPLY_EVENT"
    assert snapshot.absorption_active is None
    assert (
        "absorption_exit_superseded_by_next_same_family_supply_event"
        in snapshot.explanation_codes
    )


def test_future_evidence_cannot_change_a_point_in_time_snapshot():
    series = _settled_series()
    early = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=_at(CLOSES_OFFSET + 15),
    )
    with_future = series + _post(
        ("10",) * 10, 40, offsets=tuple(range(CLOSES_OFFSET + 15, CLOSES_OFFSET + 25))
    )
    replayed = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=with_future,
        as_of=_at(CLOSES_OFFSET + 15),
    )
    assert replayed == early
    later = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=with_future,
        as_of=_at(CLOSES_OFFSET + 25),
    )
    assert later.state == "ABSORPTION_ACTIVE"


def test_incomplete_trailing_day_is_excluded_from_the_dwell_window():
    series = _settled_series()
    partial = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=_at(CLOSES_OFFSET + 15) - timedelta(hours=1),
    )
    assert partial.post_observed_day_count == 14


def test_legacy_window_helper_matches_the_supply_cycle_rule():
    event = _event()
    assert legacy_absorption_window_end(event) == _at(LEGACY_END_OFFSET)
    assert legacy_absorption_window_end(event) == event.closes_at + (
        event.closes_at - event.opens_at
    )


def test_configuration_rejects_unsafe_thresholds():
    with pytest.raises(SupplyAbsorptionExitError):
        AbsorptionExitConfig(dwell_observed_days=0)
    with pytest.raises(SupplyAbsorptionExitError):
        AbsorptionExitConfig(maximum_dwell_span_days=2, dwell_observed_days=5)
    with pytest.raises(SupplyAbsorptionExitError):
        AbsorptionExitConfig(minimum_post_observed_days=4, trend_observed_days=10)
    with pytest.raises(SupplyAbsorptionExitError):
        AbsorptionExitConfig(activity_settled_excess=Decimal("1.5"))
    with pytest.raises(SupplyAbsorptionExitError):
        AbsorptionExitConfig(price_decline_tolerance=Decimal("1"))
    with pytest.raises(SupplyAbsorptionExitError):
        AbsorptionExitConfig(trend_observed_days=1)


def test_measurement_rejects_a_next_event_that_precedes_the_measured_event():
    with pytest.raises(SupplyAbsorptionExitError):
        measure_supply_absorption_exit(
            item_id=1,
            event=_event(),
            historical_buckets=_settled_series(),
            as_of=_at(CLOSES_OFFSET + 15),
            next_same_family_event_opens_at=_at(OPENS_OFFSET - 1),
        )


def test_measurement_requires_timezone_aware_inputs():
    with pytest.raises(SupplyAbsorptionExitError):
        measure_supply_absorption_exit(
            item_id=1,
            event=_event(),
            historical_buckets=_settled_series(),
            as_of=datetime(2026, 3, 1),
        )


def test_legacy_only_policy_ignores_absorption_evidence():
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=_settled_series(),
        as_of=_at(CLOSES_OFFSET + 15),
    )
    decision = resolve_absorption_withholding(
        legacy_absorption_active=True,
        snapshot=snapshot,
    )
    assert decision.policy == "LEGACY_ONLY"
    assert decision.withhold is True
    assert decision.differs_from_legacy is False
    assert decision.evidence_state == "ABSORPTION_EXIT_CONFIRMED"


def test_evidence_extends_legacy_never_publishes_more_than_legacy():
    confirmed = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=_settled_series(),
        as_of=_at(CLOSES_OFFSET + 15),
    )
    held = resolve_absorption_withholding(
        legacy_absorption_active=True,
        snapshot=confirmed,
        policy="EVIDENCE_EXTENDS_LEGACY",
    )
    assert held.withhold is True
    assert held.differs_from_legacy is False

    declining = _baseline() + _post(("50",) * 5 + ("90",) * 5 + ("75",) * 5, 2)
    active = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=declining,
        as_of=_at(CLOSES_OFFSET + 15),
    )
    extended = resolve_absorption_withholding(
        legacy_absorption_active=False,
        snapshot=active,
        policy="EVIDENCE_EXTENDS_LEGACY",
    )
    assert extended.withhold is True
    assert extended.differs_from_legacy is True
    assert (
        "absorption_withheld_beyond_legacy_window_by_evidence"
        in extended.explanation_codes
    )


def test_evidence_replaces_legacy_can_release_inside_the_legacy_window():
    confirmed = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=_settled_series(),
        as_of=_at(CLOSES_OFFSET + 15),
    )
    decision = resolve_absorption_withholding(
        legacy_absorption_active=True,
        snapshot=confirmed,
        policy="EVIDENCE_REPLACES_LEGACY",
    )
    assert decision.withhold is False
    assert decision.differs_from_legacy is True
    assert (
        "absorption_released_inside_legacy_window_by_evidence"
        in decision.explanation_codes
    )


def test_undetermined_evidence_always_falls_back_to_the_legacy_window():
    undetermined = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=_baseline() + _post(("70",) * 9, 2),
        as_of=_at(CLOSES_OFFSET + 9),
    )
    assert undetermined.absorption_active is None
    for policy in ("EVIDENCE_EXTENDS_LEGACY", "EVIDENCE_REPLACES_LEGACY"):
        decision = resolve_absorption_withholding(
            legacy_absorption_active=True,
            snapshot=undetermined,
            policy=policy,  # type: ignore[arg-type]
        )
        assert decision.withhold is True
        assert decision.differs_from_legacy is False


def test_missing_snapshot_falls_back_to_the_legacy_window():
    decision = resolve_absorption_withholding(
        legacy_absorption_active=True,
        policy="EVIDENCE_REPLACES_LEGACY",
    )
    assert decision.withhold is True
    assert decision.evidence_state is None
    assert (
        "absorption_exit_evidence_absent_legacy_fallback" in decision.explanation_codes
    )


def test_unsupported_policy_is_rejected():
    with pytest.raises(SupplyAbsorptionExitError):
        resolve_absorption_withholding(
            legacy_absorption_active=False,
            policy="ALWAYS_PUBLISH",  # type: ignore[arg-type]
        )


def _membership(event: SupplyPoolEvent) -> SupplyPoolMembership:
    return SupplyPoolMembership(
        event_key=event.event_key,
        item_id=1,
        known_at=event.known_at,
        coupon_mode="DIRECT_OR_COUPON",
    )


def _cycle(as_of: datetime) -> object:
    event = _event()
    return derive_supply_cycle_features(
        item_id=1,
        as_of=as_of,
        events=(event,),
        memberships=(_membership(event),),
    )


def _current(as_of: datetime) -> CurrentMarketEvidence:
    return CurrentMarketEvidence(
        item_id=1,
        observed_at=as_of,
        best_ask=Decimal("10"),
        best_bid=Decimal("9"),
        observed_ask_quantity=20,
        observed_bid_quantity=20,
    )


def _score(
    *,
    as_of: datetime,
    series: list[HistoricalTradeBucketEvidence],
    snapshot: object | None = None,
    policy: str = "LEGACY_ONLY",
):
    scorer = OpportunityScoreV2(
        OpportunityV2Config(absorption_withhold_policy=policy)  # type: ignore[arg-type]
    )
    return scorer.score(
        item_id=1,
        as_of=as_of,
        window_start=as_of - timedelta(days=8),
        current_market=_current(as_of),
        historical_buckets=series,
        supply_cycle=_cycle(as_of),
        absorption_exit=snapshot,  # type: ignore[arg-type]
    )


def _declining_series() -> list[HistoricalTradeBucketEvidence]:
    prices = ("90",) * 25 + (
        "85", "84", "83", "82", "81", "78", "77", "76", "75", "74",
    )
    return _baseline() + _post(prices, 2)


def test_default_policy_keeps_the_legacy_post_event_withholding_decision():
    as_of = _at(CLOSES_OFFSET + 15)
    series = _settled_series()
    cycle = _cycle(as_of)
    assert cycle.state == "POST_EVENT_ABSORPTION"  # type: ignore[attr-defined]
    without = _score(as_of=as_of, series=series)
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=as_of,
    )
    with_evidence = _score(as_of=as_of, series=series, snapshot=snapshot)
    assert without.score_published is False
    assert with_evidence.score_published is False
    assert without.score == with_evidence.score
    # Evidence is recorded but must not move the decision under the default policy.
    assert set(without.explanation_codes) <= set(with_evidence.explanation_codes)
    assert set(with_evidence.explanation_codes) - set(without.explanation_codes) == {
        "absorption_withhold_policy_legacy_only",
        "supply_absorption_withheld",
    }


def test_confirmed_exit_can_release_the_score_only_under_the_replacing_policy():
    as_of = _at(CLOSES_OFFSET + 15)
    series = _settled_series()
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=as_of,
    )
    assert snapshot.state == "ABSORPTION_EXIT_CONFIRMED"
    extending = _score(
        as_of=as_of,
        series=series,
        snapshot=snapshot,
        policy="EVIDENCE_EXTENDS_LEGACY",
    )
    assert extending.score_published is False
    replacing = _score(
        as_of=as_of,
        series=series,
        snapshot=snapshot,
        policy="EVIDENCE_REPLACES_LEGACY",
    )
    assert replacing.score_published is True
    assert (
        "absorption_released_inside_legacy_window_by_evidence"
        in replacing.explanation_codes
    )
    assert (
        "strategy_withheld_for_supply_cycle_regime"
        not in replacing.explanation_codes
    )


def test_active_absorption_can_withhold_beyond_the_legacy_window():
    as_of = _at(CLOSES_OFFSET + 35)
    series = _declining_series()
    cycle = _cycle(as_of)
    assert cycle.state == "NORMAL_DEPLETION"  # type: ignore[attr-defined]
    baseline = _score(as_of=as_of, series=series)
    assert baseline.score_published is True
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=as_of,
    )
    assert snapshot.state == "ABSORPTION_ACTIVE"
    extended = _score(
        as_of=as_of,
        series=series,
        snapshot=snapshot,
        policy="EVIDENCE_EXTENDS_LEGACY",
    )
    assert extended.score_published is False
    assert (
        "absorption_withheld_beyond_legacy_window_by_evidence"
        in extended.explanation_codes
    )


def test_pre_close_supply_regimes_are_never_released_by_absorption_evidence():
    as_of = _at(OPENS_OFFSET + 10)
    series = _baseline()
    cycle = _cycle(as_of)
    assert cycle.state == "ACTIVE_SUPPLY_INJECTION"  # type: ignore[attr-defined]
    result = _score(
        as_of=as_of,
        series=series,
        snapshot=None,
        policy="EVIDENCE_REPLACES_LEGACY",
    )
    assert result.score_published is False
    assert "strategy_withheld_for_supply_cycle_regime" in result.explanation_codes


def test_opportunity_rejects_absorption_evidence_from_the_future():
    as_of = _at(CLOSES_OFFSET + 15)
    series = _settled_series()
    snapshot = measure_supply_absorption_exit(
        item_id=1,
        event=_event(),
        historical_buckets=series,
        as_of=as_of,
    )
    with pytest.raises(OpportunityV2ContractError):
        _score(as_of=as_of - timedelta(days=1), series=series, snapshot=snapshot)


def test_opportunity_rejects_an_unsupported_absorption_policy():
    with pytest.raises(OpportunityV2ContractError):
        OpportunityV2Config(absorption_withhold_policy="ALWAYS_PUBLISH")  # type: ignore[arg-type]
