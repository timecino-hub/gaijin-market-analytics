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


SUPPLY_RECOVERY_VERSION = "1.0.0"


class SupplyRecoveryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SupplyRecoveryConfig:
    checkpoint_horizons_days: tuple[int, ...] = (30, 60, 90, 180)
    recovery_fractions: tuple[Decimal, ...] = (
        Decimal("0.25"),
        Decimal("0.50"),
        Decimal("0.75"),
    )
    minimum_pre_observed_days: int = 3

    def __post_init__(self) -> None:
        if not self.checkpoint_horizons_days:
            raise SupplyRecoveryError("checkpoint_horizons_days must be non-empty.")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in self.checkpoint_horizons_days
        ):
            raise SupplyRecoveryError("checkpoint horizons must be positive integers.")
        if tuple(sorted(set(self.checkpoint_horizons_days))) != self.checkpoint_horizons_days:
            raise SupplyRecoveryError(
                "checkpoint_horizons_days must be strictly increasing and unique."
            )
        if not self.recovery_fractions:
            raise SupplyRecoveryError("recovery_fractions must be non-empty.")
        if any(
            not isinstance(value, Decimal)
            or not value.is_finite()
            or not Decimal("0") < value < Decimal("1")
            for value in self.recovery_fractions
        ):
            raise SupplyRecoveryError("recovery fractions must be finite Decimals in (0, 1).")
        if tuple(sorted(set(self.recovery_fractions))) != self.recovery_fractions:
            raise SupplyRecoveryError(
                "recovery_fractions must be strictly increasing and unique."
            )
        if self.minimum_pre_observed_days <= 0:
            raise SupplyRecoveryError("minimum_pre_observed_days must be positive.")


@dataclass(frozen=True, slots=True)
class SupplyRecoveryCheckpoint:
    horizon_days: int
    target_at: datetime
    observed_at: datetime | None
    price: Decimal | None
    pre_price_ratio: Decimal | None
    available: bool


@dataclass(frozen=True, slots=True)
class SupplyRecoveryThreshold:
    recovery_fraction: Decimal
    target_price: Decimal | None
    first_recovered_at: datetime | None
    days_from_trough: Decimal | None
    reached: bool


@dataclass(frozen=True, slots=True)
class SupplyEventRecovery:
    event_key: str
    family: str
    as_of: datetime
    event_visible: bool
    event_completed: bool
    profile_eligible: bool
    event_opens_at: datetime
    event_closes_at: datetime
    recovery_cutoff_at: datetime
    next_same_family_event_opens_at: datetime | None
    pre_window_start: datetime
    pre_observed_day_count: int
    pre_price_median: Decimal | None
    trough_at: datetime | None
    trough_price: Decimal | None
    days_to_trough: Decimal | None
    checkpoints: tuple[SupplyRecoveryCheckpoint, ...]
    thresholds: tuple[SupplyRecoveryThreshold, ...]
    explanation_codes: tuple[str, ...]
    version: str = SUPPLY_RECOVERY_VERSION

    def checkpoint(self, horizon_days: int) -> SupplyRecoveryCheckpoint | None:
        return next(
            (value for value in self.checkpoints if value.horizon_days == horizon_days),
            None,
        )


@dataclass(frozen=True, slots=True)
class SupplyRecoveryHorizonProfile:
    horizon_days: int
    contributing_event_count: int
    median_pre_price_ratio: Decimal | None


@dataclass(frozen=True, slots=True)
class SupplyRecoveryThresholdProfile:
    recovery_fraction: Decimal
    reached_event_count: int
    median_days_from_trough: Decimal | None


@dataclass(frozen=True, slots=True)
class SupplyRecoveryProfile:
    event_count: int
    checkpoint_profiles: tuple[SupplyRecoveryHorizonProfile, ...]
    threshold_profiles: tuple[SupplyRecoveryThresholdProfile, ...]
    explanation_codes: tuple[str, ...]
    version: str = SUPPLY_RECOVERY_VERSION

    def checkpoint(self, horizon_days: int) -> SupplyRecoveryHorizonProfile | None:
        return next(
            (value for value in self.checkpoint_profiles if value.horizon_days == horizon_days),
            None,
        )


@dataclass(frozen=True, slots=True)
class SupplyRecoveryAnalysis:
    item_id: int
    as_of: datetime
    recoveries: tuple[SupplyEventRecovery, ...]
    profile: SupplyRecoveryProfile


