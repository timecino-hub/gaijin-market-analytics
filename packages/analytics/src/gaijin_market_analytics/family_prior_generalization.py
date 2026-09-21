from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterable

from .hierarchical_recovery import (
    FamilyRecoveryPriorConfig,
    FamilyRecoveryPriorEstimate,
    HistoricalReturnOutcomeEvidence,
    HierarchicalRecoveryDecision,
    SparseBaselineFallbackCandidate,
    estimate_family_recovery_prior,
)


FAMILY_PRIOR_GENERALIZATION_VERSION = "1.1.0"


class FamilyPriorGeneralizationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FamilyPriorGeneralizationAudit:
    target_item_id: int
    target_event_key: str
    family: str
    prediction_as_of: datetime
    horizon_days: int
    prior_example_count: int
    distinct_prior_item_count: int
    distinct_prior_event_count: int
    maximum_item_example_share: Decimal | None
    maximum_event_example_share: Decimal | None
    effective_distinct_item_count: Decimal | None
    item_balanced_profitable_group_count: int
    item_balanced_profitable_support_share: Decimal | None
    item_balanced_median_gross_exit_entry_ratio: Decimal | None
    item_balanced_support_positive: bool
    event_balanced_profitable_group_count: int
    event_balanced_profitable_support_share: Decimal | None
    event_balanced_median_gross_exit_entry_ratio: Decimal | None
    event_balanced_support_positive: bool
    item_leave_one_out_fold_count: int
    item_leave_one_out_available_count: int
    item_leave_one_out_positive_count: int
    item_leave_one_out_min_profitable_support_share: Decimal | None
    item_leave_one_out_max_profitable_support_share: Decimal | None
    event_leave_one_out_fold_count: int
    event_leave_one_out_available_count: int
    event_leave_one_out_positive_count: int
    event_leave_one_out_min_profitable_support_share: Decimal | None
    event_leave_one_out_max_profitable_support_share: Decimal | None
    item_stability_established: bool
    event_stability_established: bool
    generalization_ready: bool
    status: str
    explanation_codes: tuple[str, ...]
    research_only: bool = True
    fill_claim: bool = False
    version: str = FAMILY_PRIOR_GENERALIZATION_VERSION


@dataclass(frozen=True, slots=True)
class GeneralizationAwareRecoveryDecision:
    hierarchical: HierarchicalRecoveryDecision
    sparse_fallback: SparseBaselineFallbackCandidate
    audit: FamilyPriorGeneralizationAudit
    strict_signal_tier: str
    sparse_review_tier: str
    generalization_ready_research_signal: bool
    generalization_ready_sparse_review_candidate: bool
    explanation_codes: tuple[str, ...]
    research_only: bool = True
    fill_claim: bool = False
    version: str = FAMILY_PRIOR_GENERALIZATION_VERSION


