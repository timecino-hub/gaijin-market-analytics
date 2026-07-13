from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from gaijin_market_analytics.backtesting import (
    HistoricalTradeBucket,
    HistoricalTradeGranularity,
    ItemHistoricalTradeHistory,
    ItemMarketHistory,
    OpportunityCalibrationConfig,
    TemporalSplitConfig,
    run_opportunity_calibration,
)
from gaijin_market_analytics.contracts import MarketObservation
from gaijin_market_analytics.datasets.combined_calibration import (
    COMBINED_CALIBRATION_SCHEMA_VERSION,
    CombinedCalibrationValidationError,
    build_combined_calibration_dataset,
    combined_calibration_json_bytes,
    combined_calibration_source_fingerprint,
    combined_calibration_typed_fingerprint,
    parse_combined_calibration_dataset,
    quote_only_baseline_fingerprint,
    select_available_trade_buckets,
    trade_support_evidence_fingerprint,
)
from gaijin_market_analytics.datasets.gaijin_history_json import (
    OfflineHistoryDataset,
    OfflineHistoryManifest,
    OfflineHistorySample,
    OfflineHistorySampleData,
)
from gaijin_market_analytics.datasets.market_history_export import (
    ItemMappingEntry,
    ItemMappingManifest,
    LoadedMarketHistoryDataset,
    MarketHistoryExportProfile,
)
from gaijin_market_analytics.fees import GAIJIN_MARKET_FEE_POLICY_V1
from gaijin_market_analytics.market_rules import GAIJIN_MARKET_RULES_V1
from gaijin_market_analytics.strategies.rule_based_v1 import RuleBasedV1


BASE = datetime(2026, 1, 3, 12, tzinfo=UTC)


def observation(
    at: datetime,
    *,
    key: str = "review:one",
    bid: str = "12.00",
    ask: str = "13.00",
) -> MarketObservation:
    return MarketObservation(
        observed_at=at,
        best_bid=Decimal(bid),
        best_ask=Decimal(ask),
        bid_count=None,
        ask_count=None,
        estimated_volume=None,
        observation_key=key,
        observed_bid_quantity=4,
        observed_ask_quantity=5,
        quantity_semantics="screenshot_display_quantity",
        source_type="screen_review",
        review_status="confirmed",
    )


