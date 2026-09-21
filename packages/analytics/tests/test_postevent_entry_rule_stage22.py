"""Stage 22 — the shippable post-event entry rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.postevent_entry_rule import (
    POSTEVENT_ENTRY_RULE_VERSION,
    PostEventEntryConfig,
    PostEventEntryError,
    evaluate_postevent_entry,
    resolve_postevent_entry_withholding,
)
from gaijin_market_analytics.supply_cycles import SupplyPoolEvent


BASE = datetime(2026, 1, 1, tzinfo=UTC)
OPENS = BASE + timedelta(days=30)
CLOSES = BASE + timedelta(days=44)


def day(offset: int) -> datetime:
    return BASE + timedelta(days=offset)


def bucket(offset: int, price: str) -> HistoricalTradeBucketEvidence:
    return HistoricalTradeBucketEvidence(
        item_id=1,
        granularity="1d",
        bucket_start_utc=day(offset),
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal(price),
        reported_trade_volume=10,
    )


def event(key: str = "spring-2026", opens: int = 30, closes: int = 44) -> SupplyPoolEvent:
    return SupplyPoolEvent(
        event_key=key,
        family="spring",
        known_at=day(opens - 7),
        opens_at=day(opens),
        closes_at=day(closes),
    )


def series(post: list[str], *, pre: str = "100"):
    rows = [bucket(offset, pre) for offset in range(16, 30)]
    rows += [bucket(44 + index, price) for index, price in enumerate(post)]
    return tuple(rows)


def check(post, *, as_of_offset=46, config=None, events=None):
    return evaluate_postevent_entry(
        item_id=1,
        as_of=day(as_of_offset),
        events=events if events is not None else (event(),),
        historical_buckets=series(post),
        config=config,
    )


# --- the gate ---------------------------------------------------------------


def test_a_deep_drawdown_is_an_entry_candidate() -> None:
    result = check(["60", "62"])

    assert result.state == "ENTRY_CANDIDATE"
    assert result.is_entry_candidate is True
    assert result.pre_event_baseline == Decimal("100")
    assert result.latest_price == Decimal("62")
    assert result.drawdown_ratio == Decimal("0.62")
    assert "postevent_entry_drawdown_gate_passed" in result.explanation_codes
    assert result.version == POSTEVENT_ENTRY_RULE_VERSION


def test_a_shallow_drawdown_is_not() -> None:
    result = check(["90", "88"])

    assert result.state == "SHALLOW_DRAWDOWN"
    assert result.is_entry_candidate is False
    assert result.drawdown_ratio == Decimal("0.88")
    assert result.take_profit_price is None
    assert result.hold_until is None
    assert "postevent_entry_drawdown_gate_failed" in result.explanation_codes


def test_the_threshold_is_exactly_inclusive() -> None:
    assert check(["75"]).state == "ENTRY_CANDIDATE"
    assert check(["76"]).state == "SHALLOW_DRAWDOWN"


def test_a_stricter_threshold_refuses_what_the_default_accepts() -> None:
    post = ["70"]
    assert check(post).is_entry_candidate is True
    assert (
        check(post, config=PostEventEntryConfig(drawdown_threshold=Decimal("0.65")))
        .is_entry_candidate
        is False
    )


# --- the guidance -----------------------------------------------------------


def test_the_take_profit_price_carries_the_fee() -> None:
    result = check(["60"])

    # Netting 25% at a 15% fee needs the price up about 47%.
    assert result.take_profit_price == Decimal("60") * Decimal("1.25") / Decimal("0.85")
    assert result.take_profit_price > Decimal("88")
    assert result.take_profit_net_roi == Decimal("0.25")


def test_the_hold_date_runs_from_the_latest_completed_day() -> None:
    result = check(["60", "62"])

    assert result.hold_until == day(45) + timedelta(days=180)


def test_the_signal_never_claims_a_fill() -> None:
    result = check(["60"])

    assert result.research_only is True
    assert result.fill_claim is False
    assert (
        "postevent_entry_rule_research_only_not_execution_or_fill"
        in result.explanation_codes
    )


def test_the_signal_says_its_evidence_is_only_descriptive() -> None:
    """The walk-forward lift is 2.6 points, not the descriptive 15.6."""

    result = check(["60"])

    assert (
        "postevent_entry_evidence_is_descriptive_not_walk_forward_validated"
        in result.explanation_codes
    )


# --- refusals ---------------------------------------------------------------


def test_an_event_still_open_yields_no_signal() -> None:
    result = check(["60"], as_of_offset=40)

    assert result.state == "NO_COMPLETED_EVENT"
    assert result.is_entry_candidate is False


def test_a_stale_window_is_closed_rather_than_scored() -> None:
    result = check(["60"] * 30, as_of_offset=70)

    assert result.state == "ENTRY_WINDOW_CLOSED"
    assert result.days_since_close == 26
    assert "postevent_entry_window_has_closed" in result.explanation_codes


def test_a_newer_supply_event_supersedes_the_window() -> None:
    result = check(
        ["60"],
        as_of_offset=46,
        events=(event(), event(key="summer-2026", opens=45, closes=60)),
    )

    assert result.state == "SUPERSEDED_BY_NEXT_SUPPLY_EVENT"
    assert result.is_entry_candidate is False


def test_no_pre_event_baseline_refuses_rather_than_guessing() -> None:
    bare = tuple(bucket(44 + i, p) for i, p in enumerate(["60", "62"]))
    result = evaluate_postevent_entry(
        item_id=1, as_of=day(46), events=(event(),), historical_buckets=bare
    )

    assert result.state == "NO_PRE_EVENT_BASELINE"
    assert result.pre_event_baseline is None


def test_no_completed_day_since_the_close_refuses() -> None:
    result = check([], as_of_offset=46)

    assert result.state == "NO_POST_EVENT_PRICE"
    assert result.latest_price is None


def test_being_past_the_first_day_is_flagged() -> None:
    fresh = check(["60"], as_of_offset=45)
    later = check(["60", "61", "62"], as_of_offset=47)

    assert "postevent_entry_past_the_first_post_close_day" not in fresh.explanation_codes
    assert "postevent_entry_past_the_first_post_close_day" in later.explanation_codes


# --- the policy -------------------------------------------------------------


def test_ignore_changes_nothing_and_says_nothing() -> None:
    signal = check(["90"])
    withheld, codes = resolve_postevent_entry_withholding(signal=signal)

    assert withheld is False
    assert codes == ()


def test_annotate_publishes_the_reasoning_without_gating() -> None:
    signal = check(["90"])
    withheld, codes = resolve_postevent_entry_withholding(
        signal=signal, policy="ANNOTATE"
    )

    assert withheld is False
    assert "postevent_entry_drawdown_gate_failed" in codes
    assert "postevent_entry_annotated_only" in codes


def test_only_the_withholding_policy_refuses_a_shallow_drawdown() -> None:
    shallow = check(["90"])
    deep = check(["60"])

    assert resolve_postevent_entry_withholding(
        signal=shallow, policy="WITHHOLD_SHALLOW_DRAWDOWN"
    )[0] is True
    assert resolve_postevent_entry_withholding(
        signal=deep, policy="WITHHOLD_SHALLOW_DRAWDOWN"
    )[0] is False


def test_the_withholding_code_names_the_strategy_reason() -> None:
    signal = check(["90"])
    _, codes = resolve_postevent_entry_withholding(
        signal=signal, policy="WITHHOLD_SHALLOW_DRAWDOWN"
    )

    assert "strategy_withheld_for_shallow_post_event_drawdown" in codes


def test_a_missing_signal_never_withholds() -> None:
    for policy in ("IGNORE", "ANNOTATE", "WITHHOLD_SHALLOW_DRAWDOWN"):
        assert resolve_postevent_entry_withholding(signal=None, policy=policy) == (
            False,
            (),
        )


def test_an_unsupported_policy_is_rejected() -> None:
    with pytest.raises(PostEventEntryError, match="unsupported post-event entry"):
        resolve_postevent_entry_withholding(signal=None, policy="ALWAYS_BUY")


# --- input discipline -------------------------------------------------------


def test_a_naive_as_of_is_rejected() -> None:
    with pytest.raises(PostEventEntryError, match="timezone-aware"):
        evaluate_postevent_entry(
            item_id=1,
            as_of=datetime(2026, 2, 16),
            events=(event(),),
            historical_buckets=series(["60"]),
        )


def test_an_out_of_range_threshold_is_rejected() -> None:
    with pytest.raises(PostEventEntryError, match=r"in \(0, 1\]"):
        PostEventEntryConfig(drawdown_threshold=Decimal("1.5"))
