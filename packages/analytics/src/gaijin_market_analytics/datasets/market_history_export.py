from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from gaijin_market_analytics.backtesting.calibration_contracts import ItemMarketHistory
from gaijin_market_analytics.contracts import MarketObservation


MARKET_HISTORY_EXPORT_SCHEMA_VERSION = "round6_market_history_export_v1"
ITEM_MAPPING_SCHEMA_VERSION = "round6_item_mapping_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MarketHistoryExportValidationError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class MarketHistoryQualityTier(str, Enum):
    BROWSER_CAPTURED = "browser_captured"
    BROWSER_CAPTURED_USER_TIME_EDITED = "browser_captured_user_time_edited"
    LEGACY_SERVER_TIME = "legacy_server_time"
    UNKNOWN_OR_INCOMPLETE = "unknown_or_incomplete"


class MarketHistoryExportProfile(str, Enum):
    POINT_IN_TIME_PRIMARY = "point_in_time_primary"
    POINT_IN_TIME_REVIEWED = "point_in_time_reviewed"
    QUOTE_ONLY_REVIEWED = "quote_only_reviewed"
    ALL_VALID_REVIEWED = "all_valid_reviewed"


@dataclass(frozen=True, slots=True)
class ItemMappingEntry:
    offline_sample_key: str
    database_item_id: int
    database_external_key: str
    confirmed_by_user: bool

    def __post_init__(self) -> None:
        sample_key = self.offline_sample_key.strip()
        external_key = self.database_external_key.strip()
        if not sample_key:
            _fail("item_mapping_sample_key_invalid")
        if (
            isinstance(self.database_item_id, bool)
            or not isinstance(self.database_item_id, int)
            or self.database_item_id <= 0
        ):
            _fail("item_mapping_database_item_id_invalid")
        if not external_key:
            _fail("item_mapping_external_key_invalid")
        if self.confirmed_by_user is not True:
            _fail("item_mapping_confirmation_required")
        object.__setattr__(self, "offline_sample_key", sample_key)
        object.__setattr__(self, "database_external_key", external_key)


@dataclass(frozen=True, slots=True)
class ItemMappingManifest:
    schema_version: str
    mappings: tuple[ItemMappingEntry, ...]


@dataclass(frozen=True, slots=True)
class MarketHistorySourceRecord:
    item_id: int
    item_external_key: str
    item_name: str
    item_category: str
    item_rarity: str | None
    item_is_active: bool
    observed_at: datetime
    best_bid: Decimal | None
    best_ask: Decimal
    bid_count: int | None
    ask_count: int | None
    estimated_volume: Decimal | None
    observed_bid_quantity: int | None
    observed_ask_quantity: int | None
    quantity_semantics: str
    source_type: str
    source_version: str
    review_status: str
    source_review_id: str
    candidate_version: str
    candidate_sha256: str
    quality_tier: MarketHistoryQualityTier
    observed_at_source: str
    incomplete_quantities_acknowledged: bool
    edited_fields: tuple[str, ...]
    capture_schema_version: str
    client_capture_id: str | None
    capture_started_at: datetime | None
    captured_at: datetime | None
    page_identity: Mapping[str, Any] | None
    capture_sha256: str | None
    extension_version: str | None
    imported_at: datetime | None
    page_item_key_matches_database_item: bool | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.item_id, bool)
            or not isinstance(self.item_id, int)
            or self.item_id <= 0
        ):
            _fail("source_item_id_invalid")
        if not isinstance(self.item_is_active, bool):
            _fail("source_item_is_active_invalid")
        if not isinstance(self.incomplete_quantities_acknowledged, bool):
            _fail("source_acknowledgement_invalid")
        if self.page_item_key_matches_database_item is not None and not isinstance(
            self.page_item_key_matches_database_item, bool
        ):
            _fail("source_page_item_match_invalid")
        for field_name in (
            "item_external_key",
            "item_name",
            "item_category",
            "quantity_semantics",
            "source_type",
            "source_version",
            "review_status",
            "source_review_id",
            "candidate_version",
            "observed_at_source",
            "capture_schema_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                _fail(f"source_{field_name}_invalid")
        if not _SHA256_RE.fullmatch(self.candidate_sha256):
            _fail("source_candidate_sha256_invalid")
        observed_at = _aware_utc(self.observed_at, "observed_at")
        capture_started_at = _optional_aware_utc(
            self.capture_started_at, "capture_started_at"
        )
        captured_at = _optional_aware_utc(self.captured_at, "captured_at")
        imported_at = _optional_aware_utc(self.imported_at, "imported_at")
        if capture_started_at is not None and captured_at is not None:
            if capture_started_at > captured_at:
                _fail("source_capture_time_order_invalid")
        for name in ("best_bid", "best_ask", "estimated_volume"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                _fail(f"source_{name}_invalid")
        if self.best_ask <= Decimal("0"):
            _fail("source_best_ask_invalid")
        if self.best_bid is not None and self.best_bid < Decimal("0"):
            _fail("source_best_bid_invalid")
        for name in (
            "bid_count",
            "ask_count",
            "observed_bid_quantity",
            "observed_ask_quantity",
        ):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or value < 0):
                _fail(f"source_{name}_invalid")
        if not isinstance(self.quality_tier, MarketHistoryQualityTier):
            _fail("source_quality_tier_invalid")
        if self.quantity_semantics != "screenshot_display_quantity":
            _fail("source_quantity_semantics_invalid")
        if self.source_type != "screen_review":
            _fail("source_type_invalid")
        if self.review_status not in {"confirmed", "confirmed_with_edits"}:
            _fail("source_review_status_invalid")
        if self.bid_count is not None or self.ask_count is not None:
            _fail("source_screen_review_legacy_count_present")
        if self.estimated_volume is not None:
            _fail("source_screen_review_estimated_volume_present")
        if self.page_item_key_matches_database_item is False:
            _fail("source_page_item_key_mismatch")
        edited_fields = tuple(sorted(set(self.edited_fields)))
        if any(not isinstance(value, str) or not value.strip() for value in edited_fields):
            _fail("source_edited_fields_invalid")
        page_identity = None
        if self.page_identity is not None:
            page_identity = _normalize_page_identity(self.page_identity)
        if self.capture_sha256 is not None and not _SHA256_RE.fullmatch(
            self.capture_sha256
        ):
            _fail("source_capture_sha256_invalid")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "capture_started_at", capture_started_at)
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "imported_at", imported_at)
        object.__setattr__(self, "edited_fields", edited_fields)
        object.__setattr__(self, "page_identity", page_identity)

    @property
    def observation_key(self) -> str:
        return f"review:{self.source_review_id}"