def audit_family_prior_generalization(
    *,
    target_item_id: int,
    target_event_key: str,
    family: str,
    prediction_as_of: datetime,
    horizon_days: int,
    entry_proxy_price: Decimal | None,
    prior_outcomes: Iterable[HistoricalReturnOutcomeEvidence],
    config: FamilyRecoveryPriorConfig | None = None,
) -> FamilyPriorGeneralizationAudit:
    """Audit whether a family prior generalizes beyond one item or one event.

    The family prior is independent of the target vehicle: every historical
    outcome from ``target_item_id`` is excluded before concentration or
    leave-one-out checks are computed. This prevents target-item history from
    masquerading as cross-item breadth. The audit then asks a stronger
    *generalization* question:

    * concentration reports how much the prior is dominated by one item/event;
    * leave-one-item-out checks whether deleting any one vehicle preserves a
      usable positive family prior;
    * leave-one-event-out checks whether deleting any one return event/year
      preserves a usable positive family prior.

    Generalization-ready therefore means the conclusion is not dependent on any
    single vehicle or any single historical return event.  This remains a
    research diagnostic and does not create a production opportunity signal.
    """

    cfg = config or FamilyRecoveryPriorConfig()
    if target_item_id <= 0:
        raise FamilyPriorGeneralizationError("target_item_id must be positive.")
    if not target_event_key.strip() or not family.strip():
        raise FamilyPriorGeneralizationError(
            "target_event_key and family must be non-empty."
        )
    prediction_as_of = _utc(prediction_as_of, "prediction_as_of")
    if horizon_days <= 0:
        raise FamilyPriorGeneralizationError("horizon_days must be positive.")
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
    item_counts = _counts(value.item_id for value in visible)
    event_counts = _counts(value.event_key for value in visible)
    item_share = _maximum_share(item_counts, len(visible))
    event_share = _maximum_share(event_counts, len(visible))
    item_effective_count = _effective_category_count(item_counts)
    item_balanced = _balanced_axis_summary(
        visible,
        axis="item",
        entry_proxy_price=entry_proxy_price,
        config=cfg,
    )
    event_balanced = _balanced_axis_summary(
        visible,
        axis="event",
        entry_proxy_price=entry_proxy_price,
        config=cfg,
    )

    original = estimate_family_recovery_prior(
        target_item_id=target_item_id,
        target_event_key=target_event_key,
        family=family,
        prediction_as_of=prediction_as_of,
        horizon_days=horizon_days,
        entry_proxy_price=entry_proxy_price,
        prior_outcomes=visible,
        config=cfg,
    )

    item_folds = tuple(
        estimate_family_recovery_prior(
            target_item_id=target_item_id,
            target_event_key=target_event_key,
            family=family,
            prediction_as_of=prediction_as_of,
            horizon_days=horizon_days,
            entry_proxy_price=entry_proxy_price,
            prior_outcomes=tuple(
                value for value in visible if value.item_id != held_out_item
            ),
            config=cfg,
        )
        for held_out_item in sorted(item_counts)
    )
    event_folds = tuple(
        estimate_family_recovery_prior(
            target_item_id=target_item_id,
            target_event_key=target_event_key,
            family=family,
            prediction_as_of=prediction_as_of,
            horizon_days=horizon_days,
            entry_proxy_price=entry_proxy_price,
            prior_outcomes=tuple(
                value for value in visible if value.event_key != held_out_event
            ),
            config=cfg,
        )
        for held_out_event in sorted(event_counts)
    )

    item_available = tuple(value for value in item_folds if value.prior_available)
    event_available = tuple(value for value in event_folds if value.prior_available)
    item_positive_count = sum(value.family_support_positive for value in item_folds)
    event_positive_count = sum(value.family_support_positive for value in event_folds)

    # To remove one item and still retain the Stage 6 minimum number of distinct
    # items, the original evidence necessarily needs at least one extra item.
    minimum_items_for_stability = cfg.minimum_distinct_items + 1
    item_stable = (
        original.prior_available
        and original.family_support_positive
        and len(item_counts) >= minimum_items_for_stability
        and len(item_folds) == len(item_counts)
        and len(item_available) == len(item_folds)
        and item_positive_count == len(item_folds)
    )

    # Cross-event stability is meaningful only after at least two separate return
    # events have actually produced observable outcomes.  Every held-out-event
    # fold must still satisfy the original cross-item family-prior contract.
    event_stable = (
        original.prior_available
        and original.family_support_positive
        and len(event_counts) >= 2
        and len(event_folds) == len(event_counts)
        and len(event_available) == len(event_folds)
        and event_positive_count == len(event_folds)
    )

    generalization_ready = (
        item_balanced[3]
        and event_balanced[3]
        and item_stable
        and event_stable
    )
    codes: list[str] = []
    if not original.prior_available:
        status = "PRIOR_UNAVAILABLE"
        codes.append("family_prior_unavailable")
    elif not original.family_support_positive:
        status = "PRIOR_NON_POSITIVE"
        codes.append("family_prior_not_positive")
    elif not item_balanced[3]:
        status = "ITEM_BALANCE_CONFLICT"
        codes.append("equal_item_weighting_does_not_confirm_family_prior")
    elif not event_balanced[3]:
        status = "EVENT_BALANCE_CONFLICT"
        codes.append("equal_event_weighting_does_not_confirm_family_prior")
    elif len(item_counts) < minimum_items_for_stability:
        status = "INSUFFICIENT_ITEM_BREADTH"
        codes.append("leave_one_item_out_requires_additional_distinct_item")
    elif not item_stable:
        status = "ITEM_FRAGILE"
        codes.append("family_prior_depends_on_at_least_one_item")
    elif len(event_counts) < 2:
        status = "INSUFFICIENT_EVENT_BREADTH"
        codes.append("leave_one_event_out_requires_multiple_historical_events")
    elif not event_stable:
        status = "EVENT_FRAGILE"
        codes.append("family_prior_depends_on_at_least_one_event")
    else:
        status = "GENERALIZATION_READY"
        codes.extend(
            (
                "leave_one_item_out_stable",
                "leave_one_event_out_stable",
                "family_prior_generalization_ready",
            )
        )

    if item_share is not None:
        codes.append("item_concentration_reported")
    if event_share is not None:
        codes.append("event_concentration_reported")

    return FamilyPriorGeneralizationAudit(
        target_item_id=target_item_id,
        target_event_key=target_event_key,
        family=family,
        prediction_as_of=prediction_as_of,
        horizon_days=horizon_days,
        prior_example_count=len(visible),
        distinct_prior_item_count=len(item_counts),
        distinct_prior_event_count=len(event_counts),
        maximum_item_example_share=item_share,
        maximum_event_example_share=event_share,
        effective_distinct_item_count=item_effective_count,
        item_balanced_profitable_group_count=item_balanced[0],
        item_balanced_profitable_support_share=item_balanced[1],
        item_balanced_median_gross_exit_entry_ratio=item_balanced[2],
        item_balanced_support_positive=item_balanced[3],
        event_balanced_profitable_group_count=event_balanced[0],
        event_balanced_profitable_support_share=event_balanced[1],
        event_balanced_median_gross_exit_entry_ratio=event_balanced[2],
        event_balanced_support_positive=event_balanced[3],
        item_leave_one_out_fold_count=len(item_folds),
        item_leave_one_out_available_count=len(item_available),
        item_leave_one_out_positive_count=item_positive_count,
        item_leave_one_out_min_profitable_support_share=_fold_support_bound(
            item_available, minimum=True
        ),
        item_leave_one_out_max_profitable_support_share=_fold_support_bound(
            item_available, minimum=False
        ),
        event_leave_one_out_fold_count=len(event_folds),
        event_leave_one_out_available_count=len(event_available),
        event_leave_one_out_positive_count=event_positive_count,
        event_leave_one_out_min_profitable_support_share=_fold_support_bound(
            event_available, minimum=True
        ),
        event_leave_one_out_max_profitable_support_share=_fold_support_bound(
            event_available, minimum=False
        ),
        item_stability_established=item_stable,
        event_stability_established=event_stable,
        generalization_ready=generalization_ready,
        status=status,
        explanation_codes=tuple(codes),
    )


