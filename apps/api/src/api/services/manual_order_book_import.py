from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import (
    ImportJob,
    Item,
    ManualOrderBookImport,
    ManualOrderBookLevel,
)
from api.importers.manual_order_book_json import (
    ManualOrderBookCapture,
    confirm_pending_manual_order_book_json,
    parse_manual_order_book_json,
)
from api.services.csv_import import advisory_lock_key_for_import


MANUAL_RESPONSE_JSON_SOURCE_TYPE = "manual_response_json"
ORDER_BOOK_PRICE_SEMANTICS = "gaijin_market_response_price_raw_unscaled"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ManualOrderBookImportError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ManualOrderBookImportInspection:
    database_item_id: int
    decoded_external_key: str
    existing_import_id: int | None
    would_create: bool
    capture_id: str
    normalized_capture_fingerprint: str
    buy_level_count: int
    sell_level_count: int


@dataclass(frozen=True)
class ManualOrderBookImportResult:
    database_item_id: int
    import_job_id: int
    manual_order_book_import_id: int
    imported_at: datetime
    created: bool
    capture_id: str
    normalized_capture_fingerprint: str
    buy_level_count: int
    sell_level_count: int


@dataclass(frozen=True)
class _BoundManualOrderBookDocument:
    capture: ManualOrderBookCapture
    capture_payload: dict[str, Any]
    source_file_sha256: str


async def inspect_manual_order_book_import(
    *,
    session: AsyncSession,
    content: bytes,
) -> ManualOrderBookImportInspection:
    document = _bind_document(content)
    return await _inspect_bound_document(session=session, document=document)


async def inspect_pending_manual_order_book_import(
    *,
    session: AsyncSession,
    content: bytes,
) -> ManualOrderBookImportInspection:
    document = _bind_promoted_pending_document(content)
    return await _inspect_bound_document(session=session, document=document)


async def _inspect_bound_document(
    *,
    session: AsyncSession,
    document: _BoundManualOrderBookDocument,
) -> ManualOrderBookImportInspection:
    capture = document.capture
    source_file_sha256 = document.source_file_sha256
    item = await _resolve_existing_item(session, capture.page.decoded_external_key)
    existing = await _find_existing_import(
        session,
        capture=capture,
        source_file_sha256=source_file_sha256,
    )
    if existing is not None:
        _validate_existing_identity(
            existing,
            capture=capture,
            source_file_sha256=source_file_sha256,
            item_id=item.id,
        )
    return ManualOrderBookImportInspection(
        database_item_id=item.id,
        decoded_external_key=item.external_key,
        existing_import_id=None if existing is None else existing.id,
        would_create=existing is None,
        capture_id=capture.capture_id,
        normalized_capture_fingerprint=(
            capture.source.normalized_capture_fingerprint
        ),
        buy_level_count=capture.order_book.buy_level_count,
        sell_level_count=capture.order_book.sell_level_count,
    )


async def import_manual_order_book(
    *,
    session: AsyncSession,
    content: bytes,
    source_filename: str,
) -> ManualOrderBookImportResult:
    document = _bind_document(content)
    return await _import_bound_document(
        session=session,
        document=document,
        source_filename=source_filename,
    )


async def import_pending_manual_order_book(
    *,
    session: AsyncSession,
    content: bytes,
    source_filename: str,
) -> ManualOrderBookImportResult:
    document = _bind_promoted_pending_document(content)
    return await _import_bound_document(
        session=session,
        document=document,
        source_filename=source_filename,
    )


