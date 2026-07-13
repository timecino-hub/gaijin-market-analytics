"""Deterministic Round 6C calibration examples and walk-forward evaluation.

The evaluation layer consumes the validated Round 6B combined dataset.  Quote
features and targets are derived only from reviewed quote observations.  Trade
history is retained in a separate descriptive support block and never changes
OpportunityScoreV1 inputs, quote-only predictions, or quote-only metrics.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from enum import Enum
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
from gaijin_market_analytics.datasets.combined_calibration import (
    LoadedCombinedCalibrationDataset,
    ObservationTradeAvailability,
)
from gaijin_market_analytics.datasets.market_history_export import (
    MarketHistoryQualityTier,
)


CALIBRATION_EVALUATION_SCHEMA_VERSION = "round6_calibration_evaluation_v1"
WALK_FORWARD_CONFIG_SCHEMA_VERSION = "round6_walk_forward_config_v1"
CALIBRATION_EXAMPLE_VERSION = "1.0.0"
TARGET_DEFINITION_NAME = "next_complete_quote_mid_price_change"
TARGET_DEFINITION_VERSION = "1.0.0"
METRIC_SCHEMA_VERSION = "round6_calibration_metrics_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_QUALITY_TIERS = frozenset(tier.value for tier in MarketHistoryQualityTier)


class CalibrationEvaluationValidationError(ValueError):
    """Stable validation failure without embedding raw user data."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class EvaluationTargetStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class EvaluationTargetUnavailableReason(str, Enum):
    CURRENT_QUOTE_INCOMPLETE = "current_quote_incomplete"
    NO_FUTURE_OBSERVATION = "no_future_observation"
    FUTURE_QUOTE_INCOMPLETE = "future_quote_incomplete"


@dataclass(frozen=True, slots=True)
class CalibrationEvaluationConfig:
    target_horizon_seconds: int
    minimum_train_timestamps: int
    test_timestamp_count: int
    step_timestamp_count: int
    embargo_seconds: int = 0
    included_quality_tiers: tuple[str, ...] = (
        MarketHistoryQualityTier.BROWSER_CAPTURED.value,
        MarketHistoryQualityTier.BROWSER_CAPTURED_USER_TIME_EDITED.value,
    )

    def __post_init__(self) -> None:
        for name in (
            "target_horizon_seconds",
            "minimum_train_timestamps",
            "test_timestamp_count",
            "step_timestamp_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                _fail(f"calibration_config_{name}_invalid")
        if (
            isinstance(self.embargo_seconds, bool)
            or not isinstance(self.embargo_seconds, int)
            or self.embargo_seconds < 0
        ):
            _fail("calibration_config_embargo_seconds_invalid")
        if self.step_timestamp_count < self.test_timestamp_count:
            _fail("calibration_config_overlapping_test_windows")
        tiers = tuple(sorted(set(self.included_quality_tiers)))
        if not tiers or any(tier not in _SUPPORTED_QUALITY_TIERS for tier in tiers):
            _fail("calibration_config_quality_tiers_invalid")
        if MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE.value in tiers:
            _fail("calibration_config_unknown_quality_tier_forbidden")
        object.__setattr__(self, "included_quality_tiers", tiers)


@dataclass(frozen=True, slots=True)
class LoadedCalibrationEvaluationDataset:
    config: CalibrationEvaluationConfig
    examples: tuple[Mapping[str, Any], ...]
    folds: tuple[Mapping[str, Any], ...]
    metrics: Mapping[str, Any]
    fingerprints: Mapping[str, str]
    diagnostics: Mapping[str, Any]


def parse_calibration_evaluation_config(payload: object) -> CalibrationEvaluationConfig:
    if not isinstance(payload, Mapping):
        _fail("calibration_config_top_level_invalid")
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "target_horizon_seconds",
            "minimum_train_timestamps",
            "test_timestamp_count",
            "step_timestamp_count",
            "embargo_seconds",
            "included_quality_tiers",
        },
        "calibration_config",
    )
    if payload["schema_version"] != WALK_FORWARD_CONFIG_SCHEMA_VERSION:
        _fail("calibration_config_schema_unsupported")
    tiers = payload["included_quality_tiers"]
    if not isinstance(tiers, list) or any(not isinstance(value, str) for value in tiers):
        _fail("calibration_config_quality_tiers_invalid")
    return CalibrationEvaluationConfig(
        target_horizon_seconds=_required_int(
            payload["target_horizon_seconds"],
            "calibration_config_target_horizon_seconds_invalid",
            positive=True,
        ),
        minimum_train_timestamps=_required_int(
            payload["minimum_train_timestamps"],
            "calibration_config_minimum_train_timestamps_invalid",
            positive=True,
        ),
        test_timestamp_count=_required_int(
            payload["test_timestamp_count"],
            "calibration_config_test_timestamp_count_invalid",
            positive=True,
        ),
        step_timestamp_count=_required_int(
            payload["step_timestamp_count"],
            "calibration_config_step_timestamp_count_invalid",
            positive=True,
        ),
        embargo_seconds=_required_int(
            payload["embargo_seconds"],
            "calibration_config_embargo_seconds_invalid",
            minimum=0,
        ),
        included_quality_tiers=tuple(tiers),
    )


def load_calibration_evaluation_config(path: str | Path) -> CalibrationEvaluationConfig:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail("calibration_config_file_not_found")
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("calibration_config_json_invalid")
    return parse_calibration_evaluation_config(value)


def calibration_evaluation_config_payload(
    config: CalibrationEvaluationConfig,
) -> dict[str, Any]:
    return {
        "schema_version": WALK_FORWARD_CONFIG_SCHEMA_VERSION,
        "target_horizon_seconds": config.target_horizon_seconds,
        "minimum_train_timestamps": config.minimum_train_timestamps,
        "test_timestamp_count": config.test_timestamp_count,
        "step_timestamp_count": config.step_timestamp_count,
        "embargo_seconds": config.embargo_seconds,
        "included_quality_tiers": list(config.included_quality_tiers),
    }


