from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from api.schemas.historical_trades import HistoricalTradeImportRequest
from api.services.historical_trades import (
    PRICE_SEMANTICS,
    VOLUME_SEMANTICS,
    _overlap_consistency,
    _sanitize_market_source_url,
)


MANAGEMENT_HEADERS = {"Origin": "http://localhost:3000"}


def _payload(item_key: str = "History Test Item") -> dict[str, object]:
    return {
        "schema_version": "gaijin_trade_history_v1",
        "item_key": item_key,
        "source_url": f"https://trade.gaijin.net/market/1067/{quote(item_key)}?token=redacted#ignored",
        "captured_at": "2026-07-11T00:00:00Z",
        "series": {
            "1h": [
                [1783728000, 1000000, 1],
                [1783731600, 1200000, 1],
            ],
            "1d": [[1783728000, 1100000, 2]],
        },
    }


def _pair(client: TestClient) -> str:
    created = client.post(
        "/api/v1/local-recognition/pairing-codes",
        headers=MANAGEMENT_HEADERS,
        json={},
    ).json()
    paired = client.post(
        "/api/v1/local-recognition/pair",
        json={
            "pairing_code_id": created["pairing_code_id"],
            "pairing_code": created["pairing_code"],
            "client_name": "history-test",
            "extension_version": "0.1.0",
        },
    )
    assert paired.status_code == 200
    return paired.json()["token"]


def _insert_item(database_url: str, external_key: str) -> int:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            return connection.execute(
                text(
                    """
                    INSERT INTO items (external_key, name, category)
                    VALUES (:key, :key, 'vehicle')
                    RETURNING id
                    """
                ),
                {"key": external_key},
            ).scalar_one()
    finally:
        engine.dispose()


def test_history_contract_accepts_wire_tuples_and_confirms_overlap() -> None:
    request = HistoricalTradeImportRequest.model_validate(_payload())

    assert request.series.one_hour[0].price_raw == 1000000
    assert _overlap_consistency(request) == (1, 0)


def test_overlap_consistency_skips_a_partial_leading_hourly_day() -> None:
    payload = _payload()
    payload["series"] = {
        "1h": [
            [1783645200, 900000, 1],
            [1783728000, 1000000, 1],
            [1783731600, 1200000, 1],
        ],
        "1d": [
            [1783641600, 950000, 2],
            [1783728000, 1100000, 2],
        ],
    }
    request = HistoricalTradeImportRequest.model_validate(payload)

    assert _overlap_consistency(request) == (1, 0)


def test_history_contract_rejects_unaligned_or_duplicate_points() -> None:
    unaligned = _payload()
    unaligned["series"]["1h"][0][0] += 1
    with pytest.raises(ValidationError, match="timestamp_not_aligned"):
        HistoricalTradeImportRequest.model_validate(unaligned)

    duplicated = _payload()
    duplicated["series"]["1d"].append(list(duplicated["series"]["1d"][0]))
    with pytest.raises(ValidationError, match="duplicate_1d_timestamp"):
        HistoricalTradeImportRequest.model_validate(duplicated)


def test_source_url_is_reduced_to_supported_market_path() -> None:
    safe, segment = _sanitize_market_source_url(
        "https://trade.gaijin.net/market/1067/History%20Test%20Item?token=hidden#fragment"
    )

    assert safe == "https://trade.gaijin.net/market/1067/History%20Test%20Item"
    assert segment == "History Test Item"


def test_extension_history_import_is_idempotent_and_queryable(
    client: TestClient,
    migrated_database: str,
) -> None:
    item_id = _insert_item(migrated_database, "History Test Item")
    token = _pair(client)

    first = client.post(
        "/api/v1/local-recognition/extension-history-imports",
        headers={"Authorization": f"Bearer {token}"},
        json=_payload(),
    )
    second = client.post(
        "/api/v1/local-recognition/extension-history-imports",
        headers={"Authorization": f"Bearer {token}"},
        json=_payload(),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    body = first.json()
    assert body["item_id"] == item_id
    assert body["inserted_count"] == 3
    assert body["updated_count"] == 0
    assert body["overlap_day_count"] == 1
    assert body["overlap_mismatch_count"] == 0
    assert body["deduplicated"] is False
    second_body = second.json()
    assert second_body["deduplicated"] is True
    assert second_body["import_id"] == body["import_id"]
    assert second_body["inserted_count"] == 0
    assert second_body["updated_count"] == 0
    assert second_body["unchanged_count"] == 0

    daily = client.get(f"/api/v1/items/{item_id}/historical-trades?granularity=1d")
    assert daily.status_code == 200
    result = daily.json()
    assert result["total"] == 1
    assert result["buckets"][0]["reported_vwap_price"] == "110.0000"
    assert result["buckets"][0]["reported_trade_volume"] == 2
    assert result["buckets"][0]["price_semantics"] == PRICE_SEMANTICS
    assert result["buckets"][0]["volume_semantics"] == VOLUME_SEMANTICS


def test_history_import_rejects_item_page_mismatch(
    client: TestClient,
    migrated_database: str,
) -> None:
    _insert_item(migrated_database, "History Test Item")
    token = _pair(client)
    payload = _payload()
    payload["item_key"] = "Different Item"

    response = client.post(
        "/api/v1/local-recognition/extension-history-imports",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "historical_trade_item_mismatch"
