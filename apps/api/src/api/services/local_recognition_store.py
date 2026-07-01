from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from api.schemas.local_recognition import (
    CandidateRecognition,
    ImageMetadata,
    ItemIdentity,
    ObservedAtSource,
    OcrCandidate,
    OcrEvidenceSummary,
    RecognitionMetadata,
    ReviewDraft,
    ReviewResponse,
    ReviewStatus,
    ReviewedCandidate,
)


class ReviewNotFoundError(LookupError):
    pass


class ReviewNotEditableError(ValueError):
    pass


class ReviewStoreFullError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewRecord:
    review_id: str
    created_at: datetime
    expires_at: datetime
    status: ReviewStatus
    suggested_observed_at: datetime
    image: ImageMetadata | None
    recognition: RecognitionMetadata
    ocr_candidate: OcrCandidate = field(default_factory=OcrCandidate)
    draft: ReviewDraft = field(default_factory=ReviewDraft)
    ocr_evidence_summary: OcrEvidenceSummary = field(default_factory=OcrEvidenceSummary)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    candidate: ReviewedCandidate | None = None
    confirmed_at: datetime | None = None
    rejected_at: datetime | None = None

    def to_response(self) -> ReviewResponse:
        return ReviewResponse(
            review_id=self.review_id,
            created_at=self.created_at,
            expires_at=self.expires_at,
            status=self.status,
            suggested_observed_at=self.suggested_observed_at,
            image=self.image,
            recognition=self.recognition,
            ocr_candidate=self.ocr_candidate,
            draft=self.draft,
            ocr_evidence_summary=self.ocr_evidence_summary,
            warnings=list(self.warnings),
            errors=list(self.errors),
            candidate=self.candidate,
            confirmed_at=self.confirmed_at,
            rejected_at=self.rejected_at,
        )


