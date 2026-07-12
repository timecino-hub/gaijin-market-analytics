from datetime import UTC, datetime, timedelta
from threading import Thread

import pytest

from decimal import Decimal

from api.schemas.local_recognition import ImageMetadata, ReviewDraft
from api.services.local_extension_pairing import CaptureAlreadyReserved, CaptureConflict, LocalExtensionPairingStore
from api.services.local_recognition import LocalRecognitionError, _validate_confirm_values, create_review_record
from api.services.local_recognition_source import SourceMetadataError, extension_source_metadata
from api.services.local_recognition_store import review_store


NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)
CAPTURE_ID = "123e4567-e89b-42d3-a456-426614174000"


def metadata(**overrides):
    values = {
        "pairing_id": "pair_1",
        "capture_sha256": "a" * 64,
        "extension_version": "0.1.0",
        "source_url": "https://trade.gaijin.net/market/1067/Test?secret=1#x",
        "source_tab_title": None,
        "client_capture_id": CAPTURE_ID,
        "capture_started_at": "2026-07-12T11:59:59Z",
        "captured_at": "2026-07-12T12:00:00Z",
        "page_identity": '{"origin":"https://trade.gaijin.net","market_path":"/market/1067/Test","item_key":"Test"}',
        "received_at": NOW,
    }
    values.update(overrides)
    return extension_source_metadata(**values)


def test_capture_bundle_is_normalized_and_safe() -> None:
    value = metadata()
    assert value.client_capture_id == CAPTURE_ID
    assert value.captured_at == NOW
    assert value.capture_duration_ms == 1000
    assert value.page_identity.item_key == "Test"
    assert value.source_url_safe == "https://trade.gaijin.net/market/1067/Test"


@pytest.mark.parametrize("field", ["client_capture_id", "capture_started_at", "captured_at", "page_identity"])
def test_capture_bundle_requires_all_fields(field: str) -> None:
    with pytest.raises(SourceMetadataError, match="required"):
        metadata(**{field: None})


def test_capture_rejects_naive_and_reversed_or_long_timestamps() -> None:
    with pytest.raises(SourceMetadataError, match="timezone"):
        metadata(captured_at="2026-07-12T12:00:00")
    with pytest.raises(SourceMetadataError, match="must not be later"):
        metadata(capture_started_at="2026-07-12T12:00:01Z")
    with pytest.raises(SourceMetadataError, match="30 seconds"):
        metadata(capture_started_at="2026-07-12T11:59:00Z")


def test_capture_rejects_clock_skew() -> None:
    with pytest.raises(SourceMetadataError, match="future"):
        metadata(
            captured_at=(NOW + timedelta(minutes=6)).isoformat(),
            capture_started_at=(NOW + timedelta(minutes=6, seconds=-1)).isoformat(),
        )
    with pytest.raises(SourceMetadataError, match="too old"):
        metadata(captured_at=(NOW - timedelta(minutes=16)).isoformat(), capture_started_at=(NOW - timedelta(minutes=16, seconds=1)).isoformat())


def test_page_identity_rejects_query_fragment_origin_and_forged_item_key() -> None:
    for identity in (
        '{"origin":"https://trade.gaijin.net","market_path":"/market/1067/Test?q=1","item_key":"Test"}',
        '{"origin":"https://example.com","market_path":"/market/1067/Test","item_key":"Test"}',
        '{"origin":"https://trade.gaijin.net","market_path":"/market/1067/Test","item_key":"Other"}',
    ):
        with pytest.raises(SourceMetadataError):
            metadata(page_identity=identity)


def test_source_url_must_match_page_identity_after_sanitizing() -> None:
    assert metadata(source_url="https://trade.gaijin.net/market/1067/Test?q=1#x").page_identity.item_key == "Test"
    with pytest.raises(SourceMetadataError, match="same public page"):
        metadata(source_url="https://trade.gaijin.net/market/1067/Other")


def test_item_key_decode_rejects_malformed_utf8_without_replacement_character() -> None:
    for tail in ("%", "%2", "%ZZ", "Test%4Z", "%FF", "%C3%28"):
        with pytest.raises(SourceMetadataError, match="escape|encoding"):
            metadata(page_identity=f'{{"origin":"https://trade.gaijin.net","market_path":"/market/1067/{tail}","item_key":null}}')


