from __future__ import annotations

from decimal import Decimal

from gaijin_market_analytics.backtesting.calibration_contracts import (
    CalibrationCaseStatus,
    CalibrationCohortSummary,
    CalibrationExitReason,
    CalibrationSplit,
    ComponentCorrelation,
    OpportunityCalibrationCase,
    OpportunityCalibrationConfig,
    ScoreBinSummary,
)


_CORRELATION_COMPONENTS = (
    "score",
    "profitability_score",
    "liquidity_score",
    "stability_score",
    "data_confidence_score",
    "freshness_score",
    "risk_penalty",
)


def build_calibration_statistics(
    cases: tuple[OpportunityCalibrationCase, ...],
    config: OpportunityCalibrationConfig,
) -> tuple[
    tuple[CalibrationCohortSummary, ...],
    tuple[ScoreBinSummary, ...],
    tuple[ComponentCorrelation, ...],
]:
    cohort_keys = _cohort_keys(config)
    score_bins = tuple(
        summary
        for split, horizon in cohort_keys
        for summary in _score_bin_summaries(
            _filter_cases(cases, split, horizon),
            split=split,
            forward_horizon_days=horizon,
            edges=config.score_bin_edges,
        )
    )
    correlations = tuple(
        ComponentCorrelation(
            split=split,
            forward_horizon_days=horizon,
            component_name=component,
            sample_count=len(_realized_pairs(cohort, component)),
            spearman_with_net_roi=_spearman(_realized_pairs(cohort, component)),
        )
        for split, horizon in cohort_keys
        for cohort in (_filter_cases(cases, split, horizon),)
        for component in _CORRELATION_COMPONENTS
    )
    cohorts = tuple(
        _summarize_cohort(
            _filter_cases(cases, split, horizon),
            split=split,
            forward_horizon_days=horizon,
            bins=tuple(
                value
                for value in score_bins
                if value.split == split
                and value.forward_horizon_days == horizon
            ),
        )
        for split, horizon in cohort_keys
    )
    return cohorts, score_bins, correlations


def _cohort_keys(
    config: OpportunityCalibrationConfig,
) -> tuple[tuple[CalibrationSplit | None, int | None], ...]:
    keys: list[tuple[CalibrationSplit | None, int | None]] = [
        (None, horizon) for horizon in config.forward_horizon_days
    ]
    keys.extend(
        (split, horizon)
        for split in CalibrationSplit
        for horizon in config.forward_horizon_days
    )
    return tuple(keys)


def _filter_cases(
    cases: tuple[OpportunityCalibrationCase, ...],
    split: CalibrationSplit | None,
    forward_horizon_days: int | None,
) -> tuple[OpportunityCalibrationCase, ...]:
    return tuple(
        case
        for case in cases
        if (split is None or case.split == split)
        and (
            forward_horizon_days is None
            or case.forward_horizon_days == forward_horizon_days
        )
    )


def _score_bin_summaries(
    cases: tuple[OpportunityCalibrationCase, ...],
    *,
    split: CalibrationSplit | None,
    forward_horizon_days: int | None,
    edges: tuple[Decimal, ...],
) -> tuple[ScoreBinSummary, ...]:
    results = []
    for index, (lower, upper) in enumerate(zip(edges, edges[1:])):
        includes_upper = index == len(edges) - 2
        in_bin = tuple(
            case
            for case in cases
            if case.score is not None
            and case.score >= lower
            and (case.score <= upper if includes_upper else case.score < upper)
        )
        realized = tuple(case for case in in_bin if case.net_roi is not None)
        rois = tuple(case.net_roi for case in realized if case.net_roi is not None)
        positive_count = sum(1 for case in realized if case.positive_return is True)
        results.append(
            ScoreBinSummary(
                split=split,
                forward_horizon_days=forward_horizon_days,
                lower_bound=lower,
                upper_bound=upper,
                includes_upper_bound=includes_upper,
                scored_case_count=len(in_bin),
                eligible_case_count=sum(
                    1 for case in in_bin if case.opportunity_eligible is True
                ),
                entry_executed_count=sum(1 for case in in_bin if case.entry_executed),
                realized_trade_count=len(realized),
                positive_return_count=positive_count,
                positive_return_rate=_rate(positive_count, len(realized)),
                mean_net_roi=_mean_decimal(rois),
                median_net_roi=_median_decimal(rois),
            )
        )
    return tuple(results)


