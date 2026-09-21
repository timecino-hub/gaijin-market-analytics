from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterable

from .post_rerelease import (
    PostReReleaseOutcome,
    PostReReleaseReferenceEstimate,
    sale_proceeds,
)


HIERARCHICAL_RECOVERY_VERSION = "1.1.0"


class HierarchicalRecoveryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HistoricalReturnOutcomeEvidence:
    item_id: int
    family: str
    event_key: str
    horizon_days: int
    prediction_as_of: datetime
    outcome_observed_at: datetime
    entry_proxy_price: Decimal
    exit_proxy_price: Decimal
    gross_exit_entry_ratio: Decimal
    realized_net_roi: Decimal


@dataclass(frozen=True, slots=True)
class FamilyRecoveryPriorConfig:
    fee_rate: Decimal = Decimal("0.15")
    currency_quantum: Decimal = Decimal("0.01")
    minimum_prior_examples: int = 2
    minimum_distinct_items: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.fee_rate, Decimal) or not self.fee_rate.is_finite():
            raise HierarchicalRecoveryError("fee_rate must be a finite Decimal.")
        if not Decimal("0") <= self.fee_rate < Decimal("1"):
            raise HierarchicalRecoveryError("fee_rate must be in [0, 1).")
        if (
            not isinstance(self.currency_quantum, Decimal)
            or not self.currency_quantum.is_finite()
            or self.currency_quantum <= Decimal("0")
        ):
            raise HierarchicalRecoveryError("currency_quantum must be a positive Decimal.")
        if self.minimum_prior_examples < 2:
            raise HierarchicalRecoveryError(
                "minimum_prior_examples must be at least 2 for a cross-item prior."
            )
        if self.minimum_distinct_items < 2:
            raise HierarchicalRecoveryError(
                "minimum_distinct_items must be at least 2 for a cross-item prior."
            )


@dataclass(frozen=True, slots=True)
class FamilyRecoveryPriorEstimate:
    target_item_id: int
    target_event_key: str
    family: str
    prediction_as_of: datetime
    horizon_days: int
    prior_example_count: int
    distinct_prior_item_count: int
    profitable_prior_example_count: int
    profitable_support_share: Decimal | None
    median_gross_exit_entry_ratio: Decimal | None
    entry_proxy_price: Decimal | None
    reference_sell_price: Decimal | None
    sale_proceeds: Decimal | None
    net_profit: Decimal | None
    net_roi: Decimal | None
    prior_available: bool
    family_support_positive: bool
    explanation_codes: tuple[str, ...]
    research_only: bool = True
    fill_claim: bool = False
    version: str = HIERARCHICAL_RECOVERY_VERSION


@dataclass(frozen=True, slots=True)
class HierarchicalRecoveryDecision:
    primary: PostReReleaseReferenceEstimate
    family_prior: FamilyRecoveryPriorEstimate
    corroboration_status: str
    research_signal_positive: bool
    explanation_codes: tuple[str, ...]
    research_only: bool = True
    fill_claim: bool = False
    version: str = HIERARCHICAL_RECOVERY_VERSION


def return_outcome_evidence(
    *,
    item_id: int,
    estimate: PostReReleaseReferenceEstimate,
    outcome: PostReReleaseOutcome,
) -> HistoricalReturnOutcomeEvidence | None:
    """Convert an observed walk-forward target into reusable prior evidence.

    The evidence is deliberately independent of whether the original prediction
    was positive.  It records only an entry proxy and a future exit proxy that
    had actually become observable.  This lets later events learn from misses as
    well as wins without rewriting the old prediction.
    """

    if item_id <= 0:
        raise HierarchicalRecoveryError("item_id must be positive.")
    if estimate.event_key != outcome.event_key:
        raise HierarchicalRecoveryError("estimate/outcome event_key mismatch.")
    if estimate.horizon_days != outcome.horizon_days:
        raise HierarchicalRecoveryError("estimate/outcome horizon mismatch.")
    if not outcome.target_available:
        return None
    if (
        estimate.entry_proxy_price is None
        or outcome.exit_proxy_price is None
        or outcome.realized_net_roi is None
        or outcome.observed_at is None
    ):
        return None
    if outcome.observed_at > outcome.evaluation_as_of:
        raise HierarchicalRecoveryError("outcome observed_at exceeds evaluation_as_of.")
    if estimate.prediction_as_of >= outcome.observed_at:
        raise HierarchicalRecoveryError(
            "outcome must become observable strictly after the original prediction."
        )
    return HistoricalReturnOutcomeEvidence(
        item_id=item_id,
        family=estimate.family,
        event_key=estimate.event_key,
        horizon_days=estimate.horizon_days,
        prediction_as_of=estimate.prediction_as_of,
        outcome_observed_at=outcome.observed_at,
        entry_proxy_price=estimate.entry_proxy_price,
        exit_proxy_price=outcome.exit_proxy_price,
        gross_exit_entry_ratio=outcome.exit_proxy_price / estimate.entry_proxy_price,
        realized_net_roi=outcome.realized_net_roi,
    )


