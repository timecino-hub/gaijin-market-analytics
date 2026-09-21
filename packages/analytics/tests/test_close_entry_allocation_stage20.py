"""Stage 20 — position sizing and the finite-capital close-entry portfolio."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.close_entry_allocation import (
    CLOSE_ENTRY_ALLOCATION_VERSION,
    AllocationConfig,
    CloseEntryAllocationError,
    CloseEntryCandidate,
    ReturnAtom,
    compare_allocation_rules,
    simulate_close_entry_portfolio,
    size_position,
)


BASE = datetime(2026, 1, 1, tzinfo=UTC)


def day(offset: int) -> datetime:
    return BASE + timedelta(days=offset)


def atoms(pairs: tuple[tuple[str, str], ...]) -> tuple[ReturnAtom, ...]:
    return tuple(
        ReturnAtom(net_roi=Decimal(roi), weight_share=Decimal(weight))
        for roi, weight in pairs
    )


WINNING = atoms((("-0.20", "0.4"), ("0.60", "0.6")))
LOSING = atoms((("-0.40", "0.6"), ("0.20", "0.4")))


def candidate(
    *,
    item_id: int = 1,
    key: str = "event",
    entry: int = 0,
    hold: int = 90,
    distribution=WINNING,
    supported: bool = True,
    realized: str | None = "0.20",
) -> CloseEntryCandidate:
    return CloseEntryCandidate(
        item_id=item_id,
        event_key=key,
        horizon_days=hold,
        entry_at=day(entry),
        exit_at=day(entry + hold),
        distribution=distribution,
        distribution_supported=supported,
        realized_net_roi=Decimal(realized) if realized is not None else None,
    )


# --- sizing -----------------------------------------------------------------


def test_a_positive_edge_is_sized_and_capped() -> None:
    result = size_position(candidate=candidate())

    assert result.status == "SIZED"
    assert result.fraction > Decimal("0")
    assert result.fraction <= AllocationConfig().maximum_position_fraction
    assert result.unconstrained_fraction is not None
    assert result.expected_log_growth is not None
    assert "close_entry_allocation_fractional_kelly_capped" in result.explanation_codes


def test_the_kelly_fraction_scales_the_optimum_down() -> None:
    config = AllocationConfig(
        maximum_position_fraction=Decimal("1.0"),
        kelly_fraction=Decimal("0.25"),
        fraction_grid_step=Decimal("0.01"),
    )
    result = size_position(candidate=candidate(), config=config)

    assert result.unconstrained_fraction is not None
    assert result.fraction == result.unconstrained_fraction * Decimal("0.25")


def test_a_negative_edge_is_not_sized() -> None:
    result = size_position(candidate=candidate(distribution=LOSING))

    assert result.status == "NO_POSITIVE_EDGE"
    assert result.fraction == Decimal("0")
    assert (
        "close_entry_allocation_no_positive_log_growth" in result.explanation_codes
    )


def test_an_unsupported_distribution_is_refused_rather_than_sized() -> None:
    """Stage 16 found these supports carry two to four atoms."""

    result = size_position(candidate=candidate(supported=False))

    assert result.status == "UNSUPPORTED_DISTRIBUTION"
    assert result.fraction == Decimal("0")
    assert (
        "close_entry_allocation_distribution_not_self_supported"
        in result.explanation_codes
    )


def test_a_missing_distribution_is_refused() -> None:
    result = size_position(candidate=candidate(distribution=()))

    assert result.status == "NO_DISTRIBUTION"
    assert result.fraction == Decimal("0")


def test_a_size_below_the_floor_is_not_taken() -> None:
    config = AllocationConfig(minimum_position_fraction=Decimal("0.09"))
    thin = atoms((("-0.20", "0.49"), ("0.21", "0.51")))
    result = size_position(candidate=candidate(distribution=thin), config=config)

    assert result.status == "NO_POSITIVE_EDGE"
    assert (
        "close_entry_allocation_below_minimum_position_size"
        in result.explanation_codes
    )


def test_equal_weight_ignores_the_distribution_shape() -> None:
    first = size_position(candidate=candidate(), rule="EQUAL_WEIGHT")
    steeper = atoms((("-0.10", "0.2"), ("0.90", "0.8")))
    second = size_position(
        candidate=candidate(distribution=steeper), rule="EQUAL_WEIGHT"
    )

    assert first.fraction == second.fraction == AllocationConfig().equal_weight_fraction
    assert (
        "close_entry_allocation_equal_weight_ignores_the_distribution"
        in first.explanation_codes
    )


def test_equal_weight_still_refuses_a_losing_distribution() -> None:
    result = size_position(candidate=candidate(distribution=LOSING), rule="EQUAL_WEIGHT")

    assert result.status == "NO_POSITIVE_EDGE"


# --- the portfolio ----------------------------------------------------------


def test_capital_compounds_through_a_single_position() -> None:
    config = AllocationConfig(
        starting_capital=Decimal("1000.00"),
        maximum_position_fraction=Decimal("0.50"),
        equal_weight_fraction=Decimal("0.50"),
        minimum_position_fraction=Decimal("0.10"),
    )
    result = simulate_close_entry_portfolio(
        candidates=(candidate(realized="0.20"),),
        rule="EQUAL_WEIGHT",
        config=config,
    )

    assert result.closed_count == 1
    assert result.positions[0].stake == Decimal("500.00")
    assert result.ending_capital == Decimal("1100.000")
    assert result.total_return == Decimal("0.100")


def test_an_open_position_is_carried_at_cost_not_at_its_future_proceeds() -> None:
    """Sizing the second position must not see the first one's outcome."""

    config = AllocationConfig(
        starting_capital=Decimal("1000.00"),
        maximum_position_fraction=Decimal("0.50"),
        equal_weight_fraction=Decimal("0.50"),
        minimum_position_fraction=Decimal("0.10"),
    )
    lucky = simulate_close_entry_portfolio(
        candidates=(
            candidate(item_id=1, key="first", entry=0, hold=90, realized="5.00"),
            candidate(item_id=2, key="second", entry=10, hold=90, realized="0.10"),
        ),
        rule="EQUAL_WEIGHT",
        config=config,
    )
    plain = simulate_close_entry_portfolio(
        candidates=(
            candidate(item_id=1, key="first", entry=0, hold=90, realized="0.00"),
            candidate(item_id=2, key="second", entry=10, hold=90, realized="0.10"),
        ),
        rule="EQUAL_WEIGHT",
        config=config,
    )
    second_of = lambda result: next(
        value for value in result.positions if value.event_key == "second"
    )

    assert second_of(lucky).equity_at_entry == second_of(plain).equity_at_entry
    assert second_of(lucky).stake == second_of(plain).stake


