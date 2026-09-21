from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Literal

from .historical_trades import (
    DailyHistoricalTradeEvidence,
    HistoricalTradeBucketEvidence,
    aggregate_trade_evidence_by_utc_day,
    select_historical_trade_evidence,
)
from .supply_cycles import SupplyPoolEvent


SUPPLY_ABSORPTION_EXIT_VERSION = "1.0.0"

AbsorptionAxisState = Literal[
    "UNAVAILABLE",
    "NOT_APPLICABLE",
    "NOT_SETTLED",
    "SETTLED",
]
SupplyAbsorptionExitState = Literal[
    "INSUFFICIENT_EVIDENCE",
    "EVENT_NOT_COMPLETED",
    "SUPERSEDED_BY_NEXT_SUPPLY_EVENT",
    "NO_CLEAR_SUPPLY_SHOCK",
    "ABSORPTION_ACTIVE",
    "ABSORPTION_EXIT_CONFIRMED",
]
AbsorptionWithholdPolicy = Literal[
    "LEGACY_ONLY",
    "EVIDENCE_EXTENDS_LEGACY",
    "EVIDENCE_REPLACES_LEGACY",
]
ABSORPTION_WITHHOLD_POLICIES: tuple[AbsorptionWithholdPolicy, ...] = (
    "LEGACY_ONLY",
    "EVIDENCE_EXTENDS_LEGACY",
    "EVIDENCE_REPLACES_LEGACY",
)


