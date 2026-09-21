from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Iterable, Mapping

from .historical_trades import (
    DailyHistoricalTradeEvidence,
    HistoricalTradeBucketEvidence,
    aggregate_trade_evidence_by_utc_day,
    select_historical_trade_evidence,
)
from .manual_context import (
    ResolvedManualMarketContext,
    context_comparability,
    weighted_decimal_median,
)
from .supply_cycle_recovery import SupplyEventRecovery
from .supply_cycles import SupplyPoolEvent


POST_RERELEASE_WALK_FORWARD_VERSION = "1.1.0"
PRICE_PROXY_SEMANTICS = "historical_trade_daily_vwap_proxy_not_executable_quote"


class PostReReleaseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PostReReleaseConfig:
    fee_rate: Decimal = Decimal("0.15")
    currency_quantum: Decimal = Decimal("0.01")
    minimum_prior_events: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.fee_rate, Decimal) or not self.fee_rate.is_finite():
            raise PostReReleaseError("fee_rate must be a finite Decimal.")
        if not Decimal("0") <= self.fee_rate < Decimal("1"):
            raise PostReReleaseError("fee_rate must be in [0, 1).")
        if (
            not isinstance(self.currency_quantum, Decimal)
            or not self.currency_quantum.is_finite()
            or self.currency_quantum <= Decimal("0")
        ):
            raise PostReReleaseError("currency_quantum must be a positive Decimal.")
        if self.minimum_prior_events <= 0:
            raise PostReReleaseError("minimum_prior_events must be positive.")


@dataclass(frozen=True, slots=True)
class PostReReleaseReferenceEstimate:
    event_key: str
    family: str
    prediction_as_of: datetime
    horizon_days: int
    prior_event_count: int
    effective_prior_event_weight: Decimal
    mean_prior_context_comparability: Decimal | None
    current_strength_index: Decimal
    current_attention_index: Decimal
    entry_proxy_price: Decimal | None
    current_pre_price_median: Decimal | None
    predicted_recovery_ratio: Decimal | None
    reference_sell_price: Decimal | None
    sale_proceeds: Decimal | None
    fee_amount: Decimal | None
    net_profit: Decimal | None
    net_roi: Decimal | None
    research_signal_positive: bool
    explanation_codes: tuple[str, ...]
    research_only: bool = True
    price_proxy_semantics: str = PRICE_PROXY_SEMANTICS
    fill_claim: bool = False
    version: str = POST_RERELEASE_WALK_FORWARD_VERSION


@dataclass(frozen=True, slots=True)
class PostReReleaseOutcome:
    event_key: str
    horizon_days: int
    evaluation_as_of: datetime
    target_at: datetime
    observed_at: datetime | None
    exit_proxy_price: Decimal | None
    sale_proceeds: Decimal | None
    realized_net_profit: Decimal | None
    realized_net_roi: Decimal | None
    target_available: bool
    price_proxy_semantics: str = PRICE_PROXY_SEMANTICS
    fill_claim: bool = False


@dataclass(frozen=True, slots=True)
class PostReReleaseWalkForwardExample:
    estimate: PostReReleaseReferenceEstimate
    outcome: PostReReleaseOutcome


