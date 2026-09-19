from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ImportJob, Item
from api.importers.item_catalog_csv import ItemCatalogDocument, ItemCatalogRow
from api.services.csv_import import advisory_lock_key_for_import
from api.services.manual_order_book_import import _safe_filename

ITEM_CATALOG_SOURCE_TYPE = "item_catalog_csv_v1"


class ItemCatalogImportError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ItemCatalogInspection:
    row_count: int
    create_count: int
    reuse_count: int
    existing_import_job_id: int | None


@dataclass(frozen=True, slots=True)
class ItemCatalogImportResult(ItemCatalogInspection):
    import_job_id: int
    created: bool


async def inspect_item_catalog(
    *, session: AsyncSession, document: ItemCatalogDocument
) -> ItemCatalogInspection:
    return await _inspect(session=session, document=document)


async def import_item_catalog(
    *,
    session: AsyncSession,
    document: ItemCatalogDocument,
    source_filename: str,
) -> ItemCatalogImportResult:
    filename = _safe_filename(source_filename)
    try:
        async with session.begin():
            await _acquire_locks(session=session, document=document)
            inspection = await _inspect(session=session, document=document)
            if inspection.existing_import_job_id is not None:
                job = _job(
                    filename=filename,
                    checksum=document.normalized_catalog_sha256,
                    status="duplicate",
                    row_count=inspection.row_count,
                    valid_row_count=0,
                    report={
                        "duplicate_of_job_id": inspection.existing_import_job_id,
                        "source_file_sha256": document.source_file_sha256,
                    },
                )
                session.add(job)
                await session.flush()
                return ItemCatalogImportResult(
                    row_count=inspection.row_count,
                    create_count=inspection.create_count,
                    reuse_count=inspection.reuse_count,
                    existing_import_job_id=inspection.existing_import_job_id,
                    import_job_id=job.id,
                    created=False,
                )

            existing_keys = set(
                (
                    await session.scalars(
                        select(Item.external_key).where(
                            Item.external_key.in_(
                                row.external_key for row in document.rows
                            )
                        )
                    )
                ).all()
            )
            session.add_all(
                Item(
                    external_key=row.external_key,
                    name=row.name,
                    category=row.category,
                    rarity=row.rarity,
                    is_active=row.is_active,
                )
                for row in document.rows
                if row.external_key not in existing_keys
            )
            now = datetime.now(UTC)
            job = _job(
                filename=filename,
                checksum=document.normalized_catalog_sha256,
                status="completed",
                row_count=inspection.row_count,
                valid_row_count=inspection.row_count,
                report={
                    "created_item_count": inspection.create_count,
                    "reused_item_count": inspection.reuse_count,
                    "source_file_sha256": document.source_file_sha256,
                },
                now=now,
            )
            session.add(job)
            await session.flush()
            return ItemCatalogImportResult(
                row_count=inspection.row_count,
                create_count=inspection.create_count,
                reuse_count=inspection.reuse_count,
                existing_import_job_id=inspection.existing_import_job_id,
                import_job_id=job.id,
                created=True,
            )
    except ItemCatalogImportError:
        await session.rollback()
        await _record_failed(
            session=session,
            filename=filename,
            checksum=document.normalized_catalog_sha256,
            row_count=len(document.rows),
            code="item_metadata_conflict",
            source_file_sha256=document.source_file_sha256,
        )
        raise
    except SQLAlchemyError as exc:
        await session.rollback()
        try:
            await _record_failed(
                session=session,
                filename=filename,
                checksum=document.normalized_catalog_sha256,
                row_count=len(document.rows),
                code="database_error",
                source_file_sha256=document.source_file_sha256,
            )
        except SQLAlchemyError:
            pass
        raise ItemCatalogImportError("database_error") from exc


async def record_invalid_item_catalog_attempt(
    *,
    session: AsyncSession,
    source_filename: str,
    source_file_sha256: str,
    error_code: str,
) -> int:
    filename = _safe_filename(source_filename)
    return await _record_failed(
        session=session,
        filename=filename,
        checksum=source_file_sha256,
        row_count=0,
        code=error_code,
        source_file_sha256=source_file_sha256,
    )


async def _inspect(
    *, session: AsyncSession, document: ItemCatalogDocument
) -> ItemCatalogInspection:
    items = list(
        (
            await session.scalars(
                select(Item).where(
                    Item.external_key.in_(row.external_key for row in document.rows)
                )
            )
        ).all()
    )
    existing = {item.external_key: item for item in items}
    for row in document.rows:
        item = existing.get(row.external_key)
        if item is not None and not _metadata_matches(item, row):
            raise ItemCatalogImportError("item_metadata_conflict")
    existing_job = await session.scalar(
        select(ImportJob.id)
        .where(
            ImportJob.source_type == ITEM_CATALOG_SOURCE_TYPE,
            ImportJob.checksum == document.normalized_catalog_sha256,
            ImportJob.status == "completed",
        )
        .order_by(ImportJob.id.asc())
        .limit(1)
    )
    return ItemCatalogInspection(
        row_count=len(document.rows),
        create_count=len(document.rows) - len(existing),
        reuse_count=len(existing),
        existing_import_job_id=existing_job,
    )


async def _acquire_locks(
    *, session: AsyncSession, document: ItemCatalogDocument
) -> None:
    identities = sorted(
        {f"catalog:{document.normalized_catalog_sha256}"}
        | {f"item:{row.external_key}" for row in document.rows}
    )
    for identity in identities:
        key = advisory_lock_key_for_import(ITEM_CATALOG_SOURCE_TYPE, identity)
        await session.execute(select(func.pg_advisory_xact_lock(key)))


def _metadata_matches(item: Item, row: ItemCatalogRow) -> bool:
    return (
        item.name == row.name
        and item.category == row.category
        and item.rarity == row.rarity
        and item.is_active is row.is_active
    )


def _job(
    *,
    filename: str,
    checksum: str,
    status: str,
    row_count: int,
    valid_row_count: int,
    report: dict[str, object],
    now: datetime | None = None,
) -> ImportJob:
    timestamp = now or datetime.now(UTC)
    return ImportJob(
        source_type=ITEM_CATALOG_SOURCE_TYPE,
        filename=filename,
        checksum=checksum,
        status=status,
        started_at=timestamp,
        finished_at=timestamp,
        row_count=row_count,
        valid_row_count=valid_row_count,
        invalid_row_count=0 if status != "failed" else row_count,
        error_report={"errors": [], "warnings": [], **report},
    )


async def _record_failed(
    *,
    session: AsyncSession,
    filename: str,
    checksum: str,
    row_count: int,
    code: str,
    source_file_sha256: str,
) -> int:
    async with session.begin():
        job = _job(
            filename=filename,
            checksum=checksum,
            status="failed",
            row_count=row_count,
            valid_row_count=0,
            report={
                "errors": [{"field": "file", "error_code": code}],
                "source_file_sha256": source_file_sha256,
            },
        )
        session.add(job)
        await session.flush()
        return job.id