def test_capital_returns_only_when_a_position_actually_exits() -> None:
    config = AllocationConfig(
        starting_capital=Decimal("1000.00"),
        maximum_position_fraction=Decimal("1.0"),
        equal_weight_fraction=Decimal("1.0"),
        minimum_position_fraction=Decimal("0.50"),
    )
    blocked = simulate_close_entry_portfolio(
        candidates=(
            candidate(item_id=1, key="first", entry=0, hold=90),
            candidate(item_id=2, key="second", entry=30, hold=90),
        ),
        rule="EQUAL_WEIGHT",
        config=config,
    )
    sequential = simulate_close_entry_portfolio(
        candidates=(
            candidate(item_id=1, key="first", entry=0, hold=90),
            candidate(item_id=2, key="second", entry=95, hold=90),
        ),
        rule="EQUAL_WEIGHT",
        config=config,
    )

    assert blocked.closed_count == 1
    assert blocked.skipped_no_capital_count == 1
    assert (
        "close_entry_allocation_capital_bound_at_least_once"
        in blocked.explanation_codes
    )
    assert sequential.closed_count == 2
    assert sequential.skipped_no_capital_count == 0


def test_the_concurrency_cap_is_enforced_and_counted() -> None:
    config = AllocationConfig(
        starting_capital=Decimal("10000.00"),
        maximum_concurrent_positions=2,
        equal_weight_fraction=Decimal("0.10"),
        maximum_position_fraction=Decimal("0.10"),
    )
    result = simulate_close_entry_portfolio(
        candidates=tuple(
            candidate(item_id=index + 1, key=f"e{index}", entry=index, hold=90)
            for index in range(5)
        ),
        rule="EQUAL_WEIGHT",
        config=config,
    )

    assert result.peak_concurrent_positions == 2
    assert result.skipped_concurrency_count == 3
    assert (
        "close_entry_allocation_concurrency_cap_bound_at_least_once"
        in result.explanation_codes
    )


def test_a_candidate_with_no_outcome_is_recorded_and_takes_no_capital() -> None:
    result = simulate_close_entry_portfolio(
        candidates=(candidate(realized=None),), rule="EQUAL_WEIGHT"
    )

    assert result.outcome_unavailable_count == 1
    assert result.closed_count == 0
    assert result.ending_capital == AllocationConfig().starting_capital


def test_refused_candidates_are_recorded_rather_than_dropped() -> None:
    result = simulate_close_entry_portfolio(
        candidates=(
            candidate(item_id=1, key="losing", distribution=LOSING),
            candidate(item_id=2, key="unsupported", entry=1, supported=False),
        ),
        rule="EQUAL_WEIGHT",
    )

    assert len(result.positions) == 2
    assert result.skipped_not_sized_count == 2
    assert result.closed_count == 0


