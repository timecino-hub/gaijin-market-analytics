from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from collections.abc import Mapping
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Item, MarketSnapshot
from api.schemas.items import SortField, SortOrder


class ItemNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class SnapshotData:
    id: int | None = None
    item_id: int | None = None
    observed_at: datetime | None = None
    best_ask: Decimal | None = None
    best_bid: Decimal | None = None
    ask_count: int | None = None
    bid_count: int | None = None
    estimated_volume: Decimal | None = None
    source_import_job_id: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class ItemWithLatestSnapshot:
    item: Item
    latest_snapshot: SnapshotData | None


@dataclass(frozen=True)
class ItemDetailData(ItemWithLatestSnapshot):
    snapshot_count: int
    first_snapshot_at: datetime | None
    last_snapshot_at: datetime | None


@dataclass(frozen=True)
class ItemListData:
    items: list[ItemWithLatestSnapshot]
    total: int


class ItemQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_items(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None,
        category: str | None,
        rarity: str | None,
        is_active: bool | None,
        sort: SortField,
        order: SortOrder,
    ) -> ItemListData:
        filters = _item_filters(
            search=search,
            category=category,
            rarity=rarity,
            is_active=is_active,
        )
        count_statement = select(func.count(Item.id)).where(*filters)
        total = await self._session.scalar(count_statement)

        latest = _latest_snapshot_subquery()
        statement = (
            select(Item, latest)
            .outerjoin(latest, latest.c.item_id == Item.id)
            .where(*filters)
            .order_by(*_item_order_by(sort, order), Item.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self._session.execute(statement)
        items = [
            ItemWithLatestSnapshot(item=row[0], latest_snapshot=_snapshot_from_row(row._mapping))
            for row in result.all()
        ]
        return ItemListData(items=items, total=total or 0)

    async def get_item_detail(self, item_id: int) -> ItemDetailData:
        latest = _latest_snapshot_subquery()
        aggregates = (
            select(
                MarketSnapshot.item_id.label("item_id"),
                func.count(MarketSnapshot.id).label("snapshot_count"),
                func.min(MarketSnapshot.observed_at).label("first_snapshot_at"),
                func.max(MarketSnapshot.observed_at).label("last_snapshot_at"),
            )
            .where(MarketSnapshot.item_id == item_id)
            .group_by(MarketSnapshot.item_id)
            .subquery()
        )
        statement = (
            select(
                Item,
                latest,
                func.coalesce(aggregates.c.snapshot_count, 0).label("snapshot_count"),
                aggregates.c.first_snapshot_at,
                aggregates.c.last_snapshot_at,
            )
            .outerjoin(latest, latest.c.item_id == Item.id)
            .outerjoin(aggregates, aggregates.c.item_id == Item.id)
            .where(Item.id == item_id)
            .limit(1)
        )
        result = await self._session.execute(statement)
        row = result.one_or_none()
        if row is None:
            raise ItemNotFoundError(f"Item {item_id} was not found.")

        mapping = row._mapping
        return ItemDetailData(
            item=row[0],
            latest_snapshot=_snapshot_from_row(mapping),
            snapshot_count=mapping["snapshot_count"],
            first_snapshot_at=mapping["first_snapshot_at"],
            last_snapshot_at=mapping["last_snapshot_at"],
        )

    async def list_snapshots(
        self,
        *,
        item_id: int,
        from_at: datetime | None,
        to_at: datetime | None,
        limit: int,
        order: Literal["asc", "desc"],
    ) -> list[MarketSnapshot]:
        exists_statement = select(Item.id).where(Item.id == item_id).limit(1)
        if await self._session.scalar(exists_statement) is None:
            raise ItemNotFoundError(f"Item {item_id} was not found.")

        filters = [MarketSnapshot.item_id == item_id]
        if from_at is not None:
            filters.append(MarketSnapshot.observed_at >= from_at)
        if to_at is not None:
            filters.append(MarketSnapshot.observed_at <= to_at)

        sort_direction = (
            MarketSnapshot.observed_at.asc if order == "asc" else MarketSnapshot.observed_at.desc
        )
        id_direction = MarketSnapshot.id.asc if order == "asc" else MarketSnapshot.id.desc
        statement = (
            select(MarketSnapshot)
            .where(*filters)
            .order_by(sort_direction(), id_direction())
            .limit(limit)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())


def _item_filters(
    *,
    search: str | None,
    category: str | None,
    rarity: str | None,
    is_active: bool | None,
) -> list[object]:
    filters: list[object] = []
    if search:
        pattern = f"%{search}%"
        filters.append(or_(Item.name.ilike(pattern), Item.external_key.ilike(pattern)))
    if category:
        filters.append(Item.category == category)
    if rarity:
        filters.append(Item.rarity == rarity)
    if is_active is not None:
        filters.append(Item.is_active.is_(is_active))
    return filters


def _item_order_by(sort: SortField, order: SortOrder) -> list[object]:
    columns = {
        "name": Item.name,
        "created_at": Item.created_at,
        "updated_at": Item.updated_at,
    }
    column = columns[sort]
    direction = column.asc if order == "asc" else column.desc
    return [direction()]


def _latest_snapshot_subquery():
    ranked = (
        select(
            MarketSnapshot.id.label("latest_id"),
            MarketSnapshot.item_id.label("item_id"),
            MarketSnapshot.observed_at.label("latest_observed_at"),
            MarketSnapshot.best_ask.label("latest_best_ask"),
            MarketSnapshot.best_bid.label("latest_best_bid"),
            MarketSnapshot.ask_count.label("latest_ask_count"),
            MarketSnapshot.bid_count.label("latest_bid_count"),
            MarketSnapshot.estimated_volume.label("latest_estimated_volume"),
            func.row_number()
            .over(
                partition_by=MarketSnapshot.item_id,
                order_by=(MarketSnapshot.observed_at.desc(), MarketSnapshot.id.desc()),
            )
            .label("snapshot_rank"),
        )
    ).subquery()
    return select(ranked).where(ranked.c.snapshot_rank == 1).subquery()


def _snapshot_from_row(mapping: Mapping[str, object]) -> SnapshotData | None:
    if mapping["latest_observed_at"] is None:
        return None
    return SnapshotData(
        observed_at=mapping["latest_observed_at"],
        best_ask=mapping["latest_best_ask"],
        best_bid=mapping["latest_best_bid"],
        ask_count=mapping["latest_ask_count"],
        bid_count=mapping["latest_bid_count"],
        estimated_volume=mapping["latest_estimated_volume"],
    )
