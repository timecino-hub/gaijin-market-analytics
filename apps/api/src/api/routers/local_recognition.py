from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.session import get_session
from api.schemas.local_recognition import (
    ReviewCapabilitiesResponse,
    ReviewClearResponse,
    ReviewConfirmRequest,
    ReviewCreateResponse,
    ReviewListResponse,
    ReviewPatchRequest,
    ReviewRejectRequest,
    ReviewResponse,
    ReviewUnreadableRequest,
)
from api.services.items import ItemNotFoundError
from api.services.local_recognition import (
    MAX_IMAGE_BYTES,
    ImageValidationError,
    LocalRecognitionError,
    capabilities_payload,
    confirm_review,
    create_processing_review,
    patch_review_draft,
    process_review_image,
    validate_image_upload,
)
from api.services.local_recognition_store import (
    ReviewNotEditableError,
    ReviewNotFoundError,
    ReviewStoreFullError,
    review_store,
)

router = APIRouter(prefix="/api/v1/local-recognition", tags=["local-recognition"])


@router.get("/capabilities", response_model=ReviewCapabilitiesResponse)
def get_capabilities() -> ReviewCapabilitiesResponse:
    return ReviewCapabilitiesResponse(**capabilities_payload())


@router.post(
    "/reviews",
    response_model=ReviewCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_review(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
) -> ReviewCreateResponse:
    content = await file.read(MAX_IMAGE_BYTES + 1)
    try:
        image, temp_path = validate_image_upload(filename=file.filename, content=content)
        record = create_processing_review(image=image)
    except ImageValidationError as exc:
        raise _business_error(
            _image_error_status(exc.code),
            exc.code,
            exc.message,
        ) from exc
    except ReviewStoreFullError as exc:
        raise _business_error(
            status.HTTP_409_CONFLICT,
            "review_store_full",
            "The local review store is full.",
        ) from exc
    background_tasks.add_task(process_review_image, record.review_id, temp_path)
    return ReviewCreateResponse(
        review_id=record.review_id,
        status=record.status,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


@router.get("/reviews", response_model=ReviewListResponse)
def list_reviews() -> ReviewListResponse:
    records = sorted(
        review_store.list(),
        key=lambda record: (_status_sort(record.status.value), record.created_at),
        reverse=True,
    )
    return ReviewListResponse(
        reviews=[record.to_response() for record in records],
        total=len(records),
        store_count=review_store.count(),
        store_capacity=review_store.max_reviews,
    )


@router.delete("/reviews", response_model=ReviewClearResponse)
def clear_reviews(confirm: Annotated[bool, Query()] = False) -> ReviewClearResponse:
    if not confirm:
        raise _business_error(
            status.HTTP_400_BAD_REQUEST,
            "clear_confirmation_required",
            "Pass confirm=true to clear all in-memory reviews.",
        )
    return ReviewClearResponse(cleared=review_store.clear())


@router.get("/reviews/{review_id}", response_model=ReviewResponse)
def get_review(review_id: str) -> ReviewResponse:
    try:
        return review_store.get(review_id).to_response()
    except ReviewNotFoundError as exc:
        raise _business_error(
            status.HTTP_404_NOT_FOUND,
            "review_not_found",
            "Review was not found.",
        ) from exc


@router.patch("/reviews/{review_id}", response_model=ReviewResponse)
def patch_review(review_id: str, request: ReviewPatchRequest) -> ReviewResponse:
    try:
        record = review_store.get(review_id)
        draft = patch_review_draft(record, request)
        return review_store.patch_draft(review_id, draft).to_response()
    except ReviewNotFoundError as exc:
        raise _business_error(status.HTTP_404_NOT_FOUND, "review_not_found", "Review was not found.") from exc
    except ReviewNotEditableError as exc:
        raise _business_error(
            status.HTTP_409_CONFLICT,
            "review_not_editable",
            "Only pending reviews can be edited.",
        ) from exc
    except LocalRecognitionError as exc:
        raise _business_error(status.HTTP_400_BAD_REQUEST, exc.code, exc.message) from exc


@router.post("/reviews/{review_id}/confirm", response_model=ReviewResponse)
async def confirm_review_endpoint(
    review_id: str,
    request: ReviewConfirmRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReviewResponse:
    try:
        record = review_store.get(review_id)
        return (await confirm_review(session=session, record=record, request=request)).to_response()
    except ReviewNotFoundError as exc:
        raise _business_error(status.HTTP_404_NOT_FOUND, "review_not_found", "Review was not found.") from exc
    except ReviewNotEditableError as exc:
        raise _business_error(
            status.HTTP_409_CONFLICT,
            "review_not_editable",
            "Only pending reviews can be confirmed.",
        ) from exc
    except ItemNotFoundError as exc:
        raise _business_error(status.HTTP_404_NOT_FOUND, "item_not_found", "The requested item was not found.") from exc
    except LocalRecognitionError as exc:
        raise _business_error(status.HTTP_400_BAD_REQUEST, exc.code, exc.message) from exc


@router.post("/reviews/{review_id}/reject", response_model=ReviewResponse)
def reject_review(review_id: str, request: ReviewRejectRequest) -> ReviewResponse:
    try:
        from datetime import UTC, datetime

        return review_store.reject(
            review_id,
            reviewer_note=request.reviewer_note,
            rejected_at=datetime.now(UTC),
        ).to_response()
    except ReviewNotFoundError as exc:
        raise _business_error(status.HTTP_404_NOT_FOUND, "review_not_found", "Review was not found.") from exc
    except ReviewNotEditableError as exc:
        raise _business_error(
            status.HTTP_409_CONFLICT,
            "review_not_editable",
            "Only pending reviews can be rejected.",
        ) from exc


@router.post("/reviews/{review_id}/unreadable", response_model=ReviewResponse)
def mark_unreadable(review_id: str, request: ReviewUnreadableRequest) -> ReviewResponse:
    try:
        from datetime import UTC, datetime

        return review_store.unreadable(
            review_id,
            reviewer_note=request.reviewer_note,
            rejected_at=datetime.now(UTC),
        ).to_response()
    except ReviewNotFoundError as exc:
        raise _business_error(status.HTTP_404_NOT_FOUND, "review_not_found", "Review was not found.") from exc
    except ReviewNotEditableError as exc:
        raise _business_error(
            status.HTTP_409_CONFLICT,
            "review_not_editable",
            "Only pending reviews can be marked unreadable.",
        ) from exc


@router.delete("/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_review(review_id: str) -> None:
    try:
        review_store.delete(review_id)
    except ReviewNotFoundError as exc:
        raise _business_error(status.HTTP_404_NOT_FOUND, "review_not_found", "Review was not found.") from exc


def _status_sort(value: str) -> int:
    return {
        "pending_review": 3,
        "unreadable": 2,
        "failed": 2,
    }.get(value, 1)


def _image_error_status(code: str) -> int:
    if code in {"image_too_large", "image_dimensions_too_large"}:
        return status.HTTP_413_CONTENT_TOO_LARGE
    if code in {"unsupported_image_format", "image_signature_mismatch"}:
        return status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    return status.HTTP_400_BAD_REQUEST


def _business_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})

