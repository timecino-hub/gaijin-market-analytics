"""Stage 16 — strict walk-forward calibration of the exit price distribution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.price_distribution import (
    PredictiveRatioAtom,
    PriceRatioObservation,
    forecast_price_distribution,
)
from gaijin_market_analytics.walk_forward import (
    WALK_FORWARD_CALIBRATION_VERSION,
    DispersionCalibration,
    ResolvedRatioForecast,
    WalkForwardCalibrationConfig,
    WalkForwardCalibrationError,
    calibrate_dispersion,
    evaluate_walk_forward_calibration,
    resolve_dispersion_observations,
    scale_ratio_observations,
    scale_support,
    select_resolutions,
    uniformity_criterion,
)


BASE = datetime(2026, 1, 1, tzinfo=UTC)
LOOSE_FIT = WalkForwardCalibrationConfig(
    minimum_resolved_forecasts=3, minimum_distinct_items=2
)
NARROW = ((Decimal("0.80"), Decimal("1")), (Decimal("1.00"), Decimal("1")), (Decimal("1.25"), Decimal("1")))


def atoms(pairs: tuple[tuple[Decimal, Decimal], ...]) -> tuple[PredictiveRatioAtom, ...]:
    total = sum((weight for _, weight in pairs), Decimal("0"))
    built: list[PredictiveRatioAtom] = []
    cumulative = Decimal("0")
    for ratio, weight in pairs:
        share = weight / total
        cumulative += share
        built.append(
            PredictiveRatioAtom(
                ratio=ratio, weight_share=share, cumulative_weight_share=cumulative
            )
        )
    return tuple(built)


def resolution(
    *,
    item_id: int,
    event: str,
    day: int,
    realized: Decimal,
    support: tuple[PredictiveRatioAtom, ...] | None = None,
    label_days: int = 30,
) -> ResolvedRatioForecast:
    return ResolvedRatioForecast(
        item_id=item_id,
        event_key=event,
        horizon_days=30,
        basis="PRE_EVENT_MEDIAN",
        prediction_as_of=BASE + timedelta(days=day),
        outcome_observed_at=BASE + timedelta(days=day + label_days),
        realized_ratio=realized,
        support=support if support is not None else atoms(NARROW),
    )


def narrow_pool() -> tuple[ResolvedRatioForecast, ...]:
    """Nine resolutions from three items whose published spread is too tight."""

    realized = [
        Decimal("0.50"),
        Decimal("0.55"),
        Decimal("0.60"),
        Decimal("0.90"),
        Decimal("1.00"),
        Decimal("1.10"),
        Decimal("1.80"),
        Decimal("1.90"),
        Decimal("2.00"),
    ]
    return tuple(
        resolution(
            item_id=1 + index % 3,
            event=f"event-{index:02d}",
            day=index * 5,
            realized=value,
        )
        for index, value in enumerate(realized)
    )


def observations(ratios: tuple[Decimal, ...]) -> tuple[PriceRatioObservation, ...]:
    return tuple(
        PriceRatioObservation(
            source_item_id=10 + index,
            source_event_key=f"source-{index}",
            horizon_days=30,
            ratio=ratio,
            weight=Decimal("1"),
            observed_at=BASE - timedelta(days=10),
            basis="PRE_EVENT_MEDIAN",
            source="ITEM_LOCAL",
        )
        for index, ratio in enumerate(ratios)
    )


# --- point-in-time label closure -------------------------------------------


def test_a_resolution_whose_label_is_still_open_is_purged_and_counted() -> None:
    pool = (
        resolution(item_id=1, event="closed", day=0, realized=Decimal("1.0")),
        resolution(item_id=2, event="open", day=20, realized=Decimal("1.0")),
    )
    selection = select_resolutions(resolutions=pool, as_of=BASE + timedelta(days=40))

    assert [value.event_key for value in selection.usable] == ["closed"]
    assert selection.purged_label_open_count == 1
    assert selection.purged_embargo_count == 0
    assert (
        "walk_forward_purged_open_label_resolutions" in selection.explanation_codes
    )


def test_a_label_closing_exactly_at_as_of_is_usable() -> None:
    pool = (resolution(item_id=1, event="edge", day=0, realized=Decimal("1.0")),)
    selection = select_resolutions(resolutions=pool, as_of=BASE + timedelta(days=30))

    assert len(selection.usable) == 1
    assert selection.purged_label_open_count == 0


def test_the_embargo_removes_labels_that_closed_too_recently() -> None:
    pool = (resolution(item_id=1, event="recent", day=0, realized=Decimal("1.0")),)
    config = WalkForwardCalibrationConfig(embargo_days=7)
    selection = select_resolutions(
        resolutions=pool, as_of=BASE + timedelta(days=33), config=config
    )

    assert not selection.usable
    assert selection.purged_embargo_count == 1
    assert selection.purged_label_open_count == 0
    assert "walk_forward_purged_embargoed_resolutions" in selection.explanation_codes


def test_a_forecast_never_contributes_to_the_fit_that_scores_it() -> None:
    report = evaluate_walk_forward_calibration(
        resolutions=narrow_pool(),
        config=WalkForwardCalibrationConfig(
            minimum_resolved_forecasts=1, minimum_distinct_items=1
        ),
    )

    # Every row's fit sample must have closed strictly before that row's own
    # decision instant, which for the earliest row means no sample at all.
    assert report.trials[0].fit_sample_count == 0
    assert report.trials[0].factor is None
    for row in report.trials:
        closed_before = sum(
            value.outcome_observed_at <= row.prediction_as_of
            for value in narrow_pool()
        )
        assert row.fit_sample_count <= closed_before


def test_selection_is_deterministically_ordered() -> None:
    pool = narrow_pool()
    first = select_resolutions(resolutions=pool, as_of=BASE + timedelta(days=200))
    second = select_resolutions(
        resolutions=tuple(reversed(pool)), as_of=BASE + timedelta(days=200)
    )

    assert first.usable == second.usable


# --- refusals ---------------------------------------------------------------


def test_too_few_closed_labels_refuses_rather_than_guessing() -> None:
    result = calibrate_dispersion(
        resolutions=narrow_pool(), as_of=BASE + timedelta(days=40)
    )

    assert result.status == "INSUFFICIENT_RESOLVED_SAMPLE"
    assert result.applicable is False
    assert result.factor is None
    assert (
        "walk_forward_insufficient_closed_labels_to_calibrate"
        in result.explanation_codes
    )


def test_a_sample_concentrated_in_one_item_refuses() -> None:
    pool = tuple(
        resolution(
            item_id=1,
            event=f"event-{index}",
            day=index * 5,
            realized=Decimal("0.5") + Decimal(index) / Decimal("10"),
        )
        for index in range(9)
    )
    result = calibrate_dispersion(resolutions=pool, as_of=BASE + timedelta(days=200))

    assert result.status == "INSUFFICIENT_DISTINCT_ITEMS"
    assert result.distinct_item_count == 1
    assert (
        "walk_forward_calibration_sample_not_spread_across_items"
        in result.explanation_codes
    )


def test_a_sample_of_point_masses_carries_no_dispersion_information() -> None:
    point = atoms(((Decimal("1.00"), Decimal("1")),))
    pool = tuple(
        resolution(
            item_id=1 + index % 3,
            event=f"event-{index}",
            day=index * 5,
            realized=Decimal("1.2"),
            support=point,
        )
        for index in range(9)
    )
    result = calibrate_dispersion(resolutions=pool, as_of=BASE + timedelta(days=200))

    assert result.status == "NO_DISPERSION_INFORMATION"
    assert result.degenerate_count == 9
    assert result.usable_count == 0


def test_degenerate_rows_are_excluded_without_blocking_a_fit() -> None:
    pool = (
        *narrow_pool(),
        resolution(
            item_id=4,
            event="point-mass",
            day=1,
            realized=Decimal("1.0"),
            support=atoms(((Decimal("1.00"), Decimal("1")),)),
        ),
    )
    result = calibrate_dispersion(resolutions=pool, as_of=BASE + timedelta(days=200))

    assert result.status == "CALIBRATED"
    assert result.degenerate_count == 1
    assert result.usable_count == 9
    assert (
        "walk_forward_excluded_degenerate_support_resolutions"
        in result.explanation_codes
    )


# --- the fit ----------------------------------------------------------------


def test_a_too_narrow_distribution_is_widened_and_the_criterion_improves() -> None:
    result = calibrate_dispersion(
        resolutions=narrow_pool(), as_of=BASE + timedelta(days=200)
    )

    assert result.status == "CALIBRATED"
    assert result.factor is not None and result.factor > Decimal("1")
    assert result.criterion_at_unit is not None
    assert result.criterion_at_factor is not None
    assert result.criterion_at_factor < result.criterion_at_unit
    assert "walk_forward_dispersion_widened" in result.explanation_codes
    assert result.distinct_item_count == 3
    assert result.version == WALK_FORWARD_CALIBRATION_VERSION


def test_the_fitted_factor_is_deterministic() -> None:
    pool = narrow_pool()
    first = calibrate_dispersion(resolutions=pool, as_of=BASE + timedelta(days=200))
    second = calibrate_dispersion(
        resolutions=tuple(reversed(pool)), as_of=BASE + timedelta(days=200)
    )

    assert first.factor == second.factor
    assert first.transform_at_factor == second.transform_at_factor


def test_a_sample_already_uniform_is_left_alone() -> None:
    # Realized ratios landing on the published atoms produce transform values
    # spread across the unit interval, so no widening can improve on 1.
    realized = [
        Decimal("0.70"),
        Decimal("0.90"),
        Decimal("0.95"),
        Decimal("1.05"),
        Decimal("1.10"),
        Decimal("1.15"),
        Decimal("1.20"),
        Decimal("1.22"),
        Decimal("1.24"),
    ]
    pool = tuple(
        resolution(
            item_id=1 + index % 3,
            event=f"event-{index}",
            day=index * 5,
            realized=value,
        )
        for index, value in enumerate(realized)
    )
    result = calibrate_dispersion(resolutions=pool, as_of=BASE + timedelta(days=200))

    assert result.status == "CALIBRATED"
    assert result.criterion_at_factor is not None
    assert result.criterion_at_unit is not None
    assert result.criterion_at_factor <= result.criterion_at_unit


def test_the_grid_always_contains_the_no_change_factor() -> None:
    assert Decimal("1") in WalkForwardCalibrationConfig().dispersion_grid


def test_a_grid_that_misses_one_is_rejected() -> None:
    with pytest.raises(WalkForwardCalibrationError, match="land exactly on 1"):
        WalkForwardCalibrationConfig(
            dispersion_grid_minimum=Decimal("0.40"),
            dispersion_grid_step=Decimal("0.25"),
        )

    with pytest.raises(WalkForwardCalibrationError, match="must contain 1"):
        WalkForwardCalibrationConfig(
            dispersion_grid_minimum=Decimal("1.50"),
            dispersion_grid_maximum=Decimal("4.00"),
        )


# --- the transform ----------------------------------------------------------


def test_scaling_at_one_is_the_identity() -> None:
    support = atoms(NARROW)
    samples = observations((Decimal("0.8"), Decimal("1.0"), Decimal("1.25")))

    assert scale_support(support, Decimal("1")) is support
    assert scale_ratio_observations(samples, Decimal("1")) == samples


def test_widening_preserves_the_median_and_pushes_the_tails_out() -> None:
    support = atoms(NARROW)
    widened = scale_support(support, Decimal("2"))

    assert widened[1].ratio == support[1].ratio == Decimal("1.00")
    assert widened[0].ratio < support[0].ratio
    assert widened[2].ratio > support[2].ratio
    assert [value.weight_share for value in widened] == [
        value.weight_share for value in support
    ]


def test_tightening_pulls_the_tails_in() -> None:
    support = atoms(NARROW)
    tightened = scale_support(support, Decimal("0.5"))

    assert tightened[1].ratio == Decimal("1.00")
    assert tightened[0].ratio > support[0].ratio
    assert tightened[2].ratio < support[2].ratio


def test_scaling_preserves_support_order() -> None:
    support = atoms(
        (
            (Decimal("0.10"), Decimal("1")),
            (Decimal("0.99"), Decimal("1")),
            (Decimal("1.00"), Decimal("1")),
            (Decimal("1.01"), Decimal("1")),
            (Decimal("9.00"), Decimal("1")),
        )
    )
    widened = scale_support(support, Decimal("4"))
    ratios = [value.ratio for value in widened]

    assert ratios == sorted(ratios)


def test_scaling_observations_leaves_the_published_median_exactly_where_it_was() -> None:
    samples = observations(
        (Decimal("0.60"), Decimal("0.85"), Decimal("1.00"), Decimal("1.30"), Decimal("1.90"))
    )
    kwargs = dict(
        item_id=7,
        event_key="calibration-invariant",
        family="heavy-tank",
        prediction_as_of=BASE,
        horizon_days=30,
        basis="PRE_EVENT_MEDIAN",
        anchor_price=Decimal("100.00"),
        entry_proxy_price=Decimal("100.00"),
    )
    raw = forecast_price_distribution(observations=samples, **kwargs)
    widened = forecast_price_distribution(
        observations=scale_ratio_observations(samples, Decimal("2.5")), **kwargs
    )

    def at(forecast, probability: Decimal) -> Decimal:
        return next(
            value.ratio
            for value in forecast.quantiles
            if value.probability == probability
        )

    assert at(widened, Decimal("0.50")) == at(raw, Decimal("0.50"))
    assert at(widened, Decimal("0.10")) < at(raw, Decimal("0.10"))
    assert at(widened, Decimal("0.90")) > at(raw, Decimal("0.90"))
    assert widened.sample_count == raw.sample_count
    assert widened.total_weight == raw.total_weight
    assert widened.effective_sample_size == raw.effective_sample_size


def test_a_non_positive_factor_is_rejected() -> None:
    with pytest.raises(WalkForwardCalibrationError, match="positive finite"):
        scale_support(atoms(NARROW), Decimal("0"))


# --- policy -----------------------------------------------------------------


def test_raw_only_returns_the_observations_untouched() -> None:
    samples = observations((Decimal("0.8"), Decimal("1.0"), Decimal("1.25")))
    calibration = calibrate_dispersion(
        resolutions=narrow_pool(), as_of=BASE + timedelta(days=200)
    )
    result, codes = resolve_dispersion_observations(
        observations=samples, calibration=calibration, policy="RAW_ONLY"
    )

    assert result == samples
    assert codes == ("walk_forward_dispersion_raw_only",)


def test_shadow_reports_without_applying() -> None:
    samples = observations((Decimal("0.8"), Decimal("1.0"), Decimal("1.25")))
    calibration = calibrate_dispersion(
        resolutions=narrow_pool(), as_of=BASE + timedelta(days=200)
    )
    result, codes = resolve_dispersion_observations(
        observations=samples, calibration=calibration, policy="SHADOW"
    )

    assert calibration.applicable is True
    assert result == samples
    assert codes == ("walk_forward_dispersion_shadow_not_applied",)


def test_apply_scales_the_observations() -> None:
    samples = observations((Decimal("0.8"), Decimal("1.0"), Decimal("1.25")))
    calibration = calibrate_dispersion(
        resolutions=narrow_pool(), as_of=BASE + timedelta(days=200)
    )
    result, codes = resolve_dispersion_observations(
        observations=samples, calibration=calibration, policy="APPLY"
    )

    assert codes == ("walk_forward_dispersion_applied",)
    assert result != samples
    assert result[1].ratio == Decimal("1.0")


def test_apply_without_a_usable_calibration_changes_nothing() -> None:
    samples = observations((Decimal("0.8"), Decimal("1.0"), Decimal("1.25")))
    refused = calibrate_dispersion(
        resolutions=narrow_pool(), as_of=BASE + timedelta(days=40)
    )
    result, codes = resolve_dispersion_observations(
        observations=samples, calibration=refused, policy="APPLY"
    )

    assert result == samples
    assert codes == (
        "walk_forward_dispersion_not_applied_insufficient_resolved_sample",
    )


def test_apply_without_any_calibration_at_all_changes_nothing() -> None:
    samples = observations((Decimal("0.8"), Decimal("1.0"), Decimal("1.25")))
    result, codes = resolve_dispersion_observations(
        observations=samples, calibration=None, policy="APPLY"
    )

    assert result == samples
    assert codes == ("walk_forward_dispersion_not_applied_unavailable",)


def test_an_unsupported_policy_is_rejected() -> None:
    with pytest.raises(WalkForwardCalibrationError, match="unsupported dispersion"):
        resolve_dispersion_observations(
            observations=(), calibration=None, policy="ALWAYS_WIDEN"
        )


# --- the out-of-time report -------------------------------------------------


def test_the_walk_forward_report_scores_raw_and_calibrated_on_the_same_rows() -> None:
    report = evaluate_walk_forward_calibration(
        resolutions=narrow_pool(),
        config=WalkForwardCalibrationConfig(
            minimum_resolved_forecasts=3, minimum_distinct_items=2
        ),
    )

    assert report.trial_count == 9
    assert report.calibrated_trial_count > 0
    assert report.raw_uniformity_criterion is not None
    assert report.calibrated_uniformity_criterion is not None
    assert dict(report.status_counts)["INSUFFICIENT_RESOLVED_SAMPLE"] > 0
    assert [value.nominal_coverage for value in report.coverage] == [
        Decimal("0.50"),
        Decimal("0.80"),
    ]
    for summary in report.coverage:
        assert summary.trial_count == 9
        assert summary.calibrated_covered_count >= summary.raw_covered_count


def test_an_empty_pool_reports_nothing_rather_than_dividing_by_zero() -> None:
    report = evaluate_walk_forward_calibration(resolutions=())

    assert report.trial_count == 0
    assert report.raw_uniformity_criterion is None
    assert report.mean_applied_factor is None
    assert "walk_forward_no_resolutions_to_replay" in report.explanation_codes
    assert (
        "walk_forward_no_row_had_a_usable_calibration_sample"
        in report.explanation_codes
    )


def test_the_default_configuration_refuses_on_a_nine_row_pool() -> None:
    # Nine rows across three items never has eight *closed* labels available at
    # any decision instant, so the shipped defaults never fire on this cohort.
    report = evaluate_walk_forward_calibration(resolutions=narrow_pool())

    assert report.calibrated_trial_count == 0
    assert report.raw_uniformity_criterion == report.calibrated_uniformity_criterion


# --- the criterion ----------------------------------------------------------


def test_a_perfectly_uniform_sample_hits_the_criterion_floor() -> None:
    count = 8
    values = tuple(
        (Decimal(2 * index) - Decimal("1")) / (Decimal("2") * Decimal(count))
        for index in range(1, count + 1)
    )

    assert uniformity_criterion(values) == Decimal("1") / (
        Decimal("12") * Decimal(count)
    )


def test_a_u_shaped_sample_scores_worse_than_a_uniform_one() -> None:
    uniform = tuple(
        (Decimal(2 * index) - Decimal("1")) / Decimal("8") for index in range(1, 5)
    )
    u_shaped = (Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1"))

    assert uniformity_criterion(u_shaped) > uniformity_criterion(uniform)


def test_the_criterion_needs_at_least_one_value() -> None:
    with pytest.raises(WalkForwardCalibrationError, match="at least one"):
        uniformity_criterion(())


# --- input discipline -------------------------------------------------------


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(WalkForwardCalibrationError, match="timezone-aware"):
        ResolvedRatioForecast(
            item_id=1,
            event_key="naive",
            horizon_days=30,
            basis="PRE_EVENT_MEDIAN",
            prediction_as_of=datetime(2026, 1, 1),
            outcome_observed_at=BASE + timedelta(days=30),
            realized_ratio=Decimal("1"),
            support=atoms(NARROW),
        )


def test_an_outcome_that_precedes_its_forecast_is_rejected() -> None:
    with pytest.raises(WalkForwardCalibrationError, match="must be later"):
        ResolvedRatioForecast(
            item_id=1,
            event_key="backwards",
            horizon_days=30,
            basis="PRE_EVENT_MEDIAN",
            prediction_as_of=BASE + timedelta(days=30),
            outcome_observed_at=BASE,
            realized_ratio=Decimal("1"),
            support=atoms(NARROW),
        )


def test_a_support_that_does_not_sum_to_one_is_rejected() -> None:
    broken = (
        PredictiveRatioAtom(
            ratio=Decimal("1.0"),
            weight_share=Decimal("0.4"),
            cumulative_weight_share=Decimal("0.4"),
        ),
    )
    with pytest.raises(WalkForwardCalibrationError, match="sum to one"):
        ResolvedRatioForecast(
            item_id=1,
            event_key="unnormalized",
            horizon_days=30,
            basis="PRE_EVENT_MEDIAN",
            prediction_as_of=BASE,
            outcome_observed_at=BASE + timedelta(days=30),
            realized_ratio=Decimal("1"),
            support=broken,
        )


def test_an_empty_support_is_rejected() -> None:
    with pytest.raises(WalkForwardCalibrationError, match="non-empty"):
        ResolvedRatioForecast(
            item_id=1,
            event_key="empty",
            horizon_days=30,
            basis="PRE_EVENT_MEDIAN",
            prediction_as_of=BASE,
            outcome_observed_at=BASE + timedelta(days=30),
            realized_ratio=Decimal("1"),
            support=(),
        )


def test_a_negative_embargo_is_rejected() -> None:
    with pytest.raises(WalkForwardCalibrationError, match="non-negative"):
        WalkForwardCalibrationConfig(embargo_days=-1)


def test_the_calibration_carries_its_version() -> None:
    result = calibrate_dispersion(resolutions=(), as_of=BASE)

    assert isinstance(result, DispersionCalibration)
    assert result.version == WALK_FORWARD_CALIBRATION_VERSION
    assert result.status == "INSUFFICIENT_RESOLVED_SAMPLE"


# --- support reach ----------------------------------------------------------


def test_scaling_a_two_atom_support_can_only_move_the_upper_atom() -> None:
    """The median convention makes the correction one-sided on an even support.

    With two equally weighted atoms the no-interpolation inverse CDF puts the
    median on the lower one, so it is the fixed point and widening pushes only
    upward. This is a real limit of the correction and is asserted rather than
    worked around: moving the centre would move the published median quantile.
    """

    support = atoms(((Decimal("0.80"), Decimal("1")), (Decimal("1.25"), Decimal("1"))))
    widened = scale_support(support, Decimal("3"))

    assert widened[0].ratio == Decimal("0.80")
    assert widened[1].ratio > Decimal("1.25")


def test_the_report_counts_how_often_the_support_reaches_the_realization() -> None:
    report = evaluate_walk_forward_calibration(
        resolutions=narrow_pool(),
        config=WalkForwardCalibrationConfig(
            minimum_resolved_forecasts=3, minimum_distinct_items=2
        ),
    )
    reach = report.support_reach

    assert reach.trial_count == 9
    assert reach.support_atom_counts == ((3, 9),)
    assert (
        reach.raw_below_count + reach.raw_inside_count + reach.raw_above_count == 9
    )
    assert reach.raw_below_count == 3
    assert reach.raw_above_count == 3


def test_a_support_that_never_reaches_the_realization_says_so() -> None:
    pool = tuple(
        resolution(
            item_id=1 + index % 3,
            event=f"event-{index}",
            day=index * 5,
            realized=Decimal("5.00"),
        )
        for index in range(9)
    )
    report = evaluate_walk_forward_calibration(
        resolutions=pool,
        config=WalkForwardCalibrationConfig(
            minimum_resolved_forecasts=3, minimum_distinct_items=2
        ),
    )

    assert report.support_reach.raw_inside_count == 0
    assert (
        "walk_forward_support_never_reached_the_realization"
        in report.explanation_codes
    )
    assert (
        "walk_forward_support_too_coarse_for_dispersion_to_be_the_defect"
        in report.explanation_codes
    )


def test_a_support_with_an_interior_is_not_flagged_as_too_coarse() -> None:
    wide = atoms(tuple((Decimal("0.5") + Decimal(index) / Decimal("10"), Decimal("1")) for index in range(10)))
    pool = tuple(
        resolution(
            item_id=1 + index % 3,
            event=f"event-{index}",
            day=index * 5,
            realized=Decimal("0.95"),
            support=wide,
        )
        for index in range(9)
    )
    report = evaluate_walk_forward_calibration(resolutions=pool)

    assert report.support_reach.support_atom_counts == ((10, 9),)
    assert report.support_reach.raw_inside_count == 9
    assert (
        "walk_forward_support_too_coarse_for_dispersion_to_be_the_defect"
        not in report.explanation_codes
    )


# --- location bias ----------------------------------------------------------


def test_location_bias_is_measured_on_both_sides_of_the_centre() -> None:
    report = evaluate_walk_forward_calibration(resolutions=narrow_pool())
    bias = report.location_bias

    assert bias.trial_count == 9
    assert bias.below_centre_count == 4
    assert bias.at_centre_count == 1
    assert bias.above_centre_count == 4
    assert bias.median_transform is not None
    # Exactly one half in exact arithmetic; a third is not representable.
    assert abs(bias.median_transform - Decimal("0.5")) <= Decimal("1E-20")
    assert (
        "walk_forward_realizations_fall_entirely_on_one_side"
        not in report.explanation_codes
    )


def test_realizations_entirely_on_one_side_are_flagged_as_a_location_defect() -> None:
    pool = tuple(
        resolution(
            item_id=1 + index % 3,
            event=f"event-{index}",
            day=index * 5,
            realized=Decimal("0.10"),
        )
        for index in range(9)
    )
    report = evaluate_walk_forward_calibration(resolutions=pool)
    bias = report.location_bias

    assert bias.below_centre_count == 9
    assert bias.above_centre_count == 0
    assert bias.median_transform == Decimal("0")
    assert (
        "walk_forward_realizations_fall_entirely_on_one_side"
        in report.explanation_codes
    )


def test_location_bias_is_reported_but_never_corrected() -> None:
    """No policy and no factor reaches the centre; only the counts are published."""

    samples = observations((Decimal("0.8"), Decimal("1.0"), Decimal("1.25")))
    calibration = calibrate_dispersion(
        resolutions=narrow_pool(), as_of=BASE + timedelta(days=200)
    )
    applied, _ = resolve_dispersion_observations(
        observations=samples, calibration=calibration, policy="APPLY"
    )

    assert calibration.factor is not None
    assert applied[1].ratio == samples[1].ratio == Decimal("1.0")


# --- the transform memo -----------------------------------------------------


def test_a_shared_memo_cannot_change_an_answer() -> None:
    """The contract that makes the replay's speed-up safe.

    A resolution's transform under a factor depends only on that resolution and
    that factor, so sharing one memo across every decision instant must give
    exactly what computing each in isolation gives.
    """

    pool = narrow_pool()
    shared: dict = {}
    moments = [BASE + timedelta(days=offset) for offset in (60, 100, 140, 200)]

    for moment in moments:
        fresh = calibrate_dispersion(resolutions=pool, as_of=moment, config=LOOSE_FIT)
        memoized = calibrate_dispersion(
            resolutions=pool, as_of=moment, config=LOOSE_FIT, transform_cache=shared
        )
        assert memoized == fresh

    assert shared, "the memo should have been populated"


def test_the_memo_is_reused_rather_than_regrown() -> None:
    pool = narrow_pool()
    shared: dict = {}
    calibrate_dispersion(
        resolutions=pool,
        as_of=BASE + timedelta(days=200),
        config=LOOSE_FIT,
        transform_cache=shared,
    )
    size = len(shared)
    calibrate_dispersion(
        resolutions=pool,
        as_of=BASE + timedelta(days=200),
        config=LOOSE_FIT,
        transform_cache=shared,
    )

    assert len(shared) == size


def test_the_replay_matches_an_uncached_fit_at_every_instant() -> None:
    pool = narrow_pool()
    report = evaluate_walk_forward_calibration(resolutions=pool, config=LOOSE_FIT)

    for row in report.trials:
        standalone = calibrate_dispersion(
            resolutions=pool, as_of=row.prediction_as_of, config=LOOSE_FIT
        )
        expected = standalone.factor if standalone.applicable else None
        assert row.factor == expected
        assert row.calibration_status == standalone.status
