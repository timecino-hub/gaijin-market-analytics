from __future__ import annotations

import tempfile
import time
import uuid
import warnings
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path, PurePath
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Item
from api.schemas.local_recognition import (
    ImageMetadata,
    IdentityFieldSource,
    ItemIdentity,
    ObservedAtSource,
    OcrCandidate,
    OcrEvidenceSummary,
    RecognitionMetadata,
    ReviewConfirmRequest,
    ReviewDraft,
    ReviewPatchRequest,
    ReviewStatus,
)
from api.screen_recognition.config import default_current_cut_config
from api.screen_recognition.contracts import CUT_RUNNER_VERSION, PARSER_VERSION
from api.screen_recognition.image_io import ImageReadError, read_image_info
from api.screen_recognition.layouts import LayoutUnsupportedError, get_layout_profile, validate_layout_match
from api.screen_recognition.ocr_backend import (
    OcrBackendError,
    OcrBackendNotConfiguredError,
    OcrInvocation,
    get_recognizer,
)
from api.screen_recognition.parser import parse_ocr_contract
from api.services.items import ItemNotFoundError
from api.services.local_recognition_store import (
    ReviewRecord,
    compute_edited_fields,
    make_candidate,
    review_store,
)


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
SUPPORTED_FORMATS = ("png", "jpeg")
DEFAULT_LAYOUT_PROFILE = "gaijin-market-desktop-v1"
DEFAULT_OCR_BACKEND = "windows-ocr"


class LocalRecognitionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ImageValidationError(LocalRecognitionError):
    pass


def validate_image_upload(*, filename: str | None, content: bytes) -> tuple[ImageMetadata, Path]:
    safe_filename = _safe_upload_basename(filename)
    if len(content) > MAX_IMAGE_BYTES:
        raise ImageValidationError("image_too_large", "Image uploads are limited to 10 MB.")
    if not content:
        raise ImageValidationError("unsupported_image_format", "Image content is empty.")

    declared_format = _declared_format(safe_filename)
    actual_format = _signature_format(content)
    if actual_format is None:
        raise ImageValidationError("unsupported_image_format", "Only PNG and JPEG images are supported.")
    if declared_format != actual_format:
        raise ImageValidationError(
            "image_signature_mismatch",
            "Image extension and file signature do not match.",
        )
    width, height = _decode_dimensions(content)
    if width * height > MAX_IMAGE_PIXELS:
        raise ImageValidationError(
            "image_dimensions_too_large",
            "Decoded image dimensions exceed the 40 MP limit.",
        )

    temp = tempfile.NamedTemporaryFile(
        prefix="screen-review-",
        suffix=".png" if actual_format == "png" else ".jpg",
        delete=False,
    )
    try:
        temp.write(content)
        temp.flush()
        temp.close()
    except Exception:
        temp.close()
        _delete_temp_file(Path(temp.name))
        raise

    return (
        ImageMetadata(
            original_filename=safe_filename,
            width=width,
            height=height,
            format=actual_format,
        ),
        Path(temp.name),
    )


def create_processing_review(*, image: ImageMetadata) -> ReviewRecord:
    created_at = datetime.now(UTC)
    config = default_current_cut_config(
        layout_profile_name=DEFAULT_LAYOUT_PROFILE,
        ocr_backend_name=DEFAULT_OCR_BACKEND,
    )
    profile = get_layout_profile(DEFAULT_LAYOUT_PROFILE)
    return review_store.create_processing(
        review_id=f"review_{uuid.uuid4().hex}",
        created_at=created_at,
        image=image,
        recognition=RecognitionMetadata(
            ocr_backend=DEFAULT_OCR_BACKEND,
            ocr_backend_version="pending",
            layout_name=profile.name,
            layout_version=profile.version,
            config_sha256=config.sha256(),
            parser_version=PARSER_VERSION,
            runner_version=CUT_RUNNER_VERSION,
            processing_duration_ms=None,
        ),
    )