async def _import_bound_document(
    *,
    session: AsyncSession,
    document: _BoundManualOrderBookDocument,
    source_filename: str,
) -> ManualOrderBookImportResult:
    capture = document.capture
    capture_payload = document.capture_payload
    source_file_sha256 = document.source_file_sha256
    filename = _safe_filename(source_filename)
    total_levels = capture.order_book.buy_level_count + capture.order_book.sell_level_count

    try:
        async with session.begin():
            await _acquire_identity_locks(
                session,
                capture=capture,
                source_file_sha256=source_file_sha256,
            )
            item = await _resolve_existing_item(
                session,
                capture.page.decoded_external_key,
            )
            existing = await _find_existing_import(
                session,
                capture=capture,
                source_file_sha256=source_file_sha256,
            )
            if existing is not None:
                _validate_existing_identity(
                    existing,
                    capture=capture,
                    source_file_sha256=source_file_sha256,
                    item_id=item.id,
                )
                duplicate_job = await _create_duplicate_job(
                    session,
                    filename=filename,
                    checksum=source_file_sha256,
                    existing=existing,
                    row_count=total_levels,
                )
                return ManualOrderBookImportResult(
                    database_item_id=existing.item_id,
                    import_job_id=duplicate_job.id,
                    manual_order_book_import_id=existing.id,
                    imported_at=existing.imported_at,
                    created=False,
                    capture_id=capture.capture_id,
                    normalized_capture_fingerprint=(
                        capture.source.normalized_capture_fingerprint
                    ),
                    buy_level_count=capture.order_book.buy_level_count,
                    sell_level_count=capture.order_book.sell_level_count,
                )

            started_at = datetime.now(UTC)
            job = ImportJob(
                source_type=MANUAL_RESPONSE_JSON_SOURCE_TYPE,
                filename=filename,
                checksum=source_file_sha256,
                status="processing",
                started_at=started_at,
                row_count=total_levels,
                valid_row_count=0,
                invalid_row_count=0,
                error_report={"errors": [], "warnings": []},
            )
            session.add(job)
            await session.flush()

            imported_at = datetime.now(UTC)
            imported = ManualOrderBookImport(
                import_job_id=job.id,
                item_id=item.id,
                capture_id=capture.capture_id,
                source_schema_version=capture.schema_version,
                source_type=MANUAL_RESPONSE_JSON_SOURCE_TYPE,
                capture_method=capture.source.capture_method,
                source_filename=filename,
                source_file_sha256=source_file_sha256,
                raw_response_sha256=capture.source.raw_response_sha256,
                normalized_capture_fingerprint=(
                    capture.source.normalized_capture_fingerprint
                ),
                captured_at=_captured_at(capture.captured_at),
                timezone_offset_minutes=capture.timezone_offset_minutes,
                page_origin=capture.page.origin,
                page_path=capture.page.path,
                literal_external_key=capture.page.literal_external_key,
                decoded_external_key=capture.page.decoded_external_key,
                request_method=capture.request.method,
                request_origin=capture.request.origin,
                request_path=capture.request.path,
                review_status=capture.review.status,
                price_semantics=ORDER_BOOK_PRICE_SEMANTICS,
                buy_level_count=capture.order_book.buy_level_count,
                sell_level_count=capture.order_book.sell_level_count,
                buy_depth=capture.order_book.reported_depth.buy,
                sell_depth=capture.order_book.reported_depth.sell,
                best_buy_price_raw=capture.order_book.best_buy.price_raw,
                best_buy_quantity=capture.order_book.best_buy.quantity,
                best_sell_price_raw=capture.order_book.best_sell.price_raw,
                best_sell_quantity=capture.order_book.best_sell.quantity,
                capture_payload=capture_payload,
                imported_at=imported_at,
            )
            session.add(imported)
            await session.flush()
            await _persist_levels(session, imported=imported, capture=capture)

            job.status = "completed"
            job.finished_at = imported_at
            job.valid_row_count = total_levels
            job.error_report = {
                "errors": [],
                "warnings": [],
                "manual_order_book_import_id": imported.id,
            }
            await session.flush()

            return ManualOrderBookImportResult(
                database_item_id=item.id,
                import_job_id=job.id,
                manual_order_book_import_id=imported.id,
                imported_at=imported_at,
                created=True,
                capture_id=capture.capture_id,
                normalized_capture_fingerprint=(
                    capture.source.normalized_capture_fingerprint
                ),
                buy_level_count=capture.order_book.buy_level_count,
                sell_level_count=capture.order_book.sell_level_count,
            )
    except ManualOrderBookImportError as exc:
        await _record_failed_job(
            session,
            filename=filename,
            checksum=source_file_sha256,
            error_code=exc.code,
            row_count=total_levels,
        )
        raise
    except SQLAlchemyError as exc:
        await session.rollback()
        try:
            await _record_failed_job(
                session,
                filename=filename,
                checksum=source_file_sha256,
                error_code="database_error",
                row_count=total_levels,
            )
        except SQLAlchemyError:
            pass
        raise ManualOrderBookImportError(
            "database_error",
            "The manual order-book import could not be completed.",
        ) from exc


