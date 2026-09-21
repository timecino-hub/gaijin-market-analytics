"""The shippable post-event entry rule, and the guidance that goes with it.

This is the one thing the cohort supports well enough to put in front of a
user: after a supply event closes, a vehicle whose price has fallen far below
its own pre-event baseline recovers better than one that has not, and the
position is worth holding for months rather than weeks.

What the evidence says, stated at the size it deserves:

- Descriptively, on 88 completed events, filtering at 0.75 x baseline lifts the
  share of profitable 180-day outcomes from 67.0% to 82.6%.
- Under strict point-in-time stratified selection — asking whether the evidence
  available at the time would have told you to use the filter — the lift is
  only 67.0% to 69.6%, and the rule qualifies on a minority of decisions. A
  180-day label closes too slowly to accumulate evidence quickly.
- The threshold is a plateau, not a cliff. Every value between 0.55 and 1.00
  orders the outcomes monotonically, which is the shape of a real effect rather
  than of a fitted one.

So the rule is published as **guidance carrying its own uncertainty**, and the
default policy changes nothing: ``IGNORE`` leaves scoring exactly as it was.
``ANNOTATE`` attaches the explanation codes without gating, which is how the
site can show the signal while the evidence keeps accumulating. Only
``WITHHOLD_SHALLOW_DRAWDOWN`` actually refuses to publish, and nothing should
switch to it without re-running the walk-forward on a larger cohort.

Nothing here is a fill claim. Prices are daily trade-VWAP proxies, and the exit
guidance is a price to rest an offer at, not a price anyone is promised.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Literal

from .historical_trades import (
    HistoricalTradeBucketEvidence,
    aggregate_trade_evidence_by_utc_day,
    select_historical_trade_evidence,
)
from .supply_cycles import SupplyPoolEvent


POSTEVENT_ENTRY_RULE_VERSION = "1.0.0"

PostEventEntryState = Literal[
    "NO_COMPLETED_EVENT",
    "ENTRY_WINDOW_CLOSED",
    "NO_PRE_EVENT_BASELINE",
    "NO_POST_EVENT_PRICE",
    "SUPERSEDED_BY_NEXT_SUPPLY_EVENT",
    "SHALLOW_DRAWDOWN",
    "ENTRY_CANDIDATE",
]
PostEventEntryPolicy = Literal["IGNORE", "ANNOTATE", "WITHHOLD_SHALLOW_DRAWDOWN"]
POSTEVENT_ENTRY_POLICIES: tuple[PostEventEntryPolicy, ...] = (
    "IGNORE",
    "ANNOTATE",
    "WITHHOLD_SHALLOW_DRAWDOWN",
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


class PostEventEntryError(ValueError):
    """Raised when a post-event entry input is not usable."""


@dataclass(frozen=True, slots=True)
class PostEventEntryConfig:
    # 0.75 sits in the middle of the plateau. Lower is more selective: 0.65
    # keeps a third of events at a higher rate, 0.85 keeps two thirds at a
    # lower one. Nothing in the data distinguishes a single correct value.
    drawdown_threshold: Decimal = Decimal("0.75")
    entry_window_days: int = 7
    holding_days: int = 180
    target_net_roi: Decimal = Decimal("0.25")
    minimum_pre_event_days: int = 3
    fee_rate: Decimal = Decimal("0.15")

    def __post_init__(self) -> None:
        for name in ("entry_window_days", "holding_days", "minimum_pre_event_days"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise PostEventEntryError(f"{name} must be a positive integer.")
        if (
            not isinstance(self.drawdown_threshold, Decimal)
            or not self.drawdown_threshold.is_finite()
            or not _ZERO < self.drawdown_threshold <= _ONE
        ):
            raise PostEventEntryError(
                "drawdown_threshold must be a finite Decimal in (0, 1]."
            )
        if (
            not isinstance(self.target_net_roi, Decimal)
            or not self.target_net_roi.is_finite()
            or self.target_net_roi <= _ZERO
        ):
            raise PostEventEntryError("target_net_roi must be a positive Decimal.")
        if (
            not isinstance(self.fee_rate, Decimal)
            or not self.fee_rate.is_finite()
            or not _ZERO <= self.fee_rate < _ONE
        ):
            raise PostEventEntryError("fee_rate must be a finite Decimal in [0, 1).")


@dataclass(frozen=True, slots=True)
class PostEventEntrySignal:
    """Whether a vehicle is a fresh post-event entry, and what to do with it."""

    item_id: int
    as_of: datetime
    state: PostEventEntryState
    is_entry_candidate: bool
    event_key: str | None
    event_closed_at: datetime | None
    days_since_close: int | None
    pre_event_baseline: Decimal | None
    latest_price: Decimal | None
    drawdown_ratio: Decimal | None
    threshold: Decimal
    # Guidance, not a promise: the offer price that clears the target after the
    # fee, and the date the remaining half would be sold at market.
    take_profit_price: Decimal | None
    take_profit_net_roi: Decimal
    hold_until: datetime | None
    explanation_codes: tuple[str, ...]
    research_only: bool = True
    fill_claim: bool = False
    version: str = POSTEVENT_ENTRY_RULE_VERSION


def evaluate_postevent_entry(
    *,
    item_id: int,
    as_of: datetime,
    events: Iterable[SupplyPoolEvent],
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    config: PostEventEntryConfig | None = None,
) -> PostEventEntrySignal:
    """Decide whether ``item_id`` is inside a fresh post-event entry window.

    Everything is read from days completed at ``as_of``: the pre-event baseline
    from before the event opened, the current price from the latest completed
    day. Nothing about the future is consulted, which is what makes this
    runnable live rather than only in a replay.
    """

    cfg = config or PostEventEntryConfig()
    if item_id <= 0:
        raise PostEventEntryError("item_id must be positive.")
    moment = _utc(as_of, "as_of")
    buckets = tuple(historical_buckets)
    ordered = sorted(events, key=lambda value: value.opens_at)
    codes: list[str] = [
        "postevent_entry_rule_research_only_not_execution_or_fill",
    ]

    def empty(state: PostEventEntryState, code: str, **extra) -> PostEventEntrySignal:
        base = dict(
            item_id=item_id,
            as_of=moment,
            state=state,
            is_entry_candidate=False,
            event_key=None,
            event_closed_at=None,
            days_since_close=None,
            pre_event_baseline=None,
            latest_price=None,
            drawdown_ratio=None,
            threshold=cfg.drawdown_threshold,
            take_profit_price=None,
            take_profit_net_roi=cfg.target_net_roi,
            hold_until=None,
            explanation_codes=tuple(dict.fromkeys([*codes, code])),
        )
        base.update(extra)
        return PostEventEntrySignal(**base)

    completed = [value for value in ordered if value.closes_at <= moment]
    if not completed:
        return empty(
            "NO_COMPLETED_EVENT", "postevent_entry_no_completed_supply_event"
        )
    event = completed[-1]
    days_since = (moment - event.closes_at).days
    if days_since > cfg.entry_window_days:
        return empty(
            "ENTRY_WINDOW_CLOSED",
            "postevent_entry_window_has_closed",
            event_key=event.event_key,
            event_closed_at=event.closes_at,
            days_since_close=days_since,
        )

    next_open = min(
        (value.opens_at for value in ordered if value.opens_at > event.opens_at),
        default=None,
    )
    if next_open is not None and next_open <= moment:
        return empty(
            "SUPERSEDED_BY_NEXT_SUPPLY_EVENT",
            "postevent_entry_superseded_by_a_newer_supply_event",
            event_key=event.event_key,
            event_closed_at=event.closes_at,
            days_since_close=days_since,
        )

    baseline = _pre_event_baseline(buckets, event=event, config=cfg)
    if baseline is None or baseline <= _ZERO:
        return empty(
            "NO_PRE_EVENT_BASELINE",
            "postevent_entry_no_pre_event_baseline",
            event_key=event.event_key,
            event_closed_at=event.closes_at,
            days_since_close=days_since,
        )

    post = _completed_days(buckets, window_start=event.closes_at, as_of=moment)
    if not post:
        return empty(
            "NO_POST_EVENT_PRICE",
            "postevent_entry_no_completed_day_since_the_event_closed",
            event_key=event.event_key,
            event_closed_at=event.closes_at,
            days_since_close=days_since,
            pre_event_baseline=baseline,
        )

    latest = post[-1].vwap_price
    ratio = latest / baseline
    passed = ratio <= cfg.drawdown_threshold
    codes.append(
        "postevent_entry_drawdown_gate_passed"
        if passed
        else "postevent_entry_drawdown_gate_failed"
    )
    codes.append("postevent_entry_evidence_is_descriptive_not_walk_forward_validated")
    if days_since > 1:
        codes.append("postevent_entry_past_the_first_post_close_day")
    return PostEventEntrySignal(
        item_id=item_id,
        as_of=moment,
        state="ENTRY_CANDIDATE" if passed else "SHALLOW_DRAWDOWN",
        is_entry_candidate=passed,
        event_key=event.event_key,
        event_closed_at=event.closes_at,
        days_since_close=days_since,
        pre_event_baseline=baseline,
        latest_price=latest,
        drawdown_ratio=ratio,
        threshold=cfg.drawdown_threshold,
        take_profit_price=_take_profit_price(latest, cfg) if passed else None,
        take_profit_net_roi=cfg.target_net_roi,
        hold_until=(
            post[-1].day_start_utc + timedelta(days=cfg.holding_days)
            if passed
            else None
        ),
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def resolve_postevent_entry_withholding(
    *,
    signal: PostEventEntrySignal | None,
    policy: PostEventEntryPolicy = "IGNORE",
) -> tuple[bool, tuple[str, ...]]:
    """Decide whether the policy withholds, and what it says about it.

    ``IGNORE`` is the shipped default and returns no codes at all, so a caller
    that has not opted in produces exactly what it produced before this module
    existed. ``ANNOTATE`` publishes the reasoning without gating on it, which
    is what the site should run while the walk-forward evidence is still thin.
    """

    if policy not in POSTEVENT_ENTRY_POLICIES:
        raise PostEventEntryError("unsupported post-event entry policy.")
    if policy == "IGNORE" or signal is None:
        return False, ()
    if policy == "ANNOTATE":
        return False, (*signal.explanation_codes, "postevent_entry_annotated_only")
    if signal.state == "SHALLOW_DRAWDOWN":
        return True, (
            *signal.explanation_codes,
            "strategy_withheld_for_shallow_post_event_drawdown",
        )
    return False, signal.explanation_codes


def _take_profit_price(entry_price: Decimal, config: PostEventEntryConfig) -> Decimal:
    """The offer that clears ``target_net_roi`` once the sale fee is paid.

    A net target is further away than it reads: at a 15% fee, netting 25% needs
    the price to rise about 47%.
    """

    return (entry_price * (_ONE + config.target_net_roi)) / (_ONE - config.fee_rate)


def _pre_event_baseline(
    buckets: Iterable[HistoricalTradeBucketEvidence],
    *,
    event: SupplyPoolEvent,
    config: PostEventEntryConfig,
) -> Decimal | None:
    duration = event.closes_at - event.opens_at
    days = _completed_days(
        buckets, window_start=event.opens_at - duration, as_of=event.opens_at
    )
    if len(days) < config.minimum_pre_event_days:
        return None
    return _median(tuple(point.vwap_price for point in days))


def _completed_days(
    buckets: Iterable[HistoricalTradeBucketEvidence],
    *,
    window_start: datetime,
    as_of: datetime,
):
    if as_of <= window_start:
        return ()
    evidence = select_historical_trade_evidence(
        tuple(buckets), as_of=as_of, window_start=window_start
    )
    return tuple(
        point
        for point in aggregate_trade_evidence_by_utc_day(evidence.buckets)
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
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PostEventEntryError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
