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
from datetime import UTC, datetime

from gaijin_market_analytics.supply_pool_catalog_v1 import DEFAULT_SUPPLY_POOL_CATALOG
from gaijin_market_analytics.vehicle_potential import (
    VehiclePotential,
    VehiclePotentialConfig,
    evaluate_vehicle_potential,
    rank_vehicle_potential,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Item
from api.services.historical_evidence import load_historical_evidence


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

    buckets_by_item = await load_historical_evidence(
        session, item_ids=tuple(item.id for item in items), as_of=as_of
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
        reading = evaluate_vehicle_potential(
            item_id=item.id,
            as_of=as_of,
            events=events,
            historical_buckets=buckets_by_item.get(item.id, ()),
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