def bucket(
    start: datetime,
    *,
    item_id: int = 9001,
    granularity: HistoricalTradeGranularity = HistoricalTradeGranularity.HOUR_1,
    price: str = "12.50",
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


def historical_dataset(
    *,
    item_key: str | None = "item-one",
    buckets: tuple[HistoricalTradeBucket, ...] | None = None,
    include_extra: bool = False,
    offline_item_id: int = 9001,
) -> OfflineHistoryDataset:
    values = buckets or (
        bucket(datetime(2026, 1, 2, 10, tzinfo=UTC)),
        bucket(
            datetime(2026, 1, 2, tzinfo=UTC),
            granularity=HistoricalTradeGranularity.DAY_1,
        ),
        bucket(datetime(2026, 1, 3, 12, tzinfo=UTC)),
    )
    first_spec = OfflineHistorySample(
        offline_item_id=offline_item_id,
        sample_key="sample-one",
        display_name="Historical display name",
        file="sample-one.json",
        expected_sha256="a" * 64,
        item_key=item_key,
        source_url="https://example.invalid/market/item-one",
        captured_at="2026-01-04T00:00:00Z",
    )
    samples = [
        OfflineHistorySampleData(
            spec=first_spec,
            source_file=Path("sample-one.json"),
            source_file_sha256="a" * 64,
            history=ItemHistoricalTradeHistory(
                item_id=offline_item_id,
                buckets=tuple(
                    HistoricalTradeBucket(
                        item_id=offline_item_id,
                        granularity=value.granularity,
                        bucket_start_utc=value.bucket_start_utc,
                        bucket_duration_seconds=value.bucket_duration_seconds,
                        reported_vwap_price=value.reported_vwap_price,
                        reported_trade_volume=value.reported_trade_volume,
                        price_semantics=value.price_semantics,
                        volume_semantics=value.volume_semantics,
                        source_schema_version=value.source_schema_version,
                    )
                    for value in values
                ),
            ),
        )
    ]
    specs = [first_spec]
    if include_extra:
        second_spec = OfflineHistorySample(
            offline_item_id=9002,
            sample_key="sample-two",
            display_name="Unmapped historical item",
            file="sample-two.json",
            expected_sha256="b" * 64,
        )
        specs.append(second_spec)
        samples.append(
            OfflineHistorySampleData(
                spec=second_spec,
                source_file=Path("sample-two.json"),
                source_file_sha256="b" * 64,
                history=ItemHistoricalTradeHistory(item_id=9002, buckets=()),
            )
        )
    manifest = OfflineHistoryManifest(
        schema_version="round6_offline_history_manifest_v1",
        samples=tuple(specs),
        manifest_path=Path("manifest.json"),
        input_root=Path("."),
    )
    return OfflineHistoryDataset(
        manifest=manifest,
        samples=tuple(samples),
        source_manifest_sha256="1" * 64,
        typed_trade_dataset_sha256="2" * 64,
    )


def market_dataset(*, include_extra: bool = False) -> LoadedMarketHistoryDataset:
    first = ItemMarketHistory(
        item_id=101,
        observations=(observation(BASE),),
    )
    histories = [first]
    identities: dict[int, dict[str, object]] = {
        101: {
            "external_key": "item-one",
            "name": "Database display name",
            "category": "vehicle",
            "rarity": None,
            "is_active": True,
        }
    }
    provenance: dict[str, dict[str, object]] = {
        "review:one": {
            "source_review_id": "one",
            "candidate_version": "screen_review_candidate_v1",
            "candidate_sha256": "3" * 64,
            "client_capture_id": "00000000-0000-4000-8000-000000000001",
            "capture_schema_version": "point_in_time_capture_v1",
            "capture_started_at": "2026-01-03T12:00:00Z",
            "captured_at": "2026-01-03T12:00:00Z",
            "final_observed_at": "2026-01-03T12:00:00Z",
            "observed_at_source": "browser_capture",
            "page_identity": {
                "origin": "https://trade.gaijin.net",
                "market_path": "/market/1067/item-one",
                "item_key": "item-one",
            },
            "capture_sha256": "4" * 64,
            "extension_version": "1.0.0",
            "incomplete_quantities_acknowledged": False,
            "edited_fields": [],
            "quality_tier": "browser_captured",
            "imported_at": "2026-01-03T12:01:00Z",
            "page_item_key_matches_database_item": True,
        }
    }
    if include_extra:
        histories.append(
            ItemMarketHistory(
                item_id=102,
                observations=(
                    observation(
                        BASE + timedelta(hours=1),
                        key="review:two",
                    ),
                ),
            )
        )
        identities[102] = {
            "external_key": "item-two",
            "name": "Extra",
            "category": "vehicle",
            "rarity": None,
            "is_active": True,
        }
        provenance["review:two"] = {
            **provenance["review:one"],
            "source_review_id": "two",
            "client_capture_id": "00000000-0000-4000-8000-000000000002",
            "final_observed_at": "2026-01-03T13:00:00Z",
            "captured_at": "2026-01-03T13:00:00Z",
            "capture_started_at": "2026-01-03T13:00:00Z",
        }
    return LoadedMarketHistoryDataset(
        histories=tuple(histories),
        provenance_by_key=provenance,
        item_identity_by_id=identities,
        source_fingerprint="5" * 64,
        typed_history_fingerprint="6" * 64,
        export_profile=MarketHistoryExportProfile.POINT_IN_TIME_REVIEWED,
        item_mapping_fingerprint=None,
        diagnostics={},
    )


def mapping(
    *,
    sample_key: str = "sample-one",
    database_item_id: int = 101,
    external_key: str = "item-one",
) -> ItemMappingManifest:
    return ItemMappingManifest(
        schema_version="round6_item_mapping_v1",
        mappings=(
            ItemMappingEntry(
                offline_sample_key=sample_key,
                database_item_id=database_item_id,
                database_external_key=external_key,
                confirmed_by_user=True,
            ),
        ),
    )


def built_payload(**kwargs: object) -> dict[str, object]:
    return build_combined_calibration_dataset(
        historical_dataset=kwargs.get("historical") or historical_dataset(),
        market_dataset=kwargs.get("market") or market_dataset(),
        item_mapping=kwargs.get("item_mapping") or mapping(),
    )


def refresh_fingerprints(payload: dict[str, object]) -> None:
    quote = payload["quote_only_baseline"]
    trade = payload["trade_support_evidence"]
    assert isinstance(quote, dict)
    assert isinstance(trade, dict)
    quote["fingerprint"] = quote_only_baseline_fingerprint(quote)
    trade["fingerprint"] = trade_support_evidence_fingerprint(trade)
    payload["combined_source_fingerprint"] = combined_calibration_source_fingerprint(payload)
    payload["combined_typed_fingerprint"] = combined_calibration_typed_fingerprint(payload)


def test_build_and_parse_combined_dataset() -> None:
    payload = built_payload()
    loaded = parse_combined_calibration_dataset(payload)

    assert payload["schema_version"] == COMBINED_CALIBRATION_SCHEMA_VERSION
    assert len(loaded.market_histories) == 1
    assert len(loaded.historical_trade_histories) == 1
    assert loaded.market_histories[0].item_id == 101
    assert loaded.historical_trade_histories[0].item_id == 101
    assert loaded.mapping_manifest.mappings[0].offline_sample_key == "sample-one"
    assert loaded.availability_by_observation_key["review:one"].available_bucket_keys == (
        "1h:2026-01-02T10:00:00Z",
    )


def test_output_is_order_independent_and_byte_deterministic() -> None:
    forward = built_payload()
    reverse_mapping = ItemMappingManifest(
        schema_version="round6_item_mapping_v1",
        mappings=tuple(reversed(mapping().mappings)),
    )
    reverse = built_payload(item_mapping=reverse_mapping)
    assert forward == reverse
    assert combined_calibration_json_bytes(forward) == combined_calibration_json_bytes(reverse)


def test_unmapped_inputs_are_reported_but_not_guessed() -> None:
    payload = built_payload(
        historical=historical_dataset(include_extra=True),
        market=market_dataset(include_extra=True),
    )
    diagnostics = payload["diagnostics"]
    assert isinstance(diagnostics, dict)
    assert diagnostics["unmapped_historical_sample_count"] == 1
    assert diagnostics["unmapped_market_item_count"] == 1
    assert diagnostics["combined_item_count"] == 1


@pytest.mark.parametrize(
    ("item_mapping", "code"),
    [
        (mapping(sample_key="missing"), "combined_mapping_historical_sample_missing"),
        (mapping(database_item_id=999), "combined_mapping_market_item_missing"),
        (mapping(external_key="wrong"), "combined_mapping_database_external_key_mismatch"),
    ],
)
def test_mapping_must_resolve_explicit_identities(
    item_mapping: ItemMappingManifest, code: str
) -> None:
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        built_payload(item_mapping=item_mapping)
    assert exc_info.value.code == code


def test_historical_item_key_must_match_database_external_key() -> None:
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        built_payload(historical=historical_dataset(item_key="different"))
    assert exc_info.value.code == "combined_mapping_historical_item_key_mismatch"


def test_documented_keyless_historical_sample_is_allowed() -> None:
    payload = built_payload(historical=historical_dataset(item_key=None))
    evidence = payload["mapping_evidence"]
    assert isinstance(evidence, list)
    assert evidence[0]["historical_item_key"] is None
    assert evidence[0]["historical_item_key_matches_database_external_key"] is None


def test_only_completed_buckets_are_available_at_observation_time() -> None:
    payload = built_payload()
    trade = payload["trade_support_evidence"]
    assert isinstance(trade, dict)
    availability = trade["items"][0]["observation_availability"][0]
    assert availability["available_bucket_keys"] == ["1h:2026-01-02T10:00:00Z"]
    assert "1h:2026-01-03T12:00:00Z" not in availability["available_bucket_keys"]


def test_unfinished_hour_and_day_are_excluded() -> None:
    cutoff = datetime(2026, 1, 3, 12, 30, tzinfo=UTC)
    values = (
        bucket(datetime(2026, 1, 3, 12, tzinfo=UTC)),
        bucket(
            datetime(2026, 1, 3, tzinfo=UTC),
            granularity=HistoricalTradeGranularity.DAY_1,
        ),
    )
    assert select_available_trade_buckets(values, cutoff) == ()


def test_hourly_bucket_replaces_daily_fallback_for_same_utc_day() -> None:
    cutoff = datetime(2026, 1, 3, tzinfo=UTC)
    values = (
        bucket(
            datetime(2026, 1, 2, tzinfo=UTC),
            granularity=HistoricalTradeGranularity.DAY_1,
        ),
        bucket(datetime(2026, 1, 2, 10, tzinfo=UTC)),
    )
    selected = select_available_trade_buckets(values, cutoff)
    assert [value.granularity for value in selected] == [
        HistoricalTradeGranularity.HOUR_1
    ]


def test_missing_hour_is_not_synthesized_as_zero_trade() -> None:
    values = (bucket(datetime(2026, 1, 2, 10, tzinfo=UTC)),)
    selected = select_available_trade_buckets(values, BASE)
    assert len(selected) == 1
    assert selected[0].reported_trade_volume == 3


def test_trade_change_does_not_change_quote_only_baseline() -> None:
    first = built_payload()
    changed_history = historical_dataset(
        buckets=(bucket(datetime(2026, 1, 2, 10, tzinfo=UTC), price="99"),)
    )
    second = built_payload(historical=changed_history)
    assert first["quote_only_baseline"] == second["quote_only_baseline"]
    assert (
        first["trade_support_evidence"]["fingerprint"]
        != second["trade_support_evidence"]["fingerprint"]
    )
    assert first["combined_typed_fingerprint"] != second["combined_typed_fingerprint"]


def test_quote_change_changes_quote_and_combined_fingerprints() -> None:
    first = built_payload()
    changed = market_dataset()
    changed_history = ItemMarketHistory(
        item_id=101,
        observations=(observation(BASE, bid="11.50"),),
    )
    changed = LoadedMarketHistoryDataset(
        histories=(changed_history,),
        provenance_by_key=changed.provenance_by_key,
        item_identity_by_id=changed.item_identity_by_id,
        source_fingerprint=changed.source_fingerprint,
        typed_history_fingerprint="7" * 64,
        export_profile=changed.export_profile,
        item_mapping_fingerprint=None,
        diagnostics={},
    )
    second = built_payload(market=changed)
    assert (
        first["quote_only_baseline"]["fingerprint"]
        != second["quote_only_baseline"]["fingerprint"]
    )
    assert first["combined_typed_fingerprint"] != second["combined_typed_fingerprint"]


def test_mapping_change_changes_combined_fingerprint() -> None:
    first = built_payload()
    changed = copy.deepcopy(first)
    changed["input_fingerprints"]["item_mapping_fingerprint"] = "9" * 64
    refresh_fingerprints(changed)
    assert first["combined_typed_fingerprint"] != changed["combined_typed_fingerprint"]
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        parse_combined_calibration_dataset(changed)
    assert exc_info.value.code == "combined_mapping_fingerprint_mismatch"


def test_parser_rejects_forged_future_bucket_availability_even_with_new_hashes() -> None:
    payload = copy.deepcopy(built_payload())
    availability = payload["trade_support_evidence"]["items"][0][
        "observation_availability"
    ][0]
    availability["available_bucket_keys"].append("1h:2026-01-03T12:00:00Z")
    availability["available_bucket_keys"].sort()
    availability["selected_hourly_bucket_count"] += 1
    refresh_fingerprints(payload)
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        parse_combined_calibration_dataset(payload)
    assert exc_info.value.code == "combined_no_lookahead_availability_mismatch"


def test_parser_rejects_tampered_fingerprint() -> None:
    payload = built_payload()
    payload["combined_source_fingerprint"] = "0" * 64
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        parse_combined_calibration_dataset(payload)
    assert exc_info.value.code == "combined_source_fingerprint_mismatch"


def test_parser_rejects_missing_market_provenance() -> None:
    payload = copy.deepcopy(built_payload())
    payload["provenance"]["market_by_observation_key"].clear()
    refresh_fingerprints(payload)
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        parse_combined_calibration_dataset(payload)
    assert exc_info.value.code == "combined_market_provenance_missing"


def test_parser_rejects_diagnostics_mismatch() -> None:
    payload = copy.deepcopy(built_payload())
    payload["diagnostics"]["trade_bucket_count"] = 999
    refresh_fingerprints(payload)
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        parse_combined_calibration_dataset(payload)
    assert exc_info.value.code == "combined_diagnostics_bucket_count_mismatch"


def test_loaded_dataset_preserves_quote_only_calibration_result_when_trade_is_added() -> None:
    payload = built_payload()
    loaded = parse_combined_calibration_dataset(payload)
    config = OpportunityCalibrationConfig(
        lookback_horizon_days=7,
        forward_horizon_days=(7,),
        start_at=BASE,
        end_at=BASE + timedelta(days=2),
        cadence_days=1,
        temporal_splits=TemporalSplitConfig(
            train_end_at=BASE,
            validation_end_at=BASE + timedelta(days=1),
        ),
        minimum_snapshot_count=1,
        minimum_entry_quote_observations=1,
    )
    kwargs = dict(
        histories=loaded.market_histories,
        config=config,
        fee_policy=GAIJIN_MARKET_FEE_POLICY_V1,
        market_rules=GAIJIN_MARKET_RULES_V1,
        analysis_strategy=RuleBasedV1(),
    )
    quote_only = run_opportunity_calibration(**kwargs)
    with_trade = run_opportunity_calibration(
        **kwargs,
        historical_trade_histories=loaded.historical_trade_histories,
    )
    assert quote_only.dataset_sha256 == with_trade.dataset_sha256
    assert quote_only.cases == with_trade.cases
    assert quote_only.cohorts == with_trade.cohorts
    assert quote_only.score_bins == with_trade.score_bins



def test_market_imported_at_does_not_change_combined_fingerprints() -> None:
    first = built_payload()
    changed_market = market_dataset()
    changed_provenance = copy.deepcopy(changed_market.provenance_by_key)
    changed_provenance["review:one"]["imported_at"] = "2026-02-01T00:00:00Z"
    changed_market = LoadedMarketHistoryDataset(
        histories=changed_market.histories,
        provenance_by_key=changed_provenance,
        source_fingerprint=changed_market.source_fingerprint,
        typed_history_fingerprint=changed_market.typed_history_fingerprint,
        export_profile=changed_market.export_profile,
        item_mapping_fingerprint=None,
        diagnostics={},
        item_identity_by_id=changed_market.item_identity_by_id,
    )
    second = built_payload(market=changed_market)
    assert first["combined_source_fingerprint"] == second["combined_source_fingerprint"]
    assert first["combined_typed_fingerprint"] == second["combined_typed_fingerprint"]


def test_parser_rejects_page_identity_item_mismatch_after_rehash() -> None:
    payload = copy.deepcopy(built_payload())
    provenance = payload["provenance"]["market_by_observation_key"]["review:one"]
    provenance["page_identity"] = {
        "origin": "https://trade.gaijin.net",
        "market_path": "/market/1067/item-two",
        "item_key": "item-two",
    }
    refresh_fingerprints(payload)
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        parse_combined_calibration_dataset(payload)
    assert exc_info.value.code == "combined_market_page_item_key_mismatch"


def test_parser_rejects_non_v4_capture_id_after_rehash() -> None:
    payload = copy.deepcopy(built_payload())
    payload["provenance"]["market_by_observation_key"]["review:one"][
        "client_capture_id"
    ] = "00000000-0000-1000-8000-000000000001"
    refresh_fingerprints(payload)
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        parse_combined_calibration_dataset(payload)
    assert exc_info.value.code == "combined_market_client_capture_id_invalid"

def test_display_names_do_not_participate_in_mapping_decision() -> None:
    payload = built_payload()
    historical = payload["provenance"]["historical_by_sample_key"]["sample-one"]
    assert historical["display_name"] == "Historical display name"
    quote = payload["quote_only_baseline"]["items"][0]
    assert quote["external_key"] == "item-one"


def test_direct_mapping_manifest_still_receives_strict_schema_validation() -> None:
    invalid = ItemMappingManifest(schema_version="wrong", mappings=mapping().mappings)
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        built_payload(item_mapping=invalid)
    assert exc_info.value.code == "item_mapping_schema_unsupported"


def test_parser_rejects_legacy_count_in_quote_block_after_rehash() -> None:
    payload = copy.deepcopy(built_payload())
    payload["quote_only_baseline"]["items"][0]["observations"][0]["bid_count"] = 3
    refresh_fingerprints(payload)
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        parse_combined_calibration_dataset(payload)
    assert exc_info.value.code == "combined_quote_legacy_count_present"


def test_parser_rejects_historical_source_url_query_after_rehash() -> None:
    payload = copy.deepcopy(built_payload())
    payload["provenance"]["historical_by_sample_key"]["sample-one"][
        "source_url"
    ] = "https://example.invalid/item-one?unexpected=value"
    refresh_fingerprints(payload)
    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        parse_combined_calibration_dataset(payload)
    assert exc_info.value.code == "combined_historical_provenance_source_url_invalid"


def test_parser_preserves_user_edited_quality_tier() -> None:
    payload = copy.deepcopy(built_payload())
    observation = payload["quote_only_baseline"]["items"][0]["observations"][0]
    provenance = payload["provenance"]["market_by_observation_key"]["review:one"]
    observation["quality_tier"] = "browser_captured_user_time_edited"
    provenance["quality_tier"] = "browser_captured_user_time_edited"
    provenance["observed_at_source"] = "user_edited"
    provenance["edited_fields"] = ["observed_at"]
    provenance["capture_started_at"] = "2026-01-03T11:59:55Z"
    provenance["captured_at"] = "2026-01-03T12:00:00Z"
    provenance["final_observed_at"] = observation["observed_at"]
    payload["diagnostics"]["quality_tier_counts"] = {
        "browser_captured_user_time_edited": 1
    }
    refresh_fingerprints(payload)

    loaded = parse_combined_calibration_dataset(payload)
    assert loaded.market_histories[0].observations[0].observed_at == BASE
    assert (
        loaded.market_provenance_by_key["review:one"]["quality_tier"]
        == "browser_captured_user_time_edited"
    )


def test_parser_preserves_acknowledged_incomplete_quantity() -> None:
    payload = copy.deepcopy(built_payload())
    observation = payload["quote_only_baseline"]["items"][0]["observations"][0]
    provenance = payload["provenance"]["market_by_observation_key"]["review:one"]
    observation["observed_bid_quantity"] = None
    provenance["incomplete_quantities_acknowledged"] = True
    refresh_fingerprints(payload)

    loaded = parse_combined_calibration_dataset(payload)
    assert loaded.market_histories[0].observations[0].observed_bid_quantity is None
    assert (
        loaded.market_provenance_by_key["review:one"][
            "incomplete_quantities_acknowledged"
        ]
        is True
    )


def test_parser_rejects_duplicate_observation_availability_after_rehash() -> None:
    payload = built_payload()
    trade = payload["trade_support_evidence"]
    assert isinstance(trade, dict)
    items = trade["items"]
    assert isinstance(items, list)
    availability = items[0]["observation_availability"]
    availability.append(copy.deepcopy(availability[0]))
    availability.sort(key=lambda value: (value["knowledge_cutoff"], value["observation_key"]))
    refresh_fingerprints(payload)

    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        parse_combined_calibration_dataset(payload)
    assert exc_info.value.code == "combined_trade_availability_observation_duplicate"


def test_parser_rejects_unsupported_historical_source_schema_after_rehash() -> None:
    payload = built_payload()
    trade = payload["trade_support_evidence"]
    assert isinstance(trade, dict)
    items = trade["items"]
    assert isinstance(items, list)
    items[0]["buckets"][0]["source_schema_version"] = "unsupported_history_v2"
    refresh_fingerprints(payload)

    with pytest.raises(CombinedCalibrationValidationError) as exc_info:
        parse_combined_calibration_dataset(payload)
    assert exc_info.value.code == "combined_trade_source_schema_invalid"


def test_source_only_offline_item_id_does_not_change_typed_fingerprint() -> None:
    first = built_payload(historical=historical_dataset(offline_item_id=9001))
    second = built_payload(historical=historical_dataset(offline_item_id=9901))

    assert first["combined_source_fingerprint"] != second["combined_source_fingerprint"]
    assert first["combined_typed_fingerprint"] == second["combined_typed_fingerprint"]
    assert first["quote_only_baseline"] == second["quote_only_baseline"]
    assert first["trade_support_evidence"] == second["trade_support_evidence"]