def measure_supply_event_recovery(
    *,
    event: SupplyPoolEvent,
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    as_of: datetime,
    next_same_family_event_opens_at: datetime | None = None,
    config: SupplyRecoveryConfig | None = None,
) -> SupplyEventRecovery:
    """Measure a long post-return recovery curve without looking past ``as_of``.

    UTC-day observations are assigned conservatively: a day contributes to PRE
    or ACTIVE only when the full UTC day is contained in that phase.  This avoids
    treating a daily bucket that straddles an 11:00 UTC event boundary as if it
    belonged wholly to one side of the supply shock.

    Recovery is cut off before the next known same-family event, so a later
    supply injection cannot masquerade as part of the previous event's recovery.
    """

    cfg = config or SupplyRecoveryConfig()
    as_of = _utc(as_of, "as_of")
    next_open = (
        _utc(next_same_family_event_opens_at, "next_same_family_event_opens_at")
        if next_same_family_event_opens_at is not None
        else None
    )
    if next_open is not None and next_open <= event.opens_at:
        raise SupplyRecoveryError("next same-family event must open after the measured event.")

    duration = event.closes_at - event.opens_at
    pre_start = event.opens_at - duration
    cutoff = min(as_of, next_open) if next_open is not None else as_of

    if event.known_at > as_of or as_of <= pre_start:
        codes = (
            ("supply_event_not_known_as_of",)
            if event.known_at > as_of
            else ("recovery_observation_precedes_pre_window",)
        )
        return _empty_recovery(
            event=event,
            as_of=as_of,
            pre_start=pre_start,
            cutoff=cutoff,
            next_open=next_open,
            config=cfg,
            codes=codes,
        )

    buckets = tuple(historical_buckets)
    evidence = select_historical_trade_evidence(
        buckets,
        as_of=as_of,
        window_start=pre_start,
    )
    daily = aggregate_trade_evidence_by_utc_day(evidence.buckets)

    pre = tuple(
        point
        for point in daily
        if point.day_start_utc >= pre_start
        and _day_end(point) <= event.opens_at
    )
    active = tuple(
        point
        for point in daily
        if point.day_start_utc >= event.opens_at
        and _day_end(point) <= event.closes_at
    )
    recovery_points = tuple(
        point
        for point in daily
        if point.day_start_utc >= event.closes_at
        and _day_end(point) <= cutoff
    )

    pre_price = _median(tuple(point.vwap_price for point in pre))
    trough = _first_min(active)
    trough_price = trough.vwap_price if trough is not None else None
    days_to_trough = (
        _days_between(event.opens_at, trough.day_start_utc)
        if trough is not None
        else None
    )

    checkpoints = tuple(
        _checkpoint(
            horizon_days=horizon_days,
            event=event,
            points=recovery_points,
            pre_price=pre_price,
            cutoff=cutoff,
        )
        for horizon_days in cfg.checkpoint_horizons_days
    )

    search_points = tuple(
        point
        for point in (*active, *recovery_points)
        if trough is not None
        and point.day_start_utc >= trough.day_start_utc
        and _day_end(point) <= cutoff
    )
    thresholds = tuple(
        _threshold(
            fraction=fraction,
            pre_price=pre_price,
            trough=trough,
            points=search_points,
        )
        for fraction in cfg.recovery_fractions
    )

    codes: list[str] = []
    if len(pre) < cfg.minimum_pre_observed_days:
        codes.append("insufficient_pre_event_recovery_baseline")
    if trough is None:
        codes.append("active_event_trough_unavailable")
    if as_of < event.closes_at:
        codes.append("supply_event_not_completed_as_of")
    if next_open is not None and as_of >= next_open:
        codes.append("recovery_truncated_at_next_same_family_event")
    if any(checkpoint.available for checkpoint in checkpoints):
        codes.append("recovery_checkpoint_available")
    else:
        codes.append("recovery_checkpoint_history_unavailable")

    return SupplyEventRecovery(
        event_key=event.event_key,
        family=event.family,
        as_of=as_of,
        event_visible=True,
        event_completed=as_of >= event.closes_at,
        profile_eligible=(
            as_of >= event.closes_at
            and len(pre) >= cfg.minimum_pre_observed_days
            and pre_price is not None
        ),
        event_opens_at=event.opens_at,
        event_closes_at=event.closes_at,
        recovery_cutoff_at=cutoff,
        next_same_family_event_opens_at=next_open,
        pre_window_start=pre_start,
        pre_observed_day_count=len(pre),
        pre_price_median=pre_price,
        trough_at=trough.day_start_utc if trough is not None else None,
        trough_price=trough_price,
        days_to_trough=days_to_trough,
        checkpoints=checkpoints,
        thresholds=thresholds,
        explanation_codes=tuple(codes),
    )


