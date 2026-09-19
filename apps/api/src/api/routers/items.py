from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import MarketSnapshot
from api.db.session import get_session
from api.schemas.historical_trades import (
    HistoricalTradeBucketListResponse,
    HistoricalTradeBucketResponse,
)
from api.schemas.items import (
    ItemDetailResponse,
    ItemListResponse,
    MarketItemSummary,
    CatalogOrderBookResponse,
    CatalogPriceResponse,
    ItemSummary,
    CurrentOrderBookContractResponse,
    CurrentOrderBookLevelResponse,
    CurrentOrderBookProvenanceResponse,
    CurrentOrderBookResponse,
    OrderBookObservationResponse,
    SnapshotResponse,
    SnapshotSummary,
    SortField,
    SortOrder,
)
from api.services.historical_trades import (
    HistoricalTradeImportError,
    list_historical_trade_buckets,
)
from api.services.items import (
    ItemDetailData,
    ItemNotFoundError,
    ItemQueryService,
    ItemWithLatestSnapshot,
    OrderBookObservationData,
    SnapshotData,
)
from api.services.current_order_book import (
    STALE_AFTER_SECONDS,
    CurrentOrderBookContractError,
    CurrentOrderBookNotFoundError,
    get_current_order_book,
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


@router.get(
    "/{item_id}/historical-trades",
    response_model=HistoricalTradeBucketListResponse,
)
async def list_item_historical_trades(
    item_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    granularity: Annotated[str, Query(pattern="^(1h|1d)$")] = "1d",
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> HistoricalTradeBucketListResponse:
    from_at = _parse_datetime_filter(from_, "from") if from_ is not None else None
    to_at = _parse_datetime_filter(to, "to") if to is not None else None
    if from_at is not None and to_at is not None and from_at > to_at:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_time_range",
            "from must not be after to.",
        )
    try:
        buckets = await list_historical_trade_buckets(
            session=session,
            item_id=item_id,
            granularity=granularity,
            start_at=from_at,
            end_at=to_at,
            limit=limit,
        )
    except HistoricalTradeImportError as exc:
        raise _business_error(
            status.HTTP_404_NOT_FOUND,
            exc.code,
            exc.message,
        ) from exc
    return HistoricalTradeBucketListResponse(
        item_id=item_id,
        granularity=granularity,
        total=len(buckets),
        buckets=[
            HistoricalTradeBucketResponse(
                id=bucket.id,
                item_id=bucket.item_id,
                granularity=bucket.granularity,
                bucket_start_utc=bucket.bucket_start_utc,
                bucket_duration_seconds=bucket.bucket_duration_seconds,
                vwap_price_raw=bucket.vwap_price_raw,
                price_scale=bucket.price_scale,
                reported_vwap_price=bucket.reported_vwap_price,
                reported_trade_volume=bucket.reported_trade_volume,
                price_semantics=bucket.price_semantics,
                volume_semantics=bucket.volume_semantics,
                source_schema_version=bucket.source_schema_version,
                first_seen_at=bucket.first_seen_at,
                last_seen_at=bucket.last_seen_at,
            )
            for bucket in buckets
        ],
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


@router.get("/{item_id}/order-book", response_model=CurrentOrderBookResponse)
async def get_item_current_order_book(
    item_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentOrderBookResponse:
    try:
        data = await get_current_order_book(session, item_id=item_id)
    except CurrentOrderBookNotFoundError as exc:
        code = str(exc)
        message = (
            "The requested item was not found."
            if code == "item_not_found"
            else "No approved current order-book capture is available for this item."
        )
        raise _business_error(status.HTTP_404_NOT_FOUND, code, message) from exc
    except CurrentOrderBookContractError as exc:
        raise _business_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "price_contract_not_applicable",
            "The latest capture is outside the approved price contract.",
        ) from exc

    model = data.read_model
    levels = [CurrentOrderBookLevelResponse(**level) for level in model["levels"]]
    buy_levels = [level for level in levels if level.side == "BUY"]
    sell_levels = [level for level in levels if level.side == "SELL"]
    best_buy = max(buy_levels, key=lambda level: level.price_raw)
    best_sell = min(sell_levels, key=lambda level: level.price_raw)
    contract = model["contract"]
    capture = model["capture"]
    evidence = model["evidence"]
    return CurrentOrderBookResponse(
        schema_version="web_current_order_book_v1",
        item={
            "id": data.item.id,
            "external_key": data.item.external_key,
            "name": data.item.name,
        },
        captured_at=data.source.captured_at,
        freshness=data.freshness,
        stale_after_seconds=STALE_AFTER_SECONDS,
        best_buy=best_buy,
        best_sell=best_sell,
        spread_display_text=data.spread_display_text,
        contract=CurrentOrderBookContractResponse(
            contract_id=contract["contract_id"],
            contract_version=contract["contract_version"],
            currency_code=contract["currency_code"],
            raw_scale=contract["raw_scale"],
            display_decimal_places=contract["display_decimal_places"],
            evidence_level=contract["evidence_level"],
        ),
        provenance=CurrentOrderBookProvenanceResponse(
            source_type=data.source.source_type,
            source_capture_schema_version=capture["source_capture_schema_version"],
            capture_method=capture["capture_method"],
            review_status=capture["review_status"],
            request_action=capture["request_action"],
            normalized_capture_fingerprint=capture["normalized_capture_fingerprint"],
            source_file_sha256=data.source.source_file_sha256,
            raw_response_sha256_claim=capture["raw_response_sha256_claim"],
            raw_response_hash_verifiable=capture["raw_response_hash_verifiable"],
            read_model_schema_version=model["schema_version"],
            read_model_implementation_version=evidence["read_model_implementation_version"],
        ),
        buy_levels=buy_levels,
        sell_levels=sell_levels,
    )


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


@router.get(
    "/{item_id}/order-book-observations",
    response_model=list[OrderBookObservationResponse],
)
async def list_item_order_book_observations(
    item_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    limit: str = "500",
    order: str = "asc",
) -> list[OrderBookObservationResponse]:
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
        observations = await service.list_order_book_observations(
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
    return [_order_book_observation_response(row) for row in observations]


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


def _item_summary(row: ItemWithLatestSnapshot) -> MarketItemSummary:
    base = _base_item_summary(row)
    order_book = row.current_order_book
    current = None
    if order_book.status == "available":
        assert order_book.captured_at is not None
        assert order_book.freshness is not None
        assert order_book.best_buy is not None
        assert order_book.best_sell is not None
        assert order_book.spread_display_text is not None
        assert order_book.currency_code is not None
        assert order_book.contract_id is not None
        assert order_book.contract_version is not None
        assert order_book.source_type is not None
        assert order_book.review_status is not None
        assert order_book.request_action is not None
        current = CatalogOrderBookResponse(
            schema_version="web_catalog_order_book_v1",
            captured_at=order_book.captured_at,
            freshness=order_book.freshness,
            best_buy=CatalogPriceResponse(**order_book.best_buy.__dict__),
            best_sell=CatalogPriceResponse(**order_book.best_sell.__dict__),
            spread_display_text=order_book.spread_display_text,
            currency_code=order_book.currency_code,
            contract_id=order_book.contract_id,
            contract_version=order_book.contract_version,
            source_type=order_book.source_type,
            review_status=order_book.review_status,
            request_action=order_book.request_action,
        )
    return MarketItemSummary(
        **base.model_dump(),
        current_order_book_status=order_book.status,
        current_order_book=current,
    )


def _base_item_summary(row: ItemWithLatestSnapshot) -> ItemSummary:
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
    summary = _base_item_summary(detail)
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


def _order_book_observation_response(
    observation: OrderBookObservationData,
) -> OrderBookObservationResponse:
    return OrderBookObservationResponse(
        id=observation.id,
        item_id=observation.item_id,
        market_snapshot_id=observation.market_snapshot_id,
        screen_review_import_id=observation.screen_review_import_id,
        observed_at=observation.observed_at,
        best_ask=observation.best_ask,
        best_bid=observation.best_bid,
        observed_bid_quantity=observation.observed_bid_quantity,
        observed_ask_quantity=observation.observed_ask_quantity,
        quantity_semantics=observation.quantity_semantics,
        source_type=observation.source_type,
        source_version=observation.source_version,
        review_status=observation.review_status,
        created_at=observation.created_at,
    )


def _business_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
