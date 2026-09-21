"""Strict walk-forward calibration of the exit price distribution.

Stage 12 produced a predictive distribution that ranks well and is badly
calibrated: on the real cohort its probability integral transform came out
U-shaped and its nominal 50% interval covered none of the realized exits. That
is the signature of a distribution that is too *narrow*, not one that is
biased, and a too-narrow distribution is the one failure mode that a single
parameter can honestly repair.

This module repairs it under two disciplines that the stage 12 scoring did not
enforce.

**Point-in-time label closure.** A forecast made at ``t`` with a thirty day
horizon is not a usable calibration observation until its outcome has been
observed. Fitting at ``t'`` on a forecast whose label closes after ``t'`` reads
the future, and the leak is invisible in the resulting score because the score
improves. Every fit here therefore draws only on resolutions whose
``outcome_observed_at`` is at or before ``as_of`` minus an embargo, and reports
how many candidates each rule removed.

**One parameter, median preserving.** The correction is a power scaling of the
ratio support about its own weighted median::

    ratio  ->  median * (ratio / median) ** factor

``factor > 1`` widens, ``factor < 1`` tightens, and ``factor == 1`` is the
identity. The median is a fixed point, so a calibrated forecast makes exactly
the same central claim as the raw one and differs only in how much spread it
admits around it. Nothing here can turn a losing median into a winning one,
which is the point: calibration is not allowed to improve the trade.

The factor is chosen off a fixed grid by minimizing the Cramer-von Mises
distance between the recomputed transform values and the uniform distribution,
with ties broken toward ``1`` so that an indifferent sample leaves the forecast
alone.

The scaling centre is the published median, and the published median is read
off the inverse CDF without interpolation, which on an even-weighted support
lands on the lower of the two central atoms. Scaling is therefore *one-sided*
on a support of two atoms: it can push the upper atom up but cannot pull the
lower one down. That asymmetry is kept deliberately rather than repaired with
an interpolated centre, because an interpolated centre would move the 0.50
quantile the forecast actually published and the whole safety of this
correction rests on that quantile not moving. A support with no interior is a
sample size problem, and this module reports it as one: see
``SupportReachSummary``, which counts how many realizations the support
reaches before and after scaling.

As with every production-affecting layer in this package, the default policy is
``RAW_ONLY``: the calibration is computed and reported but changes nothing
until an out-of-time comparison says it should.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Iterable, Literal

from .price_distribution import (
    PredictiveRatioAtom,
    PriceRatioObservation,
    RatioBasis,
)


WALK_FORWARD_CALIBRATION_VERSION = "1.0.0"

DispersionCalibrationStatus = Literal[
    "INSUFFICIENT_RESOLVED_SAMPLE",
    "INSUFFICIENT_DISTINCT_ITEMS",
    "NO_DISPERSION_INFORMATION",
    "CALIBRATED",
]
RealizedSupportPosition = Literal[
    "BELOW_SUPPORT",
    "INSIDE_SUPPORT",
    "ABOVE_SUPPORT",
]
DispersionCalibrationPolicy = Literal["RAW_ONLY", "SHADOW", "APPLY"]
DISPERSION_CALIBRATION_POLICIES: tuple[DispersionCalibrationPolicy, ...] = (
    "RAW_ONLY",
    "SHADOW",
    "APPLY",
)

# The power is evaluated at more precision than any input carries and the
# result is quantized, so a factor applied twice on two machines gives the same
# bytes. Ratios are of order one, so eighteen places is far past the resolution
# of anything upstream.
_POWER_PRECISION = 50
_UNIT = Decimal("1")


class WalkForwardCalibrationError(ValueError):
    """Raised when a walk-forward calibration input is not usable."""


@dataclass(frozen=True, slots=True)
class ResolvedRatioForecast:
    """One published forecast whose outcome has since been observed.

    The support is stored as it was published rather than re-derived, because
    the question calibration answers is whether *what we said* was well spread,
    not whether a distribution we could build today would have been.

    ``outcome_observed_at`` is the instant the label closed. It is the field the
    point-in-time filter keys on; ``prediction_as_of`` is never sufficient,
    because a forecast can be old and its label still open.
    """

    item_id: int
    event_key: str
    horizon_days: int
    basis: RatioBasis
    prediction_as_of: datetime
    outcome_observed_at: datetime
    realized_ratio: Decimal
    support: tuple[PredictiveRatioAtom, ...]

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise WalkForwardCalibrationError("item_id must be positive.")
        if not self.event_key.strip():
            raise WalkForwardCalibrationError("event_key must be non-empty.")
        if isinstance(self.horizon_days, bool) or not isinstance(
            self.horizon_days, int
        ):
            raise WalkForwardCalibrationError("horizon_days must be an integer.")
        if self.horizon_days <= 0:
            raise WalkForwardCalibrationError("horizon_days must be positive.")
        if self.basis not in {"PRE_EVENT_MEDIAN", "ENTRY_PROXY"}:
            raise WalkForwardCalibrationError("unsupported ratio basis.")
        object.__setattr__(
            self, "prediction_as_of", _utc(self.prediction_as_of, "prediction_as_of")
        )
        object.__setattr__(
            self,
            "outcome_observed_at",
            _utc(self.outcome_observed_at, "outcome_observed_at"),
        )
        if self.outcome_observed_at <= self.prediction_as_of:
            raise WalkForwardCalibrationError(
                "outcome_observed_at must be later than prediction_as_of."
            )
        _positive(self.realized_ratio, "realized_ratio")
        if not self.support:
            raise WalkForwardCalibrationError("support must be non-empty.")
        previous = Decimal("0")
        for atom in self.support:
            _positive(atom.ratio, "support ratio")
            if atom.weight_share <= Decimal("0"):
                raise WalkForwardCalibrationError("support weights must be positive.")
            if atom.cumulative_weight_share <= previous:
                raise WalkForwardCalibrationError(
                    "support cumulative weights must be strictly increasing."
                )
            previous = atom.cumulative_weight_share
        total = sum((atom.weight_share for atom in self.support), Decimal("0"))
        if abs(total - _UNIT) > Decimal("0.000001"):
            raise WalkForwardCalibrationError("support weights must sum to one.")

    @property
    def degenerate(self) -> bool:
        """True when the support is a single point and carries no spread.

        Such a forecast cannot respond to a dispersion factor at all, so it is
        excluded from the fit rather than allowed to dilute it.
        """

        return len(self.support) == 1


@dataclass(frozen=True, slots=True)
class WalkForwardCalibrationConfig:
    minimum_resolved_forecasts: int = 8
    minimum_distinct_items: int = 3
    embargo_days: int = 0
    dispersion_grid_minimum: Decimal = Decimal("0.50")
    dispersion_grid_maximum: Decimal = Decimal("4.00")
    dispersion_grid_step: Decimal = Decimal("0.25")
    coverage_probabilities: tuple[Decimal, ...] = (Decimal("0.50"), Decimal("0.80"))
    ratio_quantum: Decimal = Decimal("1E-18")
    # A mid-transform of exactly one half is arithmetically common -- three
    # equally weighted atoms with the realization on the middle one give
    # 1/3 + 1/6 -- but a weight share of 1/3 is not representable, so the value
    # arrives a rounding step below the centre and would be counted as a
    # downward miss. The tolerance is twenty places out, far past anything a
    # dozen events can resolve, so it can absorb representation error and
    # nothing else.
    centre_tolerance: Decimal = Decimal("1E-20")

    def __post_init__(self) -> None:
        for name in ("minimum_resolved_forecasts", "minimum_distinct_items"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise WalkForwardCalibrationError(f"{name} must be a positive integer.")
        if (
            isinstance(self.embargo_days, bool)
            or not isinstance(self.embargo_days, int)
            or self.embargo_days < 0
        ):
            raise WalkForwardCalibrationError(
                "embargo_days must be a non-negative integer."
            )
        for name in (
            "dispersion_grid_minimum",
            "dispersion_grid_maximum",
            "dispersion_grid_step",
            "ratio_quantum",
            "centre_tolerance",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value <= Decimal("0")
            ):
                raise WalkForwardCalibrationError(
                    f"{name} must be a positive finite Decimal."
                )
        if self.dispersion_grid_minimum >= self.dispersion_grid_maximum:
            raise WalkForwardCalibrationError(
                "dispersion_grid_minimum must be below dispersion_grid_maximum."
            )
        if not self.dispersion_grid_minimum <= _UNIT <= self.dispersion_grid_maximum:
            raise WalkForwardCalibrationError(
                "the dispersion grid must contain 1, the no-change factor."
            )
        offset = _UNIT - self.dispersion_grid_minimum
        if offset % self.dispersion_grid_step != Decimal("0"):
            raise WalkForwardCalibrationError(
                "the dispersion grid must land exactly on 1."
            )
        if not self.coverage_probabilities:
            raise WalkForwardCalibrationError(
                "coverage_probabilities must be non-empty."
            )
        if any(
            not isinstance(value, Decimal)
            or not value.is_finite()
            or not Decimal("0") < value < _UNIT
            for value in self.coverage_probabilities
        ):
            raise WalkForwardCalibrationError(
                "coverage probabilities must be finite Decimals in (0, 1)."
            )
        if (
            tuple(sorted(set(self.coverage_probabilities)))
            != self.coverage_probabilities
        ):
            raise WalkForwardCalibrationError(
                "coverage_probabilities must be strictly increasing and unique."
            )

    @property
    def dispersion_grid(self) -> tuple[Decimal, ...]:
        values: list[Decimal] = []
        value = self.dispersion_grid_minimum
        while value <= self.dispersion_grid_maximum:
            values.append(value)
            value += self.dispersion_grid_step
        return tuple(values)


@dataclass(frozen=True, slots=True)
class ResolutionSelection:
    """The point-in-time split of a resolution pool at one decision instant."""

    as_of: datetime
    usable: tuple[ResolvedRatioForecast, ...]
    purged_label_open_count: int
    purged_embargo_count: int
    degenerate_count: int
    explanation_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DispersionCalibration:
    as_of: datetime
    status: DispersionCalibrationStatus
    applicable: bool
    factor: Decimal | None
    usable_count: int
    degenerate_count: int
    purged_label_open_count: int
    purged_embargo_count: int
    distinct_item_count: int
    distinct_event_count: int
    criterion_at_unit: Decimal | None
    criterion_at_factor: Decimal | None
    transform_at_unit: tuple[Decimal, ...]
    transform_at_factor: tuple[Decimal, ...]
    grid: tuple[Decimal, ...]
    explanation_codes: tuple[str, ...]
    version: str = WALK_FORWARD_CALIBRATION_VERSION


@dataclass(frozen=True, slots=True)
class RatioIntervalCoverage:
    nominal_coverage: Decimal
    lower_ratio: Decimal
    upper_ratio: Decimal
    covered: bool


@dataclass(frozen=True, slots=True)
class WalkForwardTrialOutcome:
    """One forecast scored raw and calibrated, from the same decision instant."""

    item_id: int
    event_key: str
    prediction_as_of: datetime
    outcome_observed_at: datetime
    realized_ratio: Decimal
    calibration_status: DispersionCalibrationStatus
    factor: Decimal | None
    fit_sample_count: int
    raw_transform: Decimal
    calibrated_transform: Decimal
    raw_coverages: tuple[RatioIntervalCoverage, ...]
    calibrated_coverages: tuple[RatioIntervalCoverage, ...]
    support_atom_count: int
    raw_position: RealizedSupportPosition
    calibrated_position: RealizedSupportPosition


@dataclass(frozen=True, slots=True)
class IntervalCoverageSummary:
    nominal_coverage: Decimal
    trial_count: int
    raw_covered_count: int
    calibrated_covered_count: int


@dataclass(frozen=True, slots=True)
class SupportReachSummary:
    """How often the predictive support contains the realization at all.

    A distribution built from two prior events has two atoms and therefore no
    interior: every realization is either one of those two numbers or outside
    the pair. When ``raw_inside_count`` is near zero the forecast is not
    mis-scaled, it is under-resolved, and widening it is treating the wrong
    disease. ``calibrated_inside_count`` says whether scaling helped, and on a
    two-atom support it usually cannot, because scaling about the lower central
    atom only moves the upper tail.
    """

    trial_count: int
    support_atom_counts: tuple[tuple[int, int], ...]
    raw_below_count: int
    raw_inside_count: int
    raw_above_count: int
    calibrated_below_count: int
    calibrated_inside_count: int
    calibrated_above_count: int


@dataclass(frozen=True, slots=True)
class LocationBiasSummary:
    """Whether the realizations sit to one side of the forecast, not just wide.

    Measured, never corrected. A median-preserving dispersion factor cannot
    move a distribution that is simply in the wrong place, and the correction
    that could — shifting the centre — is the one degree of freedom able to
    turn a losing median into a winning one. That is not a knob to fit on a
    handful of overlapping events, so this summary exists to name the defect
    and to accumulate the evidence a later decision would need.

    Rows repeat items and events and are not independent, so the counts here
    describe this cohort and support no significance claim.
    """

    trial_count: int
    median_transform: Decimal | None
    below_centre_count: int
    at_centre_count: int
    above_centre_count: int


@dataclass(frozen=True, slots=True)
class WalkForwardCalibrationReport:
    """Out-of-time comparison of the raw and calibrated distributions.

    Every row was scored with a factor fitted only on labels that had already
    closed at that row's own decision instant, so the aggregate is a genuine
    out-of-sample comparison rather than a fit reported back to itself.
    """

    trial_count: int
    calibrated_trial_count: int
    status_counts: tuple[tuple[DispersionCalibrationStatus, int], ...]
    raw_uniformity_criterion: Decimal | None
    calibrated_uniformity_criterion: Decimal | None
    mean_applied_factor: Decimal | None
    coverage: tuple[IntervalCoverageSummary, ...]
    support_reach: SupportReachSummary
    location_bias: LocationBiasSummary
    trials: tuple[WalkForwardTrialOutcome, ...]
    explanation_codes: tuple[str, ...]
    version: str = WALK_FORWARD_CALIBRATION_VERSION


def select_resolutions(
    *,
    resolutions: Iterable[ResolvedRatioForecast],
    as_of: datetime,
    config: WalkForwardCalibrationConfig | None = None,
) -> ResolutionSelection:
    """Keep only the resolutions whose labels had closed by ``as_of``.

    A resolution qualifies when its outcome was observed at or before ``as_of``
    minus the embargo. The two rejection reasons are counted separately because
    they mean different things: an open label is a forecast we are still
    waiting on, while an embargoed one is a forecast we deliberately refuse to
    learn from because its window sits too close to the decision.
    """

    cfg = config or WalkForwardCalibrationConfig()
    moment = _utc(as_of, "as_of")
    cutoff = moment - timedelta(days=cfg.embargo_days)
    usable: list[ResolvedRatioForecast] = []
    open_labels = 0
    embargoed = 0
    for value in resolutions:
        if value.outcome_observed_at > moment:
            open_labels += 1
            continue
        if value.outcome_observed_at > cutoff:
            embargoed += 1
            continue
        usable.append(value)
    usable.sort(key=lambda value: (value.outcome_observed_at, value.item_id, value.event_key))
    codes: list[str] = ["walk_forward_point_in_time_label_closure_enforced"]
    if open_labels:
        codes.append("walk_forward_purged_open_label_resolutions")
    if embargoed:
        codes.append("walk_forward_purged_embargoed_resolutions")
    return ResolutionSelection(
        as_of=moment,
        usable=tuple(usable),
        purged_label_open_count=open_labels,
        purged_embargo_count=embargoed,
        degenerate_count=sum(value.degenerate for value in usable),
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def calibrate_dispersion(
    *,
    resolutions: Iterable[ResolvedRatioForecast],
    as_of: datetime,
    config: WalkForwardCalibrationConfig | None = None,
    transform_cache: dict[tuple[ResolvedRatioForecast, Decimal], Decimal] | None = None,
) -> DispersionCalibration:
    """Fit the median-preserving dispersion factor usable at ``as_of``.

    The fit refuses on three grounds, each of which would otherwise produce a
    confident number from nothing: too few closed labels, too few distinct
    items behind those labels, and a sample in which no forecast had any spread
    to correct. A refusal is not an error; it returns ``applicable=False`` and
    the caller leaves the forecast untouched.

    ``minimum_distinct_items`` exists because these rows are not independent.
    Eight resolutions drawn from one vehicle describe that vehicle's idiosyncratic
    path, and a factor fitted on them would be presented as a property of the
    model.

    ``transform_cache`` is an optional memo a caller may share across calls. A
    resolution's transform under a given factor depends only on that resolution
    and that factor, never on the instant being fitted, so a replay that fits
    at every decision point would otherwise recompute the same value once per
    point. Passing one dictionary through turns that quadratic term linear. The
    memo is keyed on exactly the inputs that determine the value, so sharing it
    cannot change an answer; callers must not mutate it themselves.
    """

    cfg = config or WalkForwardCalibrationConfig()
    selection = select_resolutions(resolutions=resolutions, as_of=as_of, config=cfg)
    informative = tuple(value for value in selection.usable if not value.degenerate)
    items = {value.item_id for value in informative}
    events = {(value.item_id, value.event_key) for value in informative}
    grid = cfg.dispersion_grid
    codes = list(selection.explanation_codes)

    def refuse(
        status: DispersionCalibrationStatus, code: str
    ) -> DispersionCalibration:
        return DispersionCalibration(
            as_of=selection.as_of,
            status=status,
            applicable=False,
            factor=None,
            usable_count=len(informative),
            degenerate_count=selection.degenerate_count,
            purged_label_open_count=selection.purged_label_open_count,
            purged_embargo_count=selection.purged_embargo_count,
            distinct_item_count=len(items),
            distinct_event_count=len(events),
            criterion_at_unit=None,
            criterion_at_factor=None,
            transform_at_unit=(),
            transform_at_factor=(),
            grid=grid,
            explanation_codes=tuple(dict.fromkeys([*codes, code])),
        )

    if selection.usable and not informative:
        return refuse(
            "NO_DISPERSION_INFORMATION",
            "walk_forward_every_resolution_has_degenerate_support",
        )
    if len(informative) < cfg.minimum_resolved_forecasts:
        return refuse(
            "INSUFFICIENT_RESOLVED_SAMPLE",
            "walk_forward_insufficient_closed_labels_to_calibrate",
        )
    if len(items) < cfg.minimum_distinct_items:
        return refuse(
            "INSUFFICIENT_DISTINCT_ITEMS",
            "walk_forward_calibration_sample_not_spread_across_items",
        )

    best_factor = _UNIT
    best_criterion: Decimal | None = None
    best_transforms: tuple[Decimal, ...] = ()
    unit_criterion: Decimal | None = None
    unit_transforms: tuple[Decimal, ...] = ()
    cache = {} if transform_cache is None else transform_cache
    for factor in grid:
        transforms = tuple(
            _transform_of(value, factor, cfg.ratio_quantum, cache)
            for value in informative
        )
        criterion = uniformity_criterion(transforms)
        if factor == _UNIT:
            unit_criterion = criterion
            unit_transforms = transforms
        if best_criterion is None or _better(
            criterion, factor, best_criterion, best_factor
        ):
            best_criterion = criterion
            best_factor = factor
            best_transforms = transforms

    codes.append("walk_forward_dispersion_factor_fitted_on_closed_labels")
    if best_factor == _UNIT:
        codes.append("walk_forward_dispersion_factor_left_at_unit")
    elif best_factor > _UNIT:
        codes.append("walk_forward_dispersion_widened")
    else:
        codes.append("walk_forward_dispersion_tightened")
    if selection.degenerate_count:
        codes.append("walk_forward_excluded_degenerate_support_resolutions")
    return DispersionCalibration(
        as_of=selection.as_of,
        status="CALIBRATED",
        applicable=True,
        factor=best_factor,
        usable_count=len(informative),
        degenerate_count=selection.degenerate_count,
        purged_label_open_count=selection.purged_label_open_count,
        purged_embargo_count=selection.purged_embargo_count,
        distinct_item_count=len(items),
        distinct_event_count=len(events),
        criterion_at_unit=unit_criterion,
        criterion_at_factor=best_criterion,
        transform_at_unit=unit_transforms,
        transform_at_factor=best_transforms,
        grid=grid,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def _transform_of(
    value: ResolvedRatioForecast,
    factor: Decimal,
    quantum: Decimal,
    cache: dict[tuple[ResolvedRatioForecast, Decimal], Decimal],
) -> Decimal:
    """Memoized mid-transform of one resolution under one candidate factor."""

    key = (value, factor)
    hit = cache.get(key)
    if hit is None:
        hit = _mid_transform(
            scale_support(value.support, factor, ratio_quantum=quantum),
            value.realized_ratio,
        )
        cache[key] = hit
    return hit


def scale_support(
    support: tuple[PredictiveRatioAtom, ...],
    factor: Decimal,
    *,
    ratio_quantum: Decimal = Decimal("1E-18"),
) -> tuple[PredictiveRatioAtom, ...]:
    """Power-scale a support about its weighted median.

    Weights are untouched, so the transform moves where the mass sits without
    changing how much of it there is. The median atom is returned exactly as it
    came in, which keeps the fixed point exact rather than merely close.
    """

    _positive(factor, "factor")
    if not support:
        raise WalkForwardCalibrationError("support must be non-empty.")
    if factor == _UNIT:
        return support
    median = _support_median(support)
    scaled = tuple(
        PredictiveRatioAtom(
            ratio=_scale_ratio(atom.ratio, median, factor, ratio_quantum),
            weight_share=atom.weight_share,
            cumulative_weight_share=atom.cumulative_weight_share,
        )
        for atom in support
    )
    previous = Decimal("0")
    for atom in scaled:
        if atom.ratio <= Decimal("0"):
            raise WalkForwardCalibrationError(
                "dispersion scaling produced a non-positive ratio."
            )
        if atom.ratio < previous:
            raise WalkForwardCalibrationError(
                "dispersion scaling must preserve the support order."
            )
        previous = atom.ratio
    return scaled


def scale_ratio_observations(
    observations: Iterable[PriceRatioObservation],
    factor: Decimal,
    *,
    ratio_quantum: Decimal = Decimal("1E-18"),
) -> tuple[PriceRatioObservation, ...]:
    """Apply the dispersion factor to the samples a forecast will be built from.

    Scaling the inputs rather than the finished forecast is deliberate: the
    forecast derives prices, fees, net ROI and quantile support from these
    observations, and re-deriving any of that here would be a second
    implementation of arithmetic that already exists and is already tested.
    """

    _positive(factor, "factor")
    samples = tuple(observations)
    if not samples:
        return ()
    if factor == _UNIT:
        return samples
    median = _observation_median(samples)
    return tuple(
        PriceRatioObservation(
            source_item_id=sample.source_item_id,
            source_event_key=sample.source_event_key,
            horizon_days=sample.horizon_days,
            ratio=_scale_ratio(sample.ratio, median, factor, ratio_quantum),
            weight=sample.weight,
            observed_at=sample.observed_at,
            basis=sample.basis,
            source=sample.source,
        )
        for sample in samples
    )


def resolve_dispersion_observations(
    *,
    observations: Iterable[PriceRatioObservation],
    calibration: DispersionCalibration | None,
    policy: DispersionCalibrationPolicy = "RAW_ONLY",
    config: WalkForwardCalibrationConfig | None = None,
) -> tuple[tuple[PriceRatioObservation, ...], tuple[str, ...]]:
    """Decide what the forecast is actually built from, under one policy.

    ``RAW_ONLY`` is the production default and returns the observations
    unchanged and untouched, so a deployment that has not opted in produces the
    same forecast object it produced before this module existed. ``SHADOW``
    reports the factor it would have used without applying it, which is how a
    candidate policy earns its evidence.
    """

    cfg = config or WalkForwardCalibrationConfig()
    if policy not in DISPERSION_CALIBRATION_POLICIES:
        raise WalkForwardCalibrationError("unsupported dispersion calibration policy.")
    samples = tuple(observations)
    if policy == "RAW_ONLY":
        return samples, ("walk_forward_dispersion_raw_only",)
    if calibration is None or not calibration.applicable:
        status = "unavailable" if calibration is None else calibration.status.lower()
        return samples, (f"walk_forward_dispersion_not_applied_{status}",)
    assert calibration.factor is not None
    if policy == "SHADOW":
        return samples, ("walk_forward_dispersion_shadow_not_applied",)
    if calibration.factor == _UNIT:
        return samples, ("walk_forward_dispersion_applied_at_unit",)
    scaled = scale_ratio_observations(
        samples, calibration.factor, ratio_quantum=cfg.ratio_quantum
    )
    return scaled, ("walk_forward_dispersion_applied",)


def evaluate_walk_forward_calibration(
    *,
    resolutions: Iterable[ResolvedRatioForecast],
    config: WalkForwardCalibrationConfig | None = None,
) -> WalkForwardCalibrationReport:
    """Replay a resolution pool as a strict walk-forward calibration exercise.

    Each resolution is scored twice at its own decision instant: once as it was
    published, and once under the factor that could have been fitted at that
    instant from labels which had already closed. A row never contributes to
    the fit that scores it, so the aggregate answers the only question worth
    asking, which is whether calibration would have helped a decision made
    before the answer was available.
    """

    cfg = config or WalkForwardCalibrationConfig()
    pool = tuple(resolutions)
    ordered = sorted(
        pool, key=lambda value: (value.prediction_as_of, value.item_id, value.event_key)
    )
    # One memo for the whole replay. Every decision instant fits over an
    # expanding prefix of the same resolutions, so without it each resolution's
    # transform is recomputed once per later instant.
    cache: dict[tuple[ResolvedRatioForecast, Decimal], Decimal] = {}
    trials: list[WalkForwardTrialOutcome] = []
    for trial in ordered:
        calibration = calibrate_dispersion(
            resolutions=pool,
            as_of=trial.prediction_as_of,
            config=cfg,
            transform_cache=cache,
        )
        factor = calibration.factor if calibration.applicable else None
        calibrated_support = (
            scale_support(trial.support, factor, ratio_quantum=cfg.ratio_quantum)
            if factor is not None
            else trial.support
        )
        trials.append(
            WalkForwardTrialOutcome(
                item_id=trial.item_id,
                event_key=trial.event_key,
                prediction_as_of=trial.prediction_as_of,
                outcome_observed_at=trial.outcome_observed_at,
                realized_ratio=trial.realized_ratio,
                calibration_status=calibration.status,
                factor=factor,
                fit_sample_count=calibration.usable_count,
                raw_transform=_mid_transform(trial.support, trial.realized_ratio),
                calibrated_transform=_mid_transform(
                    calibrated_support, trial.realized_ratio
                ),
                raw_coverages=_coverages(
                    trial.support, trial.realized_ratio, cfg.coverage_probabilities
                ),
                calibrated_coverages=_coverages(
                    calibrated_support, trial.realized_ratio, cfg.coverage_probabilities
                ),
                support_atom_count=len(trial.support),
                raw_position=_position(trial.support, trial.realized_ratio),
                calibrated_position=_position(
                    calibrated_support, trial.realized_ratio
                ),
            )
        )

    statuses: dict[DispersionCalibrationStatus, int] = {}
    for row in trials:
        statuses[row.calibration_status] = statuses.get(row.calibration_status, 0) + 1
    applied = tuple(
        row.factor for row in trials if row.factor is not None and row.factor != _UNIT
    )
    coverage = tuple(
        IntervalCoverageSummary(
            nominal_coverage=probability,
            trial_count=len(trials),
            raw_covered_count=sum(
                value.covered
                for row in trials
                for value in row.raw_coverages
                if value.nominal_coverage == probability
            ),
            calibrated_covered_count=sum(
                value.covered
                for row in trials
                for value in row.calibrated_coverages
                if value.nominal_coverage == probability
            ),
        )
        for probability in cfg.coverage_probabilities
    )
    histogram: dict[int, int] = {}
    for row in trials:
        histogram[row.support_atom_count] = (
            histogram.get(row.support_atom_count, 0) + 1
        )
    reach = SupportReachSummary(
        trial_count=len(trials),
        support_atom_counts=tuple(sorted(histogram.items())),
        raw_below_count=sum(row.raw_position == "BELOW_SUPPORT" for row in trials),
        raw_inside_count=sum(row.raw_position == "INSIDE_SUPPORT" for row in trials),
        raw_above_count=sum(row.raw_position == "ABOVE_SUPPORT" for row in trials),
        calibrated_below_count=sum(
            row.calibrated_position == "BELOW_SUPPORT" for row in trials
        ),
        calibrated_inside_count=sum(
            row.calibrated_position == "INSIDE_SUPPORT" for row in trials
        ),
        calibrated_above_count=sum(
            row.calibrated_position == "ABOVE_SUPPORT" for row in trials
        ),
    )

    transforms = tuple(row.raw_transform for row in trials)
    half = Decimal("0.5")
    tolerance = cfg.centre_tolerance
    location = LocationBiasSummary(
        trial_count=len(trials),
        median_transform=_median(transforms),
        below_centre_count=sum(value < half - tolerance for value in transforms),
        at_centre_count=sum(abs(value - half) <= tolerance for value in transforms),
        above_centre_count=sum(value > half + tolerance for value in transforms),
    )

    codes = ["walk_forward_calibration_report_research_only"]
    if not trials:
        codes.append("walk_forward_no_resolutions_to_replay")
    calibrated_rows = sum(row.factor is not None for row in trials)
    if not calibrated_rows:
        codes.append("walk_forward_no_row_had_a_usable_calibration_sample")
    if trials and not reach.raw_inside_count:
        codes.append("walk_forward_support_never_reached_the_realization")
    if calibrated_rows and reach.calibrated_inside_count == reach.raw_inside_count:
        codes.append("walk_forward_scaling_moved_no_realization_inside_support")
    if trials and max(histogram) <= 3:
        codes.append("walk_forward_support_too_coarse_for_dispersion_to_be_the_defect")
    if trials and not (location.below_centre_count and location.above_centre_count):
        codes.append("walk_forward_realizations_fall_entirely_on_one_side")
    return WalkForwardCalibrationReport(
        trial_count=len(trials),
        calibrated_trial_count=calibrated_rows,
        status_counts=tuple(sorted(statuses.items())),
        raw_uniformity_criterion=(
            uniformity_criterion(tuple(row.raw_transform for row in trials))
            if trials
            else None
        ),
        calibrated_uniformity_criterion=(
            uniformity_criterion(tuple(row.calibrated_transform for row in trials))
            if trials
            else None
        ),
        mean_applied_factor=(
            sum(applied, Decimal("0")) / Decimal(len(applied)) if applied else None
        ),
        coverage=coverage,
        support_reach=reach,
        location_bias=location,
        trials=tuple(trials),
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def uniformity_criterion(values: tuple[Decimal, ...]) -> Decimal:
    """Cramer-von Mises distance from the uniform distribution.

    Chosen over a Kolmogorov-Smirnov statistic because it uses every order
    statistic rather than only the worst one, which matters when the sample is
    a dozen points and the failure being measured is mass piling into both
    tails at once.
    """

    if not values:
        raise WalkForwardCalibrationError("uniformity needs at least one value.")
    ordered = sorted(values)
    count = Decimal(len(ordered))
    total = Decimal("1") / (Decimal("12") * count)
    for index, value in enumerate(ordered, start=1):
        expected = (Decimal(2 * index) - _UNIT) / (Decimal("2") * count)
        total += (value - expected) ** 2
    return total


def _better(
    criterion: Decimal,
    factor: Decimal,
    best_criterion: Decimal,
    best_factor: Decimal,
) -> bool:
    """Prefer a lower criterion, then the factor closest to leaving it alone."""

    if criterion != best_criterion:
        return criterion < best_criterion
    distance = abs(factor - _UNIT)
    best_distance = abs(best_factor - _UNIT)
    if distance != best_distance:
        return distance < best_distance
    return factor < best_factor


def _mid_transform(
    support: tuple[PredictiveRatioAtom, ...], realized: Decimal
) -> Decimal:
    """Mid probability integral transform of ``realized`` under ``support``."""

    below = sum(
        (atom.weight_share for atom in support if atom.ratio < realized), Decimal("0")
    )
    equal = sum(
        (atom.weight_share for atom in support if atom.ratio == realized), Decimal("0")
    )
    return below + equal / Decimal("2")


def _coverages(
    support: tuple[PredictiveRatioAtom, ...],
    realized: Decimal,
    probabilities: tuple[Decimal, ...],
) -> tuple[RatioIntervalCoverage, ...]:
    results: list[RatioIntervalCoverage] = []
    for probability in probabilities:
        lower = _inverse_cdf(support, (_UNIT - probability) / Decimal("2"))
        upper = _inverse_cdf(support, (_UNIT + probability) / Decimal("2"))
        results.append(
            RatioIntervalCoverage(
                nominal_coverage=probability,
                lower_ratio=lower,
                upper_ratio=upper,
                covered=lower <= realized <= upper,
            )
        )
    return tuple(results)


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _position(
    support: tuple[PredictiveRatioAtom, ...], realized: Decimal
) -> RealizedSupportPosition:
    if realized < support[0].ratio:
        return "BELOW_SUPPORT"
    if realized > support[-1].ratio:
        return "ABOVE_SUPPORT"
    return "INSIDE_SUPPORT"


def _inverse_cdf(
    support: tuple[PredictiveRatioAtom, ...], probability: Decimal
) -> Decimal:
    """Weighted inverse CDF with no interpolation between atoms.

    Matches the stage 12 convention exactly: the first atom whose cumulative
    share reaches the target, and never a value the sample did not contain.
    """

    for atom in support:
        if atom.cumulative_weight_share >= probability:
            return atom.ratio
    return support[-1].ratio


def _support_median(support: tuple[PredictiveRatioAtom, ...]) -> Decimal:
    return _inverse_cdf(support, Decimal("0.5"))


def _observation_median(samples: tuple[PriceRatioObservation, ...]) -> Decimal:
    """The median a forecast built from ``samples`` would report.

    Deliberately mirrors the stage 12 ordering and ``>=`` rule rather than
    computing a median in the ordinary sense, so that scaling about it leaves
    the published median quantile exactly where it was.
    """

    ordered = sorted(
        samples,
        key=lambda value: (value.ratio, value.source_item_id, value.source_event_key),
    )
    total = sum((value.weight for value in ordered), Decimal("0"))
    target = total / Decimal("2")
    cumulative = Decimal("0")
    for sample in ordered:
        cumulative += sample.weight
        if cumulative >= target:
            return sample.ratio
    return ordered[-1].ratio


def _scale_ratio(
    ratio: Decimal, median: Decimal, factor: Decimal, quantum: Decimal
) -> Decimal:
    if ratio == median:
        return ratio
    with localcontext() as context:
        context.prec = _POWER_PRECISION
        scaled = median * ((ratio / median) ** factor)
        return scaled.quantize(quantum, rounding=ROUND_HALF_EVEN)


def _positive(value: Decimal, field_name: str) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= Decimal("0")
    ):
        raise WalkForwardCalibrationError(
            f"{field_name} must be a positive finite Decimal."
        )


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WalkForwardCalibrationError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
