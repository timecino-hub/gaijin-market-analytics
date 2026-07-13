"""Strict, deterministic Round 6B combined calibration dataset.

The adapter combines two independently validated Analytics inputs:

* offline historical trade buckets; and
* reviewed point-in-time market observations.

Item identity is connected only by an explicit, user-confirmed mapping manifest.
Historical buckets remain support evidence and never assert that a user's order
filled.  No-lookahead availability is represented by completed bucket ends.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from gaijin_market_analytics.backtesting.calibration_contracts import ItemMarketHistory
from gaijin_market_analytics.backtesting.trade_evidence_contracts import (
    HistoricalTradeBucket,
    HistoricalTradeGranularity,
    ItemHistoricalTradeHistory,
    PRICE_SEMANTICS,
    VOLUME_SEMANTICS,
)
from gaijin_market_analytics.contracts import MarketObservation
from gaijin_market_analytics.datasets.gaijin_history_json import (
    SOURCE_SCHEMA_VERSION,
    OfflineHistoryDataset,
)
from gaijin_market_analytics.datasets.market_history_export import (
    ITEM_MAPPING_SCHEMA_VERSION,
    ItemMappingEntry,
    ItemMappingManifest,
    LoadedMarketHistoryDataset,
    MarketHistoryExportValidationError,
    MarketHistoryExportProfile,
    MarketHistoryQualityTier,
    item_mapping_manifest_sha256,
    parse_item_mapping_manifest,
)


COMBINED_CALIBRATION_SCHEMA_VERSION = "round6_combined_calibration_dataset_v1"
QUOTE_ONLY_BASELINE_SCHEMA_VERSION = "round6_quote_only_baseline_v1"
TRADE_SUPPORT_EVIDENCE_SCHEMA_VERSION = "round6_trade_support_evidence_v1"
NO_LOOKAHEAD_POLICY_NAME = "completed_bucket_end_at_or_before_observation"
NO_LOOKAHEAD_POLICY_VERSION = "1.0.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CombinedCalibrationValidationError(ValueError):
    """Stable validation failure without embedding raw input data."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True, slots=True)
class ObservationTradeAvailability:
    observation_key: str
    knowledge_cutoff: datetime
    available_bucket_keys: tuple[str, ...]
    selected_hourly_bucket_count: int
    selected_daily_fallback_count: int


@dataclass(frozen=True, slots=True)
class LoadedCombinedCalibrationDataset:
    market_histories: tuple[ItemMarketHistory, ...]
    historical_trade_histories: tuple[ItemHistoricalTradeHistory, ...]
    market_provenance_by_key: Mapping[str, Mapping[str, Any]]
    historical_provenance_by_sample_key: Mapping[str, Mapping[str, Any]]
    availability_by_observation_key: Mapping[str, ObservationTradeAvailability]
    mapping_manifest: ItemMappingManifest
    quote_only_baseline_fingerprint: str
    trade_support_evidence_fingerprint: str
    combined_source_fingerprint: str
    combined_typed_fingerprint: str
    diagnostics: Mapping[str, Any]


def build_combined_calibration_dataset(
    *,
    historical_dataset: OfflineHistoryDataset,
    market_dataset: LoadedMarketHistoryDataset,
    item_mapping: ItemMappingManifest,
) -> dict[str, Any]:
    """Build a deterministic, database-free Round 6B neutral dataset."""

    item_mapping = _validated_item_mapping_manifest(item_mapping)
    if not item_mapping.mappings:
        _fail("combined_mapping_required")
    historical_by_key = {
        sample.spec.sample_key: sample for sample in historical_dataset.samples
    }
    if len(historical_by_key) != len(historical_dataset.samples):
        _fail("combined_historical_sample_duplicate")
    market_by_id = {history.item_id: history for history in market_dataset.histories}
    if len(market_by_id) != len(market_dataset.histories):
        _fail("combined_market_item_duplicate")

    mapping_fingerprint = item_mapping_manifest_sha256(item_mapping)
    mapping_evidence: list[dict[str, Any]] = []
    quote_items: list[dict[str, Any]] = []
    trade_items: list[dict[str, Any]] = []
    market_provenance: dict[str, Mapping[str, Any]] = {}
    historical_provenance: dict[str, dict[str, Any]] = {}
    mapped_sample_keys: set[str] = set()
    mapped_database_ids: set[int] = set()
    quality_tier_counts: dict[str, int] = {}

    for mapping in sorted(
        item_mapping.mappings,
        key=lambda value: (
            value.offline_sample_key,
            value.database_item_id,
            value.database_external_key,
        ),
    ):
        sample = historical_by_key.get(mapping.offline_sample_key)
        if sample is None:
            _fail("combined_mapping_historical_sample_missing")
        market_history = market_by_id.get(mapping.database_item_id)
        if market_history is None:
            _fail("combined_mapping_market_item_missing")
        if not market_history.observations:
            _fail("combined_mapping_market_history_empty")
        identity = market_dataset.item_identity_by_id.get(mapping.database_item_id)
        if identity is None:
            _fail("combined_market_item_identity_missing")
        external_key = identity.get("external_key")
        if external_key != mapping.database_external_key:
            _fail("combined_mapping_database_external_key_mismatch")
        historical_item_key = sample.spec.item_key
        if historical_item_key is not None and historical_item_key != external_key:
            _fail("combined_mapping_historical_item_key_mismatch")

        mapped_sample_keys.add(mapping.offline_sample_key)
        mapped_database_ids.add(mapping.database_item_id)
        remapped_buckets = tuple(
            _remap_bucket(bucket, mapping.database_item_id)
            for bucket in sample.history.buckets
        )
        quote_observations = [
            _quote_observation_payload(observation, market_dataset)
            for observation in market_history.observations
        ]
        for observation in quote_observations:
            tier = observation["quality_tier"]
            quality_tier_counts[tier] = quality_tier_counts.get(tier, 0) + 1
            key = observation["provenance_ref"]
            market_provenance[key] = _canonical_copy(
                market_dataset.provenance_by_key[key]
            )
        bucket_payloads = [_trade_bucket_payload(bucket) for bucket in remapped_buckets]
        availability = [
            _availability_payload(observation, remapped_buckets)
            for observation in market_history.observations
        ]
        quote_items.append(
            {
                "item_id": mapping.database_item_id,
                "external_key": external_key,
                "observations": quote_observations,
            }
        )
        trade_items.append(
            {
                "item_id": mapping.database_item_id,
                "external_key": external_key,
                "buckets": bucket_payloads,
                "observation_availability": availability,
            }
        )
        identity_match = (
            None if historical_item_key is None else historical_item_key == external_key
        )
        mapping_evidence.append(
            {
                "offline_sample_key": mapping.offline_sample_key,
                "historical_offline_item_id": sample.spec.offline_item_id,
                "historical_item_key": historical_item_key,
                "database_item_id": mapping.database_item_id,
                "database_external_key": external_key,
                "confirmed_by_user": True,
                "historical_item_key_matches_database_external_key": identity_match,
            }
        )
        historical_provenance[mapping.offline_sample_key] = {
            "offline_item_id": sample.spec.offline_item_id,
            "display_name": sample.spec.display_name,
            "file": sample.spec.file.replace("\\", "/"),
            "source_file_sha256": sample.source_file_sha256,
            "historical_item_key": historical_item_key,
            "source_url": sample.spec.source_url,
            "captured_at": sample.spec.captured_at,
        }

    quote_items.sort(key=lambda value: value["item_id"])
    trade_items.sort(key=lambda value: value["item_id"])
    mapping_evidence.sort(
        key=lambda value: (
            value["offline_sample_key"],
            value["database_item_id"],
        )
    )
    market_provenance = {
        key: market_provenance[key] for key in sorted(market_provenance)
    }
    historical_provenance = {
        key: historical_provenance[key] for key in sorted(historical_provenance)
    }

    quote_payload: dict[str, Any] = {
        "schema_version": QUOTE_ONLY_BASELINE_SCHEMA_VERSION,
        "items": quote_items,
    }
    quote_payload["fingerprint"] = quote_only_baseline_fingerprint(quote_payload)
    trade_payload: dict[str, Any] = {
        "schema_version": TRADE_SUPPORT_EVIDENCE_SCHEMA_VERSION,
        "price_semantics": PRICE_SEMANTICS,
        "volume_semantics": VOLUME_SEMANTICS,
        "fill_claim": False,
        "items": trade_items,
    }
    trade_payload["fingerprint"] = trade_support_evidence_fingerprint(trade_payload)

    payload: dict[str, Any] = {
        "schema_version": COMBINED_CALIBRATION_SCHEMA_VERSION,
        "no_lookahead_policy": {
            "name": NO_LOOKAHEAD_POLICY_NAME,
            "version": NO_LOOKAHEAD_POLICY_VERSION,
            "availability_rule": "bucket_end_at<=knowledge_cutoff",
            "hourly_precedes_daily_for_same_utc_day": True,
            "missing_hourly_bucket_is_zero_trade": False,
        },
        "input_fingerprints": {
            "historical_source_manifest_sha256": (
                historical_dataset.source_manifest_sha256
            ),
            "historical_typed_trade_dataset_sha256": (
                historical_dataset.typed_trade_dataset_sha256
            ),
            "market_source_fingerprint": market_dataset.source_fingerprint,
            "market_typed_history_fingerprint": (
                market_dataset.typed_history_fingerprint
            ),
            "market_export_profile": market_dataset.export_profile.value,
            "item_mapping_fingerprint": mapping_fingerprint,
        },
        "mapping_evidence": mapping_evidence,
        "quote_only_baseline": quote_payload,
        "trade_support_evidence": trade_payload,
        "provenance": {
            "market_by_observation_key": market_provenance,
            "historical_by_sample_key": historical_provenance,
        },
        "combined_source_fingerprint": "",
        "combined_typed_fingerprint": "",
        "diagnostics": {
            "historical_sample_count": len(historical_dataset.samples),
            "market_item_count": len(market_dataset.histories),
            "mapping_count": len(mapping_evidence),
            "combined_item_count": len(mapping_evidence),
            "unmapped_historical_sample_count": (
                len(historical_dataset.samples) - len(mapped_sample_keys)
            ),
            "unmapped_market_item_count": (
                len(market_dataset.histories) - len(mapped_database_ids)
            ),
            "observation_count": sum(
                len(item["observations"]) for item in quote_items
            ),
            "trade_bucket_count": sum(len(item["buckets"]) for item in trade_items),
            "quality_tier_counts": {
                key: quality_tier_counts[key] for key in sorted(quality_tier_counts)
            },
        },
    }
    payload["combined_source_fingerprint"] = combined_calibration_source_fingerprint(
        payload
    )
    payload["combined_typed_fingerprint"] = combined_calibration_typed_fingerprint(
        payload
    )
    # Exercise the same strict parser that downstream consumers use.
    parse_combined_calibration_dataset(payload)
    return payload