class LocalReviewStore:
    def __init__(self, *, max_reviews: int, ttl_seconds: int) -> None:
        self.max_reviews = max_reviews
        self.ttl_seconds = ttl_seconds
        self._records: OrderedDict[str, ReviewRecord] = OrderedDict()
        self._lock = threading.RLock()

    def create_processing(
        self,
        *,
        review_id: str,
        created_at: datetime,
        image: ImageMetadata,
        recognition: RecognitionMetadata,
    ) -> ReviewRecord:
        with self._lock:
            self._cleanup_locked(datetime.now(UTC), remove_expired_records=True)
            if len(self._records) >= self.max_reviews:
                raise ReviewStoreFullError("The local review store is full.")
            record = ReviewRecord(
                review_id=review_id,
                created_at=created_at,
                expires_at=created_at + timedelta(seconds=self.ttl_seconds),
                status=ReviewStatus.PROCESSING,
                suggested_observed_at=created_at,
                image=image,
                recognition=recognition,
                draft=ReviewDraft(
                    observed_at=created_at,
                    observed_at_source=ObservedAtSource.REVIEW_CREATED_DEFAULT,
                ),
            )
            self._records[review_id] = record
            return copy.deepcopy(record)

    def get(self, review_id: str) -> ReviewRecord:
        with self._lock:
            self._cleanup_locked(datetime.now(UTC), remove_expired_records=False)
            record = self._records.get(review_id)
            if record is None:
                raise ReviewNotFoundError(review_id)
            return copy.deepcopy(record)

    def list(self) -> list[ReviewRecord]:
        with self._lock:
            self._cleanup_locked(datetime.now(UTC), remove_expired_records=False)
            return [copy.deepcopy(record) for record in self._records.values()]

    def count(self) -> int:
        with self._lock:
            self._cleanup_locked(datetime.now(UTC), remove_expired_records=False)
            return len(self._records)

    def mark_recognized(
        self,
        review_id: str,
        *,
        status: ReviewStatus,
        ocr_candidate: OcrCandidate,
        recognition: RecognitionMetadata,
        ocr_evidence_summary: OcrEvidenceSummary,
        warnings: list[str],
        errors: list[str],
    ) -> ReviewRecord:
        with self._lock:
            record = self._editable_processing_record_locked(review_id)
            updated = replace(
                record,
                status=status,
                ocr_candidate=ocr_candidate,
                recognition=recognition,
                ocr_evidence_summary=ocr_evidence_summary,
                warnings=tuple(warnings),
                errors=tuple(errors),
                draft=_draft_from_ocr(record.draft, ocr_candidate),
            )
            self._records[review_id] = updated
            return copy.deepcopy(updated)

    def mark_failed(
        self,
        review_id: str,
        *,
        recognition: RecognitionMetadata,
        errors: list[str],
        warnings: list[str] | None = None,
        unreadable: bool = False,
    ) -> ReviewRecord | None:
        with self._lock:
            record = self._records.get(review_id)
            if record is None:
                return None
            if record.status != ReviewStatus.PROCESSING:
                return copy.deepcopy(record)
            updated = replace(
                record,
                status=ReviewStatus.UNREADABLE if unreadable else ReviewStatus.FAILED,
                recognition=recognition,
                warnings=tuple(warnings or ()),
                errors=tuple(errors),
            )
            self._records[review_id] = updated
            return copy.deepcopy(updated)

    def patch_draft(self, review_id: str, draft: ReviewDraft) -> ReviewRecord:
        with self._lock:
            record = self._editable_pending_record_locked(review_id)
            updated = replace(record, draft=draft)
            self._records[review_id] = updated
            return copy.deepcopy(updated)

    def confirm(
        self,
        review_id: str,
        *,
        draft: ReviewDraft,
        candidate: ReviewedCandidate,
        edited_fields: list[str],
        confirmed_at: datetime,
    ) -> ReviewRecord:
        with self._lock:
            record = self._editable_pending_record_locked(review_id)
            status = (
                ReviewStatus.CONFIRMED_WITH_EDITS
                if edited_fields
                else ReviewStatus.CONFIRMED
            )
            updated_candidate = candidate.model_copy(update={"status": status.value})
            updated = replace(
                record,
                status=status,
                draft=draft,
                candidate=updated_candidate,
                confirmed_at=confirmed_at,
            )
            self._records[review_id] = updated
            return copy.deepcopy(updated)

    def reject(self, review_id: str, *, reviewer_note: str | None, rejected_at: datetime) -> ReviewRecord:
        with self._lock:
            record = self._editable_pending_record_locked(review_id)
            draft = record.draft.model_copy(update={"reviewer_note": reviewer_note})
            updated = replace(
                record,
                status=ReviewStatus.REJECTED,
                draft=draft,
                rejected_at=rejected_at,
            )
            self._records[review_id] = updated
            return copy.deepcopy(updated)

    def unreadable(
        self, review_id: str, *, reviewer_note: str | None, rejected_at: datetime
    ) -> ReviewRecord:
        with self._lock:
            record = self._editable_pending_record_locked(review_id)
            draft = record.draft.model_copy(update={"reviewer_note": reviewer_note})
            updated = replace(
                record,
                status=ReviewStatus.UNREADABLE,
                draft=draft,
                rejected_at=rejected_at,
            )
            self._records[review_id] = updated
            return copy.deepcopy(updated)

    def delete(self, review_id: str) -> None:
        with self._lock:
            self._cleanup_locked(datetime.now(UTC), remove_expired_records=False)
            if review_id not in self._records:
                raise ReviewNotFoundError(review_id)
            del self._records[review_id]

    def clear(self) -> int:
        with self._lock:
            cleared = len(self._records)
            self._records.clear()
            return cleared

    def _editable_processing_record_locked(self, review_id: str) -> ReviewRecord:
        self._cleanup_locked(datetime.now(UTC), remove_expired_records=False)
        record = self._records.get(review_id)
        if record is None:
            raise ReviewNotFoundError(review_id)
        if record.status != ReviewStatus.PROCESSING:
            raise ReviewNotEditableError("Review is no longer processing.")
        return record

    def _editable_pending_record_locked(self, review_id: str) -> ReviewRecord:
        self._cleanup_locked(datetime.now(UTC), remove_expired_records=False)
        record = self._records.get(review_id)
        if record is None:
            raise ReviewNotFoundError(review_id)
        if record.status != ReviewStatus.PENDING_REVIEW:
            raise ReviewNotEditableError("Review is not editable.")
        return record

    def _cleanup_locked(self, now: datetime, *, remove_expired_records: bool) -> None:
        expired_ids: list[str] = []
        for review_id, record in list(self._records.items()):
            if now < record.expires_at:
                continue
            if record.status in {ReviewStatus.PROCESSING, ReviewStatus.PENDING_REVIEW}:
                self._records[review_id] = replace(record, status=ReviewStatus.EXPIRED)
            elif remove_expired_records:
                expired_ids.append(review_id)
        if remove_expired_records:
            for review_id, record in list(self._records.items()):
                if record.status == ReviewStatus.EXPIRED:
                    expired_ids.append(review_id)
            for review_id in dict.fromkeys(expired_ids):
                self._records.pop(review_id, None)