def classify_generalization_aware_decision(
    *,
    hierarchical: HierarchicalRecoveryDecision,
    sparse_fallback: SparseBaselineFallbackCandidate,
    audit: FamilyPriorGeneralizationAudit,
) -> GeneralizationAwareRecoveryDecision:
    """Separate exploratory evidence from evidence that survived breadth checks."""

    if hierarchical.primary.event_key != audit.target_event_key:
        raise FamilyPriorGeneralizationError("hierarchical/audit event mismatch.")
    if hierarchical.primary.family != audit.family:
        raise FamilyPriorGeneralizationError("hierarchical/audit family mismatch.")
    if hierarchical.primary.horizon_days != audit.horizon_days:
        raise FamilyPriorGeneralizationError("hierarchical/audit horizon mismatch.")
    if hierarchical.primary.prediction_as_of != audit.prediction_as_of:
        raise FamilyPriorGeneralizationError(
            "hierarchical/audit prediction timestamp mismatch."
        )
    if sparse_fallback.primary != hierarchical.primary:
        raise FamilyPriorGeneralizationError(
            "sparse fallback must refer to the same primary estimate."
        )

    codes: list[str] = []
    if hierarchical.research_signal_positive:
        if audit.generalization_ready:
            strict_tier = "GENERALIZATION_READY_RESEARCH_SIGNAL"
            strict_ready = True
            codes.append("strict_signal_survives_generalization_audit")
        else:
            strict_tier = "EXPLORATORY_RESEARCH_SIGNAL"
            strict_ready = False
            codes.append("strict_signal_lacks_cross_item_or_cross_event_breadth")
    else:
        strict_tier = "NO_STRICT_SIGNAL"
        strict_ready = False

    if sparse_fallback.candidate_positive:
        if audit.generalization_ready:
            sparse_tier = "SUPPORTED_OPERATOR_REVIEW_CANDIDATE"
            sparse_ready = True
            codes.append("sparse_review_candidate_survives_generalization_audit")
        else:
            sparse_tier = "EXPLORATORY_OPERATOR_REVIEW_CANDIDATE"
            sparse_ready = False
            codes.append("sparse_review_candidate_not_generalization_ready")
    else:
        sparse_tier = "NO_SPARSE_REVIEW_CANDIDATE"
        sparse_ready = False

    return GeneralizationAwareRecoveryDecision(
        hierarchical=hierarchical,
        sparse_fallback=sparse_fallback,
        audit=audit,
        strict_signal_tier=strict_tier,
        sparse_review_tier=sparse_tier,
        generalization_ready_research_signal=strict_ready,
        generalization_ready_sparse_review_candidate=sparse_ready,
        explanation_codes=tuple(codes),
    )



