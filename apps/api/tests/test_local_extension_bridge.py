from __future__ import annotations

import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.screen_recognition.contracts import OcrFieldEvidence, OcrResult
from api.services.local_extension_pairing import (
    CaptureAlreadyReserved,
    LocalExtensionPairingStore,
    pairing_store,
)
from api.services.local_recognition_store import review_store


MANAGEMENT_HEADERS = {"Origin": "http://localhost:3000"}


@pytest.fixture(autouse=True)
def clear_bridge_stores() -> None:
    pairing_store.clear()
    review_store.clear()


def test_management_endpoints_require_allowed_origin(client: TestClient) -> None:
    missing = client.post("/api/v1/local-recognition/pairing-codes")
    denied = client.post(
        "/api/v1/local-recognition/pairing-codes",
        headers={"Origin": "http://example.invalid"},
    )
    allowed = client.post("/api/v1/local-recognition/pairing-codes", headers=MANAGEMENT_HEADERS)

    assert missing.status_code == 403
    assert missing.json()["detail"]["code"] == "local_management_origin_required"
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "local_management_origin_denied"
    assert allowed.status_code == 201
    assert len(allowed.json()["pairing_code"].replace("-", "")) == 12


def test_pairing_code_is_one_time_and_store_does_not_save_plaintext_token(client: TestClient) -> None:
    created = create_pairing_code(client)
    paired = pair_code(client, created)
    repeat = client.post("/api/v1/local-recognition/pair", json=pair_payload(created))

    assert paired.status_code == 200
    token = paired.json()["token"]
    assert repeat.status_code == 400
    assert repeat.json()["detail"]["code"] == "pairing_code_consumed"
    token_hashes = pairing_store.token_hashes_for_testing()
    assert token not in token_hashes
    assert token_hashes and all(len(value) == 64 for value in token_hashes)


def test_pairing_code_failed_attempt_limit_invalidates_code(client: TestClient) -> None:
    created = create_pairing_code(client)
    bad_payload = pair_payload(created) | {"pairing_code": "0000-0000-0000"}

    responses = [client.post("/api/v1/local-recognition/pair", json=bad_payload) for _ in range(5)]
    correct = client.post("/api/v1/local-recognition/pair", json=pair_payload(created))

    assert [response.status_code for response in responses] == [400, 400, 400, 400, 400]
    assert responses[-1].json()["detail"]["code"] == "pairing_code_attempts_exceeded"
    assert correct.status_code == 400
    assert correct.json()["detail"]["code"] == "pairing_code_attempts_exceeded"


def test_pairing_code_expiry_is_rejected() -> None:
    store = LocalExtensionPairingStore()
    now = datetime(2026, 7, 1, tzinfo=UTC)
    created = store.create_pairing_code(now=now)

    with pytest.raises(Exception) as exc_info:
        store.pair(
            pairing_code_id=created.pairing_code_id,
            pairing_code=created.pairing_code,
            client_name=None,
            extension_version=None,
            client_key="127.0.0.1",
            now=now + timedelta(seconds=601),
        )

    assert getattr(exc_info.value, "code") == "pairing_code_expired"


def test_revoke_pairing_immediately_rejects_extension_upload(client: TestClient) -> None:
    paired = pair_code(client, create_pairing_code(client)).json()
    revoked = client.delete(
        f"/api/v1/local-recognition/pairings/{paired['pairing_id']}",
        headers=MANAGEMENT_HEADERS,
    )
    upload = extension_upload(client, paired["token"])

    assert revoked.status_code == 204
    assert upload.status_code == 403
    assert upload.json()["detail"]["code"] == "extension_token_revoked"


def test_extension_upload_creates_browser_extension_review_with_sanitized_source(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("api.services.local_recognition.get_recognizer", lambda _name: FakeRecognizer())
    paired = pair_code(client, create_pairing_code(client)).json()

    response = extension_upload(
        client,
        paired["token"],
        data={
            "source_url": "https://user:secret@example.com:443/path/item?token=hidden#frag",
            "source_tab_title": "Visible Market Tab",
            "extension_version": "0.1.0",
        },
    )

    assert response.status_code == 202
    assert response.json()["deduplicated"] is False
    detail = client.get(f"/api/v1/local-recognition/reviews/{response.json()['review_id']}")
    metadata = detail.json()["source_metadata"]
    assert metadata["source"] == "browser_extension"
    assert metadata["source_url_safe"] == "https://example.com:443/path/item"
    assert metadata["source_tab_title"] == "Visible Market Tab"
    assert metadata["extension_version"] == "0.1.0"
    assert metadata["pairing_id"] == paired["pairing_id"]
    assert detail.json()["candidate"] is None


def test_extension_upload_deduplicates_same_capture_without_second_ocr(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recognizer = FakeRecognizer()
    monkeypatch.setattr("api.services.local_recognition.get_recognizer", lambda _name: recognizer)
    paired = pair_code(client, create_pairing_code(client)).json()

    first = extension_upload(client, paired["token"])
    second = extension_upload(client, paired["token"])

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["deduplicated"] is True
    assert second.json()["review_id"] == first.json()["review_id"]
    assert review_store.count() == 1


def test_extension_upload_rolls_back_dedup_reservation_when_review_creation_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("api.services.local_recognition.get_recognizer", lambda _name: FakeRecognizer())
    paired = pair_code(client, create_pairing_code(client)).json()
    monkeypatch.setattr(review_store, "max_reviews", 0)

    failed = extension_upload(client, paired["token"])
    monkeypatch.setattr(review_store, "max_reviews", 100)
    retry = extension_upload(client, paired["token"])

    assert failed.status_code == 409
    assert failed.json()["detail"]["code"] == "review_store_full"
    assert retry.status_code == 202
    assert retry.json()["deduplicated"] is False


def test_extension_upload_rate_limit_counts_duplicates(client: TestClient) -> None:
    paired = pair_code(client, create_pairing_code(client)).json()

    responses = [extension_upload(client, paired["token"]) for _ in range(4)]

    assert [response.status_code for response in responses] == [202, 200, 200, 429]
    assert responses[-1].json()["detail"]["code"] == "extension_rate_limited"
    assert responses[-1].json()["detail"]["retry_after_seconds"] >= 1


def test_extension_upload_rejects_missing_and_wrong_token(client: TestClient) -> None:
    missing = client.post(
        "/api/v1/local-recognition/extension-reviews",
        files={"file": ("sample.png", png_bytes(), "image/png")},
    )
    wrong = extension_upload(client, "wrong-token")

    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "extension_token_required"
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "extension_token_invalid"


def test_extension_upload_rejects_source_url_and_title_privacy_limits(client: TestClient) -> None:
    paired = pair_code(client, create_pairing_code(client)).json()

    invalid_url = extension_upload(client, paired["token"], data={"source_url": "file:///secret"})
    long_title = extension_upload(client, paired["token"], data={"source_tab_title": "x" * 201})

    assert invalid_url.status_code == 400
    assert invalid_url.json()["detail"]["code"] == "source_url_invalid"
    assert long_title.status_code == 400
    assert long_title.json()["detail"]["code"] == "source_title_too_long"


def test_capture_reservation_is_atomic_and_can_be_rolled_back() -> None:
    store = LocalExtensionPairingStore()
    key = {"pairing_id": "pair_1", "capture_sha256": "a" * 64}

    def reserve() -> str:
        try:
            result = store.reserve_capture(**key)
            return "reserved" if result.reserved else "duplicate"
        except CaptureAlreadyReserved:
            return "pending"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(lambda _: reserve(), range(2)))

    assert results == ["pending", "reserved"]
    store.bind_capture(**key, review_id="review_1")
    assert store.reserve_capture(**key).review_id == "review_1"
    store.rollback_capture(**key)
    assert store.reserve_capture(**key).reserved is True


def create_pairing_code(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/local-recognition/pairing-codes", headers=MANAGEMENT_HEADERS)
    assert response.status_code == 201
    return response.json()


def pair_payload(created: dict[str, str]) -> dict[str, str]:
    return {
        "pairing_code_id": created["pairing_code_id"],
        "pairing_code": created["pairing_code"],
        "client_name": "Test Extension",
        "extension_version": "0.1.0",
    }


def pair_code(client: TestClient, created: dict[str, str]):
    return client.post("/api/v1/local-recognition/pair", json=pair_payload(created))


def extension_upload(client: TestClient, token: str, data: dict[str, str] | None = None):
    return client.post(
        "/api/v1/local-recognition/extension-reviews",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("sample.png", png_bytes(), "image/png")},
        data=data or {},
    )


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
