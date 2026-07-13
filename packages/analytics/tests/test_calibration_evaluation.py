from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from gaijin_market_analytics.backtesting import (
    HistoricalTradeBucket,
    HistoricalTradeGranularity,
    ItemHistoricalTradeHistory,
    ItemMarketHistory,
)
from gaijin_market_analytics.calibration.evaluation import (
    CALIBRATION_EVALUATION_SCHEMA_VERSION,
    CalibrationEvaluationConfig,
    CalibrationEvaluationValidationError,
    build_calibration_evaluation_dataset,
    calibration_evaluation_json_bytes,
    calibration_example_source_fingerprint,
    calibration_example_typed_fingerprint,
    evaluation_report_fingerprint,
    parse_calibration_evaluation_config,
    parse_calibration_evaluation_dataset,
    quote_only_features_fingerprint,
    trade_support_features_fingerprint,
    walk_forward_plan_fingerprint,
)
from gaijin_market_analytics.contracts import MarketObservation
from gaijin_market_analytics.datasets.combined_calibration import (
    LoadedCombinedCalibrationDataset,
    ObservationTradeAvailability,
)
from gaijin_market_analytics.datasets.market_history_export import (
    ItemMappingEntry,
    ItemMappingManifest,
)


BASE = datetime(2026, 1, 1, 12, tzinfo=UTC)


def obs(
    item: int,
    index: int,
    *,
    offset_hours: int = 0,
    bid: str | None = None,
    ask: str | None = None,
    quantities: bool = True,
) -> MarketObservation:
    at = BASE + timedelta(days=index, hours=offset_hours)
    if bid is None:
        bid = str(10 + item % 100 + index)
    if ask is None:
        ask = str(11 + item % 100 + index)
    return MarketObservation(
        observed_at=at,
        best_bid=Decimal(bid) if bid is not None else None,
        best_ask=Decimal(ask) if ask is not None else None,
        bid_count=None,
        ask_count=None,
        estimated_volume=None,
        observation_key=f"review:{item}:{index}",
        observed_bid_quantity=(4 + index if quantities else None),
        observed_ask_quantity=(5 + index if quantities else None),
        quantity_semantics="screenshot_display_quantity",
        source_type="screen_review",
        review_status="confirmed" if index != 1 else "confirmed_with_edits",
    )


def trade_bucket(
    item_id: int,
    start: datetime,
    *,
    granularity: HistoricalTradeGranularity = HistoricalTradeGranularity.HOUR_1,
    price: str = "12.5",
    volume: int = 3,
) -> HistoricalTradeBucket:
    return HistoricalTradeBucket(
        item_id=item_id,
        granularity=granularity,
        bucket_start_utc=start,
        bucket_duration_seconds=granularity.duration_seconds,
        reported_vwap_price=Decimal(price),
        reported_trade_volume=volume,
    )


