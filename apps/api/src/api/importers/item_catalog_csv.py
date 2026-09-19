from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from io import StringIO

CATALOG_SCHEMA_VERSION = "item_catalog_csv_v1"
CATALOG_FIELDS = ("external_key", "name", "category", "rarity", "is_active")
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_CATALOG_ROWS = 10_000


class ItemCatalogContractError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ItemCatalogRow:
    external_key: str
    name: str
    category: str
    rarity: str | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class ItemCatalogDocument:
    rows: tuple[ItemCatalogRow, ...]
    source_file_sha256: str
    normalized_catalog_sha256: str


def parse_item_catalog_csv(content: bytes) -> ItemCatalogDocument:
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    if not content:
        raise ItemCatalogContractError("empty_file")
    if len(content) > MAX_CATALOG_BYTES:
        raise ItemCatalogContractError("file_too_large")
    try:
        decoded = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ItemCatalogContractError("invalid_utf8") from exc
    if not decoded.strip():
        raise ItemCatalogContractError("empty_file")

    try:
        reader = csv.DictReader(StringIO(decoded), strict=True)
        if tuple(reader.fieldnames or ()) != CATALOG_FIELDS:
            raise ItemCatalogContractError("header_invalid")
        rows: list[ItemCatalogRow] = []
        seen: set[str] = set()
        for raw in reader:
            if len(rows) >= MAX_CATALOG_ROWS:
                raise ItemCatalogContractError("too_many_rows")
            if None in raw:
                raise ItemCatalogContractError("row_shape_invalid")
            external_key = _text(raw.get("external_key"), "external_key", 1024)
            if external_key in seen:
                raise ItemCatalogContractError("external_key_duplicate")
            seen.add(external_key)
            rows.append(
                ItemCatalogRow(
                    external_key=external_key,
                    name=_text(raw.get("name"), "name", 512),
                    category=_text(raw.get("category"), "category", 255),
                    rarity=_optional_text(raw.get("rarity"), "rarity", 255),
                    is_active=_boolean(raw.get("is_active")),
                )
            )
    except csv.Error as exc:
        raise ItemCatalogContractError("csv_invalid") from exc

    if not rows:
        raise ItemCatalogContractError("empty_catalog")
    normalized = json.dumps(
        {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "items": [
                {
                    "category": row.category,
                    "external_key": row.external_key,
                    "is_active": row.is_active,
                    "name": row.name,
                    "rarity": row.rarity,
                }
                for row in sorted(rows, key=lambda row: row.external_key)
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return ItemCatalogDocument(
        rows=tuple(rows),
        source_file_sha256=hashlib.sha256(content).hexdigest(),
        normalized_catalog_sha256=hashlib.sha256(normalized).hexdigest(),
    )


def _text(value: str | None, field: str, maximum: int) -> str:
    normalized = "" if value is None else value.strip()
    if not normalized:
        raise ItemCatalogContractError(f"{field}_required")
    if len(normalized) > maximum or _has_unsafe_text(normalized):
        raise ItemCatalogContractError(f"{field}_invalid")
    return normalized


def _optional_text(value: str | None, field: str, maximum: int) -> str | None:
    normalized = "" if value is None else value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum or _has_unsafe_text(normalized):
        raise ItemCatalogContractError(f"{field}_invalid")
    return normalized


def _boolean(value: str | None) -> bool:
    normalized = "" if value is None else value.strip()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ItemCatalogContractError("is_active_invalid")


def _has_unsafe_text(value: str) -> bool:
    return any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value
    )