def _summarize_cohort(
    cases: tuple[OpportunityCalibrationCase, ...],
    *,
    split: CalibrationSplit | None,
    forward_horizon_days: int | None,
    bins: tuple[ScoreBinSummary, ...],
) -> CalibrationCohortSummary:
    scored = tuple(case for case in cases if case.score is not None)
    entries = tuple(case for case in cases if case.entry_executed)
    realized = tuple(case for case in cases if case.net_roi is not None)
    eligible_realized = tuple(
        case for case in realized if case.opportunity_eligible is True
    )
    positive_count = sum(1 for case in realized if case.positive_return is True)
    eligible_positive_count = sum(
        1 for case in eligible_realized if case.positive_return is True
    )
    net_rois = tuple(case.net_roi for case in realized if case.net_roi is not None)
    holding = tuple(
        case.holding_seconds
        for case in realized
        if case.holding_seconds is not None
    )
    score_corr = _spearman(_realized_pairs(cases, "score"))
    profitability_corr = _spearman(
        _realized_pairs(cases, "profitability_score")
    )
    lift = (
        score_corr - profitability_corr
        if score_corr is not None and profitability_corr is not None
        else None
    )
    monotonicity = _adjacent_bin_monotonicity(bins)
    portfolio = _portfolio_metrics(eligible_realized)
    assessment_codes = _assessment_codes(
        realized_count=len(realized),
        eligible_realized_count=len(eligible_realized),
        score_corr=score_corr,
        lift=lift,
        monotonicity=monotonicity,
    )
    return CalibrationCohortSummary(
        split=split,
        forward_horizon_days=forward_horizon_days,
        total_case_count=len(cases),
        scored_case_count=len(scored),
        eligible_case_count=sum(
            1 for case in cases if case.opportunity_eligible is True
        ),
        complete_future_case_count=sum(
            1 for case in cases if case.future_window_complete
        ),
        entry_executed_count=len(entries),
        realized_trade_count=len(realized),
        target_exit_count=sum(
            1
            for case in realized
            if case.exit_reason == CalibrationExitReason.TARGET_CONFIRMED
        ),
        terminal_liquidation_count=sum(
            1
            for case in realized
            if case.exit_reason == CalibrationExitReason.TERMINAL_LIQUIDATION
        ),
        unresolved_exit_count=sum(
            1 for case in cases if case.status == CalibrationCaseStatus.EXIT_UNAVAILABLE
        ),
        positive_return_count=positive_count,
        eligible_realized_trade_count=len(eligible_realized),
        eligible_positive_return_count=eligible_positive_count,
        eligible_positive_return_rate=_rate(
            eligible_positive_count,
            len(eligible_realized),
        ),
        eligible_total_net_profit=_sum_decimal(
            tuple(
                case.net_profit
                for case in eligible_realized
                if case.net_profit is not None
            )
        ),
        entry_execution_rate=_rate(len(entries), len(cases)),
        realized_trade_rate=_rate(len(realized), len(cases)),
        positive_return_rate=_rate(positive_count, len(realized)),
        total_net_profit=_sum_decimal(
            tuple(case.net_profit for case in realized if case.net_profit is not None)
        ),
        mean_net_roi=_mean_decimal(net_rois),
        median_net_roi=_median_decimal(net_rois),
        mean_holding_seconds=_mean_int(holding),
        median_holding_seconds=_median_int(holding),
        peak_concurrent_entry_cost=portfolio[0],
        ending_equity=portfolio[1],
        maximum_drawdown=portfolio[2],
        maximum_drawdown_rate=portfolio[3],
        score_roi_spearman=score_corr,
        profitability_baseline_spearman=profitability_corr,
        score_correlation_lift_over_profitability=lift,
        adjacent_bin_monotonicity_rate=monotonicity,
        assessment_codes=assessment_codes,
    )


def _realized_pairs(
    cases: tuple[OpportunityCalibrationCase, ...],
    component_name: str,
) -> tuple[tuple[Decimal, Decimal], ...]:
    pairs = []
    for case in cases:
        component = getattr(case, component_name)
        if component is not None and case.net_roi is not None:
            pairs.append((component, case.net_roi))
    return tuple(pairs)


def _spearman(
    pairs: tuple[tuple[Decimal, Decimal], ...],
) -> Decimal | None:
    if len(pairs) < 3:
        return None
    x_ranks = _average_ranks(tuple(value[0] for value in pairs))
    y_ranks = _average_ranks(tuple(value[1] for value in pairs))
    x_mean = sum(x_ranks, Decimal("0")) / Decimal(len(x_ranks))
    y_mean = sum(y_ranks, Decimal("0")) / Decimal(len(y_ranks))
    covariance = sum(
        (x - x_mean) * (y - y_mean)
        for x, y in zip(x_ranks, y_ranks)
    )
    x_variance = sum((value - x_mean) ** 2 for value in x_ranks)
    y_variance = sum((value - y_mean) ** 2 for value in y_ranks)
    if x_variance == Decimal("0") or y_variance == Decimal("0"):
        return None
    result = covariance / (x_variance * y_variance).sqrt()
    return result.quantize(Decimal("0.0001"))