@dataclass(frozen=True, slots=True)
class LoadedMarketHistoryDataset:
    histories: tuple[ItemMarketHistory, ...]
    provenance_by_key: Mapping[str, Mapping[str, Any]]
    source_fingerprint: str
    typed_history_fingerprint: str
    export_profile: MarketHistoryExportProfile
    item_mapping_fingerprint: str | None
    diagnostics: Mapping[str, Any]
    item_identity_by_id: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)


def load_item_mapping_manifest(path: str | Path) -> ItemMappingManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail("item_mapping_file_not_found")
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("item_mapping_json_invalid")
    return parse_item_mapping_manifest(payload)


def parse_item_mapping_manifest(payload: object) -> ItemMappingManifest:
    if not isinstance(payload, dict):
        _fail("item_mapping_top_level_invalid")
    _require_exact_keys(payload, {"schema_version", "mappings"}, "item_mapping")
    if payload["schema_version"] != ITEM_MAPPING_SCHEMA_VERSION:
        _fail("item_mapping_schema_unsupported")
    raw_mappings = payload["mappings"]
    if not isinstance(raw_mappings, list):
        _fail("item_mapping_mappings_invalid")
    mappings: list[ItemMappingEntry] = []
    for index, raw in enumerate(raw_mappings):
        if not isinstance(raw, dict):
            _fail("item_mapping_entry_invalid", f"mapping {index} is not an object")
        _require_exact_keys(
            raw,
            {
                "offline_sample_key",
                "database_item_id",
                "database_external_key",
                "confirmed_by_user",
            },
            "item_mapping_entry",
        )
        if isinstance(raw["database_item_id"], bool) or not isinstance(
            raw["database_item_id"], int
        ):
            _fail("item_mapping_database_item_id_invalid")
        mappings.append(
            ItemMappingEntry(
                offline_sample_key=_required_string(
                    raw["offline_sample_key"], "item_mapping_sample_key_invalid"
                ),
                database_item_id=raw["database_item_id"],
                database_external_key=_required_string(
                    raw["database_external_key"], "item_mapping_external_key_invalid"
                ),
                confirmed_by_user=raw["confirmed_by_user"],
            )
        )
    for field_name, code in (
        ("offline_sample_key", "item_mapping_duplicate_sample_key"),
        ("database_item_id", "item_mapping_duplicate_database_item_id"),
        ("database_external_key", "item_mapping_duplicate_external_key"),
    ):
        values = [getattr(value, field_name) for value in mappings]
        if len(values) != len(set(values)):
            _fail(code)
    return ItemMappingManifest(
        schema_version=ITEM_MAPPING_SCHEMA_VERSION,
        mappings=tuple(sorted(mappings, key=lambda value: value.offline_sample_key)),
    )


def item_mapping_manifest_sha256(manifest: ItemMappingManifest) -> str:
    payload = {
        "schema_version": manifest.schema_version,
        "mappings": [
            {
                "offline_sample_key": entry.offline_sample_key,
                "database_item_id": entry.database_item_id,
                "database_external_key": entry.database_external_key,
                "confirmed_by_user": entry.confirmed_by_user,
            }
            for entry in sorted(manifest.mappings, key=lambda value: value.offline_sample_key)
        ],
    }
    return _sha256_json(payload)


def build_market_history_export(
    records: Iterable[MarketHistorySourceRecord],
    *,
    database_schema_revision: str,
    export_profile: MarketHistoryExportProfile | str = (
        MarketHistoryExportProfile.POINT_IN_TIME_PRIMARY
    ),
    item_mapping_fingerprint: str | None = None,
    source_record_count: int | None = None,
    exclusion_reason_counts: Mapping[str, int] | None = None,
    conflict_count: int = 0,
) -> dict[str, Any]:
    revision = _required_string(
        database_schema_revision, "database_schema_revision_invalid"
    )
    profile = _profile(export_profile)
    if item_mapping_fingerprint is not None and not _SHA256_RE.fullmatch(
        item_mapping_fingerprint
    ):
        _fail("item_mapping_fingerprint_invalid")
    supplied = tuple(sorted(tuple(records), key=_record_sort_key))
    ordered = _deduplicate_source_records(supplied)
    _validate_source_record_conflicts(ordered)
    included: list[MarketHistorySourceRecord] = []
    integrity_exclusions: dict[str, int] = dict(exclusion_reason_counts or {})
    exclusions: dict[str, int] = dict(integrity_exclusions)
    for reason, count in integrity_exclusions.items():
        if not isinstance(reason, str) or not reason.strip():
            _fail("market_history_exclusion_reason_invalid")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            _fail("market_history_exclusion_count_invalid")
    if (
        isinstance(conflict_count, bool)
        or not isinstance(conflict_count, int)
        or conflict_count < 0
    ):
        _fail("market_history_conflict_count_invalid")
    for record in ordered:
        reason = _profile_exclusion_reason(record, profile)
        if reason is None:
            included.append(record)
        else:
            exclusions[reason] = exclusions.get(reason, 0) + 1
    items = _build_items(included)
    provenance = {
        record.observation_key: _provenance_payload(record)
        for record in sorted(included, key=lambda value: value.observation_key)
    }
    total_source_count = len(ordered) + sum(integrity_exclusions.values())
    if source_record_count is not None:
        if (
            isinstance(source_record_count, bool)
            or not isinstance(source_record_count, int)
            or source_record_count < total_source_count
        ):
            _fail("market_history_source_record_count_invalid")
        total_source_count = source_record_count
    payload: dict[str, Any] = {
        "schema_version": MARKET_HISTORY_EXPORT_SCHEMA_VERSION,
        "database_schema_revision": revision,
        "export_profile": profile.value,
        "item_mapping_fingerprint": item_mapping_fingerprint,
        "source_fingerprint": "",
        "typed_history_fingerprint": "",
        "items": items,
        "provenance": provenance,
        "diagnostics": {
            "source_record_count": total_source_count,
            "included_record_count": len(included),
            "excluded_record_count": total_source_count - len(included),
            "conflict_count": conflict_count,
            "quality_tier_counts": _quality_tier_counts(ordered),
            "exclusion_reason_counts": dict(sorted(exclusions.items())),
        },
    }
    payload["source_fingerprint"] = market_observation_source_fingerprint(payload)
    payload["typed_history_fingerprint"] = typed_market_history_fingerprint(payload)
    return payload