def process_review_image(review_id: str, image_path: Path) -> None:
    started = time.perf_counter()
    config = default_current_cut_config(
        layout_profile_name=DEFAULT_LAYOUT_PROFILE,
        ocr_backend_name=DEFAULT_OCR_BACKEND,
    )
    profile = get_layout_profile(DEFAULT_LAYOUT_PROFILE)
    recognizer = get_recognizer(DEFAULT_OCR_BACKEND)
    recognition = RecognitionMetadata(
        ocr_backend=recognizer.backend_name,
        ocr_backend_version=recognizer.backend_version,
        layout_name=profile.name,
        layout_version=profile.version,
        config_sha256=config.sha256(),
        parser_version=PARSER_VERSION,
        runner_version=CUT_RUNNER_VERSION,
        processing_duration_ms=None,
    )
    try:
        image_info = read_image_info(image_path)
        validate_layout_match(profile, image_info)
        ocr_result = recognizer.recognize(
            OcrInvocation(
                image_path=image_path,
                layout_profile=profile,
                debug_artifacts_dir=None,
            )
        )
        contract, parse_warnings, parse_errors = parse_ocr_contract(
            ocr_result.fields,
            item_key=None,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        recognition = recognition.model_copy(update={"processing_duration_ms": duration_ms})
        review_store.mark_recognized(
            review_id,
            status=ReviewStatus.PENDING_REVIEW,
            ocr_candidate=OcrCandidate(
                item_name_raw=(ocr_result.fields.get("item_name").raw_text if ocr_result.fields.get("item_name") else None),
                item_name_normalized=contract.item_name,
                best_bid=contract.best_bid,
                best_ask=contract.best_ask,
                total_bid_quantity=contract.total_bid_quantity,
                total_ask_quantity=contract.total_ask_quantity,
            ),
            recognition=recognition,
            ocr_evidence_summary=_evidence_summary(ocr_result.fields),
            warnings=[*ocr_result.warnings, *parse_warnings],
            errors=parse_errors,
        )
    except (ImageReadError, LayoutUnsupportedError):
        duration_ms = int((time.perf_counter() - started) * 1000)
        review_store.mark_failed(
            review_id,
            recognition=recognition.model_copy(update={"processing_duration_ms": duration_ms}),
            errors=["unsupported_layout"],
            unreadable=True,
        )
    except (OcrBackendNotConfiguredError, OcrBackendError):
        duration_ms = int((time.perf_counter() - started) * 1000)
        review_store.mark_failed(
            review_id,
            recognition=recognition.model_copy(update={"processing_duration_ms": duration_ms}),
            errors=["recognition_failed"],
        )
    except Exception:
        duration_ms = int((time.perf_counter() - started) * 1000)
        review_store.mark_failed(
            review_id,
            recognition=recognition.model_copy(update={"processing_duration_ms": duration_ms}),
            errors=["recognition_failed"],
        )
    finally:
        _delete_temp_file(image_path)


def patch_review_draft(
    record: ReviewRecord,
    request: ReviewPatchRequest,
    *,
    identity_source: IdentityFieldSource = IdentityFieldSource.USER_DRAFT,
) -> ReviewDraft:
    fields_set = request.model_fields_set
    observed_at = (
        request.observed_at
        if "observed_at" in fields_set
        else record.draft.observed_at or record.suggested_observed_at
    )
    observed_source = (
        ObservedAtSource.USER_EDITED
        if "observed_at" in fields_set
        and request.observed_at is not None
        and request.observed_at != record.suggested_observed_at
        else record.draft.observed_at_source
    )
    selected_item_id = _field_value(request, "selected_item_id", record.draft.selected_item_id)
    item_key = _blank_to_none(_field_value(request, "item_key", record.draft.item_key))
    final_item_name = _blank_to_none(
        _field_value(request, "final_item_name", record.draft.final_item_name)
    )
    identity_sources = record.draft.identity_sources.model_copy(
        update={
            "selected_item_id": _updated_identity_source(
                request,
                "selected_item_id",
                selected_item_id,
                record.draft.identity_sources.selected_item_id,
                identity_source,
            ),
            "item_key": _updated_identity_source(
                request,
                "item_key",
                item_key,
                record.draft.identity_sources.item_key,
                identity_source,
            ),
            "final_item_name": _updated_identity_source(
                request,
                "final_item_name",
                final_item_name,
                record.draft.identity_sources.final_item_name,
                identity_source,
            ),
        }
    )
    draft = record.draft.model_copy(
        update={
            "selected_item_id": selected_item_id,
            "item_key": item_key,
            "final_item_name": final_item_name,
            "identity_sources": identity_sources,
            "final_best_bid": _field_value(request, "final_best_bid", record.draft.final_best_bid),
            "final_best_ask": _field_value(request, "final_best_ask", record.draft.final_best_ask),
            "final_total_bid_quantity": _field_value(
                request,
                "final_total_bid_quantity",
                record.draft.final_total_bid_quantity,
            ),
            "final_total_ask_quantity": _field_value(
                request,
                "final_total_ask_quantity",
                record.draft.final_total_ask_quantity,
            ),
            "observed_at": observed_at,
            "observed_at_source": observed_source,
            "reviewer_note": _blank_to_none(
                _field_value(request, "reviewer_note", record.draft.reviewer_note)
            ),
        }
    )
    _validate_market_price(draft.final_best_bid)
    _validate_market_price(draft.final_best_ask)
    return draft


async def confirm_review(
    *,
    session: AsyncSession,
    record: ReviewRecord,
    request: ReviewConfirmRequest,
) -> ReviewRecord:
    draft = patch_review_draft(
        record,
        request,
        identity_source=IdentityFieldSource.CONFIRM_REQUEST,
    )
    identity = await _resolve_identity(session, draft)
    if draft.selected_item_id is not None:
        draft = _apply_canonical_identity(draft, identity)
    _validate_confirm_values(draft)
    edited_fields = compute_edited_fields(record, draft)
    status = "confirmed_with_edits" if edited_fields else "confirmed"
    candidate = make_candidate(
        record=record,
        draft=draft,
        identity=identity,
        edited_fields=edited_fields,
        status=status,
    )
    return review_store.confirm(
        record.review_id,
        draft=draft,
        candidate=candidate,
        edited_fields=edited_fields,
        confirmed_at=datetime.now(UTC),
    )


def capabilities_payload() -> dict[str, Any]:
    config = default_current_cut_config(
        layout_profile_name=DEFAULT_LAYOUT_PROFILE,
        ocr_backend_name=DEFAULT_OCR_BACKEND,
    )
    profile = get_layout_profile(DEFAULT_LAYOUT_PROFILE)
    recognizer = get_recognizer(DEFAULT_OCR_BACKEND)
    return {
        "ocr_backend": recognizer.backend_name,
        "ocr_backend_version": recognizer.backend_version,
        "installed_ocr_languages": list(config.ocr_languages),
        "current_layout_profile": profile.name,
        "layout_version": profile.version,
        "config_sha256": config.sha256(),
        "max_image_bytes": MAX_IMAGE_BYTES,
        "max_image_pixels": MAX_IMAGE_PIXELS,
        "supported_image_formats": list(SUPPORTED_FORMATS),
        "store_capacity": review_store.max_reviews,
        "store_ttl_seconds": review_store.ttl_seconds,
        "database_written": False,
        "handles_history_images": False,
        "browser_extension_connected": False,
        "automatic_recognition_available": False,
    }


def _safe_upload_basename(filename: str | None) -> str:
    basename = PurePath(filename or "").name
    if not basename or basename in {".", ".."}:
        raise ImageValidationError("unsupported_image_format", "A PNG or JPEG filename is required.")
    if basename != filename:
        raise ImageValidationError("unsupported_image_format", "Upload filename must be a basename.")
    return basename


def _declared_format(filename: str) -> str:
    suffix = PurePath(filename).suffix.lower()
    if suffix == ".png":
        return "png"
    if suffix in {".jpg", ".jpeg"}:
        return "jpeg"
    raise ImageValidationError("unsupported_image_format", "Only PNG and JPEG images are supported.")


def _signature_format(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8"):
        return "jpeg"
    return None


def _decode_dimensions(content: bytes) -> tuple[int, int]:
    original_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                image.load()
                width, height = image.size
    except Image.DecompressionBombWarning as exc:
        raise ImageValidationError(
            "image_dimensions_too_large",
            "Decoded image dimensions exceed the 40 MP limit.",
        ) from exc
    except Exception as exc:
        raise ImageValidationError("unsupported_image_format", "Image could not be decoded.") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = original_limit
    if width <= 0 or height <= 0:
        raise ImageValidationError("unsupported_image_format", "Image dimensions are invalid.")
    return width, height


def _delete_temp_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _evidence_summary(fields: dict[str, Any]) -> OcrEvidenceSummary:
    return OcrEvidenceSummary(
        fields={
            name: {
                "raw_text": evidence.raw_text,
                "line_count": len(evidence.lines),
                "word_count": sum(len(line.words) for line in evidence.lines),
                "bounding_box": None
                if evidence.bounding_box is None
                else evidence.bounding_box.to_json(),
                "warnings": list(evidence.warnings),
            }
            for name, evidence in sorted(fields.items())
        },
        confidence_source="unavailable",
        confidence_available=False,
    )


async def _resolve_identity(session: AsyncSession, draft: ReviewDraft) -> ItemIdentity:
    has_selected = draft.selected_item_id is not None
    if has_selected and draft.item_key:
        raise LocalRecognitionError(
            "item_identity_conflict",
            "Choose an existing item or provide a manual identity, not both.",
        )
    if has_selected:
        item = await session.scalar(select(Item).where(Item.id == draft.selected_item_id).limit(1))
        if item is None:
            raise ItemNotFoundError(f"Item {draft.selected_item_id} was not found.")
        return ItemIdentity(item_id=item.id, item_key=item.external_key, item_name=item.name)
    if _has_explicit_manual_identity(draft):
        assert draft.item_key is not None
        assert draft.final_item_name is not None
        return ItemIdentity(item_id=None, item_key=draft.item_key, item_name=draft.final_item_name)
    raise LocalRecognitionError(
        "item_identity_required",
        "Manual identity requires an explicitly provided item_key and final_item_name.",
    )


def _validate_confirm_values(draft: ReviewDraft) -> None:
    _validate_market_price(draft.final_best_bid)
    _validate_market_price(draft.final_best_ask)
    if draft.final_best_bid is None or draft.final_best_ask is None:
        raise LocalRecognitionError("invalid_price_string", "Best bid and best ask are required.")
    for value in (draft.final_total_bid_quantity, draft.final_total_ask_quantity):
        if value is not None and value < 0:
            raise LocalRecognitionError("invalid_quantity", "Quantity must be a non-negative integer or null.")


def _validate_market_price(value: Decimal | None) -> None:
    if value is None:
        return
    if value <= Decimal("0") or value > Decimal("2000.00"):
        raise LocalRecognitionError(
            "price_out_of_market_range",
            "Price must satisfy 0 < price <= 2000.00.",
        )


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _field_value(request: ReviewPatchRequest, name: str, fallback: Any) -> Any:
    if name in request.model_fields_set:
        return getattr(request, name)
    return fallback


def _updated_identity_source(
    request: ReviewPatchRequest,
    name: str,
    value: object,
    fallback: IdentityFieldSource | None,
    identity_source: IdentityFieldSource,
) -> IdentityFieldSource | None:
    if name not in request.model_fields_set:
        return fallback
    return identity_source if value is not None else None


def _has_explicit_manual_identity(draft: ReviewDraft) -> bool:
    if not draft.item_key or not draft.final_item_name:
        return False
    explicit_sources = {
        IdentityFieldSource.USER_DRAFT,
        IdentityFieldSource.CONFIRM_REQUEST,
    }
    return (
        draft.identity_sources.item_key in explicit_sources
        and draft.identity_sources.final_item_name in explicit_sources
    )


def _apply_canonical_identity(draft: ReviewDraft, identity: ItemIdentity) -> ReviewDraft:
    return draft.model_copy(
        update={
            "item_key": identity.item_key,
            "final_item_name": identity.item_name,
            "identity_sources": draft.identity_sources.model_copy(
                update={
                    "item_key": IdentityFieldSource.CANONICAL_ITEM,
                    "final_item_name": IdentityFieldSource.CANONICAL_ITEM,
                }
            ),
        }
    )
