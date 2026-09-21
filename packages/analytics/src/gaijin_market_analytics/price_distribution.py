from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Iterable, Literal

from .hierarchical_recovery import HistoricalReturnOutcomeEvidence
from .post_rerelease import sale_proceeds
from .supply_cycle_recovery import SupplyEventRecovery


PRICE_DISTRIBUTION_VERSION = "1.0.0"

RatioBasis = Literal["PRE_EVENT_MEDIAN", "ENTRY_PROXY"]
RatioSource = Literal["ITEM_LOCAL", "FAMILY_CROSS_ITEM"]
DistributionStatus = Literal[
    "AVAILABLE",
    "ANCHOR_UNAVAILABLE",
    "ENTRY_UNAVAILABLE",
    "INSUFFICIENT_SAMPLE",
    "INSUFFICIENT_EFFECTIVE_SAMPLE",
    "MIXED_RATIO_BASIS",
]


class PriceDistributionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PriceRatioObservation:
    """One already-observed recovery ratio usable as a forecast sample.

    ``ratio`` is expressed against ``basis``: either the source event's own
    pre-event price median or the entry proxy price paid after that event. The
    two are not interchangeable, so a forecast refuses to mix them.
    """

    source_item_id: int
    source_event_key: str
    horizon_days: int
    ratio: Decimal
    weight: Decimal
    observed_at: datetime
    basis: RatioBasis
    source: RatioSource

    def __post_init__(self) -> None:
        if self.source_item_id <= 0:
            raise PriceDistributionError("source_item_id must be positive.")
        if not self.source_event_key.strip():
            raise PriceDistributionError("source_event_key must be non-empty.")
        if isinstance(self.horizon_days, bool) or not isinstance(self.horizon_days, int):
            raise PriceDistributionError("horizon_days must be an integer.")
        if self.horizon_days <= 0:
            raise PriceDistributionError("horizon_days must be positive.")
        for name in ("ratio", "weight"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0"):
                raise PriceDistributionError(f"{name} must be a positive finite Decimal.")
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        if self.basis not in {"PRE_EVENT_MEDIAN", "ENTRY_PROXY"}:
            raise PriceDistributionError("unsupported ratio basis.")
        if self.source not in {"ITEM_LOCAL", "FAMILY_CROSS_ITEM"}:
            raise PriceDistributionError("unsupported ratio source.")


@dataclass(frozen=True, slots=True)
class PriceDistributionConfig:
    quantile_probabilities: tuple[Decimal, ...] = (
        Decimal("0.10"),
        Decimal("0.25"),
        Decimal("0.50"),
        Decimal("0.75"),
        Decimal("0.90"),
    )
    minimum_sample_count: int = 2
    minimum_effective_sample_size: Decimal = Decimal("2")
    loss_tail_probability: Decimal = Decimal("0.25")
    fee_rate: Decimal = Decimal("0.15")
    currency_quantum: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        if not self.quantile_probabilities:
            raise PriceDistributionError("quantile_probabilities must be non-empty.")
        if any(
            not isinstance(value, Decimal)
            or not value.is_finite()
            or not Decimal("0") < value < Decimal("1")
            for value in self.quantile_probabilities
        ):
            raise PriceDistributionError(
                "quantile probabilities must be finite Decimals in (0, 1)."
            )
        if tuple(sorted(set(self.quantile_probabilities))) != self.quantile_probabilities:
            raise PriceDistributionError(
                "quantile_probabilities must be strictly increasing and unique."
            )
        if isinstance(self.minimum_sample_count, bool) or not isinstance(
            self.minimum_sample_count, int
        ):
            raise PriceDistributionError("minimum_sample_count must be an integer.")
        if self.minimum_sample_count < 2:
            raise PriceDistributionError(
                "minimum_sample_count must be at least 2; one observation is not a distribution."
            )
        if (
            not isinstance(self.minimum_effective_sample_size, Decimal)
            or not self.minimum_effective_sample_size.is_finite()
            or self.minimum_effective_sample_size < Decimal("1")
        ):
            raise PriceDistributionError(
                "minimum_effective_sample_size must be a finite Decimal of at least 1."
            )
        if (
            not isinstance(self.loss_tail_probability, Decimal)
            or not self.loss_tail_probability.is_finite()
            or not Decimal("0") < self.loss_tail_probability <= Decimal("1")
        ):
            raise PriceDistributionError("loss_tail_probability must be in (0, 1].")
        if (
            not isinstance(self.fee_rate, Decimal)
            or not self.fee_rate.is_finite()
            or not Decimal("0") <= self.fee_rate < Decimal("1")
        ):
            raise PriceDistributionError("fee_rate must be a finite Decimal in [0, 1).")
        if (
            not isinstance(self.currency_quantum, Decimal)
            or not self.currency_quantum.is_finite()
            or self.currency_quantum <= Decimal("0")
        ):
            raise PriceDistributionError("currency_quantum must be a positive Decimal.")


@dataclass(frozen=True, slots=True)
class PredictiveRatioAtom:
    """One atom of the weighted empirical predictive distribution."""

    ratio: Decimal
    weight_share: Decimal
    cumulative_weight_share: Decimal


@dataclass(frozen=True, slots=True)
class PriceQuantile:
    probability: Decimal
    supported: bool
    ratio: Decimal
    price: Decimal
    sale_proceeds: Decimal
    net_profit: Decimal
    net_roi: Decimal


@dataclass(frozen=True, slots=True)
class PriceDistributionForecast:
    item_id: int
    event_key: str
    family: str
    prediction_as_of: datetime
    horizon_days: int
    basis: RatioBasis
    status: DistributionStatus
    available: bool
    anchor_price: Decimal | None
    entry_proxy_price: Decimal | None
    sample_count: int
    total_weight: Decimal
    effective_sample_size: Decimal
    supported_tail_probability: Decimal | None
    distinct_source_item_count: int
    distinct_source_event_count: int
    item_local_sample_count: int
    family_sample_count: int
    support: tuple[PredictiveRatioAtom, ...]
    quantiles: tuple[PriceQuantile, ...]
    probability_net_profit_positive: Decimal | None
    expected_net_roi: Decimal | None
    median_net_roi: Decimal | None
    loss_tail_probability: Decimal
    expected_shortfall_net_roi: Decimal | None
    explanation_codes: tuple[str, ...]
    research_only: bool = True
    fill_claim: bool = False
    version: str = PRICE_DISTRIBUTION_VERSION


@dataclass(frozen=True, slots=True)
class QuantilePinballLoss:
    probability: Decimal
    supported: bool
    predicted_net_roi: Decimal
    pinball_loss: Decimal


@dataclass(frozen=True, slots=True)
class CentralIntervalCoverage:
    nominal_coverage: Decimal
    lower_probability: Decimal
    upper_probability: Decimal
    supported: bool
    lower_net_roi: Decimal
    upper_net_roi: Decimal
    covered: bool


@dataclass(frozen=True, slots=True)
class PriceDistributionOutcome:
    """Scoring of one forecast against one realized exit observation.

    Every score here is a proper scoring rule or a calibration diagnostic, so a
    later model can be compared against the point-forecast baseline on the same
    terms instead of on a single hit/miss flag.
    """

    item_id: int
    event_key: str
    horizon_days: int
    prediction_as_of: datetime
    outcome_observed_at: datetime
    realized_exit_price: Decimal
    realized_ratio: Decimal
    realized_sale_proceeds: Decimal
    realized_net_roi: Decimal
    realized_profitable: bool
    probability_integral_transform: Decimal
    brier_score: Decimal | None
    pinball_losses: tuple[QuantilePinballLoss, ...]
    mean_supported_pinball_loss: Decimal | None
    interval_coverages: tuple[CentralIntervalCoverage, ...]
    explanation_codes: tuple[str, ...]
    version: str = PRICE_DISTRIBUTION_VERSION


def forecast_price_distribution(
    *,
    item_id: int,
    event_key: str,
    family: str,
    prediction_as_of: datetime,
    horizon_days: int,
    basis: RatioBasis,
    anchor_price: Decimal | None,
    entry_proxy_price: Decimal | None,
    observations: Iterable[PriceRatioObservation],
    config: PriceDistributionConfig | None = None,
) -> PriceDistributionForecast:
    """Turn observed recovery ratios into a weighted empirical exit distribution.

    The forecast deliberately reports what a small sample can support and no
    more. Quantiles are read off the weighted inverse CDF without interpolating
    between observations, because interpolation would invent resolution that two
    or three prior events do not contain. Each quantile level is additionally
    marked ``supported`` only when it lies inside ``[1/(ESS+1), ESS/(ESS+1)]``,
    the range an effective sample of that size can actually resolve.

    ``probability_net_profit_positive`` is the weighted share of sample ratios
    whose implied sale proceeds clear the entry price after fees. It is an
    empirical frequency over prior events, not a claim about execution.
    """

    cfg = config or PriceDistributionConfig()
    if item_id <= 0:
        raise PriceDistributionError("item_id must be positive.")
    if not event_key.strip() or not family.strip():
        raise PriceDistributionError("event_key and family must be non-empty.")
    prediction_as_of = _utc(prediction_as_of, "prediction_as_of")
    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int):
        raise PriceDistributionError("horizon_days must be an integer.")
    if horizon_days <= 0:
        raise PriceDistributionError("horizon_days must be positive.")
    if basis not in {"PRE_EVENT_MEDIAN", "ENTRY_PROXY"}:
        raise PriceDistributionError("unsupported ratio basis.")
    _validate_price(anchor_price, "anchor_price")
    _validate_price(entry_proxy_price, "entry_proxy_price")
    if (
        basis == "ENTRY_PROXY"
        and anchor_price is not None
        and entry_proxy_price is not None
        and anchor_price != entry_proxy_price
    ):
        raise PriceDistributionError(
            "ENTRY_PROXY ratios must be anchored on the entry proxy price."
        )

    samples = tuple(observations)
    for sample in samples:
        if sample.observed_at > prediction_as_of:
            raise PriceDistributionError(
                "price ratio observation must not be observed after prediction_as_of."
            )
        if sample.horizon_days != horizon_days:
            raise PriceDistributionError("price ratio observation horizon mismatch.")
    codes: list[str] = ["price_distribution_research_only_not_execution_or_fill"]
    mixed_basis = {sample.basis for sample in samples} - {basis}
    if mixed_basis:
        codes.append("price_distribution_ratio_basis_mismatch")
        return _empty_forecast(
            item_id=item_id,
            event_key=event_key,
            family=family,
            prediction_as_of=prediction_as_of,
            horizon_days=horizon_days,
            basis=basis,
            status="MIXED_RATIO_BASIS",
            anchor_price=anchor_price,
            entry_proxy_price=entry_proxy_price,
            samples=samples,
            cfg=cfg,
            codes=tuple(codes),
        )

    total_weight = sum((sample.weight for sample in samples), Decimal("0"))
    weight_square_sum = sum(
        (sample.weight * sample.weight for sample in samples), Decimal("0")
    )
    effective_sample_size = (
        total_weight * total_weight / weight_square_sum
        if weight_square_sum > Decimal("0")
        else Decimal("0")
    )

    status: DistributionStatus = "AVAILABLE"
    if len(samples) < cfg.minimum_sample_count:
        codes.append("insufficient_price_distribution_sample")
        status = "INSUFFICIENT_SAMPLE"
    elif effective_sample_size < cfg.minimum_effective_sample_size:
        codes.append("insufficient_effective_price_distribution_sample")
        status = "INSUFFICIENT_EFFECTIVE_SAMPLE"
    elif anchor_price is None:
        codes.append("price_distribution_anchor_unavailable")
        status = "ANCHOR_UNAVAILABLE"
    elif entry_proxy_price is None:
        codes.append("price_distribution_entry_proxy_unavailable")
        status = "ENTRY_UNAVAILABLE"

    if status != "AVAILABLE":
        return _empty_forecast(
            item_id=item_id,
            event_key=event_key,
            family=family,
            prediction_as_of=prediction_as_of,
            horizon_days=horizon_days,
            basis=basis,
            status=status,
            anchor_price=anchor_price,
            entry_proxy_price=entry_proxy_price,
            samples=samples,
            cfg=cfg,
            codes=tuple(codes),
            total_weight=total_weight,
            effective_sample_size=effective_sample_size,
        )

    assert anchor_price is not None and entry_proxy_price is not None
    ordered = tuple(
        sorted(samples, key=lambda value: (value.ratio, value.source_item_id, value.source_event_key))
    )
    tail = _supported_tail_probability(effective_sample_size)
    quantiles = tuple(
        _quantile_at(
            probability=probability,
            ordered=ordered,
            total_weight=total_weight,
            anchor_price=anchor_price,
            entry_proxy_price=entry_proxy_price,
            supported=tail is not None and tail <= probability <= Decimal("1") - tail,
            cfg=cfg,
        )
        for probability in cfg.quantile_probabilities
    )
    roi_by_sample = tuple(
        (
            _net_roi(
                ratio=sample.ratio,
                anchor_price=anchor_price,
                entry_proxy_price=entry_proxy_price,
                cfg=cfg,
            ),
            sample.weight,
        )
        for sample in ordered
    )
    profitable_weight = sum(
        (weight for roi, weight in roi_by_sample if roi > Decimal("0")), Decimal("0")
    )
    expected_roi = (
        sum((roi * weight for roi, weight in roi_by_sample), Decimal("0")) / total_weight
    )
    median_roi = _weighted_inverse_cdf_value(
        tuple(sorted(roi_by_sample)), total_weight, Decimal("0.5")
    )
    shortfall = _weighted_expected_shortfall(
        tuple(sorted(roi_by_sample)), total_weight, cfg.loss_tail_probability
    )
    if not any(quantile.supported for quantile in quantiles):
        codes.append("no_quantile_level_supported_by_effective_sample")
    if any(not quantile.supported for quantile in quantiles):
        codes.append("price_distribution_tail_quantiles_unsupported")
    codes.append(f"price_distribution_basis_{basis.lower()}")
    codes.append("price_distribution_weighted_empirical_inverse_cdf")

    return PriceDistributionForecast(
        item_id=item_id,
        event_key=event_key,
        family=family,
        prediction_as_of=prediction_as_of,
        horizon_days=horizon_days,
        basis=basis,
        status=status,
        available=True,
        anchor_price=anchor_price,
        entry_proxy_price=entry_proxy_price,
        sample_count=len(ordered),
        total_weight=total_weight,
        effective_sample_size=effective_sample_size,
        supported_tail_probability=tail,
        distinct_source_item_count=len({value.source_item_id for value in ordered}),
        distinct_source_event_count=len({value.source_event_key for value in ordered}),
        item_local_sample_count=sum(value.source == "ITEM_LOCAL" for value in ordered),
        family_sample_count=sum(value.source == "FAMILY_CROSS_ITEM" for value in ordered),
        support=_support_atoms(ordered, total_weight),
        quantiles=quantiles,
        probability_net_profit_positive=profitable_weight / total_weight,
        expected_net_roi=expected_roi,
        median_net_roi=median_roi,
        loss_tail_probability=cfg.loss_tail_probability,
        expected_shortfall_net_roi=shortfall,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def evaluate_price_distribution_outcome(
    *,
    forecast: PriceDistributionForecast,
    realized_exit_price: Decimal,
    outcome_observed_at: datetime,
    config: PriceDistributionConfig | None = None,
) -> PriceDistributionOutcome:
    """Score a forecast against one realized exit price.

    The forecast carries its own predictive support, so the probability integral
    transform is computed against the exact distribution that was published
    rather than a re-derived one. Scoring never mutates the forecast and is only
    ever called after the outcome became observable.
    """

    cfg = config or PriceDistributionConfig()
    if not forecast.available:
        raise PriceDistributionError("cannot score an unavailable forecast.")
    outcome_observed_at = _utc(outcome_observed_at, "outcome_observed_at")
    if outcome_observed_at <= forecast.prediction_as_of:
        raise PriceDistributionError(
            "outcome_observed_at must be later than prediction_as_of."
        )
    if realized_exit_price is None:
        raise PriceDistributionError("realized_exit_price is required.")
    _validate_price(realized_exit_price, "realized_exit_price")
    assert forecast.anchor_price is not None and forecast.entry_proxy_price is not None

    realized_ratio = realized_exit_price / forecast.anchor_price
    proceeds = sale_proceeds(realized_exit_price, cfg.fee_rate, cfg.currency_quantum)
    realized_roi = (proceeds - forecast.entry_proxy_price) / forecast.entry_proxy_price
    realized_profitable = proceeds > forecast.entry_proxy_price

    below = sum(
        (atom.weight_share for atom in forecast.support if atom.ratio < realized_ratio),
        Decimal("0"),
    )
    equal = sum(
        (atom.weight_share for atom in forecast.support if atom.ratio == realized_ratio),
        Decimal("0"),
    )
    # Mid-PIT keeps the transform deterministic on a discrete predictive
    # distribution; a plain CDF would be biased upward at observed atoms.
    pit = below + equal / Decimal("2")

    brier = (
        (forecast.probability_net_profit_positive - _indicator(realized_profitable)) ** 2
        if forecast.probability_net_profit_positive is not None
        else None
    )
    pinball = tuple(
        QuantilePinballLoss(
            probability=quantile.probability,
            supported=quantile.supported,
            predicted_net_roi=quantile.net_roi,
            pinball_loss=_pinball(quantile.probability, quantile.net_roi, realized_roi),
        )
        for quantile in forecast.quantiles
    )
    supported_losses = tuple(
        value.pinball_loss for value in pinball if value.supported
    )
    mean_supported = (
        sum(supported_losses, Decimal("0")) / Decimal(len(supported_losses))
        if supported_losses
        else None
    )
    coverages = tuple(
        _coverage(lower, upper, realized_roi)
        for lower, upper in _central_pairs(forecast.quantiles)
    )
    codes = [
        "price_distribution_outcome_research_only",
        (
            "realized_exit_profitable_after_fee"
            if realized_profitable
            else "realized_exit_not_profitable_after_fee"
        ),
    ]
    if not supported_losses:
        codes.append("no_supported_quantile_to_score")
    return PriceDistributionOutcome(
        item_id=forecast.item_id,
        event_key=forecast.event_key,
        horizon_days=forecast.horizon_days,
        prediction_as_of=forecast.prediction_as_of,
        outcome_observed_at=outcome_observed_at,
        realized_exit_price=realized_exit_price,
        realized_ratio=realized_ratio,
        realized_sale_proceeds=proceeds,
        realized_net_roi=realized_roi,
        realized_profitable=realized_profitable,
        probability_integral_transform=pit,
        brier_score=brier,
        pinball_losses=pinball,
        mean_supported_pinball_loss=mean_supported,
        interval_coverages=coverages,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def item_local_ratio_observations(
    *,
    item_id: int,
    target_event_key: str,
    family: str,
    horizon_days: int,
    prediction_as_of: datetime,
    visible_before: datetime,
    recoveries: Iterable[SupplyEventRecovery],
    weight_by_event_key: dict[str, Decimal] | None = None,
) -> tuple[PriceRatioObservation, ...]:
    """Extract the item's own prior same-family recovery ratios.

    The filter mirrors ``estimate_post_rerelease_reference`` exactly: same
    family, a different completed and profile-eligible event, and a checkpoint
    that had already been observed before ``visible_before`` (normally the
    current event's opening time).
    """

    if item_id <= 0:
        raise PriceDistributionError("item_id must be positive.")
    prediction_as_of = _utc(prediction_as_of, "prediction_as_of")
    visible_before = _utc(visible_before, "visible_before")
    weights = weight_by_event_key or {}
    result: list[PriceRatioObservation] = []
    for recovery in recoveries:
        if recovery.family != family or recovery.event_key == target_event_key:
            continue
        if not (recovery.event_completed and recovery.profile_eligible):
            continue
        if recovery.as_of > prediction_as_of:
            continue
        for checkpoint in recovery.checkpoints:
            if checkpoint.horizon_days != horizon_days or not checkpoint.available:
                continue
            if checkpoint.pre_price_ratio is None or checkpoint.observed_at is None:
                continue
            if checkpoint.observed_at > visible_before:
                continue
            result.append(
                PriceRatioObservation(
                    source_item_id=item_id,
                    source_event_key=recovery.event_key,
                    horizon_days=horizon_days,
                    ratio=checkpoint.pre_price_ratio,
                    weight=weights.get(recovery.event_key, Decimal("1")),
                    observed_at=checkpoint.observed_at,
                    basis="PRE_EVENT_MEDIAN",
                    source="ITEM_LOCAL",
                )
            )
    return tuple(
        sorted(result, key=lambda value: (value.observed_at, value.source_event_key))
    )


def family_ratio_observations(
    *,
    family: str,
    horizon_days: int,
    prediction_as_of: datetime,
    exclude_item_id: int,
    prior_outcomes: Iterable[HistoricalReturnOutcomeEvidence],
    weight: Decimal = Decimal("1"),
) -> tuple[PriceRatioObservation, ...]:
    """Extract independent cross-item family ratios against the entry proxy.

    All outcomes from ``exclude_item_id`` are dropped, matching the family-prior
    independence rule, so the target vehicle cannot be counted once as its own
    history and again as supposedly independent family corroboration.
    """

    prediction_as_of = _utc(prediction_as_of, "prediction_as_of")
    if exclude_item_id <= 0:
        raise PriceDistributionError("exclude_item_id must be positive.")
    result = tuple(
        PriceRatioObservation(
            source_item_id=value.item_id,
            source_event_key=value.event_key,
            horizon_days=horizon_days,
            ratio=value.gross_exit_entry_ratio,
            weight=weight,
            observed_at=value.outcome_observed_at,
            basis="ENTRY_PROXY",
            source="FAMILY_CROSS_ITEM",
        )
        for value in prior_outcomes
        if value.family == family
        and value.horizon_days == horizon_days
        and value.outcome_observed_at <= prediction_as_of
        and value.item_id != exclude_item_id
    )
    return tuple(
        sorted(
            result,
            key=lambda value: (
                value.observed_at,
                value.source_item_id,
                value.source_event_key,
            ),
        )
    )


def _support_atoms(
    ordered: tuple[PriceRatioObservation, ...],
    total_weight: Decimal,
) -> tuple[PredictiveRatioAtom, ...]:
    """Collapse equal ratios into atoms of the normalized predictive measure."""

    merged: list[tuple[Decimal, Decimal]] = []
    for sample in ordered:
        if merged and merged[-1][0] == sample.ratio:
            merged[-1] = (sample.ratio, merged[-1][1] + sample.weight)
        else:
            merged.append((sample.ratio, sample.weight))
    atoms: list[PredictiveRatioAtom] = []
    cumulative = Decimal("0")
    for ratio, weight in merged:
        share = weight / total_weight
        cumulative += share
        atoms.append(
            PredictiveRatioAtom(
                ratio=ratio,
                weight_share=share,
                cumulative_weight_share=cumulative,
            )
        )
    return tuple(atoms)


def _supported_tail_probability(effective_sample_size: Decimal) -> Decimal | None:
    if effective_sample_size <= Decimal("0"):
        return None
    return Decimal("1") / (effective_sample_size + Decimal("1"))


def _quantile_at(
    *,
    probability: Decimal,
    ordered: tuple[PriceRatioObservation, ...],
    total_weight: Decimal,
    anchor_price: Decimal,
    entry_proxy_price: Decimal,
    supported: bool,
    cfg: PriceDistributionConfig,
) -> PriceQuantile:
    target = probability * total_weight
    cumulative = Decimal("0")
    chosen = ordered[-1]
    for sample in ordered:
        cumulative += sample.weight
        if cumulative >= target:
            chosen = sample
            break
    price = anchor_price * chosen.ratio
    proceeds = sale_proceeds(price, cfg.fee_rate, cfg.currency_quantum)
    profit = proceeds - entry_proxy_price
    return PriceQuantile(
        probability=probability,
        supported=supported,
        ratio=chosen.ratio,
        price=price,
        sale_proceeds=proceeds,
        net_profit=profit,
        net_roi=profit / entry_proxy_price,
    )


def _net_roi(
    *,
    ratio: Decimal,
    anchor_price: Decimal,
    entry_proxy_price: Decimal,
    cfg: PriceDistributionConfig,
) -> Decimal:
    proceeds = sale_proceeds(anchor_price * ratio, cfg.fee_rate, cfg.currency_quantum)
    return (proceeds - entry_proxy_price) / entry_proxy_price


def _weighted_inverse_cdf_value(
    ordered: tuple[tuple[Decimal, Decimal], ...],
    total_weight: Decimal,
    probability: Decimal,
) -> Decimal | None:
    if not ordered or total_weight <= Decimal("0"):
        return None
    target = probability * total_weight
    cumulative = Decimal("0")
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0]


def _weighted_expected_shortfall(
    ordered: tuple[tuple[Decimal, Decimal], ...],
    total_weight: Decimal,
    tail_probability: Decimal,
) -> Decimal | None:
    """Weighted mean of the worst ``tail_probability`` share of the outcomes.

    The boundary observation contributes only its partial weight, so the result
    is a true tail expectation rather than a rounded-up average of whole
    samples.
    """

    if not ordered or total_weight <= Decimal("0"):
        return None
    budget = tail_probability * total_weight
    if budget <= Decimal("0"):
        return None
    accumulated = Decimal("0")
    weighted_sum = Decimal("0")
    for value, weight in ordered:
        take = min(weight, budget - accumulated)
        if take <= Decimal("0"):
            break
        weighted_sum += value * take
        accumulated += take
        if accumulated >= budget:
            break
    if accumulated <= Decimal("0"):
        return None
    return weighted_sum / accumulated


def _central_pairs(
    quantiles: tuple[PriceQuantile, ...],
) -> tuple[tuple[PriceQuantile, PriceQuantile], ...]:
    by_probability = {quantile.probability: quantile for quantile in quantiles}
    pairs: list[tuple[PriceQuantile, PriceQuantile]] = []
    for quantile in quantiles:
        if quantile.probability >= Decimal("0.5"):
            continue
        upper = by_probability.get(Decimal("1") - quantile.probability)
        if upper is not None:
            pairs.append((quantile, upper))
    return tuple(pairs)


def _coverage(
    lower: PriceQuantile,
    upper: PriceQuantile,
    realized_net_roi: Decimal,
) -> CentralIntervalCoverage:
    return CentralIntervalCoverage(
        nominal_coverage=upper.probability - lower.probability,
        lower_probability=lower.probability,
        upper_probability=upper.probability,
        supported=lower.supported and upper.supported,
        lower_net_roi=lower.net_roi,
        upper_net_roi=upper.net_roi,
        covered=lower.net_roi <= realized_net_roi <= upper.net_roi,
    )


def _pinball(probability: Decimal, predicted: Decimal, realized: Decimal) -> Decimal:
    if realized >= predicted:
        return probability * (realized - predicted)
    return (Decimal("1") - probability) * (predicted - realized)


def _indicator(value: bool) -> Decimal:
    return Decimal("1") if value else Decimal("0")


def _empty_forecast(
    *,
    item_id: int,
    event_key: str,
    family: str,
    prediction_as_of: datetime,
    horizon_days: int,
    basis: RatioBasis,
    status: DistributionStatus,
    anchor_price: Decimal | None,
    entry_proxy_price: Decimal | None,
    samples: tuple[PriceRatioObservation, ...],
    cfg: PriceDistributionConfig,
    codes: tuple[str, ...],
    total_weight: Decimal = Decimal("0"),
    effective_sample_size: Decimal = Decimal("0"),
) -> PriceDistributionForecast:
    return PriceDistributionForecast(
        item_id=item_id,
        event_key=event_key,
        family=family,
        prediction_as_of=prediction_as_of,
        horizon_days=horizon_days,
        basis=basis,
        status=status,
        available=False,
        anchor_price=anchor_price,
        entry_proxy_price=entry_proxy_price,
        sample_count=len(samples),
        total_weight=total_weight,
        effective_sample_size=effective_sample_size,
        supported_tail_probability=None,
        distinct_source_item_count=len({value.source_item_id for value in samples}),
        distinct_source_event_count=len({value.source_event_key for value in samples}),
        item_local_sample_count=sum(value.source == "ITEM_LOCAL" for value in samples),
        family_sample_count=sum(value.source == "FAMILY_CROSS_ITEM" for value in samples),
        support=(),
        quantiles=(),
        probability_net_profit_positive=None,
        expected_net_roi=None,
        median_net_roi=None,
        loss_tail_probability=cfg.loss_tail_probability,
        expected_shortfall_net_roi=None,
        explanation_codes=codes,
    )


def _validate_price(value: Decimal | None, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite() or value <= Decimal("0"):
        raise PriceDistributionError(
            f"{field_name} must be a positive finite Decimal or None."
        )


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PriceDistributionError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