def analyze_item_supply_recovery(
    *,
    item_id: int,
    events: Iterable[SupplyPoolEvent],
    memberships: Iterable[SupplyPoolMembership],
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    as_of: datetime,
    config: SupplyRecoveryConfig | None = None,
) -> SupplyRecoveryAnalysis:
    """Build recovery curves only for known, tradeable return memberships."""

    if item_id <= 0:
        raise SupplyRecoveryError("item_id must be positive.")
    as_of = _utc(as_of, "as_of")
    event_values = tuple(events)
    event_by_key = {event.event_key: event for event in event_values}
    if len(event_by_key) != len(event_values):
        raise SupplyRecoveryError("event_key values must be unique.")
    memberships = tuple(memberships)
    if any(member.event_key not in event_by_key for member in memberships):
        raise SupplyRecoveryError("membership references an unknown event_key.")
    if any(member.item_id != item_id for member in memberships):
        raise SupplyRecoveryError("membership item_id does not match requested item_id.")
    buckets = tuple(historical_buckets)
    if any(bucket.item_id != item_id for bucket in buckets):
        raise SupplyRecoveryError("historical bucket item_id does not match requested item_id.")

    visible_tradeable = tuple(
        member
        for member in memberships
        if member.known_at <= as_of
        and member.coupon_mode == "DIRECT_OR_COUPON"
        and event_by_key[member.event_key].known_at <= as_of
        and event_by_key[member.event_key].opens_at <= as_of
    )
    selected_events = tuple(
        sorted(
            (event_by_key[member.event_key] for member in visible_tradeable),
            key=lambda event: (event.opens_at, event.event_key),
        )
    )
    recoveries: list[SupplyEventRecovery] = []
    for event in selected_events:
        next_open = min(
            (
                candidate.opens_at
                for candidate in selected_events
                if candidate.family == event.family
                and candidate.opens_at > event.opens_at
                and candidate.known_at <= as_of
            ),
            default=None,
        )
        recoveries.append(
            measure_supply_event_recovery(
                event=event,
                historical_buckets=buckets,
                as_of=as_of,
                next_same_family_event_opens_at=next_open,
                config=config,
            )
        )

    values = tuple(recoveries)
    return SupplyRecoveryAnalysis(
        item_id=item_id,
        as_of=as_of,
        recoveries=values,
        profile=build_supply_recovery_profile(values, config=config),
    )


def build_supply_recovery_profile(
    recoveries: Iterable[SupplyEventRecovery],
    *,
    config: SupplyRecoveryConfig | None = None,
) -> SupplyRecoveryProfile:
    cfg = config or SupplyRecoveryConfig()
    values = tuple(recoveries)
    checkpoint_profiles = tuple(
        _checkpoint_profile(values, horizon_days)
        for horizon_days in cfg.checkpoint_horizons_days
    )
    threshold_profiles = tuple(
        _threshold_profile(values, fraction)
        for fraction in cfg.recovery_fractions
    )
    has_history = any(value.contributing_event_count for value in checkpoint_profiles)
    return SupplyRecoveryProfile(
        event_count=len(values),
        checkpoint_profiles=checkpoint_profiles,
        threshold_profiles=threshold_profiles,
        explanation_codes=(
            "supply_recovery_history_available"
            if has_history
            else "supply_recovery_history_unavailable",
        ),
    )


def _checkpoint(
    *,
    horizon_days: int,
    event: SupplyPoolEvent,
    points: tuple[DailyHistoricalTradeEvidence, ...],
    pre_price: Decimal | None,
    cutoff: datetime,
) -> SupplyRecoveryCheckpoint:
    target = event.closes_at + timedelta(days=horizon_days)
    point = next(
        (
            candidate
            for candidate in points
            if candidate.day_start_utc >= target and _day_end(candidate) <= cutoff
        ),
        None,
    )
    price = point.vwap_price if point is not None else None
    ratio = (
        price / pre_price
        if price is not None and pre_price is not None and pre_price > Decimal("0")
        else None
    )
    return SupplyRecoveryCheckpoint(
        horizon_days=horizon_days,
        target_at=target,
        observed_at=_day_end(point) if point is not None else None,
        price=price,
        pre_price_ratio=ratio,
        available=point is not None and ratio is not None,
    )


