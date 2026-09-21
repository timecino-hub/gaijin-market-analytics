from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable

from .historical_trades import (
    DailyHistoricalTradeEvidence,
    HistoricalTradeBucketEvidence,
    aggregate_trade_evidence_by_utc_day,
    select_historical_trade_evidence,
)
from .supply_cycles import SupplyPoolEvent, SupplyPoolMembership


class SupplyCycleImpactError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SupplyEventImpactConfig:
    minimum_observed_days_per_phase: int = 3

    def __post_init__(self) -> None:
        if self.minimum_observed_days_per_phase <= 0:
            raise SupplyCycleImpactError("minimum_observed_days_per_phase must be positive.")


@dataclass(frozen=True, slots=True)
class SupplyEventImpact:
    event_key: str
    family: str
    as_of: datetime
    event_visible: bool
    event_completed: bool
    post_window_completed: bool
    profile_eligible: bool
    pre_window_start: datetime
    event_opens_at: datetime
    event_closes_at: datetime
    post_window_end: datetime
    pre_observed_day_count: int
    active_observed_day_count: int
    post_observed_day_count: int
    pre_price_median: Decimal | None
    active_price_median: Decimal | None
    active_price_min: Decimal | None
    post_price_median: Decimal | None
    pre_activity_median: Decimal | None
    active_activity_median: Decimal | None
    post_activity_median: Decimal | None
    event_drawdown_ratio: Decimal | None
    activity_multiplier: Decimal | None
    post_price_ratio: Decimal | None
    days_to_trough: Decimal | None
    recovery_half_life_days: Decimal | None
    explanation_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SupplyCycleImpactProfile:
    event_count: int
    eligible_event_count: int
    median_event_drawdown_ratio: Decimal | None
    median_activity_multiplier: Decimal | None
    median_post_price_ratio: Decimal | None
    median_days_to_trough: Decimal | None
    median_recovery_half_life_days: Decimal | None
    explanation_codes: tuple[str, ...]



@dataclass(frozen=True, slots=True)
class SupplyCycleImpactAnalysis:
    item_id: int
    as_of: datetime
    impacts: tuple[SupplyEventImpact, ...]
    profile: SupplyCycleImpactProfile


def analyze_item_supply_cycle_impacts(
    *,
    item_id: int,
    events: Iterable[SupplyPoolEvent],
    memberships: Iterable[SupplyPoolMembership],
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    as_of: datetime,
    config: SupplyEventImpactConfig | None = None,
) -> SupplyCycleImpactAnalysis:
    """Join point-in-time catalog membership to historical event-impact measurement.

    Only memberships that were known by ``as_of`` and can create tradeable
    coupons are measured. Account-only returns remain catalog facts but are not
    treated as Marketplace supply shocks.
    """

    if item_id <= 0:
        raise SupplyCycleImpactError("item_id must be positive.")
    as_of = _utc(as_of, "as_of")
    event_values = tuple(events)
    event_by_key = {event.event_key: event for event in event_values}
    if len(event_by_key) != len(event_values):
        raise SupplyCycleImpactError("event_key values must be unique.")

    member_values = tuple(memberships)
    if any(member.event_key not in event_by_key for member in member_values):
        raise SupplyCycleImpactError("membership references an unknown event_key.")
    if any(member.item_id != item_id for member in member_values):
        raise SupplyCycleImpactError("membership item_id does not match requested item_id.")

    bucket_values = tuple(historical_buckets)
    if any(bucket.item_id != item_id for bucket in bucket_values):
        raise SupplyCycleImpactError("historical bucket item_id does not match requested item_id.")

    visible_tradeable = tuple(
        member
        for member in member_values
        if member.known_at <= as_of
        and member.coupon_mode == "DIRECT_OR_COUPON"
        and event_by_key[member.event_key].known_at <= as_of
    )
    impacts = tuple(
        measure_supply_event_impact(
            event=event_by_key[member.event_key],
            historical_buckets=bucket_values,
            as_of=as_of,
            config=config,
        )
        for member in sorted(
            visible_tradeable,
            key=lambda value: (event_by_key[value.event_key].opens_at, value.event_key),
        )
    )
    return SupplyCycleImpactAnalysis(
        item_id=item_id,
        as_of=as_of,
        impacts=impacts,
        profile=build_supply_cycle_impact_profile(impacts),
    )

