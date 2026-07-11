from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import unquote, urlsplit, urlunsplit

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import HistoricalTradeBucket, HistoricalTradeImport, Item
from api.schemas.historical_trades import (
    PRICE_SCALE,
    HistoricalTradeImportRequest,
    HistoricalTradePointInput,
)


PRICE_SEMANTICS = "bucket_volume_weighted_average_trade_price"
VOLUME_SEMANTICS = "reported_trade_volume_unknown_unit"
SUPPORTED_MARKET_ID = "1067"


class HistoricalTradeImportError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class HistoricalTradeImportResult:
    import_record: HistoricalTradeImport
    item: Item
    deduplicated: bool
    inserted_count: int
    updated_count: int
    unchanged_count: int


async def import_historical_trades(
    *,
    session: AsyncSession,
    request: HistoricalTradeImportRequest,
    pairing_id: str,
    extension_version: str | None,
) -> HistoricalTradeImportResult:
    source_url_safe, source_segment = _sanitize_market_source_url(request.source_url)
    item_key = unquote(request.item_key).strip()
    if not item_key or item_key != source_segment:
        raise HistoricalTradeImportError(
            "historical_trade_item_mismatch",
            "The item key does not match the current Gaijin Market page.",
        )

    item = await _resolve_existing_item(session, item_key)
    series_hash = _series_sha256(request)
    existing = await session.scalar(
        select(HistoricalTradeImport).where(
            HistoricalTradeImport.item_id == item.id,
            HistoricalTradeImport.source_series_sha256 == series_hash,
        )
    )
    if existing is not None:
        return HistoricalTradeImportResult(existing, item, True, 0, 0, 0)

    overlap_days, mismatch_days = _overlap_consistency(request)
    imported_at = datetime.now(UTC)
    import_values = {
        "item_id": item.id,
        "pairing_id": pairing_id,
        "source_url_safe": source_url_safe,
        "source_schema_version": request.schema_version,
        "extension_version": extension_version,
        "source_captured_at": request.captured_at,
        "source_series_sha256": series_hash,
        "point_count_1h": len(request.series.one_hour),
        "point_count_1d": len(request.series.one_day),
        "inserted_count": 0,
        "updated_count": 0,
        "unchanged_count": 0,
        "overlap_day_count": overlap_days,
        "overlap_mismatch_count": mismatch_days,
        "imported_at": imported_at,
    }
    inserted_import_id = await session.scalar(
        pg_insert(HistoricalTradeImport)
        .values(**import_values)
        .on_conflict_do_nothing(
            index_elements=[
                HistoricalTradeImport.item_id,
                HistoricalTradeImport.source_series_sha256,
            ]
        )
        .returning(HistoricalTradeImport.id)
    )
    if inserted_import_id is None:
        existing = await session.scalar(
            select(HistoricalTradeImport).where(
                HistoricalTradeImport.item_id == item.id,
                HistoricalTradeImport.source_series_sha256 == series_hash,
            )
        )
        if existing is None:
            raise HistoricalTradeImportError(
                "historical_trade_import_conflict",
                "A concurrent history import could not be resolved.",
            )
        return HistoricalTradeImportResult(existing, item, True, 0, 0, 0)

    import_record = await session.get(HistoricalTradeImport, inserted_import_id)
    if import_record is None:
        raise HistoricalTradeImportError(
            "historical_trade_import_failed",
            "Historical trade import audit could not be created.",
        )

    inserted = updated = unchanged = 0
    for granularity, duration, points in (
        ("1h", 3600, request.series.one_hour),
        ("1d", 86400, request.series.one_day),
    ):
        timestamps = [datetime.fromtimestamp(point.unix_seconds, UTC) for point in points]
        existing_by_start: dict[datetime, HistoricalTradeBucket] = {}
        for offset in range(0, len(timestamps), 5000):
            chunk = timestamps[offset : offset + 5000]
            if not chunk:
                continue
            rows = await session.scalars(
                select(HistoricalTradeBucket).where(
                    HistoricalTradeBucket.item_id == item.id,
                    HistoricalTradeBucket.granularity == granularity,
                    HistoricalTradeBucket.bucket_start_utc.in_(chunk),
                )
            )
            existing_by_start.update({row.bucket_start_utc: row for row in rows.all()})

        for point in points:
            bucket_start = datetime.fromtimestamp(point.unix_seconds, UTC)
            existing_bucket = existing_by_start.get(bucket_start)
            price = Decimal(point.price_raw) / Decimal(PRICE_SCALE)
            if existing_bucket is None:
                session.add(
                    HistoricalTradeBucket(
                        item_id=item.id,
                        source_import_id=import_record.id,
                        granularity=granularity,
                        bucket_start_utc=bucket_start,
                        bucket_duration_seconds=duration,
                        vwap_price_raw=point.price_raw,
                        price_scale=PRICE_SCALE,
                        reported_vwap_price=price,
                        reported_trade_volume=point.reported_trade_volume,
                        price_semantics=PRICE_SEMANTICS,
                        volume_semantics=VOLUME_SEMANTICS,
                        source_schema_version=request.schema_version,
                        first_seen_at=imported_at,
                        last_seen_at=imported_at,
                    )
                )
                inserted += 1
                continue

            same = (
                existing_bucket.vwap_price_raw == point.price_raw
                and existing_bucket.reported_trade_volume == point.reported_trade_volume
                and existing_bucket.bucket_duration_seconds == duration
            )
            existing_bucket.last_seen_at = imported_at
            existing_bucket.source_import_id = import_record.id
            existing_bucket.source_schema_version = request.schema_version
            if same:
                unchanged += 1
            else:
                existing_bucket.vwap_price_raw = point.price_raw
                existing_bucket.reported_vwap_price = price
                existing_bucket.reported_trade_volume = point.reported_trade_volume
                existing_bucket.bucket_duration_seconds = duration
                updated += 1

    import_record.inserted_count = inserted
    import_record.updated_count = updated
    import_record.unchanged_count = unchanged
    await session.commit()
    await session.refresh(import_record)
    return HistoricalTradeImportResult(import_record, item, False, inserted, updated, unchanged)