def combined_dataset(*, trade_multiplier: int = 1, reverse: bool = False) -> LoadedCombinedCalibrationDataset:
    item1_obs = tuple(obs(101, index) for index in range(4))
    item2_obs = tuple(obs(102, index, offset_hours=6, quantities=index != 2) for index in range(4))
    histories = [
        ItemMarketHistory(item_id=101, observations=item1_obs),
        ItemMarketHistory(item_id=102, observations=item2_obs),
    ]
    buckets1 = (
        trade_bucket(101, datetime(2026, 1, 1, 10, tzinfo=UTC), volume=2 * trade_multiplier),
        trade_bucket(101, datetime(2026, 1, 2, 10, tzinfo=UTC), price="13.0", volume=3 * trade_multiplier),
        trade_bucket(101, datetime(2026, 1, 3, 10, tzinfo=UTC), price="14.0", volume=4 * trade_multiplier),
    )
    buckets2 = (
        trade_bucket(
            102,
            datetime(2026, 1, 1, tzinfo=UTC),
            granularity=HistoricalTradeGranularity.DAY_1,
            price="22.0",
            volume=5 * trade_multiplier,
        ),
        trade_bucket(102, datetime(2026, 1, 3, 14, tzinfo=UTC), price="24.0", volume=2 * trade_multiplier),
    )
    trade_histories = [
        ItemHistoricalTradeHistory(item_id=101, buckets=buckets1),
        ItemHistoricalTradeHistory(item_id=102, buckets=buckets2),
    ]
    provenance = {}
    availability = {}
    for history, buckets in zip(histories, (buckets1, buckets2)):
        for observation in history.observations:
            key = observation.observation_key
            assert key is not None
            provenance[key] = {
                "quality_tier": (
                    "browser_captured_user_time_edited"
                    if observation.review_status == "confirmed_with_edits"
                    else "browser_captured"
                )
            }
            selected = tuple(bucket for bucket in buckets if bucket.bucket_end_utc <= observation.observed_at)
            hourly = sum(bucket.granularity is HistoricalTradeGranularity.HOUR_1 for bucket in selected)
            availability[key] = ObservationTradeAvailability(
                observation_key=key,
                knowledge_cutoff=observation.observed_at,
                available_bucket_keys=tuple(
                    sorted(
                        f"{bucket.granularity.value}:{bucket.bucket_start_utc.isoformat().replace('+00:00', 'Z')}"
                        for bucket in selected
                    )
                ),
                selected_hourly_bucket_count=hourly,
                selected_daily_fallback_count=len(selected) - hourly,
            )
    mappings = (
        ItemMappingEntry("sample-one", 101, "item-one", True),
        ItemMappingEntry("sample-two", 102, "item-two", True),
    )
    if reverse:
        histories.reverse()
        trade_histories.reverse()
        mappings = tuple(reversed(mappings))
    return LoadedCombinedCalibrationDataset(
        market_histories=tuple(histories),
        historical_trade_histories=tuple(trade_histories),
        market_provenance_by_key=provenance,
        historical_provenance_by_sample_key={},
        availability_by_observation_key=availability,
        mapping_manifest=ItemMappingManifest(
            schema_version="round6_item_mapping_v1",
            mappings=mappings,
        ),
        quote_only_baseline_fingerprint="1" * 64,
        trade_support_evidence_fingerprint=("2" if trade_multiplier == 1 else "3") * 64,
        combined_source_fingerprint=("4" if trade_multiplier == 1 else "5") * 64,
        combined_typed_fingerprint=("6" if trade_multiplier == 1 else "7") * 64,
        diagnostics={},
    )


def config() -> CalibrationEvaluationConfig:
    return CalibrationEvaluationConfig(
        target_horizon_seconds=36 * 3600,
        minimum_train_timestamps=2,
        test_timestamp_count=2,
        step_timestamp_count=3,
        embargo_seconds=0,
    )


def build(*, trade_multiplier: int = 1, reverse: bool = False) -> dict:
    return build_calibration_evaluation_dataset(
        combined_dataset=combined_dataset(
            trade_multiplier=trade_multiplier,
            reverse=reverse,
        ),
        config=config(),
    )


def refresh_fingerprints(payload: dict) -> None:
    payload["fingerprints"] = {
        "quote_only_features_fingerprint": quote_only_features_fingerprint(payload),
        "trade_support_features_fingerprint": trade_support_features_fingerprint(payload),
        "calibration_example_source_fingerprint": calibration_example_source_fingerprint(payload),
        "calibration_example_typed_fingerprint": calibration_example_typed_fingerprint(payload),
        "walk_forward_plan_fingerprint": walk_forward_plan_fingerprint(payload),
    }
    payload["fingerprints"]["evaluation_report_fingerprint"] = evaluation_report_fingerprint(payload)


def test_config_parser_normalizes_quality_tiers() -> None:
    parsed = parse_calibration_evaluation_config(
        {
            "schema_version": "round6_walk_forward_config_v1",
            "target_horizon_seconds": 3600,
            "minimum_train_timestamps": 2,
            "test_timestamp_count": 1,
            "step_timestamp_count": 1,
            "embargo_seconds": 0,
            "included_quality_tiers": [
                "browser_captured_user_time_edited",
                "browser_captured",
                "browser_captured",
            ],
        }
    )
    assert parsed.included_quality_tiers == (
        "browser_captured",
        "browser_captured_user_time_edited",
    )


def test_config_rejects_overlapping_test_windows() -> None:
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        CalibrationEvaluationConfig(
            target_horizon_seconds=3600,
            minimum_train_timestamps=2,
            test_timestamp_count=2,
            step_timestamp_count=1,
        )
    assert error.value.code == "calibration_config_overlapping_test_windows"


def test_build_round_trip_and_counts() -> None:
    payload = build()
    loaded = parse_calibration_evaluation_dataset(payload)
    assert payload["schema_version"] == CALIBRATION_EVALUATION_SCHEMA_VERSION
    assert len(loaded.examples) == 8
    assert len(loaded.folds) == 2
    assert payload["diagnostics"]["target_available_count"] == 6
    assert payload["diagnostics"]["target_unavailable_count"] == 2
    assert loaded.metrics["overall"]["example_count"] == 8
    assert all(example["target"]["semantics"].endswith("not execution or fill") for example in loaded.examples)


