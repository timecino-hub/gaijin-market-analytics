"""Serve the general strategy's own track record.

Where ``/vehicle-potential`` reads one vehicle at a time and never pools,
this pools every completed event in the cohort and never separates them. It is
the evidence *behind* the post-event entry signal rather than another signal:
nothing here tells a reader what to buy today.

The ledger is recomputed per request from completed days only, so the numbers
move as events settle instead of being pinned to the day someone last ran a
replay. What cannot be recomputed live — the strict point-in-time walk-forward,
which needs a full replay rather than an outcome ledger — is not attempted here
and is published as a dated finding on the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from gaijin_market_analytics.strategy_evidence import (
    EventOutcome,
    StrategyEvidence,
    StrategyEvidenceConfig,
    evaluate_event_outcomes,
    summarise_strategy_evidence,
)
from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Item
from api.services.historical_evidence import load_historical_evidence


class StrategyEvidenceServiceError(ValueError):
    """Raised when a strategy evidence request cannot be served."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class StrategyEvidenceLedger:
    evidence: StrategyEvidence
    outcomes: tuple[EventOutcome, ...]
    item_names: dict[int, str]
    # Vehicles the supply-pool catalog cannot bind contribute no events at all.
    # Reported so the denominator on the page is the cohort, not the subset.
    unmatched_item_count: int


async def build_strategy_evidence(
    *,
    session: AsyncSession,
    as_of: datetime,
    include_inactive: bool = False,
    config: StrategyEvidenceConfig | None = None,
) -> StrategyEvidenceLedger:
    """Run the strategy over every catalogued event and pool the results."""

    cfg = config or StrategyEvidenceConfig()
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise StrategyEvidenceServiceError(
            "strategy_evidence_as_of_not_timezone_aware",
            "as_of must be timezone-aware.",
        )
    as_of = as_of.astimezone(UTC)

    statement = select(Item)
    if not include_inactive:
        statement = statement.where(Item.is_active.is_(True))
    items = list((await session.scalars(statement.order_by(Item.id))).all())

    outcomes: list[EventOutcome] = []
    names: dict[int, str] = {}
    unmatched = 0
    selected = []
    for item in items:
        names[item.id] = item.name
        facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(
            item_id=item.id, item_name=item.name
        )
        if facts.match_status != "MATCHED":
            unmatched += 1
            continue
        member_keys = {value.event_key for value in facts.memberships}
        events = tuple(
            value for value in facts.events if value.event_key in member_keys
        )
        if not events:
            continue
        selected.append((item.id, events))

    buckets_by_item = await load_historical_evidence(
        session, item_ids=tuple(item_id for item_id, _ in selected), as_of=as_of
    )
    for item_id, events in selected:
        outcomes.extend(
            evaluate_event_outcomes(
                item_id=item_id,
                as_of=as_of,
                events=events,
                historical_buckets=buckets_by_item.get(item_id, ()),
                config=cfg,
            )
        )

    ledger = tuple(outcomes)
    return StrategyEvidenceLedger(
        evidence=summarise_strategy_evidence(as_of=as_of, outcomes=ledger, config=cfg),
        outcomes=ledger,
        item_names=names,
        unmatched_item_count=unmatched,
    )