@pytest.mark.parametrize("length", [512, 513])
def test_item_key_length_is_explicitly_enforced(length: int) -> None:
    identity = f'{{"origin":"https://trade.gaijin.net","market_path":"/market/1067/{"A" * length}","item_key":null}}'
    source_url = f"https://trade.gaijin.net/market/1067/{'A' * length}"
    if length == 512:
        assert metadata(page_identity=identity, source_url=source_url).page_identity.item_key == "A" * length
    else:
        with pytest.raises(SourceMetadataError, match="512"):
            metadata(page_identity=identity, source_url=source_url)


def test_browser_capture_defaults_suggested_observed_at_to_captured_at() -> None:
    review_store.clear()
    record = create_review_record(
        image=ImageMetadata(original_filename="x.png", width=1, height=1, format="png"),
        source_metadata=metadata(),
    )
    assert record.suggested_observed_at == NOW
    assert record.draft.observed_at == NOW
    assert record.draft.observed_at_source.value == "browser_capture"


def test_capture_id_exact_retry_deduplicates_and_changed_provenance_conflicts() -> None:
    store = LocalExtensionPairingStore(process_secret=b"x" * 32)
    first = store.reserve_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, provenance_sha256="a" * 64, now=NOW)
    assert first.reserved
    store.bind_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, review_id="review_1")
    retry = store.reserve_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, provenance_sha256="a" * 64, now=NOW)
    assert retry.review_id == "review_1"
    with pytest.raises(CaptureConflict):
        store.reserve_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, provenance_sha256="b" * 64, now=NOW)


def test_pending_client_capture_waits_for_binding_without_sleep() -> None:
    store = LocalExtensionPairingStore(process_secret=b"x" * 32)
    store.reserve_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, provenance_sha256="a" * 64, now=NOW)
    result: list[str | None] = []
    thread = Thread(target=lambda: result.append(store.wait_for_client_capture_review(pairing_id="p", client_capture_id=CAPTURE_ID, timeout_seconds=1)))
    thread.start()
    store.bind_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, review_id="review_1")
    thread.join(timeout=2)
    assert result == ["review_1"]


def test_client_wait_timeout_keeps_other_reservation_and_explicit_rollback_is_local() -> None:
    store = LocalExtensionPairingStore(process_secret=b"x" * 32)
    store.reserve_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, provenance_sha256="a" * 64, now=NOW)
    assert store.wait_for_client_capture_review(pairing_id="p", client_capture_id=CAPTURE_ID, timeout_seconds=0) is None
    with pytest.raises(CaptureAlreadyReserved):
        store.reserve_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, provenance_sha256="a" * 64, now=NOW)
    store.rollback_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID)
    assert store.reserve_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, provenance_sha256="a" * 64, now=NOW).reserved


def test_image_pending_waits_and_different_pages_do_not_merge() -> None:
    store = LocalExtensionPairingStore(process_secret=b"x" * 32)
    store.reserve_capture(pairing_id="p", capture_sha256="image-page-a", now=NOW)
    result: list[str | None] = []
    thread = Thread(target=lambda: result.append(store.wait_for_capture_review(pairing_id="p", capture_sha256="image-page-a", timeout_seconds=1)))
    thread.start()
    store.bind_capture(pairing_id="p", capture_sha256="image-page-a", review_id="review_1")
    thread.join(timeout=2)
    assert result == ["review_1"]
    assert store.reserve_capture(pairing_id="p", capture_sha256="image-page-b", now=NOW).reserved


def test_capture_id_survives_image_window_but_not_review_retention() -> None:
    store = LocalExtensionPairingStore(process_secret=b"x" * 32)
    store.reserve_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, provenance_sha256="a" * 64, now=NOW)
    store.bind_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, review_id="review_1")
    assert store.reserve_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, provenance_sha256="a" * 64, now=NOW + timedelta(seconds=21)).review_id == "review_1"
    assert store.reserve_client_capture(pairing_id="p", client_capture_id=CAPTURE_ID, provenance_sha256="a" * 64, now=NOW + timedelta(hours=3)).reserved


def test_missing_quantities_require_explicit_acknowledgement() -> None:
    draft = ReviewDraft(final_best_bid=Decimal("1"), final_best_ask=Decimal("2"))
    with pytest.raises(LocalRecognitionError, match="explicit acknowledgement"):
        _validate_confirm_values(draft)
    _validate_confirm_values(draft.model_copy(update={"acknowledge_incomplete_quantities": True}))
    complete = draft.model_copy(update={"final_total_bid_quantity": 1, "final_total_ask_quantity": 2})
    _validate_confirm_values(complete)