def _average_ranks(values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    ordered = sorted(enumerate(values), key=lambda value: (value[1], value[0]))
    ranks = [Decimal("0")] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (
            Decimal(index + 1) + Decimal(end)
        ) / Decimal("2")
        for original_index, _ in ordered[index:end]:
            ranks[original_index] = average_rank
        index = end
    return tuple(ranks)


def _adjacent_bin_monotonicity(
    bins: tuple[ScoreBinSummary, ...],
) -> Decimal | None:
    populated = tuple(value for value in bins if value.mean_net_roi is not None)
    if len(populated) < 2:
        return None
    comparisons = len(populated) - 1
    non_decreasing = sum(
        1
        for lower, upper in zip(populated, populated[1:])
        if upper.mean_net_roi is not None
        and lower.mean_net_roi is not None
        and upper.mean_net_roi >= lower.mean_net_roi
    )
    return (Decimal(non_decreasing) / Decimal(comparisons)).quantize(
        Decimal("0.0001")
    )


def _portfolio_metrics(
    eligible_realized: tuple[OpportunityCalibrationCase, ...],
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    if not eligible_realized:
        return None, None, None, None
    capital_events = []
    for case in eligible_realized:
        if (
            case.entry_at is None
            or case.exit_at is None
            or case.entry_price is None
            or case.net_profit is None
        ):
            continue
        capital_events.append((case.entry_at, 1, case.entry_price))
        capital_events.append((case.exit_at, 0, -case.entry_price))
    active = Decimal("0")
    peak_cost = Decimal("0")
    for _, _, delta in sorted(capital_events, key=lambda value: (value[0], value[1])):
        active += delta
        peak_cost = max(peak_cost, active)
    if peak_cost <= Decimal("0"):
        return None, None, None, None

    equity = peak_cost
    peak_equity = equity
    maximum_drawdown = Decimal("0")
    maximum_drawdown_rate = Decimal("0")
    for case in sorted(
        eligible_realized,
        key=lambda value: (
            value.exit_at,
            value.item_id,
            value.cutoff_as_of,
        ),
    ):
        if case.net_profit is None:
            continue
        equity += case.net_profit
        peak_equity = max(peak_equity, equity)
        drawdown = peak_equity - equity
        maximum_drawdown = max(maximum_drawdown, drawdown)
        if peak_equity > Decimal("0"):
            maximum_drawdown_rate = max(
                maximum_drawdown_rate,
                drawdown / peak_equity,
            )
    return peak_cost, equity, maximum_drawdown, maximum_drawdown_rate


def _assessment_codes(
    *,
    realized_count: int,
    eligible_realized_count: int,
    score_corr: Decimal | None,
    lift: Decimal | None,
    monotonicity: Decimal | None,
) -> tuple[str, ...]:
    codes = []
    if realized_count < 10:
        codes.append("insufficient_realized_trades_for_calibration")
    if eligible_realized_count == 0:
        codes.append("no_eligible_realized_trades")
    if score_corr is None:
        codes.append("score_return_correlation_unavailable")
    elif score_corr > Decimal("0.10"):
        codes.append("higher_scores_associated_with_higher_returns")
    elif score_corr <= Decimal("0"):
        codes.append("score_return_relationship_non_positive")
    else:
        codes.append("score_return_relationship_weak_positive")
    if lift is None:
        codes.append("profitability_baseline_comparison_unavailable")
    elif lift > Decimal("0"):
        codes.append("composite_score_outperforms_profitability_rank_correlation")
    else:
        codes.append("composite_score_does_not_outperform_profitability_baseline")
    if monotonicity is None:
        codes.append("score_bin_monotonicity_unavailable")
    elif monotonicity == Decimal("1.0000"):
        codes.append("score_bins_are_monotonic")
    elif monotonicity < Decimal("0.5000"):
        codes.append("score_bins_are_not_monotonic")
    else:
        codes.append("score_bins_are_partially_monotonic")
    return tuple(codes)


def _rate(count: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return Decimal(count) / Decimal(denominator)


def _sum_decimal(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0"))


def _mean_decimal(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _median_decimal(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _mean_int(values: tuple[int, ...]) -> int | None:
    if not values:
        return None
    return sum(values) // len(values)


def _median_int(values: tuple[int, ...]) -> int | None:
    if not values:
        return None
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2