def test_positions_are_walked_in_close_order_regardless_of_input_order() -> None:
    rows = (
        candidate(item_id=2, key="late", entry=100, hold=30),
        candidate(item_id=1, key="early", entry=0, hold=30),
    )
    result = simulate_close_entry_portfolio(candidates=rows, rule="EQUAL_WEIGHT")

    assert [value.event_key for value in result.positions] == ["early", "late"]


def test_an_empty_portfolio_reports_nothing_rather_than_dividing_by_zero() -> None:
    result = simulate_close_entry_portfolio(candidates=())

    assert result.closed_count == 0
    assert result.support_share is None
    assert result.mean_deployed_fraction is None
    assert result.total_return == Decimal("0")
    assert "close_entry_allocation_no_candidates" in result.explanation_codes


def test_the_result_carries_its_version_and_refuses_a_fill_claim() -> None:
    result = simulate_close_entry_portfolio(candidates=(candidate(),))

    assert result.version == CLOSE_ENTRY_ALLOCATION_VERSION
    assert (
        "close_entry_allocation_research_only_not_a_fill_claim"
        in result.explanation_codes
    )
    assert any(
        "would_move_under_a_real_order" in code
        for code in result.explanation_codes
    )


# --- the comparison ---------------------------------------------------------


def test_every_arm_sees_the_same_candidates() -> None:
    rows = tuple(
        candidate(item_id=index + 1, key=f"e{index}", entry=index * 100, hold=90)
        for index in range(4)
    )
    result = compare_allocation_rules(candidates=rows)

    assert result.candidate_count == 4
    assert [arm.rule for arm in result.arms] == ["FRACTIONAL_KELLY", "EQUAL_WEIGHT"]
    for arm in result.arms:
        assert [value.event_key for value in arm.positions] == [
            f"e{index}" for index in range(4)
        ]
    assert (
        "close_entry_allocation_selection_identical_across_arms"
        in result.explanation_codes
    )


def test_a_smaller_stake_moves_less_capital_on_the_same_wins() -> None:
    rows = tuple(
        candidate(item_id=index + 1, key=f"e{index}", entry=index * 100, hold=90)
        for index in range(4)
    )
    config = AllocationConfig(
        maximum_position_fraction=Decimal("0.20"),
        equal_weight_fraction=Decimal("0.20"),
        kelly_fraction=Decimal("0.10"),
    )
    result = compare_allocation_rules(candidates=rows, config=config)
    arms = {arm.rule: arm for arm in result.arms}

    assert arms["FRACTIONAL_KELLY"].closed_count == arms["EQUAL_WEIGHT"].closed_count
    assert arms["FRACTIONAL_KELLY"].total_return < arms["EQUAL_WEIGHT"].total_return


# --- input discipline -------------------------------------------------------


def test_a_loss_beyond_the_whole_stake_is_rejected() -> None:
    with pytest.raises(CloseEntryAllocationError, match="cannot lose more"):
        ReturnAtom(net_roi=Decimal("-1"), weight_share=Decimal("1"))


def test_a_distribution_that_does_not_sum_to_one_is_rejected() -> None:
    with pytest.raises(CloseEntryAllocationError, match="sum to one"):
        candidate(distribution=atoms((("0.10", "0.3"),)))


def test_an_exit_before_its_entry_is_rejected() -> None:
    with pytest.raises(CloseEntryAllocationError, match="must be later"):
        CloseEntryCandidate(
            item_id=1,
            event_key="backwards",
            horizon_days=90,
            entry_at=day(90),
            exit_at=day(0),
            distribution=WINNING,
            distribution_supported=True,
            realized_net_roi=Decimal("0.1"),
        )


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(CloseEntryAllocationError, match="timezone-aware"):
        CloseEntryCandidate(
            item_id=1,
            event_key="naive",
            horizon_days=90,
            entry_at=datetime(2026, 1, 1),
            exit_at=day(90),
            distribution=WINNING,
            distribution_supported=True,
            realized_net_roi=Decimal("0.1"),
        )


def test_an_unsupported_sizing_rule_is_rejected() -> None:
    with pytest.raises(CloseEntryAllocationError, match="unsupported sizing rule"):
        size_position(candidate=candidate(), rule="ALL_IN")


def test_a_floor_above_the_cap_is_rejected() -> None:
    with pytest.raises(CloseEntryAllocationError, match="cannot exceed"):
        AllocationConfig(
            minimum_position_fraction=Decimal("0.5"),
            maximum_position_fraction=Decimal("0.1"),
        )
