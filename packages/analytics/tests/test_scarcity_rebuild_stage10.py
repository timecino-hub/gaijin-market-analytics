from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.reissue_exposure import ReissueExposureFeatures
from gaijin_market_analytics.scarcity_rebuild import (
    ScarcityRebuildConfig,
    ScarcityRebuildError,
    evaluate_scarcity_rebuild_forward_outcome,
    measure_scarcity_rebuild,
)
from gaijin_market_analytics.supply_cycles import SupplyPoolEvent


def _event() -> SupplyPoolEvent:
    return SupplyPoolEvent(
        event_key="event-a",
        family="SUMMER",
        known_at=datetime(2026, 1, 1, tzinfo=UTC),
        opens_at=datetime(2026, 1, 11, tzinfo=UTC),
        closes_at=datetime(2026, 1, 21, tzinfo=UTC),
    )


def _bucket(day: int, price: str, volume: int) -> HistoricalTradeBucketEvidence:
    return HistoricalTradeBucketEvidence(
        item_id=1,
        granularity="1d",
        bucket_start_utc=datetime(2026, 1, day, tzinfo=UTC),
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal(price),
        reported_trade_volume=volume,
    )


def _series() -> tuple[HistoricalTradeBucketEvidence, ...]:
    values = []
    # PRE: price 100, activity 2.
    for day in range(2, 11):
        values.append(_bucket(day, "100", 2))
    # ACTIVE: trough 50, activity 10.
    for day in range(12, 21):
        values.append(_bucket(day, "50", 10))
    # POST: partial recovery with activity normalized to PRE.
    for offset, price in enumerate(("60", "65", "70", "75", "80", "85"), start=22):
        values.append(_bucket(offset, price, 2))
    return tuple(values)


def test_rebuild_uses_event_local_price_and_activity_baselines():
    snapshot = measure_scarcity_rebuild(
        item_id=1,
        event=_event(),
        historical_buckets=_series(),
        as_of=datetime(2026, 1, 28, tzinfo=UTC),
    )
    assert snapshot.state == "SCARCITY_REBUILDING"
    assert snapshot.price_regime == "PARTIAL_RECOVERY"
    assert snapshot.activity_regime == "NORMALIZED_TO_PRE_EVENT"
    assert snapshot.pre_price_median == Decimal("100")
    assert snapshot.active_trough_price == Decimal("50")
    assert snapshot.recent_price_median == Decimal("80")
    assert snapshot.price_recovery_fraction == Decimal("0.6")
    assert snapshot.activity_normalization_fraction == Decimal("1")
    assert snapshot.rebuild_progress_floor == Decimal("0.6")


def test_elevated_activity_keeps_state_in_absorption_rebuild_transition():
    history = list(_series())
    for index, day in enumerate((25, 26, 27)):
        old = history[-3 + index]
        history[-3 + index] = HistoricalTradeBucketEvidence(
            item_id=old.item_id,
            granularity=old.granularity,
            bucket_start_utc=old.bucket_start_utc,
            bucket_duration=old.bucket_duration,
            vwap_price=old.vwap_price,
            reported_trade_volume=6,
        )
    snapshot = measure_scarcity_rebuild(
        item_id=1,
        event=_event(),
        historical_buckets=tuple(history),
        as_of=datetime(2026, 1, 28, tzinfo=UTC),
    )
    assert snapshot.state == "REBUILDING_WITH_ELEVATED_ACTIVITY"
    assert snapshot.activity_normalization_fraction == Decimal("0.5")
    assert snapshot.rebuild_progress_floor == Decimal("0.5")


def test_rebuilt_requires_price_back_to_pre_and_activity_normalized():
    history = list(_series())
    history[-3:] = [
        _bucket(25, "100", 2),
        _bucket(26, "105", 2),
        _bucket(27, "110", 2),
    ]
    snapshot = measure_scarcity_rebuild(
        item_id=1,
        event=_event(),
        historical_buckets=tuple(history),
        as_of=datetime(2026, 1, 28, tzinfo=UTC),
    )
    assert snapshot.state == "SCARCITY_REBUILT"
    assert snapshot.price_recovery_fraction > Decimal("1")
    assert snapshot.rebuild_progress_floor == Decimal("1")


def test_missing_pre_baseline_is_insufficient_not_fabricated():
    history = tuple(value for value in _series() if value.bucket_start_utc.day >= 12)
    snapshot = measure_scarcity_rebuild(
        item_id=1,
        event=_event(),
        historical_buckets=history,
        as_of=datetime(2026, 1, 28, tzinfo=UTC),
    )
    assert snapshot.state == "INSUFFICIENT_EVIDENCE"
    assert snapshot.pre_price_median is None
    assert snapshot.rebuild_progress_floor is None
    assert "insufficient_pre_event_scarcity_baseline" in snapshot.explanation_codes


def test_future_post_data_cannot_change_past_snapshot():
    as_of = datetime(2026, 1, 26, tzinfo=UTC)
    baseline = measure_scarcity_rebuild(
        item_id=1,
        event=_event(),
        historical_buckets=_series(),
        as_of=as_of,
    )
    future = HistoricalTradeBucketEvidence(
        item_id=1,
        granularity="1d",
        bucket_start_utc=datetime(2026, 1, 30, tzinfo=UTC),
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal("999"),
        reported_trade_volume=999,
    )
    changed = measure_scarcity_rebuild(
        item_id=1,
        event=_event(),
        historical_buckets=(*_series(), future),
        as_of=as_of,
    )
    assert changed == baseline