def market_observation_source_fingerprint(payload: Mapping[str, Any]) -> str:
    normalized = _validate_export_payload(payload, verify_fingerprints=False)
    source_payload = {
        "schema_version": normalized["schema_version"],
        "database_schema_revision": normalized["database_schema_revision"],
        "export_profile": normalized["export_profile"],
        "item_mapping_fingerprint": normalized["item_mapping_fingerprint"],
        "items": normalized["items"],
        "provenance": {
            key: {
                name: value
                for name, value in provenance.items()
                if name != "imported_at"
            }
            for key, provenance in sorted(normalized["provenance"].items())
        },
    }
    return _sha256_json(source_payload)


def typed_market_history_fingerprint(payload: Mapping[str, Any]) -> str:
    normalized = _validate_export_payload(payload, verify_fingerprints=False)
    provenance = normalized["provenance"]
    typed_items: list[dict[str, Any]] = []
    for item in normalized["items"]:
        observations: list[dict[str, Any]] = []
        for observation in item["observations"]:
            source = provenance[observation["provenance_ref"]]
            observations.append(
                {
                    "observed_at": observation["observed_at"],
                    "best_bid_price": observation["best_bid_price"],
                    "best_ask_price": observation["best_ask_price"],
                    "bid_count": observation["bid_count"],
                    "ask_count": observation["ask_count"],
                    "estimated_volume": observation["estimated_volume"],
                    "observed_bid_quantity": observation["observed_bid_quantity"],
                    "observed_ask_quantity": observation["observed_ask_quantity"],
                    "quantity_semantics": observation["quantity_semantics"],
                    "source_type": observation["source_type"],
                    "review_status": observation["review_status"],
                    "quality_tier": observation["quality_tier"],
                    "observed_at_source": source["observed_at_source"],
                }
            )
        typed_items.append(
            {
                "item_id": item["item_id"],
                "observations": observations,
            }
        )
    return _sha256_json(
        {
            "schema_version": normalized["schema_version"],
            "export_profile": normalized["export_profile"],
            "item_mapping_fingerprint": normalized["item_mapping_fingerprint"],
            "items": typed_items,
        }
    )


def market_history_export_json_bytes(
    payload: Mapping[str, Any], *, pretty: bool = False
) -> bytes:
    normalized = _validate_export_payload(payload, verify_fingerprints=True)
    text = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    return f"{text}\n".encode("utf-8")


def load_market_history_export(path: str | Path) -> LoadedMarketHistoryDataset:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail("market_history_export_file_not_found")
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("market_history_export_json_invalid")
    return parse_market_history_export(payload)


def parse_market_history_export(payload: object) -> LoadedMarketHistoryDataset:
    normalized = _validate_export_payload(payload, verify_fingerprints=True)
    histories: list[ItemMarketHistory] = []
    seen_keys: set[str] = set()
    provenance = normalized["provenance"]
    for item in normalized["items"]:
        observations: list[MarketObservation] = []
        seen_times: set[str] = set()
        for raw in item["observations"]:
            observation_key = raw["observation_key"]
            if observation_key in seen_keys:
                _fail("market_history_duplicate_observation_key")
            seen_keys.add(observation_key)
            if raw["observed_at"] in seen_times:
                _fail("market_history_item_time_conflict")
            seen_times.add(raw["observed_at"])
            observations.append(
                MarketObservation(
                    observed_at=_parse_datetime(raw["observed_at"], "observed_at"),
                    best_ask=_parse_decimal(raw["best_ask_price"], "best_ask_price"),
                    best_bid=_parse_optional_decimal(
                        raw["best_bid_price"], "best_bid_price"
                    ),
                    ask_count=raw["ask_count"],
                    bid_count=raw["bid_count"],
                    estimated_volume=_parse_optional_decimal(
                        raw["estimated_volume"], "estimated_volume"
                    ),
                    observation_key=observation_key,
                    observed_ask_quantity=raw["observed_ask_quantity"],
                    observed_bid_quantity=raw["observed_bid_quantity"],
                    quantity_semantics=raw["quantity_semantics"],
                    source_type=raw["source_type"],
                    review_status=raw["review_status"],
                )
            )
            if raw["provenance_ref"] not in provenance:
                _fail("market_history_provenance_missing")
        histories.append(
            ItemMarketHistory(item_id=item["item_id"], observations=tuple(observations))
        )
    return LoadedMarketHistoryDataset(
        histories=tuple(histories),
        provenance_by_key=provenance,
        item_identity_by_id={
            item["item_id"]: {
                "external_key": item["external_key"],
                "name": item["name"],
                "category": item["category"],
                "rarity": item["rarity"],
                "is_active": item["is_active"],
            }
            for item in normalized["items"]
        },
        source_fingerprint=normalized["source_fingerprint"],
        typed_history_fingerprint=normalized["typed_history_fingerprint"],
        export_profile=MarketHistoryExportProfile(normalized["export_profile"]),
        item_mapping_fingerprint=normalized["item_mapping_fingerprint"],
        diagnostics=normalized["diagnostics"],
    )