def measure_supply_event_impact(
    *,
    event: SupplyPoolEvent,
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    as_of: datetime,
    config: SupplyEventImpactConfig | None = None,
) -> SupplyEventImpact:
    """Measure one known supply event with point-in-time-safe trade evidence.

    The comparison windows are intentionally tied to the real event duration:
    PRE is one event-duration immediately before open, ACTIVE is the event
    itself, and POST is one event-duration immediately after close.  A complete
    event impact is eligible for an aggregate profile only once the POST window
    has elapsed as of the requested timestamp and all three phases have enough
    observed UTC days.

    Source-reported trade volume is treated only as a relative activity unit;
    it is never interpreted as GJN turnover.
    """

    cfg = config or SupplyEventImpactConfig()
    as_of = _utc(as_of, "as_of")
    duration = event.closes_at - event.opens_at
    pre_start = event.opens_at - duration
    post_end = event.closes_at + duration

    if event.known_at > as_of:
        return _empty_impact(
            event=event,
            as_of=as_of,
            pre_start=pre_start,
            post_end=post_end,
            codes=("supply_event_not_known_as_of",),
        )

    # Selection itself enforces bucket_end <= as_of.  Starting at PRE prevents
    # older data from influencing this event's impact statistics.
    evidence = select_historical_trade_evidence(
        historical_buckets,
        as_of=as_of,
        window_start=pre_start,
    )
    daily = aggregate_trade_evidence_by_utc_day(evidence.buckets)
    # Daily buckets can straddle official event boundaries (commonly 11:00 UTC).
    # Assign a day only when the complete UTC day is contained in the phase;
    # otherwise the boundary day's mixed regime would contaminate the comparison.
    pre = tuple(
        point
        for point in daily
        if point.day_start_utc >= pre_start
        and point.day_start_utc + timedelta(days=1) <= event.opens_at
    )
    active = tuple(
        point
        for point in daily
        if point.day_start_utc >= event.opens_at
        and point.day_start_utc + timedelta(days=1) <= event.closes_at
    )
    post = tuple(
        point
        for point in daily
        if point.day_start_utc >= event.closes_at
        and point.day_start_utc + timedelta(days=1) <= post_end
    )

    event_completed = as_of >= event.closes_at
    post_window_completed = as_of >= post_end
    phase_counts_ok = all(
        len(points) >= cfg.minimum_observed_days_per_phase
        for points in (pre, active, post)
    )
    eligible = post_window_completed and phase_counts_ok
    codes: list[str] = []
    if not event_completed:
        codes.append("supply_event_active_or_future_close")
    if event_completed and not post_window_completed:
        codes.append("supply_event_post_window_incomplete")
    if not phase_counts_ok:
        codes.append("insufficient_supply_event_phase_coverage")
    if eligible:
        codes.append("supply_event_impact_profile_eligible")

    pre_price = _median_prices(pre)
    active_price = _median_prices(active)
    active_min = min((point.vwap_price for point in active), default=None)
    post_price = _median_prices(post)
    pre_activity = _median_activity(pre)
    active_activity = _median_activity(active)
    post_activity = _median_activity(post)

    drawdown = None
    if pre_price is not None and active_min is not None and pre_price > Decimal("0"):
        drawdown = max(Decimal("0"), (pre_price - active_min) / pre_price)

    activity_multiplier = None
    if (
        pre_activity is not None
        and active_activity is not None
        and pre_activity > Decimal("0")
    ):
        activity_multiplier = active_activity / pre_activity

    post_ratio = None
    if pre_price is not None and post_price is not None and pre_price > Decimal("0"):
        post_ratio = post_price / pre_price

    trough_point = _first_min_price(active)
    days_to_trough = None
    recovery_half_life = None
    if trough_point is not None:
        days_to_trough = _days_between(event.opens_at, trough_point.day_start_utc)
        if pre_price is not None and pre_price > trough_point.vwap_price:
            half_target = trough_point.vwap_price + (
                pre_price - trough_point.vwap_price
            ) / Decimal("2")
            recovery_point = next(
                (
                    point
                    for point in (*active, *post)
                    if point.day_start_utc >= trough_point.day_start_utc
                    and point.vwap_price >= half_target
                ),
                None,
            )
            if recovery_point is not None:
                recovery_half_life = _days_between(
                    trough_point.day_start_utc,
                    recovery_point.day_start_utc,
                )

    return SupplyEventImpact(
        event_key=event.event_key,
        family=event.family,
        as_of=as_of,
        event_visible=True,
        event_completed=event_completed,
        post_window_completed=post_window_completed,
        profile_eligible=eligible,
        pre_window_start=pre_start,
        event_opens_at=event.opens_at,
        event_closes_at=event.closes_at,
        post_window_end=post_end,
        pre_observed_day_count=len(pre),
        active_observed_day_count=len(active),
        post_observed_day_count=len(post),
        pre_price_median=pre_price,
        active_price_median=active_price,
        active_price_min=active_min,
        post_price_median=post_price,
        pre_activity_median=pre_activity,
        active_activity_median=active_activity,
        post_activity_median=post_activity,
        event_drawdown_ratio=drawdown,
        activity_multiplier=activity_multiplier,
        post_price_ratio=post_ratio,
        days_to_trough=days_to_trough,
        recovery_half_life_days=recovery_half_life,
        explanation_codes=tuple(codes),
    )


