from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from api.screen_recognition.contracts import OcrFieldEvidence, OcrResult
from api.services.local_extension_pairing import pairing_store
from api.services.local_recognition_store import review_store


@pytest.fixture(autouse=True)
def clear_local_stores() -> None:
    review_store.clear()
    pairing_store.clear()


def test_confirmed_review_import_creates_audited_snapshot_idempotently(
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

    confirmed = client.post(
        f"/api/v1/local-recognition/reviews/{review_id}/confirm",
        json={"selected_item_id": item_id, "reviewer_note": "verified locally"},
    )
    imported = client.post(f"/api/v1/local-recognition/reviews/{review_id}/import")
    repeated = client.post(f"/api/v1/local-recognition/reviews/{review_id}/import")

    assert confirmed.status_code == 200
    assert imported.status_code == 200
    assert repeated.status_code == 200

    first_candidate = imported.json()["candidate"]
    repeated_candidate = repeated.json()["candidate"]
    assert first_candidate["imported"] is True
    assert first_candidate["database_written"] is True
    assert first_candidate["market_snapshot_created"] is True
    assert first_candidate["database_item_id"] == item_id
    assert first_candidate["screen_review_import_id"] is not None
    assert first_candidate["market_snapshot_id"] is not None
    assert first_candidate["imported_at"] is not None
    assert (
        repeated_candidate["screen_review_import_id"]
        == first_candidate["screen_review_import_id"]
    )
    assert repeated_candidate["market_snapshot_id"] == first_candidate["market_snapshot_id"]

    engine = create_engine(migrated_database)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        sri.review_id,
                        sri.item_id,
                        sri.market_snapshot_id,
                        sri.total_bid_quantity,
                        sri.total_ask_quantity,
                        sri.reviewer_note,
                        sri.candidate_payload,
                        ms.best_bid,
                        ms.best_ask,
                        ms.bid_count,
                        ms.ask_count,
                        ms.estimated_volume,
                        ms.source_import_job_id
                    FROM screen_review_imports AS sri
                    JOIN market_snapshots AS ms ON ms.id = sri.market_snapshot_id
                    WHERE sri.review_id = :review_id
                    """
                ),
                {"review_id": review_id},
            ).mappings().one()
            import_count = conn.execute(
                text("SELECT count(*) FROM screen_review_imports WHERE review_id = :review_id"),
                {"review_id": review_id},
            ).scalar_one()
            snapshot_count = conn.execute(
                text("SELECT count(*) FROM market_snapshots WHERE item_id = :item_id"),
                {"item_id": item_id},
            ).scalar_one()
    finally:
        engine.dispose()

    assert row["review_id"] == review_id
    assert row["item_id"] == item_id
    assert row["total_bid_quantity"] == 5
    assert row["total_ask_quantity"] == 7
    assert row["reviewer_note"] == "verified locally"
    assert row["candidate_payload"]["imported"] is False
    assert row["candidate_payload"]["database_written"] is False
    assert row["candidate_payload"]["market_snapshot_created"] is False
    assert str(row["best_bid"]) == "12.340000"
    assert str(row["best_ask"]) == "13.000000"
    assert row["bid_count"] is None
    assert row["ask_count"] is None
    assert row["estimated_volume"] is None
    assert row["source_import_job_id"] is None
    assert import_count == 1
    assert snapshot_count == 1


def test_import_requires_confirmed_review(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)

    response = client.post(f"/api/v1/local-recognition/reviews/{review_id}/import")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "review_not_confirmed"


def test_import_does_not_create_unknown_manual_item(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.services.local_recognition.get_recognizer",
        lambda _name: FakeRecognizer(),
    )
    review_id = create_pending_review(client)
    confirmed = client.post(
        f"/api/v1/local-recognition/reviews/{review_id}/confirm",
        json={"item_key": "not-yet-created", "final_item_name": "Manual Item"},
    )

    response = client.post(f"/api/v1/local-recognition/reviews/{review_id}/import")

    assert confirmed.status_code == 200
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "existing_item_required"


def test_import_rejects_existing_snapshot_without_overwriting(
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
    confirmed = client.post(
        f"/api/v1/local-recognition/reviews/{review_id}/confirm",
        json={"selected_item_id": item_id},
    )
    observed_at = confirmed.json()["candidate"]["observed_at"]
    insert_snapshot(migrated_database, item_id=item_id, observed_at=observed_at)

    response = client.post(f"/api/v1/local-recognition/reviews/{review_id}/import")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "snapshot_already_exists"
    detail = client.get(f"/api/v1/local-recognition/reviews/{review_id}").json()
    assert detail["candidate"]["imported"] is False


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

    def recognize(self, invocation: Any) -> OcrResult:
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


def insert_snapshot(database_url: str, *, item_id: int, observed_at: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO market_snapshots (item_id, observed_at, best_bid, best_ask)
                    VALUES (:item_id, :observed_at, 11.000000, 14.000000)
                    """
                ),
                {"item_id": item_id, "observed_at": observed_at},
            )
    finally:
        engine.dispose()