def test_next_supply_event_supersedes_old_rebuild_state_without_future_use():
    snapshot = measure_scarcity_rebuild(
        item_id=1,
        event=_event(),
        historical_buckets=_series(),
        as_of=datetime(2026, 2, 2, tzinfo=UTC),
        next_same_family_event_opens_at=datetime(2026, 1, 29, tzinfo=UTC),
    )
    assert snapshot.state == "SUPERSEDED_BY_NEXT_SUPPLY_EVENT"
    assert "scarcity_rebuild_superseded_by_next_same_family_supply_event" in snapshot.explanation_codes


def test_reissue_exposure_is_attached_as_diagnostic_only():
    exposure = ReissueExposureFeatures(
        item_id=1,
        as_of=datetime(2026, 1, 28, tzinfo=UTC),
        known_return_count=4,
        completed_return_count=4,
        known_tradeable_supply_count=3,
        completed_tradeable_supply_count=3,
        known_account_only_return_count=1,
        completed_account_only_return_count=1,
        distinct_family_count=2,
        trailing_365d_tradeable_supply_count=1,
        trailing_730d_tradeable_supply_count=2,
        trailing_1095d_tradeable_supply_count=3,
        trailing_1095d_account_only_return_count=1,
        days_since_last_tradeable_supply_close=Decimal("7"),
        days_since_last_any_return_close=Decimal("7"),
        first_known_return_open=datetime(2023, 1, 1, tzinfo=UTC),
        last_known_return_open=datetime(2026, 1, 11, tzinfo=UTC),
        explanation_codes=(),
    )
    snapshot = measure_scarcity_rebuild(
        item_id=1,
        event=_event(),
        historical_buckets=_series(),
        as_of=datetime(2026, 1, 28, tzinfo=UTC),
        reissue_exposure=exposure,
    )
    assert snapshot.completed_tradeable_reissue_count == 3
    assert snapshot.completed_account_only_return_count == 1
    assert snapshot.distinct_reissue_family_count == 2
    assert "cumulative_reissue_exposure_attached_diagnostic_only" in snapshot.explanation_codes


def test_forward_outcome_is_separate_from_snapshot_and_fee_adjusted():
    history = list(_series())
    history.extend(
        (
            _bucket(29, "90", 2),
            _bucket(30, "100", 2),
            _bucket(31, "120", 2),
        )
    )
    snapshot = measure_scarcity_rebuild(
        item_id=1,
        event=_event(),
        historical_buckets=tuple(history),
        as_of=datetime(2026, 1, 28, tzinfo=UTC),
    )
    outcome = evaluate_scarcity_rebuild_forward_outcome(
        snapshot=snapshot,
        historical_buckets=tuple(history),
        evaluation_as_of=datetime(2026, 2, 2, tzinfo=UTC),
        forward_horizon_days=3,
    )
    assert outcome.outcome_available is True
    assert outcome.entry_proxy_price == Decimal("85")
    assert outcome.exit_proxy_price == Decimal("120")
    assert outcome.sale_proceeds == Decimal("102.00")
    assert outcome.net_profit == Decimal("17.00")
    assert outcome.net_roi == Decimal("0.2")


def test_forward_outcome_does_not_leak_when_target_not_yet_observed():
    snapshot = measure_scarcity_rebuild(
        item_id=1,
        event=_event(),
        historical_buckets=_series(),
        as_of=datetime(2026, 1, 28, tzinfo=UTC),
    )
    outcome = evaluate_scarcity_rebuild_forward_outcome(
        snapshot=snapshot,
        historical_buckets=_series(),
        evaluation_as_of=datetime(2026, 1, 30, tzinfo=UTC),
        forward_horizon_days=30,
    )
    assert outcome.outcome_available is False
    assert outcome.exit_proxy_price is None


def test_invalid_future_exposure_is_rejected():
    exposure = ReissueExposureFeatures(
        item_id=1,
        as_of=datetime(2026, 2, 1, tzinfo=UTC),
        known_return_count=0,
        completed_return_count=0,
        known_tradeable_supply_count=0,
        completed_tradeable_supply_count=0,
        known_account_only_return_count=0,
        completed_account_only_return_count=0,
        distinct_family_count=0,
        trailing_365d_tradeable_supply_count=0,
        trailing_730d_tradeable_supply_count=0,
        trailing_1095d_tradeable_supply_count=0,
        trailing_1095d_account_only_return_count=0,
        days_since_last_tradeable_supply_close=None,
        days_since_last_any_return_close=None,
        first_known_return_open=None,
        last_known_return_open=None,
        explanation_codes=(),
    )
    with pytest.raises(ScarcityRebuildError):
        measure_scarcity_rebuild(
            item_id=1,
            event=_event(),
            historical_buckets=_series(),
            as_of=datetime(2026, 1, 28, tzinfo=UTC),
            reissue_exposure=exposure,
        )
