"""Serve the post-event drawdown entry signal alongside the existing scores.

This is deliberately additive. The opportunity ranking keeps running on
``OpportunityScoreV1`` and is not touched: v1 scores market snapshots, the
post-event rule reads trade history, and the two answer different questions.
Swapping the ranking over to ``OpportunityScoreV2`` is a separate decision with
a different blast radius, and nothing here depends on it.

What this does is the annotating half of the rule — publish the reasoning and
let a reader decide — which is the only half the evidence currently supports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.postevent_entry_rule import (
    PostEventEntryConfig,
    PostEventEntrySignal,
    evaluate_postevent_entry,
)
from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import HistoricalTradeBucket, Item

# One event duration before the earliest event plus the entry window is all the
# rule reads, but events are years apart and the baseline is cheap to widen, so
# the query takes a generous span rather than one computed per item.
_HISTORY_LOOKBACK = timedelta(days=400)


class PostEventEntryServiceError(ValueError):
    """Raised when a post-event entry request cannot be served."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class PostEventEntryItemSignal:
    item_id: int
    external_key: str
    item_name: str
    catalog_match_status: str
    matched_catalog_name: str | None
    signal: PostEventEntrySignal | None


async def list_postevent_entry_signals(
    *,
    session: AsyncSession,
    as_of: datetime,
    candidates_only: bool = False,
    item_id: int | None = None,
    include_inactive: bool = False,
    config: PostEventEntryConfig | None = None,
) -> list[PostEventEntryItemSignal]:
    """Evaluate the rule for every catalogued item, newest window first.

    An item the supply-pool catalog does not know is returned with a null
    signal and its match status, rather than omitted: a reader asking why a
    vehicle is missing deserves the answer that nothing is bound to it.
    """

    cfg = config or PostEventEntryConfig()
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise PostEventEntryServiceError(
            "postevent_entry_as_of_not_timezone_aware",
            "as_of must be timezone-aware.",
        )
    as_of = as_of.astimezone(UTC)

    statement = select(Item)
    if item_id is not None:
        statement = statement.where(Item.id == item_id)
    if not include_inactive:
        statement = statement.where(Item.is_active.is_(True))
    items = list((await session.scalars(statement.order_by(Item.id))).all())
    if item_id is not None and not items:
        raise PostEventEntryServiceError(
            "postevent_entry_item_not_found", "The requested item was not found."
        )

    results: list[PostEventEntryItemSignal] = []
    for item in items:
        facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(
            item_id=item.id, item_name=item.name
        )
        if facts.match_status != "MATCHED":
            results.append(
                PostEventEntryItemSignal(
                    item_id=item.id,
                    external_key=item.external_key,
                    item_name=item.name,
                    catalog_match_status=facts.match_status,
                    matched_catalog_name=facts.matched_catalog_name,
                    signal=None,
                )
            )
            continue
        member_keys = {value.event_key for value in facts.memberships}
        events = tuple(
            value for value in facts.events if value.event_key in member_keys
        )
        buckets = await _load_buckets(session, item_id=item.id, as_of=as_of)
        signal = evaluate_postevent_entry(
            item_id=item.id,
            as_of=as_of,
            events=events,
            historical_buckets=buckets,
            config=cfg,
        )
        results.append(
            PostEventEntryItemSignal(
                item_id=item.id,
                external_key=item.external_key,
                item_name=item.name,
                catalog_match_status=facts.match_status,
                matched_catalog_name=facts.matched_catalog_name,
                signal=signal,
            )
        )

    if candidates_only:
        results = [
            value
            for value in results
            if value.signal is not None and value.signal.is_entry_candidate
        ]
    # Freshest window first; everything without one sorts after.
    results.sort(
        key=lambda value: (
            value.signal.days_since_close
            if value.signal is not None and value.signal.days_since_close is not None
            else 1_000_000,
            value.item_id,
        )
    )
    return results


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