async def record_invalid_manual_order_book_attempt(
    *,
    session: AsyncSession,
    source_filename: str,
    source_file_sha256: str,
    error_code: str,
) -> int:
    filename = _safe_filename(source_filename)
    _validate_source_file_sha256(source_file_sha256)
    job = await _record_failed_job(
        session,
        filename=filename,
        checksum=source_file_sha256,
        error_code=error_code,
        row_count=0,
    )
    return job.id


async def _resolve_existing_item(session: AsyncSession, external_key: str) -> Item:
    item = await session.scalar(
        select(Item).where(Item.external_key == external_key).limit(1)
    )
    if item is None:
        raise ManualOrderBookImportError(
            "existing_item_required",
            "Import requires an existing item with the exact decoded external key.",
        )
    return item


async def _find_existing_import(
    session: AsyncSession,
    *,
    capture: ManualOrderBookCapture,
    source_file_sha256: str,
) -> ManualOrderBookImport | None:
    rows = list(
        (
            await session.scalars(
                select(ManualOrderBookImport)
                .where(
                    or_(
                        ManualOrderBookImport.capture_id == capture.capture_id,
                        ManualOrderBookImport.normalized_capture_fingerprint
                        == capture.source.normalized_capture_fingerprint,
                        ManualOrderBookImport.source_file_sha256 == source_file_sha256,
                    )
                )
                .order_by(ManualOrderBookImport.id.asc())
                .limit(3)
            )
        ).all()
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise ManualOrderBookImportError(
            "import_identity_conflict",
            "Capture identifiers resolve to different existing imports.",
        )
    return rows[0]


def _validate_existing_identity(
    existing: ManualOrderBookImport,
    *,
    capture: ManualOrderBookCapture,
    source_file_sha256: str,
    item_id: int,
) -> None:
    if existing.capture_id == capture.capture_id and (
        existing.normalized_capture_fingerprint
        != capture.source.normalized_capture_fingerprint
    ):
        raise ManualOrderBookImportError(
            "capture_id_conflict",
            "The capture id already exists with different validated content.",
        )
    if (
        existing.normalized_capture_fingerprint
        == capture.source.normalized_capture_fingerprint
        and existing.capture_id != capture.capture_id
    ):
        raise ManualOrderBookImportError(
            "capture_fingerprint_conflict",
            "The capture fingerprint already exists with a different capture id.",
        )
    if existing.source_file_sha256 == source_file_sha256 and (
        existing.normalized_capture_fingerprint
        != capture.source.normalized_capture_fingerprint
    ):
        raise ManualOrderBookImportError(
            "source_file_checksum_conflict",
            "The source file checksum already exists with different validated content.",
        )
    if existing.item_id != item_id:
        raise ManualOrderBookImportError(
            "capture_item_conflict",
            "The capture already belongs to a different database item.",
        )


async def _acquire_identity_locks(
    session: AsyncSession,
    *,
    capture: ManualOrderBookCapture,
    source_file_sha256: str,
) -> None:
    identities = sorted(
        {
            f"capture:{capture.capture_id}",
            f"fingerprint:{capture.source.normalized_capture_fingerprint}",
            f"file:{source_file_sha256}",
        }
    )
    for identity in identities:
        key = advisory_lock_key_for_import(MANUAL_RESPONSE_JSON_SOURCE_TYPE, identity)
        await session.execute(select(func.pg_advisory_xact_lock(key)))


async def _persist_levels(
    session: AsyncSession,
    *,
    imported: ManualOrderBookImport,
    capture: ManualOrderBookCapture,
) -> None:
    levels = [
        ManualOrderBookLevel(
            source_import_id=imported.id,
            side=side,
            level_index=index,
            price_raw=level.price_raw,
            quantity=level.quantity,
        )
        for side, source_levels in (
            ("BUY", capture.order_book.buy_levels),
            ("SELL", capture.order_book.sell_levels),
        )
        for index, level in enumerate(source_levels)
    ]
    session.add_all(levels)
    await session.flush()


async def _create_duplicate_job(
    session: AsyncSession,
    *,
    filename: str,
    checksum: str,
    existing: ManualOrderBookImport,
    row_count: int,
) -> ImportJob:
    now = datetime.now(UTC)
    job = ImportJob(
        source_type=MANUAL_RESPONSE_JSON_SOURCE_TYPE,
        filename=filename,
        checksum=checksum,
        status="duplicate",
        started_at=now,
        finished_at=now,
        row_count=row_count,
        valid_row_count=0,
        invalid_row_count=0,
        error_report={
            "errors": [],
            "warnings": [],
            "duplicate_of_job_id": existing.import_job_id,
            "manual_order_book_import_id": existing.id,
        },
    )
    session.add(job)
    await session.flush()
    return job


async def _record_failed_job(
    session: AsyncSession,
    *,
    filename: str,
    checksum: str,
    error_code: str,
    row_count: int,
) -> ImportJob:
    now = datetime.now(UTC)
    async with session.begin():
        job = ImportJob(
            source_type=MANUAL_RESPONSE_JSON_SOURCE_TYPE,
            filename=filename,
            checksum=checksum,
            status="failed",
            started_at=now,
            finished_at=now,
            row_count=row_count,
            valid_row_count=0,
            invalid_row_count=row_count,
            error_report={
                "errors": [{"field": "file", "error_code": error_code}],
                "warnings": [],
            },
        )
        session.add(job)
        await session.flush()
    return job


def _captured_at(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _safe_filename(value: str) -> str:
    filename = value.replace("\\", "/").rsplit("/", 1)[-1]
    has_unsafe_category = any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in filename
    )
    if (
        not filename
        or filename in {".", ".."}
        or len(filename) > 255
        or has_unsafe_category
    ):
        raise ManualOrderBookImportError(
            "source_filename_invalid",
            "The source filename is invalid.",
        )
    try:
        filename.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ManualOrderBookImportError(
            "source_filename_invalid",
            "The source filename is invalid.",
        ) from exc
    return filename


def _bind_document(
    content: bytes,
    *,
    source_file_sha256: str | None = None,
) -> _BoundManualOrderBookDocument:
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    capture = parse_manual_order_book_json(content)
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("validated payload must be an object")
    return _BoundManualOrderBookDocument(
        capture=capture,
        capture_payload=payload,
        source_file_sha256=(
            hashlib.sha256(content).hexdigest()
            if source_file_sha256 is None
            else source_file_sha256
        ),
    )


def _bind_promoted_pending_document(content: bytes) -> _BoundManualOrderBookDocument:
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    source_file_sha256 = hashlib.sha256(content).hexdigest()
    promoted = confirm_pending_manual_order_book_json(content)
    return _bind_document(
        promoted,
        source_file_sha256=source_file_sha256,
    )


def _validate_source_file_sha256(value: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise ManualOrderBookImportError(
            "source_file_sha256_invalid",
            "The source file SHA-256 is invalid.",
        )