def quote_only_baseline_fingerprint(payload: Mapping[str, Any]) -> str:
    normalized = {
        "schema_version": payload.get("schema_version"),
        "items": payload.get("items"),
    }
    return _sha256_json(normalized)


def trade_support_evidence_fingerprint(payload: Mapping[str, Any]) -> str:
    normalized = {
        "schema_version": payload.get("schema_version"),
        "price_semantics": payload.get("price_semantics"),
        "volume_semantics": payload.get("volume_semantics"),
        "fill_claim": payload.get("fill_claim"),
        "items": payload.get("items"),
    }
    return _sha256_json(normalized)


def combined_calibration_source_fingerprint(payload: Mapping[str, Any]) -> str:
    return _sha256_json(
        {
            "schema_version": payload.get("schema_version"),
            "no_lookahead_policy": payload.get("no_lookahead_policy"),
            "input_fingerprints": payload.get("input_fingerprints"),
            "mapping_evidence": payload.get("mapping_evidence"),
            "quote_only_baseline": payload.get("quote_only_baseline"),
            "trade_support_evidence": payload.get("trade_support_evidence"),
            "provenance": _source_fingerprint_provenance(payload.get("provenance")),
        }
    )


def combined_calibration_typed_fingerprint(payload: Mapping[str, Any]) -> str:
    input_fingerprints = payload.get("input_fingerprints")
    mapping_fingerprint = (
        input_fingerprints.get("item_mapping_fingerprint")
        if isinstance(input_fingerprints, Mapping)
        else None
    )
    return _sha256_json(
        {
            "schema_version": payload.get("schema_version"),
            "no_lookahead_policy": payload.get("no_lookahead_policy"),
            "item_mapping_fingerprint": mapping_fingerprint,
            "quote_only_baseline": payload.get("quote_only_baseline"),
            "trade_support_evidence": payload.get("trade_support_evidence"),
        }
    )


def combined_calibration_json_bytes(
    payload: Mapping[str, Any], *, pretty: bool = False
) -> bytes:
    normalized = _normalize_combined_payload(payload, verify_fingerprints=True)
    if pretty:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    else:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return (encoded + "\n").encode("utf-8")


