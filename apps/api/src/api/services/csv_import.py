import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import ImportJob, Item, MarketSnapshot
from api.importers.csv_market_data import CsvContractError, CsvParseResult
from api.schemas.imports import ImportIssue

CSV_UPLOAD_SOURCE_TYPE = "csv_upload"
_SIGNED_BIGINT_BITS = 64
_SIGNED_BIGINT_MAX_EXCLUSIVE = 1 << 63
_UNSIGNED_BIGINT_MODULUS = 1 << _SIGNED_BIGINT_BITS


class ImportJobNotFoundError(LookupError):
    pass


def advisory_lock_key_for_import(source_type: str, checksum: str) -> int:
    # PostgreSQL advisory locks take signed 64-bit keys. Derive one from SHA-256
    # so the same import source/checksum maps consistently across processes.
    digest = hashlib.sha256(f"{source_type}:{checksum}".encode("utf-8")).digest()
    key = int.from_bytes(digest[:8], byteorder="big", signed=False)
    if key >= _SIGNED_BIGINT_MAX_EXCLUSIVE:
        key -= _UNSIGNED_BIGINT_MODULUS
    return key


class CsvImportService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def import_parsed_csv(
        self, *, filename: str, checksum: str, parsed: CsvParseResult
    ) -> ImportJob:
        try:
            async with self._session.begin():
                await self._acquire_import_advisory_lock(CSV_UPLOAD_SOURCE_TYPE, checksum)
                duplicate_of = await self._find_duplicate_job(checksum)
                if duplicate_of is not None:
                    return await self._create_duplicate_job(filename, checksum, duplicate_of)

                job = ImportJob(
                    source_type=CSV_UPLOAD_SOURCE_TYPE,
                    filename=filename,
                    checksum=checksum,
                    status="processing",
                    started_at=datetime.now(UTC),
                    row_count=0,
                    valid_row_count=0,
                    invalid_row_count=0,
                    error_report={"errors": [], "warnings": []},
                )
                self._session.add(job)
                await self._session.flush()

                warnings: list[ImportIssue] = []
                seen_snapshots: set[tuple[int, datetime]] = set()

                for row in parsed.rows:
                    item = await self._get_or_create_item(
                        external_key=row.item_key,
                        name=row.item_name,
                        category=row.category,
                    )
                    if item.name != row.item_name or item.category != row.category:
                        warnings.append(
                            ImportIssue(
                                row_number=row.row_number,
                                field="item_key",
                                error_code="item_metadata_mismatch",
                                message=(
                                    "Existing item was reused; incoming item_name/category "
                                    "were not applied."
                                ),
                            )
                        )

                    snapshot_key = (item.id, row.observed_at)
                    was_seen_in_file = snapshot_key in seen_snapshots
                    if was_seen_in_file:
                        warnings.append(
                            ImportIssue(
                                row_number=row.row_number,
                                field="observed_at",
                                error_code="duplicate_snapshot_in_file",
                                message=(
                                    "Snapshot with the same item_key and observed_at already "
                                    "appeared in this CSV."
                                ),
                            )
                        )
                    seen_snapshots.add(snapshot_key)

                    inserted = await self._insert_snapshot(
                        item_id=item.id,
                        observed_at=row.observed_at,
                        best_ask=row.best_ask,
                        best_bid=row.best_bid,
                        ask_count=row.ask_count,
                        bid_count=row.bid_count,
                        estimated_volume=row.estimated_volume,
                        source_import_job_id=job.id,
                    )
                    if not inserted and not was_seen_in_file:
                        warnings.append(
                            ImportIssue(
                                row_number=row.row_number,
                                field="observed_at",
                                error_code="duplicate_snapshot_existing",
                                message=(
                                    "Snapshot with the same item_key and observed_at already "
                                    "exists and was left unchanged."
                                ),
                            )
                        )

                job.status = "completed"
                job.finished_at = datetime.now(UTC)
                job.row_count = parsed.row_count
                job.valid_row_count = len(parsed.rows)
                job.invalid_row_count = len(parsed.errors)
                job.error_report = {
                    "errors": [error.model_dump(mode="json") for error in parsed.errors],
                    "warnings": [warning.model_dump(mode="json") for warning in warnings],
                }
        except SQLAlchemyError:
            job = await self._create_failed_job_for_database_error(
                filename=filename,
                checksum=checksum,
                row_count=parsed.row_count,
                valid_row_count=0,
                invalid_row_count=parsed.row_count,
            )

        return job

    async def create_failed_job_for_contract_error(
        self, *, filename: str, checksum: str, error: CsvContractError
    ) -> ImportJob:
        async with self._session.begin():
            job = ImportJob(
                source_type=CSV_UPLOAD_SOURCE_TYPE,
                filename=filename,
                checksum=checksum,
                status="failed",
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                row_count=0,
                valid_row_count=0,
                invalid_row_count=0,
                error_report={
                    "errors": [
                        ImportIssue(
                            row_number=0,
                            field=error.field,
                            error_code=error.code,
                            message=error.message,
                        ).model_dump(mode="json")
                    ],
                    "warnings": [],
                },
            )
            self._session.add(job)
        return job

    async def get_job(self, job_id: int) -> ImportJob:
        job = await self._session.get(ImportJob, job_id)
        if job is None:
            raise ImportJobNotFoundError(f"Import job {job_id} was not found.")
        return job

    async def _find_duplicate_job(self, checksum: str) -> ImportJob | None:
        result = await self._session.execute(
            select(ImportJob)
            .where(
                ImportJob.source_type == CSV_UPLOAD_SOURCE_TYPE,
                ImportJob.checksum == checksum,
                ImportJob.status == "completed",
            )
            .order_by(ImportJob.id.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _create_duplicate_job(
        self, filename: str, checksum: str, duplicate_of: ImportJob
    ) -> ImportJob:
        job = ImportJob(
            source_type=CSV_UPLOAD_SOURCE_TYPE,
            filename=filename,
            checksum=checksum,
            status="duplicate",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            row_count=duplicate_of.row_count,
            valid_row_count=0,
            invalid_row_count=0,
            error_report={
                "errors": [],
                "warnings": [],
                "duplicate_of_job_id": duplicate_of.id,
            },
        )
        self._session.add(job)
        await self._session.flush()
        return job

    async def _get_or_create_item(self, *, external_key: str, name: str, category: str) -> Item:
        insert_statement = (
            insert(Item)
            .values(external_key=external_key, name=name, category=category)
            .on_conflict_do_nothing(index_elements=[Item.external_key])
            .returning(Item)
        )
        result = await self._session.execute(insert_statement)
        item = result.scalar_one_or_none()
        if item is not None:
            return item

        select_result = await self._session.execute(
            select(Item).where(Item.external_key == external_key).limit(1)
        )
        return select_result.scalar_one()

    async def _insert_snapshot(self, **values: Any) -> bool:
        statement = (
            insert(MarketSnapshot)
            .values(**values)
            .on_conflict_do_nothing(
                constraint="uq_market_snapshots_item_observed_at",
            )
            .returning(MarketSnapshot.id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def _acquire_import_advisory_lock(self, source_type: str, checksum: str) -> None:
        lock_key = advisory_lock_key_for_import(source_type, checksum)
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def _create_failed_job_for_database_error(
        self,
        *,
        filename: str,
        checksum: str,
        row_count: int,
        valid_row_count: int,
        invalid_row_count: int,
    ) -> ImportJob:
        issue = ImportIssue(
            row_number=0,
            field="database",
            error_code="database_error",
            message="The import could not be completed due to a database error.",
        )
        async with self._session.begin():
            job = ImportJob(
                source_type=CSV_UPLOAD_SOURCE_TYPE,
                filename=filename,
                checksum=checksum,
                status="failed",
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                row_count=row_count,
                valid_row_count=valid_row_count,
                invalid_row_count=invalid_row_count,
                error_report={
                    "errors": [issue.model_dump(mode="json")],
                    "warnings": [],
                },
            )
            self._session.add(job)
        return job