def build_calibration_evaluation_dataset(
    *,
    combined_dataset: LoadedCombinedCalibrationDataset,
    config: CalibrationEvaluationConfig,
) -> dict[str, Any]:
    """Build deterministic quote targets, support features, folds, and metrics."""

    if not isinstance(combined_dataset, LoadedCombinedCalibrationDataset):
        _fail("calibration_combined_dataset_invalid")
    if not isinstance(config, CalibrationEvaluationConfig):
        _fail("calibration_config_invalid")

    market_by_id = _unique_histories(combined_dataset.market_histories)
    trade_by_id = _unique_trade_histories(
        combined_dataset.historical_trade_histories
    )
    mapping_by_id = {
        entry.database_item_id: entry
        for entry in combined_dataset.mapping_manifest.mappings
    }
    if len(mapping_by_id) != len(combined_dataset.mapping_manifest.mappings):
        _fail("calibration_mapping_item_duplicate")
    if set(market_by_id) != set(trade_by_id) or set(market_by_id) != set(mapping_by_id):
        _fail("calibration_combined_item_sets_mismatch")

    examples: list[dict[str, Any]] = []
    excluded_quality_tier_count = 0
    target_unavailable_reasons: dict[str, int] = {}
    for item_id in sorted(market_by_id):
        market_history = market_by_id[item_id]
        trade_history = trade_by_id[item_id]
        mapping = mapping_by_id[item_id]
        bucket_by_key = {
            _bucket_key(bucket): bucket for bucket in trade_history.buckets
        }
        if len(bucket_by_key) != len(trade_history.buckets):
            _fail("calibration_trade_bucket_key_duplicate")
        observations = market_history.observations
        eligible_observations: list[tuple[MarketObservation, str]] = []
        for observation in observations:
            key = observation.observation_key
            if key is None:
                _fail("calibration_observation_key_required")
            provenance = combined_dataset.market_provenance_by_key.get(key)
            if not isinstance(provenance, Mapping):
                _fail("calibration_observation_provenance_missing")
            quality_tier = provenance.get("quality_tier")
            if quality_tier not in _SUPPORTED_QUALITY_TIERS:
                _fail("calibration_quality_tier_invalid")
            if quality_tier not in config.included_quality_tiers:
                excluded_quality_tier_count += 1
                continue
            eligible_observations.append((observation, quality_tier))
        for index, (observation, quality_tier) in enumerate(eligible_observations):
            key = observation.observation_key
            assert key is not None
            availability = combined_dataset.availability_by_observation_key.get(key)
            if not isinstance(availability, ObservationTradeAvailability):
                _fail("calibration_trade_availability_missing")
            if availability.knowledge_cutoff != observation.observed_at:
                _fail("calibration_trade_knowledge_cutoff_mismatch")
            selected_buckets = _selected_buckets(
                availability=availability,
                bucket_by_key=bucket_by_key,
            )
            quote_features = _quote_features(observation, quality_tier)
            trade_features = _trade_features(
                selected_buckets,
                availability,
            )
            target = _future_target(
                current=observation,
                following=tuple(value[0] for value in eligible_observations[index + 1 :]),
                horizon_seconds=config.target_horizon_seconds,
            )
            if target["status"] == EvaluationTargetStatus.UNAVAILABLE.value:
                reason = target["unavailable_reason"]
                target_unavailable_reasons[reason] = (
                    target_unavailable_reasons.get(reason, 0) + 1
                )
            example_key = _example_key(
                item_id=item_id,
                observation_key=key,
                observed_at=observation.observed_at,
            )
            examples.append(
                {
                    "example_key": example_key,
                    "item_id": item_id,
                    "external_key": mapping.database_external_key,
                    "observation_key": key,
                    "observed_at": _datetime_text(observation.observed_at),
                    "knowledge_cutoff": _datetime_text(observation.observed_at),
                    "quality_tier": quality_tier,
                    "quote_only_features": quote_features,
                    "trade_support_features": trade_features,
                    "target": target,
                    "source_refs": {
                        "offline_sample_key": mapping.offline_sample_key,
                        "market_provenance_ref": key,
                        "trade_bucket_keys": list(availability.available_bucket_keys),
                    },
                }
            )

    examples.sort(key=_example_payload_sort_key)
    if len({value["example_key"] for value in examples}) != len(examples):
        _fail("calibration_example_key_duplicate")
    folds = _build_walk_forward_folds(examples, config)
    metrics = _build_metrics(examples, folds)
    config_payload = calibration_evaluation_config_payload(config)
    target_definition = {
        "name": TARGET_DEFINITION_NAME,
        "version": TARGET_DEFINITION_VERSION,
        "target_horizon_seconds": config.target_horizon_seconds,
        "semantics": "future reviewed quote movement; not execution or fill",
        "future_selection": "first complete quote strictly after observation within horizon",
        "target_unavailable_is_negative": False,
    }
    payload: dict[str, Any] = {
        "schema_version": CALIBRATION_EVALUATION_SCHEMA_VERSION,
        "input_fingerprints": {
            "combined_source_fingerprint": combined_dataset.combined_source_fingerprint,
            "combined_typed_fingerprint": combined_dataset.combined_typed_fingerprint,
            "quote_only_baseline_fingerprint": (
                combined_dataset.quote_only_baseline_fingerprint
            ),
            "trade_support_evidence_fingerprint": (
                combined_dataset.trade_support_evidence_fingerprint
            ),
        },
        "example_construction": {
            "version": CALIBRATION_EXAMPLE_VERSION,
            "trade_support_does_not_modify_score": True,
            "fill_claim": False,
        },
        "target_definition": target_definition,
        "walk_forward_config": config_payload,
        "examples": examples,
        "folds": folds,
        "metrics": metrics,
        "fingerprints": {},
        "diagnostics": {
            "source_item_count": len(market_by_id),
            "source_observation_count": sum(
                len(history.observations) for history in market_by_id.values()
            ),
            "included_example_count": len(examples),
            "excluded_quality_tier_count": excluded_quality_tier_count,
            "target_available_count": sum(
                value["target"]["status"] == EvaluationTargetStatus.AVAILABLE.value
                for value in examples
            ),
            "target_unavailable_count": sum(
                value["target"]["status"] == EvaluationTargetStatus.UNAVAILABLE.value
                for value in examples
            ),
            "target_unavailable_reason_counts": {
                key: target_unavailable_reasons[key]
                for key in sorted(target_unavailable_reasons)
            },
            "fold_count": len(folds),
        },
    }
    payload["fingerprints"] = _fingerprints(payload)
    parse_calibration_evaluation_dataset(payload)
    return payload


def calibration_evaluation_json_bytes(
    payload: Mapping[str, Any], *, pretty: bool = False
) -> bytes:
    normalized = _normalize_payload(payload, verify_fingerprints=True)
    if pretty:
        text = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n"
    else:
        text = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return text.encode("utf-8")


