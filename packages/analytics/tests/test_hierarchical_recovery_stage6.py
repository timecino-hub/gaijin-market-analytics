from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from gaijin_market_analytics.hierarchical_recovery import (
    FamilyRecoveryPriorConfig,
    HistoricalReturnOutcomeEvidence,
    HierarchicalRecoveryError,
    corroborate_post_rerelease_signal,
    estimate_family_recovery_prior,
    return_outcome_evidence,
)
from gaijin_market_analytics.post_rerelease import (
    PostReReleaseOutcome,
    PostReReleaseReferenceEstimate,
)


def dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def primary(*, positive: bool = True) -> PostReReleaseReferenceEstimate:
    return PostReReleaseReferenceEstimate(
        event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(10),
        horizon_days=90,
        prior_event_count=2,
        effective_prior_event_weight=Decimal("2"),
        mean_prior_context_comparability=Decimal("1"),
        current_strength_index=Decimal("1"),
        current_attention_index=Decimal("1"),
        entry_proxy_price=Decimal("100"),
        current_pre_price_median=Decimal("150"),
        predicted_recovery_ratio=Decimal("0.9"),
        reference_sell_price=Decimal("135"),
        sale_proceeds=Decimal("114.75"),
        fee_amount=Decimal("20.25"),
        net_profit=Decimal("14.75") if positive else Decimal("-1"),
        net_roi=Decimal("0.1475") if positive else Decimal("-0.01"),
        research_signal_positive=positive,
        explanation_codes=(),
    )


def evidence(
    *, item_id: int, observed_day: int, roi: str, gross: str
) -> HistoricalReturnOutcomeEvidence:
    return HistoricalReturnOutcomeEvidence(
        item_id=item_id,
        family="HISTORY_LUNAR",
        event_key=f"event-{item_id}",
        horizon_days=90,
        prediction_as_of=dt(1),
        outcome_observed_at=dt(observed_day),
        entry_proxy_price=Decimal("100"),
        exit_proxy_price=Decimal(gross) * Decimal("100"),
        gross_exit_entry_ratio=Decimal(gross),
        realized_net_roi=Decimal(roi),
    )


def test_family_prior_requires_cross_item_history_and_strict_majority() -> None:
    result = estimate_family_recovery_prior(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(10),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=(
            evidence(item_id=1, observed_day=5, roi="0.20", gross="1.50"),
            evidence(item_id=2, observed_day=6, roi="-0.10", gross="1.00"),
        ),
    )
    assert result.prior_available is True
    assert result.profitable_support_share == Decimal("0.5")
    assert result.family_support_positive is False
    assert "family_profitability_majority_not_supported" in result.explanation_codes


def test_future_family_outcome_cannot_affect_past_prediction() -> None:
    past = estimate_family_recovery_prior(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(10),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=(
            evidence(item_id=1, observed_day=5, roi="0.20", gross="1.50"),
            evidence(item_id=2, observed_day=6, roi="0.30", gross="1.60"),
        ),
    )
    with_future = estimate_family_recovery_prior(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(10),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=(
            evidence(item_id=1, observed_day=5, roi="0.20", gross="1.50"),
            evidence(item_id=2, observed_day=6, roi="0.30", gross="1.60"),
            evidence(item_id=3, observed_day=20, roi="9", gross="20"),
        ),
    )
    assert past == with_future


def test_family_prior_cannot_promote_non_positive_primary() -> None:
    family = estimate_family_recovery_prior(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(10),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=(
            evidence(item_id=1, observed_day=5, roi="0.20", gross="1.50"),
            evidence(item_id=2, observed_day=6, roi="0.30", gross="1.60"),
        ),
    )
    decision = corroborate_post_rerelease_signal(
        primary=primary(positive=False), family_prior=family
    )
    assert family.family_support_positive is True
    assert decision.research_signal_positive is False
    assert decision.corroboration_status == "PRIMARY_NOT_POSITIVE"


def test_positive_primary_requires_positive_family_corroboration() -> None:
    family = estimate_family_recovery_prior(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(10),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=(
            evidence(item_id=1, observed_day=5, roi="0.20", gross="1.50"),
            evidence(item_id=2, observed_day=6, roi="0.30", gross="1.60"),
            evidence(item_id=3, observed_day=7, roi="-0.10", gross="1.00"),
        ),
    )
    decision = corroborate_post_rerelease_signal(primary=primary(), family_prior=family)
    assert family.profitable_support_share == Decimal("2") / Decimal("3")
    assert family.family_support_positive is True
    assert decision.research_signal_positive is True
    assert decision.corroboration_status == "CONFIRMED"


