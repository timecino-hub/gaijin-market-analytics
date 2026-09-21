from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.hierarchical_recovery import (
    HistoricalReturnOutcomeEvidence,
)
from gaijin_market_analytics.price_distribution import (
    PriceDistributionConfig,
    PriceDistributionError,
    PriceRatioObservation,
    evaluate_price_distribution_outcome,
    family_ratio_observations,
    forecast_price_distribution,
    item_local_ratio_observations,
)
from gaijin_market_analytics.supply_cycle_recovery import (
    SupplyEventRecovery,
    SupplyRecoveryCheckpoint,
)


PREDICTION_AS_OF = datetime(2026, 6, 1, tzinfo=UTC)
ANCHOR = Decimal("100")
ENTRY = Decimal("80")


def _observation(
    ratio: str,
    *,
    weight: str = "1",
    event_key: str | None = None,
    item_id: int = 7,
    observed_at: datetime | None = None,
    basis: str = "PRE_EVENT_MEDIAN",
    source: str = "ITEM_LOCAL",
    horizon_days: int = 60,
) -> PriceRatioObservation:
    return PriceRatioObservation(
        source_item_id=item_id,
        source_event_key=event_key or f"event-{ratio}",
        horizon_days=horizon_days,
        ratio=Decimal(ratio),
        weight=Decimal(weight),
        observed_at=observed_at or PREDICTION_AS_OF - timedelta(days=30),
        basis=basis,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
    )


def _forecast(
    observations,
    *,
    anchor: Decimal | None = ANCHOR,
    entry: Decimal | None = ENTRY,
    basis: str = "PRE_EVENT_MEDIAN",
    config: PriceDistributionConfig | None = None,
):
    return forecast_price_distribution(
        item_id=7,
        event_key="target-event",
        family="SUMMER",
        prediction_as_of=PREDICTION_AS_OF,
        horizon_days=60,
        basis=basis,  # type: ignore[arg-type]
        anchor_price=anchor,
        entry_proxy_price=entry,
        observations=observations,
        config=config,
    )


def _four_samples():
    return tuple(_observation(value) for value in ("0.8", "1.0", "1.2", "1.5"))


def test_forecast_builds_a_weighted_empirical_distribution():
    forecast = _forecast(_four_samples())
    assert forecast.available is True
    assert forecast.status == "AVAILABLE"
    assert forecast.sample_count == 4
    assert forecast.total_weight == Decimal("4")
    assert forecast.effective_sample_size == Decimal("4")
    assert [atom.ratio for atom in forecast.support] == [
        Decimal("0.8"),
        Decimal("1.0"),
        Decimal("1.2"),
        Decimal("1.5"),
    ]
    assert [atom.cumulative_weight_share for atom in forecast.support] == [
        Decimal("0.25"),
        Decimal("0.50"),
        Decimal("0.75"),
        Decimal("1.00"),
    ]


def test_quantiles_never_interpolate_between_observed_ratios():
    forecast = _forecast(_four_samples())
    observed = {Decimal("0.8"), Decimal("1.0"), Decimal("1.2"), Decimal("1.5")}
    assert all(quantile.ratio in observed for quantile in forecast.quantiles)
    by_probability = {
        quantile.probability: quantile.ratio for quantile in forecast.quantiles
    }
    assert by_probability[Decimal("0.25")] == Decimal("0.8")
    assert by_probability[Decimal("0.50")] == Decimal("1.0")
    assert by_probability[Decimal("0.75")] == Decimal("1.2")


def test_quantile_levels_beyond_the_effective_sample_are_marked_unsupported():
    forecast = _forecast(_four_samples())
    assert forecast.supported_tail_probability == Decimal("0.2")
    supported = {
        quantile.probability: quantile.supported for quantile in forecast.quantiles
    }
    assert supported[Decimal("0.10")] is False
    assert supported[Decimal("0.25")] is True
    assert supported[Decimal("0.50")] is True
    assert supported[Decimal("0.75")] is True
    assert supported[Decimal("0.90")] is False
    assert (
        "price_distribution_tail_quantiles_unsupported" in forecast.explanation_codes
    )


def test_two_observations_support_only_the_median_level():
    forecast = _forecast((_observation("0.9"), _observation("1.3")))
    assert forecast.effective_sample_size == Decimal("2")
    supported = [
        quantile.probability
        for quantile in forecast.quantiles
        if quantile.supported
    ]
    assert supported == [Decimal("0.50")]


