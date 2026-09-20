from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.services.current_order_book import (
    STALE_AFTER_SECONDS,
    interpret_current_order_book_capture,
)
from api.services.manual_order_book_import import import_manual_order_book


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "manual-orderbook"
    / "confirmed-orderbook-capture.json"
)


def _insert_item(database_url: str, external_key: str = "id50280_f_14a_iriaf_usa") -> int:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            return int(
                connection.execute(
                    text(
                        """
                        INSERT INTO items (external_key, name, category, is_active)
                        VALUES (:external_key, 'Approved fixture item', 'vehicle', true)
                        RETURNING id
                        """
                    ),
                    {"external_key": external_key},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _import_fixture(database_url: str) -> None:
    async def operation() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                await import_manual_order_book(
                    session=session,
                    content=FIXTURE.read_bytes(),
                    source_filename="approved-fixture.json",
                )
        finally:
            await engine.dispose()

    asyncio.run(operation())


def _counts(database_url: str) -> tuple[int, int, int]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return tuple(
                int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
                for table in ("items", "manual_order_book_imports", "manual_order_book_levels")
            )
    finally:
        engine.dispose()


def test_current_order_book_freshness_uses_seven_day_window() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    captured_at = datetime(2026, 9, 1, tzinfo=UTC)

    within_window = interpret_current_order_book_capture(
        capture_payload=payload,
        captured_at=captured_at,
        now=captured_at + timedelta(days=7),
    )
    outside_window = interpret_current_order_book_capture(
        capture_payload=payload,
        captured_at=captured_at,
        now=captured_at + timedelta(days=7, seconds=1),
    )

    assert STALE_AFTER_SECONDS == 7 * 24 * 60 * 60
    assert within_window.freshness == "fresh"
    assert outside_window.freshness == "stale"


def test_current_order_book_returns_approved_interpreted_levels_and_provenance(
    client: TestClient, migrated_database: str
) -> None:
    item_id = _insert_item(migrated_database)
    _import_fixture(migrated_database)

    response = client.get(f"/api/v1/items/{item_id}/order-book")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "web_current_order_book_v1"
    assert payload["item"] == {
        "id": item_id,
        "external_key": "id50280_f_14a_iriaf_usa",
        "name": "Approved fixture item",
    }
    assert payload["best_buy"]["price_raw"] == 1_710_300
    assert payload["best_buy"]["canonical_display_text"] == "171.03"
    assert payload["best_sell"]["price_raw"] == 2_180_000
    assert payload["best_sell"]["canonical_display_text"] == "218.00"
    assert payload["spread_display_text"] == "46.97"
    assert len(payload["buy_levels"]) == 41
    assert len(payload["sell_levels"]) == 70
    assert payload["contract"]["contract_id"] == "gaijin_market_1067_current_book_gjn_v1"
    assert payload["contract"]["contract_version"] == 1
    assert payload["contract"]["currency_code"] == "GJN"
    assert payload["provenance"]["source_type"] == "manual_response_json"
    assert payload["provenance"]["review_status"] == "confirmed_by_user"
    assert payload["provenance"]["request_action"] == "UNKNOWN"
    assert payload["provenance"]["raw_response_hash_verifiable"] is False


def test_current_order_book_missing_item_and_no_capture_are_distinct(
    client: TestClient, migrated_database: str
) -> None:
    missing = client.get("/api/v1/items/999999/order-book")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "item_not_found"

    item_id = _insert_item(migrated_database, "item-without-capture")
    empty = client.get(f"/api/v1/items/{item_id}/order-book")
    assert empty.status_code == 404
    assert empty.json()["detail"]["code"] == "order_book_not_found"


def test_current_order_book_contract_failure_is_closed_and_does_not_write(
    client: TestClient, migrated_database: str
) -> None:
    item_id = _insert_item(migrated_database)
    _import_fixture(migrated_database)
    engine = create_engine(migrated_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE manual_order_book_imports
                    SET capture_payload = jsonb_set(
                        capture_payload,
                        '{request,path}',
                        '"/unsupported"'::jsonb
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    before = _counts(migrated_database)
    response = client.get(f"/api/v1/items/{item_id}/order-book")
    after = _counts(migrated_database)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "price_contract_not_applicable"
    assert after == before


def test_current_order_book_get_is_read_only(
    client: TestClient, migrated_database: str
) -> None:
    item_id = _insert_item(migrated_database)
    _import_fixture(migrated_database)
    before = _counts(migrated_database)

    for _ in range(3):
        assert client.get(f"/api/v1/items/{item_id}/order-book").status_code == 200

    assert _counts(migrated_database) == before


def test_item_catalog_includes_approved_current_order_book_summary(
    client: TestClient, migrated_database: str
) -> None:
    item_id = _insert_item(migrated_database)
    _import_fixture(migrated_database)

    response = client.get("/api/v1/items?search=approved")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == item_id
    assert item["current_order_book_status"] == "available"
    summary = item["current_order_book"]
    assert summary["schema_version"] == "web_catalog_order_book_v1"
    assert summary["best_buy"] == {
        "price_raw": 1_710_300,
        "canonical_display_text": "171.03",
        "quantity": 1,
    }
    assert summary["best_sell"] == {
        "price_raw": 2_180_000,
        "canonical_display_text": "218.00",
        "quantity": 1,
    }
    assert summary["spread_display_text"] == "46.97"
    assert summary["currency_code"] == "GJN"
    assert summary["contract_id"] == "gaijin_market_1067_current_book_gjn_v1"
    assert summary["contract_version"] == 1
    assert summary["source_type"] == "manual_response_json"
    assert summary["review_status"] == "confirmed_by_user"
    assert summary["request_action"] == "UNKNOWN"


def test_item_catalog_marks_latest_contract_failure_without_writing(
    client: TestClient, migrated_database: str
) -> None:
    _insert_item(migrated_database)
    _import_fixture(migrated_database)
    engine = create_engine(migrated_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE manual_order_book_imports
                    SET capture_payload = jsonb_set(
                        capture_payload,
                        '{request,path}',
                        '"/unsupported"'::jsonb
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    before = _counts(migrated_database)
    response = client.get("/api/v1/items")
    after = _counts(migrated_database)

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["current_order_book_status"] == "contract_error"
    assert item["current_order_book"] is None
    assert after == before
