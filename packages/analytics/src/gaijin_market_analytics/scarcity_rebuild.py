from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Iterable, Literal

from .historical_trades import (
    DailyHistoricalTradeEvidence,
    HistoricalTradeBucketEvidence,
    aggregate_trade_evidence_by_utc_day,
    select_historical_trade_evidence,
)
from .reissue_exposure import ReissueExposureFeatures
from .supply_cycles import SupplyPoolEvent


SCARCITY_REBUILD_VERSION = "1.0.0"

PriceRebuildRegime = Literal[
    "UNAVAILABLE",
    "NO_CLEAR_PRICE_SHOCK",
    "AT_OR_BELOW_TROUGH",
    "PARTIAL_RECOVERY",
    "AT_OR_ABOVE_PRE_EVENT",
]
ActivityAbsorptionRegime = Literal[
    "UNAVAILABLE",
    "NO_CLEAR_ACTIVITY_SPIKE",
    "AT_OR_ABOVE_ACTIVE_EVENT",
    "ELEVATED_ABOVE_PRE_EVENT",
    "NORMALIZED_TO_PRE_EVENT",
]
ScarcityRebuildState = Literal[
    "INSUFFICIENT_EVIDENCE",
    "EVENT_NOT_COMPLETED",
    "SUPERSEDED_BY_NEXT_SUPPLY_EVENT",
    "NO_CLEAR_TWO_AXIS_SUPPLY_SHOCK",
    "ONGOING_ABSORPTION",
    "POST_EVENT_WEAK_DEMAND",
    "REBUILDING_WITH_ELEVATED_ACTIVITY",
    "SCARCITY_REBUILDING",
    "DEMAND_DOMINANT_RECOVERY",
    "SCARCITY_REBUILT",
]