def test_fee_adjusted_profit_probability_is_a_weighted_frequency():
    forecast = _forecast(_four_samples())
    # 0.8 -> 80 gross -> 68.00 proceeds, below the 80 entry; the other three clear it.
    assert forecast.probability_net_profit_positive == Decimal("0.75")
    quantile = next(
        value for value in forecast.quantiles if value.probability == Decimal("0.50")
    )
    assert quantile.price == Decimal("100.0")
    assert quantile.sale_proceeds == Decimal("85.00")
    assert quantile.net_profit == Decimal("5.00")


def test_weights_move_the_distribution_and_the_effective_sample_size():
    ratios = ("0.8", "1.2", "1.5")
    neutral = tuple(
        _observation(value, event_key=f"e{index}")
        for index, value in enumerate(ratios)
    )
    weighted = tuple(
        _observation(value, weight="2" if value == "0.8" else "1", event_key=f"e{index}")
        for index, value in enumerate(ratios)
    )

    def median_ratio(forecast):
        return next(
            value.ratio
            for value in forecast.quantiles
            if value.probability == Decimal("0.50")
        )

    neutral_forecast = _forecast(neutral)
    weighted_forecast = _forecast(weighted)
    assert neutral_forecast.effective_sample_size == Decimal("3")
    assert weighted_forecast.total_weight == Decimal("4")
    assert weighted_forecast.effective_sample_size == Decimal("16") / Decimal("6")
    assert median_ratio(neutral_forecast) == Decimal("1.2")
    assert median_ratio(weighted_forecast) == Decimal("0.8")
    assert neutral_forecast.probability_net_profit_positive == Decimal("2") / Decimal("3")
    assert weighted_forecast.probability_net_profit_positive == Decimal("0.5")


def test_expected_roi_and_loss_tail_are_reported_separately():
    forecast = _forecast(_four_samples())
    assert forecast.expected_net_roi == Decimal("0.1953125")
    assert forecast.median_net_roi == Decimal("0.0625")
    assert forecast.loss_tail_probability == Decimal("0.25")
    assert forecast.expected_shortfall_net_roi == Decimal("-0.15")


def test_loss_tail_uses_partial_weight_at_the_boundary_observation():
    forecast = _forecast(
        _four_samples(),
        config=PriceDistributionConfig(loss_tail_probability=Decimal("0.5")),
    )
    # Worst two ROIs are -0.15 and 0.0625; their equal-weight mean is -0.04375.
    assert forecast.expected_shortfall_net_roi == Decimal("-0.04375")


def test_a_single_observation_is_not_treated_as_a_distribution():
    forecast = _forecast((_observation("1.2"),))
    assert forecast.available is False
    assert forecast.status == "INSUFFICIENT_SAMPLE"
    assert forecast.quantiles == ()
    assert forecast.probability_net_profit_positive is None
    assert "insufficient_price_distribution_sample" in forecast.explanation_codes


def test_one_dominant_weight_is_rejected_as_an_effective_sample_of_one():
    forecast = _forecast(
        (
            _observation("0.8", weight="0.02", event_key="a"),
            _observation("1.5", weight="1", event_key="b"),
        )
    )
    assert forecast.status == "INSUFFICIENT_EFFECTIVE_SAMPLE"
    assert forecast.available is False
    assert (
        "insufficient_effective_price_distribution_sample"
        in forecast.explanation_codes
    )


def test_missing_anchor_or_entry_price_does_not_produce_a_distribution():
    assert _forecast(_four_samples(), anchor=None).status == "ANCHOR_UNAVAILABLE"
    assert _forecast(_four_samples(), entry=None).status == "ENTRY_UNAVAILABLE"


def test_observations_after_the_prediction_time_are_rejected():
    with pytest.raises(PriceDistributionError):
        _forecast(
            (
                _observation("1.0", event_key="a"),
                _observation(
                    "1.2",
                    event_key="b",
                    observed_at=PREDICTION_AS_OF + timedelta(days=1),
                ),
            )
        )


def test_mismatched_horizons_are_rejected():
    with pytest.raises(PriceDistributionError):
        _forecast(
            (
                _observation("1.0", event_key="a"),
                _observation("1.2", event_key="b", horizon_days=30),
            )
        )


def test_incompatible_ratio_bases_are_never_pooled():
    forecast = _forecast(
        (
            _observation("1.0", event_key="a"),
            _observation("1.2", event_key="b", basis="ENTRY_PROXY"),
        )
    )
    assert forecast.status == "MIXED_RATIO_BASIS"
    assert forecast.available is False
    assert "price_distribution_ratio_basis_mismatch" in forecast.explanation_codes


