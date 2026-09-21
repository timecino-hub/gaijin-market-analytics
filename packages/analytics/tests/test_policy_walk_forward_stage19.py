"""Stage 19 — strict point-in-time evaluation of a withholding policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.policy_walk_forward import (
    POLICY_WALK_FORWARD_VERSION,
    PolicyDecisionRow,
    PolicyWalkForwardConfig,
    PolicyWalkForwardError,
    closed_evidence,
    evaluate_policy_walk_forward,
    select_withheld_groups,
)


BASE = datetime(2026, 1, 1, tzinfo=UTC)
STRATA = ("s30", "s60", "s90", "s180")
LOOSE = PolicyWalkForwardConfig(
    minimum_closed_rows=8,
    minimum_distinct_items=2,
    minimum_stratum_rows=2,
    minimum_qualifying_strata=2,
)


def row(
    *,
    item_id: int,
    key: str,
    group: str,
    stratum: str,
    day: int,
    profitable: bool,
    roi: str = "0.10",
    label_days: int = 30,
) -> PolicyDecisionRow:
    return PolicyDecisionRow(
        item_id=item_id,
        row_key=key,
        group=group,
        stratum=stratum,
        decided_at=BASE + timedelta(days=day),
        outcome_closed_at=BASE + timedelta(days=day + label_days),
        net_roi=Decimal(roi) if profitable else Decimal("-" + roi.lstrip("-")),
        profitable=profitable,
    )


def informative_pool() -> tuple[PolicyDecisionRow, ...]:
    """BAD loses in every stratum, GOOD wins in every stratum."""

    rows = []
    day = 0
    for index, stratum in enumerate(STRATA):
        for slot in range(4):
            rows.append(
                row(
                    item_id=1 + slot % 4,
                    key=f"good-{stratum}-{slot}",
                    group="GOOD",
                    stratum=stratum,
                    day=day,
                    profitable=slot < 3,
                )
            )
            day += 5
        for slot in range(4):
            rows.append(
                row(
                    item_id=1 + slot % 4,
                    key=f"bad-{stratum}-{slot}",
                    group="BAD",
                    stratum=stratum,
                    day=day,
                    profitable=slot < 1,
                )
            )
            day += 5
    return tuple(rows)


def one_stratum_pool() -> tuple[PolicyDecisionRow, ...]:
    """BAD loses badly, but only inside a single stratum."""

    rows = []
    day = 0
    for stratum in STRATA:
        losing = stratum == "s180"
        for slot in range(4):
            rows.append(
                row(
                    item_id=1 + slot % 4,
                    key=f"good-{stratum}-{slot}",
                    group="GOOD",
                    stratum=stratum,
                    day=day,
                    profitable=slot < 3,
                )
            )
            day += 5
        for slot in range(4):
            rows.append(
                row(
                    item_id=1 + slot % 4,
                    key=f"bad-{stratum}-{slot}",
                    group="BAD",
                    stratum=stratum,
                    day=day,
                    profitable=(slot < 3) if not losing else False,
                )
            )
            day += 5
    return tuple(rows)


LATE = BASE + timedelta(days=400)


# --- point in time ----------------------------------------------------------


def test_a_row_whose_outcome_is_still_open_is_not_evidence() -> None:
    pool = (
        row(item_id=1, key="closed", group="A", stratum="s30", day=0, profitable=True),
        row(item_id=2, key="open", group="A", stratum="s30", day=20, profitable=True),
    )
    usable, still_open, embargoed = closed_evidence(
        rows=pool, as_of=BASE + timedelta(days=40)
    )

    assert [value.row_key for value in usable] == ["closed"]
    assert still_open == 1
    assert embargoed == 0


def test_the_embargo_removes_outcomes_that_closed_too_recently() -> None:
    pool = (
        row(item_id=1, key="recent", group="A", stratum="s30", day=0, profitable=True),
    )
    usable, still_open, embargoed = closed_evidence(
        rows=pool,
        as_of=BASE + timedelta(days=33),
        config=PolicyWalkForwardConfig(embargo_days=7),
    )

    assert not usable
    assert embargoed == 1
    assert still_open == 0


def test_no_row_informs_the_selection_that_judges_it() -> None:
    pool = informative_pool()
    report = evaluate_policy_walk_forward(
        rows=pool, candidate_groups=("BAD",), config=LOOSE, policy="APPLY"
    )

    assert report.trials[0].evidence_row_count == 0
    for trial in report.trials:
        closed_before = sum(
            value.outcome_closed_at <= trial.decided_at for value in pool
        )
        assert trial.evidence_row_count <= closed_before


def test_the_selection_is_deterministic_under_reordering() -> None:
    pool = informative_pool()
    first = select_withheld_groups(
        rows=pool, as_of=LATE, candidate_groups=("BAD", "GOOD"), config=LOOSE
    )
    second = select_withheld_groups(
        rows=tuple(reversed(pool)),
        as_of=LATE,
        candidate_groups=("GOOD", "BAD"),
        config=LOOSE,
    )

    assert first.withheld_groups == second.withheld_groups
    assert first.status == second.status


# --- selection --------------------------------------------------------------


def test_a_group_that_loses_in_every_stratum_is_selected() -> None:
    result = select_withheld_groups(
        rows=informative_pool(), as_of=LATE, candidate_groups=("BAD",), config=LOOSE
    )

    assert result.status == "SELECTED"
    assert result.applicable is True
    assert result.withheld_groups == ("BAD",)
    evaluation = result.evaluations[0]
    assert evaluation.qualifying_stratum_count == 4
    assert evaluation.positive_stratum_count == 4
    assert evaluation.mean_support_gain == Decimal("0.5")
    assert result.version == POLICY_WALK_FORWARD_VERSION


def test_a_group_that_only_loses_in_one_stratum_is_refused() -> None:
    """The lesson from the real cohort: one cell must not carry a decision."""

    result = select_withheld_groups(
        rows=one_stratum_pool(), as_of=LATE, candidate_groups=("BAD",), config=LOOSE
    )

    assert result.status == "NO_GROUP_QUALIFIED"
    assert result.withheld_groups == ()
    evaluation = result.evaluations[0]
    assert evaluation.positive_stratum_count == 1
    assert evaluation.qualifying_stratum_count == 4
    assert (
        "policy_walk_forward_group_gain_not_in_most_strata"
        in evaluation.explanation_codes
    )


def test_a_group_that_wins_is_never_withheld() -> None:
    result = select_withheld_groups(
        rows=informative_pool(), as_of=LATE, candidate_groups=("GOOD",), config=LOOSE
    )

    assert result.withheld_groups == ()
    assert result.evaluations[0].mean_support_gain == Decimal("-0.5")


def test_a_small_gain_does_not_clear_the_threshold() -> None:
    result = select_withheld_groups(
        rows=informative_pool(),
        as_of=LATE,
        candidate_groups=("BAD",),
        config=PolicyWalkForwardConfig(
            minimum_closed_rows=8,
            minimum_distinct_items=2,
            minimum_stratum_rows=2,
            minimum_qualifying_strata=2,
            minimum_mean_support_gain=Decimal("0.75"),
        ),
    )

    assert result.withheld_groups == ()
    assert (
        "policy_walk_forward_group_gain_below_threshold"
        in result.evaluations[0].explanation_codes
    )


def test_thin_strata_do_not_qualify() -> None:
    result = select_withheld_groups(
        rows=informative_pool(),
        as_of=LATE,
        candidate_groups=("BAD",),
        config=PolicyWalkForwardConfig(
            minimum_closed_rows=8,
            minimum_distinct_items=2,
            minimum_stratum_rows=9,
            minimum_qualifying_strata=2,
        ),
    )

    assert result.status == "NO_GROUP_QUALIFIED"
    assert result.evaluations[0].qualifying_stratum_count == 0
    assert (
        "policy_walk_forward_group_lacks_qualifying_strata"
        in result.evaluations[0].explanation_codes
    )


def test_the_stratum_contrast_carries_its_denominators() -> None:
    result = select_withheld_groups(
        rows=informative_pool(), as_of=LATE, candidate_groups=("BAD",), config=LOOSE
    )
    contrast = result.evaluations[0].strata[0]

    assert contrast.withheld_count == 4
    assert contrast.withheld_profitable_count == 1
    assert contrast.withheld_support_share == Decimal("0.25")
    assert contrast.retained_count == 4
    assert contrast.retained_profitable_count == 3
    assert contrast.retained_support_share == Decimal("0.75")
    assert contrast.support_gain == Decimal("0.5")


# --- refusals ---------------------------------------------------------------


def test_too_little_closed_evidence_refuses() -> None:
    result = select_withheld_groups(
        rows=informative_pool(),
        as_of=BASE + timedelta(days=35),
        candidate_groups=("BAD",),
        config=LOOSE,
    )

    assert result.status == "INSUFFICIENT_CLOSED_EVIDENCE"
    assert result.applicable is False


def test_evidence_from_too_few_items_refuses() -> None:
    pool = tuple(
        row(
            item_id=1,
            key=f"only-{index}",
            group="BAD" if index % 2 else "GOOD",
            stratum=STRATA[index % 4],
            day=index,
            profitable=index % 3 == 0,
        )
        for index in range(20)
    )
    result = select_withheld_groups(
        rows=pool, as_of=LATE, candidate_groups=("BAD",), config=LOOSE
    )

    assert result.status == "INSUFFICIENT_DISTINCT_ITEMS"
    assert result.distinct_item_count == 1


def test_too_few_strata_to_control_elapsed_time_refuses() -> None:
    pool = tuple(
        row(
            item_id=1 + index % 4,
            key=f"flat-{index}",
            group="BAD" if index % 2 else "GOOD",
            stratum="s30",
            day=index,
            profitable=index % 3 == 0,
        )
        for index in range(20)
    )
    result = select_withheld_groups(
        rows=pool, as_of=LATE, candidate_groups=("BAD",), config=LOOSE
    )

    assert result.status == "INSUFFICIENT_STRATA"
    assert (
        "policy_walk_forward_too_few_strata_to_control_elapsed_time"
        in result.explanation_codes
    )


def test_the_shipped_defaults_refuse_on_a_small_pool() -> None:
    result = select_withheld_groups(
        rows=informative_pool(), as_of=LATE, candidate_groups=("BAD",)
    )

    assert result.status == "INSUFFICIENT_CLOSED_EVIDENCE"


# --- the replay -------------------------------------------------------------


def test_never_withhold_is_the_baseline_exactly() -> None:
    report = evaluate_policy_walk_forward(
        rows=informative_pool(), candidate_groups=("BAD",), config=LOOSE
    )

    assert report.withholding_trial_count == 0
    assert report.walk_forward.published_count == report.baseline.published_count
    assert report.walk_forward.support_share == report.baseline.support_share
    assert report.walk_forward.median_net_roi == report.baseline.median_net_roi
    assert (
        "policy_walk_forward_nothing_was_ever_withheld"
        in report.explanation_codes
    )


def test_applying_the_policy_trades_fewer_rows_at_a_better_rate() -> None:
    report = evaluate_policy_walk_forward(
        rows=informative_pool(),
        candidate_groups=("BAD",),
        config=LOOSE,
        policy="APPLY",
    )

    assert report.trial_count == 32
    assert report.withholding_trial_count > 0
    assert report.walk_forward.published_count < report.baseline.published_count
    assert report.walk_forward.support_share > report.baseline.support_share


def test_the_report_states_that_no_matched_comparison_exists() -> None:
    report = evaluate_policy_walk_forward(
        rows=informative_pool(), candidate_groups=("BAD",), config=LOOSE
    )

    assert any(
        "no_matched_comparison_exists" in code
        for code in report.explanation_codes
    )


def test_every_stratum_is_reported_separately() -> None:
    report = evaluate_policy_walk_forward(
        rows=informative_pool(),
        candidate_groups=("BAD",),
        config=LOOSE,
        policy="APPLY",
    )

    assert [value[0] for value in report.by_stratum] == sorted(STRATA)
    for _, baseline, walk in report.by_stratum:
        assert baseline.published_count >= walk.published_count


def test_an_empty_pool_reports_nothing_rather_than_dividing_by_zero() -> None:
    report = evaluate_policy_walk_forward(rows=(), candidate_groups=("BAD",))

    assert report.trial_count == 0
    assert report.baseline.support_share is None
    assert report.baseline.median_net_roi is None
    assert "policy_walk_forward_no_rows_to_replay" in report.explanation_codes


# --- input discipline -------------------------------------------------------


def test_an_outcome_that_closes_before_the_decision_is_rejected() -> None:
    with pytest.raises(PolicyWalkForwardError, match="must not precede"):
        PolicyDecisionRow(
            item_id=1,
            row_key="backwards",
            group="A",
            stratum="s30",
            decided_at=BASE + timedelta(days=30),
            outcome_closed_at=BASE,
            net_roi=Decimal("0.1"),
            profitable=True,
        )


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(PolicyWalkForwardError, match="timezone-aware"):
        PolicyDecisionRow(
            item_id=1,
            row_key="naive",
            group="A",
            stratum="s30",
            decided_at=datetime(2026, 1, 1),
            outcome_closed_at=BASE + timedelta(days=30),
            net_roi=Decimal("0.1"),
            profitable=True,
        )


def test_an_unsupported_policy_is_rejected() -> None:
    with pytest.raises(PolicyWalkForwardError, match="unsupported withholding"):
        evaluate_policy_walk_forward(
            rows=(), candidate_groups=("BAD",), policy="WITHHOLD_EVERYTHING"
        )


def test_a_negative_gain_threshold_is_rejected() -> None:
    with pytest.raises(PolicyWalkForwardError, match="non-negative"):
        PolicyWalkForwardConfig(minimum_mean_support_gain=Decimal("-0.1"))