def write_calibration_evaluation_dataset(
    path: str | Path,
    payload: Mapping[str, Any],
    *,
    pretty: bool = False,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = calibration_evaluation_json_bytes(payload, pretty=pretty)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_calibration_evaluation_dataset(
    path: str | Path,
) -> LoadedCalibrationEvaluationDataset:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail("calibration_dataset_file_not_found")
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("calibration_dataset_json_invalid")
    return parse_calibration_evaluation_dataset(value)


def parse_calibration_evaluation_dataset(
    payload: object,
) -> LoadedCalibrationEvaluationDataset:
    normalized = _normalize_payload(payload, verify_fingerprints=True)
    return LoadedCalibrationEvaluationDataset(
        config=parse_calibration_evaluation_config(
            normalized["walk_forward_config"]
        ),
        examples=tuple(normalized["examples"]),
        folds=tuple(normalized["folds"]),
        metrics=normalized["metrics"],
        fingerprints=normalized["fingerprints"],
        diagnostics=normalized["diagnostics"],
    )


def calibration_example_source_fingerprint(payload: Mapping[str, Any]) -> str:
    return _sha256_json(
        {
            "schema_version": payload.get("schema_version"),
            "input_fingerprints": payload.get("input_fingerprints"),
            "example_construction": payload.get("example_construction"),
            "target_definition": payload.get("target_definition"),
            "example_profile": _example_profile(payload),
            "examples": payload.get("examples"),
        }
    )


def calibration_example_typed_fingerprint(payload: Mapping[str, Any]) -> str:
    examples = payload.get("examples")
    typed_examples: list[dict[str, Any]] = []
    if isinstance(examples, list):
        for value in examples:
            if isinstance(value, Mapping):
                typed_examples.append(
                    {
                        "item_id": value.get("item_id"),
                        "external_key": value.get("external_key"),
                        "observed_at": value.get("observed_at"),
                        "quality_tier": value.get("quality_tier"),
                        "quote_only_features": value.get("quote_only_features"),
                        "trade_support_features": value.get("trade_support_features"),
                        "target": value.get("target"),
                    }
                )
    return _sha256_json(
        {
            "schema_version": payload.get("schema_version"),
            "target_definition": payload.get("target_definition"),
            "example_profile": _example_profile(payload),
            "examples": typed_examples,
        }
    )


def _example_profile(payload: Mapping[str, Any]) -> dict[str, Any]:
    config = payload.get("walk_forward_config")
    tiers = (
        config.get("included_quality_tiers")
        if isinstance(config, Mapping)
        else None
    )
    return {"included_quality_tiers": tiers}


def quote_only_features_fingerprint(payload: Mapping[str, Any]) -> str:
    examples = payload.get("examples")
    values = []
    if isinstance(examples, list):
        for example in examples:
            if isinstance(example, Mapping):
                values.append(
                    {
                        "item_id": example.get("item_id"),
                        "observed_at": example.get("observed_at"),
                        "quality_tier": example.get("quality_tier"),
                        "quote_only_features": example.get("quote_only_features"),
                        "target": example.get("target"),
                    }
                )
    return _sha256_json(
        {
            "target_definition": payload.get("target_definition"),
            "included_quality_tiers": (
                payload.get("walk_forward_config", {}).get(
                    "included_quality_tiers"
                )
                if isinstance(payload.get("walk_forward_config"), Mapping)
                else None
            ),
            "examples": values,
        }
    )


def trade_support_features_fingerprint(payload: Mapping[str, Any]) -> str:
    examples = payload.get("examples")
    values = []
    if isinstance(examples, list):
        for example in examples:
            if isinstance(example, Mapping):
                values.append(
                    {
                        "item_id": example.get("item_id"),
                        "observed_at": example.get("observed_at"),
                        "trade_support_features": example.get(
                            "trade_support_features"
                        ),
                    }
                )
    return _sha256_json({"examples": values})


def walk_forward_plan_fingerprint(payload: Mapping[str, Any]) -> str:
    return _sha256_json(
        {
            "walk_forward_config": payload.get("walk_forward_config"),
            "folds": payload.get("folds"),
        }
    )


def evaluation_report_fingerprint(payload: Mapping[str, Any]) -> str:
    fingerprints = payload.get("fingerprints")
    base = {}
    if isinstance(fingerprints, Mapping):
        base = {
            key: fingerprints.get(key)
            for key in (
                "quote_only_features_fingerprint",
                "trade_support_features_fingerprint",
                "calibration_example_source_fingerprint",
                "calibration_example_typed_fingerprint",
                "walk_forward_plan_fingerprint",
            )
        }
    return _sha256_json(
        {
            "schema_version": payload.get("schema_version"),
            "base_fingerprints": base,
            "metrics": payload.get("metrics"),
            "diagnostics": payload.get("diagnostics"),
        }
    )


def _fingerprints(payload: Mapping[str, Any]) -> dict[str, str]:
    partial = {
        "quote_only_features_fingerprint": quote_only_features_fingerprint(payload),
        "trade_support_features_fingerprint": trade_support_features_fingerprint(
            payload
        ),
        "calibration_example_source_fingerprint": (
            calibration_example_source_fingerprint(payload)
        ),
        "calibration_example_typed_fingerprint": (
            calibration_example_typed_fingerprint(payload)
        ),
        "walk_forward_plan_fingerprint": walk_forward_plan_fingerprint(payload),
    }
    temporary = dict(payload)
    temporary["fingerprints"] = partial
    partial["evaluation_report_fingerprint"] = evaluation_report_fingerprint(
        temporary
    )
    return partial


def _normalize_payload(
    payload: object, *, verify_fingerprints: bool
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        _fail("calibration_dataset_top_level_invalid")
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "input_fingerprints",
            "example_construction",
            "target_definition",
            "walk_forward_config",
            "examples",
            "folds",
            "metrics",
            "fingerprints",
            "diagnostics",
        },
        "calibration_dataset",
    )
    if payload["schema_version"] != CALIBRATION_EVALUATION_SCHEMA_VERSION:
        _fail("calibration_dataset_schema_unsupported")
    input_fingerprints = _normalize_input_fingerprints(payload["input_fingerprints"])
    construction = _normalize_construction(payload["example_construction"])
    target_definition = _normalize_target_definition(payload["target_definition"])
    config = parse_calibration_evaluation_config(payload["walk_forward_config"])
    config_payload = calibration_evaluation_config_payload(config)
    if target_definition["target_horizon_seconds"] != config.target_horizon_seconds:
        _fail("calibration_target_config_horizon_mismatch")
    examples = _normalize_examples(payload["examples"], config)
    _validate_targets_against_examples(examples, config.target_horizon_seconds)
    folds = _normalize_folds(payload["folds"], examples, config)
    expected_metrics = _build_metrics(examples, folds)
    metrics = _normalize_metrics(payload["metrics"])
    if metrics != expected_metrics:
        _fail("calibration_metrics_mismatch")
    fingerprints = _normalize_fingerprints(payload["fingerprints"])
    diagnostics = _normalize_diagnostics(payload["diagnostics"], examples, folds)
    normalized: dict[str, Any] = {
        "schema_version": CALIBRATION_EVALUATION_SCHEMA_VERSION,
        "input_fingerprints": input_fingerprints,
        "example_construction": construction,
        "target_definition": target_definition,
        "walk_forward_config": config_payload,
        "examples": examples,
        "folds": folds,
        "metrics": metrics,
        "fingerprints": fingerprints,
        "diagnostics": diagnostics,
    }
    if verify_fingerprints:
        expected = _fingerprints(normalized)
        if fingerprints != expected:
            for key in expected:
                if fingerprints.get(key) != expected[key]:
                    _fail(f"calibration_{key}_mismatch")
            _fail("calibration_fingerprint_mismatch")
    return normalized