def test_entry_proxy_ratios_must_be_anchored_on_the_entry_price():
    samples = tuple(
        _observation(value, basis="ENTRY_PROXY", source="FAMILY_CROSS_ITEM")
        for value in ("1.0", "1.2")
    )
    with pytest.raises(PriceDistributionError):
        _forecast(samples, basis="ENTRY_PROXY")
    forecast = _forecast(samples, basis="ENTRY_PROXY", anchor=ENTRY)
    assert forecast.available is True
    assert forecast.family_sample_count == 2


def test_outcome_scoring_reports_calibration_and_proper_scores():
    forecast = _forecast(_four_samples())
    outcome = evaluate_price_distribution_outcome(
        forecast=forecast,
        realized_exit_price=Decimal("110"),
        outcome_observed_at=PREDICTION_AS_OF + timedelta(days=60),
    )
    assert outcome.realized_ratio == Decimal("1.1")
    assert outcome.realized_sale_proceeds == Decimal("93.50")
    assert outcome.realized_net_roi == Decimal("0.16875")
    assert outcome.realized_profitable is True
    assert outcome.probability_integral_transform == Decimal("0.50")
    assert outcome.brier_score == Decimal("0.0625")
    median_loss = next(
        value
        for value in outcome.pinball_losses
        if value.probability == Decimal("0.50")
    )
    assert median_loss.pinball_loss == Decimal("0.053125")
    assert median_loss.supported is True


def test_probability_integral_transform_uses_mid_rank_at_an_observed_atom():
    forecast = _forecast(_four_samples())
    outcome = evaluate_price_distribution_outcome(
        forecast=forecast,
        realized_exit_price=Decimal("100"),
        outcome_observed_at=PREDICTION_AS_OF + timedelta(days=60),
    )
    assert outcome.realized_ratio == Decimal("1")
    # One atom below, one atom exactly at the realized ratio: 0.25 + 0.25/2.
    assert outcome.probability_integral_transform == Decimal("0.375")


def test_interval_coverage_is_only_claimed_for_supported_levels():
    forecast = _forecast(_four_samples())
    outcome = evaluate_price_distribution_outcome(
        forecast=forecast,
        realized_exit_price=Decimal("110"),
        outcome_observed_at=PREDICTION_AS_OF + timedelta(days=60),
    )
    by_nominal = {
        value.nominal_coverage: value for value in outcome.interval_coverages
    }
    inner = by_nominal[Decimal("0.50")]
    assert inner.supported is True
    assert inner.covered is True
    outer = by_nominal[Decimal("0.80")]
    assert outer.supported is False


def test_scoring_refuses_an_outcome_that_is_not_in_the_future():
    forecast = _forecast(_four_samples())
    with pytest.raises(PriceDistributionError):
        evaluate_price_distribution_outcome(
            forecast=forecast,
            realized_exit_price=Decimal("110"),
            outcome_observed_at=PREDICTION_AS_OF,
        )


def test_scoring_refuses_an_unavailable_forecast():
    forecast = _forecast((_observation("1.2"),))
    with pytest.raises(PriceDistributionError):
        evaluate_price_distribution_outcome(
            forecast=forecast,
            realized_exit_price=Decimal("110"),
            outcome_observed_at=PREDICTION_AS_OF + timedelta(days=60),
        )


def _recovery(
    *,
    event_key: str,
    ratio: str | None,
    observed_at: datetime | None,
    family: str = "SUMMER",
    completed: bool = True,
    eligible: bool = True,
    as_of: datetime | None = None,
) -> SupplyEventRecovery:
    opens = datetime(2025, 1, 1, tzinfo=UTC)
    closes = datetime(2025, 2, 1, tzinfo=UTC)
    return SupplyEventRecovery(
        event_key=event_key,
        family=family,
        as_of=as_of or PREDICTION_AS_OF - timedelta(days=1),
        event_visible=True,
        event_completed=completed,
        profile_eligible=eligible,
        event_opens_at=opens,
        event_closes_at=closes,
        recovery_cutoff_at=PREDICTION_AS_OF,
        next_same_family_event_opens_at=None,
        pre_window_start=opens - timedelta(days=31),
        pre_observed_day_count=10,
        pre_price_median=Decimal("100"),
        trough_at=closes,
        trough_price=Decimal("50"),
        days_to_trough=Decimal("1"),
        checkpoints=(
            SupplyRecoveryCheckpoint(
                horizon_days=60,
                target_at=closes + timedelta(days=60),
                observed_at=observed_at,
                price=Decimal("110") if ratio is not None else None,
                pre_price_ratio=Decimal(ratio) if ratio is not None else None,
                available=ratio is not None,
            ),
        ),
        thresholds=(),
        explanation_codes=(),
    )