async def list_historical_trade_buckets(
    *,
    session: AsyncSession,
    item_id: int,
    granularity: str,
    start_at: datetime | None,
    end_at: datetime | None,
    limit: int,
) -> list[HistoricalTradeBucket]:
    item_exists = await session.scalar(select(Item.id).where(Item.id == item_id))
    if item_exists is None:
        raise HistoricalTradeImportError("item_not_found", "The requested item was not found.")
    statement: Select[tuple[HistoricalTradeBucket]] = select(HistoricalTradeBucket).where(
        HistoricalTradeBucket.item_id == item_id,
        HistoricalTradeBucket.granularity == granularity,
    )
    if start_at is not None:
        statement = statement.where(HistoricalTradeBucket.bucket_start_utc >= start_at)
    if end_at is not None:
        statement = statement.where(HistoricalTradeBucket.bucket_start_utc <= end_at)
    statement = statement.order_by(HistoricalTradeBucket.bucket_start_utc.asc()).limit(limit)
    return list((await session.scalars(statement)).all())


async def _resolve_existing_item(session: AsyncSession, item_key: str) -> Item:
    item = await session.scalar(select(Item).where(Item.external_key == item_key).limit(1))
    if item is None:
        item = await session.scalar(select(Item).where(Item.name == item_key).limit(1))
    if item is None:
        raise HistoricalTradeImportError(
            "historical_trade_item_not_found",
            "The history page item does not match an existing database item.",
        )
    return item


def _sanitize_market_source_url(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(value.strip())
    except ValueError as exc:
        raise HistoricalTradeImportError(
            "historical_trade_source_url_invalid", "Source URL is invalid."
        ) from exc
    if parsed.scheme != "https" or parsed.hostname != "trade.gaijin.net":
        raise HistoricalTradeImportError(
            "historical_trade_source_url_invalid",
            "History imports must originate from a Gaijin Market item page.",
        )
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 3 or parts[0] != "market" or parts[1] != SUPPORTED_MARKET_ID:
        raise HistoricalTradeImportError(
            "historical_trade_source_url_invalid",
            "History imports must originate from a supported item page.",
        )
    segment = unquote(parts[2]).strip()
    safe = urlunsplit(("https", "trade.gaijin.net", parsed.path.rstrip("/"), "", ""))
    return safe, segment


def _series_sha256(request: HistoricalTradeImportRequest) -> str:
    payload = {
        "schema_version": request.schema_version,
        "item_key": unquote(request.item_key).strip(),
        "series": {
            "1h": [_wire_point(point) for point in request.series.one_hour],
            "1d": [_wire_point(point) for point in request.series.one_day],
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _wire_point(point: HistoricalTradePointInput) -> list[int]:
    return [point.unix_seconds, point.price_raw, point.reported_trade_volume]


def _overlap_consistency(request: HistoricalTradeImportRequest) -> tuple[int, int]:
    hourly_by_day: dict[int, list[HistoricalTradePointInput]] = defaultdict(list)
    for point in request.series.one_hour:
        hourly_by_day[point.unix_seconds - (point.unix_seconds % 86400)].append(point)
    daily = {point.unix_seconds: point for point in request.series.one_day}
    first_hour_timestamp = (
        request.series.one_hour[0].unix_seconds if request.series.one_hour else None
    )
    first_hour_day = (
        first_hour_timestamp - (first_hour_timestamp % 86400)
        if first_hour_timestamp is not None
        else None
    )
    overlap = mismatch = 0
    for day_start, hourly in hourly_by_day.items():
        daily_point = daily.get(day_start)
        if daily_point is None:
            continue
        # The hourly series is a rolling window. Its leading UTC day can be
        # truncated, so comparing that partial sum with the full daily bucket
        # would create a false mismatch. Interior days remain comparable even
        # when zero-trade hours are omitted from the wire response.
        if day_start == first_hour_day and first_hour_timestamp != day_start:
            continue
        overlap += 1
        volume = sum(point.reported_trade_volume for point in hourly)
        weighted = sum(point.price_raw * point.reported_trade_volume for point in hourly)
        expected_raw = weighted / volume
        if (
            volume != daily_point.reported_trade_volume
            or abs(expected_raw - daily_point.price_raw) > 2
        ):
            mismatch += 1
    return overlap, mismatch
