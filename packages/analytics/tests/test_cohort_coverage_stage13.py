from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gaijin_market_analytics.cohort_coverage import (
    CohortCoverageError,
    assess_history_coverage,
    suggest_catalog_vehicle_names,
)

AS_OF = datetime(2026, 9, 21, tzinfo=UTC)
# The Nuclear Football: opens 2026-04-09 11:00Z, closes 2026-05-06 11:00Z.
NUCLEAR_FOOTBALL_PRE_START = datetime(2026, 3, 13, 11, tzinfo=UTC)


def _days(start: datetime, count: int, *, step_hours: int = 24):
    return [start + timedelta(hours=step_hours * index) for index in range(count)]


def test_full_history_covers_every_pre_event_window():
    coverage = assess_history_coverage(
        vehicle_name="Kungstiger",
        bucket_start_times=_days(datetime(2024, 8, 20, tzinfo=UTC), 760),
        as_of=AS_OF,
    )
    assert coverage.match_status == "MATCHED"
    assert coverage.tradeable_event_count == 1
    assert coverage.completed_tradeable_event_count == 1
    assert coverage.fully_usable_event_count == 1
    assert coverage.covers_required_start is True
    assert coverage.required_history_start_utc == NUCLEAR_FOOTBALL_PRE_START
    assert coverage.explanation_codes == ()


def test_history_starting_after_the_baseline_window_is_flagged():
    coverage = assess_history_coverage(
        vehicle_name="Kungstiger",
        bucket_start_times=_days(datetime(2026, 6, 1, tzinfo=UTC), 100),
        as_of=AS_OF,
    )
    assert coverage.covers_required_start is False
    assert coverage.fully_usable_event_count == 0
    assert (
        "named_history_starts_after_first_pre_event_baseline"
        in coverage.explanation_codes
    )
    event = coverage.events[0]
    assert event.pre_window_covered is False
    assert event.active_window_covered is False
    assert event.post_window_observed_days > 0


def test_vehicle_outside_the_catalog_binds_to_no_event():
    coverage = assess_history_coverage(
        vehicle_name="Т-10А (USSR)",
        bucket_start_times=_days(datetime(2021, 3, 13, tzinfo=UTC), 900),
        as_of=AS_OF,
    )
    assert coverage.match_status == "UNMATCHED"
    assert coverage.tradeable_event_count == 0
    assert coverage.required_history_start_utc is None
    assert coverage.covers_required_start is False
    assert "named_history_not_bound_to_supply_catalog" in coverage.explanation_codes
    assert "no_tradeable_supply_event_bound" in coverage.explanation_codes


def test_account_only_returns_are_not_tradeable_events():
    coverage = assess_history_coverage(
        vehicle_name="VL Myrsky II",
        bucket_start_times=_days(datetime(2020, 1, 1, tzinfo=UTC), 2000),
        as_of=AS_OF,
    )
    assert coverage.match_status == "MATCHED"
    assert coverage.tradeable_event_count == 0
    assert "no_tradeable_supply_event_bound" in coverage.explanation_codes


def test_only_utc_days_completed_before_as_of_count_as_post_event():
    closes_at = datetime(2026, 5, 6, 11, tzinfo=UTC)
    coverage = assess_history_coverage(
        vehicle_name="Kungstiger",
        bucket_start_times=[
            *_days(datetime(2026, 1, 1, tzinfo=UTC), 200),
        ],
        as_of=closes_at + timedelta(days=3),
    )
    event = coverage.events[0]
    # Days starting 2026-05-07 and 2026-05-08 are complete by as_of; the day
    # starting on the close date begins before the event closed.
    assert event.post_window_observed_days == 2


def test_hourly_and_daily_buckets_on_one_day_are_counted_once():
    day = datetime(2026, 6, 1, tzinfo=UTC)
    coverage = assess_history_coverage(
        vehicle_name="Kungstiger",
        bucket_start_times=[day, day + timedelta(hours=3), day + timedelta(hours=9)],
        as_of=AS_OF,
    )
    assert coverage.observed_day_count == 1


def test_an_empty_history_is_reported_rather_than_crashing():
    coverage = assess_history_coverage(
        vehicle_name="Kungstiger",
        bucket_start_times=(),
        as_of=AS_OF,
    )
    assert coverage.observed_day_count == 0
    assert coverage.history_start_utc is None
    assert coverage.fully_usable_event_count == 0
    assert "named_history_contains_no_observations" in coverage.explanation_codes


def test_coverage_rejects_naive_datetimes():
    with pytest.raises(CohortCoverageError):
        assess_history_coverage(
            vehicle_name="Kungstiger",
            bucket_start_times=[datetime(2026, 6, 1)],
            as_of=AS_OF,
        )
    with pytest.raises(CohortCoverageError):
        assess_history_coverage(
            vehicle_name="Kungstiger",
            bucket_start_times=(),
            as_of=datetime(2026, 9, 21),
        )
    with pytest.raises(CohortCoverageError):
        assess_history_coverage(
            vehicle_name="  ", bucket_start_times=(), as_of=AS_OF
        )


def test_slug_suggestions_strip_the_marketplace_item_id():
    suggestions = suggest_catalog_vehicle_names(
        item_key="id50268_kungstiger_sweden"
    )
    assert suggestions
    assert suggestions[0][0] == "Kungstiger"
    assert 0 < suggestions[0][1] <= 1


def test_slug_suggestions_are_ranked_and_bounded():
    suggestions = suggest_catalog_vehicle_names(
        item_key="merkava_mk_3d_usa", limit=2
    )
    assert len(suggestions) <= 2
    assert any(name == "Merkava Mk.3D (USA)" for name, _ in suggestions)
    scores = [score for _, score in suggestions]
    assert scores == sorted(scores, reverse=True)


def test_slug_suggestions_return_nothing_for_an_unrecognizable_key():
    assert suggest_catalog_vehicle_names(item_key="12345") == ()
    with pytest.raises(CohortCoverageError):
        suggest_catalog_vehicle_names(item_key="kungstiger", limit=0)


def test_a_name_behind_a_catalog_alias_is_suggested_first() -> None:
    """The catalog writes Т-34E with a Cyrillic Te and aliases the ASCII form.

    Token overlap shares nothing with that spelling, so before the catalog's
    own resolution was consulted the correct answer sat below unrelated
    vehicles and an operator reading the top suggestion would have bound the
    wrong tank.
    """

    suggestions = suggest_catalog_vehicle_names(item_key="T-34E")

    assert suggestions[0][0] == "Т-34E"
    assert suggestions[0][1] == 1.0


def test_fuzzy_suggestions_still_rank_below_an_exact_resolution() -> None:
    suggestions = suggest_catalog_vehicle_names(item_key="T-34E", limit=3)

    assert [score for _, score in suggestions[1:]] == sorted(
        (score for _, score in suggestions[1:]), reverse=True
    )
    assert all(score < 1.0 for _, score in suggestions[1:])


def test_a_designation_shared_across_nations_is_not_resolved_exactly() -> None:
    """What the fix cannot do, asserted so nobody expects it to.

    ``id50361_f_6c_china`` is the Chinese J-6C. ``F-6C-10-NA`` is a
    reconnaissance Mustang built by North American Aviation. They share a
    designation fragment and nothing else, and no amount of string matching
    can tell them apart, so the catalog offers no exact resolution and every
    suggestion stays a fuzzy one for an operator to reject.
    """

    suggestions = suggest_catalog_vehicle_names(item_key="id50361_f_6c_china")

    assert suggestions
    assert all(score < 1.0 for _, score in suggestions)