def write_combined_calibration_dataset(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    pretty: bool = False,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = combined_calibration_json_bytes(payload, pretty=pretty)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(file_descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def load_combined_calibration_dataset(
    path: str | Path,
) -> LoadedCombinedCalibrationDataset:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail("combined_dataset_file_not_found")
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("combined_dataset_json_invalid")
    return parse_combined_calibration_dataset(payload)


def parse_combined_calibration_dataset(
    payload: object,
) -> LoadedCombinedCalibrationDataset:
    normalized = _normalize_combined_payload(payload, verify_fingerprints=True)
    quote_by_id: dict[int, ItemMarketHistory] = {}
    market_provenance = normalized["provenance"]["market_by_observation_key"]
    availability_by_key: dict[str, ObservationTradeAvailability] = {}
    for item in normalized["quote_only_baseline"]["items"]:
        observations = tuple(_market_observation_from_payload(value) for value in item["observations"])
        quote_by_id[item["item_id"]] = ItemMarketHistory(
            item_id=item["item_id"], observations=observations
        )
    trade_by_id: dict[int, ItemHistoricalTradeHistory] = {}
    for item in normalized["trade_support_evidence"]["items"]:
        buckets = tuple(_historical_bucket_from_payload(item["item_id"], value) for value in item["buckets"])
        trade_by_id[item["item_id"]] = ItemHistoricalTradeHistory(
            item_id=item["item_id"], buckets=buckets
        )
        for value in item["observation_availability"]:
            availability_by_key[value["observation_key"]] = ObservationTradeAvailability(
                observation_key=value["observation_key"],
                knowledge_cutoff=_parse_datetime(value["knowledge_cutoff"], "knowledge_cutoff"),
                available_bucket_keys=tuple(value["available_bucket_keys"]),
                selected_hourly_bucket_count=value["selected_hourly_bucket_count"],
                selected_daily_fallback_count=value["selected_daily_fallback_count"],
            )
    mapping_manifest = _mapping_manifest_from_evidence(
        normalized["mapping_evidence"]
    )
    return LoadedCombinedCalibrationDataset(
        market_histories=tuple(quote_by_id[item_id] for item_id in sorted(quote_by_id)),
        historical_trade_histories=tuple(
            trade_by_id[item_id] for item_id in sorted(trade_by_id)
        ),
        market_provenance_by_key=market_provenance,
        historical_provenance_by_sample_key=normalized["provenance"][
            "historical_by_sample_key"
        ],
        availability_by_observation_key=availability_by_key,
        mapping_manifest=mapping_manifest,
        quote_only_baseline_fingerprint=normalized["quote_only_baseline"][
            "fingerprint"
        ],
        trade_support_evidence_fingerprint=normalized["trade_support_evidence"][
            "fingerprint"
        ],
        combined_source_fingerprint=normalized["combined_source_fingerprint"],
        combined_typed_fingerprint=normalized["combined_typed_fingerprint"],
        diagnostics=normalized["diagnostics"],
    )


def _normalize_combined_payload(
    payload: object, *, verify_fingerprints: bool
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        _fail("combined_dataset_top_level_invalid")
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "no_lookahead_policy",
            "input_fingerprints",
            "mapping_evidence",
            "quote_only_baseline",
            "trade_support_evidence",
            "provenance",
            "combined_source_fingerprint",
            "combined_typed_fingerprint",
            "diagnostics",
        },
        "combined_dataset",
    )
    if payload["schema_version"] != COMBINED_CALIBRATION_SCHEMA_VERSION:
        _fail("combined_dataset_schema_unsupported")
    policy = _normalize_policy(payload["no_lookahead_policy"])
    inputs = _normalize_input_fingerprints(payload["input_fingerprints"])
    mappings = _normalize_mapping_evidence(payload["mapping_evidence"])
    quote = _normalize_quote_only(payload["quote_only_baseline"])
    trade = _normalize_trade_evidence(payload["trade_support_evidence"])
    provenance = _normalize_provenance(payload["provenance"])
    diagnostics = _normalize_diagnostics(payload["diagnostics"])
    source_fingerprint = _required_sha256(
        payload["combined_source_fingerprint"], "combined_source_fingerprint_invalid"
    )
    typed_fingerprint = _required_sha256(
        payload["combined_typed_fingerprint"], "combined_typed_fingerprint_invalid"
    )
    normalized: dict[str, Any] = {
        "schema_version": COMBINED_CALIBRATION_SCHEMA_VERSION,
        "no_lookahead_policy": policy,
        "input_fingerprints": inputs,
        "mapping_evidence": mappings,
        "quote_only_baseline": quote,
        "trade_support_evidence": trade,
        "provenance": provenance,
        "combined_source_fingerprint": source_fingerprint,
        "combined_typed_fingerprint": typed_fingerprint,
        "diagnostics": diagnostics,
    }
    _validate_cross_contract(normalized)
    if verify_fingerprints:
        if quote["fingerprint"] != quote_only_baseline_fingerprint(quote):
            _fail("combined_quote_fingerprint_mismatch")
        if trade["fingerprint"] != trade_support_evidence_fingerprint(trade):
            _fail("combined_trade_fingerprint_mismatch")
        if source_fingerprint != combined_calibration_source_fingerprint(normalized):
            _fail("combined_source_fingerprint_mismatch")
        if typed_fingerprint != combined_calibration_typed_fingerprint(normalized):
            _fail("combined_typed_fingerprint_mismatch")
    return normalized


def _normalize_policy(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("combined_policy_invalid")
    _require_exact_keys(
        value,
        {
            "name",
            "version",
            "availability_rule",
            "hourly_precedes_daily_for_same_utc_day",
            "missing_hourly_bucket_is_zero_trade",
        },
        "combined_policy",
    )
    normalized = {
        "name": _required_string(value["name"], "combined_policy_name_invalid"),
        "version": _required_string(value["version"], "combined_policy_version_invalid"),
        "availability_rule": _required_string(
            value["availability_rule"], "combined_policy_rule_invalid"
        ),
        "hourly_precedes_daily_for_same_utc_day": _required_bool(
            value["hourly_precedes_daily_for_same_utc_day"],
            "combined_policy_hourly_precedence_invalid",
        ),
        "missing_hourly_bucket_is_zero_trade": _required_bool(
            value["missing_hourly_bucket_is_zero_trade"],
            "combined_policy_missing_hour_invalid",
        ),
    }
    if normalized != {
        "name": NO_LOOKAHEAD_POLICY_NAME,
        "version": NO_LOOKAHEAD_POLICY_VERSION,
        "availability_rule": "bucket_end_at<=knowledge_cutoff",
        "hourly_precedes_daily_for_same_utc_day": True,
        "missing_hourly_bucket_is_zero_trade": False,
    }:
        _fail("combined_policy_unsupported")
    return normalized


def _normalize_input_fingerprints(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("combined_input_fingerprints_invalid")
    expected = {
        "historical_source_manifest_sha256",
        "historical_typed_trade_dataset_sha256",
        "market_source_fingerprint",
        "market_typed_history_fingerprint",
        "market_export_profile",
        "item_mapping_fingerprint",
    }
    _require_exact_keys(value, expected, "combined_input_fingerprints")
    profile = _required_string(value["market_export_profile"], "combined_market_profile_invalid")
    try:
        MarketHistoryExportProfile(profile)
    except ValueError as exc:
        raise CombinedCalibrationValidationError("combined_market_profile_invalid") from exc
    return {
        "historical_source_manifest_sha256": _required_sha256(
            value["historical_source_manifest_sha256"], "combined_historical_source_hash_invalid"
        ),
        "historical_typed_trade_dataset_sha256": _required_sha256(
            value["historical_typed_trade_dataset_sha256"], "combined_historical_typed_hash_invalid"
        ),
        "market_source_fingerprint": _required_sha256(
            value["market_source_fingerprint"], "combined_market_source_hash_invalid"
        ),
        "market_typed_history_fingerprint": _required_sha256(
            value["market_typed_history_fingerprint"], "combined_market_typed_hash_invalid"
        ),
        "market_export_profile": profile,
        "item_mapping_fingerprint": _required_sha256(
            value["item_mapping_fingerprint"], "combined_mapping_hash_invalid"
        ),
    }


def _normalize_mapping_evidence(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        _fail("combined_mapping_evidence_invalid")
    normalized: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            _fail("combined_mapping_evidence_entry_invalid")
        _require_exact_keys(
            raw,
            {
                "offline_sample_key",
                "historical_offline_item_id",
                "historical_item_key",
                "database_item_id",
                "database_external_key",
                "confirmed_by_user",
                "historical_item_key_matches_database_external_key",
            },
            "combined_mapping_evidence_entry",
        )
        sample_key = _required_string(raw["offline_sample_key"], "combined_sample_key_invalid")
        historical_id = _positive_int(raw["historical_offline_item_id"], "combined_historical_item_id_invalid")
        historical_key = _optional_string(raw["historical_item_key"], "combined_historical_item_key_invalid")
        database_id = _positive_int(raw["database_item_id"], "combined_database_item_id_invalid")
        external_key = _required_string(raw["database_external_key"], "combined_database_external_key_invalid")
        confirmed = _required_bool(raw["confirmed_by_user"], "combined_mapping_confirmation_invalid")
        if not confirmed:
            _fail("combined_mapping_confirmation_required")
        match = raw["historical_item_key_matches_database_external_key"]
        if historical_key is None:
            if match is not None:
                _fail("combined_historical_item_match_invalid")
        else:
            if match is not True or historical_key != external_key:
                _fail("combined_historical_item_key_mismatch")
        normalized.append(
            {
                "offline_sample_key": sample_key,
                "historical_offline_item_id": historical_id,
                "historical_item_key": historical_key,
                "database_item_id": database_id,
                "database_external_key": external_key,
                "confirmed_by_user": True,
                "historical_item_key_matches_database_external_key": match,
            }
        )
    normalized.sort(key=lambda item: (item["offline_sample_key"], item["database_item_id"]))
    for field in ("offline_sample_key", "historical_offline_item_id", "database_item_id", "database_external_key"):
        values = [item[field] for item in normalized]
        if len(values) != len(set(values)):
            _fail(f"combined_mapping_duplicate_{field}")
    return normalized


def _normalize_quote_only(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("combined_quote_only_invalid")
    _require_exact_keys(value, {"schema_version", "items", "fingerprint"}, "combined_quote_only")
    if value["schema_version"] != QUOTE_ONLY_BASELINE_SCHEMA_VERSION:
        _fail("combined_quote_schema_unsupported")
    if not isinstance(value["items"], list):
        _fail("combined_quote_items_invalid")
    items = [_normalize_quote_item(item) for item in value["items"]]
    if [item["item_id"] for item in items] != sorted(item["item_id"] for item in items):
        _fail("combined_quote_items_not_sorted")
    if len({item["item_id"] for item in items}) != len(items):
        _fail("combined_quote_item_duplicate")
    return {
        "schema_version": QUOTE_ONLY_BASELINE_SCHEMA_VERSION,
        "items": items,
        "fingerprint": _required_sha256(value["fingerprint"], "combined_quote_fingerprint_invalid"),
    }


def _normalize_quote_item(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("combined_quote_item_invalid")
    _require_exact_keys(value, {"item_id", "external_key", "observations"}, "combined_quote_item")
    if not isinstance(value["observations"], list) or not value["observations"]:
        _fail("combined_quote_observations_invalid")
    observations = [_normalize_quote_observation(raw) for raw in value["observations"]]
    expected = sorted(observations, key=lambda item: (item["observed_at"], item["observation_key"]))
    if observations != expected:
        _fail("combined_quote_observations_not_sorted")
    keys = [item["observation_key"] for item in observations]
    if len(keys) != len(set(keys)):
        _fail("combined_quote_observation_key_duplicate")
    times = [item["observed_at"] for item in observations]
    if len(times) != len(set(times)):
        _fail("combined_quote_observation_time_conflict")
    return {
        "item_id": _positive_int(value["item_id"], "combined_quote_item_id_invalid"),
        "external_key": _required_string(value["external_key"], "combined_quote_external_key_invalid"),
        "observations": observations,
    }


def _normalize_quote_observation(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("combined_quote_observation_invalid")
    expected = {
        "observed_at", "best_bid_price", "best_ask_price", "bid_count", "ask_count",
        "estimated_volume", "observed_bid_quantity", "observed_ask_quantity",
        "quantity_semantics", "source_type", "review_status", "quality_tier",
        "observation_key", "provenance_ref",
    }
    _require_exact_keys(value, expected, "combined_quote_observation")
    observation_key = _required_string(value["observation_key"], "combined_quote_observation_key_invalid")
    provenance_ref = _required_string(value["provenance_ref"], "combined_quote_provenance_ref_invalid")
    if observation_key != provenance_ref:
        _fail("combined_quote_provenance_ref_mismatch")
    quality_tier = _required_string(value["quality_tier"], "combined_quote_quality_tier_invalid")
    try:
        MarketHistoryQualityTier(quality_tier)
    except ValueError as exc:
        raise CombinedCalibrationValidationError("combined_quote_quality_tier_invalid") from exc
    best_ask = _decimal_text(_positive_decimal(value["best_ask_price"], "combined_quote_best_ask_invalid"))
    best_bid = _optional_nonnegative_decimal_text(value["best_bid_price"], "combined_quote_best_bid_invalid")
    bid_count = _optional_nonnegative_int(value["bid_count"], "combined_quote_bid_count_invalid")
    ask_count = _optional_nonnegative_int(value["ask_count"], "combined_quote_ask_count_invalid")
    estimated_volume = _optional_nonnegative_decimal_text(
        value["estimated_volume"], "combined_quote_estimated_volume_invalid"
    )
    quantity_semantics = _required_string(
        value["quantity_semantics"], "combined_quote_quantity_semantics_invalid"
    )
    source_type = _required_string(
        value["source_type"], "combined_quote_source_type_invalid"
    )
    review_status = _required_string(
        value["review_status"], "combined_quote_review_status_invalid"
    )
    if quantity_semantics != "screenshot_display_quantity":
        _fail("combined_quote_quantity_semantics_invalid")
    if source_type != "screen_review":
        _fail("combined_quote_source_type_invalid")
    if review_status not in {"confirmed", "confirmed_with_edits"}:
        _fail("combined_quote_review_status_invalid")
    if bid_count is not None or ask_count is not None:
        _fail("combined_quote_legacy_count_present")
    if estimated_volume is not None:
        _fail("combined_quote_estimated_volume_present")
    return {
        "observed_at": _datetime_text(_parse_datetime(value["observed_at"], "observed_at")),
        "best_bid_price": best_bid,
        "best_ask_price": best_ask,
        "bid_count": bid_count,
        "ask_count": ask_count,
        "estimated_volume": estimated_volume,
        "observed_bid_quantity": _optional_nonnegative_int(value["observed_bid_quantity"], "combined_quote_bid_quantity_invalid"),
        "observed_ask_quantity": _optional_nonnegative_int(value["observed_ask_quantity"], "combined_quote_ask_quantity_invalid"),
        "quantity_semantics": quantity_semantics,
        "source_type": source_type,
        "review_status": review_status,
        "quality_tier": quality_tier,
        "observation_key": observation_key,
        "provenance_ref": provenance_ref,
    }


def _normalize_trade_evidence(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("combined_trade_evidence_invalid")
    _require_exact_keys(value, {"schema_version", "price_semantics", "volume_semantics", "fill_claim", "items", "fingerprint"}, "combined_trade_evidence")
    if value["schema_version"] != TRADE_SUPPORT_EVIDENCE_SCHEMA_VERSION:
        _fail("combined_trade_schema_unsupported")
    if value["price_semantics"] != PRICE_SEMANTICS:
        _fail("combined_trade_price_semantics_invalid")
    if value["volume_semantics"] != VOLUME_SEMANTICS:
        _fail("combined_trade_volume_semantics_invalid")
    if value["fill_claim"] is not False:
        _fail("combined_trade_fill_claim_forbidden")
    if not isinstance(value["items"], list):
        _fail("combined_trade_items_invalid")
    items = [_normalize_trade_item(item) for item in value["items"]]
    if [item["item_id"] for item in items] != sorted(item["item_id"] for item in items):
        _fail("combined_trade_items_not_sorted")
    if len({item["item_id"] for item in items}) != len(items):
        _fail("combined_trade_item_duplicate")
    return {
        "schema_version": TRADE_SUPPORT_EVIDENCE_SCHEMA_VERSION,
        "price_semantics": PRICE_SEMANTICS,
        "volume_semantics": VOLUME_SEMANTICS,
        "fill_claim": False,
        "items": items,
        "fingerprint": _required_sha256(value["fingerprint"], "combined_trade_fingerprint_invalid"),
    }


def _normalize_trade_item(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("combined_trade_item_invalid")
    _require_exact_keys(value, {"item_id", "external_key", "buckets", "observation_availability"}, "combined_trade_item")
    if not isinstance(value["buckets"], list) or not isinstance(value["observation_availability"], list):
        _fail("combined_trade_item_collections_invalid")
    item_id = _positive_int(value["item_id"], "combined_trade_item_id_invalid")
    buckets = [_normalize_trade_bucket(raw, item_id) for raw in value["buckets"]]
    if buckets != sorted(buckets, key=lambda item: (item["bucket_start_utc"], item["bucket_duration_seconds"])):
        _fail("combined_trade_buckets_not_sorted")
    keys = [bucket["bucket_key"] for bucket in buckets]
    if len(keys) != len(set(keys)):
        _fail("combined_trade_bucket_duplicate")
    availability = [_normalize_availability(raw) for raw in value["observation_availability"]]
    if availability != sorted(availability, key=lambda item: (item["knowledge_cutoff"], item["observation_key"])):
        _fail("combined_trade_availability_not_sorted")
    availability_keys = [entry["observation_key"] for entry in availability]
    if len(availability_keys) != len(set(availability_keys)):
        _fail("combined_trade_availability_observation_duplicate")
    return {
        "item_id": item_id,
        "external_key": _required_string(value["external_key"], "combined_trade_external_key_invalid"),
        "buckets": buckets,
        "observation_availability": availability,
    }


def _normalize_trade_bucket(value: object, item_id: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("combined_trade_bucket_invalid")
    expected = {"bucket_key", "granularity", "bucket_start_utc", "bucket_end_utc", "available_at", "bucket_duration_seconds", "reported_vwap_price", "reported_trade_volume", "price_semantics", "volume_semantics", "source_schema_version"}
    _require_exact_keys(value, expected, "combined_trade_bucket")
    granularity_text = _required_string(value["granularity"], "combined_trade_granularity_invalid")
    try:
        granularity = HistoricalTradeGranularity(granularity_text)
    except ValueError as exc:
        raise CombinedCalibrationValidationError("combined_trade_granularity_invalid") from exc
    start = _parse_datetime(value["bucket_start_utc"], "bucket_start_utc")
    end = _parse_datetime(value["bucket_end_utc"], "bucket_end_utc")
    available = _parse_datetime(value["available_at"], "available_at")
    duration = _positive_int(value["bucket_duration_seconds"], "combined_trade_duration_invalid")
    bucket = HistoricalTradeBucket(
        item_id=item_id,
        granularity=granularity,
        bucket_start_utc=start,
        bucket_duration_seconds=duration,
        reported_vwap_price=_positive_decimal(value["reported_vwap_price"], "combined_trade_vwap_invalid"),
        reported_trade_volume=_positive_int(value["reported_trade_volume"], "combined_trade_volume_invalid"),
        price_semantics=_required_string(value["price_semantics"], "combined_trade_price_semantics_invalid"),
        volume_semantics=_required_string(value["volume_semantics"], "combined_trade_volume_semantics_invalid"),
        source_schema_version=_required_string(value["source_schema_version"], "combined_trade_source_schema_invalid"),
    )
    if bucket.source_schema_version != SOURCE_SCHEMA_VERSION:
        _fail("combined_trade_source_schema_invalid")
    if end != bucket.bucket_end_utc or available != end:
        _fail("combined_trade_bucket_availability_invalid")
    expected_key = _bucket_key(bucket)
    if value["bucket_key"] != expected_key:
        _fail("combined_trade_bucket_key_invalid")
    return _trade_bucket_payload(bucket)


def _normalize_availability(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("combined_trade_availability_invalid")
    _require_exact_keys(value, {"observation_key", "knowledge_cutoff", "available_bucket_keys", "selected_hourly_bucket_count", "selected_daily_fallback_count"}, "combined_trade_availability")
    keys = value["available_bucket_keys"]
    if not isinstance(keys, list) or any(not isinstance(key, str) or not key for key in keys):
        _fail("combined_trade_available_bucket_keys_invalid")
    if keys != sorted(keys):
        _fail("combined_trade_available_bucket_keys_not_sorted")
    if len(keys) != len(set(keys)):
        _fail("combined_trade_available_bucket_key_duplicate")
    return {
        "observation_key": _required_string(value["observation_key"], "combined_trade_availability_observation_key_invalid"),
        "knowledge_cutoff": _datetime_text(_parse_datetime(value["knowledge_cutoff"], "knowledge_cutoff")),
        "available_bucket_keys": keys,
        "selected_hourly_bucket_count": _nonnegative_int(value["selected_hourly_bucket_count"], "combined_trade_hourly_count_invalid"),
        "selected_daily_fallback_count": _nonnegative_int(value["selected_daily_fallback_count"], "combined_trade_daily_count_invalid"),
    }


def _normalize_provenance(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("combined_provenance_invalid")
    _require_exact_keys(value, {"market_by_observation_key", "historical_by_sample_key"}, "combined_provenance")
    market = value["market_by_observation_key"]
    historical = value["historical_by_sample_key"]
    if not isinstance(market, Mapping) or not isinstance(historical, Mapping):
        _fail("combined_provenance_collections_invalid")
    normalized_market = {
        key: _normalize_market_provenance_entry(key, market[key])
        for key in sorted(market)
    }
    normalized_historical: dict[str, dict[str, Any]] = {}
    for key in sorted(historical):
        raw = historical[key]
        if not isinstance(raw, Mapping):
            _fail("combined_historical_provenance_invalid")
        _require_exact_keys(raw, {"offline_item_id", "display_name", "file", "source_file_sha256", "historical_item_key", "source_url", "captured_at"}, "combined_historical_provenance")
        file_name = _required_string(
            raw["file"], "combined_historical_provenance_file_invalid"
        ).replace("\\", "/")
        if (
            file_name.startswith("/")
            or re.match(r"^[A-Za-z]:/", file_name)
            or ".." in file_name.split("/")
        ):
            _fail("combined_historical_provenance_file_invalid")
        source_url = _optional_string(
            raw["source_url"], "combined_historical_provenance_source_url_invalid"
        )
        if source_url is not None:
            if (
                not source_url.startswith("https://")
                or "?" in source_url
                or "#" in source_url
            ):
                _fail("combined_historical_provenance_source_url_invalid")
            authority_and_path = source_url.removeprefix("https://")
            authority = authority_and_path.split("/", 1)[0]
            if not authority or "@" in authority:
                _fail("combined_historical_provenance_source_url_invalid")
        captured_at = _optional_datetime_text_value(
            raw["captured_at"], "historical_captured_at"
        )
        normalized_historical[key] = {
            "offline_item_id": _positive_int(raw["offline_item_id"], "combined_historical_provenance_item_id_invalid"),
            "display_name": _required_string(raw["display_name"], "combined_historical_provenance_display_name_invalid"),
            "file": file_name,
            "source_file_sha256": _required_sha256(raw["source_file_sha256"], "combined_historical_provenance_sha_invalid"),
            "historical_item_key": _optional_string(raw["historical_item_key"], "combined_historical_provenance_item_key_invalid"),
            "source_url": source_url,
            "captured_at": captured_at,
        }
    return {"market_by_observation_key": normalized_market, "historical_by_sample_key": normalized_historical}


def _normalize_market_provenance_entry(
    key: object, value: object
) -> dict[str, Any]:
    if not isinstance(key, str) or not key.startswith("review:"):
        _fail("combined_market_provenance_key_invalid")
    if not isinstance(value, Mapping):
        _fail("combined_market_provenance_entry_invalid")
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
    _require_exact_keys(value, expected, "combined_market_provenance")
    review_id = _required_string(
        value["source_review_id"], "combined_market_source_review_id_invalid"
    )
    if key != f"review:{review_id}":
        _fail("combined_market_provenance_key_mismatch")
    candidate_sha = _required_sha256(
        value["candidate_sha256"], "combined_market_candidate_sha_invalid"
    )
    capture_sha = value["capture_sha256"]
    if capture_sha is not None:
        capture_sha = _required_sha256(
            capture_sha, "combined_market_capture_sha_invalid"
        )
    edited_fields = value["edited_fields"]
    if not isinstance(edited_fields, list) or any(
        not isinstance(field, str) or not field.strip() for field in edited_fields
    ):
        _fail("combined_market_edited_fields_invalid")
    if edited_fields != sorted(set(edited_fields)):
        _fail("combined_market_edited_fields_not_canonical")
    tier_text = _required_string(
        value["quality_tier"], "combined_market_quality_tier_invalid"
    )
    try:
        tier = MarketHistoryQualityTier(tier_text)
    except ValueError as exc:
        raise CombinedCalibrationValidationError(
            "combined_market_quality_tier_invalid"
        ) from exc
    if tier is MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE:
        _fail("combined_market_quality_tier_unknown")
    page_identity = value["page_identity"]
    if page_identity is not None:
        if not isinstance(page_identity, Mapping):
            _fail("combined_market_page_identity_invalid")
        if set(page_identity) != {"origin", "market_path", "item_key"}:
            _fail("combined_market_page_identity_fields_invalid")
        origin = _required_string(
            page_identity["origin"], "combined_market_page_origin_invalid"
        )
        market_path = _required_string(
            page_identity["market_path"], "combined_market_page_path_invalid"
        )
        item_key = _optional_string(
            page_identity["item_key"], "combined_market_page_item_key_invalid"
        )
        if origin != "https://trade.gaijin.net":
            _fail("combined_market_page_origin_invalid")
        if (
            not market_path.startswith("/market/1067/")
            or "?" in market_path
            or "#" in market_path
            or len(market_path) > 1024
        ):
            _fail("combined_market_page_path_invalid")
        derived_item_key = _strict_percent_decode(
            market_path.removeprefix("/market/1067/").strip("/")
        )
        if not derived_item_key or len(derived_item_key) > 512 or item_key != derived_item_key:
            _fail("combined_market_page_item_key_invalid")
        page_identity = {
            "origin": origin,
            "market_path": market_path,
            "item_key": derived_item_key,
        }
    capture_started_at = _optional_datetime_text_value(
        value["capture_started_at"], "capture_started_at"
    )
    captured_at = _optional_datetime_text_value(value["captured_at"], "captured_at")
    final_observed_at = _datetime_text(
        _parse_datetime(value["final_observed_at"], "final_observed_at")
    )
    imported_at = _optional_datetime_text_value(value["imported_at"], "imported_at")
    observed_source = _required_string(
        value["observed_at_source"], "combined_market_observed_at_source_invalid"
    )
    client_capture_id = _optional_string(
        value["client_capture_id"], "combined_market_client_capture_id_invalid"
    )
    capture_schema = _required_string(
        value["capture_schema_version"], "combined_market_capture_schema_invalid"
    )
    if tier in {
        MarketHistoryQualityTier.BROWSER_CAPTURED,
        MarketHistoryQualityTier.BROWSER_CAPTURED_USER_TIME_EDITED,
    }:
        if (
            capture_schema != "point_in_time_capture_v1"
            or client_capture_id is None
            or capture_started_at is None
            or captured_at is None
            or page_identity is None
            or capture_sha is None
        ):
            _fail("combined_market_browser_capture_incomplete")
        try:
            parsed_capture_id = uuid.UUID(client_capture_id)
        except ValueError as exc:
            raise CombinedCalibrationValidationError(
                "combined_market_client_capture_id_invalid"
            ) from exc
        if parsed_capture_id.version != 4:
            _fail("combined_market_client_capture_id_invalid")
        started = _parse_datetime(capture_started_at, "capture_started_at")
        captured = _parse_datetime(captured_at, "captured_at")
        if started > captured:
            _fail("combined_market_capture_time_order_invalid")
        if (captured - started).total_seconds() > 30:
            _fail("combined_market_capture_duration_too_long")
        expected_source = (
            "browser_capture"
            if tier is MarketHistoryQualityTier.BROWSER_CAPTURED
            else "user_edited"
        )
        if observed_source != expected_source:
            _fail("combined_market_observed_at_source_tier_mismatch")
        if tier is MarketHistoryQualityTier.BROWSER_CAPTURED:
            if final_observed_at != captured_at or "observed_at" in edited_fields:
                _fail("combined_market_browser_capture_time_mismatch")
        elif "observed_at" not in edited_fields:
            _fail("combined_market_user_edited_marker_missing")
    elif tier is MarketHistoryQualityTier.LEGACY_SERVER_TIME:
        if (
            capture_schema != "legacy_v1"
            or observed_source != "review_created_default"
            or client_capture_id is not None
            or capture_started_at is not None
            or captured_at is not None
            or page_identity is not None
        ):
            _fail("combined_market_legacy_provenance_invalid")
    return {
        "source_review_id": review_id,
        "candidate_version": _required_string(
            value["candidate_version"], "combined_market_candidate_version_invalid"
        ),
        "candidate_sha256": candidate_sha,
        "client_capture_id": client_capture_id,
        "capture_schema_version": capture_schema,
        "capture_started_at": capture_started_at,
        "captured_at": captured_at,
        "final_observed_at": final_observed_at,
        "observed_at_source": observed_source,
        "page_identity": page_identity,
        "capture_sha256": capture_sha,
        "extension_version": _optional_string(
            value["extension_version"], "combined_market_extension_version_invalid"
        ),
        "incomplete_quantities_acknowledged": _required_bool(
            value["incomplete_quantities_acknowledged"],
            "combined_market_acknowledgement_invalid",
        ),
        "edited_fields": edited_fields,
        "quality_tier": tier.value,
        "imported_at": imported_at,
        "page_item_key_matches_database_item": _optional_bool(
            value["page_item_key_matches_database_item"],
            "combined_market_page_item_match_invalid",
        ),
    }


def _source_fingerprint_provenance(value: object) -> object:
    canonical = _canonical_copy(value)
    if not isinstance(canonical, dict):
        return canonical
    market = canonical.get("market_by_observation_key")
    if isinstance(market, dict):
        for entry in market.values():
            if isinstance(entry, dict):
                entry.pop("imported_at", None)
    return canonical


def _optional_datetime_text_value(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _datetime_text(_parse_datetime(value, field))


def _optional_bool(value: object, code: str) -> bool | None:
    if value is None:
        return None
    return _required_bool(value, code)


def _normalize_diagnostics(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("combined_diagnostics_invalid")
    expected = {"historical_sample_count", "market_item_count", "mapping_count", "combined_item_count", "unmapped_historical_sample_count", "unmapped_market_item_count", "observation_count", "trade_bucket_count", "quality_tier_counts"}
    _require_exact_keys(value, expected, "combined_diagnostics")
    quality = value["quality_tier_counts"]
    if not isinstance(quality, Mapping):
        _fail("combined_diagnostics_quality_invalid")
    normalized_quality: dict[str, int] = {}
    for key in sorted(quality):
        try:
            MarketHistoryQualityTier(key)
        except ValueError as exc:
            raise CombinedCalibrationValidationError("combined_diagnostics_quality_tier_invalid") from exc
        normalized_quality[key] = _nonnegative_int(quality[key], "combined_diagnostics_quality_count_invalid")
    normalized = {field: _nonnegative_int(value[field], f"combined_diagnostics_{field}_invalid") for field in expected - {"quality_tier_counts"}}
    normalized["quality_tier_counts"] = normalized_quality
    return normalized


def _validate_cross_contract(payload: Mapping[str, Any]) -> None:
    quote_items = payload["quote_only_baseline"]["items"]
    trade_items = payload["trade_support_evidence"]["items"]
    mappings = payload["mapping_evidence"]
    quote_by_id = {item["item_id"]: item for item in quote_items}
    trade_by_id = {item["item_id"]: item for item in trade_items}
    mapping_by_id = {item["database_item_id"]: item for item in mappings}
    if set(quote_by_id) != set(trade_by_id) or set(quote_by_id) != set(mapping_by_id):
        _fail("combined_item_set_mismatch")
    market_provenance = payload["provenance"]["market_by_observation_key"]
    historical_provenance = payload["provenance"]["historical_by_sample_key"]
    mapping_manifest = _mapping_manifest_from_evidence(mappings)
    if (
        payload["input_fingerprints"]["item_mapping_fingerprint"]
        != item_mapping_manifest_sha256(mapping_manifest)
    ):
        _fail("combined_mapping_fingerprint_mismatch")
    all_observation_keys: set[str] = set()
    quality_counts: dict[str, int] = {}
    total_buckets = 0
    for item_id in sorted(quote_by_id):
        quote = quote_by_id[item_id]
        trade = trade_by_id[item_id]
        mapping = mapping_by_id[item_id]
        if quote["external_key"] != trade["external_key"] or quote["external_key"] != mapping["database_external_key"]:
            _fail("combined_item_external_key_mismatch")
        sample_key = mapping["offline_sample_key"]
        historical_meta = historical_provenance.get(sample_key)
        if historical_meta is None:
            _fail("combined_historical_provenance_missing")
        if historical_meta["offline_item_id"] != mapping["historical_offline_item_id"] or historical_meta["historical_item_key"] != mapping["historical_item_key"]:
            _fail("combined_historical_provenance_mismatch")
        observations_by_key = {obs["observation_key"]: obs for obs in quote["observations"]}
        availability_by_key = {entry["observation_key"]: entry for entry in trade["observation_availability"]}
        if set(observations_by_key) != set(availability_by_key):
            _fail("combined_availability_observation_set_mismatch")
        buckets = tuple(_historical_bucket_from_payload(item_id, raw) for raw in trade["buckets"])
        total_buckets += len(buckets)
        for key, observation in observations_by_key.items():
            if key in all_observation_keys:
                _fail("combined_observation_key_duplicate")
            all_observation_keys.add(key)
            if key not in market_provenance:
                _fail("combined_market_provenance_missing")
            linked_provenance = market_provenance[key]
            tier = observation["quality_tier"]
            if linked_provenance["quality_tier"] != tier:
                _fail("combined_market_provenance_quality_tier_mismatch")
            if linked_provenance["final_observed_at"] != observation["observed_at"]:
                _fail("combined_market_provenance_observed_at_mismatch")
            page_identity = linked_provenance["page_identity"]
            if page_identity is not None:
                page_item_key = page_identity.get("item_key")
                if page_item_key is not None and page_item_key != quote["external_key"]:
                    _fail("combined_market_page_item_key_mismatch")
                if linked_provenance["page_item_key_matches_database_item"] is not True:
                    _fail("combined_market_page_item_match_not_confirmed")
            if (
                observation["observed_bid_quantity"] is None
                or observation["observed_ask_quantity"] is None
            ) and not linked_provenance["incomplete_quantities_acknowledged"]:
                _fail("combined_market_incomplete_quantities_not_acknowledged")
            quality_counts[tier] = quality_counts.get(tier, 0) + 1
            expected = _availability_payload(_market_observation_from_payload(observation), buckets)
            if availability_by_key[key] != expected:
                _fail("combined_no_lookahead_availability_mismatch")
    if set(market_provenance) != all_observation_keys:
        _fail("combined_market_provenance_set_mismatch")
    if set(historical_provenance) != {item["offline_sample_key"] for item in mappings}:
        _fail("combined_historical_provenance_set_mismatch")
    diagnostics = payload["diagnostics"]
    if diagnostics["mapping_count"] != len(mappings) or diagnostics["combined_item_count"] != len(mappings):
        _fail("combined_diagnostics_mapping_count_mismatch")
    if diagnostics["observation_count"] != len(all_observation_keys):
        _fail("combined_diagnostics_observation_count_mismatch")
    if diagnostics["trade_bucket_count"] != total_buckets:
        _fail("combined_diagnostics_bucket_count_mismatch")
    if diagnostics["quality_tier_counts"] != {key: quality_counts[key] for key in sorted(quality_counts)}:
        _fail("combined_diagnostics_quality_count_mismatch")
    if diagnostics["historical_sample_count"] < len(mappings) or diagnostics["market_item_count"] < len(mappings):
        _fail("combined_diagnostics_source_count_invalid")
    if diagnostics["unmapped_historical_sample_count"] != diagnostics["historical_sample_count"] - len(mappings):
        _fail("combined_diagnostics_unmapped_historical_mismatch")
    if diagnostics["unmapped_market_item_count"] != diagnostics["market_item_count"] - len(mappings):
        _fail("combined_diagnostics_unmapped_market_mismatch")


def _quote_observation_payload(
    observation: MarketObservation, market_dataset: LoadedMarketHistoryDataset
) -> dict[str, Any]:
    key = observation.observation_key
    if key is None or key not in market_dataset.provenance_by_key:
        _fail("combined_market_provenance_missing")
    provenance = market_dataset.provenance_by_key[key]
    tier = provenance.get("quality_tier")
    if not isinstance(tier, str):
        _fail("combined_market_quality_tier_missing")
    return {
        "observed_at": _datetime_text(observation.observed_at),
        "best_bid_price": _optional_decimal_text(observation.best_bid),
        "best_ask_price": _decimal_text_required(observation.best_ask, "combined_market_best_ask_missing"),
        "bid_count": observation.bid_count,
        "ask_count": observation.ask_count,
        "estimated_volume": _optional_decimal_text(observation.estimated_volume),
        "observed_bid_quantity": observation.observed_bid_quantity,
        "observed_ask_quantity": observation.observed_ask_quantity,
        "quantity_semantics": observation.quantity_semantics,
        "source_type": observation.source_type,
        "review_status": observation.review_status,
        "quality_tier": tier,
        "observation_key": key,
        "provenance_ref": key,
    }


def _market_observation_from_payload(value: Mapping[str, Any]) -> MarketObservation:
    return MarketObservation(
        observed_at=_parse_datetime(value["observed_at"], "observed_at"),
        best_ask=_positive_decimal(value["best_ask_price"], "combined_quote_best_ask_invalid"),
        best_bid=(None if value["best_bid_price"] is None else _nonnegative_decimal(value["best_bid_price"], "combined_quote_best_bid_invalid")),
        ask_count=value["ask_count"],
        bid_count=value["bid_count"],
        estimated_volume=(None if value["estimated_volume"] is None else _nonnegative_decimal(value["estimated_volume"], "combined_quote_estimated_volume_invalid")),
        observation_key=value["observation_key"],
        observed_ask_quantity=value["observed_ask_quantity"],
        observed_bid_quantity=value["observed_bid_quantity"],
        quantity_semantics=value["quantity_semantics"],
        source_type=value["source_type"],
        review_status=value["review_status"],
    )


def _remap_bucket(bucket: HistoricalTradeBucket, item_id: int) -> HistoricalTradeBucket:
    return HistoricalTradeBucket(
        item_id=item_id,
        granularity=bucket.granularity,
        bucket_start_utc=bucket.bucket_start_utc,
        bucket_duration_seconds=bucket.bucket_duration_seconds,
        reported_vwap_price=bucket.reported_vwap_price,
        reported_trade_volume=bucket.reported_trade_volume,
        price_semantics=bucket.price_semantics,
        volume_semantics=bucket.volume_semantics,
        source_schema_version=bucket.source_schema_version,
    )


def _trade_bucket_payload(bucket: HistoricalTradeBucket) -> dict[str, Any]:
    end = bucket.bucket_end_utc
    return {
        "bucket_key": _bucket_key(bucket),
        "granularity": bucket.granularity.value,
        "bucket_start_utc": _datetime_text(bucket.bucket_start_utc),
        "bucket_end_utc": _datetime_text(end),
        "available_at": _datetime_text(end),
        "bucket_duration_seconds": bucket.bucket_duration_seconds,
        "reported_vwap_price": _decimal_text(bucket.reported_vwap_price),
        "reported_trade_volume": bucket.reported_trade_volume,
        "price_semantics": bucket.price_semantics,
        "volume_semantics": bucket.volume_semantics,
        "source_schema_version": bucket.source_schema_version,
    }


def _historical_bucket_from_payload(item_id: int, value: Mapping[str, Any]) -> HistoricalTradeBucket:
    return HistoricalTradeBucket(
        item_id=item_id,
        granularity=HistoricalTradeGranularity(value["granularity"]),
        bucket_start_utc=_parse_datetime(value["bucket_start_utc"], "bucket_start_utc"),
        bucket_duration_seconds=value["bucket_duration_seconds"],
        reported_vwap_price=_positive_decimal(value["reported_vwap_price"], "combined_trade_vwap_invalid"),
        reported_trade_volume=value["reported_trade_volume"],
        price_semantics=value["price_semantics"],
        volume_semantics=value["volume_semantics"],
        source_schema_version=value["source_schema_version"],
    )


def _availability_payload(
    observation: MarketObservation,
    buckets: Sequence[HistoricalTradeBucket],
) -> dict[str, Any]:
    if observation.observation_key is None:
        _fail("combined_observation_key_required")
    selected = _select_available_buckets(buckets, observation.observed_at)
    hourly_count = sum(
        bucket.granularity is HistoricalTradeGranularity.HOUR_1
        for bucket in selected
    )
    return {
        "observation_key": observation.observation_key,
        "knowledge_cutoff": _datetime_text(observation.observed_at),
        "available_bucket_keys": sorted(_bucket_key(bucket) for bucket in selected),
        "selected_hourly_bucket_count": hourly_count,
        "selected_daily_fallback_count": len(selected) - hourly_count,
    }


def select_available_trade_buckets(
    buckets: Sequence[HistoricalTradeBucket], knowledge_cutoff: datetime
) -> tuple[HistoricalTradeBucket, ...]:
    """Return only completed buckets, with hourly data replacing daily fallback."""

    return _select_available_buckets(buckets, _aware_utc(knowledge_cutoff, "knowledge_cutoff"))


def _select_available_buckets(
    buckets: Sequence[HistoricalTradeBucket], knowledge_cutoff: datetime
) -> tuple[HistoricalTradeBucket, ...]:
    eligible = tuple(bucket for bucket in buckets if bucket.bucket_end_utc <= knowledge_cutoff)
    hourly_days = {
        bucket.bucket_start_utc.date()
        for bucket in eligible
        if bucket.granularity is HistoricalTradeGranularity.HOUR_1
    }
    selected = tuple(
        bucket
        for bucket in eligible
        if bucket.granularity is HistoricalTradeGranularity.HOUR_1
        or bucket.bucket_start_utc.date() not in hourly_days
    )
    return tuple(sorted(selected, key=lambda bucket: (bucket.bucket_end_utc, bucket.bucket_duration_seconds, _bucket_key(bucket))))


def _bucket_key(bucket: HistoricalTradeBucket) -> str:
    return f"{bucket.granularity.value}:{_datetime_text(bucket.bucket_start_utc)}"


def _validated_item_mapping_manifest(
    manifest: ItemMappingManifest,
) -> ItemMappingManifest:
    if not isinstance(manifest, ItemMappingManifest):
        _fail("combined_mapping_manifest_invalid")
    payload = {
        "schema_version": manifest.schema_version,
        "mappings": [
            {
                "offline_sample_key": entry.offline_sample_key,
                "database_item_id": entry.database_item_id,
                "database_external_key": entry.database_external_key,
                "confirmed_by_user": entry.confirmed_by_user,
            }
            for entry in manifest.mappings
        ],
    }
    try:
        return parse_item_mapping_manifest(payload)
    except MarketHistoryExportValidationError as exc:
        raise CombinedCalibrationValidationError(exc.code) from exc


def _mapping_manifest_from_evidence(
    mappings: Sequence[Mapping[str, Any]],
) -> ItemMappingManifest:
    manifest = ItemMappingManifest(
        schema_version=ITEM_MAPPING_SCHEMA_VERSION,
        mappings=tuple(_mapping_entry_from_evidence(value) for value in mappings),
    )
    return _validated_item_mapping_manifest(manifest)


def _mapping_entry_from_evidence(value: Mapping[str, Any]) -> ItemMappingEntry:
    return ItemMappingEntry(
        offline_sample_key=value["offline_sample_key"],
        database_item_id=value["database_item_id"],
        database_external_key=value["database_external_key"],
        confirmed_by_user=True,
    )


def _canonical_copy(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return _datetime_text(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_copy(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical_copy(item) for item in value]
    _fail("combined_provenance_value_unsupported")


def _strict_percent_decode(value: str) -> str:
    encoded = bytearray()
    index = 0
    while index < len(value):
        if value[index] == "%":
            if index + 2 >= len(value) or any(
                character not in "0123456789abcdefABCDEF"
                for character in value[index + 1 : index + 3]
            ):
                _fail("combined_market_page_item_key_encoding_invalid")
            encoded.append(int(value[index + 1 : index + 3], 16))
            index += 3
            continue
        encoded.extend(value[index].encode("utf-8"))
        index += 1
    try:
        return bytes(encoded).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CombinedCalibrationValidationError(
            "combined_market_page_item_key_encoding_invalid"
        ) from exc


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        _fail(f"{label}_fields_invalid")


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


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(code)
    return value


def _optional_nonnegative_int(value: object, code: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, code)


def _positive_decimal(value: object, code: str) -> Decimal:
    parsed = _decimal(value, code)
    if parsed <= Decimal("0"):
        _fail(code)
    return parsed


def _nonnegative_decimal(value: object, code: str) -> Decimal:
    parsed = _decimal(value, code)
    if parsed < Decimal("0"):
        _fail(code)
    return parsed


def _decimal(value: object, code: str) -> Decimal:
    if not isinstance(value, str) or not value:
        _fail(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CombinedCalibrationValidationError(code) from exc
    if not parsed.is_finite():
        _fail(code)
    return parsed


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _decimal_text_required(value: Decimal | None, code: str) -> str:
    if value is None:
        _fail(code)
    return _decimal_text(value)


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


def _optional_nonnegative_decimal_text(value: object, code: str) -> str | None:
    if value is None:
        return None
    return _decimal_text(_nonnegative_decimal(value, code))


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        _fail(f"combined_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CombinedCalibrationValidationError(f"combined_{field}_invalid") from exc
    return _aware_utc(parsed, field)


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail(f"combined_{field}_timezone_required")
    return value.astimezone(UTC)


def _datetime_text(value: datetime) -> str:
    return _aware_utc(value, "datetime").isoformat().replace("+00:00", "Z")


def _required_sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        _fail(code)
    return value


def _fail(code: str, message: str | None = None) -> None:
    raise CombinedCalibrationValidationError(code, message)