def test_target_selects_first_complete_future_quote() -> None:
    payload = build()
    first = next(value for value in payload["examples"] if value["observation_key"] == "review:101:0")
    assert first["target"]["status"] == "available"
    assert first["target"]["future_observation_key"] == "review:101:1"
    assert first["target"]["label_end_at"] == "2026-01-02T12:00:00Z"
    last = next(value for value in payload["examples"] if value["observation_key"] == "review:101:3")
    assert last["target"]["status"] == "unavailable"
    assert last["target"]["unavailable_reason"] == "no_future_observation"
    assert last["target"]["mid_price_change"] is None


def test_no_lookahead_trade_features() -> None:
    payload = build()
    first = next(value for value in payload["examples"] if value["observation_key"] == "review:101:0")
    assert first["trade_support_features"]["available_bucket_keys"] == [
        "1h:2026-01-01T10:00:00Z"
    ]
    for example in payload["examples"]:
        cutoff = datetime.fromisoformat(example["knowledge_cutoff"].replace("Z", "+00:00"))
        latest = example["trade_support_features"]["latest_bucket_end_at"]
        if latest is not None:
            assert datetime.fromisoformat(latest.replace("Z", "+00:00")) <= cutoff


def test_missing_trade_support_is_not_zero_volume() -> None:
    payload = build()
    first_item2 = next(value for value in payload["examples"] if value["observation_key"] == "review:102:0")
    trade = first_item2["trade_support_features"]
    assert trade["available_bucket_count"] == 0
    assert trade["reported_volume_sum_unknown_unit"] is None
    assert trade["transaction_activity_present"] is False
    assert trade["fill_claim"] is False


def test_quote_only_fingerprint_is_invariant_to_trade_changes() -> None:
    first = build(trade_multiplier=1)
    second = build(trade_multiplier=4)
    assert first["fingerprints"]["quote_only_features_fingerprint"] == second["fingerprints"]["quote_only_features_fingerprint"]
    assert first["fingerprints"]["trade_support_features_fingerprint"] != second["fingerprints"]["trade_support_features_fingerprint"]
    assert first["fingerprints"]["evaluation_report_fingerprint"] != second["fingerprints"]["evaluation_report_fingerprint"]
    first_quote = [(value["quote_only_features"], value["target"]) for value in first["examples"]]
    second_quote = [(value["quote_only_features"], value["target"]) for value in second["examples"]]
    assert first_quote == second_quote


def test_input_order_does_not_change_output() -> None:
    assert calibration_evaluation_json_bytes(build()) == calibration_evaluation_json_bytes(build(reverse=True))


def test_walk_forward_purges_labels_that_cross_test_start() -> None:
    payload = build()
    by_key = {value["example_key"]: value for value in payload["examples"]}
    for fold in payload["folds"]:
        test_start = datetime.fromisoformat(fold["test_start_at"].replace("Z", "+00:00"))
        for key in fold["train_example_keys"]:
            target = by_key[key]["target"]
            assert target["status"] == "available"
            label_end = datetime.fromisoformat(target["label_end_at"].replace("Z", "+00:00"))
            assert label_end < test_start


def test_metrics_distinguish_target_unavailable_from_negative() -> None:
    payload = build()
    overall = payload["metrics"]["overall"]
    assert overall["target_available_count"] == 6
    assert overall["target_unavailable_count"] == 2
    assert sum(overall["direction_counts"].values()) == 6


def test_strict_loader_rejects_tampered_target_even_with_refreshed_fingerprints() -> None:
    payload = build()
    payload["examples"][0]["target"]["mid_price_change"] = "999"
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_target_change_mismatch"


def test_strict_loader_rejects_future_trade_feature_even_with_refreshed_fingerprints() -> None:
    payload = build()
    payload["examples"][0]["trade_support_features"]["latest_bucket_end_at"] = "2030-01-01T00:00:00Z"
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_trade_future_bucket"


def test_strict_loader_rejects_fold_assignment_tampering() -> None:
    payload = build()
    payload["folds"][0]["test_example_keys"] = []
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_walk_forward_plan_mismatch"


def test_strict_loader_rejects_metric_tampering() -> None:
    payload = build()
    payload["metrics"]["overall"]["example_count"] = 999
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_metrics_mismatch"


def test_unknown_top_level_field_is_rejected() -> None:
    payload = build()
    payload["generated_at"] = "2026-01-01T00:00:00Z"
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_dataset_keys_invalid"