def _build_items(records: list[MarketHistorySourceRecord]) -> list[dict[str, Any]]:
    grouped: dict[int, list[MarketHistorySourceRecord]] = {}
    for record in records:
        grouped.setdefault(record.item_id, []).append(record)
    items: list[dict[str, Any]] = []
    for item_id in sorted(grouped):
        values = grouped[item_id]
        first = values[0]
        for value in values[1:]:
            if _item_identity(value) != _item_identity(first):
                _fail("source_item_identity_conflict")
        items.append(
            {
                "item_id": item_id,
                "external_key": first.item_external_key,
                "name": first.item_name,
                "category": first.item_category,
                "rarity": first.item_rarity,
                "is_active": first.item_is_active,
                "observations": [_observation_payload(value) for value in values],
            }
        )
    return items


def _observation_payload(record: MarketHistorySourceRecord) -> dict[str, Any]:
    return {
        "observed_at": _datetime_text(record.observed_at),
        "best_bid_price": _optional_decimal_text(record.best_bid),
        "best_ask_price": _decimal_text(record.best_ask),
        "bid_count": record.bid_count,
        "ask_count": record.ask_count,
        "estimated_volume": _optional_decimal_text(record.estimated_volume),
        "observed_bid_quantity": record.observed_bid_quantity,
        "observed_ask_quantity": record.observed_ask_quantity,
        "quantity_semantics": record.quantity_semantics,
        "source_type": record.source_type,
        "review_status": record.review_status,
        "quality_tier": record.quality_tier.value,
        "observation_key": record.observation_key,
        "provenance_ref": record.observation_key,
    }


def _provenance_payload(record: MarketHistorySourceRecord) -> dict[str, Any]:
    return {
        "source_review_id": record.source_review_id,
        "candidate_version": record.candidate_version,
        "candidate_sha256": record.candidate_sha256,
        "client_capture_id": record.client_capture_id,
        "capture_schema_version": record.capture_schema_version,
        "capture_started_at": _optional_datetime_text(record.capture_started_at),
        "captured_at": _optional_datetime_text(record.captured_at),
        "final_observed_at": _datetime_text(record.observed_at),
        "observed_at_source": record.observed_at_source,
        "page_identity": record.page_identity,
        "capture_sha256": record.capture_sha256,
        "extension_version": record.extension_version,
        "incomplete_quantities_acknowledged": (
            record.incomplete_quantities_acknowledged
        ),
        "edited_fields": list(record.edited_fields),
        "quality_tier": record.quality_tier.value,
        "imported_at": _optional_datetime_text(record.imported_at),
        "page_item_key_matches_database_item": (
            record.page_item_key_matches_database_item
        ),
    }