def test_one_item_cannot_masquerade_as_cross_item_prior() -> None:
    result = estimate_family_recovery_prior(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(10),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=(
            evidence(item_id=1, observed_day=5, roi="0.20", gross="1.50"),
            HistoricalReturnOutcomeEvidence(
                item_id=1,
                family="HISTORY_LUNAR",
                event_key="event-older",
                horizon_days=90,
                prediction_as_of=dt(1),
                outcome_observed_at=dt(6),
                entry_proxy_price=Decimal("100"),
                exit_proxy_price=Decimal("160"),
                gross_exit_entry_ratio=Decimal("1.60"),
                realized_net_roi=Decimal("0.30"),
            ),
        ),
    )
    assert result.prior_example_count == 2
    assert result.distinct_prior_item_count == 1
    assert result.prior_available is False


def test_return_outcome_evidence_uses_observed_future_target_not_prediction() -> None:
    p = primary()
    outcome = PostReReleaseOutcome(
        event_key=p.event_key,
        horizon_days=p.horizon_days,
        evaluation_as_of=dt(20),
        target_at=dt(18),
        observed_at=dt(19),
        exit_proxy_price=Decimal("150"),
        sale_proceeds=Decimal("127.50"),
        realized_net_profit=Decimal("27.50"),
        realized_net_roi=Decimal("0.275"),
        target_available=True,
    )
    value = return_outcome_evidence(item_id=9, estimate=p, outcome=outcome)
    assert value is not None
    assert value.gross_exit_entry_ratio == Decimal("1.5")
    assert value.realized_net_roi == Decimal("0.275")


def test_family_prior_config_rejects_single_item_semantics() -> None:
    with pytest.raises(HierarchicalRecoveryError):
        FamilyRecoveryPriorConfig(minimum_distinct_items=1)

from gaijin_market_analytics.hierarchical_recovery import evaluate_sparse_baseline_family_fallback


def sparse_primary(*, baseline: Decimal | None) -> PostReReleaseReferenceEstimate:
    return PostReReleaseReferenceEstimate(
        event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(10),
        horizon_days=90,
        prior_event_count=0,
        effective_prior_event_weight=Decimal("0"),
        mean_prior_context_comparability=None,
        current_strength_index=Decimal("1"),
        current_attention_index=Decimal("1"),
        entry_proxy_price=Decimal("100"),
        current_pre_price_median=baseline,
        predicted_recovery_ratio=None,
        reference_sell_price=None,
        sale_proceeds=None,
        fee_amount=None,
        net_profit=None,
        net_roi=None,
        research_signal_positive=False,
        explanation_codes=("current_pre_event_baseline_ineligible",),
    )


def unanimous_family_prior() -> object:
    return estimate_family_recovery_prior(
        target_item_id=9,
        target_event_key="event-current",
        family="HISTORY_LUNAR",
        prediction_as_of=dt(10),
        horizon_days=90,
        entry_proxy_price=Decimal("100"),
        prior_outcomes=(
            evidence(item_id=1, observed_day=5, roi="0.20", gross="1.50"),
            evidence(item_id=2, observed_day=6, roi="0.30", gross="1.60"),
        ),
    )


def test_sparse_baseline_fallback_requires_unanimous_family_support() -> None:
    family = unanimous_family_prior()
    candidate = evaluate_sparse_baseline_family_fallback(
        primary=sparse_primary(baseline=Decimal("150")),
        family_prior=family,
    )
    assert candidate.candidate_positive is True
    assert candidate.requires_operator_review is True


def test_sparse_baseline_fallback_never_rescues_missing_local_baseline() -> None:
    candidate = evaluate_sparse_baseline_family_fallback(
        primary=sparse_primary(baseline=None),
        family_prior=unanimous_family_prior(),
    )
    assert candidate.candidate_positive is False
    assert "fallback_forbids_missing_local_baseline" in candidate.explanation_codes


def test_sparse_baseline_fallback_does_not_override_regular_negative_primary() -> None:
    candidate = evaluate_sparse_baseline_family_fallback(
        primary=primary(positive=False),
        family_prior=unanimous_family_prior(),
    )
    assert candidate.candidate_positive is False
    assert "fallback_requires_sparse_primary_baseline_failure" in candidate.explanation_codes