def estimate_family_recovery_prior(
    *,
    target_item_id: int,
    target_event_key: str,
    family: str,
    prediction_as_of: datetime,
    horizon_days: int,
    entry_proxy_price: Decimal | None,
    prior_outcomes: Iterable[HistoricalReturnOutcomeEvidence],
    config: FamilyRecoveryPriorConfig | None = None,
) -> FamilyRecoveryPriorEstimate:
    """Estimate a scale-free family prior from already observed earlier outcomes.

    A family prior is deliberately independent cross-item evidence: *all*
    historical outcomes from ``target_item_id`` are excluded, not merely the
    current target event. This prevents the target vehicle's own prior returns
    from being counted once in the item-specific model and again as supposedly
    independent family corroboration. The family prior is not a standalone
    opportunity signal. ``corroborate_post_rerelease_signal`` can confirm or
    veto a positive item-specific estimate, never promote an item whose own
    estimate is absent or non-positive.
    """

    cfg = config or FamilyRecoveryPriorConfig()
    if target_item_id <= 0:
        raise HierarchicalRecoveryError("target_item_id must be positive.")
    if not target_event_key.strip() or not family.strip():
        raise HierarchicalRecoveryError("target_event_key and family must be non-empty.")
    prediction_as_of = _utc(prediction_as_of, "prediction_as_of")
    if horizon_days <= 0:
        raise HierarchicalRecoveryError("horizon_days must be positive.")
    _validate_price(entry_proxy_price, "entry_proxy_price")

    visible = tuple(
        sorted(
            (
                value
                for value in prior_outcomes
                if value.family == family
                and value.horizon_days == horizon_days
                and value.outcome_observed_at <= prediction_as_of
                and value.item_id != target_item_id
            ),
            key=lambda value: (
                value.outcome_observed_at,
                value.item_id,
                value.event_key,
            ),
        )
    )
    distinct_items = len({value.item_id for value in visible})
    profitable_count = sum(
        value.realized_net_roi > Decimal("0") for value in visible
    )
    support = (
        Decimal(profitable_count) / Decimal(len(visible))
        if visible
        else None
    )
    median_ratio = _median(
        tuple(value.gross_exit_entry_ratio for value in visible)
    )
    prior_available = (
        len(visible) >= cfg.minimum_prior_examples
        and distinct_items >= cfg.minimum_distinct_items
        and median_ratio is not None
    )
    codes: list[str] = []
    codes.append("target_item_history_excluded_from_family_prior")
    if len(visible) < cfg.minimum_prior_examples:
        codes.append("insufficient_family_prior_examples")
    if distinct_items < cfg.minimum_distinct_items:
        codes.append("insufficient_distinct_family_prior_items")
    if prior_available:
        codes.append("cross_item_family_prior_available")

    reference = None
    proceeds = None
    net_profit = None
    net_roi = None
    family_positive = False
    if prior_available and entry_proxy_price is not None and median_ratio is not None:
        reference = entry_proxy_price * median_ratio
        proceeds = sale_proceeds(reference, cfg.fee_rate, cfg.currency_quantum)
        net_profit = proceeds - entry_proxy_price
        net_roi = net_profit / entry_proxy_price
        # A tie is not a majority.  Both the distribution and its median proxy
        # must support profitability before family evidence is called positive.
        family_positive = (
            support is not None
            and support > Decimal("0.5")
            and net_profit > Decimal("0")
        )
        codes.append(
            "family_profitability_majority_supported"
            if support is not None and support > Decimal("0.5")
            else "family_profitability_majority_not_supported"
        )
        codes.append(
            "family_fee_adjusted_proxy_positive"
            if net_profit > Decimal("0")
            else "family_fee_adjusted_proxy_non_positive"
        )
    elif prior_available:
        codes.append("family_entry_proxy_unavailable")

    return FamilyRecoveryPriorEstimate(
        target_item_id=target_item_id,
        target_event_key=target_event_key,
        family=family,
        prediction_as_of=prediction_as_of,
        horizon_days=horizon_days,
        prior_example_count=len(visible),
        distinct_prior_item_count=distinct_items,
        profitable_prior_example_count=profitable_count,
        profitable_support_share=support,
        median_gross_exit_entry_ratio=median_ratio,
        entry_proxy_price=entry_proxy_price,
        reference_sell_price=reference,
        sale_proceeds=proceeds,
        net_profit=net_profit,
        net_roi=net_roi,
        prior_available=prior_available,
        family_support_positive=family_positive,
        explanation_codes=tuple(codes),
    )


