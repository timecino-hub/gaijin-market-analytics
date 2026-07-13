from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from gaijin_market_analytics.datasets.market_history_export import (
    ITEM_MAPPING_SCHEMA_VERSION,
    MARKET_HISTORY_EXPORT_SCHEMA_VERSION,
    MarketHistoryExportProfile,
    MarketHistoryExportValidationError,
    MarketHistoryQualityTier,
    MarketHistorySourceRecord,
    build_market_history_export,
    item_mapping_manifest_sha256,
    market_history_export_json_bytes,
    market_observation_source_fingerprint,
    parse_item_mapping_manifest,
    parse_market_history_export,
    typed_market_history_fingerprint,
)


BASE = datetime(2026, 1, 1, tzinfo=UTC)


def source_record(
    *,
    review_id: str = "review-a",
    item_id: int = 1,
    observed_at: datetime = BASE,
    quality_tier: MarketHistoryQualityTier = MarketHistoryQualityTier.BROWSER_CAPTURED,
    observed_at_source: str = "browser_capture",
    bid_quantity: int | None = 5,
    ask_quantity: int | None = 7,
    acknowledged: bool = False,
    client_capture_id: str | None = "00000000-0000-4000-8000-000000000001",
    imported_at: datetime | None = BASE + timedelta(minutes=1),
    capture_sha256: str | None = "2" * 64,
    page_item_match: bool | None = True,
) -> MarketHistorySourceRecord:
    browser = quality_tier in {
        MarketHistoryQualityTier.BROWSER_CAPTURED,
        MarketHistoryQualityTier.BROWSER_CAPTURED_USER_TIME_EDITED,
    }
    captured_time = (
        observed_at
        if quality_tier is MarketHistoryQualityTier.BROWSER_CAPTURED
        else BASE
    )
    return MarketHistorySourceRecord(
        item_id=item_id,
        item_external_key=f"item-{item_id}",
        item_name=f"Item {item_id}",
        item_category="vehicle",
        item_rarity=None,
        item_is_active=True,
        observed_at=observed_at,
        best_bid=Decimal("12.340000"),
        best_ask=Decimal("13.000000"),
        bid_count=None,
        ask_count=None,
        estimated_volume=None,
        observed_bid_quantity=bid_quantity,
        observed_ask_quantity=ask_quantity,
        quantity_semantics="screenshot_display_quantity",
        source_type="screen_review",
        source_version="screen_review_candidate_v1",
        review_status="confirmed",
        source_review_id=review_id,
        candidate_version="screen_review_candidate_v1",
        candidate_sha256="1" * 64,
        quality_tier=quality_tier,
        observed_at_source=observed_at_source,
        incomplete_quantities_acknowledged=acknowledged,
        edited_fields=("observed_at",) if "edited" in quality_tier.value else (),
        capture_schema_version=("point_in_time_capture_v1" if browser else "legacy_v1"),
        client_capture_id=client_capture_id if browser else None,
        capture_started_at=captured_time if browser else None,
        captured_at=captured_time if browser else None,
        page_identity=(
            {
                "origin": "https://trade.gaijin.net",
                "market_path": f"/market/1067/item-{item_id}",
                "item_key": f"item-{item_id}",
            }
            if browser
            else None
        ),
        capture_sha256=capture_sha256 if browser else None,
        extension_version="1.0.0" if browser else None,
        imported_at=imported_at,
        page_item_key_matches_database_item=page_item_match if browser else None,
    )


def test_build_and_load_export_preserves_typed_observation() -> None:
    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    loaded = parse_market_history_export(payload)

    assert payload["schema_version"] == MARKET_HISTORY_EXPORT_SCHEMA_VERSION
    assert loaded.export_profile is MarketHistoryExportProfile.POINT_IN_TIME_PRIMARY
    assert len(loaded.histories) == 1
    observation = loaded.histories[0].observations[0]
    assert observation.observed_at == BASE
    assert observation.best_bid == Decimal("12.34")
    assert observation.best_ask == Decimal("13")
    assert observation.bid_count is None
    assert observation.ask_count is None
    assert observation.observed_bid_quantity == 5
    assert observation.observed_ask_quantity == 7
    assert observation.observation_key == "review:review-a"