def _threshold(
    *,
    fraction: Decimal,
    pre_price: Decimal | None,
    trough: DailyHistoricalTradeEvidence | None,
    points: tuple[DailyHistoricalTradeEvidence, ...],
) -> SupplyRecoveryThreshold:
    if pre_price is None or trough is None or pre_price <= trough.vwap_price:
        return SupplyRecoveryThreshold(
            recovery_fraction=fraction,
            target_price=None,
            first_recovered_at=None,
            days_from_trough=None,
            reached=False,
        )
    target = trough.vwap_price + (pre_price - trough.vwap_price) * fraction
    point = next((value for value in points if value.vwap_price >= target), None)
    return SupplyRecoveryThreshold(
        recovery_fraction=fraction,
        target_price=target,
        first_recovered_at=point.day_start_utc if point is not None else None,
        days_from_trough=(
            _days_between(trough.day_start_utc, point.day_start_utc)
            if point is not None
            else None
        ),
        reached=point is not None,
    )


def _checkpoint_profile(
    recoveries: tuple[SupplyEventRecovery, ...],
    horizon_days: int,
) -> SupplyRecoveryHorizonProfile:
    ratios = tuple(
        checkpoint.pre_price_ratio
        for recovery in recoveries
        for checkpoint in recovery.checkpoints
        if recovery.profile_eligible
        and checkpoint.horizon_days == horizon_days
        and checkpoint.available
        and checkpoint.pre_price_ratio is not None
    )
    return SupplyRecoveryHorizonProfile(
        horizon_days=horizon_days,
        contributing_event_count=len(ratios),
        median_pre_price_ratio=_median(ratios),
    )


def _threshold_profile(
    recoveries: tuple[SupplyEventRecovery, ...],
    fraction: Decimal,
) -> SupplyRecoveryThresholdProfile:
    days = tuple(
        threshold.days_from_trough
        for recovery in recoveries
        for threshold in recovery.thresholds
        if recovery.profile_eligible
        and threshold.recovery_fraction == fraction
        and threshold.reached
        and threshold.days_from_trough is not None
    )
    return SupplyRecoveryThresholdProfile(
        recovery_fraction=fraction,
        reached_event_count=len(days),
        median_days_from_trough=_median(days),
    )


def _empty_recovery(
    *,
    event: SupplyPoolEvent,
    as_of: datetime,
    pre_start: datetime,
    cutoff: datetime,
    next_open: datetime | None,
    config: SupplyRecoveryConfig,
    codes: tuple[str, ...],
) -> SupplyEventRecovery:
    return SupplyEventRecovery(
        event_key=event.event_key,
        family=event.family,
        as_of=as_of,
        event_visible=False,
        event_completed=False,
        profile_eligible=False,
        event_opens_at=event.opens_at,
        event_closes_at=event.closes_at,
        recovery_cutoff_at=cutoff,
        next_same_family_event_opens_at=next_open,
        pre_window_start=pre_start,
        pre_observed_day_count=0,
        pre_price_median=None,
        trough_at=None,
        trough_price=None,
        days_to_trough=None,
        checkpoints=tuple(
            SupplyRecoveryCheckpoint(
                horizon_days=days,
                target_at=event.closes_at + timedelta(days=days),
                observed_at=None,
                price=None,
                pre_price_ratio=None,
                available=False,
            )
            for days in config.checkpoint_horizons_days
        ),
        thresholds=tuple(
            SupplyRecoveryThreshold(
                recovery_fraction=fraction,
                target_price=None,
                first_recovered_at=None,
                days_from_trough=None,
                reached=False,
            )
            for fraction in config.recovery_fractions
        ),
        explanation_codes=codes,
    )


def _day_end(point: DailyHistoricalTradeEvidence | None) -> datetime:
    if point is None:
        raise SupplyRecoveryError("day evidence is required.")
    return point.day_start_utc + timedelta(days=1)


def _first_min(
    points: tuple[DailyHistoricalTradeEvidence, ...],
) -> DailyHistoricalTradeEvidence | None:
    if not points:
        return None
    minimum = min(point.vwap_price for point in points)
    return next(point for point in points if point.vwap_price == minimum)


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _days_between(left: datetime, right: datetime) -> Decimal:
    return Decimal(str((right - left).total_seconds())) / Decimal("86400")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SupplyRecoveryError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
