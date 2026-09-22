"""Stage 25 — the pooled ledger for the one general strategy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.strategy_evidence import (
    STRATEGY_EVIDENCE_VERSION,
    StrategyEvidenceConfig,
    StrategyEvidenceError,
    evaluate_event_outcomes,
    summarise_strategy_evidence,
)
from gaijin_market_analytics.supply_cycles import SupplyPoolEvent

BASE = datetime(2024, 1, 1, tzinfo=UTC)


def day(offset: int) -> datetime:
    return BASE + timedelta(days=offset)


def bucket(
    offset: int, price: str, *, item_id: int = 1, volume: int = 10
) -> HistoricalTradeBucketEvidence:
    return HistoricalTradeBucketEvidence(
        item_id=item_id,
        granularity="1d",
        bucket_start_utc=day(offset),
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal(price),
        reported_trade_volume=volume,
    )


def event(key: str, *, opens: int, closes: int) -> SupplyPoolEvent:
    return SupplyPoolEvent(
        event_key=key,
        family="spring",
        known_at=day(opens - 7),
        opens_at=day(opens),
        closes_at=day(closes),
    )


def series(prices: dict[int, str], *, item_id: int = 1, span: int = 400, fill: str = "100"):
    """A daily series with explicit overrides on top of a flat baseline."""

    return tuple(
        bucket(index, prices.get(index, fill), item_id=item_id) for index in range(span)
    )


def baseline_days(price: str = "100") -> dict[int, str]:
    """The pre-event window an event opening on day 20 reads for its baseline.

    Pinning it explicitly lets a test set the post-event drift to whatever it
    needs without dragging the baseline — and the drawdown filter — along.
    """

    return {index: price for index in range(10, 20)}


def outcomes_for(buckets, events, *, as_of_offset: int, item_id: int = 1, config=None):
    return evaluate_event_outcomes(
        item_id=item_id,
        as_of=day(as_of_offset),
        events=events,
        historical_buckets=buckets,
        config=config,
    )


# --- the per-event ledger ---------------------------------------------------


def test_the_entry_is_the_first_completed_day_after_the_event_closes() -> None:
    # Baseline 100 before the event, price crashes to 60 on the day it closes.
    history = series({30: "60", 31: "62", 32: "64"})
    outcome = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)[0]

    assert outcome.state == "EVALUATED"
    assert outcome.entry_day == day(30)
    assert outcome.entry_price == Decimal("60")
    assert outcome.pre_event_baseline == Decimal("100")
    assert outcome.drawdown_ratio == Decimal("0.6")
    assert outcome.passes_drawdown_filter is True


def test_a_shallow_drawdown_is_evaluated_and_kept_rather_than_discarded() -> None:
    # The rejected half is the control group; dropping it would make the
    # filter's claim unfalsifiable.
    history = series({30: "95"})
    outcome = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)[0]

    assert outcome.state == "EVALUATED"
    assert outcome.drawdown_ratio == Decimal("0.95")
    assert outcome.passes_drawdown_filter is False
    assert outcome.horizon(180) is not None


def test_the_net_return_is_after_the_sale_fee() -> None:
    history = series({30: "50"}, fill="100")
    outcome = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)[0]

    reading = outcome.horizon(180)
    assert reading is not None
    assert reading.state == "SETTLED"
    # Bought at 50, sold at 100, minus the 15% fee: 100 * 0.85 / 50 - 1.
    assert reading.net_roi == Decimal("0.7")


def test_a_horizon_that_has_not_finished_is_pending_not_zero() -> None:
    history = series({30: "50"}, span=120)
    outcome = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=120)[0]

    assert outcome.horizon(30).state == "SETTLED"
    assert outcome.horizon(60).state == "SETTLED"
    assert outcome.horizon(90).state == "PENDING"
    assert outcome.horizon(180).state == "PENDING"
    assert outcome.horizon(90).net_roi is None


def test_a_window_cut_by_the_next_supply_event_is_marked_superseded() -> None:
    history = series({30: "50"})
    events = (event("e1", opens=20, closes=30), event("e2", opens=150, closes=160))
    first = outcomes_for(history, events, as_of_offset=400)[0]

    # 30 + 180 = day 210, which is past the next event's opening on day 150.
    assert first.horizon(90).state == "SETTLED"
    assert first.horizon(180).state == "SUPERSEDED"
    assert first.horizon(180).net_roi is None
    assert first.superseded_at == day(150)


def test_an_event_still_open_at_as_of_is_not_evaluated() -> None:
    history = series({}, span=60)
    outcome = outcomes_for(history, (event("e1", opens=20, closes=50),), as_of_offset=40)[0]

    assert outcome.state == "EVENT_NOT_COMPLETED"
    assert outcome.horizons == ()


def test_too_few_pre_event_days_refuses_a_baseline_rather_than_guessing() -> None:
    # Only two completed days exist before the event opens.
    history = tuple(bucket(index, "100") for index in (18, 19)) + tuple(
        bucket(index, "60") for index in range(20, 400)
    )
    outcome = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)[0]

    assert outcome.state == "NO_PRE_EVENT_BASELINE"
    assert outcome.pre_event_baseline is None
    assert outcome.drawdown_ratio is None
    assert outcome.passes_drawdown_filter is False


def test_no_trading_after_the_close_means_no_entry_price() -> None:
    history = tuple(bucket(index, "100") for index in range(30))
    outcome = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)[0]

    assert outcome.state == "NO_ENTRY_PRICE"
    assert outcome.entry_price is None


# --- the take-profit simulation ---------------------------------------------


def test_a_net_target_is_grossed_up_for_the_fee() -> None:
    history = series({30: "100"}, fill="100")
    outcome = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)[0]
    offer = next(
        value.offer_price
        for value in outcome.take_profits
        if value.target_net_roi == Decimal("0.25")
    )

    # Netting 25% at a 15% fee needs the price 47.1% higher, not 25%.
    assert offer == Decimal("100") * Decimal("1.25") / Decimal("0.85")
    assert offer > Decimal("147")


def test_a_take_profit_that_fills_realizes_exactly_its_target() -> None:
    # Baseline 100, entry 50, the market drifts at 60 — below the 73.53 offer —
    # and spikes to 200 on day 40, ten days after the entry.
    history = series(baseline_days() | {30: "50", 40: "200"}, fill="60")
    outcome = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)[0]
    reading = next(
        value for value in outcome.take_profits if value.target_net_roi == Decimal("0.25")
    )

    assert reading.hit is True
    assert reading.days_to_hit == 10
    assert reading.pure_net_roi == Decimal("0.25")
    # The other half runs to the cap, which returned 2% here.
    assert outcome.horizon(180).net_roi == Decimal("0.02")
    assert reading.split_net_roi == Decimal("0.135")


def test_a_take_profit_that_never_fills_still_produces_the_held_outcome() -> None:
    # Never rises above the entry: the offer is never reached.
    history = series(baseline_days() | {30: "50"}, fill="40")
    outcome = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)[0]
    reading = next(
        value for value in outcome.take_profits if value.target_net_roi == Decimal("0.25")
    )
    held = outcome.horizon(180)

    assert reading.hit is False
    assert reading.days_to_hit is None
    # A miss is not a hole — the position is sold at the cap and that number
    # is what both exit columns report.
    assert reading.pure_net_roi == held.net_roi
    assert reading.split_net_roi == held.net_roi


def test_the_entry_day_is_the_purchase_and_never_also_the_exit() -> None:
    # One lone day of trading after the close. Reading the exit off it would
    # report a flat return where the truth is that nothing was observed.
    history = tuple(bucket(index, "100") for index in range(20)) + (bucket(30, "50"),)
    outcome = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)[0]

    assert outcome.entry_day == day(30)
    assert outcome.entry_price == Decimal("50")
    assert outcome.horizon(30).state == "NO_EXIT_PRICE"
    assert outcome.horizon(30).net_roi is None
    assert all(value.hit is False for value in outcome.take_profits)


# --- the pooled summary -----------------------------------------------------


def two_vehicle_cohort(*, as_of_offset: int = 400):
    """Two vehicles, one selected and profitable, one rejected and flat."""

    deep = outcomes_for(
        series({30: "50"}),
        (event("a1", opens=20, closes=30),),
        as_of_offset=as_of_offset,
        item_id=1,
    )
    shallow = outcomes_for(
        series({30: "95"}, item_id=2),
        (event("b1", opens=20, closes=30),),
        as_of_offset=as_of_offset,
        item_id=2,
    )
    return deep + shallow


def test_the_selected_and_rejected_halves_add_up_to_the_whole() -> None:
    evidence = summarise_strategy_evidence(
        as_of=day(400), outcomes=two_vehicle_cohort()
    )
    horizon = next(value for value in evidence.horizons if value.horizon_days == 180)

    assert horizon.all_events.sample_size == 2
    assert horizon.selected.sample_size == 1
    assert horizon.rejected.sample_size == 1
    assert (
        horizon.selected.sample_size + horizon.rejected.sample_size
        == horizon.all_events.sample_size
    )
    assert evidence.version == STRATEGY_EVIDENCE_VERSION


def test_every_summary_carries_the_vehicle_count_behind_its_events() -> None:
    # Two events from one vehicle are not two independent trials, and the
    # summary has to make that visible.
    history = series({30: "50", 200: "50"}, span=600)
    outcomes = outcomes_for(
        history,
        (event("a1", opens=20, closes=30), event("a2", opens=190, closes=200)),
        as_of_offset=600,
    )
    evidence = summarise_strategy_evidence(as_of=day(600), outcomes=outcomes)
    horizon = next(value for value in evidence.horizons if value.horizon_days == 90)

    assert horizon.all_events.sample_size == 2
    assert horizon.all_events.vehicle_count == 1
    assert (
        "strategy_evidence_events_repeat_vehicles_and_are_not_independent"
        in evidence.explanation_codes
    )


def test_pending_events_are_counted_rather_than_dropped() -> None:
    evidence = summarise_strategy_evidence(
        as_of=day(120), outcomes=two_vehicle_cohort(as_of_offset=120)
    )
    horizon = next(value for value in evidence.horizons if value.horizon_days == 180)

    assert horizon.all_events.sample_size == 0
    assert horizon.pending_count == 2
    assert (
        "strategy_evidence_some_events_have_not_finished_their_horizon"
        in evidence.explanation_codes
    )


def test_events_without_a_baseline_are_reported_not_folded_into_a_side() -> None:
    history = tuple(bucket(index, "100") for index in (18, 19)) + tuple(
        bucket(index, "60") for index in range(20, 400)
    )
    outcomes = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)
    evidence = summarise_strategy_evidence(as_of=day(400), outcomes=outcomes)

    assert evidence.completed_event_count == 1
    assert evidence.evaluated_event_count == 0
    assert evidence.no_baseline_count == 1
    assert evidence.selected_event_count == 0
    assert evidence.rejected_event_count == 0


def test_the_support_ratio_counts_outcomes_above_zero_after_the_fee() -> None:
    # Bought at 100, sold at 110: a 10% gross gain is a loss after a 15% fee.
    history = series({30: "100", 210: "110"}, fill="100")
    outcomes = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)
    evidence = summarise_strategy_evidence(as_of=day(400), outcomes=outcomes)
    horizon = next(value for value in evidence.horizons if value.horizon_days == 180)

    assert horizon.all_events.sample_size == 1
    assert horizon.all_events.median < Decimal("0")
    assert horizon.all_events.support_ratio == Decimal("0")


def test_the_percentile_is_an_outcome_that_actually_happened() -> None:
    # Ten events with returns spread far apart: an interpolated tenth
    # percentile would invent a number between two of them.
    outcomes: list = []
    for index in range(10):
        exit_price = str(50 + index * 50)
        history = series({30: "50", 210: exit_price}, item_id=index + 1, fill="100")
        outcomes.extend(
            outcomes_for(
                history,
                (event(f"e{index}", opens=20, closes=30),),
                as_of_offset=400,
                item_id=index + 1,
            )
        )
    evidence = summarise_strategy_evidence(as_of=day(400), outcomes=tuple(outcomes))
    horizon = next(value for value in evidence.horizons if value.horizon_days == 180)
    realized = sorted(
        (value.horizon(180).net_roi for value in outcomes),
    )

    assert horizon.selected.sample_size == 10
    assert horizon.selected.tenth_percentile in realized
    assert horizon.selected.ninetieth_percentile in realized
    assert horizon.selected.tenth_percentile == realized[0]
    assert horizon.selected.ninetieth_percentile == realized[8]


# --- the exit frontier ------------------------------------------------------


def test_the_frontier_scores_every_rule_on_the_same_events() -> None:
    history = series({30: "50", 100: "80"})
    outcomes = outcomes_for(history, (event("e1", opens=20, closes=30),), as_of_offset=400)
    evidence = summarise_strategy_evidence(as_of=day(400), outcomes=outcomes)

    sizes = {rule.summary.sample_size for rule in evidence.exit_frontier}
    assert sizes == {1}
    assert evidence.exit_frontier[0].kind == "FIXED_HOLD"
    assert evidence.exit_frontier[0].target_net_roi is None
    kinds = {rule.kind for rule in evidence.exit_frontier}
    assert kinds == {"FIXED_HOLD", "TAKE_PROFIT", "SPLIT_HALF"}


def test_a_lower_target_fills_at_least_as_often_as_a_higher_one() -> None:
    # The structural trade-off the strategy is built around: hit rate is bought
    # with target. If this ever inverts, the simulation is wrong.
    outcomes: list = []
    for index in range(8):
        # Entry at 50 against a baseline of 100, a market that otherwise sits
        # at 40, and one peak that reaches 60 + 20i. The offers run from 67.6
        # at +15% net to 82.4 at +40%, so the peaks separate the targets.
        peak = str(60 + index * 20)
        history = series(
            baseline_days() | {30: "50", 100: peak}, item_id=index + 1, fill="40"
        )
        outcomes.extend(
            outcomes_for(
                history,
                (event(f"e{index}", opens=20, closes=30),),
                as_of_offset=400,
                item_id=index + 1,
            )
        )
    evidence = summarise_strategy_evidence(as_of=day(400), outcomes=tuple(outcomes))
    ratios = [
        (rule.target_net_roi, rule.hit_ratio)
        for rule in evidence.exit_frontier
        if rule.kind == "TAKE_PROFIT"
    ]

    assert len(ratios) == 5
    for (_, higher), (_, lower) in zip(ratios, ratios[1:]):
        assert higher >= lower
    # And the ladder has to actually separate, or the assertion above is vacuous.
    assert ratios[0][1] > ratios[-1][1]


def test_the_frontier_excludes_events_whose_holding_cap_has_not_closed() -> None:
    # A take-profit measured over a partial window would be compared against
    # one measured over a full one.
    settled = outcomes_for(
        series({30: "50"}),
        (event("a1", opens=20, closes=30),),
        as_of_offset=400,
        item_id=1,
    )
    pending = outcomes_for(
        series({330: "50"}, item_id=2, span=400),
        (event("b1", opens=320, closes=330),),
        as_of_offset=400,
        item_id=2,
    )
    evidence = summarise_strategy_evidence(
        as_of=day(400), outcomes=settled + pending
    )

    assert evidence.selected_event_count == 2
    assert all(rule.summary.sample_size == 1 for rule in evidence.exit_frontier)


# --- refusals ---------------------------------------------------------------


def test_a_naive_as_of_is_refused() -> None:
    with pytest.raises(StrategyEvidenceError):
        evaluate_event_outcomes(
            item_id=1,
            as_of=datetime(2024, 6, 1),
            events=(),
            historical_buckets=(),
        )


def test_the_primary_horizon_must_be_one_of_the_reported_horizons() -> None:
    with pytest.raises(StrategyEvidenceError):
        StrategyEvidenceConfig(horizons=(30, 90), primary_horizon_days=180)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"drawdown_threshold": Decimal("0")},
        {"drawdown_threshold": Decimal("1.5")},
        {"fee_rate": Decimal("1")},
        {"horizons": ()},
        {"horizons": (30, 30)},
        {"take_profit_targets": ()},
        {"take_profit_targets": (Decimal("-0.1"),)},
        {"minimum_pre_event_days": 0},
    ],
)
def test_impossible_configurations_are_refused(kwargs) -> None:
    with pytest.raises(StrategyEvidenceError):
        StrategyEvidenceConfig(**kwargs)


def test_an_empty_cohort_summarises_to_zero_rather_than_failing() -> None:
    evidence = summarise_strategy_evidence(as_of=day(400), outcomes=())

    assert evidence.completed_event_count == 0
    assert evidence.vehicle_count == 0
    assert all(value.all_events.sample_size == 0 for value in evidence.horizons)
    assert all(value.all_events.median is None for value in evidence.horizons)
    assert all(rule.summary.sample_size == 0 for rule in evidence.exit_frontier)
