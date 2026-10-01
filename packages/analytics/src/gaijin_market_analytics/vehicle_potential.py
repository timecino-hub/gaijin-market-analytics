"""Stage 24 — what one vehicle's own history says about it.

This answers a different question from the post-event entry rule. That rule is
one general strategy applied to every vehicle alike; this is the character of a
single vehicle, read only from its own series.

It has two halves, and they are deliberately not equal in weight.

**Where the price sits now**, against this vehicle's own past. Each vehicle
carries hundreds to thousands of completed trading days, so a percentile over
that window is a real measurement and is what the ranking uses.

**What happened at its own past events**, which is evidence a reader should
see and which is far too thin to rank on: a vehicle in this cohort has between
one and five completed events. Every aggregate here therefore carries the count
it was computed from, and nothing in this module orders vehicles by it.

A low percentile is not a forecast. A vehicle can sit at the bottom of its own
range because it is cheap or because it is still falling, and nothing in this
series distinguishes those. The module reports the position and says so.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal, localcontext
from typing import Literal

from .general_strategy import take_profit_offer
from .historical_trades import (
    HistoricalTradeBucketEvidence,
    aggregate_trade_evidence_by_utc_day,
    select_historical_trade_evidence,
)
from .market_rules import GAIJIN_MARKET_RULES_V1
from .supply_cycles import SupplyPoolEvent

VEHICLE_POTENTIAL_VERSION = "1.1.0"

_ZERO = Decimal(0)
_ONE = Decimal(1)

VehiclePotentialState = Literal[
    "RANKED",
    "INSUFFICIENT_HISTORY",
    "NO_CURRENT_PRICE",
]
VehicleOutcomeStatus = Literal[
    "OBSERVED", "PENDING_HORIZON", "MISSING_EXIT_PRICE", "NO_ENTRY_PRICE"
]


class VehiclePotentialError(ValueError):
    """Raised when a vehicle potential reading cannot be produced."""


@dataclass(frozen=True, slots=True)
class VehiclePotentialConfig:
    # Two years of daily history is long enough to place a price against
    # several supply cycles and short enough that a vehicle's oldest, thinnest
    # trading is not treated as equal evidence to last month's.
    history_days: int = 730
    # Below this there is no distribution worth taking a percentile of, and the
    # module refuses rather than publishing a percentile of a dozen days.
    minimum_history_days: int = 120
    # A price older than this is stale; missing observations do not establish
    # whether trading stopped.
    maximum_price_age_days: int = 14
    recent_volume_days: int = 30
    holding_days: int = 180
    fee_rate: Decimal = Decimal("0.15")
    minimum_pre_event_days: int = 3

    def __post_init__(self) -> None:
        for name in (
            "history_days",
            "minimum_history_days",
            "maximum_price_age_days",
            "recent_volume_days",
            "holding_days",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise VehiclePotentialError(f"{name} must be a positive integer.")
        if self.minimum_history_days > self.history_days:
            raise VehiclePotentialError(
                "minimum_history_days cannot exceed history_days."
            )
        if (
            not isinstance(self.fee_rate, Decimal)
            or not self.fee_rate.is_finite()
            or not _ZERO <= self.fee_rate < _ONE
        ):
            raise VehiclePotentialError("fee_rate must be a finite Decimal in [0, 1).")


@dataclass(frozen=True, slots=True)
class VehicleEventRecord:
    """One completed supply event this vehicle went through.

    The entry is the first recorded complete day after close, exposed with its
    actual date. This legacy fixed-hold comparison requires the exact complete
    day 180 observation; an unfinished horizon or missing endpoint has no
    return. These are observed prices, never actual purchases or liquidations.
    """

    event_key: str
    closes_at: datetime
    pre_event_baseline: Decimal | None
    entry_price: Decimal | None
    entry_price_day: datetime | None
    drawdown_ratio: Decimal | None
    holding_days: int
    exit_price: Decimal | None
    net_roi: Decimal | None
    outcome_closed: bool
    outcome_status: VehicleOutcomeStatus


@dataclass(frozen=True, slots=True)
class VehicleTargetReference:
    """Required offers using an observed average as a cost reference only."""

    cost_basis: Literal["LATEST_COMPLETED_DAY_AVERAGE_PROXY"]
    reference_cost: Decimal
    fee_rate: Decimal
    preferred_offer: Decimal
    minimum_offer: Decimal
    median_net_roi: Decimal
    median_supports_preferred: bool
    median_supports_minimum: bool
    preferred_within_market_cap: bool
    minimum_within_market_cap: bool


@dataclass(frozen=True, slots=True)
class VehiclePotential:
    item_id: int
    as_of: datetime
    state: VehiclePotentialState

    # --- where the price sits now: the ranking basis ---------------------
    current_price: Decimal | None
    current_price_day: datetime | None
    price_age_days: int | None
    history_day_count: int
    window_start: datetime | None
    window_end: datetime | None
    # Fraction of this vehicle's own completed days trading at or below the
    # current price. 0.05 means it has rarely been this cheap.
    price_percentile: Decimal | None
    median_price: Decimal | None
    low_price: Decimal | None
    high_price: Decimal | None
    ratio_to_median: Decimal | None
    median_daily_volume: int | None
    recent_daily_volume: int | None
    recent_trading_day_count: int | None
    target_reference: VehicleTargetReference | None

    # --- what its own events did: evidence, never the ranking ------------
    events: tuple[VehicleEventRecord, ...]
    completed_event_count: int
    closed_outcome_count: int
    median_drawdown_ratio: Decimal | None
    median_net_roi: Decimal | None
    positive_outcome_count: int | None
    days_since_last_event_close: int | None

    explanation_codes: tuple[str, ...]
    research_only: bool = True
    version: str = VEHICLE_POTENTIAL_VERSION


def evaluate_vehicle_potential(
    *,
    item_id: int,
    as_of: datetime,
    events: Iterable[SupplyPoolEvent] = (),
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    config: VehiclePotentialConfig | None = None,
) -> VehiclePotential:
    """Read one vehicle's own series and place its price inside it.

    Everything comes from days completed at ``as_of``. A vehicle with no
    catalogued events still gets a full price reading — the two halves are
    independent, and an unbound vehicle is not a vehicle without a price.
    """

    cfg = config or VehiclePotentialConfig()
    if item_id <= 0:
        raise VehiclePotentialError("item_id must be positive.")
    moment = _utc(as_of, "as_of")

    # One aggregation of the whole series, then every window is a slice of it.
    # The price window and each event's two windows would otherwise each
    # re-aggregate a decade of buckets.
    series = _daily_series(tuple(historical_buckets), as_of=moment)
    window_start = moment - timedelta(days=cfg.history_days)
    days = _window(series, window_start=window_start, as_of=moment)
    codes: list[str] = ["vehicle_potential_descriptive_not_a_forecast"]

    event_side = _event_record(series, events=events, as_of=moment, config=cfg)

    def refused(state: VehiclePotentialState, code: str, **extra) -> VehiclePotential:
        base = dict(
            item_id=item_id,
            as_of=moment,
            state=state,
            current_price=None,
            current_price_day=None,
            price_age_days=None,
            history_day_count=len(days),
            window_start=days[0].day_start_utc if days else None,
            window_end=days[-1].day_start_utc if days else None,
            price_percentile=None,
            median_price=None,
            low_price=None,
            high_price=None,
            ratio_to_median=None,
            median_daily_volume=None,
            recent_daily_volume=None,
            recent_trading_day_count=None,
            target_reference=None,
            explanation_codes=tuple(dict.fromkeys([*codes, code])),
            **event_side,
        )
        base.update(extra)
        return VehiclePotential(**base)

    if len(days) < cfg.minimum_history_days:
        return refused(
            "INSUFFICIENT_HISTORY", "vehicle_potential_not_enough_trading_history"
        )

    latest = days[-1]
    age = (moment - (latest.day_start_utc + timedelta(days=1))).days
    if age > cfg.maximum_price_age_days:
        return refused(
            "NO_CURRENT_PRICE",
            "vehicle_potential_latest_price_is_stale",
            price_age_days=age,
        )

    current = latest.vwap_price
    prices = tuple(point.vwap_price for point in days)
    at_or_below = sum(1 for price in prices if price <= current)
    percentile = Decimal(at_or_below) / Decimal(len(prices))
    median = _median(prices)
    assert median is not None

    volumes = tuple(point.reported_trade_volume for point in days)
    recent = tuple(
        point.reported_trade_volume
        for point in days
        if point.day_start_utc >= moment - timedelta(days=cfg.recent_volume_days)
    )

    if percentile <= Decimal("0.25"):
        codes.append("vehicle_potential_near_the_bottom_of_its_own_range")
    elif percentile >= Decimal("0.75"):
        codes.append("vehicle_potential_near_the_top_of_its_own_range")
    if event_side["completed_event_count"] == 0:
        codes.append("vehicle_potential_no_completed_event_record")
    elif event_side["closed_outcome_count"] < 3:
        codes.append("vehicle_potential_event_record_too_small_to_generalise")

    return VehiclePotential(
        item_id=item_id,
        as_of=moment,
        state="RANKED",
        current_price=current,
        current_price_day=latest.day_start_utc,
        price_age_days=age,
        history_day_count=len(days),
        window_start=days[0].day_start_utc,
        window_end=latest.day_start_utc,
        price_percentile=percentile,
        median_price=median,
        low_price=min(prices),
        high_price=max(prices),
        ratio_to_median=(current / median if median and median > _ZERO else None),
        median_daily_volume=_median_int(volumes),
        recent_daily_volume=_median_int(recent) if recent else None,
        recent_trading_day_count=len(recent),
        target_reference=_target_reference(current, median, fee_rate=cfg.fee_rate),
        explanation_codes=tuple(dict.fromkeys(codes)),
        **event_side,
    )


def _target_reference(
    cost: Decimal, median: Decimal, *, fee_rate: Decimal
) -> VehicleTargetReference:
    preferred = take_profit_offer(cost, Decimal("0.50"), fee_rate)
    minimum = take_profit_offer(cost, Decimal("0.40"), fee_rate)
    with localcontext() as context:
        context.prec = 64
        receipt = (median * (_ONE - fee_rate)).quantize(
            GAIJIN_MARKET_RULES_V1.currency_quantum, rounding=ROUND_FLOOR
        )
        median_net_roi = receipt / cost - _ONE
    return VehicleTargetReference(
        cost_basis="LATEST_COMPLETED_DAY_AVERAGE_PROXY",
        reference_cost=cost,
        fee_rate=fee_rate,
        preferred_offer=preferred,
        minimum_offer=minimum,
        median_net_roi=median_net_roi,
        median_supports_preferred=preferred <= median,
        median_supports_minimum=minimum <= median,
        preferred_within_market_cap=(
            preferred <= GAIJIN_MARKET_RULES_V1.maximum_listing_price
        ),
        minimum_within_market_cap=(
            minimum <= GAIJIN_MARKET_RULES_V1.maximum_listing_price
        ),
    )


def rank_vehicle_potential(
    readings: Iterable[VehiclePotential],
) -> tuple[VehiclePotential, ...]:
    """Cheapest against its own history first; unreadable vehicles last.

    The order comes only from the price position. The event record is evidence
    a reader weighs, not a score — with one to five events per vehicle, ordering
    by it would be ordering by sampling noise.
    """

    def key(value: VehiclePotential) -> tuple[int, Decimal, int]:
        if value.state != "RANKED" or value.price_percentile is None:
            return (1, _ZERO, value.item_id)
        return (0, value.price_percentile, value.item_id)

    return tuple(sorted(readings, key=key))


def _event_record(
    series,
    *,
    events: Iterable[SupplyPoolEvent],
    as_of: datetime,
    config: VehiclePotentialConfig,
) -> dict:
    completed = sorted(
        (value for value in events if value.closes_at <= as_of),
        key=lambda value: value.closes_at,
    )
    records: list[VehicleEventRecord] = []
    for event in completed:
        baseline = _pre_event_baseline(series, event=event, config=config)
        post = _window(series, window_start=event.closes_at, as_of=as_of)
        entry = post[0].vwap_price if post else None
        entry_day = post[0].day_start_utc if post else None

        exit_price: Decimal | None = None
        closed = False
        outcome_status: VehicleOutcomeStatus = "NO_ENTRY_PRICE"
        if entry_day is not None:
            target = entry_day + timedelta(days=config.holding_days)
            outcome_status = "PENDING_HORIZON"
            if target + timedelta(days=1) <= as_of:
                endpoint = next(
                    (point for point in post if point.day_start_utc == target),
                    None,
                )
                outcome_status = "MISSING_EXIT_PRICE"
                if endpoint is not None:
                    exit_price = endpoint.vwap_price
                    closed = True
                    outcome_status = "OBSERVED"

        net_roi: Decimal | None = None
        if exit_price is not None and entry is not None and entry > _ZERO:
            net_roi = (exit_price * (_ONE - config.fee_rate)) / entry - _ONE

        records.append(
            VehicleEventRecord(
                event_key=event.event_key,
                closes_at=event.closes_at,
                pre_event_baseline=baseline,
                entry_price=entry,
                entry_price_day=entry_day,
                drawdown_ratio=(
                    entry / baseline
                    if entry is not None and baseline and baseline > _ZERO
                    else None
                ),
                holding_days=config.holding_days,
                exit_price=exit_price,
                net_roi=net_roi,
                outcome_closed=closed,
                outcome_status=outcome_status,
            )
        )

    drawdowns = tuple(
        value.drawdown_ratio for value in records if value.drawdown_ratio is not None
    )
    returns = tuple(value.net_roi for value in records if value.net_roi is not None)
    return {
        "events": tuple(records),
        "completed_event_count": len(records),
        "closed_outcome_count": len(returns),
        "median_drawdown_ratio": _median(drawdowns) if drawdowns else None,
        "median_net_roi": _median(returns) if returns else None,
        "positive_outcome_count": (
            sum(1 for value in returns if value > _ZERO) if returns else None
        ),
        "days_since_last_event_close": (
            (as_of - completed[-1].closes_at).days if completed else None
        ),
    }


def _pre_event_baseline(
    series,
    *,
    event: SupplyPoolEvent,
    config: VehiclePotentialConfig,
) -> Decimal | None:
    duration = event.closes_at - event.opens_at
    days = _window(series, window_start=event.opens_at - duration, as_of=event.opens_at)
    if len(days) < config.minimum_pre_event_days:
        return None
    return _median(tuple(point.vwap_price for point in days))


def _daily_series(
    buckets: tuple[HistoricalTradeBucketEvidence, ...], *, as_of: datetime
):
    """Every completed UTC day this vehicle traded on, aggregated once.

    Selecting over the widest window is what each narrower call would have done
    for the days it kept: the 1h-over-1d precedence is decided per day, and a
    day a narrower window would have cut in half is dropped by the
    ``>= window_start`` test in ``_window`` either way.
    """

    if not buckets:
        return ()
    window_start = min(value.bucket_start_utc for value in buckets)
    if as_of <= window_start:
        return ()
    evidence = select_historical_trade_evidence(
        buckets, as_of=as_of, window_start=window_start
    )
    return tuple(
        point
        for point in aggregate_trade_evidence_by_utc_day(evidence.buckets)
        if point.day_start_utc + timedelta(days=1) <= as_of
    )


def _window(series, *, window_start: datetime, as_of: datetime):
    """The completed days of ``series`` inside one window."""

    if as_of <= window_start:
        return ()
    return tuple(
        point
        for point in series
        if point.day_start_utc >= window_start
        and point.day_start_utc + timedelta(days=1) <= as_of
    )


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _median_int(values: tuple[int, ...]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise VehiclePotentialError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