def _balanced_axis_summary(
    values: tuple[HistoricalReturnOutcomeEvidence, ...],
    *,
    axis: str,
    entry_proxy_price: Decimal | None,
    config: FamilyRecoveryPriorConfig,
) -> tuple[int, Decimal | None, Decimal | None, bool]:
    if axis not in {"item", "event"}:
        raise FamilyPriorGeneralizationError("unknown balancing axis.")
    groups: dict[object, list[Decimal]] = {}
    for value in values:
        key: object = value.item_id if axis == "item" else value.event_key
        groups.setdefault(key, []).append(value.gross_exit_entry_ratio)
    medians = tuple(
        _median(tuple(group_values))
        for _, group_values in sorted(groups.items(), key=lambda pair: str(pair[0]))
    )
    medians = tuple(value for value in medians if value is not None)
    if not medians:
        return 0, None, None, False
    overall_median = _median(medians)
    if entry_proxy_price is None or overall_median is None:
        return 0, None, overall_median, False
    profitable = 0
    for ratio in medians:
        reference = entry_proxy_price * ratio
        proceeds = _sale_proceeds(
            reference, config.fee_rate, config.currency_quantum
        )
        if proceeds > entry_proxy_price:
            profitable += 1
    support = Decimal(profitable) / Decimal(len(medians))
    overall_reference = entry_proxy_price * overall_median
    overall_proceeds = _sale_proceeds(
        overall_reference, config.fee_rate, config.currency_quantum
    )
    positive = support > Decimal("0.5") and overall_proceeds > entry_proxy_price
    return profitable, support, overall_median, positive


def _sale_proceeds(
    gross_sale_price: Decimal, fee_rate: Decimal, quantum: Decimal
) -> Decimal:
    return (
        (gross_sale_price * (Decimal("1") - fee_rate)) / quantum
    ).to_integral_value(rounding="ROUND_DOWN") * quantum


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")

def _counts(values: Iterable[object]) -> dict[object, int]:
    result: dict[object, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def _maximum_share(counts: dict[object, int], total: int) -> Decimal | None:
    if not counts or total <= 0:
        return None
    return Decimal(max(counts.values())) / Decimal(total)


def _effective_category_count(counts: dict[object, int]) -> Decimal | None:
    if not counts:
        return None
    total = Decimal(sum(counts.values()))
    denominator = sum(
        (Decimal(value) * Decimal(value) for value in counts.values()),
        Decimal("0"),
    )
    if denominator == Decimal("0"):
        return None
    return total * total / denominator


def _fold_support_bound(
    folds: tuple[FamilyRecoveryPriorEstimate, ...],
    *,
    minimum: bool,
) -> Decimal | None:
    values = tuple(
        value.profitable_support_share
        for value in folds
        if value.profitable_support_share is not None
    )
    if not values:
        return None
    return min(values) if minimum else max(values)


def _validate_price(value: Decimal | None, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0"):
        raise FamilyPriorGeneralizationError(
            f"{field_name} must be a positive finite Decimal or None."
        )


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FamilyPriorGeneralizationError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