def test_item_local_observations_mirror_the_point_model_filter():
    visible_before = datetime(2026, 1, 1, tzinfo=UTC)
    recoveries = (
        _recovery(
            event_key="kept",
            ratio="1.1",
            observed_at=visible_before - timedelta(days=1),
        ),
        _recovery(
            event_key="target-event",
            ratio="9.9",
            observed_at=visible_before - timedelta(days=1),
        ),
        _recovery(
            event_key="other-family",
            ratio="9.9",
            observed_at=visible_before - timedelta(days=1),
            family="WINTER",
        ),
        _recovery(
            event_key="not-completed",
            ratio="9.9",
            observed_at=visible_before - timedelta(days=1),
            completed=False,
        ),
        _recovery(
            event_key="not-eligible",
            ratio="9.9",
            observed_at=visible_before - timedelta(days=1),
            eligible=False,
        ),
        _recovery(
            event_key="observed-too-late",
            ratio="9.9",
            observed_at=visible_before + timedelta(days=1),
        ),
        _recovery(event_key="unavailable", ratio=None, observed_at=None),
    )
    observations = item_local_ratio_observations(
        item_id=7,
        target_event_key="target-event",
        family="SUMMER",
        horizon_days=60,
        prediction_as_of=PREDICTION_AS_OF,
        visible_before=visible_before,
        recoveries=recoveries,
    )
    assert [value.source_event_key for value in observations] == ["kept"]
    assert observations[0].ratio == Decimal("1.1")
    assert observations[0].basis == "PRE_EVENT_MEDIAN"
    assert observations[0].source == "ITEM_LOCAL"
    assert observations[0].source_item_id == 7


def test_item_local_observations_accept_explicit_comparability_weights():
    visible_before = datetime(2026, 1, 1, tzinfo=UTC)
    recoveries = (
        _recovery(
            event_key="kept",
            ratio="1.1",
            observed_at=visible_before - timedelta(days=1),
        ),
    )
    observations = item_local_ratio_observations(
        item_id=7,
        target_event_key="target-event",
        family="SUMMER",
        horizon_days=60,
        prediction_as_of=PREDICTION_AS_OF,
        visible_before=visible_before,
        recoveries=recoveries,
        weight_by_event_key={"kept": Decimal("0.4")},
    )
    assert observations[0].weight == Decimal("0.4")


def _family_evidence(
    *,
    item_id: int,
    event_key: str,
    ratio: str,
    family: str = "SUMMER",
    observed_at: datetime | None = None,
    horizon_days: int = 60,
) -> HistoricalReturnOutcomeEvidence:
    return HistoricalReturnOutcomeEvidence(
        item_id=item_id,
        family=family,
        event_key=event_key,
        horizon_days=horizon_days,
        prediction_as_of=PREDICTION_AS_OF - timedelta(days=200),
        outcome_observed_at=observed_at or PREDICTION_AS_OF - timedelta(days=100),
        entry_proxy_price=Decimal("50"),
        exit_proxy_price=Decimal("50") * Decimal(ratio),
        gross_exit_entry_ratio=Decimal(ratio),
        realized_net_roi=Decimal("0.1"),
    )


def test_family_observations_exclude_every_outcome_from_the_target_item():
    evidence = (
        _family_evidence(item_id=11, event_key="a", ratio="1.2"),
        _family_evidence(item_id=12, event_key="b", ratio="1.4"),
        _family_evidence(item_id=7, event_key="own", ratio="9.9"),
        _family_evidence(item_id=13, event_key="c", ratio="9.9", family="WINTER"),
        _family_evidence(item_id=14, event_key="d", ratio="9.9", horizon_days=30),
        _family_evidence(
            item_id=15,
            event_key="e",
            ratio="9.9",
            observed_at=PREDICTION_AS_OF + timedelta(days=1),
        ),
    )
    observations = family_ratio_observations(
        family="SUMMER",
        horizon_days=60,
        prediction_as_of=PREDICTION_AS_OF,
        exclude_item_id=7,
        prior_outcomes=evidence,
    )
    assert [value.source_event_key for value in observations] == ["a", "b"]
    assert all(value.basis == "ENTRY_PROXY" for value in observations)
    assert all(value.source == "FAMILY_CROSS_ITEM" for value in observations)


def test_configuration_rejects_unsound_quantile_settings():
    with pytest.raises(PriceDistributionError):
        PriceDistributionConfig(quantile_probabilities=(Decimal("0"),))
    with pytest.raises(PriceDistributionError):
        PriceDistributionConfig(
            quantile_probabilities=(Decimal("0.5"), Decimal("0.25"))
        )
    with pytest.raises(PriceDistributionError):
        PriceDistributionConfig(minimum_sample_count=1)
    with pytest.raises(PriceDistributionError):
        PriceDistributionConfig(loss_tail_probability=Decimal("0"))
