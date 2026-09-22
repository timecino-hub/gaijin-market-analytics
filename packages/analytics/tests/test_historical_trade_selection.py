"""The 1h-over-1d selection, and that narrowing it did not change it.

`select_historical_trade_evidence` used to compare every daily bucket against
every hourly one. A decade of daily buckets against a month of hourly ones is
millions of comparisons, and it dominated the two endpoints that read a whole
series at once. The comparison is now made against an ordered slice.

These tests pin the behaviour against the naive form on inputs built to break
a slicing shortcut: hourly buckets that straddle midnight, days with no hourly
cover at all, and a day whose hourly cover stops one hour short.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.historical_trades import (
    HistoricalTradeBucketEvidence,
    _overlaps,
    aggregate_trade_evidence_by_utc_day,
    select_historical_trade_evidence,
)

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def daily(offset: int, price: str = "100") -> HistoricalTradeBucketEvidence:
    return HistoricalTradeBucketEvidence(
        item_id=1,
        granularity="1d",
        bucket_start_utc=BASE + timedelta(days=offset),
        bucket_duration=timedelta(days=1),
        vwap_price=Decimal(price),
        reported_trade_volume=10,
    )


def hourly(hours: int, price: str = "100") -> HistoricalTradeBucketEvidence:
    return HistoricalTradeBucketEvidence(
        item_id=1,
        granularity="1h",
        bucket_start_utc=BASE + timedelta(hours=hours),
        bucket_duration=timedelta(hours=1),
        vwap_price=Decimal(price),
        reported_trade_volume=1,
    )


def naive_kept(daily_buckets, hourly_buckets):
    """The original quadratic form, kept here as the thing to agree with."""

    return tuple(
        day
        for day in daily_buckets
        if not any(_overlaps(day, hour) for hour in hourly_buckets)
    )


def select(buckets, *, as_of: datetime):
    return select_historical_trade_evidence(
        tuple(buckets), as_of=as_of, window_start=BASE - timedelta(days=1)
    )


def test_an_hourly_bucket_anywhere_in_a_day_discards_that_day() -> None:
    # One hour of hourly evidence in the middle of day 3.
    buckets = [daily(index) for index in range(6)] + [hourly(3 * 24 + 13)]
    evidence = select(buckets, as_of=BASE + timedelta(days=10))
    days = {bucket.bucket_start_utc for bucket in evidence.buckets if bucket.granularity == "1d"}

    assert BASE + timedelta(days=3) not in days
    assert len(days) == 5
    assert evidence.overlapping_daily_buckets_dropped == 1


def test_a_day_with_no_hourly_cover_survives() -> None:
    buckets = [daily(index) for index in range(6)] + [hourly(24)]
    evidence = select(buckets, as_of=BASE + timedelta(days=10))
    kept = tuple(bucket for bucket in evidence.buckets if bucket.granularity == "1d")

    assert len(kept) == 5
    assert all(bucket.bucket_start_utc != BASE + timedelta(days=1) for bucket in kept)


def test_the_hour_before_midnight_does_not_reach_the_next_day() -> None:
    # The last hour of day 0 touches day 0 and nothing after it. An off-by-one
    # in the slice bounds would take day 1 with it.
    buckets = [daily(0), daily(1), hourly(23)]
    kept = tuple(
        bucket
        for bucket in select(buckets, as_of=BASE + timedelta(days=5)).buckets
        if bucket.granularity == "1d"
    )

    assert len(kept) == 1
    assert kept[0].bucket_start_utc == BASE + timedelta(days=1)


def test_the_first_hour_of_a_day_reaches_that_day_and_not_the_one_before() -> None:
    buckets = [daily(0), daily(1), hourly(24)]
    kept = tuple(
        bucket
        for bucket in select(buckets, as_of=BASE + timedelta(days=5)).buckets
        if bucket.granularity == "1d"
    )

    assert len(kept) == 1
    assert kept[0].bucket_start_utc == BASE


def test_the_ordered_form_agrees_with_the_quadratic_one_on_random_cohorts() -> None:
    rng = random.Random(20260922)
    as_of = BASE + timedelta(days=400)
    for _ in range(40):
        day_offsets = sorted(rng.sample(range(0, 120), rng.randint(1, 60)))
        hour_offsets = sorted(rng.sample(range(0, 120 * 24), rng.randint(0, 80)))
        daily_buckets = tuple(daily(offset) for offset in day_offsets)
        hourly_buckets = tuple(hourly(offset) for offset in hour_offsets)

        evidence = select(daily_buckets + hourly_buckets, as_of=as_of)
        kept = tuple(
            bucket for bucket in evidence.buckets if bucket.granularity == "1d"
        )

        assert kept == naive_kept(daily_buckets, hourly_buckets)
        assert evidence.overlapping_daily_buckets_dropped == len(daily_buckets) - len(kept)


def test_the_surviving_days_still_aggregate_without_mixed_resolution() -> None:
    # The whole point of the discard: a day must never be aggregated from both
    # an hourly and a daily bucket.
    buckets = [daily(0), daily(1)] + [hourly(24 + index) for index in range(3)]
    evidence = select(buckets, as_of=BASE + timedelta(days=5))
    points = aggregate_trade_evidence_by_utc_day(evidence.buckets)

    assert len(points) == 2
    assert points[0].used_hourly_resolution is False
    assert points[1].used_hourly_resolution is True
    assert points[1].source_bucket_count == 3
