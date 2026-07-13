from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from api.db.models import Item, MarketSnapshot, OrderBookObservation, ScreenReviewImport
from api.schemas.local_recognition import (
    CandidateRecognition,
    ItemIdentity,
    ObservedAtSource,
    ReviewSourceMetadata,
    ReviewedCandidate,
    SafePageIdentity,
)
from api.services.local_recognition_candidate import (
    candidate_audit_payload,
    candidate_payload_sha256,
)
from api.services.market_history_export import (
    MarketHistoryExportError,
    classify_quality_tier,
    source_record_from_models,
)
from gaijin_market_analytics.datasets.market_history_export import (
    MarketHistoryQualityTier,
)


BASE = datetime(2026, 1, 1, tzinfo=UTC)


def candidate(
    *,
    observed_at: datetime = BASE,
    observed_at_source: ObservedAtSource = ObservedAtSource.BROWSER_CAPTURE,
    bid_quantity: int | None = 5,
    ask_quantity: int | None = 7,
    acknowledged: bool = False,
    status: str = "confirmed",
) -> ReviewedCandidate:
    return ReviewedCandidate(
        review_id="review-a",
        observed_at=observed_at,
        observed_at_source=observed_at_source,
        item_identity=ItemIdentity(
            item_id=1,
            item_key="item-a",
            item_name="Item A",
        ),
        best_bid=Decimal("12.34"),
        best_ask=Decimal("13.00"),
        total_bid_quantity=bid_quantity,
        total_ask_quantity=ask_quantity,
        acknowledge_incomplete_quantities=acknowledged,
        recognition=CandidateRecognition(
            layout_name="market",
            layout_version="1",
            config_sha256="3" * 64,
            ocr_backend="windows-ocr",
            edited_fields=(
                ["observed_at"]
                if observed_at_source is ObservedAtSource.USER_EDITED
                else []
            ),
            parser_version="1",
            runner_version="1",
        ),
        status=status,
    )


def browser_metadata() -> ReviewSourceMetadata:
    return ReviewSourceMetadata(
        source="browser_extension",
        extension_version="1.0.0",
        source_url_safe="https://trade.gaijin.net/market/1067/item-a",
        source_tab_title="Item A",
        capture_sha256="2" * 64,
        pairing_id="pairing-placeholder",
        capture_schema_version="point_in_time_capture_v1",
        client_capture_id="00000000-0000-4000-8000-000000000001",
        capture_started_at=BASE,
        captured_at=BASE,
        capture_duration_ms=0,
        page_identity=SafePageIdentity(
            origin="https://trade.gaijin.net",
            market_path="/market/1067/item-a",
            item_key="item-a",
        ),
        observation_time_semantics="browser_captured_at",
    )


def model_bundle(
    *,
    reviewed: ReviewedCandidate | None = None,
    metadata: ReviewSourceMetadata | None = None,
):
    reviewed = reviewed or candidate()
    metadata = metadata or browser_metadata()
    payload = candidate_audit_payload(reviewed)
    item = Item(
        id=1,
        external_key="item-a",
        name="Item A",
        category="vehicle",
        rarity=None,
        is_active=True,
    )
    snapshot = MarketSnapshot(
        id=10,
        item_id=1,
        observed_at=reviewed.observed_at,
        best_bid=reviewed.best_bid,
        best_ask=reviewed.best_ask,
        ask_count=None,
        bid_count=None,
        estimated_volume=None,
        source_import_job_id=None,
    )
    imported = ScreenReviewImport(
        id=20,
        review_id=reviewed.review_id,
        item_id=1,
        market_snapshot_id=10,
        review_status=reviewed.status,
        candidate_version=reviewed.candidate_version,
        candidate_sha256=candidate_payload_sha256(payload),
        total_bid_quantity=reviewed.total_bid_quantity,
        total_ask_quantity=reviewed.total_ask_quantity,
        candidate_payload=payload,
        source_metadata=metadata.model_dump(mode="json"),
        reviewer_note=None,
        imported_at=BASE + timedelta(minutes=1),
    )
    observation = OrderBookObservation(
        id=30,
        market_snapshot_id=10,
        screen_review_import_id=20,
        observed_bid_quantity=reviewed.total_bid_quantity,
        observed_ask_quantity=reviewed.total_ask_quantity,
        quantity_semantics="screenshot_display_quantity",
        source_type="screen_review",
        source_version=reviewed.candidate_version,
        review_status=reviewed.status,
        created_at=BASE + timedelta(minutes=1),
    )
    return imported, snapshot, observation, item