def test_pretty_and_compact_keep_logical_fingerprints() -> None:
    payload = build()
    assert calibration_evaluation_json_bytes(payload) != calibration_evaluation_json_bytes(payload, pretty=True)
    assert parse_calibration_evaluation_dataset(json_load(calibration_evaluation_json_bytes(payload))).fingerprints == parse_calibration_evaluation_dataset(json_load(calibration_evaluation_json_bytes(payload, pretty=True))).fingerprints


def json_load(value: bytes) -> dict:
    import json
    return json.loads(value)


def test_target_definition_horizon_must_match_config() -> None:
    payload = build()
    payload["target_definition"]["target_horizon_seconds"] += 1
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_target_config_horizon_mismatch"


def test_future_target_reference_is_cross_checked_against_examples() -> None:
    payload = build()
    first = payload["examples"][0]
    first["target"]["future_observation_key"] = "review:101:2"
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_target_future_selection_mismatch"


def test_trade_bucket_granularity_counts_are_derived_from_keys() -> None:
    payload = build()
    example = next(value for value in payload["examples"] if value["trade_support_features"]["available_bucket_count"] > 0)
    example["trade_support_features"]["hourly_bucket_count"] = 0
    example["trade_support_features"]["daily_fallback_count"] = example["trade_support_features"]["available_bucket_count"]
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_trade_bucket_granularity_count_mismatch"


def test_trade_latest_end_is_derived_from_bucket_keys() -> None:
    payload = build()
    example = next(value for value in payload["examples"] if value["trade_support_features"]["available_bucket_count"] > 0)
    example["trade_support_features"]["latest_bucket_end_at"] = example["knowledge_cutoff"]
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_trade_latest_end_mismatch"


def test_diagnostics_source_observation_count_is_consistent() -> None:
    payload = build()
    payload["diagnostics"]["source_observation_count"] += 1
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_diagnostics_source_observation_count_mismatch"


def test_walk_forward_config_changes_plan_not_quote_features() -> None:
    dataset = combined_dataset()
    first = build_calibration_evaluation_dataset(combined_dataset=dataset, config=config())
    second_config = CalibrationEvaluationConfig(
        target_horizon_seconds=config().target_horizon_seconds,
        minimum_train_timestamps=config().minimum_train_timestamps,
        test_timestamp_count=config().test_timestamp_count,
        step_timestamp_count=config().step_timestamp_count,
        embargo_seconds=3600,
    )
    second = build_calibration_evaluation_dataset(combined_dataset=dataset, config=second_config)
    assert first["fingerprints"]["quote_only_features_fingerprint"] == second["fingerprints"]["quote_only_features_fingerprint"]
    assert first["fingerprints"]["calibration_example_source_fingerprint"] == second["fingerprints"]["calibration_example_source_fingerprint"]
    assert first["fingerprints"]["calibration_example_typed_fingerprint"] == second["fingerprints"]["calibration_example_typed_fingerprint"]
    assert first["fingerprints"]["walk_forward_plan_fingerprint"] != second["fingerprints"]["walk_forward_plan_fingerprint"]
    assert first["fingerprints"]["evaluation_report_fingerprint"] != second["fingerprints"]["evaluation_report_fingerprint"]


def test_combined_source_identity_changes_source_not_quote_features() -> None:
    first_dataset = combined_dataset()
    second_dataset = LoadedCombinedCalibrationDataset(
        market_histories=first_dataset.market_histories,
        historical_trade_histories=first_dataset.historical_trade_histories,
        market_provenance_by_key=first_dataset.market_provenance_by_key,
        historical_provenance_by_sample_key=first_dataset.historical_provenance_by_sample_key,
        availability_by_observation_key=first_dataset.availability_by_observation_key,
        mapping_manifest=first_dataset.mapping_manifest,
        quote_only_baseline_fingerprint=first_dataset.quote_only_baseline_fingerprint,
        trade_support_evidence_fingerprint=first_dataset.trade_support_evidence_fingerprint,
        combined_source_fingerprint="9" * 64,
        combined_typed_fingerprint=first_dataset.combined_typed_fingerprint,
        diagnostics=first_dataset.diagnostics,
    )
    first = build_calibration_evaluation_dataset(combined_dataset=first_dataset, config=config())
    second = build_calibration_evaluation_dataset(combined_dataset=second_dataset, config=config())
    assert first["fingerprints"]["quote_only_features_fingerprint"] == second["fingerprints"]["quote_only_features_fingerprint"]
    assert first["fingerprints"]["calibration_example_source_fingerprint"] != second["fingerprints"]["calibration_example_source_fingerprint"]
    assert first["fingerprints"]["calibration_example_typed_fingerprint"] == second["fingerprints"]["calibration_example_typed_fingerprint"]


