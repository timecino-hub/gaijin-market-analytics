"""Stage 24 — one vehicle's own history, and where its price sits in it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.supply_cycles import SupplyPoolEvent
from gaijin_market_analytics.vehicle_potential import (
    VEHICLE_POTENTIAL_VERSION,
    VehiclePotentialConfig,
    VehiclePotentialError,
    evaluate_vehicle_potential,
    rank_vehicle_potential,
)

BASE = datetime(2024, 1, 1, tzinfo=UTC)


def day(offset: int) -> datetime:
    return BASE + timedelta(days=offset)


def bucket(offset: int, price: str, volume: int = 10) -> HistoricalTradeBucketEvidence:
    return HistoricalTradeBucketEvidence(
        item_id=1,
        granularity="1d",
        bucket_start_utc=day(offset),
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal(price),
        reported_trade_volume=volume,
    )


def flat_history(days: int = 400, price: str = "100", volume: int = 10):
    return tuple(bucket(index, price, volume) for index in range(days))


def evaluate(buckets, *, as_of_offset: int, events=(), config=None):
    return evaluate_vehicle_potential(
        item_id=1,
        as_of=day(as_of_offset),
        events=events,
        historical_buckets=buckets,
        config=config,
    )


# --- where the price sits ---------------------------------------------------


def test_a_price_at_the_bottom_of_its_own_range_reads_as_such() -> None:
    # 399 days at 100, then one day at 50.
    history = flat_history(399) + (bucket(399, "50"),)
    result = evaluate(history, as_of_offset=400)

    assert result.state == "RANKED"
    assert result.current_price == Decimal("50")
    assert result.price_percentile == Decimal(1) / Decimal(400)
    assert result.low_price == Decimal("50")
    assert result.high_price == Decimal("100")
    assert result.median_price == Decimal("100")
    assert result.ratio_to_median == Decimal("0.5")
    assert "vehicle_potential_near_the_bottom_of_its_own_range" in result.explanation_codes
    assert result.version == VEHICLE_POTENTIAL_VERSION


def test_a_price_at_the_top_of_its_own_range_reads_as_such() -> None:
    history = flat_history(399) + (bucket(399, "200"),)
    result = evaluate(history, as_of_offset=400)

    assert result.price_percentile == Decimal("1")
    assert "vehicle_potential_near_the_top_of_its_own_range" in result.explanation_codes


def test_the_percentile_counts_days_at_or_below_the_current_price() -> None:
    # 200 days at 100, 199 at 300, then today at 100: 201 of 400 are <= 100.
    history = (
        tuple(bucket(i, "100") for i in range(200))
        + tuple(bucket(i, "300") for i in range(200, 399))
        + (bucket(399, "100"),)
    )
    result = evaluate(history, as_of_offset=400)

    assert result.price_percentile == Decimal(201) / Decimal(400)


def test_the_reading_never_claims_to_be_a_forecast() -> None:
    result = evaluate(flat_history(), as_of_offset=400)

    assert result.research_only is True
    assert "vehicle_potential_descriptive_not_a_forecast" in result.explanation_codes


def test_volume_is_reported_for_the_window_and_for_recent_days() -> None:
    history = tuple(bucket(i, "100", 4) for i in range(380)) + tuple(
        bucket(i, "100", 40) for i in range(380, 400)
    )
    result = evaluate(history, as_of_offset=400)

    assert result.median_daily_volume == 4
    assert result.recent_daily_volume == 40


# --- refusals ---------------------------------------------------------------


def test_too_little_history_refuses_rather_than_taking_a_percentile_of_nothing() -> None:
    result = evaluate(flat_history(30), as_of_offset=31)

    assert result.state == "INSUFFICIENT_HISTORY"
    assert result.price_percentile is None
    assert result.current_price is None
    assert "vehicle_potential_not_enough_trading_history" in result.explanation_codes


def test_a_stale_last_price_is_not_published_as_current() -> None:
    # Trading stops on day 400 but we ask on day 430.
    result = evaluate(flat_history(400), as_of_offset=430)

    assert result.state == "NO_CURRENT_PRICE"
    assert result.current_price is None
    assert result.price_age_days is not None and result.price_age_days > 14
    assert "vehicle_potential_latest_price_is_stale" in result.explanation_codes


def test_a_price_just_inside_the_staleness_limit_still_counts() -> None:
    result = evaluate(flat_history(400), as_of_offset=414)

    assert result.state == "RANKED"
    assert result.current_price == Decimal("100")


def test_an_incomplete_day_is_not_read_as_a_price() -> None:
    """A day only counts once it has finished at as_of."""

    history = flat_history(400)
    mid_day = day(400) + timedelta(hours=12)
    result = evaluate_vehicle_potential(
        item_id=1, as_of=mid_day, historical_buckets=history + (bucket(400, "1"),)
    )

    assert result.current_price == Decimal("100")


# --- the event record -------------------------------------------------------


def event(key: str, opens: int, closes: int) -> SupplyPoolEvent:
    return SupplyPoolEvent(
        event_key=key,
        family="spring",
        known_at=day(opens - 7),
        opens_at=day(opens),
        closes_at=day(closes),
    )


def history_with_event(*, drop_to: str, recover_to: str):
    """100 before the event, `drop_to` right after it, `recover_to` later."""

    rows = [bucket(i, "100") for i in range(0, 200)]
    rows += [bucket(i, drop_to) for i in range(200, 210)]
    rows += [bucket(i, recover_to) for i in range(210, 500)]
    return tuple(rows)


def test_an_event_record_measures_drawdown_and_the_return_after_the_hold() -> None:
    history = history_with_event(drop_to="60", recover_to="120")
    result = evaluate(
        history, as_of_offset=490, events=(event("spring-2024", 190, 200),)
    )

    assert result.completed_event_count == 1
    assert result.closed_outcome_count == 1
    record = result.events[0]
    assert record.pre_event_baseline == Decimal("100")
    assert record.entry_price == Decimal("60")
    assert record.drawdown_ratio == Decimal("0.6")
    assert record.exit_price == Decimal("120")
    assert record.outcome_closed is True
    # 120 sold at a 15% fee against a cost of 60 is a net +70%.
    assert record.net_roi == (Decimal("120") * Decimal("0.85")) / Decimal("60") - 1
    assert result.positive_outcome_count == 1


def test_an_event_whose_horizon_has_not_finished_reports_no_return() -> None:
    history = history_with_event(drop_to="60", recover_to="120")
    # The event closed on day 200; 180 days of holding ends around day 380.
    result = evaluate(
        history, as_of_offset=300, events=(event("spring-2024", 190, 200),)
    )

    record = result.events[0]
    assert record.outcome_closed is False
    assert record.net_roi is None
    assert record.exit_price is None
    # The drawdown is still known — only the outcome is in the future.
    assert record.drawdown_ratio == Decimal("0.6")
    assert result.closed_outcome_count == 0
    assert result.median_net_roi is None


def test_an_event_still_running_is_not_in_the_record() -> None:
    history = history_with_event(drop_to="60", recover_to="120")
    result = evaluate(
        history, as_of_offset=195, events=(event("spring-2024", 190, 200),)
    )

    assert result.completed_event_count == 0
    assert result.events == ()
    assert result.days_since_last_event_close is None


def test_a_thin_event_record_says_so_rather_than_generalising() -> None:
    history = history_with_event(drop_to="60", recover_to="120")
    result = evaluate(
        history, as_of_offset=490, events=(event("spring-2024", 190, 200),)
    )

    assert result.closed_outcome_count == 1
    assert (
        "vehicle_potential_event_record_too_small_to_generalise"
        in result.explanation_codes
    )


def test_a_vehicle_with_no_events_still_gets_a_full_price_reading() -> None:
    result = evaluate(flat_history(), as_of_offset=400, events=())

    assert result.state == "RANKED"
    assert result.price_percentile is not None
    assert result.completed_event_count == 0
    assert result.median_net_roi is None
    assert "vehicle_potential_no_completed_event_record" in result.explanation_codes


def test_a_missing_pre_event_baseline_does_not_invent_a_drawdown() -> None:
    # History starts after the event opened, so there is nothing to compare to.
    rows = tuple(bucket(i, "60") for i in range(200, 500))
    result = evaluate(rows, as_of_offset=490, events=(event("spring-2024", 190, 200),))

    record = result.events[0]
    assert record.pre_event_baseline is None
    assert record.drawdown_ratio is None
    assert result.median_drawdown_ratio is None


# --- the ranking ------------------------------------------------------------


def test_the_ranking_is_cheapest_against_its_own_history_first() -> None:
    cheap = evaluate(flat_history(399) + (bucket(399, "50"),), as_of_offset=400)
    dear = evaluate(flat_history(399) + (bucket(399, "200"),), as_of_offset=400)
    middling = evaluate(
        tuple(bucket(i, "100") for i in range(200))
        + tuple(bucket(i, "300") for i in range(200, 399))
        + (bucket(399, "100"),),
        as_of_offset=400,
    )

    ordered = rank_vehicle_potential((dear, middling, cheap))

    assert [value.price_percentile for value in ordered] == sorted(
        value.price_percentile for value in (dear, middling, cheap)
    )
    assert ordered[0] is cheap


def test_vehicles_without_a_reading_sort_last_rather_than_first() -> None:
    """An unreadable vehicle must not lead the list by scoring zero."""

    readable = evaluate(flat_history(399) + (bucket(399, "50"),), as_of_offset=400)
    unreadable = evaluate(flat_history(30), as_of_offset=31)

    ordered = rank_vehicle_potential((unreadable, readable))

    assert ordered[0] is readable
    assert ordered[-1] is unreadable


def test_the_event_record_never_reorders_the_ranking() -> None:
    """Ranking is the price position only; a great record does not promote."""

    great_record_but_expensive = evaluate(
        history_with_event(drop_to="60", recover_to="500")[:399]
        + (bucket(399, "500"),),
        as_of_offset=400,
        events=(event("spring-2024", 190, 200),),
    )
    no_record_but_cheap = evaluate(
        flat_history(399) + (bucket(399, "50"),), as_of_offset=400
    )

    ordered = rank_vehicle_potential(
        (great_record_but_expensive, no_record_but_cheap)
    )

    assert ordered[0] is no_record_but_cheap


# --- input discipline -------------------------------------------------------


def test_a_naive_as_of_is_rejected() -> None:
    with pytest.raises(VehiclePotentialError, match="timezone-aware"):
        evaluate_vehicle_potential(
            item_id=1,
            as_of=datetime(2024, 6, 1),
            historical_buckets=flat_history(),
        )


def test_a_non_positive_item_id_is_rejected() -> None:
    with pytest.raises(VehiclePotentialError, match="item_id"):
        evaluate_vehicle_potential(
            item_id=0, as_of=day(400), historical_buckets=flat_history()
        )


def test_an_impossible_configuration_is_rejected() -> None:
    with pytest.raises(VehiclePotentialError, match="minimum_history_days"):
        VehiclePotentialConfig(history_days=100, minimum_history_days=200)
    with pytest.raises(VehiclePotentialError, match="fee_rate"):
        VehiclePotentialConfig(fee_rate=Decimal("1"))
