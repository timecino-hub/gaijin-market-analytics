"""Position sizing and portfolio simulation for close-entry decisions.

The decision this module sizes is the one the cohort shows is worth making: buy
the first completed day after a supply event closes, hold for a fixed horizon.
Everything upstream produces a view on that position's return. What has never
existed is how much to put into it, and what happens when several of them want
the same money at once.

Three constraints make the simulation mean something.

**Capital is finite and positions overlap.** A hundred and eighty day hold ties
money up across several later closes. A backtest that sizes each position
against notional equity it does not have is reporting the returns of a larger
account than the one being simulated, so capital here is released only when a
position actually exits, and a candidate that arrives with nothing available is
recorded as ``SKIPPED_NO_CAPITAL`` rather than quietly dropped.

**Sizing is judged against not sizing.** Every arm takes exactly the same
positions in the same order; only the stake differs. A sizing rule that appears
to win by also picking different events has not been measured.

**The distribution is not trusted past what it can support.** Stage 16 found
these forecasts carry two to four atoms, which is far too coarse to justify
full Kelly. The optimum is therefore scaled by an explicit fraction and capped,
and a forecast whose effective sample cannot support its own quantiles is
refused outright instead of sized from optimistically.

Nothing here claims a fill. Prices are daily VWAP proxies and a real order at
these sizes would move them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Iterable, Literal


CLOSE_ENTRY_ALLOCATION_VERSION = "1.0.0"

SizingStatus = Literal[
    "NO_DISTRIBUTION",
    "UNSUPPORTED_DISTRIBUTION",
    "NO_POSITIVE_EDGE",
    "SIZED",
]
PositionStatus = Literal[
    "SKIPPED_NOT_SIZED",
    "SKIPPED_NO_CAPITAL",
    "SKIPPED_CONCURRENCY_CAP",
    "OUTCOME_UNAVAILABLE",
    "CLOSED",
]
SizingRule = Literal["FRACTIONAL_KELLY", "EQUAL_WEIGHT"]
SIZING_RULES: tuple[SizingRule, ...] = ("FRACTIONAL_KELLY", "EQUAL_WEIGHT")

_ZERO = Decimal("0")
_ONE = Decimal("1")
_LOG_PRECISION = 40


class CloseEntryAllocationError(ValueError):
    """Raised when an allocation input is not usable."""


@dataclass(frozen=True, slots=True)
class ReturnAtom:
    """One atom of the predictive distribution over a position's net ROI."""

    net_roi: Decimal
    weight_share: Decimal

    def __post_init__(self) -> None:
        for name in ("net_roi", "weight_share"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise CloseEntryAllocationError(f"{name} must be a finite Decimal.")
        if self.weight_share <= _ZERO:
            raise CloseEntryAllocationError("weight_share must be positive.")
        if self.net_roi <= -_ONE:
            raise CloseEntryAllocationError(
                "net_roi must exceed -1; a position cannot lose more than its stake."
            )


@dataclass(frozen=True, slots=True)
class CloseEntryCandidate:
    """One position that could be opened the day after an event closed."""

    item_id: int
    event_key: str
    horizon_days: int
    entry_at: datetime
    exit_at: datetime
    distribution: tuple[ReturnAtom, ...]
    distribution_supported: bool
    realized_net_roi: Decimal | None

    def __post_init__(self) -> None:
        if self.item_id <= 0:
            raise CloseEntryAllocationError("item_id must be positive.")
        if not self.event_key.strip():
            raise CloseEntryAllocationError("event_key must be non-empty.")
        if (
            isinstance(self.horizon_days, bool)
            or not isinstance(self.horizon_days, int)
            or self.horizon_days <= 0
        ):
            raise CloseEntryAllocationError("horizon_days must be a positive integer.")
        object.__setattr__(self, "entry_at", _utc(self.entry_at, "entry_at"))
        object.__setattr__(self, "exit_at", _utc(self.exit_at, "exit_at"))
        if self.exit_at <= self.entry_at:
            raise CloseEntryAllocationError("exit_at must be later than entry_at.")
        if self.distribution:
            total = sum(
                (atom.weight_share for atom in self.distribution), _ZERO
            )
            if abs(total - _ONE) > Decimal("0.000001"):
                raise CloseEntryAllocationError(
                    "distribution weights must sum to one."
                )
        if self.realized_net_roi is not None:
            if (
                not isinstance(self.realized_net_roi, Decimal)
                or not self.realized_net_roi.is_finite()
                or self.realized_net_roi <= -_ONE
            ):
                raise CloseEntryAllocationError(
                    "realized_net_roi must be a finite Decimal above -1."
                )


@dataclass(frozen=True, slots=True)
class AllocationConfig:
    starting_capital: Decimal = Decimal("1000.00")
    kelly_fraction: Decimal = Decimal("0.25")
    maximum_position_fraction: Decimal = Decimal("0.10")
    equal_weight_fraction: Decimal = Decimal("0.05")
    minimum_position_fraction: Decimal = Decimal("0.005")
    maximum_concurrent_positions: int = 10
    fraction_grid_step: Decimal = Decimal("0.01")
    currency_quantum: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        for name in (
            "starting_capital",
            "kelly_fraction",
            "maximum_position_fraction",
            "equal_weight_fraction",
            "minimum_position_fraction",
            "fraction_grid_step",
            "currency_quantum",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value <= _ZERO
            ):
                raise CloseEntryAllocationError(
                    f"{name} must be a positive finite Decimal."
                )
        for name in ("kelly_fraction", "maximum_position_fraction", "equal_weight_fraction"):
            if getattr(self, name) > _ONE:
                raise CloseEntryAllocationError(f"{name} must not exceed 1.")
        if self.minimum_position_fraction > self.maximum_position_fraction:
            raise CloseEntryAllocationError(
                "minimum_position_fraction cannot exceed maximum_position_fraction."
            )
        if self.fraction_grid_step > self.maximum_position_fraction:
            raise CloseEntryAllocationError(
                "fraction_grid_step cannot exceed maximum_position_fraction."
            )
        if (
            isinstance(self.maximum_concurrent_positions, bool)
            or not isinstance(self.maximum_concurrent_positions, int)
            or self.maximum_concurrent_positions < 1
        ):
            raise CloseEntryAllocationError(
                "maximum_concurrent_positions must be a positive integer."
            )


@dataclass(frozen=True, slots=True)
class PositionSize:
    status: SizingStatus
    rule: SizingRule
    unconstrained_fraction: Decimal | None
    fraction: Decimal
    expected_log_growth: Decimal | None
    explanation_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    item_id: int
    event_key: str
    horizon_days: int
    entry_at: datetime
    exit_at: datetime
    status: PositionStatus
    sizing_status: SizingStatus
    fraction: Decimal
    stake: Decimal
    equity_at_entry: Decimal
    realized_net_roi: Decimal | None
    profit: Decimal | None


@dataclass(frozen=True, slots=True)
class PortfolioResult:
    rule: SizingRule
    starting_capital: Decimal
    ending_capital: Decimal
    total_return: Decimal
    opened_count: int
    closed_count: int
    profitable_count: int
    support_share: Decimal | None
    skipped_not_sized_count: int
    skipped_no_capital_count: int
    skipped_concurrency_count: int
    outcome_unavailable_count: int
    peak_concurrent_positions: int
    mean_deployed_fraction: Decimal | None
    positions: tuple[PortfolioPosition, ...]
    explanation_codes: tuple[str, ...]
    version: str = CLOSE_ENTRY_ALLOCATION_VERSION


@dataclass(frozen=True, slots=True)
class AllocationComparison:
    """Every arm takes the same candidates in the same order; stake differs."""

    candidate_count: int
    arms: tuple[PortfolioResult, ...]
    explanation_codes: tuple[str, ...]
    version: str = CLOSE_ENTRY_ALLOCATION_VERSION


def size_position(
    *,
    candidate: CloseEntryCandidate,
    rule: SizingRule = "FRACTIONAL_KELLY",
    config: AllocationConfig | None = None,
) -> PositionSize:
    """Choose the fraction of equity to stake on one candidate.

    The Kelly optimum is read off a fixed grid rather than solved, which keeps
    the answer exactly reproducible on a discrete distribution and makes the
    cap a property of the search rather than a clamp applied afterwards. It is
    then scaled by ``kelly_fraction``, because a criterion that is optimal for
    a known distribution is reckless for one built from three prior events.
    """

    cfg = config or AllocationConfig()
    if rule not in SIZING_RULES:
        raise CloseEntryAllocationError("unsupported sizing rule.")
    codes: list[str] = ["close_entry_allocation_research_only_not_a_fill_claim"]

    if not candidate.distribution:
        return PositionSize(
            status="NO_DISTRIBUTION",
            rule=rule,
            unconstrained_fraction=None,
            fraction=_ZERO,
            expected_log_growth=None,
            explanation_codes=tuple(
                [*codes, "close_entry_allocation_no_predictive_distribution"]
            ),
        )
    if not candidate.distribution_supported:
        return PositionSize(
            status="UNSUPPORTED_DISTRIBUTION",
            rule=rule,
            unconstrained_fraction=None,
            fraction=_ZERO,
            expected_log_growth=None,
            explanation_codes=tuple(
                [*codes, "close_entry_allocation_distribution_not_self_supported"]
            ),
        )

    optimum, growth = _kelly_optimum(candidate.distribution, cfg)
    if optimum <= _ZERO:
        return PositionSize(
            status="NO_POSITIVE_EDGE",
            rule=rule,
            unconstrained_fraction=optimum,
            fraction=_ZERO,
            expected_log_growth=growth,
            explanation_codes=tuple(
                [*codes, "close_entry_allocation_no_positive_log_growth"]
            ),
        )

    if rule == "EQUAL_WEIGHT":
        fraction = cfg.equal_weight_fraction
        codes.append("close_entry_allocation_equal_weight_ignores_the_distribution")
    else:
        fraction = min(optimum * cfg.kelly_fraction, cfg.maximum_position_fraction)
        codes.append("close_entry_allocation_fractional_kelly_capped")
    if fraction < cfg.minimum_position_fraction:
        return PositionSize(
            status="NO_POSITIVE_EDGE",
            rule=rule,
            unconstrained_fraction=optimum,
            fraction=_ZERO,
            expected_log_growth=growth,
            explanation_codes=tuple(
                [*codes, "close_entry_allocation_below_minimum_position_size"]
            ),
        )
    return PositionSize(
        status="SIZED",
        rule=rule,
        unconstrained_fraction=optimum,
        fraction=fraction,
        expected_log_growth=growth,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def simulate_close_entry_portfolio(
    *,
    candidates: Iterable[CloseEntryCandidate],
    rule: SizingRule = "FRACTIONAL_KELLY",
    config: AllocationConfig | None = None,
) -> PortfolioResult:
    """Walk the candidates in close order against a finite, recycling account.

    Capital returns to the account only when a position's exit instant has
    passed, so two positions cannot spend the same money, and a candidate that
    arrives when the account is committed is recorded rather than skipped
    silently. That record is the difference between a simulation of this
    account and a simulation of an arbitrarily large one.
    """

    cfg = config or AllocationConfig()
    pool = sorted(
        candidates,
        key=lambda value: (value.entry_at, value.item_id, value.event_key),
    )
    cash = cfg.starting_capital
    # (exit instant, stake, proceeds). An open position is carried at its cost
    # until it closes: valuing it at the proceeds it will eventually return
    # would put the outcome of a position still in flight into the equity that
    # sizes the next one, which is a look-ahead the rest of this package spends
    # its time preventing.
    open_positions: list[tuple[datetime, Decimal, Decimal]] = []
    positions: list[PortfolioPosition] = []
    peak = 0
    deployed: list[Decimal] = []

    def release(now: datetime) -> None:
        nonlocal cash
        remaining: list[tuple[datetime, Decimal, Decimal]] = []
        for exit_at, stake, proceeds in open_positions:
            if exit_at <= now:
                cash += proceeds
            else:
                remaining.append((exit_at, stake, proceeds))
        open_positions[:] = remaining

    for candidate in pool:
        release(candidate.entry_at)
        committed = sum((stake for _, stake, _ in open_positions), _ZERO)
        equity = cash + committed
        size = size_position(candidate=candidate, rule=rule, config=cfg)

        def record(
            status: PositionStatus, stake: Decimal, roi: Decimal | None
        ) -> None:
            positions.append(
                PortfolioPosition(
                    item_id=candidate.item_id,
                    event_key=candidate.event_key,
                    horizon_days=candidate.horizon_days,
                    entry_at=candidate.entry_at,
                    exit_at=candidate.exit_at,
                    status=status,
                    sizing_status=size.status,
                    fraction=size.fraction,
                    stake=stake,
                    equity_at_entry=equity,
                    realized_net_roi=roi,
                    profit=(stake * roi if roi is not None and stake > _ZERO else None),
                )
            )

        if size.status != "SIZED":
            record("SKIPPED_NOT_SIZED", _ZERO, None)
            continue
        if len(open_positions) >= cfg.maximum_concurrent_positions:
            record("SKIPPED_CONCURRENCY_CAP", _ZERO, None)
            continue
        stake = (equity * size.fraction).quantize(cfg.currency_quantum)
        if stake > cash:
            stake = cash.quantize(cfg.currency_quantum)
        if stake < (equity * cfg.minimum_position_fraction).quantize(
            cfg.currency_quantum
        ) or stake <= _ZERO:
            record("SKIPPED_NO_CAPITAL", _ZERO, None)
            continue
        if candidate.realized_net_roi is None:
            record("OUTCOME_UNAVAILABLE", _ZERO, None)
            continue

        cash -= stake
        proceeds = stake * (_ONE + candidate.realized_net_roi)
        open_positions.append((candidate.exit_at, stake, proceeds))
        peak = max(peak, len(open_positions))
        deployed.append(stake / equity if equity > _ZERO else _ZERO)
        record("CLOSED", stake, candidate.realized_net_roi)

    for _, _, proceeds in open_positions:
        cash += proceeds
    open_positions.clear()

    closed = [value for value in positions if value.status == "CLOSED"]
    profitable = sum(
        bool(value.realized_net_roi is not None and value.realized_net_roi > _ZERO)
        for value in closed
    )
    codes = [
        "close_entry_allocation_research_only_not_a_fill_claim",
        "close_entry_allocation_daily_vwap_proxy_would_move_under_a_real_order",
        "close_entry_allocation_same_candidates_in_every_arm",
    ]
    if not pool:
        codes.append("close_entry_allocation_no_candidates")
    if any(value.status == "SKIPPED_NO_CAPITAL" for value in positions):
        codes.append("close_entry_allocation_capital_bound_at_least_once")
    if any(value.status == "SKIPPED_CONCURRENCY_CAP" for value in positions):
        codes.append("close_entry_allocation_concurrency_cap_bound_at_least_once")
    return PortfolioResult(
        rule=rule,
        starting_capital=cfg.starting_capital,
        ending_capital=cash,
        total_return=(
            (cash - cfg.starting_capital) / cfg.starting_capital
            if cfg.starting_capital > _ZERO
            else _ZERO
        ),
        opened_count=len(closed),
        closed_count=len(closed),
        profitable_count=profitable,
        support_share=(
            Decimal(profitable) / Decimal(len(closed)) if closed else None
        ),
        skipped_not_sized_count=sum(
            value.status == "SKIPPED_NOT_SIZED" for value in positions
        ),
        skipped_no_capital_count=sum(
            value.status == "SKIPPED_NO_CAPITAL" for value in positions
        ),
        skipped_concurrency_count=sum(
            value.status == "SKIPPED_CONCURRENCY_CAP" for value in positions
        ),
        outcome_unavailable_count=sum(
            value.status == "OUTCOME_UNAVAILABLE" for value in positions
        ),
        peak_concurrent_positions=peak,
        mean_deployed_fraction=(
            sum(deployed, _ZERO) / Decimal(len(deployed)) if deployed else None
        ),
        positions=tuple(positions),
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def compare_allocation_rules(
    *,
    candidates: Iterable[CloseEntryCandidate],
    config: AllocationConfig | None = None,
) -> AllocationComparison:
    """Run every sizing rule over an identical candidate stream."""

    pool = tuple(candidates)
    arms = tuple(
        simulate_close_entry_portfolio(candidates=pool, rule=rule, config=config)
        for rule in SIZING_RULES
    )
    codes = [
        "close_entry_allocation_comparison_research_only",
        "close_entry_allocation_selection_identical_across_arms",
    ]
    if not pool:
        codes.append("close_entry_allocation_no_candidates")
    return AllocationComparison(
        candidate_count=len(pool),
        arms=arms,
        explanation_codes=tuple(dict.fromkeys(codes)),
    )


def _kelly_optimum(
    distribution: tuple[ReturnAtom, ...], config: AllocationConfig
) -> tuple[Decimal, Decimal | None]:
    """Grid-search the fraction maximizing expected log growth.

    Ties resolve to the smaller fraction, so an indifferent distribution stakes
    less rather than more.
    """

    best_fraction = _ZERO
    best_growth: Decimal | None = None
    fraction = _ZERO
    with localcontext() as context:
        context.prec = _LOG_PRECISION
        while fraction <= config.maximum_position_fraction:
            growth = _ZERO
            for atom in distribution:
                factor = _ONE + fraction * atom.net_roi
                if factor <= _ZERO:
                    growth = None
                    break
                growth += atom.weight_share * factor.ln()
            if growth is not None and (best_growth is None or growth > best_growth):
                best_growth = growth
                best_fraction = fraction
            fraction += config.fraction_grid_step
    return best_fraction, best_growth


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CloseEntryAllocationError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)
