from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from api.schemas.local_recognition import ReviewSourceMetadata


MAX_SOURCE_URL_LENGTH = 2048
MAX_SOURCE_TITLE_LENGTH = 200
MAX_EXTENSION_VERSION_LENGTH = 64


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
    return ReviewSourceMetadata(
        source="browser_extension",
        extension_version=clean_extension_version,
        source_url_safe=sanitize_source_url(source_url),
        source_tab_title=clean_title,
        capture_sha256=capture_sha256,
        pairing_id=pairing_id,
    )


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