def test_export_is_order_independent_and_byte_deterministic() -> None:
    first = source_record()
    second = source_record(
        review_id="review-b",
        item_id=2,
        observed_at=BASE + timedelta(hours=1),
        client_capture_id="00000000-0000-4000-8000-000000000002",
    )
    forward = build_market_history_export(
        [first, second], database_schema_revision="20260711_0004"
    )
    reverse = build_market_history_export(
        [second, first], database_schema_revision="20260711_0004"
    )

    assert forward == reverse
    assert market_history_export_json_bytes(forward) == market_history_export_json_bytes(
        reverse
    )


def test_same_review_exact_duplicate_is_deduplicated() -> None:
    record = source_record()
    payload = build_market_history_export(
        [record, record], database_schema_revision="20260711_0004"
    )
    assert payload["diagnostics"]["source_record_count"] == 1
    assert payload["diagnostics"]["included_record_count"] == 1


def test_same_review_changed_content_is_conflict() -> None:
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        build_market_history_export(
            [source_record(), source_record(ask_quantity=8)],
            database_schema_revision="20260711_0004",
        )
    assert exc_info.value.code == "source_review_conflict"


def test_same_item_and_time_is_conflict() -> None:
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        build_market_history_export(
            [
                source_record(),
                source_record(
                    review_id="review-b",
                    client_capture_id="00000000-0000-4000-8000-000000000002",
                ),
            ],
            database_schema_revision="20260711_0004",
        )
    assert exc_info.value.code == "source_item_time_conflict"


def test_duplicate_client_capture_id_is_conflict() -> None:
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        build_market_history_export(
            [
                source_record(),
                source_record(
                    review_id="review-b",
                    item_id=2,
                    observed_at=BASE + timedelta(hours=1),
                ),
            ],
            database_schema_revision="20260711_0004",
        )
    assert exc_info.value.code == "source_client_capture_id_conflict"


def test_primary_profile_excludes_user_edited_and_incomplete() -> None:
    edited = source_record(
        review_id="review-edited",
        item_id=2,
        observed_at=BASE + timedelta(hours=1),
        quality_tier=MarketHistoryQualityTier.BROWSER_CAPTURED_USER_TIME_EDITED,
        observed_at_source="user_edited",
        client_capture_id="00000000-0000-4000-8000-000000000002",
    )
    incomplete = source_record(
        review_id="review-incomplete",
        item_id=3,
        observed_at=BASE + timedelta(hours=2),
        bid_quantity=None,
        acknowledged=True,
        client_capture_id="00000000-0000-4000-8000-000000000003",
    )
    payload = build_market_history_export(
        [source_record(), edited, incomplete],
        database_schema_revision="20260711_0004",
    )
    assert payload["diagnostics"]["included_record_count"] == 1
    assert payload["diagnostics"]["excluded_record_count"] == 2


def test_reviewed_profile_includes_user_edited_and_acknowledged_incomplete() -> None:
    records = [
        source_record(
            review_id="review-edited",
            quality_tier=MarketHistoryQualityTier.BROWSER_CAPTURED_USER_TIME_EDITED,
            observed_at_source="user_edited",
        ),
        source_record(
            review_id="review-incomplete",
            item_id=2,
            observed_at=BASE + timedelta(hours=1),
            bid_quantity=None,
            acknowledged=True,
            client_capture_id="00000000-0000-4000-8000-000000000002",
        ),
    ]
    payload = build_market_history_export(
        records,
        database_schema_revision="20260711_0004",
        export_profile=MarketHistoryExportProfile.POINT_IN_TIME_REVIEWED,
    )
    assert payload["diagnostics"]["included_record_count"] == 2


def test_legacy_is_only_in_quote_or_all_valid_profile() -> None:
    legacy = source_record(
        quality_tier=MarketHistoryQualityTier.LEGACY_SERVER_TIME,
        observed_at_source="review_created_default",
        client_capture_id=None,
    )
    primary = build_market_history_export(
        [legacy], database_schema_revision="20260711_0004"
    )
    quote = build_market_history_export(
        [legacy],
        database_schema_revision="20260711_0004",
        export_profile=MarketHistoryExportProfile.QUOTE_ONLY_REVIEWED,
    )
    assert primary["diagnostics"]["included_record_count"] == 0
    assert quote["diagnostics"]["included_record_count"] == 1


