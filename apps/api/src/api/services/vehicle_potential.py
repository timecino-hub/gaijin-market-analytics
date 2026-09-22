"""Serve each vehicle's own potential reading.

This replaces nothing. ``OpportunityScoreV1`` still answers its own question on
``market_snapshots``; this reads ``historical_trade_buckets``, which is where
this deployment's evidence actually lives — five snapshot rows against fifty
thousand trading days.

The catalog is consulted for the event record only. A vehicle the catalog does
not know still gets a full price reading, because an unbound vehicle is not a
vehicle without a price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gaijin_market_analytics.historical_trades import HistoricalTradeBucketEvidence
from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG
from gaijin_market_analytics.vehicle_potential import (
    VehiclePotential,
    VehiclePotentialConfig,
    evaluate_vehicle_potential,
    rank_vehicle_potential,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import HistoricalTradeBucket, Item

# The reading needs the configured history window plus enough room before the
# oldest catalogued event to establish its pre-event baseline. Events are years
# apart and the query is cheap, so this takes a generous span rather than one
# computed per vehicle.
_HISTORY_LOOKBACK = timedelta(days=3650)


class VehiclePotentialServiceError(ValueError):
    """Raised when a vehicle potential request cannot be served."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class VehiclePotentialItem:
    item_id: int
    external_key: str
    item_name: str
    category: str | None
    catalog_match_status: str
    matched_catalog_name: str | None
    reading: VehiclePotential


async def list_vehicle_potential(
    *,
    session: AsyncSession,
    as_of: datetime,
    item_id: int | None = None,
    include_inactive: bool = False,
    ranked_only: bool = False,
    config: VehiclePotentialConfig | None = None,
) -> list[VehiclePotentialItem]:
    """Read every vehicle, cheapest against its own history first."""

    cfg = config or VehiclePotentialConfig()
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise VehiclePotentialServiceError(
            "vehicle_potential_as_of_not_timezone_aware",
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
        raise VehiclePotentialServiceError(
            "vehicle_potential_item_not_found", "The requested item was not found."
        )

    results: list[VehiclePotentialItem] = []
    for item in items:
        facts = DEFAULT_SUPPLY_POOL_CATALOG.bind_item(
            item_id=item.id, item_name=item.name
        )
        events = ()
        if facts.match_status == "MATCHED":
            member_keys = {value.event_key for value in facts.memberships}
            events = tuple(
                value for value in facts.events if value.event_key in member_keys
            )
        buckets = await _load_buckets(session, item_id=item.id, as_of=as_of)
        reading = evaluate_vehicle_potential(
            item_id=item.id,
            as_of=as_of,
            events=events,
            historical_buckets=buckets,
            config=cfg,
        )
        results.append(
            VehiclePotentialItem(
                item_id=item.id,
                external_key=item.external_key,
                item_name=item.name,
                category=item.category,
                catalog_match_status=facts.match_status,
                matched_catalog_name=facts.matched_catalog_name,
                reading=reading,
            )
        )

    if ranked_only:
        results = [value for value in results if value.reading.state == "RANKED"]

    order = {
        reading.item_id: index
        for index, reading in enumerate(
            rank_vehicle_potential(value.reading for value in results)
        )
    }
    results.sort(key=lambda value: order[value.item_id])
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
