from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Literal

Granularity = Literal["1h", "1d"]

_PRICE_SEMANTICS = "bucket_volume_weighted_average_trade_price"
_VOLUME_SEMANTICS = "reported_trade_volume_unknown_unit"


class HistoricalTradeContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HistoricalTradeBucketEvidence:
    item_id: int
    granularity: Granularity
    bucket_start_utc: datetime
    bucket_duration: timedelta
    vwap_price: Decimal
    reported_trade_volume: int
    price_semantics: str = _PRICE_SEMANTICS
    volume_semantics: str = _VOLUME_SEMANTICS

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise HistoricalTradeContractError("item_id must be positive.")
        object.__setattr__(
            self,
            "bucket_start_utc",
            _require_aware_utc(self.bucket_start_utc, "bucket_start_utc"),
        )
        expected_duration = timedelta(hours=1) if self.granularity == "1h" else timedelta(days=1)
        if self.granularity not in {"1h", "1d"}:
            raise HistoricalTradeContractError("granularity must be '1h' or '1d'.")
        if self.bucket_duration != expected_duration:
            raise HistoricalTradeContractError(
                f"{self.granularity} buckets must have duration {expected_duration}."
            )
        if not isinstance(self.vwap_price, Decimal) or not self.vwap_price.is_finite():
            raise HistoricalTradeContractError("vwap_price must be a finite Decimal.")
        if self.vwap_price <= Decimal("0"):
            raise HistoricalTradeContractError("vwap_price must be greater than 0.")
        if self.reported_trade_volume <= 0:
            raise HistoricalTradeContractError("reported_trade_volume must be greater than 0.")
        if self.price_semantics != _PRICE_SEMANTICS:
            raise HistoricalTradeContractError("unsupported historical price semantics.")
        if self.volume_semantics != _VOLUME_SEMANTICS:
            raise HistoricalTradeContractError("unsupported historical volume semantics.")

    @property
    def bucket_end_utc(self) -> datetime:
        return self.bucket_start_utc + self.bucket_duration


@dataclass(frozen=True, slots=True)
class HistoricalTradeEvidence:
    item_id: int
    as_of: datetime
    window_start: datetime
    buckets: tuple[HistoricalTradeBucketEvidence, ...]
    hourly_bucket_count: int
    daily_bucket_count: int
    overlapping_daily_buckets_dropped: int
    future_or_incomplete_buckets_dropped: int
    before_window_buckets_dropped: int

    @property
    def first_bucket_at(self) -> datetime | None:
        return self.buckets[0].bucket_start_utc if self.buckets else None

    @property
    def last_bucket_end_at(self) -> datetime | None:
        return max((bucket.bucket_end_utc for bucket in self.buckets), default=None)

    @property
    def latest_trade_at(self) -> datetime | None:
        return self.last_bucket_end_at

    @property
    def observed_span(self) -> timedelta:
        if not self.buckets:
            return timedelta(0)
        return self.last_bucket_end_at - self.first_bucket_at  # type: ignore[operator]


@dataclass(frozen=True, slots=True)
class DailyHistoricalTradeEvidence:
    """One UTC-day observation after 1h/1d overlap selection.

    ``reported_trade_volume`` remains a source-reported activity unit. It is
    only used as an intra-day VWAP weighting factor and must not be interpreted
    as GJN turnover.
    """

    day_start_utc: datetime
    available_at: datetime
    vwap_price: Decimal
    reported_trade_volume: Decimal
    source_bucket_count: int
    used_hourly_resolution: bool