def _draft_from_ocr(draft: ReviewDraft, candidate: OcrCandidate) -> ReviewDraft:
    return draft.model_copy(
        update={
            "final_item_name": candidate.item_name_normalized,
            "final_best_bid": candidate.best_bid,
            "final_best_ask": candidate.best_ask,
            "final_total_bid_quantity": candidate.total_bid_quantity,
            "final_total_ask_quantity": candidate.total_ask_quantity,
        }
    )


def make_candidate(
    *,
    record: ReviewRecord,
    draft: ReviewDraft,
    identity: ItemIdentity,
    edited_fields: list[str],
    status: Literal["confirmed", "confirmed_with_edits"],
) -> ReviewedCandidate:
    assert draft.observed_at is not None
    assert draft.final_best_bid is not None
    assert draft.final_best_ask is not None
    return ReviewedCandidate(
        review_id=record.review_id,
        observed_at=draft.observed_at,
        observed_at_source=draft.observed_at_source,
        item_identity=identity,
        best_bid=draft.final_best_bid,
        best_ask=draft.final_best_ask,
        total_bid_quantity=draft.final_total_bid_quantity,
        total_ask_quantity=draft.final_total_ask_quantity,
        recognition=CandidateRecognition(
            layout_name=record.recognition.layout_name,
            layout_version=record.recognition.layout_version,
            config_sha256=record.recognition.config_sha256,
            ocr_backend=record.recognition.ocr_backend,
            edited_fields=edited_fields,
            parser_version=record.recognition.parser_version,
            runner_version=record.recognition.runner_version,
        ),
        status=status,
    )


def compute_edited_fields(record: ReviewRecord, draft: ReviewDraft) -> list[str]:
    candidate = record.ocr_candidate
    edited: list[str] = []
    if (candidate.item_name_normalized or None) != (draft.final_item_name or None):
        edited.append("item_name")
    if not _decimal_equal(candidate.best_bid, draft.final_best_bid):
        edited.append("best_bid")
    if not _decimal_equal(candidate.best_ask, draft.final_best_ask):
        edited.append("best_ask")
    if candidate.total_bid_quantity != draft.final_total_bid_quantity:
        edited.append("total_bid_quantity")
    if candidate.total_ask_quantity != draft.final_total_ask_quantity:
        edited.append("total_ask_quantity")
    if draft.observed_at is not None and draft.observed_at != record.suggested_observed_at:
        edited.append("observed_at")
    return edited


def _decimal_equal(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return left is right
    return left == right


review_store = LocalReviewStore(max_reviews=100, ttl_seconds=7200)