def test_browser_capture_models_convert_to_source_record() -> None:
    imported, snapshot, observation, item = model_bundle()
    record = source_record_from_models(
        imported=imported,
        snapshot=snapshot,
        observation=observation,
        item=item,
    )
    assert record.quality_tier is MarketHistoryQualityTier.BROWSER_CAPTURED
    assert record.observed_at == BASE
    assert record.best_bid == Decimal("12.34")
    assert record.observed_bid_quantity == 5
    assert record.observation_key == "review:review-a"
    assert record.page_item_key_matches_database_item is True


def test_user_edited_time_is_separate_quality_tier() -> None:
    reviewed = candidate(
        observed_at=BASE + timedelta(seconds=1),
        observed_at_source=ObservedAtSource.USER_EDITED,
    )
    imported, snapshot, observation, item = model_bundle(reviewed=reviewed)
    record = source_record_from_models(
        imported=imported,
        snapshot=snapshot,
        observation=observation,
        item=item,
    )
    assert (
        record.quality_tier
        is MarketHistoryQualityTier.BROWSER_CAPTURED_USER_TIME_EDITED
    )
    assert record.captured_at == BASE
    assert record.observed_at == BASE + timedelta(seconds=1)


def test_legacy_metadata_is_explicit_quality_tier() -> None:
    reviewed = candidate(
        observed_at_source=ObservedAtSource.REVIEW_CREATED_DEFAULT
    )
    metadata = ReviewSourceMetadata(source="manual_upload")
    assert (
        classify_quality_tier(source_metadata=metadata, candidate=reviewed)
        is MarketHistoryQualityTier.LEGACY_SERVER_TIME
    )


def test_partial_capture_metadata_is_unknown() -> None:
    reviewed = candidate()
    metadata = browser_metadata().model_copy(update={"captured_at": None})
    assert (
        classify_quality_tier(source_metadata=metadata, candidate=reviewed)
        is MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE
    )


def test_candidate_hash_tampering_is_rejected() -> None:
    imported, snapshot, observation, item = model_bundle()
    imported.candidate_sha256 = "0" * 64
    with pytest.raises(MarketHistoryExportError) as exc_info:
        source_record_from_models(
            imported=imported,
            snapshot=snapshot,
            observation=observation,
            item=item,
        )
    assert exc_info.value.code == "export_candidate_hash_mismatch"


def test_quantity_copy_mismatch_is_rejected() -> None:
    imported, snapshot, observation, item = model_bundle()
    observation.observed_ask_quantity = 8
    with pytest.raises(MarketHistoryExportError) as exc_info:
        source_record_from_models(
            imported=imported,
            snapshot=snapshot,
            observation=observation,
            item=item,
        )
    assert exc_info.value.code == "export_ask_quantity_mismatch"


def test_fk_combination_mismatch_is_rejected() -> None:
    imported, snapshot, observation, item = model_bundle()
    observation.market_snapshot_id = 11
    with pytest.raises(MarketHistoryExportError) as exc_info:
        source_record_from_models(
            imported=imported,
            snapshot=snapshot,
            observation=observation,
            item=item,
        )
    assert exc_info.value.code == "export_observation_snapshot_relation_mismatch"


def test_missing_quantity_requires_persisted_acknowledgement() -> None:
    reviewed = candidate(bid_quantity=None, acknowledged=False)
    imported, snapshot, observation, item = model_bundle(reviewed=reviewed)
    with pytest.raises(MarketHistoryExportError) as exc_info:
        source_record_from_models(
            imported=imported,
            snapshot=snapshot,
            observation=observation,
            item=item,
        )
    assert exc_info.value.code == "export_incomplete_quantities_not_acknowledged"


def test_acknowledged_missing_quantity_remains_null() -> None:
    reviewed = candidate(bid_quantity=None, acknowledged=True)
    imported, snapshot, observation, item = model_bundle(reviewed=reviewed)
    record = source_record_from_models(
        imported=imported,
        snapshot=snapshot,
        observation=observation,
        item=item,
    )
    assert record.observed_bid_quantity is None
    assert record.incomplete_quantities_acknowledged is True


def test_source_metadata_unexpected_field_is_rejected() -> None:
    imported, snapshot, observation, item = model_bundle()
    imported.source_metadata["unexpected"] = "value"
    with pytest.raises(MarketHistoryExportError) as exc_info:
        source_record_from_models(
            imported=imported,
            snapshot=snapshot,
            observation=observation,
            item=item,
        )
    assert exc_info.value.code == "export_source_metadata_invalid"