def estimate_post_rerelease_reference(
    *,
    event: SupplyPoolEvent,
    prediction_as_of: datetime,
    horizon_days: int,
    entry_proxy_price: Decimal | None,
    current_recovery: SupplyEventRecovery,
    prior_recoveries: Iterable[SupplyEventRecovery],
    current_context: ResolvedManualMarketContext | None = None,
    prior_context_by_event: Mapping[str, ResolvedManualMarketContext] | None = None,
    config: PostReReleaseConfig | None = None,
) -> PostReReleaseReferenceEstimate:
    """Estimate a post-return exit reference using prior events only.

    This is intentionally a research estimator, not an execution model.  The
    current event's future recovery checkpoint never enters the prediction.
    Optional operator context changes only the comparability weight assigned to
    prior recovery examples; it never multiplies a market price directly.
    """

    cfg = config or PostReReleaseConfig()
    prediction_as_of = _utc(prediction_as_of, "prediction_as_of")
    if horizon_days <= 0:
        raise PostReReleaseError("horizon_days must be positive.")
    _validate_price(entry_proxy_price, "entry_proxy_price")
    if current_recovery.event_key != event.event_key:
        raise PostReReleaseError("current_recovery event_key does not match event.")
    if current_recovery.as_of > prediction_as_of:
        raise PostReReleaseError("current_recovery contains information after prediction_as_of.")
    current_pre_price_median = current_recovery.pre_price_median
    _validate_price(current_pre_price_median, "current_pre_price_median")

    prior = tuple(
        recovery
        for recovery in prior_recoveries
        if recovery.family == event.family
        and recovery.event_key != event.event_key
        and recovery.event_closes_at <= event.opens_at
        and recovery.event_completed
        and recovery.profile_eligible
        and recovery.as_of <= prediction_as_of
    )
    ratio_points = tuple(
        (recovery.event_key, checkpoint.pre_price_ratio)
        for recovery in prior
        for checkpoint in recovery.checkpoints
        if checkpoint.horizon_days == horizon_days
        and checkpoint.available
        and checkpoint.pre_price_ratio is not None
        and checkpoint.observed_at is not None
        and checkpoint.observed_at <= event.opens_at
    )
    context_map = prior_context_by_event or {}
    current_strength = (
        current_context.strength_index if current_context is not None else Decimal("1")
    )
    current_attention = (
        current_context.attention_index if current_context is not None else Decimal("1")
    )
    weighted_ratios: list[tuple[Decimal, Decimal]] = []
    context_weights: list[Decimal] = []
    for event_key, ratio in ratio_points:
        weight = _context_weight(
            current_context=current_context,
            prior_context=context_map.get(event_key),
        )
        weighted_ratios.append((ratio, weight))
        context_weights.append(weight)
    effective_prior_weight = sum(context_weights, Decimal("0"))
    mean_context_comparability = (
        effective_prior_weight / Decimal(len(context_weights))
        if context_weights
        else None
    )
    codes: list[str] = []
    if not current_recovery.profile_eligible:
        codes.append("current_pre_event_baseline_ineligible")
        return _empty_estimate(
            event=event,
            prediction_as_of=prediction_as_of,
            horizon_days=horizon_days,
            entry_proxy_price=entry_proxy_price,
            current_pre_price_median=current_pre_price_median,
            prior_event_count=len(ratio_points),
            effective_prior_event_weight=effective_prior_weight,
            mean_prior_context_comparability=mean_context_comparability,
            current_strength_index=current_strength,
            current_attention_index=current_attention,
            codes=tuple(codes),
        )
    if len(ratio_points) < cfg.minimum_prior_events:
        codes.append("insufficient_prior_return_recovery_history")
        return _empty_estimate(
            event=event,
            prediction_as_of=prediction_as_of,
            horizon_days=horizon_days,
            entry_proxy_price=entry_proxy_price,
            current_pre_price_median=current_pre_price_median,
            prior_event_count=len(ratio_points),
            effective_prior_event_weight=effective_prior_weight,
            mean_prior_context_comparability=mean_context_comparability,
            current_strength_index=current_strength,
            current_attention_index=current_attention,
            codes=tuple(codes),
        )
    if effective_prior_weight < Decimal(cfg.minimum_prior_events):
        codes.append("insufficient_context_comparable_prior_return_history")
        return _empty_estimate(
            event=event,
            prediction_as_of=prediction_as_of,
            horizon_days=horizon_days,
            entry_proxy_price=entry_proxy_price,
            current_pre_price_median=current_pre_price_median,
            prior_event_count=len(ratio_points),
            effective_prior_event_weight=effective_prior_weight,
            mean_prior_context_comparability=mean_context_comparability,
            current_strength_index=current_strength,
            current_attention_index=current_attention,
            codes=tuple(codes),
        )
    if entry_proxy_price is None:
        codes.append("entry_trade_proxy_unavailable")
        return _empty_estimate(
            event=event,
            prediction_as_of=prediction_as_of,
            horizon_days=horizon_days,
            entry_proxy_price=None,
            current_pre_price_median=current_pre_price_median,
            prior_event_count=len(ratio_points),
            effective_prior_event_weight=effective_prior_weight,
            mean_prior_context_comparability=mean_context_comparability,
            current_strength_index=current_strength,
            current_attention_index=current_attention,
            codes=tuple(codes),
        )
    if current_pre_price_median is None:
        codes.append("current_pre_event_baseline_unavailable")
        return _empty_estimate(
            event=event,
            prediction_as_of=prediction_as_of,
            horizon_days=horizon_days,
            entry_proxy_price=entry_proxy_price,
            current_pre_price_median=None,
            prior_event_count=len(ratio_points),
            effective_prior_event_weight=effective_prior_weight,
            mean_prior_context_comparability=mean_context_comparability,
            current_strength_index=current_strength,
            current_attention_index=current_attention,
            codes=tuple(codes),
        )

    predicted_ratio = weighted_decimal_median(weighted_ratios)
    assert predicted_ratio is not None
    reference = current_pre_price_median * predicted_ratio
    proceeds = sale_proceeds(reference, cfg.fee_rate, cfg.currency_quantum)
    fee_amount = reference - proceeds
    net_profit = proceeds - entry_proxy_price
    net_roi = net_profit / entry_proxy_price
    research_signal_positive = net_profit > Decimal("0")
    codes.extend((
        "prior_return_recovery_reference",
        (
            "manual_context_weighted_prior_recovery"
            if any(weight < Decimal("1") for weight in context_weights)
            else "manual_context_neutral_prior_recovery"
        ),
        (
            "fee_adjusted_proxy_positive"
            if research_signal_positive
            else "fee_adjusted_proxy_non_positive"
        ),
        "research_only_not_execution_or_fill",
    ))
    return PostReReleaseReferenceEstimate(
        event_key=event.event_key,
        family=event.family,
        prediction_as_of=prediction_as_of,
        horizon_days=horizon_days,
        prior_event_count=len(ratio_points),
        effective_prior_event_weight=effective_prior_weight,
        mean_prior_context_comparability=mean_context_comparability,
        current_strength_index=current_strength,
        current_attention_index=current_attention,
        entry_proxy_price=entry_proxy_price,
        current_pre_price_median=current_pre_price_median,
        predicted_recovery_ratio=predicted_ratio,
        reference_sell_price=reference,
        sale_proceeds=proceeds,
        fee_amount=fee_amount,
        net_profit=net_profit,
        net_roi=net_roi,
        research_signal_positive=research_signal_positive,
        explanation_codes=tuple(codes),
    )