def _normalize_input_fingerprints(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        _fail("calibration_input_fingerprints_invalid")
    keys = {
        "combined_source_fingerprint",
        "combined_typed_fingerprint",
        "quote_only_baseline_fingerprint",
        "trade_support_evidence_fingerprint",
    }
    _require_exact_keys(value, keys, "calibration_input_fingerprints")
    return {
        key: _required_sha256(value[key], f"calibration_{key}_invalid")
        for key in sorted(keys)
    }


def _normalize_construction(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("calibration_example_construction_invalid")
    _require_exact_keys(
        value,
        {"version", "trade_support_does_not_modify_score", "fill_claim"},
        "calibration_example_construction",
    )
    if value["version"] != CALIBRATION_EXAMPLE_VERSION:
        _fail("calibration_example_version_unsupported")
    if value["trade_support_does_not_modify_score"] is not True:
        _fail("calibration_trade_support_score_separation_required")
    if value["fill_claim"] is not False:
        _fail("calibration_fill_claim_forbidden")
    return {
        "version": CALIBRATION_EXAMPLE_VERSION,
        "trade_support_does_not_modify_score": True,
        "fill_claim": False,
    }


def _normalize_target_definition(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("calibration_target_definition_invalid")
    _require_exact_keys(
        value,
        {
            "name",
            "version",
            "target_horizon_seconds",
            "semantics",
            "future_selection",
            "target_unavailable_is_negative",
        },
        "calibration_target_definition",
    )
    if value["name"] != TARGET_DEFINITION_NAME or value["version"] != TARGET_DEFINITION_VERSION:
        _fail("calibration_target_definition_unsupported")
    horizon = _required_int(
        value["target_horizon_seconds"],
        "calibration_target_horizon_invalid",
        positive=True,
    )
    if value["semantics"] != "future reviewed quote movement; not execution or fill":
        _fail("calibration_target_semantics_invalid")
    if value["future_selection"] != "first complete quote strictly after observation within horizon":
        _fail("calibration_target_selection_invalid")
    if value["target_unavailable_is_negative"] is not False:
        _fail("calibration_target_unavailable_semantics_invalid")
    return {
        "name": TARGET_DEFINITION_NAME,
        "version": TARGET_DEFINITION_VERSION,
        "target_horizon_seconds": horizon,
        "semantics": value["semantics"],
        "future_selection": value["future_selection"],
        "target_unavailable_is_negative": False,
    }


def _normalize_examples(
    value: object, config: CalibrationEvaluationConfig
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _fail("calibration_examples_invalid")
    examples = [_normalize_example(item, config) for item in value]
    if examples != sorted(examples, key=_example_payload_sort_key):
        _fail("calibration_examples_not_sorted")
    keys = [item["example_key"] for item in examples]
    if len(keys) != len(set(keys)):
        _fail("calibration_example_key_duplicate")
    observation_keys = [item["observation_key"] for item in examples]
    if len(observation_keys) != len(set(observation_keys)):
        _fail("calibration_observation_key_duplicate")
    _validate_example_identity_consistency(examples)
    return examples


def _validate_example_identity_consistency(
    examples: Sequence[Mapping[str, Any]],
) -> None:
    item_to_external: dict[int, str] = {}
    external_to_item: dict[str, int] = {}
    item_to_sample: dict[int, str] = {}
    sample_to_item: dict[str, int] = {}
    for example in examples:
        item_id = example["item_id"]
        external_key = example["external_key"]
        offline_sample_key = example["source_refs"]["offline_sample_key"]
        existing_external = item_to_external.setdefault(item_id, external_key)
        if existing_external != external_key:
            _fail("calibration_item_external_key_mismatch")
        existing_item = external_to_item.setdefault(external_key, item_id)
        if existing_item != item_id:
            _fail("calibration_external_key_item_collision")
        existing_sample = item_to_sample.setdefault(item_id, offline_sample_key)
        if existing_sample != offline_sample_key:
            _fail("calibration_item_offline_sample_key_mismatch")
        sample_item = sample_to_item.setdefault(offline_sample_key, item_id)
        if sample_item != item_id:
            _fail("calibration_offline_sample_key_item_collision")


def _normalize_example(
    value: object, config: CalibrationEvaluationConfig
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("calibration_example_invalid")
    _require_exact_keys(
        value,
        {
            "example_key",
            "item_id",
            "external_key",
            "observation_key",
            "observed_at",
            "knowledge_cutoff",
            "quality_tier",
            "quote_only_features",
            "trade_support_features",
            "target",
            "source_refs",
        },
        "calibration_example",
    )
    item_id = _required_int(value["item_id"], "calibration_item_id_invalid", positive=True)
    external_key = _required_string(value["external_key"], "calibration_external_key_invalid")
    observation_key = _required_string(value["observation_key"], "calibration_observation_key_invalid")
    observed_at = _parse_datetime(value["observed_at"], "calibration_observed_at_invalid")
    knowledge_cutoff = _parse_datetime(value["knowledge_cutoff"], "calibration_knowledge_cutoff_invalid")
    if knowledge_cutoff != observed_at:
        _fail("calibration_knowledge_cutoff_mismatch")
    quality_tier = _required_string(value["quality_tier"], "calibration_quality_tier_invalid")
    if quality_tier not in config.included_quality_tiers:
        _fail("calibration_quality_tier_not_in_config")
    quote = _normalize_quote_features(value["quote_only_features"], observed_at, quality_tier)
    trade = _normalize_trade_features(value["trade_support_features"], knowledge_cutoff)
    target = _normalize_target(value["target"], observed_at, config.target_horizon_seconds, quote)
    refs = _normalize_source_refs(value["source_refs"], observation_key, trade)
    expected_key = _example_key(
        item_id=item_id,
        observation_key=observation_key,
        observed_at=observed_at,
    )
    if value["example_key"] != expected_key:
        _fail("calibration_example_key_mismatch")
    return {
        "example_key": expected_key,
        "item_id": item_id,
        "external_key": external_key,
        "observation_key": observation_key,
        "observed_at": _datetime_text(observed_at),
        "knowledge_cutoff": _datetime_text(knowledge_cutoff),
        "quality_tier": quality_tier,
        "quote_only_features": quote,
        "trade_support_features": trade,
        "target": target,
        "source_refs": refs,
    }


def _normalize_quote_features(
    value: object, observed_at: datetime, quality_tier: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("calibration_quote_features_invalid")
    keys = {
        "feature_available_at",
        "best_bid_price",
        "best_ask_price",
        "mid_price",
        "spread_absolute",
        "spread_ratio",
        "observed_bid_quantity",
        "observed_ask_quantity",
        "quantity_complete",
        "quantity_imbalance",
        "quality_tier",
        "source_type",
        "review_status",
    }
    _require_exact_keys(value, keys, "calibration_quote_features")
    available_at = _parse_datetime(value["feature_available_at"], "calibration_quote_available_at_invalid")
    if available_at != observed_at:
        _fail("calibration_quote_available_at_mismatch")
    bid = _optional_decimal(value["best_bid_price"], "calibration_quote_bid_invalid", minimum=Decimal("0"))
    ask = _optional_decimal(value["best_ask_price"], "calibration_quote_ask_invalid", positive=True)
    midpoint, spread, spread_ratio = _quote_derived_values(bid, ask)
    if _optional_decimal(value["mid_price"], "calibration_quote_mid_invalid", positive=True) != midpoint:
        _fail("calibration_quote_mid_mismatch")
    if _optional_decimal(value["spread_absolute"], "calibration_quote_spread_invalid") != spread:
        _fail("calibration_quote_spread_mismatch")
    if _optional_decimal(value["spread_ratio"], "calibration_quote_spread_ratio_invalid") != spread_ratio:
        _fail("calibration_quote_spread_ratio_mismatch")
    bid_quantity = _optional_nonnegative_int(value["observed_bid_quantity"], "calibration_quote_bid_quantity_invalid")
    ask_quantity = _optional_nonnegative_int(value["observed_ask_quantity"], "calibration_quote_ask_quantity_invalid")
    complete = bid_quantity is not None and ask_quantity is not None
    if value["quantity_complete"] is not complete:
        _fail("calibration_quote_quantity_complete_mismatch")
    imbalance = _quantity_imbalance(bid_quantity, ask_quantity)
    if _optional_decimal(value["quantity_imbalance"], "calibration_quote_quantity_imbalance_invalid") != imbalance:
        _fail("calibration_quote_quantity_imbalance_mismatch")
    if value["quality_tier"] != quality_tier:
        _fail("calibration_quote_quality_tier_mismatch")
    if value["source_type"] != "screen_review":
        _fail("calibration_quote_source_type_invalid")
    if value["review_status"] not in {"confirmed", "confirmed_with_edits"}:
        _fail("calibration_quote_review_status_invalid")
    return {
        "feature_available_at": _datetime_text(available_at),
        "best_bid_price": _decimal_text(bid),
        "best_ask_price": _decimal_text(ask),
        "mid_price": _decimal_text(midpoint),
        "spread_absolute": _decimal_text(spread),
        "spread_ratio": _decimal_text(spread_ratio),
        "observed_bid_quantity": bid_quantity,
        "observed_ask_quantity": ask_quantity,
        "quantity_complete": complete,
        "quantity_imbalance": _decimal_text(imbalance),
        "quality_tier": quality_tier,
        "source_type": "screen_review",
        "review_status": value["review_status"],
    }


def _normalize_trade_features(value: object, cutoff: datetime) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("calibration_trade_features_invalid")
    keys = {
        "feature_available_at",
        "available_bucket_count",
        "hourly_bucket_count",
        "daily_fallback_count",
        "transaction_activity_present",
        "reported_volume_sum_unknown_unit",
        "latest_reported_vwap_price",
        "latest_bucket_end_at",
        "reported_vwap_change_ratio",
        "price_semantics",
        "volume_semantics",
        "fill_claim",
        "available_bucket_keys",
    }
    _require_exact_keys(value, keys, "calibration_trade_features")
    available_at = _parse_datetime(value["feature_available_at"], "calibration_trade_available_at_invalid")
    if available_at != cutoff:
        _fail("calibration_trade_available_at_mismatch")
    bucket_keys = value["available_bucket_keys"]
    if not isinstance(bucket_keys, list) or any(not isinstance(item, str) or not item for item in bucket_keys):
        _fail("calibration_trade_bucket_keys_invalid")
    if bucket_keys != sorted(bucket_keys) or len(bucket_keys) != len(set(bucket_keys)):
        _fail("calibration_trade_bucket_keys_not_canonical")
    count = _required_int(value["available_bucket_count"], "calibration_trade_bucket_count_invalid", minimum=0)
    hourly = _required_int(value["hourly_bucket_count"], "calibration_trade_hourly_count_invalid", minimum=0)
    daily = _required_int(value["daily_fallback_count"], "calibration_trade_daily_count_invalid", minimum=0)
    derived_hourly, derived_daily, derived_latest_end = _trade_bucket_key_summary(
        bucket_keys, cutoff
    )
    if count != len(bucket_keys) or hourly + daily != count:
        _fail("calibration_trade_bucket_count_mismatch")
    if hourly != derived_hourly or daily != derived_daily:
        _fail("calibration_trade_bucket_granularity_count_mismatch")
    if value["transaction_activity_present"] is not (count > 0):
        _fail("calibration_trade_activity_flag_mismatch")
    volume = _optional_nonnegative_int(value["reported_volume_sum_unknown_unit"], "calibration_trade_volume_invalid")
    if count == 0 and volume is not None:
        _fail("calibration_trade_volume_without_buckets")
    if count > 0 and volume is None:
        _fail("calibration_trade_volume_missing")
    latest_vwap = _optional_decimal(value["latest_reported_vwap_price"], "calibration_trade_latest_vwap_invalid", positive=True)
    latest_end = (
        None
        if value["latest_bucket_end_at"] is None
        else _parse_datetime(value["latest_bucket_end_at"], "calibration_trade_latest_end_invalid")
    )
    if count == 0 and (latest_vwap is not None or latest_end is not None):
        _fail("calibration_trade_latest_without_buckets")
    if count > 0 and (latest_vwap is None or latest_end is None):
        _fail("calibration_trade_latest_missing")
    if latest_end is not None and latest_end > cutoff:
        _fail("calibration_trade_future_bucket")
    if latest_end != derived_latest_end:
        _fail("calibration_trade_latest_end_mismatch")
    change = _optional_decimal(value["reported_vwap_change_ratio"], "calibration_trade_vwap_change_invalid")
    if value["price_semantics"] != PRICE_SEMANTICS:
        _fail("calibration_trade_price_semantics_invalid")
    if value["volume_semantics"] != VOLUME_SEMANTICS:
        _fail("calibration_trade_volume_semantics_invalid")
    if value["fill_claim"] is not False:
        _fail("calibration_trade_fill_claim_forbidden")
    return {
        "feature_available_at": _datetime_text(available_at),
        "available_bucket_count": count,
        "hourly_bucket_count": hourly,
        "daily_fallback_count": daily,
        "transaction_activity_present": count > 0,
        "reported_volume_sum_unknown_unit": volume,
        "latest_reported_vwap_price": _decimal_text(latest_vwap),
        "latest_bucket_end_at": _datetime_text(latest_end) if latest_end else None,
        "reported_vwap_change_ratio": _decimal_text(change),
        "price_semantics": PRICE_SEMANTICS,
        "volume_semantics": VOLUME_SEMANTICS,
        "fill_claim": False,
        "available_bucket_keys": bucket_keys,
    }


def _normalize_target(
    value: object,
    observed_at: datetime,
    horizon_seconds: int,
    quote_features: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("calibration_target_invalid")
    keys = {
        "status",
        "unavailable_reason",
        "target_name",
        "target_version",
        "horizon_seconds",
        "label_start_at",
        "label_end_at",
        "future_observation_key",
        "future_mid_price",
        "mid_price_change",
        "mid_price_change_ratio",
        "direction",
        "persistence_prediction_mid_price",
        "persistence_absolute_error",
        "semantics",
    }
    _require_exact_keys(value, keys, "calibration_target")
    if value["target_name"] != TARGET_DEFINITION_NAME or value["target_version"] != TARGET_DEFINITION_VERSION:
        _fail("calibration_target_contract_invalid")
    if value["horizon_seconds"] != horizon_seconds:
        _fail("calibration_target_horizon_mismatch")
    start = _parse_datetime(value["label_start_at"], "calibration_target_start_invalid")
    if start != observed_at:
        _fail("calibration_target_start_mismatch")
    if value["semantics"] != "future reviewed quote movement; not execution or fill":
        _fail("calibration_target_semantics_invalid")
    status = value["status"]
    if status == EvaluationTargetStatus.UNAVAILABLE.value:
        reason = value["unavailable_reason"]
        if reason not in {item.value for item in EvaluationTargetUnavailableReason}:
            _fail("calibration_target_unavailable_reason_invalid")
        nullable = (
            "label_end_at",
            "future_observation_key",
            "future_mid_price",
            "mid_price_change",
            "mid_price_change_ratio",
            "direction",
            "persistence_prediction_mid_price",
            "persistence_absolute_error",
        )
        if any(value[name] is not None for name in nullable):
            _fail("calibration_target_unavailable_payload_invalid")
        return {
            "status": status,
            "unavailable_reason": reason,
            "target_name": TARGET_DEFINITION_NAME,
            "target_version": TARGET_DEFINITION_VERSION,
            "horizon_seconds": horizon_seconds,
            "label_start_at": _datetime_text(start),
            "label_end_at": None,
            "future_observation_key": None,
            "future_mid_price": None,
            "mid_price_change": None,
            "mid_price_change_ratio": None,
            "direction": None,
            "persistence_prediction_mid_price": None,
            "persistence_absolute_error": None,
            "semantics": value["semantics"],
        }
    if status != EvaluationTargetStatus.AVAILABLE.value:
        _fail("calibration_target_status_invalid")
    if value["unavailable_reason"] is not None:
        _fail("calibration_target_available_reason_present")
    end = _parse_datetime(value["label_end_at"], "calibration_target_end_invalid")
    if not observed_at < end <= observed_at + timedelta(seconds=horizon_seconds):
        _fail("calibration_target_time_window_invalid")
    future_key = _required_string(value["future_observation_key"], "calibration_target_future_key_invalid")
    future_mid = _required_decimal(value["future_mid_price"], "calibration_target_future_mid_invalid", positive=True)
    current_mid = _required_decimal(quote_features["mid_price"], "calibration_target_current_mid_invalid", positive=True)
    change = future_mid - current_mid
    ratio = change / current_mid
    error = abs(change)
    if _required_decimal(value["mid_price_change"], "calibration_target_change_invalid") != change:
        _fail("calibration_target_change_mismatch")
    if _required_decimal(value["mid_price_change_ratio"], "calibration_target_ratio_invalid") != ratio:
        _fail("calibration_target_ratio_mismatch")
    direction = "up" if change > 0 else "down" if change < 0 else "flat"
    if value["direction"] != direction:
        _fail("calibration_target_direction_mismatch")
    if _required_decimal(value["persistence_prediction_mid_price"], "calibration_target_prediction_invalid", positive=True) != current_mid:
        _fail("calibration_target_prediction_mismatch")
    if _required_decimal(value["persistence_absolute_error"], "calibration_target_error_invalid", minimum=Decimal("0")) != error:
        _fail("calibration_target_error_mismatch")
    return {
        "status": status,
        "unavailable_reason": None,
        "target_name": TARGET_DEFINITION_NAME,
        "target_version": TARGET_DEFINITION_VERSION,
        "horizon_seconds": horizon_seconds,
        "label_start_at": _datetime_text(start),
        "label_end_at": _datetime_text(end),
        "future_observation_key": future_key,
        "future_mid_price": _decimal_text(future_mid),
        "mid_price_change": _decimal_text(change),
        "mid_price_change_ratio": _decimal_text(ratio),
        "direction": direction,
        "persistence_prediction_mid_price": _decimal_text(current_mid),
        "persistence_absolute_error": _decimal_text(error),
        "semantics": value["semantics"],
    }


def _normalize_source_refs(
    value: object,
    observation_key: str,
    trade_features: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("calibration_source_refs_invalid")
    _require_exact_keys(
        value,
        {"offline_sample_key", "market_provenance_ref", "trade_bucket_keys"},
        "calibration_source_refs",
    )
    offline_key = _required_string(value["offline_sample_key"], "calibration_source_sample_key_invalid")
    if value["market_provenance_ref"] != observation_key:
        _fail("calibration_source_market_ref_mismatch")
    keys = value["trade_bucket_keys"]
    if keys != trade_features["available_bucket_keys"]:
        _fail("calibration_source_trade_refs_mismatch")
    return {
        "offline_sample_key": offline_key,
        "market_provenance_ref": observation_key,
        "trade_bucket_keys": list(keys),
    }


def _validate_targets_against_examples(
    examples: Sequence[Mapping[str, Any]], horizon_seconds: int
) -> None:
    by_item: dict[int, list[Mapping[str, Any]]] = {}
    for example in examples:
        by_item.setdefault(example["item_id"], []).append(example)
    for values in by_item.values():
        ordered = sorted(values, key=_example_payload_sort_key)
        for index, current in enumerate(ordered):
            expected = _future_target_from_example_payload(
                current=current,
                following=ordered[index + 1 :],
                horizon_seconds=horizon_seconds,
            )
            if current["target"] != expected:
                _fail("calibration_target_future_selection_mismatch")


def _future_target_from_example_payload(
    *,
    current: Mapping[str, Any],
    following: Sequence[Mapping[str, Any]],
    horizon_seconds: int,
) -> dict[str, Any]:
    observed_at = _parse_datetime(current["observed_at"], "calibration_observed_at_invalid")
    semantics = "future reviewed quote movement; not execution or fill"
    base = {
        "target_name": TARGET_DEFINITION_NAME,
        "target_version": TARGET_DEFINITION_VERSION,
        "horizon_seconds": horizon_seconds,
        "label_start_at": _datetime_text(observed_at),
        "semantics": semantics,
    }
    current_mid_text = current["quote_only_features"]["mid_price"]
    if current_mid_text is None:
        return _unavailable_target(
            base, EvaluationTargetUnavailableReason.CURRENT_QUOTE_INCOMPLETE
        )
    current_mid = _decimal_from_text(current_mid_text)
    horizon_end = observed_at + timedelta(seconds=horizon_seconds)
    future_in_window = [
        value
        for value in following
        if observed_at
        < _parse_datetime(value["observed_at"], "calibration_observed_at_invalid")
        <= horizon_end
    ]
    if not future_in_window:
        return _unavailable_target(
            base, EvaluationTargetUnavailableReason.NO_FUTURE_OBSERVATION
        )
    selected = next(
        (
            value
            for value in future_in_window
            if value["quote_only_features"]["mid_price"] is not None
        ),
        None,
    )
    if selected is None:
        return _unavailable_target(
            base, EvaluationTargetUnavailableReason.FUTURE_QUOTE_INCOMPLETE
        )
    future_mid = _decimal_from_text(selected["quote_only_features"]["mid_price"])
    change = future_mid - current_mid
    ratio = change / current_mid
    direction = "up" if change > 0 else "down" if change < 0 else "flat"
    return {
        "status": EvaluationTargetStatus.AVAILABLE.value,
        "unavailable_reason": None,
        **base,
        "label_end_at": selected["observed_at"],
        "future_observation_key": selected["observation_key"],
        "future_mid_price": _decimal_text(future_mid),
        "mid_price_change": _decimal_text(change),
        "mid_price_change_ratio": _decimal_text(ratio),
        "direction": direction,
        "persistence_prediction_mid_price": _decimal_text(current_mid),
        "persistence_absolute_error": _decimal_text(abs(change)),
    }


def _trade_bucket_key_summary(
    bucket_keys: Sequence[str], cutoff: datetime
) -> tuple[int, int, datetime | None]:
    hourly = 0
    daily = 0
    ends: list[datetime] = []
    for key in bucket_keys:
        if key.startswith("1h:"):
            duration = 3600
            hourly += 1
            timestamp = key[3:]
        elif key.startswith("1d:"):
            duration = 86400
            daily += 1
            timestamp = key[3:]
        else:
            _fail("calibration_trade_bucket_key_invalid")
        start = _parse_datetime(timestamp, "calibration_trade_bucket_key_invalid")
        if int(start.timestamp()) % duration != 0:
            _fail("calibration_trade_bucket_key_alignment_invalid")
        end = start + timedelta(seconds=duration)
        if end > cutoff:
            _fail("calibration_trade_future_bucket")
        ends.append(end)
    return hourly, daily, max(ends, default=None)


def _normalize_folds(
    value: object,
    examples: Sequence[Mapping[str, Any]],
    config: CalibrationEvaluationConfig,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _fail("calibration_folds_invalid")
    expected = _build_walk_forward_folds(examples, config)
    if value != expected:
        _fail("calibration_walk_forward_plan_mismatch")
    return expected


def _normalize_metrics(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("calibration_metrics_invalid")
    # Canonical JSON round-trip rejects unsupported values while preserving exact data.
    try:
        normalized = json.loads(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    except (TypeError, ValueError):
        _fail("calibration_metrics_invalid")
    if normalized.get("schema_version") != METRIC_SCHEMA_VERSION:
        _fail("calibration_metrics_schema_unsupported")
    return normalized


def _normalize_fingerprints(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        _fail("calibration_fingerprints_invalid")
    keys = {
        "quote_only_features_fingerprint",
        "trade_support_features_fingerprint",
        "calibration_example_source_fingerprint",
        "calibration_example_typed_fingerprint",
        "walk_forward_plan_fingerprint",
        "evaluation_report_fingerprint",
    }
    _require_exact_keys(value, keys, "calibration_fingerprints")
    return {
        key: _required_sha256(value[key], f"calibration_{key}_invalid")
        for key in sorted(keys)
    }


def _normalize_diagnostics(
    value: object,
    examples: Sequence[Mapping[str, Any]],
    folds: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("calibration_diagnostics_invalid")
    keys = {
        "source_item_count",
        "source_observation_count",
        "included_example_count",
        "excluded_quality_tier_count",
        "target_available_count",
        "target_unavailable_count",
        "target_unavailable_reason_counts",
        "fold_count",
    }
    _require_exact_keys(value, keys, "calibration_diagnostics")
    counts = {
        key: _required_int(value[key], f"calibration_diagnostics_{key}_invalid", minimum=0)
        for key in keys
        if key != "target_unavailable_reason_counts"
    }
    if counts["included_example_count"] != len(examples):
        _fail("calibration_diagnostics_example_count_mismatch")
    if counts["fold_count"] != len(folds):
        _fail("calibration_diagnostics_fold_count_mismatch")
    available = sum(item["target"]["status"] == "available" for item in examples)
    unavailable = len(examples) - available
    if counts["target_available_count"] != available or counts["target_unavailable_count"] != unavailable:
        _fail("calibration_diagnostics_target_count_mismatch")
    reasons_value = value["target_unavailable_reason_counts"]
    if not isinstance(reasons_value, Mapping):
        _fail("calibration_diagnostics_reason_counts_invalid")
    reasons: dict[str, int] = {}
    for key, item in reasons_value.items():
        if key not in {reason.value for reason in EvaluationTargetUnavailableReason}:
            _fail("calibration_diagnostics_reason_invalid")
        reasons[key] = _required_int(item, "calibration_diagnostics_reason_count_invalid", minimum=0)
    actual: dict[str, int] = {}
    for example in examples:
        reason = example["target"]["unavailable_reason"]
        if reason is not None:
            actual[reason] = actual.get(reason, 0) + 1
    if reasons != {key: actual[key] for key in sorted(actual)}:
        _fail("calibration_diagnostics_reason_count_mismatch")
    unique_items = len({example["item_id"] for example in examples})
    if counts["source_observation_count"] != (
        counts["included_example_count"] + counts["excluded_quality_tier_count"]
    ):
        _fail("calibration_diagnostics_source_observation_count_mismatch")
    minimum_source_items = unique_items
    if counts["source_observation_count"] > 0 and minimum_source_items == 0:
        minimum_source_items = 1
    maximum_source_items = unique_items + counts["excluded_quality_tier_count"]
    if not (
        minimum_source_items
        <= counts["source_item_count"]
        <= maximum_source_items
    ):
        _fail("calibration_diagnostics_source_item_count_invalid")
    return {
        "source_item_count": counts["source_item_count"],
        "source_observation_count": counts["source_observation_count"],
        "included_example_count": counts["included_example_count"],
        "excluded_quality_tier_count": counts["excluded_quality_tier_count"],
        "target_available_count": available,
        "target_unavailable_count": unavailable,
        "target_unavailable_reason_counts": reasons,
        "fold_count": len(folds),
    }


def _unique_histories(
    histories: Sequence[ItemMarketHistory],
) -> dict[int, ItemMarketHistory]:
    result = {value.item_id: value for value in histories}
    if len(result) != len(histories):
        _fail("calibration_market_item_duplicate")
    return result


def _unique_trade_histories(
    histories: Sequence[ItemHistoricalTradeHistory],
) -> dict[int, ItemHistoricalTradeHistory]:
    result = {value.item_id: value for value in histories}
    if len(result) != len(histories):
        _fail("calibration_trade_item_duplicate")
    return result


def _selected_buckets(
    *,
    availability: ObservationTradeAvailability,
    bucket_by_key: Mapping[str, HistoricalTradeBucket],
) -> tuple[HistoricalTradeBucket, ...]:
    selected: list[HistoricalTradeBucket] = []
    for key in availability.available_bucket_keys:
        bucket = bucket_by_key.get(key)
        if bucket is None:
            _fail("calibration_trade_availability_bucket_missing")
        if bucket.bucket_end_utc > availability.knowledge_cutoff:
            _fail("calibration_trade_availability_future_bucket")
        selected.append(bucket)
    selected.sort(key=lambda value: (value.bucket_end_utc, value.granularity.value))
    hourly_count = sum(
        value.granularity is HistoricalTradeGranularity.HOUR_1 for value in selected
    )
    if hourly_count != availability.selected_hourly_bucket_count:
        _fail("calibration_trade_hourly_count_mismatch")
    if len(selected) - hourly_count != availability.selected_daily_fallback_count:
        _fail("calibration_trade_daily_count_mismatch")
    return tuple(selected)


def _quote_features(
    observation: MarketObservation,
    quality_tier: str,
) -> dict[str, Any]:
    midpoint, spread, spread_ratio = _quote_derived_values(
        observation.best_bid,
        observation.best_ask,
    )
    imbalance = _quantity_imbalance(
        observation.observed_bid_quantity,
        observation.observed_ask_quantity,
    )
    return {
        "feature_available_at": _datetime_text(observation.observed_at),
        "best_bid_price": _decimal_text(observation.best_bid),
        "best_ask_price": _decimal_text(observation.best_ask),
        "mid_price": _decimal_text(midpoint),
        "spread_absolute": _decimal_text(spread),
        "spread_ratio": _decimal_text(spread_ratio),
        "observed_bid_quantity": observation.observed_bid_quantity,
        "observed_ask_quantity": observation.observed_ask_quantity,
        "quantity_complete": (
            observation.observed_bid_quantity is not None
            and observation.observed_ask_quantity is not None
        ),
        "quantity_imbalance": _decimal_text(imbalance),
        "quality_tier": quality_tier,
        "source_type": observation.source_type,
        "review_status": observation.review_status,
    }


def _trade_features(
    buckets: Sequence[HistoricalTradeBucket],
    availability: ObservationTradeAvailability,
) -> dict[str, Any]:
    hourly = sum(
        bucket.granularity is HistoricalTradeGranularity.HOUR_1
        for bucket in buckets
    )
    latest = buckets[-1] if buckets else None
    first = buckets[0] if buckets else None
    change = None
    if first is not None and latest is not None and len(buckets) >= 2:
        change = (
            latest.reported_vwap_price - first.reported_vwap_price
        ) / first.reported_vwap_price
    return {
        "feature_available_at": _datetime_text(availability.knowledge_cutoff),
        "available_bucket_count": len(buckets),
        "hourly_bucket_count": hourly,
        "daily_fallback_count": len(buckets) - hourly,
        "transaction_activity_present": bool(buckets),
        "reported_volume_sum_unknown_unit": (
            sum(bucket.reported_trade_volume for bucket in buckets)
            if buckets
            else None
        ),
        "latest_reported_vwap_price": (
            _decimal_text(latest.reported_vwap_price) if latest else None
        ),
        "latest_bucket_end_at": (
            _datetime_text(latest.bucket_end_utc) if latest else None
        ),
        "reported_vwap_change_ratio": _decimal_text(change),
        "price_semantics": PRICE_SEMANTICS,
        "volume_semantics": VOLUME_SEMANTICS,
        "fill_claim": False,
        "available_bucket_keys": list(availability.available_bucket_keys),
    }


def _future_target(
    *,
    current: MarketObservation,
    following: Sequence[MarketObservation],
    horizon_seconds: int,
) -> dict[str, Any]:
    semantics = "future reviewed quote movement; not execution or fill"
    current_mid, _, _ = _quote_derived_values(current.best_bid, current.best_ask)
    base = {
        "target_name": TARGET_DEFINITION_NAME,
        "target_version": TARGET_DEFINITION_VERSION,
        "horizon_seconds": horizon_seconds,
        "label_start_at": _datetime_text(current.observed_at),
        "semantics": semantics,
    }
    if current_mid is None:
        return _unavailable_target(
            base,
            EvaluationTargetUnavailableReason.CURRENT_QUOTE_INCOMPLETE,
        )
    horizon_end = current.observed_at + timedelta(seconds=horizon_seconds)
    future_in_window = [
        value
        for value in following
        if current.observed_at < value.observed_at <= horizon_end
    ]
    if not future_in_window:
        return _unavailable_target(
            base,
            EvaluationTargetUnavailableReason.NO_FUTURE_OBSERVATION,
        )
    selected = next(
        (
            value
            for value in future_in_window
            if _quote_derived_values(value.best_bid, value.best_ask)[0] is not None
        ),
        None,
    )
    if selected is None:
        return _unavailable_target(
            base,
            EvaluationTargetUnavailableReason.FUTURE_QUOTE_INCOMPLETE,
        )
    future_mid, _, _ = _quote_derived_values(selected.best_bid, selected.best_ask)
    assert future_mid is not None
    change = future_mid - current_mid
    ratio = change / current_mid
    direction = "up" if change > 0 else "down" if change < 0 else "flat"
    return {
        "status": EvaluationTargetStatus.AVAILABLE.value,
        "unavailable_reason": None,
        **base,
        "label_end_at": _datetime_text(selected.observed_at),
        "future_observation_key": selected.observation_key,
        "future_mid_price": _decimal_text(future_mid),
        "mid_price_change": _decimal_text(change),
        "mid_price_change_ratio": _decimal_text(ratio),
        "direction": direction,
        "persistence_prediction_mid_price": _decimal_text(current_mid),
        "persistence_absolute_error": _decimal_text(abs(change)),
    }


def _unavailable_target(
    base: Mapping[str, Any], reason: EvaluationTargetUnavailableReason
) -> dict[str, Any]:
    return {
        "status": EvaluationTargetStatus.UNAVAILABLE.value,
        "unavailable_reason": reason.value,
        **base,
        "label_end_at": None,
        "future_observation_key": None,
        "future_mid_price": None,
        "mid_price_change": None,
        "mid_price_change_ratio": None,
        "direction": None,
        "persistence_prediction_mid_price": None,
        "persistence_absolute_error": None,
    }


def _build_walk_forward_folds(
    examples: Sequence[Mapping[str, Any]],
    config: CalibrationEvaluationConfig,
) -> list[dict[str, Any]]:
    times = sorted(
        {
            _parse_datetime(
                value["observed_at"], "calibration_observed_at_invalid"
            )
            for value in examples
        }
    )
    starts = list(
        range(
            config.minimum_train_timestamps,
            len(times),
            config.step_timestamp_count,
        )
    )
    folds: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        test_times = times[start : start + config.test_timestamp_count]
        if not test_times:
            continue
        test_start = test_times[0]
        test_end = test_times[-1]
        next_test_start = (
            times[starts[index + 1]] if index + 1 < len(starts) else None
        )
        embargo_cutoff = test_start - timedelta(seconds=config.embargo_seconds)
        train_keys: list[str] = []
        purged_train = 0
        target_unavailable_train = 0
        for example in examples:
            observed = _parse_datetime(
                example["observed_at"], "calibration_observed_at_invalid"
            )
            if observed >= test_start:
                continue
            target = example["target"]
            if target["status"] != EvaluationTargetStatus.AVAILABLE.value:
                target_unavailable_train += 1
                continue
            label_end = _parse_datetime(
                target["label_end_at"], "calibration_target_end_invalid"
            )
            if label_end >= embargo_cutoff:
                purged_train += 1
                continue
            train_keys.append(example["example_key"])
        test_time_set = set(test_times)
        test_examples = [
            example
            for example in examples
            if _parse_datetime(
                example["observed_at"], "calibration_observed_at_invalid"
            )
            in test_time_set
        ]
        test_keys = sorted(example["example_key"] for example in test_examples)
        evaluation_keys: list[str] = []
        purged_test_overlap = 0
        target_unavailable_test = 0
        for example in test_examples:
            target = example["target"]
            if target["status"] != EvaluationTargetStatus.AVAILABLE.value:
                target_unavailable_test += 1
                continue
            label_end = _parse_datetime(
                target["label_end_at"], "calibration_target_end_invalid"
            )
            if next_test_start is not None and label_end >= next_test_start:
                purged_test_overlap += 1
                continue
            evaluation_keys.append(example["example_key"])
        train_start = min(
            (
                _parse_datetime(
                    example["observed_at"], "calibration_observed_at_invalid"
                )
                for example in examples
                if example["example_key"] in set(train_keys)
            ),
            default=None,
        )
        folds.append(
            {
                "fold_id": f"fold:{index + 1:03d}",
                "mode": "expanding",
                "train_start_at": (
                    _datetime_text(train_start) if train_start is not None else None
                ),
                "train_end_before": _datetime_text(embargo_cutoff),
                "test_start_at": _datetime_text(test_start),
                "test_end_at": _datetime_text(test_end),
                "next_test_start_at": (
                    _datetime_text(next_test_start)
                    if next_test_start is not None
                    else None
                ),
                "embargo_seconds": config.embargo_seconds,
                "train_example_keys": sorted(train_keys),
                "test_example_keys": test_keys,
                "test_evaluation_example_keys": sorted(evaluation_keys),
                "purged_train_example_count": purged_train,
                "target_unavailable_train_example_count": (
                    target_unavailable_train
                ),
                "purged_test_label_overlap_count": purged_test_overlap,
                "target_unavailable_test_example_count": target_unavailable_test,
            }
        )
    return folds

def _build_metrics(
    examples: Sequence[Mapping[str, Any]],
    folds: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_key = {value["example_key"]: value for value in examples}
    fold_metrics = []
    for fold in folds:
        selected = [
            by_key[key] for key in fold["test_evaluation_example_keys"]
        ]
        fold_metrics.append(
            {
                "fold_id": fold["fold_id"],
                "test_feature_example_count": len(fold["test_example_keys"]),
                "test_evaluation_example_count": len(selected),
                "purged_test_label_overlap_count": fold[
                    "purged_test_label_overlap_count"
                ],
                "target_unavailable_test_example_count": fold[
                    "target_unavailable_test_example_count"
                ],
                **_metric_block(selected),
            }
        )
    return {
        "schema_version": METRIC_SCHEMA_VERSION,
        "overall": _metric_block(examples),
        "folds": fold_metrics,
    }


def _metric_block(examples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    available = [
        value
        for value in examples
        if value["target"]["status"] == EvaluationTargetStatus.AVAILABLE.value
    ]
    changes = tuple(_decimal_from_text(value["target"]["mid_price_change"]) for value in available)
    ratios = tuple(_decimal_from_text(value["target"]["mid_price_change_ratio"]) for value in available)
    errors = tuple(_decimal_from_text(value["target"]["persistence_absolute_error"]) for value in available)
    spread_pairs = tuple(
        (
            _decimal_from_text(value["quote_only_features"]["spread_ratio"]),
            abs(_decimal_from_text(value["target"]["mid_price_change_ratio"])),
        )
        for value in available
        if value["quote_only_features"]["spread_ratio"] is not None
    )
    imbalance_pairs = tuple(
        (
            _decimal_from_text(value["quote_only_features"]["quantity_imbalance"]),
            _decimal_from_text(value["target"]["mid_price_change_ratio"]),
        )
        for value in available
        if value["quote_only_features"]["quantity_imbalance"] is not None
    )
    supported = [value for value in available if value["trade_support_features"]["available_bucket_count"] > 0]
    unsupported = [value for value in available if value["trade_support_features"]["available_bucket_count"] == 0]
    return {
        "example_count": len(examples),
        "target_available_count": len(available),
        "target_unavailable_count": len(examples) - len(available),
        "target_availability_rate": _decimal_text(_rate(len(available), len(examples))),
        "persistence_mae_price": _decimal_text(_mean(errors)),
        "persistence_rmse_price": _decimal_text(_rmse(errors)),
        "mean_signed_mid_change": _decimal_text(_mean(changes)),
        "mean_signed_mid_change_ratio": _decimal_text(_mean(ratios)),
        "mean_absolute_mid_change_ratio": _decimal_text(_mean(tuple(abs(value) for value in ratios))),
        "direction_counts": {
            direction: sum(value["target"]["direction"] == direction for value in available)
            for direction in ("up", "down", "flat")
        },
        "spread_ratio_to_absolute_change_spearman": _decimal_text(_spearman(spread_pairs)),
        "quantity_imbalance_to_signed_change_spearman": _decimal_text(_spearman(imbalance_pairs)),
        "trade_support_comparison": {
            "supported_target_count": len(supported),
            "unsupported_target_count": len(unsupported),
            "supported_mean_absolute_mid_change_ratio": _decimal_text(
                _mean(tuple(abs(_decimal_from_text(value["target"]["mid_price_change_ratio"])) for value in supported))
            ),
            "unsupported_mean_absolute_mid_change_ratio": _decimal_text(
                _mean(tuple(abs(_decimal_from_text(value["target"]["mid_price_change_ratio"])) for value in unsupported))
            ),
            "supported_mean_signed_mid_change_ratio": _decimal_text(
                _mean(tuple(_decimal_from_text(value["target"]["mid_price_change_ratio"]) for value in supported))
            ),
            "unsupported_mean_signed_mid_change_ratio": _decimal_text(
                _mean(tuple(_decimal_from_text(value["target"]["mid_price_change_ratio"]) for value in unsupported))
            ),
            "semantics": "descriptive comparison only; not execution or fill",
        },
    }


def _quote_derived_values(
    bid: Decimal | None,
    ask: Decimal | None,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if bid is None or ask is None:
        return None, None, None
    midpoint = (bid + ask) / Decimal("2")
    if midpoint <= 0:
        return None, None, None
    spread = ask - bid
    return midpoint, spread, spread / midpoint


def _quantity_imbalance(
    bid_quantity: int | None,
    ask_quantity: int | None,
) -> Decimal | None:
    if bid_quantity is None or ask_quantity is None:
        return None
    total = bid_quantity + ask_quantity
    if total == 0:
        return None
    return Decimal(bid_quantity - ask_quantity) / Decimal(total)


def _example_key(*, item_id: int, observation_key: str, observed_at: datetime) -> str:
    return "example:" + _sha256_json(
        {
            "version": CALIBRATION_EXAMPLE_VERSION,
            "item_id": item_id,
            "observation_key": observation_key,
            "observed_at": _datetime_text(observed_at),
            "target": TARGET_DEFINITION_NAME,
        }
    )


def _example_payload_sort_key(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value["observed_at"],
        value["item_id"],
        value["example_key"],
    )


def _bucket_key(bucket: HistoricalTradeBucket) -> str:
    return f"{bucket.granularity.value}:{_datetime_text(bucket.bucket_start_utc)}"


def _rate(count: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return Decimal(count) / Decimal(denominator)


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _rmse(errors: Sequence[Decimal]) -> Decimal | None:
    if not errors:
        return None
    mean_square = sum((value * value for value in errors), Decimal("0")) / Decimal(len(errors))
    with localcontext() as context:
        context.prec = 50
        return mean_square.sqrt()


def _spearman(pairs: Sequence[tuple[Decimal, Decimal]]) -> Decimal | None:
    if len(pairs) < 3:
        return None
    xs = tuple(value[0] for value in pairs)
    ys = tuple(value[1] for value in pairs)
    x_ranks = _average_ranks(xs)
    y_ranks = _average_ranks(ys)
    x_mean = sum(x_ranks, Decimal("0")) / Decimal(len(x_ranks))
    y_mean = sum(y_ranks, Decimal("0")) / Decimal(len(y_ranks))
    covariance = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_ranks, y_ranks))
    x_variance = sum((x - x_mean) ** 2 for x in x_ranks)
    y_variance = sum((y - y_mean) ** 2 for y in y_ranks)
    if x_variance == 0 or y_variance == 0:
        return None
    with localcontext() as context:
        context.prec = 50
        return covariance / (x_variance * y_variance).sqrt()


def _average_ranks(values: Sequence[Decimal]) -> tuple[Decimal, ...]:
    ordered = sorted(enumerate(values), key=lambda pair: (pair[1], pair[0]))
    ranks = [Decimal("0")] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (Decimal(index + 1) + Decimal(end)) / Decimal("2")
        for original, _ in ordered[index:end]:
            ranks[original] = rank
        index = end
    return tuple(ranks)


def _canonical_json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return _datetime_text(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_json_value(item) for item in value]
    _fail("calibration_canonical_value_invalid")


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        _canonical_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decimal_from_text(value: object) -> Decimal:
    if not isinstance(value, str):
        _fail("calibration_decimal_invalid")
    try:
        result = Decimal(value)
    except InvalidOperation:
        _fail("calibration_decimal_invalid")
    if not result.is_finite():
        _fail("calibration_decimal_invalid")
    return result


def _required_decimal(
    value: object,
    code: str,
    *,
    positive: bool = False,
    minimum: Decimal | None = None,
) -> Decimal:
    result = _decimal_from_text(value)
    if positive and result <= 0:
        _fail(code)
    if minimum is not None and result < minimum:
        _fail(code)
    return result


def _optional_decimal(
    value: object,
    code: str,
    *,
    positive: bool = False,
    minimum: Decimal | None = None,
) -> Decimal | None:
    if value is None:
        return None
    return _required_decimal(value, code, positive=positive, minimum=minimum)


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _parse_datetime(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    return parsed.astimezone(UTC)


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("calibration_datetime_naive")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _required_string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(code)
    return value.strip()


def _required_int(
    value: object,
    code: str,
    *,
    positive: bool = False,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(code)
    if positive and value <= 0:
        _fail(code)
    if minimum is not None and value < minimum:
        _fail(code)
    return value


def _optional_nonnegative_int(value: object, code: str) -> int | None:
    if value is None:
        return None
    return _required_int(value, code, minimum=0)


def _required_sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        _fail(code)
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], context: str
) -> None:
    if set(value) != expected:
        _fail(f"{context}_keys_invalid")


def _fail(code: str, message: str | None = None) -> None:
    raise CalibrationEvaluationValidationError(code, message)