def test_excluded_quality_tier_is_not_used_as_future_target() -> None:
    dataset = combined_dataset()
    provenance = dict(dataset.market_provenance_by_key)
    provenance["review:101:1"] = {"quality_tier": "legacy_server_time"}
    modified = LoadedCombinedCalibrationDataset(
        market_histories=dataset.market_histories,
        historical_trade_histories=dataset.historical_trade_histories,
        market_provenance_by_key=provenance,
        historical_provenance_by_sample_key=dataset.historical_provenance_by_sample_key,
        availability_by_observation_key=dataset.availability_by_observation_key,
        mapping_manifest=dataset.mapping_manifest,
        quote_only_baseline_fingerprint=dataset.quote_only_baseline_fingerprint,
        trade_support_evidence_fingerprint=dataset.trade_support_evidence_fingerprint,
        combined_source_fingerprint=dataset.combined_source_fingerprint,
        combined_typed_fingerprint=dataset.combined_typed_fingerprint,
        diagnostics=dataset.diagnostics,
    )
    wide = CalibrationEvaluationConfig(
        target_horizon_seconds=72 * 3600,
        minimum_train_timestamps=2,
        test_timestamp_count=1,
        step_timestamp_count=1,
    )
    payload = build_calibration_evaluation_dataset(combined_dataset=modified, config=wide)
    first = next(value for value in payload["examples"] if value["observation_key"] == "review:101:0")
    assert first["target"]["future_observation_key"] == "review:101:2"
    assert all(value["observation_key"] != "review:101:1" for value in payload["examples"])
    assert payload["diagnostics"]["excluded_quality_tier_count"] == 1


def test_fold_evaluation_purges_targets_crossing_next_test_boundary() -> None:
    payload = build()
    by_key = {value["example_key"]: value for value in payload["examples"]}
    for fold in payload["folds"]:
        next_start = fold["next_test_start_at"]
        for key in fold["test_evaluation_example_keys"]:
            target = by_key[key]["target"]
            assert target["status"] == "available"
            if next_start is not None:
                assert target["label_end_at"] < next_start


def test_same_item_must_keep_one_external_key() -> None:
    payload = build()
    item_id = payload["examples"][0]["item_id"]
    same_item = [value for value in payload["examples"] if value["item_id"] == item_id]
    assert len(same_item) >= 2
    same_item[1]["external_key"] = "different-external-key"
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_item_external_key_mismatch"


def test_external_key_cannot_identify_multiple_items() -> None:
    payload = build()
    item_ids = sorted({value["item_id"] for value in payload["examples"]})
    first_external = next(
        value["external_key"]
        for value in payload["examples"]
        if value["item_id"] == item_ids[0]
    )
    for value in payload["examples"]:
        if value["item_id"] == item_ids[1]:
            value["external_key"] = first_external
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_external_key_item_collision"


def test_same_item_must_keep_one_offline_sample_key() -> None:
    payload = build()
    item_id = payload["examples"][0]["item_id"]
    same_item = [value for value in payload["examples"] if value["item_id"] == item_id]
    assert len(same_item) >= 2
    same_item[1]["source_refs"]["offline_sample_key"] = "different-sample"
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_item_offline_sample_key_mismatch"


def test_offline_sample_key_cannot_identify_multiple_items() -> None:
    payload = build()
    item_ids = sorted({value["item_id"] for value in payload["examples"]})
    first_sample = next(
        value["source_refs"]["offline_sample_key"]
        for value in payload["examples"]
        if value["item_id"] == item_ids[0]
    )
    for value in payload["examples"]:
        if value["item_id"] == item_ids[1]:
            value["source_refs"]["offline_sample_key"] = first_sample
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_offline_sample_key_item_collision"


def test_diagnostics_are_bound_to_report_fingerprint() -> None:
    first = build()
    second = copy.deepcopy(first)
    second["diagnostics"]["excluded_quality_tier_count"] += 1
    second["diagnostics"]["source_observation_count"] += 1
    refresh_fingerprints(second)
    assert (
        first["fingerprints"]["evaluation_report_fingerprint"]
        != second["fingerprints"]["evaluation_report_fingerprint"]
    )
    parse_calibration_evaluation_dataset(second)


def test_diagnostics_source_item_count_has_derived_bounds() -> None:
    payload = build()
    payload["diagnostics"]["source_item_count"] = 999
    refresh_fingerprints(payload)
    with pytest.raises(CalibrationEvaluationValidationError) as error:
        parse_calibration_evaluation_dataset(payload)
    assert error.value.code == "calibration_diagnostics_source_item_count_invalid"