def test_imported_at_does_not_change_source_or_typed_fingerprint() -> None:
    first = build_market_history_export(
        [source_record(imported_at=BASE)], database_schema_revision="20260711_0004"
    )
    changed = copy.deepcopy(first)
    changed["provenance"]["review:review-a"]["imported_at"] = (
        BASE + timedelta(days=1)
    ).isoformat().replace("+00:00", "Z")
    assert market_observation_source_fingerprint(changed) == first["source_fingerprint"]
    assert typed_market_history_fingerprint(changed) == first["typed_history_fingerprint"]


def test_provenance_only_change_affects_source_not_typed_fingerprint() -> None:
    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    changed = copy.deepcopy(payload)
    changed["provenance"]["review:review-a"]["capture_sha256"] = "9" * 64
    assert market_observation_source_fingerprint(changed) != payload["source_fingerprint"]
    assert typed_market_history_fingerprint(changed) == payload["typed_history_fingerprint"]


def test_typed_change_affects_both_fingerprints() -> None:
    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    changed = copy.deepcopy(payload)
    changed["items"][0]["observations"][0]["best_ask_price"] = "14"
    assert market_observation_source_fingerprint(changed) != payload["source_fingerprint"]
    assert typed_market_history_fingerprint(changed) != payload["typed_history_fingerprint"]


def test_wrong_fingerprint_is_rejected() -> None:
    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    payload["typed_history_fingerprint"] = "0" * 64
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        parse_market_history_export(payload)
    assert exc_info.value.code == "market_history_typed_fingerprint_mismatch"


def test_duplicate_observation_key_is_rejected() -> None:
    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    duplicate_item = copy.deepcopy(payload["items"][0])
    duplicate_item["item_id"] = 2
    payload["items"].append(duplicate_item)
    payload["diagnostics"]["included_record_count"] = 2
    payload["diagnostics"]["source_record_count"] = 2
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        market_observation_source_fingerprint(payload)
    assert exc_info.value.code == "market_history_duplicate_observation_key"


def test_null_quantities_are_not_converted_to_zero() -> None:
    payload = build_market_history_export(
        [source_record(bid_quantity=None, ask_quantity=None, acknowledged=True)],
        database_schema_revision="20260711_0004",
        export_profile=MarketHistoryExportProfile.POINT_IN_TIME_REVIEWED,
    )
    observation = parse_market_history_export(payload).histories[0].observations[0]
    assert observation.observed_bid_quantity is None
    assert observation.observed_ask_quantity is None
    assert observation.bid_count is None
    assert observation.ask_count is None


def test_item_mapping_fingerprint_is_order_independent() -> None:
    raw = {
        "schema_version": ITEM_MAPPING_SCHEMA_VERSION,
        "mappings": [
            {
                "offline_sample_key": "b",
                "database_item_id": 2,
                "database_external_key": "item-b",
                "confirmed_by_user": True,
            },
            {
                "offline_sample_key": "a",
                "database_item_id": 1,
                "database_external_key": "item-a",
                "confirmed_by_user": True,
            },
        ],
    }
    first = parse_item_mapping_manifest(raw)
    raw["mappings"].reverse()
    second = parse_item_mapping_manifest(raw)
    assert item_mapping_manifest_sha256(first) == item_mapping_manifest_sha256(second)


def test_item_mapping_requires_explicit_confirmation() -> None:
    raw = {
        "schema_version": ITEM_MAPPING_SCHEMA_VERSION,
        "mappings": [
            {
                "offline_sample_key": "a",
                "database_item_id": 1,
                "database_external_key": "item-a",
                "confirmed_by_user": False,
            }
        ],
    }
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        parse_item_mapping_manifest(raw)
    assert exc_info.value.code == "item_mapping_confirmation_required"


def test_compact_json_is_valid_utf8_and_ends_with_newline(tmp_path: Path) -> None:
    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    encoded = market_history_export_json_bytes(payload)
    assert encoded.endswith(b"\n")
    assert json.loads(encoded.decode("utf-8"))["source_fingerprint"] == payload[
        "source_fingerprint"
    ]


