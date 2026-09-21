from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from gaijin_market_analytics.family_prior_generalization import (
    audit_family_prior_generalization,
    classify_generalization_aware_decision,
)
from gaijin_market_analytics.hierarchical_recovery import (
    HistoricalReturnOutcomeEvidence,
    corroborate_post_rerelease_signal,
    evaluate_sparse_baseline_family_fallback,
    estimate_family_recovery_prior,
)
from gaijin_market_analytics.post_rerelease import PostReReleaseReferenceEstimate


def dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def evidence(
    *, item_id: int, event_key: str, observed_day: int, roi: str, gross: str
) -> HistoricalReturnOutcomeEvidence:
    return HistoricalReturnOutcomeEvidence(
        item_id=item_id,
        family="HISTORY_LUNAR",
        event_key=event_key,
        horizon_days=90,
        prediction_as_of=dt(1),
        outcome_observed_at=dt(observed_day),
        entry_proxy_price=Decimal("100"),
        exit_proxy_price=Decimal(gross) * Decimal("100"),
        gross_exit_entry_ratio=Decimal(gross),
        realized_net_roi=Decimal(roi),
    )


def primary(*, positive: bool = True, sparse: bool = False) -> PostReReleaseReferenceEstimate:
    return PostReReleaseReferenceEstimate(
        event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        prior_event_count=2 if not sparse else 0,
        effective_prior_event_weight=Decimal("2") if not sparse else Decimal("0"),
        mean_prior_context_comparability=Decimal("1") if not sparse else None,
        current_strength_index=Decimal("1"),
        current_attention_index=Decimal("1"),
        entry_proxy_price=Decimal("100"),
        current_pre_price_median=Decimal("150"),
        predicted_recovery_ratio=Decimal("0.9") if not sparse else None,
        reference_sell_price=Decimal("135") if not sparse else None,
        sale_proceeds=Decimal("114.75") if not sparse else None,
        fee_amount=Decimal("20.25") if not sparse else None,
        net_profit=(Decimal("14.75") if positive else Decimal("-1")) if not sparse else None,
        net_roi=(Decimal("0.1475") if positive else Decimal("-0.01")) if not sparse else None,
        research_signal_positive=positive if not sparse else False,
        explanation_codes=("current_pre_event_baseline_ineligible",) if sparse else (),
    )


def stable_evidence() -> tuple[HistoricalReturnOutcomeEvidence, ...]:
    values = []
    day = 2
    for event_key in ("event-old-a", "event-old-b"):
        for item_id in (1, 2, 3):
            values.append(
                evidence(
                    item_id=item_id,
                    event_key=event_key,
                    observed_day=day,
                    roi="0.20",
                    gross="1.50",
                )
            )
            day += 1
    return tuple(values)


def test_two_item_prior_is_not_generalization_ready() -> None:
    values = (
        evidence(item_id=1, event_key="old-a", observed_day=2, roi="0.2", gross="1.5"),
        evidence(item_id=2, event_key="old-a", observed_day=3, roi="0.3", gross="1.6"),
        evidence(item_id=1, event_key="old-b", observed_day=4, roi="0.2", gross="1.5"),
        evidence(item_id=2, event_key="old-b", observed_day=5, roi="0.3", gross="1.6"),
    )
    audit = audit_family_prior_generalization(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=values,
    )
    assert audit.distinct_prior_item_count == 2
    assert audit.status == "INSUFFICIENT_ITEM_BREADTH"
    assert audit.generalization_ready is False


def test_three_item_two_event_prior_survives_both_leave_one_out_axes() -> None:
    audit = audit_family_prior_generalization(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=stable_evidence(),
    )
    assert audit.item_leave_one_out_fold_count == 3
    assert audit.item_leave_one_out_available_count == 3
    assert audit.item_leave_one_out_positive_count == 3
    assert audit.event_leave_one_out_fold_count == 2
    assert audit.event_leave_one_out_available_count == 2
    assert audit.event_leave_one_out_positive_count == 2
    assert audit.generalization_ready is True
    assert audit.status == "GENERALIZATION_READY"


def test_item_dominated_prior_is_marked_fragile() -> None:
    values = (
        evidence(item_id=1, event_key="a", observed_day=2, roi="0.2", gross="1.5"),
        evidence(item_id=1, event_key="b", observed_day=3, roi="0.2", gross="1.5"),
        evidence(item_id=1, event_key="c", observed_day=4, roi="0.2", gross="1.5"),
        evidence(item_id=1, event_key="d", observed_day=5, roi="0.2", gross="1.5"),
        evidence(item_id=2, event_key="a", observed_day=6, roi="-0.2", gross="0.9"),
        evidence(item_id=3, event_key="a", observed_day=7, roi="-0.2", gross="0.9"),
    )
    audit = audit_family_prior_generalization(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=values,
    )
    assert audit.maximum_item_example_share == Decimal("4") / Decimal("6")
    assert audit.item_balanced_support_positive is False
    assert audit.item_stability_established is False
    assert audit.status == "ITEM_BALANCE_CONFLICT"


