from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Item, MarketSnapshot, ScreenReviewImport
from api.schemas.local_recognition import ReviewStatus, ReviewedCandidate
from api.services.csv_import import advisory_lock_key_for_import
from api.services.local_recognition_store import ReviewRecord


class ReviewImportError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ReviewImportResult:
    database_item_id: int
    screen_review_import_id: int
    market_snapshot_id: int
    imported_at: datetime
    created: bool


async def import_confirmed_review(
    *,
    session: AsyncSession,
    record: ReviewRecord,
) -> ReviewImportResult:
    if record.status not in {ReviewStatus.CONFIRMED, ReviewStatus.CONFIRMED_WITH_EDITS}:
        raise ReviewImportError(
            "review_not_confirmed",
            "Only confirmed reviews can be imported.",
        )
    if record.candidate is None:
        raise ReviewImportError(
            "review_candidate_missing",
            "Confirmed review is missing its candidate payload.",
        )

    candidate = record.candidate
    candidate_payload = _candidate_audit_payload(candidate)
    candidate_sha256 = _payload_sha256(candidate_payload)

    async with session.begin():
        await _acquire_review_lock(session, review_id=record.review_id)
        existing_import = await session.scalar(
            select(ScreenReviewImport)
            .where(ScreenReviewImport.review_id == record.review_id)
            .limit(1)
        )
        if existing_import is not None:
            if existing_import.candidate_sha256 != candidate_sha256:
                raise ReviewImportError(
                    "review_import_conflict",
                    "This review id was already imported with a different candidate payload.",
                )
            return ReviewImportResult(
                database_item_id=existing_import.item_id,
                screen_review_import_id=existing_import.id,
                market_snapshot_id=existing_import.market_snapshot_id,
                imported_at=existing_import.imported_at,
                created=False,
            )

        item = await _resolve_existing_item(session, candidate)
        await _acquire_snapshot_lock(
            session,
            item_id=item.id,
            observed_at=candidate.observed_at,
        )
        snapshot_id = await session.scalar(
            insert(MarketSnapshot)
            .values(
                item_id=item.id,
                observed_at=candidate.observed_at,
                best_ask=candidate.best_ask,
                best_bid=candidate.best_bid,
                ask_count=None,
                bid_count=None,
                estimated_volume=None,
                source_import_job_id=None,
            )
            .on_conflict_do_nothing(
                index_elements=[MarketSnapshot.item_id, MarketSnapshot.observed_at]
            )
            .returning(MarketSnapshot.id)
        )
        if snapshot_id is None:
            raise ReviewImportError(
                "snapshot_already_exists",
                "A market snapshot already exists for this item and observed_at value.",
            )

        imported_at = datetime.now(UTC)
        imported = ScreenReviewImport(
            review_id=record.review_id,
            item_id=item.id,
            market_snapshot_id=snapshot_id,
            review_status=candidate.status,
            candidate_version=candidate.candidate_version,
            candidate_sha256=candidate_sha256,
            total_bid_quantity=candidate.total_bid_quantity,
            total_ask_quantity=candidate.total_ask_quantity,
            candidate_payload=candidate_payload,
            source_metadata=record.source_metadata.model_dump(mode="json"),
            reviewer_note=record.draft.reviewer_note,
            imported_at=imported_at,
        )
        session.add(imported)
        await session.flush()

        return ReviewImportResult(
            database_item_id=item.id,
            screen_review_import_id=imported.id,
            market_snapshot_id=snapshot_id,
            imported_at=imported_at,
            created=True,
        )


async def _resolve_existing_item(session: AsyncSession, candidate: ReviewedCandidate) -> Item:
    identity = candidate.item_identity
    if identity.item_id is not None:
        item = await session.get(Item, identity.item_id)
    else:
        item = await session.scalar(
            select(Item).where(Item.external_key == identity.item_key).limit(1)
        )

    if item is None:
        raise ReviewImportError(
            "existing_item_required",
            (
                "Import requires an existing item. Create or import the item first, "
                "then reconfirm the review."
            ),
        )
    if item.external_key != identity.item_key or item.name != identity.item_name:
        raise ReviewImportError(
            "item_identity_changed",
            (
                "The existing item identity changed after review confirmation. "
                "Reconfirm before importing."
            ),
        )
    return item


async def _acquire_review_lock(session: AsyncSession, *, review_id: str) -> None:
    lock_key = advisory_lock_key_for_import("screen_review_import", review_id)
    await session.execute(select(func.pg_advisory_xact_lock(lock_key)))


async def _acquire_snapshot_lock(
    session: AsyncSession,
    *,
    item_id: int,
    observed_at: datetime,
) -> None:
    lock_key = advisory_lock_key_for_import(
        "screen_review_snapshot",
        f"{item_id}:{observed_at.astimezone(UTC).isoformat()}",
    )
    await session.execute(select(func.pg_advisory_xact_lock(lock_key)))


def _candidate_audit_payload(candidate: ReviewedCandidate) -> dict[str, Any]:
    immutable_candidate = candidate.model_copy(
        update={
            "imported": False,
            "database_written": False,
            "market_snapshot_created": False,
            "database_item_id": None,
            "screen_review_import_id": None,
            "market_snapshot_id": None,
            "imported_at": None,
        }
    )
    return immutable_candidate.model_dump(mode="json")


def _payload_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
