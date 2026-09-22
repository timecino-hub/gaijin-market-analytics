"""Stage 25 — the one general strategy, and how well the cohort supports it.

The post-event entry rule publishes a *signal*: which vehicles are inside an
entry window right now. This module publishes the *evidence for the rule
itself* — what actually happened, event by event, when the whole strategy was
applied to every vehicle alike.

It is the pooled counterpart to stage 24. Vehicle potential reads one vehicle's
own series and never pools; this reads every completed event in the cohort and
never separates them. The two answer different questions on purpose.

The strategy has four steps and this module measures the first three:

1. **Select** — the entry price must be at or below a fraction of the vehicle's
   own pre-event baseline.
2. **Enter** — at market, on the first completed day after the event closes.
   No timing rule beat this one in the replay, so there is nothing to choose.
3. **Exit** — a resting offer at a net target, the remainder at the holding cap.
4. **Size** — equal weight under a concurrency cap. Portfolio construction is a
   replay question, not an outcome-ledger one, and is deliberately absent here.

Three properties are load-bearing:

**No horizon is reported before it closes.** An event whose 180th day has not
completed at ``as_of`` has no 180-day return, and it is counted as pending
rather than quietly dropped — a dropped pending event would bias the ledger
toward whatever the settled ones happened to do.

**A miss is not a hole.** A take-profit offer that never fills still produces an
outcome: the position is sold at the holding cap, and that number is in every
column. A hit rate computed over filled orders only would flatter the rule.

**Events are not independent.** They repeat vehicles and families, so every
summary carries both its event count and the number of distinct vehicles behind
it. A median over forty events drawn from seventeen vehicles is not a median
over forty independent trials, and the reader is given what they need to see
that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Iterable, Literal

from .historical_trades import (
    HistoricalTradeBucketEvidence,
    aggregate_trade_evidence_by_utc_day,
    select_historical_trade_evidence,
)
from .supply_cycles import SupplyPoolEvent

STRATEGY_EVIDENCE_VERSION = "1.0.0"

_ZERO = Decimal("0")
_ONE = Decimal("1")
_TWO = Decimal("2")

EventOutcomeState = Literal[
    "EVALUATED",
    "EVENT_NOT_COMPLETED",
    "NO_PRE_EVENT_BASELINE",
    "NO_ENTRY_PRICE",
]
HorizonState = Literal["SETTLED", "PENDING", "SUPERSEDED", "NO_EXIT_PRICE"]


class StrategyEvidenceError(ValueError):
    """Raised when a strategy evidence input is not usable."""


@dataclass(frozen=True, slots=True)
class StrategyEvidenceConfig:
    # 30/60/90/180 is the ladder the replay used. It is reported whole because
    # the shape is the finding: the short horizons lose money and only the long
    # one pays, which is an argument against trading this quickly.
    horizons: tuple[int, ...] = (30, 60, 90, 180)
    primary_horizon_days: int = 180
    # The published selection rule. Reported alongside the events it rejects,
    # so the filter's claim can be checked rather than taken.
    drawdown_threshold: Decimal = Decimal("0.75")
    # Net of the sale fee, not gross. Reported across a ladder so a reader can
    # see that the low targets are dominated instead of being told.
    take_profit_targets: tuple[Decimal, ...] = (
        Decimal("0.15"),
        Decimal("0.20"),
        Decimal("0.25"),
        Decimal("0.30"),
        Decimal("0.40"),
    )
    fee_rate: Decimal = Decimal("0.15")
    minimum_pre_event_days: int = 3
    # What a reader actually has to spend. This is the constraint that decides
    # the strategy for most people: at a hundred coins the choice is one
    # vehicle or none, so a portfolio rule with twenty slots describes a
    # situation they are not in.
    budget: Decimal = Decimal("100")
    # Reported as a ladder so the question "what would more capital buy me"
    # has an answer on the page rather than requiring another run.
    budget_ladder: tuple[Decimal, ...] = (
        Decimal("25"),
        Decimal("50"),
        Decimal("100"),
        Decimal("200"),
        Decimal("500"),
    )

    def __post_init__(self) -> None:
        if not self.horizons or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in self.horizons
        ):
            raise StrategyEvidenceError("horizons must be positive integers.")
        if len(set(self.horizons)) != len(self.horizons):
            raise StrategyEvidenceError("horizons must be distinct.")
        if self.primary_horizon_days not in self.horizons:
            raise StrategyEvidenceError("primary_horizon_days must be one of horizons.")
        if (
            isinstance(self.minimum_pre_event_days, bool)
            or not isinstance(self.minimum_pre_event_days, int)
            or self.minimum_pre_event_days < 1
        ):
            raise StrategyEvidenceError("minimum_pre_event_days must be positive.")
        if (
            not isinstance(self.drawdown_threshold, Decimal)
            or not self.drawdown_threshold.is_finite()
            or not _ZERO < self.drawdown_threshold <= _ONE
        ):
            raise StrategyEvidenceError(
                "drawdown_threshold must be a finite Decimal in (0, 1]."
            )
        if not self.take_profit_targets:
            raise StrategyEvidenceError("at least one take-profit target is required.")
        for target in self.take_profit_targets:
            if (
                not isinstance(target, Decimal)
                or not target.is_finite()
                or target <= _ZERO
            ):
                raise StrategyEvidenceError(
                    "take_profit_targets must be positive Decimals."
                )
        if len(set(self.take_profit_targets)) != len(self.take_profit_targets):
            raise StrategyEvidenceError("take_profit_targets must be distinct.")
        if (
            not isinstance(self.fee_rate, Decimal)
            or not self.fee_rate.is_finite()
            or not _ZERO <= self.fee_rate < _ONE
        ):
            raise StrategyEvidenceError("fee_rate must be a finite Decimal in [0, 1).")
        for value in (self.budget, *self.budget_ladder):
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value <= _ZERO
            ):
                raise StrategyEvidenceError(
                    "budget and every ladder rung must be positive Decimals."
                )


@dataclass(frozen=True, slots=True)
class HorizonOutcome:
    """What one event returned at one horizon, or why it returned nothing."""

    horizon_days: int
    state: HorizonState
    exit_day: datetime | None
    exit_price: Decimal | None
    net_roi: Decimal | None


@dataclass(frozen=True, slots=True)
class TakeProfitOutcome:
    """What a resting offer at ``target_net_roi`` would have produced.

    ``pure_net_roi`` sells the whole position at the offer; ``split_net_roi``
    sells half there and carries the rest to the holding cap. When the offer
    never fills both fall back to the cap's own return, which is what a holder
    would actually have got.
    """

    target_net_roi: Decimal
    offer_price: Decimal | None
    hit: bool
    days_to_hit: int | None
    pure_net_roi: Decimal | None
    split_net_roi: Decimal | None


@dataclass(frozen=True, slots=True)
class EventOutcome:
    """One completed supply event, run through the whole strategy."""

    item_id: int
    event_key: str
    opens_at: datetime
    closes_at: datetime
    state: EventOutcomeState
    pre_event_baseline: Decimal | None
    entry_day: datetime | None
    entry_price: Decimal | None
    drawdown_ratio: Decimal | None
    passes_drawdown_filter: bool
    # The next event opening on this vehicle. A holding window that runs past it
    # is measuring the next supply shock, not this recovery.
    superseded_at: datetime | None
    horizons: tuple[HorizonOutcome, ...]
    take_profits: tuple[TakeProfitOutcome, ...]

    def horizon(self, horizon_days: int) -> HorizonOutcome | None:
        for value in self.horizons:
            if value.horizon_days == horizon_days:
                return value
        return None


@dataclass(frozen=True, slots=True)
class ReturnSummary:
    """A distribution of realized returns, with the dependence left visible.

    ``vehicle_count`` is here because ``sample_size`` alone overstates the
    evidence: these events repeat vehicles, so forty events from seventeen
    vehicles are not forty independent trials.
    """

    sample_size: int
    vehicle_count: int
    median: Decimal | None
    mean: Decimal | None
    # Share of outcomes strictly above zero, after the fee.
    support_ratio: Decimal | None
    tenth_percentile: Decimal | None
    ninetieth_percentile: Decimal | None


@dataclass(frozen=True, slots=True)
class HorizonEvidence:
    """One horizon, split by the selection rule so the filter can be checked."""

    horizon_days: int
    all_events: ReturnSummary
    selected: ReturnSummary
    rejected: ReturnSummary
    pending_count: int
    superseded_count: int


@dataclass(frozen=True, slots=True)
class ExitRuleEvidence:
    """One exit rule measured over the selected, settled events."""

    kind: Literal["FIXED_HOLD", "TAKE_PROFIT", "SPLIT_HALF"]
    target_net_roi: Decimal | None
    hit_ratio: Decimal | None
    median_days_to_hit: int | None
    summary: ReturnSummary


@dataclass(frozen=True, slots=True)
class BudgetRung:
    """What one budget could have bought, and what those buys returned.

    ``median_units`` is whole units, because a coupon cannot be bought in
    halves: at 100 coins against a 74-coin vehicle the answer is one, and the
    remaining 26 buy nothing.
    """

    budget: Decimal
    affordable_count: int
    total_count: int
    median_units: int | None
    summary: ReturnSummary


@dataclass(frozen=True, slots=True)
class BudgetEvidence:
    """The strategy seen from a fixed amount of money.

    The portfolio question — how to weight twenty positions — only exists above
    a capital level most readers are not at. Below it the strategy is one
    position at a time, so what matters is which opportunities were reachable
    at all and how the single-trade distribution looked.

    ``deepest_drawdown`` against ``choice_cycle_events`` is the one pick rule
    worth testing: when an event closes, several vehicles qualify at once and
    only one can be bought. Both are reported over the cycles where a choice
    actually existed, because a cycle with a single affordable vehicle tells
    you nothing about choosing.
    """

    budget: Decimal
    ladder: tuple[BudgetRung, ...]
    choice_cycle_count: int
    deepest_drawdown: ReturnSummary
    choice_cycle_events: ReturnSummary


@dataclass(frozen=True, slots=True)
class StrategyEvidence:
    """The whole ledger: what the rule did, on everything it has been run on."""

    as_of: datetime
    drawdown_threshold: Decimal
    primary_horizon_days: int
    fee_rate: Decimal

    completed_event_count: int
    evaluated_event_count: int
    vehicle_count: int
    selected_event_count: int
    rejected_event_count: int
    # Events that closed but produced no usable reading, kept in view so the
    # denominator on the page is the real one.
    no_baseline_count: int
    no_entry_price_count: int

    horizons: tuple[HorizonEvidence, ...]
    exit_frontier: tuple[ExitRuleEvidence, ...]
    budget: BudgetEvidence

    explanation_codes: tuple[str, ...]
    research_only: bool = True
    version: str = STRATEGY_EVIDENCE_VERSION


def evaluate_event_outcomes(
    *,
    item_id: int,
    as_of: datetime,
    events: Iterable[SupplyPoolEvent],
    historical_buckets: Iterable[HistoricalTradeBucketEvidence],
    config: StrategyEvidenceConfig | None = None,
) -> tuple[EventOutcome, ...]:
    """Run the strategy over one vehicle's completed events.

    Every reading comes from days completed at ``as_of``. The entry is the first
    completed day at or after the close, which is the convention the replay
    settled on; each horizon is read from the last completed day inside it.
    """

    cfg = config or StrategyEvidenceConfig()
    if item_id <= 0:
        raise StrategyEvidenceError("item_id must be positive.")
    moment = _utc(as_of, "as_of")
    # Aggregated once, then sliced per event. Re-selecting from the raw buckets
    # for every window costs a full-series aggregation each time, and there are
    # two windows per event plus a decade of buckets behind them.
    series = _daily_series(tuple(historical_buckets), as_of=moment)
    ordered = sorted(events, key=lambda value: value.opens_at)

    outcomes: list[EventOutcome] = []
    for index, event in enumerate(ordered):
        following = ordered[index + 1 :]
        superseded_at = following[0].opens_at if following else None

        if event.closes_at > moment:
            outcomes.append(
                _empty_outcome(
                    item_id=item_id,
                    event=event,
                    state="EVENT_NOT_COMPLETED",
                    superseded_at=superseded_at,
                )
            )
            continue

        baseline = _pre_event_baseline(series, event=event, config=cfg)
        post = _window(series, window_start=event.closes_at, as_of=moment)
        if not post:
            outcomes.append(
                _empty_outcome(
                    item_id=item_id,
                    event=event,
                    state="NO_ENTRY_PRICE",
                    superseded_at=superseded_at,
                    pre_event_baseline=baseline,
                )
            )
            continue

        entry_day = post[0].day_start_utc
        entry_price = post[0].vwap_price
        if entry_price <= _ZERO:
            outcomes.append(
                _empty_outcome(
                    item_id=item_id,
                    event=event,
                    state="NO_ENTRY_PRICE",
                    superseded_at=superseded_at,
                    pre_event_baseline=baseline,
                )
            )
            continue
        if baseline is None or baseline <= _ZERO:
            outcomes.append(
                _empty_outcome(
                    item_id=item_id,
                    event=event,
                    state="NO_PRE_EVENT_BASELINE",
                    superseded_at=superseded_at,
                    entry_day=entry_day,
                    entry_price=entry_price,
                )
            )
            continue

        # Only days after the entry are part of the holding period; the entry
        # day itself is the purchase, not an exit opportunity.
        holding = tuple(point for point in post if point.day_start_utc > entry_day)
        horizons = tuple(
            _horizon_outcome(
                holding,
                horizon_days=horizon,
                entry_day=entry_day,
                entry_price=entry_price,
                as_of=moment,
                superseded_at=superseded_at,
                config=cfg,
            )
            for horizon in sorted(cfg.horizons)
        )
        primary = next(
            value
            for value in horizons
            if value.horizon_days == cfg.primary_horizon_days
        )
        take_profits = tuple(
            _take_profit_outcome(
                holding,
                target=target,
                entry_day=entry_day,
                entry_price=entry_price,
                primary=primary,
                config=cfg,
            )
            for target in cfg.take_profit_targets
        )
        ratio = entry_price / baseline
        outcomes.append(
            EventOutcome(
                item_id=item_id,
                event_key=event.event_key,
                opens_at=event.opens_at,
                closes_at=event.closes_at,
                state="EVALUATED",
                pre_event_baseline=baseline,
                entry_day=entry_day,
                entry_price=entry_price,
                drawdown_ratio=ratio,
                passes_drawdown_filter=ratio <= cfg.drawdown_threshold,
                superseded_at=superseded_at,
                horizons=horizons,
                take_profits=take_profits,
            )
        )
    return tuple(outcomes)


def summarise_strategy_evidence(
    *,
    as_of: datetime,
    outcomes: Iterable[EventOutcome],
    config: StrategyEvidenceConfig | None = None,
) -> StrategyEvidence:
    """Pool the per-event ledger into the numbers the strategy is judged on.

    The universe for every horizon comparison is the set of events that both
    settled at that horizon and have a drawdown ratio, so ``selected`` and
    ``rejected`` always add up to ``all_events``. Events missing a baseline are
    reported as their own count rather than folded into one side.
    """

    cfg = config or StrategyEvidenceConfig()
    moment = _utc(as_of, "as_of")
    ledger = tuple(outcomes)
    completed = tuple(
        value for value in ledger if value.state != "EVENT_NOT_COMPLETED"
    )
    evaluated = tuple(value for value in completed if value.state == "EVALUATED")
    selected = tuple(value for value in evaluated if value.passes_drawdown_filter)
    rejected = tuple(value for value in evaluated if not value.passes_drawdown_filter)

    horizons = tuple(
        _horizon_evidence(evaluated, horizon_days=horizon)
        for horizon in sorted(cfg.horizons)
    )
    frontier = _exit_frontier(selected, config=cfg)
    budget = _budget_evidence(selected, config=cfg)

    codes = ["strategy_evidence_descriptive_ledger_not_walk_forward_validated"]
    if len({value.item_id for value in evaluated}) < len(evaluated):
        codes.append("strategy_evidence_events_repeat_vehicles_and_are_not_independent")
    primary = next(
        value for value in horizons if value.horizon_days == cfg.primary_horizon_days
    )
    if primary.pending_count:
        codes.append("strategy_evidence_some_events_have_not_finished_their_horizon")
    if primary.superseded_count:
        codes.append("strategy_evidence_some_windows_were_cut_by_the_next_supply_event")
    if primary.selected.sample_size < 30:
        codes.append("strategy_evidence_selected_sample_is_small")
    reachable = next(
        (rung for rung in budget.ladder if rung.budget == cfg.budget), None
    )
    if reachable is not None and reachable.affordable_count < reachable.total_count:
        codes.append("strategy_evidence_some_opportunities_were_out_of_budget")
    if budget.choice_cycle_count and budget.choice_cycle_count < 10:
        codes.append("strategy_evidence_too_few_cycles_to_rank_candidates")

    return StrategyEvidence(
        as_of=moment,
        drawdown_threshold=cfg.drawdown_threshold,
        primary_horizon_days=cfg.primary_horizon_days,
        fee_rate=cfg.fee_rate,
        completed_event_count=len(completed),
        evaluated_event_count=len(evaluated),
        vehicle_count=len({value.item_id for value in evaluated}),
        selected_event_count=len(selected),
        rejected_event_count=len(rejected),
        no_baseline_count=sum(
            1 for value in completed if value.state == "NO_PRE_EVENT_BASELINE"
        ),
        no_entry_price_count=sum(
            1 for value in completed if value.state == "NO_ENTRY_PRICE"
        ),
        horizons=horizons,
        exit_frontier=frontier,
        budget=budget,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def _horizon_evidence(
    evaluated: tuple[EventOutcome, ...], *, horizon_days: int
) -> HorizonEvidence:
    settled: list[tuple[EventOutcome, Decimal]] = []
    pending = 0
    superseded = 0
    for outcome in evaluated:
        reading = outcome.horizon(horizon_days)
        if reading is None:
            continue
        if reading.state == "PENDING":
            pending += 1
            continue
        if reading.state == "SUPERSEDED":
            superseded += 1
            continue
        if reading.state != "SETTLED" or reading.net_roi is None:
            continue
        settled.append((outcome, reading.net_roi))

    chosen = tuple(value for value in settled if value[0].passes_drawdown_filter)
    refused = tuple(value for value in settled if not value[0].passes_drawdown_filter)
    return HorizonEvidence(
        horizon_days=horizon_days,
        all_events=_summarise(tuple(settled)),
        selected=_summarise(chosen),
        rejected=_summarise(refused),
        pending_count=pending,
        superseded_count=superseded,
    )


def _exit_frontier(
    selected: tuple[EventOutcome, ...], *, config: StrategyEvidenceConfig
) -> tuple[ExitRuleEvidence, ...]:
    """Every exit rule scored on the same events, so the rows are comparable.

    An event is in the frontier only if its primary horizon settled — a
    take-profit that has not had its full window yet cannot be compared with
    one that has.
    """

    held: list[tuple[EventOutcome, Decimal]] = []
    for outcome in selected:
        reading = outcome.horizon(config.primary_horizon_days)
        if reading is None or reading.state != "SETTLED" or reading.net_roi is None:
            continue
        held.append((outcome, reading.net_roi))
    settled = tuple(outcome for outcome, _ in held)

    rules: list[ExitRuleEvidence] = [
        ExitRuleEvidence(
            kind="FIXED_HOLD",
            target_net_roi=None,
            hit_ratio=None,
            median_days_to_hit=None,
            summary=_summarise(tuple(held)),
        )
    ]
    for target in config.take_profit_targets:
        usable = tuple(
            (outcome, reading)
            for outcome in settled
            if (reading := _take_profit_for(outcome, target)) is not None
        )
        hits = tuple(reading for _, reading in usable if reading.hit)
        hit_ratio = Decimal(len(hits)) / Decimal(len(usable)) if usable else None
        days = tuple(
            reading.days_to_hit for reading in hits if reading.days_to_hit is not None
        )
        for kind, attribute in (
            ("TAKE_PROFIT", "pure_net_roi"),
            ("SPLIT_HALF", "split_net_roi"),
        ):
            rules.append(
                ExitRuleEvidence(
                    kind=kind,  # type: ignore[arg-type]
                    target_net_roi=target,
                    hit_ratio=hit_ratio,
                    median_days_to_hit=_median_int(days) if days else None,
                    summary=_summarise(
                        tuple(
                            (outcome, getattr(reading, attribute))
                            for outcome, reading in usable
                            if getattr(reading, attribute) is not None
                        )
                    ),
                )
            )
    return tuple(rules)


def _budget_evidence(
    selected: tuple[EventOutcome, ...], *, config: StrategyEvidenceConfig
) -> BudgetEvidence:
    """What the strategy looks like to someone with a fixed amount to spend.

    Only settled events are used, so every rung is scored on outcomes that
    finished rather than on a mix of finished and running positions.
    """

    settled: list[tuple[EventOutcome, Decimal]] = []
    for outcome in selected:
        reading = outcome.horizon(config.primary_horizon_days)
        if reading is None or reading.state != "SETTLED" or reading.net_roi is None:
            continue
        if outcome.entry_price is None or outcome.entry_price <= _ZERO:
            continue
        settled.append((outcome, reading.net_roi))

    rungs = tuple(
        _budget_rung(settled, budget=rung) for rung in sorted(config.budget_ladder)
    )
    if not any(rung.budget == config.budget for rung in rungs):
        rungs = tuple(
            sorted(
                (*rungs, _budget_rung(settled, budget=config.budget)),
                key=lambda rung: rung.budget,
            )
        )

    affordable = tuple(
        pair for pair in settled if pair[0].entry_price <= config.budget  # type: ignore[operator]
    )
    cycles: dict[datetime, list[tuple[EventOutcome, Decimal]]] = {}
    for pair in affordable:
        cycles.setdefault(pair[0].closes_at, []).append(pair)
    # A cycle offering one affordable vehicle is not a choice, and including it
    # would credit a pick rule for a decision it never made.
    contested = [group for group in cycles.values() if len(group) > 1]
    deepest = tuple(
        min(
            group,
            key=lambda pair: (
                pair[0].drawdown_ratio
                if pair[0].drawdown_ratio is not None
                else _ONE,
                pair[0].item_id,
            ),
        )
        for group in contested
    )
    every = tuple(pair for group in contested for pair in group)

    return BudgetEvidence(
        budget=config.budget,
        ladder=rungs,
        choice_cycle_count=len(contested),
        deepest_drawdown=_summarise(deepest),
        choice_cycle_events=_summarise(every),
    )


def _budget_rung(
    settled: tuple[tuple[EventOutcome, Decimal], ...] | list, *, budget: Decimal
) -> BudgetRung:
    affordable = tuple(
        pair
        for pair in settled
        if pair[0].entry_price is not None and pair[0].entry_price <= budget
    )
    units = tuple(
        int(budget // pair[0].entry_price)  # type: ignore[operator]
        for pair in affordable
    )
    return BudgetRung(
        budget=budget,
        affordable_count=len(affordable),
        total_count=len(settled),
        median_units=_median_int(units) if units else None,
        summary=_summarise(affordable),
    )


def _take_profit_for(
    outcome: EventOutcome, target: Decimal
) -> TakeProfitOutcome | None:
    for value in outcome.take_profits:
        if value.target_net_roi == target:
            return value
    return None


def _summarise(pairs: tuple[tuple[EventOutcome, Decimal], ...]) -> ReturnSummary:
    values = tuple(sorted(value for _, value in pairs))
    vehicles = len({outcome.item_id for outcome, _ in pairs})
    if not values:
        return ReturnSummary(
            sample_size=0,
            vehicle_count=vehicles,
            median=None,
            mean=None,
            support_ratio=None,
            tenth_percentile=None,
            ninetieth_percentile=None,
        )
    return ReturnSummary(
        sample_size=len(values),
        vehicle_count=vehicles,
        median=_median(values),
        mean=sum(values, _ZERO) / Decimal(len(values)),
        support_ratio=Decimal(sum(1 for value in values if value > _ZERO))
        / Decimal(len(values)),
        tenth_percentile=_percentile(values, Decimal("0.10")),
        ninetieth_percentile=_percentile(values, Decimal("0.90")),
    )


def _horizon_outcome(
    holding,
    *,
    horizon_days: int,
    entry_day: datetime,
    entry_price: Decimal,
    as_of: datetime,
    superseded_at: datetime | None,
    config: StrategyEvidenceConfig,
) -> HorizonOutcome:
    target_day = entry_day + timedelta(days=horizon_days)
    if superseded_at is not None and superseded_at <= target_day:
        return HorizonOutcome(
            horizon_days=horizon_days,
            state="SUPERSEDED",
            exit_day=None,
            exit_price=None,
            net_roi=None,
        )
    # The horizon closes only once its final day has itself completed.
    if target_day + timedelta(days=1) > as_of:
        return HorizonOutcome(
            horizon_days=horizon_days,
            state="PENDING",
            exit_day=None,
            exit_price=None,
            net_roi=None,
        )
    inside = tuple(point for point in holding if point.day_start_utc <= target_day)
    if not inside:
        return HorizonOutcome(
            horizon_days=horizon_days,
            state="NO_EXIT_PRICE",
            exit_day=None,
            exit_price=None,
            net_roi=None,
        )
    exit_point = inside[-1]
    return HorizonOutcome(
        horizon_days=horizon_days,
        state="SETTLED",
        exit_day=exit_point.day_start_utc,
        exit_price=exit_point.vwap_price,
        net_roi=(exit_point.vwap_price * (_ONE - config.fee_rate)) / entry_price - _ONE,
    )


def _take_profit_outcome(
    holding,
    *,
    target: Decimal,
    entry_day: datetime,
    entry_price: Decimal,
    primary: HorizonOutcome,
    config: StrategyEvidenceConfig,
) -> TakeProfitOutcome:
    """Rest an offer at the grossed-up target and see whether it is reached.

    A net target is further away than it reads: clearing 25% after a 15% fee
    needs the price 47.1% higher. A day counts as reaching it when that day's
    own VWAP is at or above the offer, which is conservative — an intraday touch
    that closed lower is a miss here.
    """

    offer = (entry_price * (_ONE + target)) / (_ONE - config.fee_rate)
    horizon_roi = primary.net_roi if primary.state == "SETTLED" else None
    window = tuple(
        point
        for point in holding
        if point.day_start_utc <= entry_day + timedelta(days=config.primary_horizon_days)
    )
    filled = next(
        (point for point in window if point.vwap_price >= offer),
        None,
    )
    if filled is None:
        return TakeProfitOutcome(
            target_net_roi=target,
            offer_price=offer,
            hit=False,
            days_to_hit=None,
            pure_net_roi=horizon_roi,
            split_net_roi=horizon_roi,
        )
    return TakeProfitOutcome(
        target_net_roi=target,
        offer_price=offer,
        hit=True,
        days_to_hit=(filled.day_start_utc - entry_day).days,
        pure_net_roi=target,
        split_net_roi=(
            (target + horizon_roi) / _TWO if horizon_roi is not None else None
        ),
    )


def _empty_outcome(
    *,
    item_id: int,
    event: SupplyPoolEvent,
    state: EventOutcomeState,
    superseded_at: datetime | None,
    pre_event_baseline: Decimal | None = None,
    entry_day: datetime | None = None,
    entry_price: Decimal | None = None,
) -> EventOutcome:
    return EventOutcome(
        item_id=item_id,
        event_key=event.event_key,
        opens_at=event.opens_at,
        closes_at=event.closes_at,
        state=state,
        pre_event_baseline=pre_event_baseline,
        entry_day=entry_day,
        entry_price=entry_price,
        drawdown_ratio=None,
        passes_drawdown_filter=False,
        superseded_at=superseded_at,
        # An event with no entry has no holding period to report horizons for.
        # The empty tuple is the honest shape; a row of nulls would read as a
        # measurement that came back empty rather than one never taken.
        horizons=(),
        take_profits=(),
    )


def _pre_event_baseline(
    series,
    *,
    event: SupplyPoolEvent,
    config: StrategyEvidenceConfig,
) -> Decimal | None:
    duration = event.closes_at - event.opens_at
    days = _window(
        series, window_start=event.opens_at - duration, as_of=event.opens_at
    )
    if len(days) < config.minimum_pre_event_days:
        return None
    return _median(tuple(point.vwap_price for point in days))


def _daily_series(
    buckets: tuple[HistoricalTradeBucketEvidence, ...], *, as_of: datetime
):
    """Every completed UTC day this vehicle traded on, aggregated once.

    Selecting over the widest window is what the per-window calls would each
    have done for the days they kept: the 1h-over-1d precedence is decided per
    day, and a day that a narrower window would have cut in half is dropped by
    the ``>= window_start`` test in ``_window`` either way.
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
    return (ordered[middle - 1] + ordered[middle]) / _TWO


def _median_int(values: tuple[int, ...]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _percentile(ordered: tuple[Decimal, ...], fraction: Decimal) -> Decimal | None:
    """Nearest-rank percentile: no interpolation between two real outcomes.

    Interpolating would invent a return nobody realized. With samples this
    small that matters — the tenth percentile of forty-six events should be an
    event that happened.
    """

    if not ordered:
        return None
    rank = int((fraction * Decimal(len(ordered))).to_integral_value(ROUND_CEILING))
    index = min(max(rank - 1, 0), len(ordered) - 1)
    return ordered[index]


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StrategyEvidenceError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