def test_primary_profile_excludes_unverified_page_item_identity() -> None:
    payload = build_market_history_export(
        [source_record(page_item_match=None)],
        database_schema_revision="20260711_0004",
    )
    assert payload["diagnostics"]["included_record_count"] == 0
    assert payload["diagnostics"]["exclusion_reason_counts"] == {
        "page_item_identity_unverified": 1
    }


def test_loader_rejects_wrong_quantity_semantics_even_with_recomputed_hashes() -> None:
    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    payload["items"][0]["observations"][0]["quantity_semantics"] = "unknown"
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        market_observation_source_fingerprint(payload)
    assert exc_info.value.code == "market_history_quantity_semantics_invalid"


def test_page_identity_item_key_must_match_decoded_market_path() -> None:
    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    payload["provenance"]["review:review-a"]["page_identity"]["item_key"] = "other"
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        market_observation_source_fingerprint(payload)
    assert exc_info.value.code == "page_identity_item_key_invalid"


def test_item_mapping_fingerprint_changes_source_and_typed_dataset_identity() -> None:
    first = build_market_history_export(
        [source_record()],
        database_schema_revision="20260711_0004",
        item_mapping_fingerprint="3" * 64,
    )
    second = build_market_history_export(
        [source_record()],
        database_schema_revision="20260711_0004",
        item_mapping_fingerprint="4" * 64,
    )
    assert first["source_fingerprint"] != second["source_fingerprint"]
    assert first["typed_history_fingerprint"] != second["typed_history_fingerprint"]


def test_stable_review_id_changes_source_but_not_typed_fingerprint() -> None:
    first = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    second = build_market_history_export(
        [source_record(review_id="review-b")],
        database_schema_revision="20260711_0004",
    )
    assert first["source_fingerprint"] != second["source_fingerprint"]
    assert first["typed_history_fingerprint"] == second["typed_history_fingerprint"]


def test_item_mapping_rejects_duplicate_database_external_key() -> None:
    raw = {
        "schema_version": ITEM_MAPPING_SCHEMA_VERSION,
        "mappings": [
            {
                "offline_sample_key": "a",
                "database_item_id": 1,
                "database_external_key": "shared-key",
                "confirmed_by_user": True,
            },
            {
                "offline_sample_key": "b",
                "database_item_id": 2,
                "database_external_key": "shared-key",
                "confirmed_by_user": True,
            },
        ],
    }
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        parse_item_mapping_manifest(raw)
    assert exc_info.value.code == "item_mapping_duplicate_external_key"


def test_loader_rejects_page_item_match_assertion_that_contradicts_item() -> None:
    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    payload["items"][0]["external_key"] = "different-item"
    payload["source_fingerprint"] = ""
    payload["typed_history_fingerprint"] = ""
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        market_observation_source_fingerprint(payload)
    assert exc_info.value.code == "market_history_page_item_match_inconsistent"


def test_loader_rejects_browser_capture_longer_than_protocol_limit() -> None:
    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    payload["provenance"]["review:review-a"]["capture_started_at"] = (
        BASE - timedelta(seconds=31)
    ).isoformat().replace("+00:00", "Z")
    payload["source_fingerprint"] = ""
    payload["typed_history_fingerprint"] = ""
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        market_observation_source_fingerprint(payload)
    assert exc_info.value.code == "market_history_capture_duration_too_long"


def test_loader_rejects_internally_inconsistent_diagnostics() -> None:
    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    payload["diagnostics"]["conflict_count"] = 1
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        market_observation_source_fingerprint(payload)
    assert exc_info.value.code == "market_history_diagnostics_conflict_count_invalid"

    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    payload["diagnostics"]["exclusion_reason_counts"] = {"fake": 1}
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        market_observation_source_fingerprint(payload)
    assert exc_info.value.code == "market_history_diagnostics_exclusion_reasons_mismatch"

    payload = build_market_history_export(
        [source_record()], database_schema_revision="20260711_0004"
    )
    payload["diagnostics"]["quality_tier_counts"] = {"fake": 1}
    with pytest.raises(MarketHistoryExportValidationError) as exc_info:
        market_observation_source_fingerprint(payload)
    assert exc_info.value.code == "market_history_quality_tier_invalid"