def _validate_export_payload(
    payload: object, *, verify_fingerprints: bool
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        _fail("market_history_export_top_level_invalid")
    expected = {
        "schema_version",
        "database_schema_revision",
        "export_profile",
        "item_mapping_fingerprint",
        "source_fingerprint",
        "typed_history_fingerprint",
        "items",
        "provenance",
        "diagnostics",
    }
    _require_exact_keys(payload, expected, "market_history_export")
    if payload["schema_version"] != MARKET_HISTORY_EXPORT_SCHEMA_VERSION:
        _fail("market_history_export_schema_unsupported")
    revision = _required_string(
        payload["database_schema_revision"], "database_schema_revision_invalid"
    )
    profile = _profile(payload["export_profile"]).value
    mapping_fingerprint = payload["item_mapping_fingerprint"]
    if mapping_fingerprint is not None and not (
        isinstance(mapping_fingerprint, str)
        and _SHA256_RE.fullmatch(mapping_fingerprint)
    ):
        _fail("item_mapping_fingerprint_invalid")
    raw_provenance = payload["provenance"]
    if not isinstance(raw_provenance, Mapping):
        _fail("market_history_provenance_invalid")
    provenance: dict[str, dict[str, Any]] = {}
    for key in sorted(raw_provenance):
        if not isinstance(key, str) or not key.startswith("review:"):
            _fail("market_history_provenance_key_invalid")
        provenance[key] = _normalize_provenance(raw_provenance[key], key)
    raw_items = payload["items"]
    if not isinstance(raw_items, list):
        _fail("market_history_items_invalid")
    items = [_normalize_item(value, provenance) for value in raw_items]
    if [item["item_id"] for item in items] != sorted(
        item["item_id"] for item in items
    ):
        _fail("market_history_items_not_sorted")
    if len({item["item_id"] for item in items}) != len(items):
        _fail("market_history_duplicate_item")
    all_observations = [
        (item["item_id"], observation)
        for item in items
        for observation in item["observations"]
    ]
    observation_keys = [observation["observation_key"] for _, observation in all_observations]
    if len(observation_keys) != len(set(observation_keys)):
        _fail("market_history_duplicate_observation_key")
    item_times = [
        (item_id, observation["observed_at"])
        for item_id, observation in all_observations
    ]
    if len(item_times) != len(set(item_times)):
        _fail("market_history_item_time_conflict")
    if set(observation_keys) != set(provenance):
        _fail("market_history_provenance_set_mismatch")
    capture_ids = [
        value["client_capture_id"]
        for value in provenance.values()
        if value["client_capture_id"] is not None
    ]
    if len(capture_ids) != len(set(capture_ids)):
        _fail("market_history_client_capture_id_conflict")
    resolved_profile = MarketHistoryExportProfile(profile)
    for _, observation in all_observations:
        linked = provenance[observation["provenance_ref"]]
        if not _normalized_profile_allows(observation, linked, resolved_profile):
            _fail("market_history_profile_membership_invalid")
    diagnostics = _normalize_diagnostics(payload["diagnostics"])
    if diagnostics["included_record_count"] != len(all_observations):
        _fail("market_history_diagnostics_included_count_mismatch")
    if diagnostics["source_record_count"] < diagnostics["included_record_count"]:
        _fail("market_history_diagnostics_source_count_invalid")
    if diagnostics["excluded_record_count"] != (
        diagnostics["source_record_count"] - diagnostics["included_record_count"]
    ):
        _fail("market_history_diagnostics_excluded_count_mismatch")
    if diagnostics["conflict_count"] > diagnostics["excluded_record_count"]:
        _fail("market_history_diagnostics_conflict_count_invalid")
    if sum(diagnostics["exclusion_reason_counts"].values()) != diagnostics[
        "excluded_record_count"
    ]:
        _fail("market_history_diagnostics_exclusion_reasons_mismatch")
    for tier_name in diagnostics["quality_tier_counts"]:
        _quality_tier(tier_name)
    included_tier_counts: dict[str, int] = {}
    for _, observation in all_observations:
        tier_name = observation["quality_tier"]
        included_tier_counts[tier_name] = included_tier_counts.get(tier_name, 0) + 1
    for tier_name, included_count in included_tier_counts.items():
        if diagnostics["quality_tier_counts"].get(tier_name, 0) < included_count:
            _fail("market_history_diagnostics_quality_tier_count_invalid")
    total_quality_count = sum(diagnostics["quality_tier_counts"].values())
    if not (
        diagnostics["included_record_count"]
        <= total_quality_count
        <= diagnostics["source_record_count"]
    ):
        _fail("market_history_diagnostics_quality_tier_count_invalid")
    source_fingerprint = payload["source_fingerprint"]
    typed_fingerprint = payload["typed_history_fingerprint"]
    if verify_fingerprints:
        if not isinstance(source_fingerprint, str) or not _SHA256_RE.fullmatch(
            source_fingerprint
        ):
            _fail("market_history_source_fingerprint_invalid")
        if not isinstance(typed_fingerprint, str) or not _SHA256_RE.fullmatch(
            typed_fingerprint
        ):
            _fail("market_history_typed_fingerprint_invalid")
    else:
        if not isinstance(source_fingerprint, str):
            _fail("market_history_source_fingerprint_invalid")
        if not isinstance(typed_fingerprint, str):
            _fail("market_history_typed_fingerprint_invalid")
    normalized = {
        "schema_version": MARKET_HISTORY_EXPORT_SCHEMA_VERSION,
        "database_schema_revision": revision,
        "export_profile": profile,
        "item_mapping_fingerprint": mapping_fingerprint,
        "source_fingerprint": source_fingerprint,
        "typed_history_fingerprint": typed_fingerprint,
        "items": items,
        "provenance": provenance,
        "diagnostics": diagnostics,
    }
    if verify_fingerprints:
        if market_observation_source_fingerprint(normalized) != source_fingerprint:
            _fail("market_history_source_fingerprint_mismatch")
        if typed_market_history_fingerprint(normalized) != typed_fingerprint:
            _fail("market_history_typed_fingerprint_mismatch")
    return normalized


def _normalize_item(
    value: object, provenance: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("market_history_item_invalid")
    _require_exact_keys(
        value,
        {
            "item_id",
            "external_key",
            "name",
            "category",
            "rarity",
            "is_active",
            "observations",
        },
        "market_history_item",
    )
    item_id = value["item_id"]
    if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0:
        _fail("market_history_item_id_invalid")
    external_key = _required_string(
        value["external_key"], "market_history_external_key_invalid"
    )
    observations = value["observations"]
    if not isinstance(observations, list):
        _fail("market_history_observations_invalid")
    normalized_observations = [
        _normalize_observation(raw, provenance) for raw in observations
    ]
    for observation in normalized_observations:
        linked = provenance[observation["provenance_ref"]]
        page_identity = linked["page_identity"]
        derived_match = (
            None
            if page_identity is None or page_identity["item_key"] is None
            else page_identity["item_key"] == external_key
        )
        if linked["page_item_key_matches_database_item"] is not derived_match:
            _fail("market_history_page_item_match_inconsistent")
    sort_keys = [
        (raw["observed_at"], raw["observation_key"])
        for raw in normalized_observations
    ]
    if sort_keys != sorted(sort_keys):
        _fail("market_history_observations_not_sorted")
    if len({raw["observation_key"] for raw in normalized_observations}) != len(
        normalized_observations
    ):
        _fail("market_history_duplicate_observation_key")
    return {
        "item_id": item_id,
        "external_key": external_key,
        "name": _required_string(value["name"], "market_history_item_name_invalid"),
        "category": _required_string(
            value["category"], "market_history_item_category_invalid"
        ),
        "rarity": _optional_string(value["rarity"], "market_history_item_rarity_invalid"),
        "is_active": _required_bool(value["is_active"], "market_history_item_active_invalid"),
        "observations": normalized_observations,
    }


def _normalize_observation(
    value: object, provenance: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("market_history_observation_invalid")
    expected = {
        "observed_at",
        "best_bid_price",
        "best_ask_price",
        "bid_count",
        "ask_count",
        "estimated_volume",
        "observed_bid_quantity",
        "observed_ask_quantity",
        "quantity_semantics",
        "source_type",
        "review_status",
        "quality_tier",
        "observation_key",
        "provenance_ref",
    }
    _require_exact_keys(value, expected, "market_history_observation")
    observed_at = _datetime_text(_parse_datetime(value["observed_at"], "observed_at"))
    best_bid = _optional_decimal_text(
        _parse_optional_decimal(value["best_bid_price"], "best_bid_price")
    )
    if best_bid is not None and Decimal(best_bid) < Decimal("0"):
        _fail("market_history_best_bid_invalid")
    best_ask = _decimal_text(_parse_decimal(value["best_ask_price"], "best_ask_price"))
    if Decimal(best_ask) <= Decimal("0"):
        _fail("market_history_best_ask_invalid")
    for name in (
        "bid_count",
        "ask_count",
        "observed_bid_quantity",
        "observed_ask_quantity",
    ):
        _optional_nonnegative_int(value[name], f"market_history_{name}_invalid")
    estimated_volume = _optional_decimal_text(
        _parse_optional_decimal(value["estimated_volume"], "estimated_volume")
    )
    if estimated_volume is not None and Decimal(estimated_volume) < Decimal("0"):
        _fail("market_history_estimated_volume_invalid")
    quality_tier = _quality_tier(value["quality_tier"]).value
    observation_key = _required_string(
        value["observation_key"], "market_history_observation_key_invalid"
    )
    provenance_ref = _required_string(
        value["provenance_ref"], "market_history_provenance_ref_invalid"
    )
    if observation_key != provenance_ref or provenance_ref not in provenance:
        _fail("market_history_provenance_reference_invalid")
    linked_provenance = provenance[provenance_ref]
    if linked_provenance["quality_tier"] != quality_tier:
        _fail("market_history_quality_tier_mismatch")
    if linked_provenance["final_observed_at"] != observed_at:
        _fail("market_history_final_observed_at_mismatch")
    if (
        value["observed_bid_quantity"] is None
        or value["observed_ask_quantity"] is None
    ) and not linked_provenance["incomplete_quantities_acknowledged"]:
        _fail("market_history_incomplete_quantities_not_acknowledged")
    quantity_semantics = _required_string(
        value["quantity_semantics"], "market_history_quantity_semantics_invalid"
    )
    if quantity_semantics != "screenshot_display_quantity":
        _fail("market_history_quantity_semantics_invalid")
    source_type = _required_string(
        value["source_type"], "market_history_source_type_invalid"
    )
    if source_type != "screen_review":
        _fail("market_history_source_type_invalid")
    review_status = _required_string(
        value["review_status"], "market_history_review_status_invalid"
    )
    if review_status not in {"confirmed", "confirmed_with_edits"}:
        _fail("market_history_review_status_invalid")
    if value["bid_count"] is not None or value["ask_count"] is not None:
        _fail("market_history_screen_review_legacy_count_present")
    if estimated_volume is not None:
        _fail("market_history_screen_review_estimated_volume_present")
    return {
        "observed_at": observed_at,
        "best_bid_price": best_bid,
        "best_ask_price": best_ask,
        "bid_count": value["bid_count"],
        "ask_count": value["ask_count"],
        "estimated_volume": estimated_volume,
        "observed_bid_quantity": value["observed_bid_quantity"],
        "observed_ask_quantity": value["observed_ask_quantity"],
        "quantity_semantics": quantity_semantics,
        "source_type": source_type,
        "review_status": review_status,
        "quality_tier": quality_tier,
        "observation_key": observation_key,
        "provenance_ref": provenance_ref,
    }


def _normalize_provenance(value: object, key: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("market_history_provenance_invalid")
    expected = {
        "source_review_id",
        "candidate_version",
        "candidate_sha256",
        "client_capture_id",
        "capture_schema_version",
        "capture_started_at",
        "captured_at",
        "final_observed_at",
        "observed_at_source",
        "page_identity",
        "capture_sha256",
        "extension_version",
        "incomplete_quantities_acknowledged",
        "edited_fields",
        "quality_tier",
        "imported_at",
        "page_item_key_matches_database_item",
    }
    _require_exact_keys(value, expected, "market_history_provenance")
    review_id = _required_string(
        value["source_review_id"], "market_history_source_review_id_invalid"
    )
    if key != f"review:{review_id}":
        _fail("market_history_provenance_key_mismatch")
    candidate_hash = value["candidate_sha256"]
    if not isinstance(candidate_hash, str) or not _SHA256_RE.fullmatch(candidate_hash):
        _fail("market_history_candidate_sha256_invalid")
    capture_hash = value["capture_sha256"]
    if capture_hash is not None and (
        not isinstance(capture_hash, str) or not _SHA256_RE.fullmatch(capture_hash)
    ):
        _fail("market_history_capture_sha256_invalid")
    edited_fields = value["edited_fields"]
    if not isinstance(edited_fields, list) or any(
        not isinstance(field, str) or not field.strip() for field in edited_fields
    ):
        _fail("market_history_edited_fields_invalid")
    normalized_edited = sorted(set(edited_fields))
    if normalized_edited != edited_fields:
        _fail("market_history_edited_fields_not_canonical")
    quality_tier = _quality_tier(value["quality_tier"]).value
    normalized = {
        "source_review_id": review_id,
        "candidate_version": _required_string(
            value["candidate_version"], "market_history_candidate_version_invalid"
        ),
        "candidate_sha256": candidate_hash,
        "client_capture_id": _optional_string(
            value["client_capture_id"], "market_history_client_capture_id_invalid"
        ),
        "capture_schema_version": _required_string(
            value["capture_schema_version"],
            "market_history_capture_schema_version_invalid",
        ),
        "capture_started_at": _optional_datetime_value(
            value["capture_started_at"], "capture_started_at"
        ),
        "captured_at": _optional_datetime_value(value["captured_at"], "captured_at"),
        "final_observed_at": _datetime_text(
            _parse_datetime(value["final_observed_at"], "final_observed_at")
        ),
        "observed_at_source": _required_string(
            value["observed_at_source"], "market_history_observed_at_source_invalid"
        ),
        "page_identity": (
            None
            if value["page_identity"] is None
            else _normalize_page_identity(value["page_identity"])
        ),
        "capture_sha256": capture_hash,
        "extension_version": _optional_string(
            value["extension_version"], "market_history_extension_version_invalid"
        ),
        "incomplete_quantities_acknowledged": _required_bool(
            value["incomplete_quantities_acknowledged"],
            "market_history_acknowledgement_invalid",
        ),
        "edited_fields": normalized_edited,
        "quality_tier": quality_tier,
        "imported_at": _optional_datetime_value(value["imported_at"], "imported_at"),
        "page_item_key_matches_database_item": _optional_bool(
            value["page_item_key_matches_database_item"],
            "market_history_page_item_match_invalid",
        ),
    }
    _validate_normalized_provenance(normalized)
    return normalized


def _validate_normalized_provenance(value: Mapping[str, Any]) -> None:
    tier = _quality_tier(value["quality_tier"])
    if tier is MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE:
        _fail("market_history_quality_tier_unknown")
    if tier in {
        MarketHistoryQualityTier.BROWSER_CAPTURED,
        MarketHistoryQualityTier.BROWSER_CAPTURED_USER_TIME_EDITED,
    }:
        if value["capture_schema_version"] != "point_in_time_capture_v1":
            _fail("market_history_browser_capture_schema_invalid")
        if (
            value["client_capture_id"] is None
            or value["capture_started_at"] is None
            or value["captured_at"] is None
            or value["page_identity"] is None
            or value["capture_sha256"] is None
        ):
            _fail("market_history_browser_capture_incomplete")
        try:
            parsed_uuid = uuid.UUID(value["client_capture_id"])
        except ValueError as exc:
            raise MarketHistoryExportValidationError(
                "market_history_client_capture_id_invalid"
            ) from exc
        if parsed_uuid.version != 4:
            _fail("market_history_client_capture_id_invalid")
        started = _parse_datetime(value["capture_started_at"], "capture_started_at")
        captured = _parse_datetime(value["captured_at"], "captured_at")
        if started > captured:
            _fail("market_history_capture_time_order_invalid")
        if captured - started > timedelta(seconds=30):
            _fail("market_history_capture_duration_too_long")
        expected_source = (
            "browser_capture"
            if tier is MarketHistoryQualityTier.BROWSER_CAPTURED
            else "user_edited"
        )
        if value["observed_at_source"] != expected_source:
            _fail("market_history_observed_at_source_tier_mismatch")
        if tier is MarketHistoryQualityTier.BROWSER_CAPTURED:
            if value["final_observed_at"] != value["captured_at"]:
                _fail("market_history_browser_capture_time_mismatch")
            if "observed_at" in value["edited_fields"]:
                _fail("market_history_browser_capture_edit_mismatch")
        else:
            if "observed_at" not in value["edited_fields"]:
                _fail("market_history_user_edited_time_marker_missing")
        if value["page_item_key_matches_database_item"] is False:
            _fail("market_history_page_item_key_mismatch")
        return
    if tier is MarketHistoryQualityTier.LEGACY_SERVER_TIME:
        if (
            value["capture_schema_version"] != "legacy_v1"
            or value["observed_at_source"] != "review_created_default"
            or value["client_capture_id"] is not None
            or value["capture_started_at"] is not None
            or value["captured_at"] is not None
            or value["page_identity"] is not None
        ):
            _fail("market_history_legacy_provenance_invalid")
        return
    _fail("market_history_quality_tier_invalid")


def _normalize_diagnostics(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("market_history_diagnostics_invalid")
    expected = {
        "source_record_count",
        "included_record_count",
        "excluded_record_count",
        "conflict_count",
        "quality_tier_counts",
        "exclusion_reason_counts",
    }
    _require_exact_keys(value, expected, "market_history_diagnostics")
    result: dict[str, Any] = {}
    for name in (
        "source_record_count",
        "included_record_count",
        "excluded_record_count",
        "conflict_count",
    ):
        raw = value[name]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            _fail("market_history_diagnostic_count_invalid")
        result[name] = raw
    for name in ("quality_tier_counts", "exclusion_reason_counts"):
        raw = value[name]
        if not isinstance(raw, Mapping):
            _fail("market_history_diagnostic_map_invalid")
        normalized: dict[str, int] = {}
        for key in sorted(raw):
            count = raw[key]
            if (
                not isinstance(key, str)
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                _fail("market_history_diagnostic_map_invalid")
            normalized[key] = count
        result[name] = normalized
    return result


def _deduplicate_source_records(
    records: tuple[MarketHistorySourceRecord, ...]
) -> tuple[MarketHistorySourceRecord, ...]:
    deduplicated: list[MarketHistorySourceRecord] = []
    by_review: dict[str, MarketHistorySourceRecord] = {}
    for record in records:
        previous = by_review.get(record.source_review_id)
        if previous is None:
            by_review[record.source_review_id] = record
            deduplicated.append(record)
        elif previous != record:
            _fail("source_review_conflict")
    return tuple(deduplicated)


def _validate_source_record_conflicts(
    records: tuple[MarketHistorySourceRecord, ...]
) -> None:
    seen_reviews: dict[str, MarketHistorySourceRecord] = {}
    seen_capture_ids: dict[str, str] = {}
    seen_item_times: dict[tuple[int, datetime], MarketHistorySourceRecord] = {}
    for record in records:
        previous = seen_reviews.get(record.source_review_id)
        if previous is not None:
            _fail("source_review_conflict")
        seen_reviews[record.source_review_id] = record
        if record.client_capture_id is not None:
            existing_review = seen_capture_ids.get(record.client_capture_id)
            if existing_review is not None and existing_review != record.source_review_id:
                _fail("source_client_capture_id_conflict")
            seen_capture_ids[record.client_capture_id] = record.source_review_id
        item_time = (record.item_id, record.observed_at)
        existing = seen_item_times.get(item_time)
        if existing is not None:
            _fail("source_item_time_conflict")
        seen_item_times[item_time] = record


def _profile_exclusion_reason(
    record: MarketHistorySourceRecord, profile: MarketHistoryExportProfile
) -> str | None:
    tier = record.quality_tier
    complete_quantities = (
        record.observed_bid_quantity is not None
        and record.observed_ask_quantity is not None
    )
    if tier is MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE:
        return "unknown_or_incomplete"
    if profile is MarketHistoryExportProfile.POINT_IN_TIME_PRIMARY:
        if tier is not MarketHistoryQualityTier.BROWSER_CAPTURED:
            return "quality_tier_not_primary"
        if record.page_item_key_matches_database_item is not True:
            return "page_item_identity_unverified"
        if not complete_quantities:
            return "incomplete_quantities"
        return None
    if profile is MarketHistoryExportProfile.POINT_IN_TIME_REVIEWED:
        if tier not in {
            MarketHistoryQualityTier.BROWSER_CAPTURED,
            MarketHistoryQualityTier.BROWSER_CAPTURED_USER_TIME_EDITED,
        }:
            return "quality_tier_not_point_in_time"
        if not complete_quantities and not record.incomplete_quantities_acknowledged:
            return "incomplete_quantities_not_acknowledged"
        return None
    if profile in {
        MarketHistoryExportProfile.QUOTE_ONLY_REVIEWED,
        MarketHistoryExportProfile.ALL_VALID_REVIEWED,
    }:
        if not complete_quantities and not record.incomplete_quantities_acknowledged:
            return "incomplete_quantities_not_acknowledged"
        return None
    _fail("market_history_export_profile_invalid")


def _normalized_profile_allows(
    observation: Mapping[str, Any],
    provenance: Mapping[str, Any],
    profile: MarketHistoryExportProfile,
) -> bool:
    tier = _quality_tier(observation["quality_tier"])
    complete = (
        observation["observed_bid_quantity"] is not None
        and observation["observed_ask_quantity"] is not None
    )
    if profile is MarketHistoryExportProfile.POINT_IN_TIME_PRIMARY:
        return (
            tier is MarketHistoryQualityTier.BROWSER_CAPTURED
            and complete
            and provenance["page_item_key_matches_database_item"] is True
        )
    if profile is MarketHistoryExportProfile.POINT_IN_TIME_REVIEWED:
        return (
            tier
            in {
                MarketHistoryQualityTier.BROWSER_CAPTURED,
                MarketHistoryQualityTier.BROWSER_CAPTURED_USER_TIME_EDITED,
            }
            and (complete or provenance["incomplete_quantities_acknowledged"])
        )
    if profile in {
        MarketHistoryExportProfile.QUOTE_ONLY_REVIEWED,
        MarketHistoryExportProfile.ALL_VALID_REVIEWED,
    }:
        return complete or provenance["incomplete_quantities_acknowledged"]
    return False


def _quality_tier_counts(
    records: Iterable[MarketHistorySourceRecord],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for record in records:
        key = record.quality_tier.value
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _record_sort_key(record: MarketHistorySourceRecord) -> tuple[object, ...]:
    return (record.item_id, record.observed_at, record.source_review_id)


def _item_identity(record: MarketHistorySourceRecord) -> tuple[object, ...]:
    return (
        record.item_external_key,
        record.item_name,
        record.item_category,
        record.item_rarity,
        record.item_is_active,
    )


def _quality_tier(value: object) -> MarketHistoryQualityTier:
    try:
        return (
            value
            if isinstance(value, MarketHistoryQualityTier)
            else MarketHistoryQualityTier(value)
        )
    except (TypeError, ValueError) as exc:
        raise MarketHistoryExportValidationError(
            "market_history_quality_tier_invalid"
        ) from exc


def _profile(value: MarketHistoryExportProfile | str) -> MarketHistoryExportProfile:
    try:
        return (
            value
            if isinstance(value, MarketHistoryExportProfile)
            else MarketHistoryExportProfile(value)
        )
    except (TypeError, ValueError) as exc:
        raise MarketHistoryExportValidationError(
            "market_history_export_profile_invalid"
        ) from exc


def _normalize_page_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("page_identity_invalid")
    _require_exact_keys(value, {"origin", "market_path", "item_key"}, "page_identity")
    origin = _required_string(value["origin"], "page_identity_origin_invalid")
    market_path = _required_string(value["market_path"], "page_identity_path_invalid")
    item_key = _optional_string(value["item_key"], "page_identity_item_key_invalid")
    if origin != "https://trade.gaijin.net":
        _fail("page_identity_origin_invalid")
    if (
        not market_path.startswith("/market/1067/")
        or "?" in market_path
        or "#" in market_path
        or len(market_path) > 1024
    ):
        _fail("page_identity_path_invalid")
    tail = market_path.removeprefix("/market/1067/").strip("/")
    derived = _strict_percent_decode(tail) if tail else None
    if derived is not None and len(derived) > 512:
        _fail("page_identity_item_key_invalid")
    if item_key != derived:
        _fail("page_identity_item_key_invalid")
    return {"origin": origin, "market_path": market_path, "item_key": derived}


def _strict_percent_decode(value: str) -> str:
    encoded = bytearray()
    index = 0
    while index < len(value):
        if value[index] == "%":
            if index + 2 >= len(value) or any(
                char not in "0123456789abcdefABCDEF"
                for char in value[index + 1 : index + 3]
            ):
                _fail("page_identity_item_key_encoding_invalid")
            encoded.append(int(value[index + 1 : index + 3], 16))
            index += 3
            continue
        encoded.extend(value[index].encode("utf-8"))
        index += 1
    try:
        return bytes(encoded).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise MarketHistoryExportValidationError(
            "page_identity_item_key_encoding_invalid"
        ) from exc


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        suffix = f" missing={missing} unexpected={unexpected}"
        _fail(f"{label}_keys_invalid", suffix)


def _required_string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(code)
    return value.strip()


def _optional_string(value: object, code: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, code)


def _required_bool(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        _fail(code)
    return value


def _optional_bool(value: object, code: str) -> bool | None:
    if value is None:
        return None
    return _required_bool(value, code)


def _optional_nonnegative_int(value: object, code: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(code)
    return value


def _parse_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or not value:
        _fail(f"market_history_{field}_invalid")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise MarketHistoryExportValidationError(
            f"market_history_{field}_invalid"
        ) from exc
    if not parsed.is_finite():
        _fail(f"market_history_{field}_invalid")
    return parsed


def _parse_optional_decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    return _parse_decimal(value, field)


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        _fail(f"market_history_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketHistoryExportValidationError(
            f"market_history_{field}_invalid"
        ) from exc
    return _aware_utc(parsed, field)


def _optional_datetime_value(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _datetime_text(_parse_datetime(value, field))


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(f"source_{field}_timezone_required")
    return value.astimezone(UTC)


def _optional_aware_utc(value: datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    return _aware_utc(value, field)


def _datetime_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _optional_datetime_text(value: datetime | None) -> str | None:
    return None if value is None else _datetime_text(value)


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fail(code: str, message: str | None = None) -> None:
    raise MarketHistoryExportValidationError(code, message)