def evaluate_post_rerelease_outcome(
    *,
    estimate: PostReReleaseReferenceEstimate,
    event: SupplyPoolEvent,
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    evaluation_as_of: datetime,
    config: PostReReleaseConfig | None = None,
    next_same_family_event_opens_at: datetime | None = None,
) -> PostReReleaseOutcome:
    """Attach a future outcome for evaluation without mutating the prediction."""

    cfg = config or PostReReleaseConfig()
    evaluation_as_of = _utc(evaluation_as_of, "evaluation_as_of")
    if estimate.event_key != event.event_key:
        raise PostReReleaseError("estimate event_key does not match event.")
    target_at = event.closes_at + timedelta(days=estimate.horizon_days)
    next_open = (
        _utc(next_same_family_event_opens_at, "next_same_family_event_opens_at")
        if next_same_family_event_opens_at is not None
        else None
    )
    cutoff = min(evaluation_as_of, next_open) if next_open is not None else evaluation_as_of
    exit_point: DailyHistoricalTradeEvidence | None = None
    if evaluation_as_of > event.closes_at:
        evidence = select_historical_trade_evidence(
            historical_buckets,
            as_of=evaluation_as_of,
            window_start=event.closes_at,
        )
        daily = aggregate_trade_evidence_by_utc_day(evidence.buckets)
        exit_point = next(
            (
                point
                for point in daily
                if point.day_start_utc >= target_at
                and point.day_start_utc + timedelta(days=1) <= cutoff
            ),
            None,
        )

    price = exit_point.vwap_price if exit_point is not None else None
    proceeds = (
        sale_proceeds(price, cfg.fee_rate, cfg.currency_quantum)
        if price is not None
        else None
    )
    profit = (
        proceeds - estimate.entry_proxy_price
        if proceeds is not None and estimate.entry_proxy_price is not None
        else None
    )
    roi = (
        profit / estimate.entry_proxy_price
        if profit is not None and estimate.entry_proxy_price is not None
        else None
    )
    return PostReReleaseOutcome(
        event_key=event.event_key,
        horizon_days=estimate.horizon_days,
        evaluation_as_of=evaluation_as_of,
        target_at=target_at,
        observed_at=(
            exit_point.day_start_utc + timedelta(days=1)
            if exit_point is not None
            else None
        ),
        exit_proxy_price=price,
        sale_proceeds=proceeds,
        realized_net_profit=profit,
        realized_net_roi=roi,
        target_available=price is not None,
    )


