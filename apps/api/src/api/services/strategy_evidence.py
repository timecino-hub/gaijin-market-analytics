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
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
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

from api.db.models import HistoricalTradeBucket, Item

# Events in this catalog reach back to 2018 and each one needs its own
# pre-event window before it, so the ledger reads the whole series rather than
# a rolling window.
_HISTORY_LOOKBACK = timedelta(days=3650)


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
        buckets = await _load_buckets(session, item_id=item.id, as_of=as_of)
        outcomes.extend(
            evaluate_event_outcomes(
                item_id=item.id,
                as_of=as_of,
                events=events,
                historical_buckets=buckets,
                config=cfg,
            )
        )

    ledger = tuple(outcomes)
    return StrategyEvidenceLedger(
        evidence=summarise_strategy_evidence(
            as_of=as_of, outcomes=ledger, config=cfg
        ),
        outcomes=ledger,
        item_names=names,
        unmatched_item_count=unmatched,
    )


async def _load_buckets(
    session: AsyncSession, *, item_id: int, as_of: datetime
) -> tuple[HistoricalTradeBucketEvidence, ...]:
    statement = (
        select(HistoricalTradeBucket)
        .where(
            HistoricalTradeBucket.item_id == item_id,
            HistoricalTradeBucket.bucket_start_utc >= as_of - _HISTORY_LOOKBACK,
            HistoricalTradeBucket.bucket_start_utc <= as_of,
        )
        .order_by(HistoricalTradeBucket.bucket_start_utc.asc())
    )
    rows = list((await session.scalars(statement)).all())
    return tuple(
        HistoricalTradeBucketEvidence(
            item_id=row.item_id,
            granularity=row.granularity,  # type: ignore[arg-type]
            bucket_start_utc=_utc(row.bucket_start_utc),
            bucket_duration=timedelta(seconds=row.bucket_duration_seconds),
            vwap_price=Decimal(row.reported_vwap_price),
            reported_trade_volume=row.reported_trade_volume,
        )
        for row in rows
    )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