def corroborate_post_rerelease_signal(
    *,
    primary: PostReReleaseReferenceEstimate,
    family_prior: FamilyRecoveryPriorEstimate,
) -> HierarchicalRecoveryDecision:
    """Require item-specific evidence first and family evidence second.

    This intentionally forbids a common hierarchical-model failure mode: strong
    performance by neighbouring items must never create a signal for an item
    whose own historical baseline is missing or non-profitable.
    """

    if primary.event_key != family_prior.target_event_key:
        raise HierarchicalRecoveryError("primary/family event mismatch.")
    if primary.family != family_prior.family:
        raise HierarchicalRecoveryError("primary/family pool mismatch.")
    if primary.horizon_days != family_prior.horizon_days:
        raise HierarchicalRecoveryError("primary/family horizon mismatch.")
    if primary.prediction_as_of != family_prior.prediction_as_of:
        raise HierarchicalRecoveryError("primary/family prediction timestamp mismatch.")

    codes: list[str] = []
    if not primary.research_signal_positive:
        status = "PRIMARY_NOT_POSITIVE"
        positive = False
        codes.append("item_specific_signal_required")
    elif not family_prior.prior_available:
        status = "FAMILY_PRIOR_INSUFFICIENT"
        positive = False
        codes.append("family_corroboration_required")
    elif not family_prior.family_support_positive:
        status = "FAMILY_PRIOR_CONTRADICTS"
        positive = False
        codes.append("family_prior_does_not_confirm_profitability")
    else:
        status = "CONFIRMED"
        positive = True
        codes.extend((
            "item_specific_signal_positive",
            "cross_item_family_prior_confirms",
        ))

    return HierarchicalRecoveryDecision(
        primary=primary,
        family_prior=family_prior,
        corroboration_status=status,
        research_signal_positive=positive,
        explanation_codes=tuple(codes),
    )


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _validate_price(value: Decimal | None, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0"):
        raise HierarchicalRecoveryError(
            f"{field_name} must be a positive finite Decimal or None."
        )


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HierarchicalRecoveryError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)

@dataclass(frozen=True, slots=True)
class SparseBaselineFallbackCandidate:
    primary: PostReReleaseReferenceEstimate
    family_prior: FamilyRecoveryPriorEstimate
    candidate_positive: bool
    explanation_codes: tuple[str, ...]
    requires_operator_review: bool = True
    research_only: bool = True
    fill_claim: bool = False
    version: str = HIERARCHICAL_RECOVERY_VERSION


def evaluate_sparse_baseline_family_fallback(
    *,
    primary: PostReReleaseReferenceEstimate,
    family_prior: FamilyRecoveryPriorEstimate,
) -> SparseBaselineFallbackCandidate:
    """Expose a guarded research candidate for sparse-but-present local baselines.

    This does not alter the strict hierarchical signal.  It is intentionally a
    separate, operator-review-only surface.  The fallback is allowed only when:

    * the item-specific estimator failed specifically because its pre-event
      baseline was too sparse;
    * at least one real pre-event price observation exists (represented by a
      non-null current_pre_price_median);
    * the cross-item family prior is available from at least two distinct items;
    * every visible family example was profitable after fees; and
    * the family median proxy is itself profitable after fees.

    A completely missing local baseline can therefore never be rescued by other
    vehicles' performance.
    """

    if primary.event_key != family_prior.target_event_key:
        raise HierarchicalRecoveryError("primary/family event mismatch.")
    if primary.family != family_prior.family:
        raise HierarchicalRecoveryError("primary/family pool mismatch.")
    if primary.horizon_days != family_prior.horizon_days:
        raise HierarchicalRecoveryError("primary/family horizon mismatch.")
    if primary.prediction_as_of != family_prior.prediction_as_of:
        raise HierarchicalRecoveryError("primary/family prediction timestamp mismatch.")

    codes: list[str] = []
    baseline_sparse = (
        not primary.research_signal_positive
        and "current_pre_event_baseline_ineligible" in primary.explanation_codes
    )
    baseline_present = primary.current_pre_price_median is not None
    unanimous_family = (
        family_prior.prior_available
        and family_prior.profitable_support_share == Decimal("1")
        and family_prior.family_support_positive
    )
    candidate = baseline_sparse and baseline_present and unanimous_family

    if not baseline_sparse:
        codes.append("fallback_requires_sparse_primary_baseline_failure")
    if not baseline_present:
        codes.append("fallback_forbids_missing_local_baseline")
    if not family_prior.prior_available:
        codes.append("fallback_requires_cross_item_family_prior")
    elif family_prior.profitable_support_share != Decimal("1"):
        codes.append("fallback_requires_unanimous_family_profitability")
    elif not family_prior.family_support_positive:
        codes.append("fallback_requires_positive_family_proxy")
    if candidate:
        codes.extend((
            "sparse_local_baseline_present",
            "unanimous_cross_item_family_support",
            "operator_review_required_before_any_use",
        ))

    return SparseBaselineFallbackCandidate(
        primary=primary,
        family_prior=family_prior,
        candidate_positive=candidate,
        explanation_codes=tuple(codes),
    )
