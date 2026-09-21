"""Independent entry timing for the post re-release price path.

Everything upstream of this module answers *whether* to buy: it fixes the entry
at the first completed UTC day after a supply event closes and then predicts
what that position recovers to. This module answers the separate question of
*when* to buy, and it is deliberately not a filter layered on the existing
score — a rule here can enter on an event the opportunity stack refuses, and
refuse one it likes.

The comparison is held honest by three rules.

**Abstention is an outcome, not a gap.** A rule that waits for a drawdown which
never arrives has bought nothing. Counting only the events where such a rule
fired would report the returns of its luckiest subset, so ``NOT_TRIGGERED`` is
a first-class status and every summary carries the trigger rate beside the
return.

**Rules are compared on the events they share.** A rule firing on a third of
the events cannot be put beside a benchmark that fires on all of them. Each
rule therefore also reports a matched comparison against the immediate entry
restricted to exactly the events where both produced an observed outcome.

**The trough is a bound, never a strategy.** ``TROUGH_ORACLE`` buys the actual
minimum of the window, which nobody can do, and it carries ``attainable=False``
so it can never be read as a result. Its only job is to say how much room
exists at all: when the oracle itself does not clear fees, no causal rule built
on the same path can.

Holding is measured from entry rather than to a fixed calendar instant. A rule
that waits ten days and is then scored to the same exit date would be charged
for a ten-day-shorter hold, which measures patience rather than entry quality.
Every rule here holds for the same number of days; only the window moves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Literal, Sequence

from .historical_trades import (
    DailyHistoricalTradeEvidence,
    HistoricalTradeBucketEvidence,
    aggregate_trade_evidence_by_utc_day,
    select_historical_trade_evidence,
)
from .post_rerelease import PRICE_PROXY_SEMANTICS, sale_proceeds
from .supply_cycles import SupplyPoolEvent


RERELEASE_BOTTOM_FISHING_VERSION = "1.0.0"

EntryRule = Literal[
    "IMMEDIATE",
    "DRAWDOWN_TRIGGER",
    "STABILISED",
    "PATIENT_FLOOR",
    "TROUGH_ORACLE",
]
ENTRY_RULES: tuple[EntryRule, ...] = (
    "IMMEDIATE",
    "DRAWDOWN_TRIGGER",
    "STABILISED",
    "PATIENT_FLOOR",
    "TROUGH_ORACLE",
)
CAUSAL_ENTRY_RULES: tuple[EntryRule, ...] = (
    "IMMEDIATE",
    "DRAWDOWN_TRIGGER",
    "STABILISED",
    "PATIENT_FLOOR",
)
BENCHMARK_ENTRY_RULE: EntryRule = "IMMEDIATE"

EntryStatus = Literal[
    "EVENT_NOT_COMPLETED",
    "NO_POST_EVENT_EVIDENCE",
    "NO_PRE_EVENT_BASELINE",
    "NOT_TRIGGERED",
    "ENTERED",
]
OutcomeStatus = Literal[
    "NOT_ENTERED",
    "OUTCOME_NOT_OBSERVABLE",
    "SUPERSEDED_BY_NEXT_SUPPLY_EVENT",
    "OBSERVED",
]


class BottomFishingError(ValueError):
    """Raised when a bottom fishing input is not usable."""


@dataclass(frozen=True, slots=True)
class BottomFishingConfig:
    maximum_wait_days: int = 30
    drawdown_trigger_ratio: Decimal = Decimal("0.75")
    stabilised_days: int = 3
    holding_days: int = 30
    minimum_pre_event_days: int = 3
    fee_rate: Decimal = Decimal("0.15")
    currency_quantum: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        for name in (
            "maximum_wait_days",
            "stabilised_days",
            "holding_days",
            "minimum_pre_event_days",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise BottomFishingError(f"{name} must be a positive integer.")
        if self.stabilised_days > self.maximum_wait_days:
            raise BottomFishingError(
                "stabilised_days cannot exceed maximum_wait_days; the rule "
                "could never fire."
            )
        if (
            not isinstance(self.drawdown_trigger_ratio, Decimal)
            or not self.drawdown_trigger_ratio.is_finite()
            or not Decimal("0") < self.drawdown_trigger_ratio <= Decimal("1")
        ):
            raise BottomFishingError(
                "drawdown_trigger_ratio must be a finite Decimal in (0, 1]."
            )
        if (
            not isinstance(self.fee_rate, Decimal)
            or not self.fee_rate.is_finite()
            or not Decimal("0") <= self.fee_rate < Decimal("1")
        ):
            raise BottomFishingError("fee_rate must be a finite Decimal in [0, 1).")
        if (
            not isinstance(self.currency_quantum, Decimal)
            or not self.currency_quantum.is_finite()
            or self.currency_quantum <= Decimal("0")
        ):
            raise BottomFishingError("currency_quantum must be a positive Decimal.")


@dataclass(frozen=True, slots=True)
class BottomFishingEntry:
    """Where one rule would have entered, decided only from completed days."""

    rule: EntryRule
    attainable: bool
    item_id: int
    event_key: str
    family: str
    as_of: datetime
    status: EntryStatus
    entered: bool
    entry_day_start_utc: datetime | None
    entry_available_at: datetime | None
    entry_price: Decimal | None
    days_waited: int | None
    candidate_day_count: int
    pre_event_median: Decimal | None
    immediate_price: Decimal | None
    window_low: Decimal | None
    explanation_codes: tuple[str, ...]
    research_only: bool = True
    fill_claim: bool = False
    price_proxy_semantics: str = PRICE_PROXY_SEMANTICS
    version: str = RERELEASE_BOTTOM_FISHING_VERSION

    @property
    def entry_discount(self) -> Decimal | None:
        """How far below the immediate entry this rule bought, as a share."""

        if self.entry_price is None or self.immediate_price is None:
            return None
        if self.immediate_price <= Decimal("0"):
            return None
        return (self.immediate_price - self.entry_price) / self.immediate_price


@dataclass(frozen=True, slots=True)
class BottomFishingOutcome:
    rule: EntryRule
    attainable: bool
    item_id: int
    event_key: str
    status: OutcomeStatus
    entry_price: Decimal | None
    exit_day_start_utc: datetime | None
    exit_price: Decimal | None
    sale_proceeds: Decimal | None
    net_profit: Decimal | None
    net_roi: Decimal | None
    profitable: bool | None
    holding_days: int
    explanation_codes: tuple[str, ...]
    research_only: bool = True
    fill_claim: bool = False
    price_proxy_semantics: str = PRICE_PROXY_SEMANTICS
    version: str = RERELEASE_BOTTOM_FISHING_VERSION


@dataclass(frozen=True, slots=True)
class BottomFishingTrial:
    entry: BottomFishingEntry
    outcome: BottomFishingOutcome


@dataclass(frozen=True, slots=True)
class RuleSummary:
    rule: EntryRule
    attainable: bool
    trial_count: int
    entered_count: int
    not_triggered_count: int
    unavailable_count: int
    observed_count: int
    trigger_share: Decimal | None
    mean_days_waited: Decimal | None
    median_entry_discount: Decimal | None
    profitable_count: int
    support_share: Decimal | None
    median_net_roi: Decimal | None


@dataclass(frozen=True, slots=True)
class MatchedComparison:
    """One rule against the immediate entry on exactly the events they share.

    ``matched_count`` is the only denominator under which the two columns mean
    the same thing. A rule that looks better here has beaten the benchmark on
    the benchmark's own ground; one that looks better only in ``RuleSummary``
    has merely chosen easier events.
    """

    rule: EntryRule
    attainable: bool
    matched_count: int
    rule_profitable_count: int
    rule_support_share: Decimal | None
    rule_median_net_roi: Decimal | None
    benchmark_profitable_count: int
    benchmark_support_share: Decimal | None
    benchmark_median_net_roi: Decimal | None
    median_net_roi_difference: Decimal | None


@dataclass(frozen=True, slots=True)
class BottomFishingComparison:
    event_count: int
    summaries: tuple[RuleSummary, ...]
    matched: tuple[MatchedComparison, ...]
    explanation_codes: tuple[str, ...]
    version: str = RERELEASE_BOTTOM_FISHING_VERSION


def resolve_bottom_fishing_entry(
    *,
    rule: EntryRule,
    item_id: int,
    event: SupplyPoolEvent,
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    as_of: datetime,
    config: BottomFishingConfig | None = None,
    next_same_family_event_opens_at: datetime | None = None,
) -> BottomFishingEntry:
    """Decide where ``rule`` would have entered after ``event`` closed.

    Only UTC days that had completed before the candidate entry day are ever
    read, so a rule that enters on day seven has seen days zero through seven
    and nothing else. The window closes at ``maximum_wait_days``, at ``as_of``,
    or when the next same-family event opens, whichever comes first — a fresh
    supply injection ends the setup rather than continuing it.
    """

    cfg = config or BottomFishingConfig()
    if rule not in ENTRY_RULES:
        raise BottomFishingError("unsupported entry rule.")
    if item_id <= 0:
        raise BottomFishingError("item_id must be positive.")
    moment = _utc(as_of, "as_of")
    buckets = tuple(historical_buckets)
    attainable = rule != "TROUGH_ORACLE"
    codes: list[str] = ["rerelease_bottom_fishing_research_only_not_execution"]
    if not attainable:
        codes.append("rerelease_bottom_fishing_oracle_bound_not_attainable")

    def empty(status: EntryStatus, code: str) -> BottomFishingEntry:
        return BottomFishingEntry(
            rule=rule,
            attainable=attainable,
            item_id=item_id,
            event_key=event.event_key,
            family=str(event.family),
            as_of=moment,
            status=status,
            entered=False,
            entry_day_start_utc=None,
            entry_available_at=None,
            entry_price=None,
            days_waited=None,
            candidate_day_count=0,
            pre_event_median=None,
            immediate_price=None,
            window_low=None,
            explanation_codes=tuple(dict.fromkeys([*codes, code])),
        )

    if moment <= event.closes_at:
        return empty(
            "EVENT_NOT_COMPLETED", "rerelease_bottom_fishing_event_still_open"
        )

    horizon_end = event.closes_at + timedelta(days=cfg.maximum_wait_days)
    cutoff = min(moment, horizon_end)
    next_open = (
        _utc(next_same_family_event_opens_at, "next_same_family_event_opens_at")
        if next_same_family_event_opens_at is not None
        else None
    )
    if next_open is not None:
        cutoff = min(cutoff, next_open)
        codes.append("rerelease_bottom_fishing_window_capped_by_next_supply_event")

    candidates = _completed_days(
        buckets, window_start=event.closes_at, as_of=cutoff
    )
    if not candidates:
        return empty(
            "NO_POST_EVENT_EVIDENCE",
            "rerelease_bottom_fishing_no_completed_post_event_day",
        )

    immediate = candidates[0].vwap_price
    window_low = min(point.vwap_price for point in candidates)
    pre_median = _pre_event_median(buckets, event=event, config=cfg)

    chosen: DailyHistoricalTradeEvidence | None
    if rule == "IMMEDIATE":
        chosen = candidates[0]
    elif rule == "TROUGH_ORACLE":
        chosen = min(
            candidates, key=lambda point: (point.vwap_price, point.day_start_utc)
        )
    elif rule == "DRAWDOWN_TRIGGER":
        if pre_median is None:
            return empty(
                "NO_PRE_EVENT_BASELINE",
                "rerelease_bottom_fishing_no_pre_event_baseline",
            )
        threshold = pre_median * cfg.drawdown_trigger_ratio
        chosen = next(
            (point for point in candidates if point.vwap_price <= threshold), None
        )
    else:
        chosen = _stabilised_entry(
            candidates,
            stabilised_days=cfg.stabilised_days,
            require_prior_new_low=rule == "PATIENT_FLOOR",
        )

    if chosen is None:
        codes.append("rerelease_bottom_fishing_rule_did_not_trigger_in_window")
        return BottomFishingEntry(
            rule=rule,
            attainable=attainable,
            item_id=item_id,
            event_key=event.event_key,
            family=str(event.family),
            as_of=moment,
            status="NOT_TRIGGERED",
            entered=False,
            entry_day_start_utc=None,
            entry_available_at=None,
            entry_price=None,
            days_waited=None,
            candidate_day_count=len(candidates),
            pre_event_median=pre_median,
            immediate_price=immediate,
            window_low=window_low,
            explanation_codes=tuple(dict.fromkeys(codes)),
        )

    waited = (chosen.day_start_utc - candidates[0].day_start_utc).days
    codes.append(f"rerelease_bottom_fishing_entry_rule_{rule.lower()}")
    if waited:
        codes.append("rerelease_bottom_fishing_entry_delayed_past_first_day")
    return BottomFishingEntry(
        rule=rule,
        attainable=attainable,
        item_id=item_id,
        event_key=event.event_key,
        family=str(event.family),
        as_of=moment,
        status="ENTERED",
        entered=True,
        entry_day_start_utc=chosen.day_start_utc,
        entry_available_at=chosen.day_start_utc + timedelta(days=1),
        entry_price=chosen.vwap_price,
        days_waited=waited,
        candidate_day_count=len(candidates),
        pre_event_median=pre_median,
        immediate_price=immediate,
        window_low=window_low,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def evaluate_bottom_fishing_outcome(
    *,
    entry: BottomFishingEntry,
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    as_of: datetime,
    config: BottomFishingConfig | None = None,
    next_same_family_event_opens_at: datetime | None = None,
) -> BottomFishingOutcome:
    """Score one entry after holding it for the configured number of days.

    The holding period runs from the entry day, identically for every rule, so
    a later entry is never charged for a shorter hold. The cost of waiting
    shows up where it belongs: in ``days_waited`` and in the outcomes that a
    late entry pushes past the end of the data.
    """

    cfg = config or BottomFishingConfig()
    moment = _utc(as_of, "as_of")
    codes: list[str] = ["rerelease_bottom_fishing_outcome_research_only"]
    if not entry.attainable:
        codes.append("rerelease_bottom_fishing_oracle_bound_not_attainable")

    def empty(status: OutcomeStatus, code: str) -> BottomFishingOutcome:
        return BottomFishingOutcome(
            rule=entry.rule,
            attainable=entry.attainable,
            item_id=entry.item_id,
            event_key=entry.event_key,
            status=status,
            entry_price=entry.entry_price,
            exit_day_start_utc=None,
            exit_price=None,
            sale_proceeds=None,
            net_profit=None,
            net_roi=None,
            profitable=None,
            holding_days=cfg.holding_days,
            explanation_codes=tuple(dict.fromkeys([*codes, code])),
        )

    if not entry.entered or entry.entry_day_start_utc is None:
        return empty("NOT_ENTERED", "rerelease_bottom_fishing_nothing_was_bought")
    assert entry.entry_price is not None

    target = entry.entry_day_start_utc + timedelta(days=cfg.holding_days)
    next_open = (
        _utc(next_same_family_event_opens_at, "next_same_family_event_opens_at")
        if next_same_family_event_opens_at is not None
        else None
    )
    if next_open is not None and next_open <= target:
        return empty(
            "SUPERSEDED_BY_NEXT_SUPPLY_EVENT",
            "rerelease_bottom_fishing_hold_spans_the_next_supply_event",
        )
    cutoff = min(moment, next_open) if next_open is not None else moment
    forward = _completed_days(
        historical_buckets, window_start=entry.entry_day_start_utc, as_of=cutoff
    )
    exit_point = next(
        (point for point in forward if point.day_start_utc >= target), None
    )
    if exit_point is None:
        return empty(
            "OUTCOME_NOT_OBSERVABLE",
            "rerelease_bottom_fishing_holding_period_not_yet_complete",
        )

    proceeds = sale_proceeds(
        exit_point.vwap_price, cfg.fee_rate, cfg.currency_quantum
    )
    profit = proceeds - entry.entry_price
    roi = profit / entry.entry_price
    codes.append(
        "rerelease_bottom_fishing_exit_profitable_after_fee"
        if profit > Decimal("0")
        else "rerelease_bottom_fishing_exit_not_profitable_after_fee"
    )
    return BottomFishingOutcome(
        rule=entry.rule,
        attainable=entry.attainable,
        item_id=entry.item_id,
        event_key=entry.event_key,
        status="OBSERVED",
        entry_price=entry.entry_price,
        exit_day_start_utc=exit_point.day_start_utc,
        exit_price=exit_point.vwap_price,
        sale_proceeds=proceeds,
        net_profit=profit,
        net_roi=roi,
        profitable=profit > Decimal("0"),
        holding_days=cfg.holding_days,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def compare_bottom_fishing_rules(
    *, trials: Iterable[BottomFishingTrial]
) -> BottomFishingComparison:
    """Summarize each rule, and match every rule against the immediate entry.

    Two tables are produced because either alone misleads. ``summaries`` shows
    what a rule did across all the events it was offered, including the ones it
    declined. ``matched`` removes the difference in which events each rule
    traded, which is the only way a return comparison means anything.
    """

    pool = tuple(trials)
    by_rule: dict[EntryRule, list[BottomFishingTrial]] = {}
    for trial in pool:
        by_rule.setdefault(trial.entry.rule, []).append(trial)
    events = {(trial.entry.item_id, trial.entry.event_key) for trial in pool}

    benchmark = {
        (trial.entry.item_id, trial.entry.event_key): trial
        for trial in by_rule.get(BENCHMARK_ENTRY_RULE, ())
        if trial.outcome.status == "OBSERVED"
    }

    summaries: list[RuleSummary] = []
    matched: list[MatchedComparison] = []
    for rule in ENTRY_RULES:
        rows = by_rule.get(rule)
        if not rows:
            continue
        entered = [row for row in rows if row.entry.entered]
        observed = [row for row in rows if row.outcome.status == "OBSERVED"]
        waits = tuple(
            Decimal(row.entry.days_waited)
            for row in entered
            if row.entry.days_waited is not None
        )
        discounts = tuple(
            row.entry.entry_discount
            for row in entered
            if row.entry.entry_discount is not None
        )
        rois = tuple(
            row.outcome.net_roi
            for row in observed
            if row.outcome.net_roi is not None
        )
        profitable = sum(bool(row.outcome.profitable) for row in observed)
        summaries.append(
            RuleSummary(
                rule=rule,
                attainable=rows[0].entry.attainable,
                trial_count=len(rows),
                entered_count=len(entered),
                not_triggered_count=sum(
                    row.entry.status == "NOT_TRIGGERED" for row in rows
                ),
                unavailable_count=sum(
                    row.outcome.status
                    in ("OUTCOME_NOT_OBSERVABLE", "SUPERSEDED_BY_NEXT_SUPPLY_EVENT")
                    for row in rows
                ),
                observed_count=len(observed),
                trigger_share=(
                    Decimal(len(entered)) / Decimal(len(rows)) if rows else None
                ),
                mean_days_waited=(
                    sum(waits, Decimal("0")) / Decimal(len(waits)) if waits else None
                ),
                median_entry_discount=_median(discounts),
                profitable_count=profitable,
                support_share=(
                    Decimal(profitable) / Decimal(len(observed)) if observed else None
                ),
                median_net_roi=_median(rois),
            )
        )

        if rule == BENCHMARK_ENTRY_RULE:
            continue
        pairs = [
            (row, benchmark[(row.entry.item_id, row.entry.event_key)])
            for row in observed
            if (row.entry.item_id, row.entry.event_key) in benchmark
        ]
        rule_rois = tuple(
            row.outcome.net_roi for row, _ in pairs if row.outcome.net_roi is not None
        )
        base_rois = tuple(
            base.outcome.net_roi
            for _, base in pairs
            if base.outcome.net_roi is not None
        )
        rule_median = _median(rule_rois)
        base_median = _median(base_rois)
        matched.append(
            MatchedComparison(
                rule=rule,
                attainable=rows[0].entry.attainable,
                matched_count=len(pairs),
                rule_profitable_count=sum(bool(row.outcome.profitable) for row, _ in pairs),
                rule_support_share=(
                    Decimal(sum(bool(row.outcome.profitable) for row, _ in pairs))
                    / Decimal(len(pairs))
                    if pairs
                    else None
                ),
                rule_median_net_roi=rule_median,
                benchmark_profitable_count=sum(
                    bool(base.outcome.profitable) for _, base in pairs
                ),
                benchmark_support_share=(
                    Decimal(sum(bool(base.outcome.profitable) for _, base in pairs))
                    / Decimal(len(pairs))
                    if pairs
                    else None
                ),
                benchmark_median_net_roi=base_median,
                median_net_roi_difference=(
                    rule_median - base_median
                    if rule_median is not None and base_median is not None
                    else None
                ),
            )
        )

    codes = [
        "rerelease_bottom_fishing_comparison_research_only",
        "rerelease_bottom_fishing_rows_repeat_items_and_are_not_independent",
    ]
    if not pool:
        codes.append("rerelease_bottom_fishing_no_trials_to_compare")
    if any(not value.attainable for value in summaries):
        codes.append("rerelease_bottom_fishing_table_includes_an_unattainable_bound")
    return BottomFishingComparison(
        event_count=len(events),
        summaries=tuple(summaries),
        matched=tuple(matched),
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def _stabilised_entry(
    candidates: Sequence[DailyHistoricalTradeEvidence],
    *,
    stabilised_days: int,
    require_prior_new_low: bool,
) -> DailyHistoricalTradeEvidence | None:
    """First day ending a run of ``stabilised_days`` days that made no new low.

    Decidable from the days up to and including the candidate, which is what
    makes it a rule rather than a hindsight observation: at day ``i`` the run
    is checked against the running minimum of the days before it, all of which
    had completed.
    """

    running_low = candidates[0].vwap_price
    made_new_low: list[bool] = [False]
    seen_new_low = False
    for point in candidates[1:]:
        fresh = point.vwap_price < running_low
        made_new_low.append(fresh)
        if fresh:
            running_low = point.vwap_price
            seen_new_low = True
        if require_prior_new_low and not seen_new_low:
            continue
        index = len(made_new_low) - 1
        if index + 1 < stabilised_days:
            continue
        window = made_new_low[index - stabilised_days + 1 : index + 1]
        if not any(window):
            return point
    return None


def _completed_days(
    buckets: Iterable[HistoricalTradeBucketEvidence],
    *,
    window_start: datetime,
    as_of: datetime,
) -> tuple[DailyHistoricalTradeEvidence, ...]:
    if as_of <= window_start:
        return ()
    evidence = select_historical_trade_evidence(
        buckets, as_of=as_of, window_start=window_start
    )
    return tuple(
        point
        for point in aggregate_trade_evidence_by_utc_day(evidence.buckets)
        if point.day_start_utc >= window_start
        and point.day_start_utc + timedelta(days=1) <= as_of
    )


def _pre_event_median(
    buckets: Iterable[HistoricalTradeBucketEvidence],
    *,
    event: SupplyPoolEvent,
    config: BottomFishingConfig,
) -> Decimal | None:
    """Median daily price over one event duration before the event opened."""

    duration = event.closes_at - event.opens_at
    window_start = event.opens_at - duration
    days = _completed_days(buckets, window_start=window_start, as_of=event.opens_at)
    if len(days) < config.minimum_pre_event_days:
        return None
    return _median(tuple(point.vwap_price for point in days))


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
        raise BottomFishingError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
