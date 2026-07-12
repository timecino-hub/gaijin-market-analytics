from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote_to_bytes, urlsplit, urlunsplit

from api.schemas.local_recognition import ReviewSourceMetadata, SafePageIdentity


MAX_SOURCE_URL_LENGTH = 2048
MAX_SOURCE_TITLE_LENGTH = 200
MAX_EXTENSION_VERSION_LENGTH = 64
MAX_CAPTURE_DURATION = timedelta(seconds=30)
MAX_CAPTURE_FUTURE_SKEW = timedelta(minutes=5)
MAX_CAPTURE_AGE = timedelta(minutes=15)
MAX_ITEM_KEY_LENGTH = 512


class SourceMetadataError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def manual_source_metadata() -> ReviewSourceMetadata:
    return ReviewSourceMetadata(source="manual_upload")


def extension_source_metadata(
    *,
    pairing_id: str,
    capture_sha256: str,
    extension_version: str | None,
    source_url: str | None,
    source_tab_title: str | None,
    client_capture_id: str | None = None,
    capture_started_at: str | None = None,
    captured_at: str | None = None,
    page_identity: str | None = None,
    received_at: datetime | None = None,
) -> ReviewSourceMetadata:
    clean_extension_version = _clean_optional_text(
        extension_version,
        max_length=MAX_EXTENSION_VERSION_LENGTH,
        code="source_title_too_long",
        label="Extension version",
    )
    clean_title = _clean_optional_text(
        source_tab_title,
        max_length=MAX_SOURCE_TITLE_LENGTH,
        code="source_title_too_long",
        label="Source tab title",
    )
    capture_fields = _capture_bundle_fields(
        client_capture_id=client_capture_id,
        capture_started_at=capture_started_at,
        captured_at=captured_at,
        page_identity=page_identity,
        received_at=received_at or datetime.now(UTC),
    )
    safe_source_url = sanitize_source_url(source_url)
    identity = capture_fields.get("page_identity")
    if isinstance(identity, SafePageIdentity):
        canonical_url = f"{identity.origin}{identity.market_path}"
        if safe_source_url != canonical_url:
            raise SourceMetadataError(
                "capture_page_identity_mismatch",
                "source_url and page_identity must identify the same public page.",
            )
    return ReviewSourceMetadata(
        source="browser_extension",
        extension_version=clean_extension_version,
        source_url_safe=safe_source_url,
        source_tab_title=clean_title,
        capture_sha256=capture_sha256,
        pairing_id=pairing_id,
        **capture_fields,
    )


def _capture_bundle_fields(
    *,
    client_capture_id: str | None,
    capture_started_at: str | None,
    captured_at: str | None,
    page_identity: str | None,
    received_at: datetime,
) -> dict[str, object]:
    values = (client_capture_id, capture_started_at, captured_at, page_identity)
    if not any(value is not None for value in values):
        return {}
    if not all(value is not None for value in values):
        raise SourceMetadataError("capture_bundle_incomplete", "All point-in-time capture fields are required.")
    try:
        capture_uuid = uuid.UUID(str(client_capture_id))
    except ValueError as exc:
        raise SourceMetadataError("client_capture_id_invalid", "client_capture_id must be a UUID v4.") from exc
    if capture_uuid.version != 4:
        raise SourceMetadataError("client_capture_id_invalid", "client_capture_id must be a UUID v4.")
    started = _aware_utc(str(capture_started_at), "capture_started_at")
    captured = _aware_utc(str(captured_at), "captured_at")
    if started > captured:
        raise SourceMetadataError("capture_time_order_invalid", "capture_started_at must not be later than captured_at.")
    duration = captured - started
    if duration > MAX_CAPTURE_DURATION:
        raise SourceMetadataError("capture_duration_too_long", "Screenshot capture duration exceeds 30 seconds.")
    received_at = received_at.astimezone(UTC)
    if captured > received_at + MAX_CAPTURE_FUTURE_SKEW:
        raise SourceMetadataError("captured_at_too_late", "captured_at is too far in the future.")
    if captured < received_at - MAX_CAPTURE_AGE:
        raise SourceMetadataError("captured_at_too_early", "captured_at is too old for an immediate upload.")
    identity = _safe_page_identity(str(page_identity))
    return {
        "capture_schema_version": "point_in_time_capture_v1",
        "client_capture_id": str(capture_uuid),
        "capture_started_at": started,
        "captured_at": captured,
        "capture_duration_ms": int(duration.total_seconds() * 1000),
        "page_identity": identity,
        "observation_time_semantics": "browser_captured_at",
    }


def _aware_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceMetadataError(f"{field}_invalid", f"{field} must be RFC3339.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceMetadataError(f"{field}_timezone_required", f"{field} must include a timezone.")
    return parsed.astimezone(UTC)


def _safe_page_identity(value: str) -> SafePageIdentity:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SourceMetadataError("page_identity_invalid", "page_identity must be valid JSON.") from exc
    if not isinstance(payload, dict) or set(payload) - {"origin", "market_path", "item_key"}:
        raise SourceMetadataError("page_identity_invalid", "page_identity contains unexpected fields.")
    origin = payload.get("origin")
    path = payload.get("market_path")
    if origin != "https://trade.gaijin.net" or not isinstance(path, str):
        raise SourceMetadataError("page_identity_invalid", "page_identity origin or path is invalid.")
    if "?" in path or "#" in path or not path.startswith("/market/1067/") or len(path) > 1024:
        raise SourceMetadataError("page_identity_invalid", "page_identity must contain a safe market path.")
    tail = path.removeprefix("/market/1067/").strip("/")
    index = 0
    while index < len(tail):
        if tail[index] == "%":
            if index + 2 >= len(tail) or any(char not in "0123456789abcdefABCDEF" for char in tail[index + 1 : index + 3]):
                raise SourceMetadataError("page_identity_item_key_encoding_invalid", "item_key contains an invalid percent escape.")
            index += 3
        else:
            index += 1
    try:
        derived = unquote_to_bytes(tail).decode("utf-8", errors="strict") if tail else None
    except UnicodeDecodeError as exc:
        raise SourceMetadataError("page_identity_item_key_encoding_invalid", "item_key encoding is invalid.") from exc
    if derived is not None and len(derived) > MAX_ITEM_KEY_LENGTH:
        raise SourceMetadataError("page_identity_item_key_too_long", "item_key exceeds the 512 character limit.")
    supplied = payload.get("item_key")
    if supplied is not None and (not isinstance(supplied, str) or supplied != derived):
        raise SourceMetadataError("page_identity_item_key_invalid", "item_key must be derived from the market path.")
    return SafePageIdentity(origin=origin, market_path=path, item_key=derived)


def sanitize_source_url(source_url: str | None) -> str | None:
    value = (source_url or "").strip()
    if not value:
        return None
    if len(value) > MAX_SOURCE_URL_LENGTH:
        raise SourceMetadataError("source_url_too_long", "Source URL is too long.")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise SourceMetadataError("source_url_invalid", "Source URL is invalid.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SourceMetadataError("source_url_invalid", "Source URL must be an HTTP or HTTPS URL.")
    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    safe = urlunsplit((parsed.scheme, netloc, parsed.path or "/", "", ""))
    if len(safe) > MAX_SOURCE_URL_LENGTH:
        raise SourceMetadataError("source_url_too_long", "Source URL is too long.")
    return safe


def _clean_optional_text(
    value: str | None,
    *,
    max_length: int,
    code: str,
    label: str,
) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise SourceMetadataError(code, f"{label} is too long.")
    return cleaned
