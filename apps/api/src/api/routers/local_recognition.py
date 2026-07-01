from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings, parse_cors_allowed_origins
from api.db.session import get_session
from api.schemas.local_recognition import (
    ExtensionPairingSummary,
    ExtensionReviewCreateResponse,
    ExtensionStatusResponse,
    PairRequest,
    PairResponse,
    PairingCodeCreateResponse,
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
from api.services.local_extension_pairing import (
    CAPTURE_DEDUP_WINDOW_SECONDS,
    GLOBAL_PAIR_ATTEMPTS_PER_MINUTE,
    PAIRING_CODE_MAX_FAILED_ATTEMPTS,
    PAIRING_CODE_TTL_SECONDS,
    PAIR_ATTEMPTS_PER_CLIENT_PER_MINUTE,
    UPLOAD_RATE_LIMIT_CAPACITY,
    UPLOAD_RATE_LIMIT_REFILL_PER_MINUTE,
    CaptureAlreadyReserved,
    PairingError,
    RateLimitExceeded,
    capture_sha256,
    pairing_store,
)
from api.services.items import ItemNotFoundError
from api.services.local_recognition import (
    MAX_IMAGE_BYTES,
    MAX_MULTIPART_BODY_BYTES,
    ImageValidationError,
    LocalRecognitionError,
    capabilities_payload,
    confirm_review,
    create_review_from_image,
    create_review_record,
    patch_review_draft,
    process_review_image,
    validate_image_upload,
)
from api.services.local_recognition_source import SourceMetadataError, extension_source_metadata
from api.services.local_recognition_store import (
    ReviewNotEditableError,
    ReviewNotFoundError,
    ReviewStoreFullError,
    review_store,
)

router = APIRouter(prefix="/api/v1/local-recognition", tags=["local-recognition"])


def local_management_dependency(request: Request) -> None:
    _require_local_management_request(request)


@router.get("/capabilities", response_model=ReviewCapabilitiesResponse)
def get_capabilities() -> ReviewCapabilitiesResponse:
    return ReviewCapabilitiesResponse(**capabilities_payload())


@router.post("/pairing-codes", response_model=PairingCodeCreateResponse, status_code=status.HTTP_201_CREATED)
def create_pairing_code(
    _: Annotated[None, Depends(local_management_dependency)],
) -> PairingCodeCreateResponse:
    try:
        created = pairing_store.create_pairing_code()
    except PairingError as exc:
        raise _pairing_business_error(exc) from exc
    return PairingCodeCreateResponse(
        pairing_code_id=created.pairing_code_id,
        pairing_code=created.pairing_code,
        expires_at=created.expires_at,
        ttl_seconds=created.ttl_seconds,
    )


@router.post("/pair", response_model=PairResponse)
def pair_extension(request: Request, payload: PairRequest) -> PairResponse:
    try:
        created = pairing_store.pair(
            pairing_code_id=payload.pairing_code_id,
            pairing_code=payload.pairing_code,
            client_name=payload.client_name,
            extension_version=payload.extension_version,
            client_key=_client_key(request),
        )
    except PairingError as exc:
        raise _pairing_business_error(exc) from exc
    return PairResponse(pairing_id=created.pairing_id, token=created.token, created_at=created.created_at)


@router.get("/extension-status", response_model=ExtensionStatusResponse)
def get_extension_status(
    _: Annotated[None, Depends(local_management_dependency)],
) -> ExtensionStatusResponse:
    return ExtensionStatusResponse(
        bridge_available=True,
        restart_notice="Local extension pairings are stored only in API process memory. Restarting the API requires pairing again.",
        pairings=[
            ExtensionPairingSummary(
                pairing_id=pairing.pairing_id,
                created_at=pairing.created_at,
                last_seen_at=pairing.last_seen_at,
                revoked_at=pairing.revoked_at,
                extension_version=pairing.extension_version,
                client_name=pairing.client_name,
            )
            for pairing in pairing_store.list_pairings()
        ],
        pairing_code_ttl_seconds=PAIRING_CODE_TTL_SECONDS,
        pairing_code_max_failed_attempts=PAIRING_CODE_MAX_FAILED_ATTEMPTS,
        pair_attempts_per_client_per_minute=PAIR_ATTEMPTS_PER_CLIENT_PER_MINUTE,
        global_pair_attempts_per_minute=GLOBAL_PAIR_ATTEMPTS_PER_MINUTE,
        extension_uploads_per_minute=UPLOAD_RATE_LIMIT_REFILL_PER_MINUTE,
        extension_upload_burst=UPLOAD_RATE_LIMIT_CAPACITY,
        extension_dedup_window_seconds=CAPTURE_DEDUP_WINDOW_SECONDS,
    )


@router.delete("/pairings/{pairing_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_pairing(
    pairing_id: str,
    _: Annotated[None, Depends(local_management_dependency)],
) -> None:
    try:
        pairing_store.revoke_pairing(pairing_id)
    except PairingError as exc:
        raise _pairing_business_error(exc) from exc


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
        record, temp_path = create_review_from_image(filename=file.filename, content=content)
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


@router.post(
    "/extension-reviews",
    response_model=ExtensionReviewCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_extension_review(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    authorization: Annotated[str | None, Header()] = None,
    source_url: Annotated[str | None, Form()] = None,
    source_tab_title: Annotated[str | None, Form()] = None,
    extension_version: Annotated[str | None, Form()] = None,
) -> ExtensionReviewCreateResponse:
    _require_loopback_request(request)
    _enforce_content_length(request)
    try:
        token = _bearer_token(authorization)
        pairing = pairing_store.authenticate_token(token)
        pairing_store.consume_upload_token(pairing.pairing_id)
    except PairingError as exc:
        raise _pairing_business_error(exc) from exc

    content = await file.read(MAX_IMAGE_BYTES + 1)
    temp_path = None
    reserved_capture = False
    try:
        image, temp_path = validate_image_upload(filename=file.filename, content=content)
        image_hash = capture_sha256(content)
        metadata = extension_source_metadata(
            pairing_id=pairing.pairing_id,
            capture_sha256=image_hash,
            extension_version=extension_version or pairing.extension_version,
            source_url=source_url,
            source_tab_title=source_tab_title,
        )
        try:
            reservation = pairing_store.reserve_capture(
                pairing_id=pairing.pairing_id,
                capture_sha256=image_hash,
            )
        except CaptureAlreadyReserved:
            existing_review_id = pairing_store.wait_for_capture_review(
                pairing_id=pairing.pairing_id,
                capture_sha256=image_hash,
            )
            if existing_review_id is None:
                raise _business_error(
                    status.HTTP_409_CONFLICT,
                    "capture_pending",
                    "A matching upload is still being created.",
                )
            _delete_temp_path(temp_path)
            response.status_code = status.HTTP_200_OK
            return ExtensionReviewCreateResponse(
                review_id=existing_review_id,
                status=review_store.get(existing_review_id).status,
                created_at=review_store.get(existing_review_id).created_at,
                expires_at=review_store.get(existing_review_id).expires_at,
                deduplicated=True,
            )
        if not reservation.reserved and reservation.review_id is not None:
            _delete_temp_path(temp_path)
            existing = review_store.get(reservation.review_id)
            response.status_code = status.HTTP_200_OK
            return ExtensionReviewCreateResponse(
                review_id=existing.review_id,
                status=existing.status,
                created_at=existing.created_at,
                expires_at=existing.expires_at,
                deduplicated=True,
            )
        reserved_capture = True
        record = create_review_record(image=image, source_metadata=metadata)
        pairing_store.bind_capture(
            pairing_id=pairing.pairing_id,
            capture_sha256=image_hash,
            review_id=record.review_id,
        )
    except ImageValidationError as exc:
        _delete_temp_path(temp_path)
        raise _business_error(_image_error_status(exc.code), exc.code, exc.message) from exc
    except SourceMetadataError as exc:
        _delete_temp_path(temp_path)
        raise _business_error(status.HTTP_400_BAD_REQUEST, exc.code, exc.message) from exc
    except ReviewStoreFullError as exc:
        if reserved_capture:
            pairing_store.rollback_capture(pairing_id=pairing.pairing_id, capture_sha256=image_hash)
        _delete_temp_path(temp_path)
        raise _business_error(status.HTTP_409_CONFLICT, "review_store_full", "The local review store is full.") from exc
    except HTTPException:
        _delete_temp_path(temp_path)
        raise
    except Exception:
        if reserved_capture:
            pairing_store.rollback_capture(pairing_id=pairing.pairing_id, capture_sha256=image_hash)
        _delete_temp_path(temp_path)
        raise

    background_tasks.add_task(process_review_image, record.review_id, temp_path)
    return ExtensionReviewCreateResponse(
        review_id=record.review_id,
        status=record.status,
        created_at=record.created_at,
        expires_at=record.expires_at,
        deduplicated=False,
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


def _pairing_business_error(exc: PairingError) -> HTTPException:
    status_code = status.HTTP_400_BAD_REQUEST
    payload: dict[str, object] = {"code": exc.code, "message": exc.message}
    if isinstance(exc, RateLimitExceeded):
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
        payload["retry_after_seconds"] = exc.retry_after_seconds
    elif exc.code in {"extension_token_required", "extension_token_invalid"}:
        status_code = status.HTTP_401_UNAUTHORIZED
    elif exc.code == "extension_token_revoked":
        status_code = status.HTTP_403_FORBIDDEN
    elif exc.code == "pairing_store_full":
        status_code = status.HTTP_409_CONFLICT
    elif exc.code == "pairing_not_found":
        status_code = status.HTTP_404_NOT_FOUND
    return HTTPException(status_code=status_code, detail=payload)


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise PairingError("extension_token_required", "Extension token is required.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise PairingError("extension_token_required", "Extension token is required.")
    return token.strip()


def _require_loopback_request(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host == "testclient":
        return
    if host == "::1" or host == "localhost" or host.startswith("127."):
        return
    raise _business_error(
        status.HTTP_403_FORBIDDEN,
        "extension_loopback_required",
        "Extension uploads are accepted only from loopback clients.",
    )


def _require_local_management_request(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        raise _business_error(
            status.HTTP_403_FORBIDDEN,
            "local_management_origin_required",
            "Local management requests require an allowed Origin header.",
        )
    if _normalize_origin(origin) not in _allowed_management_origins():
        raise _business_error(
            status.HTTP_403_FORBIDDEN,
            "local_management_origin_denied",
            "Local management request Origin is not allowed.",
        )


def _allowed_management_origins() -> set[str]:
    allowed: set[str] = set()
    for origin in parse_cors_allowed_origins(get_settings().cors_allowed_origins):
        normalized = _normalize_origin(origin)
        if not normalized:
            continue
        allowed.add(normalized)
        parsed = urlsplit(normalized)
        if parsed.hostname == "localhost":
            allowed.add(_replace_origin_host(parsed, "127.0.0.1"))
        elif parsed.hostname == "127.0.0.1":
            allowed.add(_replace_origin_host(parsed, "localhost"))
    return allowed


def _normalize_origin(origin: str) -> str:
    try:
        parsed = urlsplit(origin.strip().rstrip("/"))
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return _replace_origin_host(parsed, parsed.hostname)


def _replace_origin_host(parsed: object, host: str) -> str:
    port = getattr(parsed, "port", None)
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((getattr(parsed, "scheme"), netloc, "", "", ""))


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _enforce_content_length(request: Request) -> None:
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        length = int(raw)
    except ValueError:
        return
    if length > MAX_MULTIPART_BODY_BYTES:
        raise _business_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "image_too_large",
            "Multipart upload body exceeds the local recognition limit.",
        )


def _delete_temp_path(path: object) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass
