from __future__ import annotations

import struct
import zlib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from api.schemas.local_recognition import ImageMetadata, RecognitionMetadata
from api.screen_recognition.contracts import OcrFieldEvidence, OcrResult
from api.screen_recognition.ocr_backend import OcrBackendError
from api.services.local_recognition_store import review_store


@pytest.fixture(autouse=True)
def clear_review_store() -> None:
    review_store.clear()


def test_capabilities_report_local_review_boundary(client: TestClient) -> None:
    response = client.get("/api/v1/local-recognition/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["ocr_backend"] == "windows-ocr"
    assert body["current_layout_profile"] == "gaijin-market-desktop-v1"
    assert body["max_image_bytes"] == 10 * 1024 * 1024
    assert body["max_image_pixels"] == 40_000_000
    assert body["store_capacity"] == 100
    assert body["store_ttl_seconds"] == 7200
    assert body["database_written"] is False
    assert body["handles_history_images"] is False


def test_openapi_exposes_local_review_paths_without_extension_endpoint(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/local-recognition/reviews" in paths
    assert "/api/v1/local-recognition/reviews/{review_id}/confirm" in paths
    assert "/api/v1/local-recognition/reviews/{review_id}/unreadable" in paths
    assert not any("extension" in path for path in paths)


def test_create_review_returns_202_then_background_ocr_creates_pending_review(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_paths: list[Path] = []
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(seen_paths=seen_paths),
    )

    response = client.post(
        "/api/v1/local-recognition/reviews",
        files={"file": ("sample.png", png_bytes(), "image/png")},
    )

    assert response.status_code == 202
    created = response.json()
    assert created["status"] == "processing"

    detail = client.get(f"/api/v1/local-recognition/reviews/{created['review_id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "pending_review"
    assert body["ocr_candidate"]["item_name_normalized"] == "Synthetic Alpha"
    assert body["ocr_candidate"]["best_bid"] == "12.34"
    assert body["draft"]["final_total_bid_quantity"] == 5
    assert body["draft"]["observed_at_source"] == "review_created_default"
    assert seen_paths and not seen_paths[0].exists()


def test_background_ocr_exception_sets_failed_and_deletes_original(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_paths: list[Path] = []
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: BrokenRecognizer(seen_paths=seen_paths),
    )

    response = client.post(
        "/api/v1/local-recognition/reviews",
        files={"file": ("sample.png", png_bytes(), "image/png")},
    )

    assert response.status_code == 202
    review_id = response.json()["review_id"]
    detail = client.get(f"/api/v1/local-recognition/reviews/{review_id}")
    assert detail.json()["status"] == "failed"
    assert detail.json()["errors"] == ["recognition_failed"]
    assert seen_paths and not seen_paths[0].exists()


def test_upload_rejects_signature_mismatch_and_unsupported_format(client: TestClient) -> None:
    mismatch = client.post(
        "/api/v1/local-recognition/reviews",
        files={"file": ("sample.jpg", png_bytes(), "image/jpeg")},
    )
    unsupported = client.post(
        "/api/v1/local-recognition/reviews",
        files={"file": ("sample.gif", b"GIF89a", "image/gif")},
    )

    assert mismatch.status_code == 415
    assert mismatch.json()["detail"]["code"] == "image_signature_mismatch"
    assert unsupported.status_code == 415
    assert unsupported.json()["detail"]["code"] == "unsupported_image_format"


def test_decoded_pixel_limit_is_enforced(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("api.services.local_recognition.MAX_IMAGE_PIXELS", 10)

    response = client.post(
        "/api/v1/local-recognition/reviews",
        files={"file": ("large.png", png_bytes(width=4, height=3), "image/png")},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "image_dimensions_too_large"


def test_store_full_does_not_silently_delete_live_reviews(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    monkeypatch.setattr(review_store, "max_reviews", 1)

    first = client.post(
        "/api/v1/local-recognition/reviews",
        files={"file": ("one.png", png_bytes(), "image/png")},
    )
    second = client.post(
        "/api/v1/local-recognition/reviews",
        files={"file": ("two.png", png_bytes(), "image/png")},
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "review_store_full"
    assert client.get(f"/api/v1/local-recognition/reviews/{first.json()['review_id']}").status_code == 200


def test_clear_store_requires_explicit_confirmation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    client.post(
        "/api/v1/local-recognition/reviews",
        files={"file": ("sample.png", png_bytes(), "image/png")},
    )

    rejected = client.delete("/api/v1/local-recognition/reviews")
    cleared = client.delete("/api/v1/local-recognition/reviews?confirm=true")

    assert rejected.status_code == 400
    assert rejected.json()["detail"]["code"] == "clear_confirmation_required"
    assert cleared.status_code == 200
    assert cleared.json()["cleared"] == 1


def test_patch_forbids_ocr_field_mutation_and_rejects_terminal_reviews(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    forbidden = client.patch(
        f"/api/v1/local-recognition/reviews/{review_id}",
        json={"ocr_candidate": {"best_bid": "1.00"}},
    )
    assert forbidden.status_code == 422

    reject = client.post(f"/api/v1/local-recognition/reviews/{review_id}/reject", json={})
    assert reject.status_code == 200

    terminal_patch = client.patch(
        f"/api/v1/local-recognition/reviews/{review_id}",
        json={"reviewer_note": "late"},
    )
    assert terminal_patch.status_code == 409
    assert terminal_patch.json()["detail"]["code"] == "review_not_editable"


def test_confirm_manual_identity_computes_edits_and_candidate_flags(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    response = client.post(
        f"/api/v1/local-recognition/reviews/{review_id}/confirm",
        json={
            "item_key": "admin-alpha",
            "final_item_name": "Synthetic Alpha Edited",
            "final_best_bid": "12.34",
            "final_best_ask": "13.01",
            "final_total_bid_quantity": None,
            "final_total_ask_quantity": 0,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed_with_edits"
    candidate = body["candidate"]
    assert candidate["imported"] is False
    assert candidate["database_written"] is False
    assert candidate["item_identity"] == {
        "item_id": None,
        "item_key": "admin-alpha",
        "item_name": "Synthetic Alpha Edited",
    }
    assert candidate["total_bid_quantity"] is None
    assert candidate["total_ask_quantity"] == 0
    assert candidate["recognition"]["edited_fields"] == [
        "item_name",
        "best_ask",
        "total_bid_quantity",
        "total_ask_quantity",
    ]


def test_confirm_selected_item_rereads_server_identity(
    client: TestClient,
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    item_id = insert_item(migrated_database)
    review_id = create_pending_review(client)

    response = client.post(
        f"/api/v1/local-recognition/reviews/{review_id}/confirm",
        json={"selected_item_id": item_id},
    )

    assert response.status_code == 200
    identity = response.json()["candidate"]["item_identity"]
    assert identity == {
        "item_id": item_id,
        "item_key": "server-key",
        "item_name": "Server Name",
    }
    draft = response.json()["draft"]
    assert draft["identity_sources"]["item_key"] == "canonical_item"
    assert draft["identity_sources"]["final_item_name"] == "canonical_item"


def test_confirm_item_key_only_does_not_reuse_ocr_final_name(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    response = client.post(
        f"/api/v1/local-recognition/reviews/{review_id}/confirm",
        json={"item_key": "manual-only"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "item_identity_required"
    assert (
        response.json()["detail"]["message"]
        == "Manual identity requires an explicitly provided item_key and final_item_name."
    )


def test_confirm_final_item_name_only_is_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    response = client.post(
        f"/api/v1/local-recognition/reviews/{review_id}/confirm",
        json={"final_item_name": "Manual Name"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "item_identity_required"


def test_patch_item_key_only_then_confirm_empty_is_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    patched = client.patch(
        f"/api/v1/local-recognition/reviews/{review_id}",
        json={"item_key": "manual-key"},
    )
    response = client.post(f"/api/v1/local-recognition/reviews/{review_id}/confirm", json={})

    assert patched.status_code == 200
    assert patched.json()["draft"]["identity_sources"]["item_key"] == "user_draft"
    assert patched.json()["draft"]["identity_sources"]["final_item_name"] == "ocr_initial"
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "item_identity_required"


def test_patch_final_item_name_only_then_confirm_empty_is_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    patched = client.patch(
        f"/api/v1/local-recognition/reviews/{review_id}",
        json={"final_item_name": "Manual Name"},
    )
    response = client.post(f"/api/v1/local-recognition/reviews/{review_id}/confirm", json={})

    assert patched.status_code == 200
    assert patched.json()["draft"]["identity_sources"]["item_key"] is None
    assert patched.json()["draft"]["identity_sources"]["final_item_name"] == "user_draft"
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "item_identity_required"


def test_patch_manual_identity_then_confirm_empty_succeeds(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    patched = client.patch(
        f"/api/v1/local-recognition/reviews/{review_id}",
        json={"item_key": "manual-key", "final_item_name": "Manual Name"},
    )
    response = client.post(f"/api/v1/local-recognition/reviews/{review_id}/confirm", json={})

    assert patched.status_code == 200
    assert response.status_code == 200
    assert response.json()["candidate"]["item_identity"] == {
        "item_id": None,
        "item_key": "manual-key",
        "item_name": "Manual Name",
    }
    assert response.json()["draft"]["identity_sources"]["item_key"] == "user_draft"
    assert response.json()["draft"]["identity_sources"]["final_item_name"] == "user_draft"


def test_confirm_manual_identity_directly_succeeds(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    response = client.post(
        f"/api/v1/local-recognition/reviews/{review_id}/confirm",
        json={"item_key": "manual-key", "final_item_name": "Manual Name"},
    )

    assert response.status_code == 200
    assert response.json()["candidate"]["item_identity"] == {
        "item_id": None,
        "item_key": "manual-key",
        "item_name": "Manual Name",
    }
    assert response.json()["draft"]["identity_sources"]["item_key"] == "confirm_request"
    assert response.json()["draft"]["identity_sources"]["final_item_name"] == "confirm_request"


def test_ocr_name_same_as_final_name_without_user_submission_is_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    response = client.post(
        f"/api/v1/local-recognition/reviews/{review_id}/confirm",
        json={"item_key": "manual-key"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "item_identity_required"


def test_ocr_name_same_as_user_submitted_final_name_succeeds_without_edit_flag(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    response = client.post(
        f"/api/v1/local-recognition/reviews/{review_id}/confirm",
        json={"item_key": "manual-key", "final_item_name": "Synthetic Alpha"},
    )

    assert response.status_code == 200
    assert response.json()["candidate"]["item_identity"]["item_name"] == "Synthetic Alpha"
    assert response.json()["draft"]["identity_sources"]["final_item_name"] == "confirm_request"
    assert "item_name" not in response.json()["candidate"]["recognition"]["edited_fields"]


def test_identity_conflict_and_missing_identity_are_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    missing_id = create_pending_review(client)
    conflict_id = create_pending_review(client)
    full_conflict_id = create_pending_review(client)

    missing = client.post(
        f"/api/v1/local-recognition/reviews/{missing_id}/confirm",
        json={},
    )
    conflict = client.post(
        f"/api/v1/local-recognition/reviews/{conflict_id}/confirm",
        json={"selected_item_id": 1, "item_key": "manual"},
    )
    full_conflict = client.post(
        f"/api/v1/local-recognition/reviews/{full_conflict_id}/confirm",
        json={
            "selected_item_id": 1,
            "item_key": "manual",
            "final_item_name": "Manual Name",
        },
    )

    assert missing.status_code == 400
    assert missing.json()["detail"]["code"] == "item_identity_required"
    assert conflict.status_code == 400
    assert conflict.json()["detail"]["code"] == "item_identity_conflict"
    assert full_conflict.status_code == 400
    assert full_conflict.json()["detail"]["code"] == "item_identity_conflict"


def test_observed_at_rules_and_user_edited_source(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    naive = client.patch(
        f"/api/v1/local-recognition/reviews/{review_id}",
        json={"observed_at": "2026-07-01T00:00:00"},
    )
    assert naive.status_code == 400
    assert naive.json()["detail"]["code"] == "observed_at_timezone_required"

    patched = client.patch(
        f"/api/v1/local-recognition/reviews/{review_id}",
        json={"observed_at": "2026-07-01T00:00:00+00:00"},
    )
    assert patched.status_code == 200
    assert patched.json()["draft"]["observed_at_source"] == "user_edited"


def test_price_and_quantity_validation(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    float_price = client.patch(
        f"/api/v1/local-recognition/reviews/{review_id}",
        json={"final_best_bid": 12.34},
    )
    over_cap = client.patch(
        f"/api/v1/local-recognition/reviews/{review_id}",
        json={"final_best_ask": "2000.01"},
    )
    negative_quantity = client.patch(
        f"/api/v1/local-recognition/reviews/{review_id}",
        json={"final_total_ask_quantity": -1},
    )

    assert float_price.status_code == 400
    assert float_price.json()["detail"]["code"] == "invalid_price_string"
    assert over_cap.status_code == 400
    assert over_cap.json()["detail"]["code"] == "price_out_of_market_range"
    assert negative_quantity.status_code == 400
    assert negative_quantity.json()["detail"]["code"] == "invalid_quantity"


def create_pending_review(client: TestClient) -> str:
    response = client.post(
        "/api/v1/local-recognition/reviews",
        files={"file": ("sample.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 202
    return str(response.json()["review_id"])


class FakeRecognizer:
    backend_name = "windows-ocr"
    backend_version = "fake-windows-ocr"
    test_scope = "end_to_end"

    def __init__(self, seen_paths: list[Path] | None = None) -> None:
        self._seen_paths = seen_paths

    def recognize(self, invocation: Any) -> OcrResult:
        if self._seen_paths is not None:
            self._seen_paths.append(invocation.image_path)
        return OcrResult(
            backend_name=self.backend_name,
            backend_version=self.backend_version,
            fields={
                "item_name": OcrFieldEvidence("item_name", "Synthetic Alpha", None),
                "best_bid": OcrFieldEvidence("best_bid", "12.34", None),
                "best_ask": OcrFieldEvidence("best_ask", "13.00", None),
                "total_bid_quantity": OcrFieldEvidence("total_bid_quantity", "5", None),
                "total_ask_quantity": OcrFieldEvidence("total_ask_quantity", "7", None),
            },
        )


class BrokenRecognizer(FakeRecognizer):
    def recognize(self, invocation: Any) -> OcrResult:
        if self._seen_paths is not None:
            self._seen_paths.append(invocation.image_path)
        raise OcrBackendError("boom")


def png_bytes(width: int = 1200, height: int = 800) -> bytes:
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def insert_item(database_url: str) -> int:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            return int(
                conn.execute(
                    text(
                        """
                        INSERT INTO items (external_key, name, category, is_active)
                        VALUES ('server-key', 'Server Name', 'vehicle', true)
                        RETURNING id
                        """
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()
