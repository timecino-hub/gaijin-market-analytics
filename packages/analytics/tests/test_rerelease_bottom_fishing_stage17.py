"""Stage 17 — independent entry timing after a re-release."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.rerelease_bottom_fishing import (
    RERELEASE_BOTTOM_FISHING_VERSION,
    BottomFishingConfig,
    BottomFishingError,
    BottomFishingTrial,
    compare_bottom_fishing_rules,
    evaluate_bottom_fishing_outcome,
    resolve_bottom_fishing_entry,
)
from gaijin_market_analytics.supply_cycles import SupplyPoolEvent


BASE = datetime(2026, 1, 1, tzinfo=UTC)
OPENS = BASE + timedelta(days=30)
CLOSES = BASE + timedelta(days=44)


def day(offset: int) -> datetime:
    return BASE + timedelta(days=offset)


def bucket(offset: int, price: str, *, item_id: int = 1) -> HistoricalTradeBucketEvidence:
    return HistoricalTradeBucketEvidence(
        item_id=item_id,
        granularity="1d",
        bucket_start_utc=day(offset),
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal(price),
        reported_trade_volume=10,
    )


def event(key: str = "spring-2026", *, item_id: int = 1) -> SupplyPoolEvent:
    return SupplyPoolEvent(
        event_key=key,
        family="spring",
        known_at=OPENS - timedelta(days=7),
        opens_at=OPENS,
        closes_at=CLOSES,
    )


def series(prices: dict[int, str], *, item_id: int = 1, pre: str = "100"):
    """Pre-event days at a flat baseline, then the given post-close path."""

    rows = [bucket(offset, pre, item_id=item_id) for offset in range(16, 30)]
    rows += [bucket(offset, price, item_id=item_id) for offset, price in prices.items()]
    return tuple(rows)


def path(prices: list[str], *, start: int = 44, **kwargs):
    return series({start + i: p for i, p in enumerate(prices)}, **kwargs)


def entry(rule, buckets, *, as_of_offset: int = 120, config=None, next_open=None, item_id: int = 1):
    return resolve_bottom_fishing_entry(
        rule=rule,
        item_id=item_id,
        event=event(item_id=item_id),
        historical_buckets=buckets,
        as_of=day(as_of_offset),
        config=config,
        next_same_family_event_opens_at=next_open,
    )


# --- the benchmark reproduces today's fixed entry ---------------------------


def test_immediate_buys_the_first_completed_day_after_close() -> None:
    buckets = path(["70", "60", "55", "58", "62"])
    result = entry("IMMEDIATE", buckets)

    assert result.status == "ENTERED"
    assert result.entry_day_start_utc == CLOSES
    assert result.entry_price == Decimal("70")
    assert result.days_waited == 0
    assert result.entry_discount == Decimal("0")
    assert result.version == RERELEASE_BOTTOM_FISHING_VERSION


def test_a_day_that_has_not_completed_yet_is_invisible() -> None:
    """The trough can only be the lowest day that had already completed."""

    buckets = path(["70", "60", "55"])
    # The first post-close day starts at CLOSES and completes a day later, so
    # at that instant exactly one day is usable and the trough is its price.
    first = entry("TROUGH_ORACLE", buckets, as_of_offset=45)
    second = entry("TROUGH_ORACLE", buckets, as_of_offset=46)

    assert first.candidate_day_count == 1
    assert first.entry_price == Decimal("70")
    assert second.candidate_day_count == 2
    assert second.entry_price == Decimal("60")


def test_an_event_that_has_not_closed_yields_nothing() -> None:
    result = entry("IMMEDIATE", path(["70"]), as_of_offset=40)

    assert result.status == "EVENT_NOT_COMPLETED"
    assert result.entered is False
    assert "rerelease_bottom_fishing_event_still_open" in result.explanation_codes


def test_no_post_event_day_is_reported_rather_than_guessed() -> None:
    buckets = series({})
    result = entry("IMMEDIATE", buckets)

    assert result.status == "NO_POST_EVENT_EVIDENCE"
    assert result.entry_price is None


# --- drawdown trigger -------------------------------------------------------


def test_the_drawdown_trigger_fires_on_the_first_day_under_the_threshold() -> None:
    buckets = path(["90", "80", "74", "60"])
    result = entry("DRAWDOWN_TRIGGER", buckets)

    assert result.pre_event_median == Decimal("100")
    assert result.status == "ENTERED"
    assert result.entry_price == Decimal("74")
    assert result.days_waited == 2
    assert result.entry_discount == (Decimal("90") - Decimal("74")) / Decimal("90")


def test_a_drawdown_that_never_arrives_is_an_abstention_not_a_zero() -> None:
    buckets = path(["90", "88", "92", "95"])
    result = entry("DRAWDOWN_TRIGGER", buckets)

    assert result.status == "NOT_TRIGGERED"
    assert result.entered is False
    assert result.entry_price is None
    assert result.candidate_day_count == 4
    assert (
        "rerelease_bottom_fishing_rule_did_not_trigger_in_window"
        in result.explanation_codes
    )


def test_the_drawdown_trigger_refuses_without_a_pre_event_baseline() -> None:
    buckets = tuple(bucket(44 + i, p) for i, p in enumerate(["70", "60"]))
    result = entry("DRAWDOWN_TRIGGER", buckets)

    assert result.status == "NO_PRE_EVENT_BASELINE"
    assert result.pre_event_median is None


# --- stabilised and patient floor -------------------------------------------


def test_stabilised_enters_after_a_run_with_no_new_low() -> None:
    # New lows on days 1 and 2, then three days without one.
    buckets = path(["90", "80", "70", "72", "75", "71"])
    result = entry("STABILISED", buckets, config=BottomFishingConfig(stabilised_days=3))

    assert result.status == "ENTERED"
    assert result.entry_price == Decimal("71")
    assert result.days_waited == 5


def test_stabilised_can_enter_on_a_path_that_only_ever_rises() -> None:
    buckets = path(["70", "75", "80", "85"])
    result = entry("STABILISED", buckets, config=BottomFishingConfig(stabilised_days=3))

    assert result.status == "ENTERED"
    assert result.days_waited == 2


def test_patient_floor_refuses_a_path_that_never_dipped() -> None:
    """The distinguishing rule: no fall means no bottom to fish for."""

    buckets = path(["70", "75", "80", "85"])
    config = BottomFishingConfig(stabilised_days=3)
    stabilised = entry("STABILISED", buckets, config=config)
    patient = entry("PATIENT_FLOOR", buckets, config=config)

    assert stabilised.status == "ENTERED"
    assert patient.status == "NOT_TRIGGERED"


def test_patient_floor_enters_once_a_low_has_been_made_and_held() -> None:
    """Confirmation costs the difference between the low and the entry.

    The low is set on day one, so the first run of three days that does not
    break it ends on day four. Entering at 76 rather than the 70 that was
    available is exactly the price of waiting for the confirmation, and the
    rule is not allowed to pretend otherwise.
    """

    buckets = path(["90", "70", "72", "74", "76"])
    result = entry(
        "PATIENT_FLOOR", buckets, config=BottomFishingConfig(stabilised_days=3)
    )

    assert result.status == "ENTERED"
    assert result.entry_price == Decimal("76")
    assert result.days_waited == 4
    assert result.window_low == Decimal("70")


# --- the oracle bound -------------------------------------------------------


def test_the_oracle_buys_the_actual_minimum_and_is_marked_unattainable() -> None:
    buckets = path(["90", "60", "75", "55", "80"])
    result = entry("TROUGH_ORACLE", buckets)

    assert result.entry_price == Decimal("55")
    assert result.window_low == Decimal("55")
    assert result.attainable is False
    assert (
        "rerelease_bottom_fishing_oracle_bound_not_attainable"
        in result.explanation_codes
    )


def test_the_oracle_breaks_ties_toward_the_earlier_day() -> None:
    buckets = path(["90", "55", "70", "55"])
    result = entry("TROUGH_ORACLE", buckets)

    assert result.entry_day_start_utc == CLOSES + timedelta(days=1)


def test_every_causal_rule_is_marked_attainable() -> None:
    buckets = path(["90", "70", "72", "74", "76"])
    for rule in ("IMMEDIATE", "DRAWDOWN_TRIGGER", "STABILISED", "PATIENT_FLOOR"):
        assert entry(rule, buckets).attainable is True


# --- the window -------------------------------------------------------------


def test_the_wait_window_closes_at_the_maximum(
) -> None:
    buckets = path(["90", "88", "86", "84", "50"])
    config = BottomFishingConfig(maximum_wait_days=3)
    result = entry("DRAWDOWN_TRIGGER", buckets, config=config)

    # The day at 50 sits past the window, so the rule abstains rather than
    # reaching forward for it.
    assert result.status == "NOT_TRIGGERED"
    assert result.candidate_day_count == 3


def test_the_next_supply_event_ends_the_window() -> None:
    buckets = path(["90", "88", "50"])
    result = entry("DRAWDOWN_TRIGGER", buckets, next_open=day(46))

    assert result.status == "NOT_TRIGGERED"
    assert (
        "rerelease_bottom_fishing_window_capped_by_next_supply_event"
        in result.explanation_codes
    )


# --- outcomes ---------------------------------------------------------------


def test_the_holding_period_runs_from_entry_for_every_rule() -> None:
    buckets = path(["90", "60", "62", "64"] + ["100"] * 40)
    config = BottomFishingConfig(holding_days=10, stabilised_days=3)
    rows = {}
    for rule in ("IMMEDIATE", "TROUGH_ORACLE"):
        e = entry(rule, buckets, config=config)
        rows[rule] = evaluate_bottom_fishing_outcome(
            entry=e, historical_buckets=buckets, as_of=day(120), config=config
        )

    assert rows["IMMEDIATE"].exit_day_start_utc == CLOSES + timedelta(days=10)
    assert rows["TROUGH_ORACLE"].exit_day_start_utc == CLOSES + timedelta(days=11)
    assert rows["IMMEDIATE"].holding_days == rows["TROUGH_ORACLE"].holding_days == 10
    # Same exit price, cheaper entry, so the oracle must score strictly better.
    assert rows["TROUGH_ORACLE"].net_roi > rows["IMMEDIATE"].net_roi


def test_an_outcome_is_scored_after_fees() -> None:
    buckets = path(["100"] + ["100"] * 40)
    config = BottomFishingConfig(holding_days=10, fee_rate=Decimal("0.15"))
    e = entry("IMMEDIATE", buckets, config=config)
    result = evaluate_bottom_fishing_outcome(
        entry=e, historical_buckets=buckets, as_of=day(120), config=config
    )

    assert result.sale_proceeds == Decimal("85.00")
    assert result.net_profit == Decimal("-15.00")
    assert result.profitable is False
    assert (
        "rerelease_bottom_fishing_exit_not_profitable_after_fee"
        in result.explanation_codes
    )


def test_an_unfinished_hold_is_not_observable_rather_than_zero() -> None:
    buckets = path(["90", "60", "62"])
    config = BottomFishingConfig(holding_days=30)
    e = entry("IMMEDIATE", buckets, config=config)
    result = evaluate_bottom_fishing_outcome(
        entry=e, historical_buckets=buckets, as_of=day(50), config=config
    )

    assert result.status == "OUTCOME_NOT_OBSERVABLE"
    assert result.net_roi is None


def test_a_hold_that_spans_the_next_supply_event_is_refused() -> None:
    buckets = path(["90"] + ["100"] * 40)
    config = BottomFishingConfig(holding_days=30)
    e = entry("IMMEDIATE", buckets, config=config)
    result = evaluate_bottom_fishing_outcome(
        entry=e,
        historical_buckets=buckets,
        as_of=day(120),
        config=config,
        next_same_family_event_opens_at=day(60),
    )

    assert result.status == "SUPERSEDED_BY_NEXT_SUPPLY_EVENT"
    assert result.net_roi is None


def test_nothing_bought_is_scored_as_nothing_bought() -> None:
    buckets = path(["90", "88", "92"])
    e = entry("DRAWDOWN_TRIGGER", buckets)
    result = evaluate_bottom_fishing_outcome(
        entry=e, historical_buckets=buckets, as_of=day(120)
    )

    assert e.status == "NOT_TRIGGERED"
    assert result.status == "NOT_ENTERED"
    assert result.profitable is None


# --- the comparison ---------------------------------------------------------


def _trial(rule, buckets, *, item_id, config, as_of_offset=200):
    e = resolve_bottom_fishing_entry(
        rule=rule,
        item_id=item_id,
        event=event(item_id=item_id),
        historical_buckets=buckets,
        as_of=day(as_of_offset),
        config=config,
    )
    o = evaluate_bottom_fishing_outcome(
        entry=e,
        historical_buckets=buckets,
        as_of=day(as_of_offset),
        config=config,
    )
    return BottomFishingTrial(entry=e, outcome=o)


def test_abstention_is_counted_beside_the_return() -> None:
    config = BottomFishingConfig(holding_days=10, stabilised_days=3)
    # Two events fall far enough to trigger, one does not.
    paths = {
        1: ["90", "60", "62", "64"] + ["95"] * 30,
        2: ["90", "55", "58", "60"] + ["95"] * 30,
        3: ["90", "89", "91", "92"] + ["95"] * 30,
    }
    trials = [
        _trial(rule, path(p, item_id=i), item_id=i, config=config)
        for i, p in paths.items()
        for rule in ("IMMEDIATE", "DRAWDOWN_TRIGGER")
    ]
    result = compare_bottom_fishing_rules(trials=trials)
    by_rule = {value.rule: value for value in result.summaries}

    assert result.event_count == 3
    assert by_rule["IMMEDIATE"].entered_count == 3
    assert by_rule["DRAWDOWN_TRIGGER"].entered_count == 2
    assert by_rule["DRAWDOWN_TRIGGER"].not_triggered_count == 1
    assert by_rule["DRAWDOWN_TRIGGER"].trigger_share == Decimal("2") / Decimal("3")


def test_the_matched_comparison_uses_only_the_shared_events() -> None:
    """A rule that trades a subset is compared on that subset, not on its own.

    The third event rises throughout and is profitable for the immediate entry.
    The drawdown rule never trades it, so including it in the benchmark column
    would credit the rule with a trade it declined.
    """

    config = BottomFishingConfig(holding_days=10, stabilised_days=3)
    paths = {
        1: ["90", "60", "62", "64"] + ["95"] * 30,
        2: ["90", "55", "58", "60"] + ["95"] * 30,
        3: ["90", "89", "91", "92"] + ["400"] * 30,
    }
    trials = [
        _trial(rule, path(p, item_id=i), item_id=i, config=config)
        for i, p in paths.items()
        for rule in ("IMMEDIATE", "DRAWDOWN_TRIGGER")
    ]
    result = compare_bottom_fishing_rules(trials=trials)
    matched = {value.rule: value for value in result.matched}["DRAWDOWN_TRIGGER"]
    summaries = {value.rule: value for value in result.summaries}

    assert matched.matched_count == 2
    # Across all three events the benchmark caught the one big winner.
    assert summaries["IMMEDIATE"].observed_count == 3
    assert summaries["IMMEDIATE"].profitable_count == 1
    # On the two events the rule actually traded, the benchmark caught nothing,
    # so the rule may not claim credit for the event it declined.
    assert matched.benchmark_profitable_count == 0
    assert matched.benchmark_support_share == Decimal("0")
    assert matched.rule_support_share == Decimal("1")
    assert matched.median_net_roi_difference == (
        matched.rule_median_net_roi - matched.benchmark_median_net_roi
    )


def test_the_benchmark_is_not_matched_against_itself() -> None:
    config = BottomFishingConfig(holding_days=10)
    trials = [
        _trial("IMMEDIATE", path(["90"] + ["95"] * 30, item_id=1), item_id=1, config=config)
    ]
    result = compare_bottom_fishing_rules(trials=trials)

    assert [value.rule for value in result.matched] == []
    assert [value.rule for value in result.summaries] == ["IMMEDIATE"]


def test_the_comparison_flags_an_unattainable_row() -> None:
    config = BottomFishingConfig(holding_days=10)
    buckets = path(["90", "60"] + ["95"] * 30)
    trials = [
        _trial(rule, buckets, item_id=1, config=config)
        for rule in ("IMMEDIATE", "TROUGH_ORACLE")
    ]
    result = compare_bottom_fishing_rules(trials=trials)
    oracle = {value.rule: value for value in result.matched}["TROUGH_ORACLE"]

    assert oracle.attainable is False
    assert (
        "rerelease_bottom_fishing_table_includes_an_unattainable_bound"
        in result.explanation_codes
    )


def test_an_empty_comparison_says_so() -> None:
    result = compare_bottom_fishing_rules(trials=())

    assert result.event_count == 0
    assert result.summaries == ()
    assert "rerelease_bottom_fishing_no_trials_to_compare" in result.explanation_codes


# --- input discipline -------------------------------------------------------


def test_a_stabilised_window_longer_than_the_wait_is_rejected() -> None:
    with pytest.raises(BottomFishingError, match="could never fire"):
        BottomFishingConfig(maximum_wait_days=2, stabilised_days=3)


def test_an_out_of_range_trigger_ratio_is_rejected() -> None:
    with pytest.raises(BottomFishingError, match="in \\(0, 1\\]"):
        BottomFishingConfig(drawdown_trigger_ratio=Decimal("1.5"))


def test_an_unsupported_rule_is_rejected() -> None:
    with pytest.raises(BottomFishingError, match="unsupported entry rule"):
        entry("BUY_THE_DIP", path(["90"]))


def test_a_naive_as_of_is_rejected() -> None:
    with pytest.raises(BottomFishingError, match="timezone-aware"):
        resolve_bottom_fishing_entry(
            rule="IMMEDIATE",
            item_id=1,
            event=event(),
            historical_buckets=path(["90"]),
            as_of=datetime(2026, 6, 1),
        )


# --- the windowed bid -------------------------------------------------------


def hour(offset_days: int, hour_of_day: int, price: str, *, item_id: int = 1):
    return HistoricalTradeBucketEvidence(
        item_id=item_id,
        granularity="1h",
        bucket_start_utc=day(offset_days) + timedelta(hours=hour_of_day),
        bucket_duration=timedelta(hours=1),
        vwap_price=Decimal(price),
        reported_trade_volume=4,
    )


BID_CONFIG = BottomFishingConfig(
    maximum_wait_days=7,
    bid_target_drop=Decimal("0.30"),
    bid_tolerance=Decimal("0.05"),
    holding_days=30,
)


def test_the_daily_proxy_can_never_fill_at_the_bid() -> None:
    """A structural consequence worth pinning, not a defect of this case.

    Under the daily proxy the fill condition is that the day's price reached
    the bid, so the day's price is at or below it, so ``min(bid, price)`` is
    always the price. The bid rule therefore degenerates to a drawdown trigger
    at a different threshold, and only an intraday low can express a bid that
    someone sold down into.
    """

    buckets = path(["90", "85", "72", "80"])
    result = entry("WINDOWED_BID_ABSTAIN", buckets, config=BID_CONFIG)

    assert result.status == "ENTERED"
    assert result.pre_event_median == Decimal("100")
    assert result.bid_price == Decimal("73.500")
    assert result.filled_at_bid is False
    assert result.entry_price == Decimal("72")
    assert result.days_waited == 2


def test_a_bid_that_is_never_reached_abstains_in_mode_a() -> None:
    buckets = path(["90", "88", "85", "84", "86", "85", "87"])
    result = entry("WINDOWED_BID_ABSTAIN", buckets, config=BID_CONFIG)

    assert result.status == "NOT_TRIGGERED"
    assert result.entered is False
    assert result.entry_price is None
    # The bid it rested at is still reported, so an abstention says what was
    # asked for and missed rather than only that nothing happened.
    assert result.bid_price == Decimal("73.500")
    assert result.filled_at_bid is False


def test_the_same_path_crosses_the_spread_in_mode_b() -> None:
    """Mode B refuses to miss the position and pays the market to get it."""

    buckets = path(["90", "88", "85", "84", "86", "85", "87"])
    strict = entry("WINDOWED_BID_ABSTAIN", buckets, config=BID_CONFIG)
    forced = entry("WINDOWED_BID_FORCED", buckets, config=BID_CONFIG)

    assert strict.status == "NOT_TRIGGERED"
    assert forced.status == "ENTERED"
    assert forced.filled_at_bid is False
    assert forced.entry_price == Decimal("87")
    assert forced.days_waited == 6
    assert (
        "rerelease_bottom_fishing_bid_expired_bought_at_market"
        in forced.explanation_codes
    )


def test_mode_b_takes_the_reached_price_rather_than_waiting_out_the_window() -> None:
    buckets = path(["90", "72", "80", "85"])
    forced = entry("WINDOWED_BID_FORCED", buckets, config=BID_CONFIG)

    assert forced.entry_price == Decimal("72")
    assert forced.days_waited == 1


def test_the_window_bounds_how_long_the_bid_rests() -> None:
    # The bid is only reached on day 8, past a seven day window.
    buckets = path(["90", "88", "87", "86", "85", "84", "83", "60"])
    result = entry("WINDOWED_BID_ABSTAIN", buckets, config=BID_CONFIG)

    assert result.status == "NOT_TRIGGERED"
    assert result.candidate_day_count == 7


def test_the_hourly_proxy_fills_where_the_daily_average_does_not() -> None:
    """A day can trade through a bid and still average above it.

    The daily proxy calls that a miss, the hourly one calls it a fill, and the
    difference is exactly the measurement error the choice introduces.
    """

    pre = [bucket(offset, "100") for offset in range(16, 30)]
    post = [bucket(44, "90"), bucket(46, "88")]
    # Day 45 trades between 70 and 96: its average clears the bid, one hour of
    # it does not.
    hours = [hour(45, index, price) for index, price in enumerate(["96", "70", "95", "94"])]
    buckets = tuple(pre + post + hours)

    daily = entry("WINDOWED_BID_ABSTAIN", buckets, config=BID_CONFIG)
    hourly = entry(
        "WINDOWED_BID_ABSTAIN",
        buckets,
        config=BottomFishingConfig(
            maximum_wait_days=7,
            bid_target_drop=Decimal("0.30"),
            bid_tolerance=Decimal("0.05"),
            bid_fill_proxy="HOURLY_LOW",
            holding_days=30,
        ),
    )

    assert daily.status == "NOT_TRIGGERED"
    assert hourly.status == "ENTERED"
    # This is the only shape that is genuinely a resting bid: an hour traded
    # down into it while the day as a whole stayed above, so the fill is at the
    # bid and strictly better than any market buyer got that day.
    assert hourly.filled_at_bid is True
    assert hourly.entry_price == Decimal("73.500")
    assert hourly.entry_price < Decimal("94")
    assert (
        "rerelease_bottom_fishing_bid_filled_on_hourly_low"
        in hourly.explanation_codes
    )


def test_a_bid_without_a_pre_event_baseline_is_refused() -> None:
    buckets = tuple(bucket(44 + i, p) for i, p in enumerate(["70", "60"]))
    result = entry("WINDOWED_BID_ABSTAIN", buckets, config=BID_CONFIG)

    assert result.status == "NO_PRE_EVENT_BASELINE"
    assert result.bid_price is None


def test_a_wider_tolerance_fills_more_often_and_pays_more() -> None:
    """The trade the tolerance makes, asserted rather than assumed."""

    buckets = path(["90", "85", "76", "80"])
    tight = entry(
        "WINDOWED_BID_ABSTAIN",
        buckets,
        config=BottomFishingConfig(
            maximum_wait_days=7, bid_tolerance=Decimal("0"), holding_days=30
        ),
    )
    loose = entry(
        "WINDOWED_BID_ABSTAIN",
        buckets,
        config=BottomFishingConfig(
            maximum_wait_days=7, bid_tolerance=Decimal("0.10"), holding_days=30
        ),
    )

    assert tight.status == "NOT_TRIGGERED"
    assert loose.status == "ENTERED"
    # 76 cleared the looser 77.00 bid and not the tight 70.00 one, and the
    # fill is at the market price it cleared rather than at the bid.
    assert loose.entry_price == Decimal("76")


def test_an_unsupported_fill_proxy_is_rejected() -> None:
    with pytest.raises(BottomFishingError, match="unsupported bid fill proxy"):
        BottomFishingConfig(bid_fill_proxy="TOUCH_THE_LOW")


def test_a_negative_tolerance_is_rejected() -> None:
    with pytest.raises(BottomFishingError, match="bid_tolerance"):
        BottomFishingConfig(bid_tolerance=Decimal("-0.01"))


def test_a_bid_above_the_market_pays_the_market_not_the_bid() -> None:
    """A resting order cannot fill above the best offer.

    The event has already driven the price to 55 while the bid rests at 73.50.
    Crediting a fill at 73.50 would report the strategy paying more than any
    buyer had to, and would make an overpay look like a discount.
    """

    buckets = path(["55", "58", "60"])
    result = entry("WINDOWED_BID_ABSTAIN", buckets, config=BID_CONFIG)

    assert result.status == "ENTERED"
    assert result.bid_price == Decimal("73.500")
    assert result.filled_at_bid is False
    assert result.entry_price == Decimal("55")
    assert result.entry_discount == Decimal("0")
    assert (
        "rerelease_bottom_fishing_bid_sat_above_the_market_and_paid_it"
        in result.explanation_codes
    )