def test_legacy_candidate_payload_missing_new_default_field_can_still_validate() -> None:
    reviewed = candidate(observed_at_source=ObservedAtSource.REVIEW_CREATED_DEFAULT)
    metadata = ReviewSourceMetadata(source="manual_upload")
    imported, snapshot, observation, item = model_bundle(
        reviewed=reviewed,
        metadata=metadata,
    )
    imported.candidate_payload.pop("acknowledge_incomplete_quantities")
    imported.candidate_sha256 = candidate_payload_sha256(imported.candidate_payload)
    record = source_record_from_models(
        imported=imported,
        snapshot=snapshot,
        observation=observation,
        item=item,
    )
    assert record.quality_tier is MarketHistoryQualityTier.LEGACY_SERVER_TIME


def test_candidate_audit_state_must_remain_immutable() -> None:
    imported, snapshot, observation, item = model_bundle()
    imported.candidate_payload["imported"] = True
    imported.candidate_sha256 = candidate_payload_sha256(imported.candidate_payload)
    with pytest.raises(MarketHistoryExportError) as exc_info:
        source_record_from_models(
            imported=imported,
            snapshot=snapshot,
            observation=observation,
            item=item,
        )
    assert exc_info.value.code == "export_candidate_audit_state_invalid"


def test_point_in_time_source_url_must_match_page_identity() -> None:
    reviewed = candidate()
    metadata = browser_metadata().model_copy(
        update={"source_url_safe": "https://trade.gaijin.net/market/1067/other"}
    )
    imported, snapshot, observation, item = model_bundle(
        reviewed=reviewed, metadata=metadata
    )
    with pytest.raises(MarketHistoryExportError) as exc_info:
        source_record_from_models(
            imported=imported,
            snapshot=snapshot,
            observation=observation,
            item=item,
        )
    assert exc_info.value.code == "export_quality_tier_unknown"


def test_naive_point_in_time_timestamp_is_unknown_not_internal_error() -> None:
    metadata = browser_metadata().model_copy(
        update={"capture_started_at": BASE.replace(tzinfo=None)}
    )
    assert (
        classify_quality_tier(source_metadata=metadata, candidate=candidate())
        is MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE
    )


def test_user_edited_time_requires_persisted_edit_marker() -> None:
    reviewed = candidate(
        observed_at=BASE + timedelta(seconds=1),
        observed_at_source=ObservedAtSource.USER_EDITED,
    )
    reviewed.recognition.edited_fields = []
    assert (
        classify_quality_tier(
            source_metadata=browser_metadata(), candidate=reviewed
        )
        is MarketHistoryQualityTier.UNKNOWN_OR_INCOMPLETE
    )


def test_page_item_key_mismatch_is_rejected() -> None:
    metadata = browser_metadata().model_copy(
        update={
            "source_url_safe": "https://trade.gaijin.net/market/1067/other",
            "page_identity": SafePageIdentity(
                origin="https://trade.gaijin.net",
                market_path="/market/1067/other",
                item_key="other",
            ),
        }
    )
    imported, snapshot, observation, item = model_bundle(metadata=metadata)
    with pytest.raises(MarketHistoryExportError) as exc_info:
        source_record_from_models(
            imported=imported,
            snapshot=snapshot,
            observation=observation,
            item=item,
        )
    assert exc_info.value.code == "source_page_item_key_mismatch"


def test_candidate_audit_payload_strips_database_result_fields() -> None:
    reviewed = candidate().model_copy(
        update={
            "imported": True,
            "database_written": True,
            "market_snapshot_created": True,
            "database_item_id": 10,
            "screen_review_import_id": 20,
            "market_snapshot_id": 30,
            "order_book_observation_id": 40,
            "imported_at": BASE,
        }
    )
    payload = candidate_audit_payload(reviewed)
    assert payload["imported"] is False
    assert payload["database_written"] is False
    assert payload["market_snapshot_created"] is False
    assert payload["database_item_id"] is None
    assert payload["screen_review_import_id"] is None
    assert payload["market_snapshot_id"] is None
    assert payload["order_book_observation_id"] is None
    assert payload["imported_at"] is None


def test_candidate_payload_hash_is_key_order_independent() -> None:
    payload = candidate_audit_payload(candidate())
    reversed_payload = dict(reversed(list(payload.items())))
    assert candidate_payload_sha256(payload) == candidate_payload_sha256(
        reversed_payload
    )