class ScarcityRebuildError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScarcityRebuildConfig:
    recent_observed_day_count: int = 3
    minimum_pre_observed_days: int = 3
    minimum_active_observed_days: int = 1

    def __post_init__(self) -> None:
        for field_name in (
            "recent_observed_day_count",
            "minimum_pre_observed_days",
            "minimum_active_observed_days",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ScarcityRebuildError(f"{field_name} must be a positive integer.")


@dataclass(frozen=True, slots=True)
class ScarcityRebuildSnapshot:
    item_id: int
    event_key: str
    family: str
    as_of: datetime
    event_opens_at: datetime
    event_closes_at: datetime
    next_same_family_event_opens_at: datetime | None
    state: ScarcityRebuildState
    price_regime: PriceRebuildRegime
    activity_regime: ActivityAbsorptionRegime
    pre_observed_day_count: int
    active_observed_day_count: int
    post_observed_day_count: int
    recent_observed_day_count: int
    pre_price_median: Decimal | None
    active_trough_price: Decimal | None
    recent_price_median: Decimal | None
    latest_post_trade_price: Decimal | None
    latest_post_trade_observed_at: datetime | None
    pre_activity_median: Decimal | None
    active_activity_median: Decimal | None
    recent_activity_median: Decimal | None
    price_recovery_fraction: Decimal | None
    activity_normalization_fraction: Decimal | None
    rebuild_progress_floor: Decimal | None
    days_since_event_close: Decimal | None
    completed_tradeable_reissue_count: int | None
    completed_account_only_return_count: int | None
    distinct_reissue_family_count: int | None
    explanation_codes: tuple[str, ...]
    version: str = SCARCITY_REBUILD_VERSION


@dataclass(frozen=True, slots=True)
class ScarcityRebuildForwardOutcome:
    item_id: int
    event_key: str
    snapshot_as_of: datetime
    forward_horizon_days: int
    target_at: datetime
    evaluation_as_of: datetime
    observed_at: datetime | None
    entry_proxy_price: Decimal | None
    exit_proxy_price: Decimal | None
    sale_proceeds: Decimal | None
    fee_amount: Decimal | None
    net_profit: Decimal | None
    net_roi: Decimal | None
    outcome_available: bool
    explanation_codes: tuple[str, ...]
    version: str = SCARCITY_REBUILD_VERSION


def measure_scarcity_rebuild(
    *,
    item_id: int,
    event: SupplyPoolEvent,
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    as_of: datetime,
    next_same_family_event_opens_at: datetime | None = None,
    reissue_exposure: ReissueExposureFeatures | None = None,
    config: ScarcityRebuildConfig | None = None,
) -> ScarcityRebuildSnapshot:
    """Measure post-reissue supply absorption with item-local event baselines.

    The model deliberately avoids a global scarcity penalty. Price rebuild is
    normalized from the active-event trough back toward the pre-event median.
    Activity absorption is normalized from the active-event activity spike back
    toward the pre-event activity median. ``rebuild_progress_floor`` is the
    weaker of those two normalized dimensions, clipped to [0, 1]. It is a
    descriptive progress measure, not a future-return or execution probability.

    Only fully completed UTC-day evidence is used in PRE/ACTIVE/POST phases, so
    daily buckets that straddle non-midnight event boundaries are not assigned to
    either side. The recent state uses the most recent N *observed* post-event
    days, rather than interpreting missing days as zero activity.
    """

    cfg = config or ScarcityRebuildConfig()
    if item_id <= 0:
        raise ScarcityRebuildError("item_id must be positive.")
    as_of = _utc(as_of, "as_of")
    next_open = (
        _utc(next_same_family_event_opens_at, "next_same_family_event_opens_at")
        if next_same_family_event_opens_at is not None
        else None
    )
    if next_open is not None and next_open <= event.opens_at:
        raise ScarcityRebuildError("next same-family event must open after the measured event.")
    if reissue_exposure is not None:
        if reissue_exposure.item_id != item_id:
            raise ScarcityRebuildError("reissue_exposure item_id does not match.")
        if reissue_exposure.as_of > as_of:
            raise ScarcityRebuildError("reissue_exposure must not contain future information.")

    duration = event.closes_at - event.opens_at
    pre_start = event.opens_at - duration
    codes: list[str] = []

    if event.known_at > as_of or as_of <= pre_start:
        codes.append(
            "supply_event_not_known_as_of"
            if event.known_at > as_of
            else "scarcity_rebuild_observation_precedes_pre_window"
        )
        return _empty_snapshot(
            item_id=item_id,
            event=event,
            as_of=as_of,
            next_open=next_open,
            reissue_exposure=reissue_exposure,
            state="INSUFFICIENT_EVIDENCE",
            codes=tuple(codes),
        )

    if as_of < event.closes_at:
        codes.append("supply_event_not_completed_as_of")
        return _empty_snapshot(
            item_id=item_id,
            event=event,
            as_of=as_of,
            next_open=next_open,
            reissue_exposure=reissue_exposure,
            state="EVENT_NOT_COMPLETED",
            codes=tuple(codes),
        )

    superseded = next_open is not None and as_of >= next_open
    cutoff = next_open if superseded else as_of
    # ``cutoff`` may equal pre_start only for malformed chronological inputs;
    # event validation guarantees event.closes_at > pre_start.
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
    recent = post[-cfg.recent_observed_day_count :]

    pre_price = _median(tuple(point.vwap_price for point in pre))
    trough = _first_min(active)
    recent_price = _median(tuple(point.vwap_price for point in recent))
    pre_activity = _median(tuple(point.reported_trade_volume for point in pre))
    active_activity = _median(tuple(point.reported_trade_volume for point in active))
    recent_activity = _median(tuple(point.reported_trade_volume for point in recent))

    price_regime, price_fraction = _price_axis(
        pre_price=pre_price,
        trough_price=trough.vwap_price if trough is not None else None,
        recent_price=recent_price,
    )
    activity_regime, activity_fraction = _activity_axis(
        pre_activity=pre_activity,
        active_activity=active_activity,
        recent_activity=recent_activity,
    )
    progress = (
        min(_clamp01(price_fraction), _clamp01(activity_fraction))
        if price_fraction is not None and activity_fraction is not None
        else None
    )

    evidence_ok = True
    if len(pre) < cfg.minimum_pre_observed_days:
        codes.append("insufficient_pre_event_scarcity_baseline")
        evidence_ok = False
    if len(active) < cfg.minimum_active_observed_days:
        codes.append("insufficient_active_event_scarcity_evidence")
        evidence_ok = False
    if len(recent) < cfg.recent_observed_day_count:
        codes.append("insufficient_recent_post_event_scarcity_evidence")
        evidence_ok = False
    if pre_price is None or trough is None or recent_price is None:
        codes.append("scarcity_price_axis_unavailable")
        evidence_ok = False
    if pre_activity is None or active_activity is None or recent_activity is None:
        codes.append("scarcity_activity_axis_unavailable")
        evidence_ok = False

    if superseded:
        codes.append("scarcity_rebuild_superseded_by_next_same_family_supply_event")
        state: ScarcityRebuildState = "SUPERSEDED_BY_NEXT_SUPPLY_EVENT"
    elif not evidence_ok:
        state = "INSUFFICIENT_EVIDENCE"
    else:
        state = _state(price_regime=price_regime, activity_regime=activity_regime)

    codes.extend((f"price_regime_{price_regime.lower()}", f"activity_regime_{activity_regime.lower()}"))
    if progress is not None:
        codes.append("scarcity_rebuild_progress_measured")
    if reissue_exposure is not None:
        codes.append("cumulative_reissue_exposure_attached_diagnostic_only")
    codes.append("scarcity_rebuild_research_only_no_opportunity_score_adjustment")

    latest_post = post[-1] if post else None
    return ScarcityRebuildSnapshot(
        item_id=item_id,
        event_key=event.event_key,
        family=event.family,
        as_of=as_of,
        event_opens_at=event.opens_at,
        event_closes_at=event.closes_at,
        next_same_family_event_opens_at=next_open,
        state=state,
        price_regime=price_regime,
        activity_regime=activity_regime,
        pre_observed_day_count=len(pre),
        active_observed_day_count=len(active),
        post_observed_day_count=len(post),
        recent_observed_day_count=len(recent),
        pre_price_median=pre_price,
        active_trough_price=trough.vwap_price if trough is not None else None,
        recent_price_median=recent_price,
        latest_post_trade_price=latest_post.vwap_price if latest_post is not None else None,
        latest_post_trade_observed_at=latest_post.available_at if latest_post is not None else None,
        pre_activity_median=pre_activity,
        active_activity_median=active_activity,
        recent_activity_median=recent_activity,
        price_recovery_fraction=price_fraction,
        activity_normalization_fraction=activity_fraction,
        rebuild_progress_floor=progress,
        days_since_event_close=_days_since(event.closes_at, as_of),
        completed_tradeable_reissue_count=(
            reissue_exposure.completed_tradeable_supply_count
            if reissue_exposure is not None
            else None
        ),
        completed_account_only_return_count=(
            reissue_exposure.completed_account_only_return_count
            if reissue_exposure is not None
            else None
        ),
        distinct_reissue_family_count=(
            reissue_exposure.distinct_family_count if reissue_exposure is not None else None
        ),
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def evaluate_scarcity_rebuild_forward_outcome(
    *,
    snapshot: ScarcityRebuildSnapshot,
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    evaluation_as_of: datetime,
    forward_horizon_days: int = 60,
    next_same_family_event_opens_at: datetime | None = None,
    fee_rate: Decimal = Decimal("0.15"),
    currency_quantum: Decimal = Decimal("0.01"),
) -> ScarcityRebuildForwardOutcome:
    """Evaluate a future trade-VWAP proxy without mutating the snapshot.

    This is deliberately separated from ``measure_scarcity_rebuild`` so future
    outcomes cannot leak into the point-in-time state. The result is a research
    label only; historical VWAP is not an executable quote or fill.
    """

    if isinstance(forward_horizon_days, bool) or not isinstance(forward_horizon_days, int):
        raise ScarcityRebuildError("forward_horizon_days must be an integer.")
    if forward_horizon_days <= 0:
        raise ScarcityRebuildError("forward_horizon_days must be positive.")
    evaluation_as_of = _utc(evaluation_as_of, "evaluation_as_of")
    next_open = (
        _utc(next_same_family_event_opens_at, "next_same_family_event_opens_at")
        if next_same_family_event_opens_at is not None
        else snapshot.next_same_family_event_opens_at
    )
    if not isinstance(fee_rate, Decimal) or not fee_rate.is_finite() or not Decimal("0") <= fee_rate < Decimal("1"):
        raise ScarcityRebuildError("fee_rate must be a finite Decimal in [0, 1).")
    if not isinstance(currency_quantum, Decimal) or not currency_quantum.is_finite() or currency_quantum <= Decimal("0"):
        raise ScarcityRebuildError("currency_quantum must be a positive finite Decimal.")

    target_at = snapshot.as_of + timedelta(days=forward_horizon_days)
    cutoff = min(evaluation_as_of, next_open) if next_open is not None else evaluation_as_of
    exit_point: DailyHistoricalTradeEvidence | None = None
    if target_at < cutoff:
        evidence = select_historical_trade_evidence(
            tuple(historical_buckets),
            as_of=cutoff,
            window_start=snapshot.as_of,
        )
        daily = aggregate_trade_evidence_by_utc_day(evidence.buckets)
        exit_point = next(
            (
                point
                for point in daily
                if point.day_start_utc >= target_at and point.available_at <= cutoff
            ),
            None,
        )

    entry_price = snapshot.latest_post_trade_price
    exit_price = exit_point.vwap_price if exit_point is not None else None
    proceeds = (
        _sale_proceeds(exit_price, fee_rate, currency_quantum)
        if exit_price is not None
        else None
    )
    fee_amount = exit_price - proceeds if exit_price is not None and proceeds is not None else None
    profit = proceeds - entry_price if proceeds is not None and entry_price is not None else None
    roi = profit / entry_price if profit is not None and entry_price is not None else None
    codes: list[str] = []
    if entry_price is None:
        codes.append("scarcity_rebuild_entry_proxy_unavailable")
    if exit_point is None:
        codes.append("scarcity_rebuild_forward_outcome_unavailable")
    else:
        codes.append("scarcity_rebuild_forward_outcome_available")
        codes.append("historical_trade_vwap_proxy_not_execution_or_fill")
    return ScarcityRebuildForwardOutcome(
        item_id=snapshot.item_id,
        event_key=snapshot.event_key,
        snapshot_as_of=snapshot.as_of,
        forward_horizon_days=forward_horizon_days,
        target_at=target_at,
        evaluation_as_of=evaluation_as_of,
        observed_at=exit_point.available_at if exit_point is not None else None,
        entry_proxy_price=entry_price,
        exit_proxy_price=exit_price,
        sale_proceeds=proceeds,
        fee_amount=fee_amount,
        net_profit=profit,
        net_roi=roi,
        outcome_available=(entry_price is not None and exit_price is not None),
        explanation_codes=tuple(codes),
    )


def _price_axis(
    *,
    pre_price: Decimal | None,
    trough_price: Decimal | None,
    recent_price: Decimal | None,
) -> tuple[PriceRebuildRegime, Decimal | None]:
    if pre_price is None or trough_price is None or recent_price is None:
        return "UNAVAILABLE", None
    if pre_price <= trough_price:
        return "NO_CLEAR_PRICE_SHOCK", None
    fraction = (recent_price - trough_price) / (pre_price - trough_price)
    if recent_price <= trough_price:
        return "AT_OR_BELOW_TROUGH", fraction
    if recent_price < pre_price:
        return "PARTIAL_RECOVERY", fraction
    return "AT_OR_ABOVE_PRE_EVENT", fraction


def _activity_axis(
    *,
    pre_activity: Decimal | None,
    active_activity: Decimal | None,
    recent_activity: Decimal | None,
) -> tuple[ActivityAbsorptionRegime, Decimal | None]:
    if pre_activity is None or active_activity is None or recent_activity is None:
        return "UNAVAILABLE", None
    if active_activity <= pre_activity:
        return "NO_CLEAR_ACTIVITY_SPIKE", None
    fraction = (active_activity - recent_activity) / (active_activity - pre_activity)
    if recent_activity <= pre_activity:
        return "NORMALIZED_TO_PRE_EVENT", fraction
    if recent_activity < active_activity:
        return "ELEVATED_ABOVE_PRE_EVENT", fraction
    return "AT_OR_ABOVE_ACTIVE_EVENT", fraction


def _state(
    *,
    price_regime: PriceRebuildRegime,
    activity_regime: ActivityAbsorptionRegime,
) -> ScarcityRebuildState:
    if price_regime == "NO_CLEAR_PRICE_SHOCK" or activity_regime == "NO_CLEAR_ACTIVITY_SPIKE":
        return "NO_CLEAR_TWO_AXIS_SUPPLY_SHOCK"
    if price_regime == "AT_OR_BELOW_TROUGH":
        return (
            "POST_EVENT_WEAK_DEMAND"
            if activity_regime == "NORMALIZED_TO_PRE_EVENT"
            else "ONGOING_ABSORPTION"
        )
    if price_regime == "PARTIAL_RECOVERY":
        return (
            "SCARCITY_REBUILDING"
            if activity_regime == "NORMALIZED_TO_PRE_EVENT"
            else "REBUILDING_WITH_ELEVATED_ACTIVITY"
        )
    if price_regime == "AT_OR_ABOVE_PRE_EVENT":
        return (
            "SCARCITY_REBUILT"
            if activity_regime == "NORMALIZED_TO_PRE_EVENT"
            else "DEMAND_DOMINANT_RECOVERY"
        )
    return "INSUFFICIENT_EVIDENCE"


def _empty_snapshot(
    *,
    item_id: int,
    event: SupplyPoolEvent,
    as_of: datetime,
    next_open: datetime | None,
    reissue_exposure: ReissueExposureFeatures | None,
    state: ScarcityRebuildState,
    codes: tuple[str, ...],
) -> ScarcityRebuildSnapshot:
    return ScarcityRebuildSnapshot(
        item_id=item_id,
        event_key=event.event_key,
        family=event.family,
        as_of=as_of,
        event_opens_at=event.opens_at,
        event_closes_at=event.closes_at,
        next_same_family_event_opens_at=next_open,
        state=state,
        price_regime="UNAVAILABLE",
        activity_regime="UNAVAILABLE",
        pre_observed_day_count=0,
        active_observed_day_count=0,
        post_observed_day_count=0,
        recent_observed_day_count=0,
        pre_price_median=None,
        active_trough_price=None,
        recent_price_median=None,
        latest_post_trade_price=None,
        latest_post_trade_observed_at=None,
        pre_activity_median=None,
        active_activity_median=None,
        recent_activity_median=None,
        price_recovery_fraction=None,
        activity_normalization_fraction=None,
        rebuild_progress_floor=None,
        days_since_event_close=(
            _days_since(event.closes_at, as_of) if as_of >= event.closes_at else None
        ),
        completed_tradeable_reissue_count=(
            reissue_exposure.completed_tradeable_supply_count
            if reissue_exposure is not None
            else None
        ),
        completed_account_only_return_count=(
            reissue_exposure.completed_account_only_return_count
            if reissue_exposure is not None
            else None
        ),
        distinct_reissue_family_count=(
            reissue_exposure.distinct_family_count if reissue_exposure is not None else None
        ),
        explanation_codes=codes,
    )


def _first_min(points: tuple[DailyHistoricalTradeEvidence, ...]) -> DailyHistoricalTradeEvidence | None:
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


def _day_end(point: DailyHistoricalTradeEvidence) -> datetime:
    return point.day_start_utc + timedelta(days=1)


def _days_since(left: datetime, right: datetime) -> Decimal:
    return Decimal(str((right - left).total_seconds())) / Decimal("86400")


def _clamp01(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("1"), value))


def _sale_proceeds(price: Decimal, fee_rate: Decimal, quantum: Decimal) -> Decimal:
    gross = price * (Decimal("1") - fee_rate)
    units = (gross / quantum).to_integral_value(rounding=ROUND_DOWN)
    return units * quantum


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScarcityRebuildError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
