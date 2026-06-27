from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import MarketSnapshot
from api.db.session import get_session
from api.schemas.items import (
    ItemDetailResponse,
    ItemListResponse,
    ItemSummary,
    SnapshotResponse,
    SnapshotSummary,
    SortField,
    SortOrder,
)
from api.services.items import (
    ItemDetailData,
    ItemNotFoundError,
    ItemQueryService,
    ItemWithLatestSnapshot,
    SnapshotData,
)

router = APIRouter(prefix="/api/v1/items", tags=["items"])

ALLOWED_SORTS = {"name", "created_at", "updated_at"}
ALLOWED_ORDERS = {"asc", "desc"}


@router.get("", response_model=ItemListResponse)
async def list_items(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: str = "1",
    page_size: str = "50",
    search: str | None = None,
    category: str | None = None,
    rarity: str | None = None,
    is_active: bool | None = None,
    sort: str = "name",
    order: str = "asc",
) -> ItemListResponse:
    parsed_page = _parse_positive_int(page, "page", maximum=None)
    parsed_page_size = _parse_positive_int(page_size, "page_size", maximum=100)
    parsed_sort = _parse_sort(sort)
    parsed_order = _parse_order(order)

    service = ItemQueryService(session)
    result = await service.list_items(
        page=parsed_page,
        page_size=parsed_page_size,
        search=search,
        category=category,
        rarity=rarity,
        is_active=is_active,
        sort=parsed_sort,
        order=parsed_order,
    )
    total_pages = (result.total + parsed_page_size - 1) // parsed_page_size
    return ItemListResponse(
        items=[_item_summary(row) for row in result.items],
        page=parsed_page,
        page_size=parsed_page_size,
        total=result.total,
        total_pages=total_pages,
    )


@router.get("/{item_id}", response_model=ItemDetailResponse)
async def get_item(
    item_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ItemDetailResponse:
    service = ItemQueryService(session)
    try:
        detail = await service.get_item_detail(item_id)
    except ItemNotFoundError as exc:
        raise _business_error(
            status.HTTP_404_NOT_FOUND,
            "item_not_found",
            "The requested item was not found.",
        ) from exc
    return _item_detail(detail)


@router.get("/{item_id}/snapshots", response_model=list[SnapshotResponse])
async def list_item_snapshots(
    item_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    limit: str = "500",
    order: str = "asc",
) -> list[SnapshotResponse]:
    from_at = _parse_datetime_filter(from_, "from") if from_ is not None else None
    to_at = _parse_datetime_filter(to, "to") if to is not None else None
    if from_at is not None and to_at is not None and from_at > to_at:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_time_range",
            "from must not be later than to.",
        )

    parsed_limit = _parse_positive_int(limit, "limit", maximum=2000)
    parsed_order = _parse_order(order)
    service = ItemQueryService(session)
    try:
        snapshots = await service.list_snapshots(
            item_id=item_id,
            from_at=from_at,
            to_at=to_at,
            limit=parsed_limit,
            order=parsed_order,
        )
    except ItemNotFoundError as exc:
        raise _business_error(
            status.HTTP_404_NOT_FOUND,
            "item_not_found",
            "The requested item was not found.",
        ) from exc
    return [_snapshot_response(snapshot) for snapshot in snapshots]


def _parse_positive_int(value: str, field: str, *, maximum: int | None) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_pagination",
            f"{field} must be an integer.",
        ) from exc
    if parsed < 1 or (maximum is not None and parsed > maximum):
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_pagination",
            f"{field} is outside the allowed range.",
        )
    return parsed


def _parse_sort(value: str) -> SortField:
    if value not in ALLOWED_SORTS:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_sort",
            "sort must be one of: name, created_at, updated_at.",
        )
    return value  # type: ignore[return-value]


def _parse_order(value: str) -> SortOrder:
    if value not in ALLOWED_ORDERS:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_sort",
            "order must be asc or desc.",
        )
    return value  # type: ignore[return-value]


def _parse_datetime_filter(value: str | None, field: str) -> datetime:
    if value is None or value == "":
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_time_range",
            f"{field} must be an ISO-8601 datetime with timezone.",
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_time_range",
            f"{field} must be an ISO-8601 datetime with timezone.",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_time_range",
            f"{field} must include a timezone.",
        )
    return parsed.astimezone(UTC)


def _item_summary(row: ItemWithLatestSnapshot) -> ItemSummary:
    item = row.item
    return ItemSummary(
        id=item.id,
        external_key=item.external_key,
        name=item.name,
        category=item.category,
        rarity=item.rarity,
        is_active=item.is_active,
        created_at=item.created_at,
        updated_at=item.updated_at,
        latest_snapshot=_snapshot_summary(row.latest_snapshot),
    )


def _item_detail(detail: ItemDetailData) -> ItemDetailResponse:
    summary = _item_summary(detail)
    return ItemDetailResponse(
        **summary.model_dump(),
        snapshot_count=detail.snapshot_count,
        first_snapshot_at=detail.first_snapshot_at,
        last_snapshot_at=detail.last_snapshot_at,
    )


def _snapshot_summary(snapshot: SnapshotData | None) -> SnapshotSummary | None:
    if snapshot is None:
        return None
    return SnapshotSummary(
        observed_at=snapshot.observed_at,
        best_ask=snapshot.best_ask,
        best_bid=snapshot.best_bid,
        ask_count=snapshot.ask_count,
        bid_count=snapshot.bid_count,
        estimated_volume=snapshot.estimated_volume,
    )


def _snapshot_response(snapshot: MarketSnapshot) -> SnapshotResponse:
    return SnapshotResponse(
        id=snapshot.id,
        item_id=snapshot.item_id,
        observed_at=snapshot.observed_at,
        best_ask=snapshot.best_ask,
        best_bid=snapshot.best_bid,
        ask_count=snapshot.ask_count,
        bid_count=snapshot.bid_count,
        estimated_volume=snapshot.estimated_volume,
        source_import_job_id=snapshot.source_import_job_id,
        created_at=snapshot.created_at,
    )


def _business_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