def build_supply_cycle_impact_profile(
    impacts: Iterable[SupplyEventImpact],
) -> SupplyCycleImpactProfile:
    values = tuple(impacts)
    eligible = tuple(impact for impact in values if impact.profile_eligible)
    codes: list[str] = []
    if not eligible:
        codes.append("supply_cycle_impact_history_unavailable")
    else:
        codes.append("supply_cycle_impact_history_available")

    return SupplyCycleImpactProfile(
        event_count=len(values),
        eligible_event_count=len(eligible),
        median_event_drawdown_ratio=_median_optional(
            tuple(impact.event_drawdown_ratio for impact in eligible)
        ),
        median_activity_multiplier=_median_optional(
            tuple(impact.activity_multiplier for impact in eligible)
        ),
        median_post_price_ratio=_median_optional(
            tuple(impact.post_price_ratio for impact in eligible)
        ),
        median_days_to_trough=_median_optional(
            tuple(impact.days_to_trough for impact in eligible)
        ),
        median_recovery_half_life_days=_median_optional(
            tuple(impact.recovery_half_life_days for impact in eligible)
        ),
        explanation_codes=tuple(codes),
    )


def _empty_impact(
    *,
    event: SupplyPoolEvent,
    as_of: datetime,
    pre_start: datetime,
    post_end: datetime,
    codes: tuple[str, ...],
) -> SupplyEventImpact:
    return SupplyEventImpact(
        event_key=event.event_key,
        family=event.family,
        as_of=as_of,
        event_visible=False,
        event_completed=False,
        post_window_completed=False,
        profile_eligible=False,
        pre_window_start=pre_start,
        event_opens_at=event.opens_at,
        event_closes_at=event.closes_at,
        post_window_end=post_end,
        pre_observed_day_count=0,
        active_observed_day_count=0,
        post_observed_day_count=0,
        pre_price_median=None,
        active_price_median=None,
        active_price_min=None,
        post_price_median=None,
        pre_activity_median=None,
        active_activity_median=None,
        post_activity_median=None,
        event_drawdown_ratio=None,
        activity_multiplier=None,
        post_price_ratio=None,
        days_to_trough=None,
        recovery_half_life_days=None,
        explanation_codes=codes,
    )


def _median_prices(points: tuple[DailyHistoricalTradeEvidence, ...]) -> Decimal | None:
    return _median(tuple(point.vwap_price for point in points))


def _median_activity(points: tuple[DailyHistoricalTradeEvidence, ...]) -> Decimal | None:
    return _median(tuple(point.reported_trade_volume for point in points))


def _median_optional(values: tuple[Decimal | None, ...]) -> Decimal | None:
    return _median(tuple(value for value in values if value is not None))


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _first_min_price(
    points: tuple[DailyHistoricalTradeEvidence, ...],
) -> DailyHistoricalTradeEvidence | None:
    if not points:
        return None
    minimum = min(point.vwap_price for point in points)
    return next(point for point in points if point.vwap_price == minimum)


def _days_between(left: datetime, right: datetime) -> Decimal:
    seconds = Decimal(str((right - left).total_seconds()))
    return seconds / Decimal("86400")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SupplyCycleImpactError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