class SupplyAbsorptionExitError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AbsorptionExitConfig:
    """Thresholds for declaring a completed supply shock no longer absorbing.

    The defaults are deliberate research defaults, not calibrated production
    thresholds. They are chosen so that a single quiet day cannot flip the state
    and so that a sparse series cannot claim settlement it has not observed.
    """

    dwell_observed_days: int = 5
    maximum_dwell_span_days: int = 10
    trend_observed_days: int = 10
    minimum_post_observed_days: int = 10
    minimum_pre_observed_days: int = 3
    minimum_active_observed_days: int = 1
    activity_settled_excess: Decimal = Decimal("0.25")
    price_decline_tolerance: Decimal = Decimal("0.03")
    price_shock_margin: Decimal = Decimal("0.05")
    activity_spike_margin: Decimal = Decimal("0.50")

    def __post_init__(self) -> None:
        for field_name in (
            "dwell_observed_days",
            "maximum_dwell_span_days",
            "trend_observed_days",
            "minimum_post_observed_days",
            "minimum_pre_observed_days",
            "minimum_active_observed_days",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SupplyAbsorptionExitError(f"{field_name} must be a positive integer.")
        if self.trend_observed_days < 2:
            raise SupplyAbsorptionExitError("trend_observed_days must be at least 2.")
        if self.maximum_dwell_span_days < self.dwell_observed_days:
            raise SupplyAbsorptionExitError(
                "maximum_dwell_span_days must not be shorter than dwell_observed_days."
            )
        if self.minimum_post_observed_days < max(
            self.dwell_observed_days, self.trend_observed_days
        ):
            raise SupplyAbsorptionExitError(
                "minimum_post_observed_days must cover the dwell and trend windows."
            )
        for field_name in (
            "activity_settled_excess",
            "price_decline_tolerance",
            "price_shock_margin",
            "activity_spike_margin",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise SupplyAbsorptionExitError(f"{field_name} must be a finite Decimal.")
        if not Decimal("0") <= self.activity_settled_excess <= Decimal("1"):
            raise SupplyAbsorptionExitError("activity_settled_excess must be in [0, 1].")
        if not Decimal("0") <= self.price_decline_tolerance < Decimal("1"):
            raise SupplyAbsorptionExitError("price_decline_tolerance must be in [0, 1).")
        if not Decimal("0") <= self.price_shock_margin < Decimal("1"):
            raise SupplyAbsorptionExitError("price_shock_margin must be in [0, 1).")
        if self.activity_spike_margin < Decimal("0"):
            raise SupplyAbsorptionExitError("activity_spike_margin must be non-negative.")


@dataclass(frozen=True, slots=True)
class SupplyAbsorptionExitSnapshot:
    item_id: int
    event_key: str
    family: str
    as_of: datetime
    event_opens_at: datetime
    event_closes_at: datetime
    next_same_family_event_opens_at: datetime | None
    state: SupplyAbsorptionExitState
    activity_axis: AbsorptionAxisState
    price_axis: AbsorptionAxisState
    clear_supply_shock_detected: bool
    price_shock_detected: bool
    activity_spike_detected: bool
    pre_observed_day_count: int
    active_observed_day_count: int
    post_observed_day_count: int
    dwell_observed_day_count: int
    dwell_calendar_span_days: int | None
    trend_observed_day_count: int
    pre_price_median: Decimal | None
    active_trough_price: Decimal | None
    post_trough_price: Decimal | None
    post_trough_day_start_utc: datetime | None
    post_trough_age_observed_days: int | None
    pre_activity_median: Decimal | None
    active_activity_median: Decimal | None
    dwell_activity_excess_peak: Decimal | None
    trend_older_price_median: Decimal | None
    trend_newer_price_median: Decimal | None
    trend_price_change_ratio: Decimal | None
    fresh_post_event_low: bool | None
    days_since_event_close: Decimal | None
    legacy_absorption_active: bool
    legacy_absorption_ends_at: datetime
    explanation_codes: tuple[str, ...]
    version: str = SUPPLY_ABSORPTION_EXIT_VERSION

    @property
    def absorption_active(self) -> bool | None:
        """Tri-state evidence answer: True, False, or None when undetermined."""

        if self.state == "ABSORPTION_ACTIVE":
            return True
        if self.state in {"ABSORPTION_EXIT_CONFIRMED", "NO_CLEAR_SUPPLY_SHOCK"}:
            return False
        return None


@dataclass(frozen=True, slots=True)
class AbsorptionWithholdDecision:
    policy: AbsorptionWithholdPolicy
    legacy_absorption_active: bool
    evidence_state: SupplyAbsorptionExitState | None
    evidence_absorption_active: bool | None
    withhold: bool
    differs_from_legacy: bool
    explanation_codes: tuple[str, ...]
    version: str = SUPPLY_ABSORPTION_EXIT_VERSION


def legacy_absorption_window_end(event: SupplyPoolEvent) -> datetime:
    """End of the legacy fixed-duration post-event withholding window.

    The legacy rule in :mod:`supply_cycles` treats the item as absorbing for one
    further event-duration after the event closes. It is reproduced here so the
    evidence-based state can be compared against it without importing the
    state-machine internals.
    """

    return event.closes_at + (event.closes_at - event.opens_at)


def measure_supply_absorption_exit(
    *,
    item_id: int,
    event: SupplyPoolEvent,
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    as_of: datetime,
    next_same_family_event_opens_at: datetime | None = None,
    config: AbsorptionExitConfig | None = None,
) -> SupplyAbsorptionExitSnapshot:
    """Decide whether a completed supply shock is still being actively absorbed.

    This answers a narrower question than scarcity rebuild: not "how far has the
    item recovered" but "has the market stopped reacting to the injection".  An
    item may exit absorption permanently below its pre-event price level, so the
    exit test is built from stopping conditions, never from a recovery level.

    Two axes must both stop moving, and must stay stopped for a dwell window of
    consecutive *observed* UTC days:

    * activity has decayed from the active-event spike back toward the
      pre-event activity baseline, and
    * the post-event price is neither making fresh lows nor still trending down.

    Only buckets completed by ``as_of`` are visible, a UTC day contributes to a
    phase only when the whole day falls inside it, and missing days are never
    treated as quiet days. A later same-family supply event supersedes the
    measurement rather than extending it.
    """

    cfg = config or AbsorptionExitConfig()
    if item_id <= 0:
        raise SupplyAbsorptionExitError("item_id must be positive.")
    as_of = _utc(as_of, "as_of")
    next_open = (
        _utc(next_same_family_event_opens_at, "next_same_family_event_opens_at")
        if next_same_family_event_opens_at is not None
        else None
    )
    if next_open is not None and next_open <= event.opens_at:
        raise SupplyAbsorptionExitError(
            "next same-family event must open after the measured event."
        )

    duration = event.closes_at - event.opens_at
    pre_start = event.opens_at - duration
    legacy_ends_at = legacy_absorption_window_end(event)
    legacy_active = event.closes_at <= as_of < legacy_ends_at
    codes: list[str] = []

    if event.known_at > as_of:
        codes.append("supply_event_not_known_as_of")
        return _empty_snapshot(
            item_id=item_id,
            event=event,
            as_of=as_of,
            next_open=next_open,
            state="INSUFFICIENT_EVIDENCE",
            legacy_active=legacy_active,
            legacy_ends_at=legacy_ends_at,
            codes=tuple(codes),
        )
    if as_of <= pre_start:
        codes.append("absorption_exit_observation_precedes_pre_window")
        return _empty_snapshot(
            item_id=item_id,
            event=event,
            as_of=as_of,
            next_open=next_open,
            state="INSUFFICIENT_EVIDENCE",
            legacy_active=legacy_active,
            legacy_ends_at=legacy_ends_at,
            codes=tuple(codes),
        )
    if as_of < event.closes_at:
        codes.append("supply_event_not_completed_as_of")
        return _empty_snapshot(
            item_id=item_id,
            event=event,
            as_of=as_of,
            next_open=next_open,
            state="EVENT_NOT_COMPLETED",
            legacy_active=legacy_active,
            legacy_ends_at=legacy_ends_at,
            codes=tuple(codes),
        )

    superseded = next_open is not None and as_of >= next_open
    cutoff = next_open if superseded else as_of
    evidence = select_historical_trade_evidence(
        tuple(historical_buckets),
        as_of=cutoff,
        window_start=pre_start,
    )
    daily = aggregate_trade_evidence_by_utc_day(evidence.buckets)

    pre = tuple(
        point
        for point in daily
        if point.day_start_utc >= pre_start and _day_end(point) <= event.opens_at
    )
    active = tuple(
        point
        for point in daily
        if point.day_start_utc >= event.opens_at and _day_end(point) <= event.closes_at
    )
    post = tuple(
        point
        for point in daily
        if point.day_start_utc >= event.closes_at and _day_end(point) <= cutoff
    )
    dwell = post[-cfg.dwell_observed_days :]
    trend = post[-(cfg.trend_observed_days) :]

    pre_price = _median(tuple(point.vwap_price for point in pre))
    active_trough = _first_min(active)
    post_trough_index, post_trough = _first_min_with_index(post)
    pre_activity = _median(tuple(point.reported_trade_volume for point in pre))
    active_activity = _median(tuple(point.reported_trade_volume for point in active))

    dwell_span = _calendar_span_days(dwell)
    price_shock = (
        pre_price is not None
        and active_trough is not None
        and active_trough.vwap_price
        <= pre_price * (Decimal("1") - cfg.price_shock_margin)
    )
    activity_spike = (
        pre_activity is not None
        and active_activity is not None
        and active_activity >= pre_activity * (Decimal("1") + cfg.activity_spike_margin)
    )

    activity_axis, activity_excess_peak = _activity_axis(
        pre_activity=pre_activity,
        active_activity=active_activity,
        dwell=dwell,
        activity_spike=activity_spike,
        cfg=cfg,
    )
    (
        price_axis,
        older_median,
        newer_median,
        change_ratio,
        fresh_low,
    ) = _price_axis(
        post=post,
        trend=trend,
        post_trough_index=post_trough_index,
        cfg=cfg,
    )

    evidence_ok = True
    if len(pre) < cfg.minimum_pre_observed_days:
        codes.append("insufficient_pre_event_absorption_baseline")
        evidence_ok = False
    if len(active) < cfg.minimum_active_observed_days:
        codes.append("insufficient_active_event_absorption_evidence")
        evidence_ok = False
    if len(post) < cfg.minimum_post_observed_days:
        codes.append("insufficient_post_event_absorption_evidence")
        evidence_ok = False
    if dwell_span is not None and dwell_span > cfg.maximum_dwell_span_days:
        codes.append("absorption_dwell_window_too_sparse")
        evidence_ok = False

    if superseded:
        codes.append("absorption_exit_superseded_by_next_same_family_supply_event")
        state: SupplyAbsorptionExitState = "SUPERSEDED_BY_NEXT_SUPPLY_EVENT"
    elif not evidence_ok:
        state = "INSUFFICIENT_EVIDENCE"
    elif not (price_shock or activity_spike):
        codes.append("no_clear_post_event_supply_shock_to_absorb")
        state = "NO_CLEAR_SUPPLY_SHOCK"
    elif "UNAVAILABLE" in (activity_axis, price_axis):
        codes.append("absorption_axis_evidence_unavailable")
        state = "INSUFFICIENT_EVIDENCE"
    elif "NOT_SETTLED" in (activity_axis, price_axis):
        state = "ABSORPTION_ACTIVE"
    else:
        state = "ABSORPTION_EXIT_CONFIRMED"

    codes.extend((
        f"absorption_activity_axis_{activity_axis.lower()}",
        f"absorption_price_axis_{price_axis.lower()}",
    ))
    if price_shock:
        codes.append("post_event_price_shock_detected")
    if activity_spike:
        codes.append("post_event_activity_spike_detected")
    if state == "ABSORPTION_EXIT_CONFIRMED" and legacy_active:
        codes.append("absorption_exit_earlier_than_legacy_fixed_duration")
    if state == "ABSORPTION_ACTIVE" and not legacy_active:
        codes.append("absorption_still_active_after_legacy_fixed_duration")
    codes.append("absorption_exit_measures_regime_not_future_return")

    return SupplyAbsorptionExitSnapshot(
        item_id=item_id,
        event_key=event.event_key,
        family=event.family,
        as_of=as_of,
        event_opens_at=event.opens_at,
        event_closes_at=event.closes_at,
        next_same_family_event_opens_at=next_open,
        state=state,
        activity_axis=activity_axis,
        price_axis=price_axis,
        clear_supply_shock_detected=price_shock or activity_spike,
        price_shock_detected=price_shock,
        activity_spike_detected=activity_spike,
        pre_observed_day_count=len(pre),
        active_observed_day_count=len(active),
        post_observed_day_count=len(post),
        dwell_observed_day_count=len(dwell),
        dwell_calendar_span_days=dwell_span,
        trend_observed_day_count=len(trend),
        pre_price_median=pre_price,
        active_trough_price=active_trough.vwap_price if active_trough is not None else None,
        post_trough_price=post_trough.vwap_price if post_trough is not None else None,
        post_trough_day_start_utc=(
            post_trough.day_start_utc if post_trough is not None else None
        ),
        post_trough_age_observed_days=(
            len(post) - 1 - post_trough_index if post_trough_index is not None else None
        ),
        pre_activity_median=pre_activity,
        active_activity_median=active_activity,
        dwell_activity_excess_peak=activity_excess_peak,
        trend_older_price_median=older_median,
        trend_newer_price_median=newer_median,
        trend_price_change_ratio=change_ratio,
        fresh_post_event_low=fresh_low,
        days_since_event_close=_days_since(event.closes_at, as_of),
        legacy_absorption_active=legacy_active,
        legacy_absorption_ends_at=legacy_ends_at,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def resolve_absorption_withholding(
    *,
    legacy_absorption_active: bool,
    snapshot: SupplyAbsorptionExitSnapshot | None = None,
    policy: AbsorptionWithholdPolicy = "LEGACY_ONLY",
) -> AbsorptionWithholdDecision:
    """Combine the legacy fixed-duration rule with absorption-exit evidence.

    ``LEGACY_ONLY`` reproduces current production behaviour exactly and is the
    default, so adding evidence to a caller cannot silently change published
    scores. ``EVIDENCE_EXTENDS_LEGACY`` can only ever withhold *more* than the
    legacy rule, which is the conservative direction: it keeps a score
    unpublished while the item's own series still shows an active supply shock.
    ``EVIDENCE_REPLACES_LEGACY`` lets confirmed evidence also shorten the legacy
    window and must not be enabled before out-of-time evaluation supports it.
    """

    if policy not in ABSORPTION_WITHHOLD_POLICIES:
        raise SupplyAbsorptionExitError("unsupported absorption withholding policy.")
    if not isinstance(legacy_absorption_active, bool):
        raise SupplyAbsorptionExitError("legacy_absorption_active must be a bool.")

    evidence_state = snapshot.state if snapshot is not None else None
    evidence_active = snapshot.absorption_active if snapshot is not None else None
    codes: list[str] = [f"absorption_withhold_policy_{policy.lower()}"]

    if policy == "LEGACY_ONLY" or snapshot is None:
        withhold = legacy_absorption_active
        if snapshot is None and policy != "LEGACY_ONLY":
            codes.append("absorption_exit_evidence_absent_legacy_fallback")
    elif policy == "EVIDENCE_EXTENDS_LEGACY":
        withhold = legacy_absorption_active or evidence_active is True
        if evidence_active is True and not legacy_absorption_active:
            codes.append("absorption_withheld_beyond_legacy_window_by_evidence")
    else:
        if evidence_active is None:
            withhold = legacy_absorption_active
            codes.append("absorption_exit_evidence_undetermined_legacy_fallback")
        else:
            withhold = evidence_active
            if not withhold and legacy_absorption_active:
                codes.append("absorption_released_inside_legacy_window_by_evidence")
            if withhold and not legacy_absorption_active:
                codes.append("absorption_withheld_beyond_legacy_window_by_evidence")

    codes.append(
        "supply_absorption_withheld" if withhold else "supply_absorption_not_withheld"
    )
    return AbsorptionWithholdDecision(
        policy=policy,
        legacy_absorption_active=legacy_absorption_active,
        evidence_state=evidence_state,
        evidence_absorption_active=evidence_active,
        withhold=withhold,
        differs_from_legacy=withhold != legacy_absorption_active,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def _activity_axis(
    *,
    pre_activity: Decimal | None,
    active_activity: Decimal | None,
    dwell: tuple[DailyHistoricalTradeEvidence, ...],
    activity_spike: bool,
    cfg: AbsorptionExitConfig,
) -> tuple[AbsorptionAxisState, Decimal | None]:
    if pre_activity is None or active_activity is None:
        return "UNAVAILABLE", None
    if not activity_spike or active_activity <= pre_activity:
        return "NOT_APPLICABLE", None
    if len(dwell) < cfg.dwell_observed_days:
        return "UNAVAILABLE", None
    span = active_activity - pre_activity
    peak = max(
        max(Decimal("0"), (point.reported_trade_volume - pre_activity) / span)
        for point in dwell
    )
    if peak <= cfg.activity_settled_excess:
        return "SETTLED", peak
    return "NOT_SETTLED", peak


def _price_axis(
    *,
    post: tuple[DailyHistoricalTradeEvidence, ...],
    trend: tuple[DailyHistoricalTradeEvidence, ...],
    post_trough_index: int | None,
    cfg: AbsorptionExitConfig,
) -> tuple[
    AbsorptionAxisState,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    bool | None,
]:
    if (
        len(post) < cfg.minimum_post_observed_days
        or len(trend) < cfg.trend_observed_days
        or post_trough_index is None
    ):
        return "UNAVAILABLE", None, None, None, None
    half = len(trend) // 2
    window = trend[len(trend) - 2 * half :]
    older = window[:half]
    newer = window[half:]
    older_median = _median(tuple(point.vwap_price for point in older))
    newer_median = _median(tuple(point.vwap_price for point in newer))
    if older_median is None or newer_median is None or older_median <= Decimal("0"):
        return "UNAVAILABLE", older_median, newer_median, None, None
    change_ratio = (newer_median - older_median) / older_median
    fresh_low = post_trough_index >= len(post) - cfg.dwell_observed_days
    not_declining = change_ratio >= -cfg.price_decline_tolerance
    axis: AbsorptionAxisState = (
        "SETTLED" if not_declining and not fresh_low else "NOT_SETTLED"
    )
    return axis, older_median, newer_median, change_ratio, fresh_low


def _empty_snapshot(
    *,
    item_id: int,
    event: SupplyPoolEvent,
    as_of: datetime,
    next_open: datetime | None,
    state: SupplyAbsorptionExitState,
    legacy_active: bool,
    legacy_ends_at: datetime,
    codes: tuple[str, ...],
) -> SupplyAbsorptionExitSnapshot:
    return SupplyAbsorptionExitSnapshot(
        item_id=item_id,
        event_key=event.event_key,
        family=event.family,
        as_of=as_of,
        event_opens_at=event.opens_at,
        event_closes_at=event.closes_at,
        next_same_family_event_opens_at=next_open,
        state=state,
        activity_axis="UNAVAILABLE",
        price_axis="UNAVAILABLE",
        clear_supply_shock_detected=False,
        price_shock_detected=False,
        activity_spike_detected=False,
        pre_observed_day_count=0,
        active_observed_day_count=0,
        post_observed_day_count=0,
        dwell_observed_day_count=0,
        dwell_calendar_span_days=None,
        trend_observed_day_count=0,
        pre_price_median=None,
        active_trough_price=None,
        post_trough_price=None,
        post_trough_day_start_utc=None,
        post_trough_age_observed_days=None,
        pre_activity_median=None,
        active_activity_median=None,
        dwell_activity_excess_peak=None,
        trend_older_price_median=None,
        trend_newer_price_median=None,
        trend_price_change_ratio=None,
        fresh_post_event_low=None,
        days_since_event_close=(
            _days_since(event.closes_at, as_of) if as_of >= event.closes_at else None
        ),
        legacy_absorption_active=legacy_active,
        legacy_absorption_ends_at=legacy_ends_at,
        explanation_codes=codes,
    )


def _first_min(
    points: tuple[DailyHistoricalTradeEvidence, ...],
) -> DailyHistoricalTradeEvidence | None:
    return _first_min_with_index(points)[1]


def _first_min_with_index(
    points: tuple[DailyHistoricalTradeEvidence, ...],
) -> tuple[int | None, DailyHistoricalTradeEvidence | None]:
    if not points:
        return None, None
    minimum = min(point.vwap_price for point in points)
    index = next(
        position for position, point in enumerate(points) if point.vwap_price == minimum
    )
    return index, points[index]


def _calendar_span_days(
    points: tuple[DailyHistoricalTradeEvidence, ...],
) -> int | None:
    if not points:
        return None
    return (points[-1].day_start_utc - points[0].day_start_utc).days + 1


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _day_end(point: DailyHistoricalTradeEvidence) -> datetime:
    return point.day_start_utc + timedelta(days=1)


def _days_since(left: datetime, right: datetime) -> Decimal:
    return Decimal(str((right - left).total_seconds())) / Decimal("86400")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SupplyAbsorptionExitError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