def test_event_dominated_prior_is_marked_fragile() -> None:
    values = (
        evidence(item_id=1, event_key="good", observed_day=2, roi="0.3", gross="1.6"),
        evidence(item_id=2, event_key="good", observed_day=3, roi="0.3", gross="1.6"),
        evidence(item_id=3, event_key="good", observed_day=4, roi="0.3", gross="1.6"),
        evidence(item_id=4, event_key="good", observed_day=5, roi="0.3", gross="1.6"),
        evidence(item_id=1, event_key="bad", observed_day=6, roi="0.3", gross="1.6"),
        evidence(item_id=2, event_key="bad", observed_day=7, roi="0.3", gross="1.6"),
        evidence(item_id=3, event_key="bad", observed_day=8, roi="-0.2", gross="0.9"),
        evidence(item_id=4, event_key="bad", observed_day=9, roi="-0.2", gross="0.9"),
    )
    audit = audit_family_prior_generalization(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=values,
    )
    assert audit.item_stability_established is True
    assert audit.event_stability_established is False
    assert audit.status == "EVENT_FRAGILE"


def test_future_outcome_is_excluded_from_generalization_audit() -> None:
    past = stable_evidence()
    future = evidence(
        item_id=99,
        event_key="future",
        observed_day=25,
        roi="-9",
        gross="0.1",
    )
    one = audit_family_prior_generalization(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=past,
    )
    two = audit_family_prior_generalization(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=past + (future,),
    )
    assert one == two


def test_effective_distinct_item_count_penalizes_concentration() -> None:
    values = (
        evidence(item_id=1, event_key="a", observed_day=2, roi="0.2", gross="1.5"),
        evidence(item_id=1, event_key="b", observed_day=3, roi="0.2", gross="1.5"),
        evidence(item_id=1, event_key="c", observed_day=4, roi="0.2", gross="1.5"),
        evidence(item_id=2, event_key="a", observed_day=5, roi="0.2", gross="1.5"),
    )
    audit = audit_family_prior_generalization(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=values,
    )
    assert audit.distinct_prior_item_count == 2
    assert audit.effective_distinct_item_count == Decimal("16") / Decimal("10")


def test_generalization_classification_never_upgrades_exploratory_signal() -> None:
    values = (
        evidence(item_id=1, event_key="old-a", observed_day=2, roi="0.2", gross="1.5"),
        evidence(item_id=2, event_key="old-a", observed_day=3, roi="0.3", gross="1.6"),
        evidence(item_id=1, event_key="old-b", observed_day=4, roi="0.2", gross="1.5"),
        evidence(item_id=2, event_key="old-b", observed_day=5, roi="0.3", gross="1.6"),
    )
    family = estimate_family_recovery_prior(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=values,
    )
    hierarchical = corroborate_post_rerelease_signal(
        primary=primary(), family_prior=family
    )
    fallback = evaluate_sparse_baseline_family_fallback(
        primary=primary(), family_prior=family
    )
    audit = audit_family_prior_generalization(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=values,
    )
    decision = classify_generalization_aware_decision(
        hierarchical=hierarchical,
        sparse_fallback=fallback,
        audit=audit,
    )
    assert hierarchical.research_signal_positive is True
    assert audit.generalization_ready is False
    assert decision.generalization_ready_research_signal is False
    assert decision.strict_signal_tier == "EXPLORATORY_RESEARCH_SIGNAL"


def test_stable_prior_can_be_labeled_generalization_ready_research_signal() -> None:
    values = stable_evidence()
    family = estimate_family_recovery_prior(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=values,
    )
    p = primary()
    hierarchical = corroborate_post_rerelease_signal(primary=p, family_prior=family)
    fallback = evaluate_sparse_baseline_family_fallback(primary=p, family_prior=family)
    audit = audit_family_prior_generalization(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(20),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=values,
    )
    decision = classify_generalization_aware_decision(
        hierarchical=hierarchical,
        sparse_fallback=fallback,
        audit=audit,
    )
    assert decision.generalization_ready_research_signal is True
    assert decision.strict_signal_tier == "GENERALIZATION_READY_RESEARCH_SIGNAL"