def select_historical_trade_evidence(
    buckets: Iterable[HistoricalTradeBucketEvidence],
    *,
    as_of: datetime,
    window_start: datetime,
) -> HistoricalTradeEvidence:
    """Select completed, look-ahead-safe buckets without 1h/1d double counting.

    If any selected 1h bucket overlaps a selected 1d bucket, the entire daily
    bucket is discarded and the hourly evidence wins. This intentionally trades
    some temporal coverage for unambiguous, deterministic non-overlap semantics.
    """

    as_of = _require_aware_utc(as_of, "as_of")
    window_start = _require_aware_utc(window_start, "window_start")
    if window_start >= as_of:
        raise HistoricalTradeContractError("window_start must be earlier than as_of.")

    normalized = tuple(buckets)
    item_ids = {bucket.item_id for bucket in normalized}
    if len(item_ids) > 1:
        raise HistoricalTradeContractError("historical evidence must contain one item_id.")
    item_id = next(iter(item_ids), 0)

    seen: set[tuple[str, datetime]] = set()
    for bucket in normalized:
        key = (bucket.granularity, bucket.bucket_start_utc)
        if key in seen:
            raise HistoricalTradeContractError(
                "duplicate historical bucket identity for granularity/start."
            )
        seen.add(key)

    before_window = tuple(
        bucket for bucket in normalized if bucket.bucket_start_utc < window_start
    )
    future_or_incomplete = tuple(
        bucket
        for bucket in normalized
        if bucket.bucket_start_utc >= window_start and bucket.bucket_end_utc > as_of
    )
    completed = tuple(
        bucket
        for bucket in normalized
        if bucket.bucket_start_utc >= window_start and bucket.bucket_end_utc <= as_of
    )

    hourly = tuple(bucket for bucket in completed if bucket.granularity == "1h")
    daily = tuple(bucket for bucket in completed if bucket.granularity == "1d")
    kept_daily = _daily_without_hourly_overlap(daily, hourly)
    overlap_dropped = len(daily) - len(kept_daily)
    selected = tuple(
        sorted(
            (*hourly, *kept_daily),
            key=lambda bucket: (
                bucket.bucket_start_utc,
                0 if bucket.granularity == "1h" else 1,
            ),
        )
    )

    return HistoricalTradeEvidence(
        item_id=item_id,
        as_of=as_of,
        window_start=window_start,
        buckets=selected,
        hourly_bucket_count=sum(bucket.granularity == "1h" for bucket in selected),
        daily_bucket_count=sum(bucket.granularity == "1d" for bucket in selected),
        overlapping_daily_buckets_dropped=overlap_dropped,
        future_or_incomplete_buckets_dropped=len(future_or_incomplete),
        before_window_buckets_dropped=len(before_window),
    )


def aggregate_trade_evidence_by_utc_day(
    buckets: Iterable[HistoricalTradeBucketEvidence],
) -> tuple[DailyHistoricalTradeEvidence, ...]:
    """Collapse selected evidence to one price/activity observation per UTC day.

    Callers should normally pass the already-selected ``HistoricalTradeEvidence``
    buckets. The function rejects mixed 1h/1d evidence for the same UTC day so a
    later caller cannot accidentally reintroduce double weighting.
    """

    grouped: dict[datetime, list[HistoricalTradeBucketEvidence]] = {}
    for bucket in buckets:
        day = bucket.bucket_start_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        grouped.setdefault(day, []).append(bucket)

    result: list[DailyHistoricalTradeEvidence] = []
    for day in sorted(grouped):
        points = grouped[day]
        granularities = {point.granularity for point in points}
        if len(granularities) > 1:
            raise HistoricalTradeContractError(
                "daily aggregation received overlapping 1h and 1d evidence for one UTC day."
            )
        total_volume = sum(
            (Decimal(point.reported_trade_volume) for point in points), Decimal("0")
        )
        if total_volume <= Decimal("0"):
            raise HistoricalTradeContractError("daily reported trade volume must be positive.")
        weighted_price = sum(
            (
                point.vwap_price * Decimal(point.reported_trade_volume)
                for point in points
            ),
            Decimal("0"),
        ) / total_volume
        result.append(
            DailyHistoricalTradeEvidence(
                day_start_utc=day,
                available_at=max(point.bucket_end_utc for point in points),
                vwap_price=weighted_price,
                reported_trade_volume=total_volume,
                source_bucket_count=len(points),
                used_hourly_resolution="1h" in granularities,
            )
        )
    return tuple(result)


def _daily_without_hourly_overlap(
    daily: tuple[HistoricalTradeBucketEvidence, ...],
    hourly: tuple[HistoricalTradeBucketEvidence, ...],
) -> tuple[HistoricalTradeBucketEvidence, ...]:
    """The daily buckets that no hourly bucket overlaps.

    Testing every daily bucket against every hourly one is quadratic, and a
    decade of daily buckets against a month of hourly ones runs to millions of
    comparisons. The hourly buckets are ordered once, and each day compares
    only against the slice that could possibly reach it — the same
    ``_overlaps`` test on a set narrowed by a necessary condition, so the
    result is unchanged.
    """

    if not daily or not hourly:
        return tuple(daily)
    ordered = sorted(hourly, key=lambda bucket: bucket.bucket_start_utc)
    starts = [bucket.bucket_start_utc for bucket in ordered]
    # An hourly bucket can only reach back by its own duration, so anything
    # starting earlier than this cannot touch the day however long the day is.
    longest = max(bucket.bucket_duration for bucket in ordered)
    kept: list[HistoricalTradeBucketEvidence] = []
    for day in daily:
        first = bisect_right(starts, day.bucket_start_utc - longest)
        last = bisect_left(starts, day.bucket_end_utc)
        if not any(
            _overlaps(day, ordered[index]) for index in range(first, last)
        ):
            kept.append(day)
    return tuple(kept)


def _overlaps(
    left: HistoricalTradeBucketEvidence,
    right: HistoricalTradeBucketEvidence,
) -> bool:
    return (
        left.bucket_start_utc < right.bucket_end_utc
        and right.bucket_start_utc < left.bucket_end_utc
    )


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalTradeContractError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