def first_post_event_trade_proxy(
    *,
    event: SupplyPoolEvent,
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    as_of: datetime,
) -> tuple[datetime, Decimal] | None:
    """Return the first completed UTC-day trade VWAP after an event closes."""

    as_of = _utc(as_of, "as_of")
    if as_of <= event.closes_at:
        return None
    evidence = select_historical_trade_evidence(
        historical_buckets,
        as_of=as_of,
        window_start=event.closes_at,
    )
    daily = aggregate_trade_evidence_by_utc_day(evidence.buckets)
    point = next(
        (
            value
            for value in daily
            if value.day_start_utc >= event.closes_at
            and value.day_start_utc + timedelta(days=1) <= as_of
        ),
        None,
    )
    if point is None:
        return None
    return point.day_start_utc + timedelta(days=1), point.vwap_price


def sale_proceeds(price: Decimal, fee_rate: Decimal, quantum: Decimal) -> Decimal:
    _validate_price(price, "price")
    if (
        not isinstance(fee_rate, Decimal)
        or not fee_rate.is_finite()
        or not Decimal("0") <= fee_rate < Decimal("1")
    ):
        raise PostReReleaseError("fee_rate must be a finite Decimal in [0, 1).")
    if (
        not isinstance(quantum, Decimal)
        or not quantum.is_finite()
        or quantum <= Decimal("0")
    ):
        raise PostReReleaseError("quantum must be a positive finite Decimal.")
    raw = price * (Decimal("1") - fee_rate)
    units = (raw / quantum).to_integral_value(rounding=ROUND_DOWN)
    return units * quantum


def _empty_estimate(
    *,
    event: SupplyPoolEvent,
    prediction_as_of: datetime,
    horizon_days: int,
    entry_proxy_price: Decimal | None,
    current_pre_price_median: Decimal | None,
    prior_event_count: int,
    effective_prior_event_weight: Decimal,
    mean_prior_context_comparability: Decimal | None,
    current_strength_index: Decimal,
    current_attention_index: Decimal,
    codes: tuple[str, ...],
) -> PostReReleaseReferenceEstimate:
    return PostReReleaseReferenceEstimate(
        event_key=event.event_key,
        family=event.family,
        prediction_as_of=prediction_as_of,
        horizon_days=horizon_days,
        prior_event_count=prior_event_count,
        effective_prior_event_weight=effective_prior_event_weight,
        mean_prior_context_comparability=mean_prior_context_comparability,
        current_strength_index=current_strength_index,
        current_attention_index=current_attention_index,
        entry_proxy_price=entry_proxy_price,
        current_pre_price_median=current_pre_price_median,
        predicted_recovery_ratio=None,
        reference_sell_price=None,
        sale_proceeds=None,
        fee_amount=None,
        net_profit=None,
        net_roi=None,
        research_signal_positive=False,
        explanation_codes=codes,
    )


def _context_weight(
    *,
    current_context: ResolvedManualMarketContext | None,
    prior_context: ResolvedManualMarketContext | None,
) -> Decimal:
    if current_context is None:
        if prior_context is None:
            return Decimal("1")
        return (
            min(Decimal("1"), prior_context.strength_index)
            / max(Decimal("1"), prior_context.strength_index)
            * min(Decimal("1"), prior_context.attention_index)
            / max(Decimal("1"), prior_context.attention_index)
        )
    if prior_context is None:
        return (
            min(Decimal("1"), current_context.strength_index)
            / max(Decimal("1"), current_context.strength_index)
            * min(Decimal("1"), current_context.attention_index)
            / max(Decimal("1"), current_context.attention_index)
        )
    return context_comparability(current_context, prior_context).combined_similarity


def _validate_price(value: Decimal | None, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0"):
        raise PostReReleaseError(f"{field_name} must be a positive finite Decimal or None.")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PostReReleaseError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
