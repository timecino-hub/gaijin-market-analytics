from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text


def _insert_item(
    database_url: str,
    *,
    external_key: str,
    name: str,
    category: str = "vehicle",
    rarity: str | None = None,
    is_active: bool = True,
) -> int:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            return int(
                conn.execute(
                    text(
                        """
                        INSERT INTO items (external_key, name, category, rarity, is_active)
                        VALUES (:external_key, :name, :category, :rarity, :is_active)
                        RETURNING id
                        """
                    ),
                    {
                        "external_key": external_key,
                        "name": name,
                        "category": category,
                        "rarity": rarity,
                        "is_active": is_active,
                    },
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _insert_import_job(database_url: str, checksum: str = "c" * 64) -> int:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            return int(
                conn.execute(
                    text(
                        """
                        INSERT INTO import_jobs (source_type, filename, checksum, status)
                        VALUES ('synthetic_fixture', 'synthetic.csv', :checksum, 'completed')
                        RETURNING id
                        """
                    ),
                    {"checksum": checksum},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _insert_snapshot(
    database_url: str,
    *,
    item_id: int,
    observed_at: str,
    best_ask: str,
    best_bid: str | None = "1.000000",
    ask_count: int | None = 1,
    bid_count: int | None = 1,
    estimated_volume: str | None = "10.000000",
    source_import_job_id: int | None = None,
) -> int:
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            return int(
                conn.execute(
                    text(
                        """
                        INSERT INTO market_snapshots (
                            item_id,
                            observed_at,
                            best_ask,
                            best_bid,
                            ask_count,
                            bid_count,
                            estimated_volume,
                            source_import_job_id
                        )
                        VALUES (
                            :item_id,
                            :observed_at,
                            :best_ask,
                            :best_bid,
                            :ask_count,
                            :bid_count,
                            :estimated_volume,
                            :source_import_job_id
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "item_id": item_id,
                        "observed_at": observed_at,
                        "best_ask": Decimal(best_ask),
                        "best_bid": Decimal(best_bid) if best_bid is not None else None,
                        "ask_count": ask_count,
                        "bid_count": bid_count,
                        "estimated_volume": (
                            Decimal(estimated_volume) if estimated_volume is not None else None
                        ),
                        "source_import_job_id": source_import_job_id,
                    },
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_empty_database_item_list(client: TestClient) -> None:
    response = client.get("/api/v1/items")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "page": 1,
        "page_size": 50,
        "total": 0,
        "total_pages": 0,
    }


def test_item_list_paginates_multiple_items(client: TestClient, migrated_database: str) -> None:
    for index in range(3):
        _insert_item(
            migrated_database,
            external_key=f"synthetic-page-{index}",
            name=f"Synthetic Page {index}",
        )

    response = client.get("/api/v1/items?page=2&page_size=2")

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert body["total"] == 3
    assert body["total_pages"] == 2
    assert [item["external_key"] for item in body["items"]] == ["synthetic-page-2"]


def test_page_and_page_size_lower_boundaries_are_rejected(client: TestClient) -> None:
    page_response = client.get("/api/v1/items?page=0")
    size_response = client.get("/api/v1/items?page_size=0")

    assert page_response.status_code == 400
    assert page_response.json()["detail"]["code"] == "invalid_pagination"
    assert size_response.status_code == 400
    assert size_response.json()["detail"]["code"] == "invalid_pagination"


def test_page_size_over_100_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/items?page_size=101")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_pagination"


def test_item_list_searches_name(client: TestClient, migrated_database: str) -> None:
    _insert_item(migrated_database, external_key="synthetic-alpha", name="Synthetic Alpha")
    _insert_item(migrated_database, external_key="synthetic-beta", name="Synthetic Beta")

    response = client.get("/api/v1/items?search=alpha")

    assert response.status_code == 200
    assert [item["external_key"] for item in response.json()["items"]] == ["synthetic-alpha"]


def test_item_list_searches_external_key(client: TestClient, migrated_database: str) -> None:
    _insert_item(migrated_database, external_key="synthetic-key-target", name="Synthetic A")
    _insert_item(migrated_database, external_key="synthetic-other", name="Synthetic B")

    response = client.get("/api/v1/items?search=KEY-TARGET")

    assert response.status_code == 200
    assert [item["external_key"] for item in response.json()["items"]] == ["synthetic-key-target"]


def test_item_list_filters_category(client: TestClient, migrated_database: str) -> None:
    _insert_item(migrated_database, external_key="synthetic-vehicle", name="A", category="vehicle")
    _insert_item(migrated_database, external_key="synthetic-coupon", name="B", category="coupon")

    response = client.get("/api/v1/items?category=coupon")

    assert response.status_code == 200
    assert [item["external_key"] for item in response.json()["items"]] == ["synthetic-coupon"]


def test_item_list_filters_rarity(client: TestClient, migrated_database: str) -> None:
    _insert_item(migrated_database, external_key="synthetic-rare", name="A", rarity="rare")
    _insert_item(migrated_database, external_key="synthetic-common", name="B", rarity="common")

    response = client.get("/api/v1/items?rarity=rare")

    assert response.status_code == 200
    assert [item["external_key"] for item in response.json()["items"]] == ["synthetic-rare"]


def test_item_list_filters_is_active(client: TestClient, migrated_database: str) -> None:
    _insert_item(migrated_database, external_key="synthetic-active", name="A", is_active=True)
    _insert_item(migrated_database, external_key="synthetic-inactive", name="B", is_active=False)

    response = client.get("/api/v1/items?is_active=false")

    assert response.status_code == 200
    assert [item["external_key"] for item in response.json()["items"]] == ["synthetic-inactive"]


def test_item_list_sorts_name_ascending_and_descending(
    client: TestClient, migrated_database: str
) -> None:
    _insert_item(migrated_database, external_key="synthetic-b", name="Beta")
    _insert_item(migrated_database, external_key="synthetic-a", name="Alpha")

    ascending = client.get("/api/v1/items?sort=name&order=asc").json()
    descending = client.get("/api/v1/items?sort=name&order=desc").json()

    assert [item["name"] for item in ascending["items"]] == ["Alpha", "Beta"]
    assert [item["name"] for item in descending["items"]] == ["Beta", "Alpha"]


def test_item_list_uses_stable_id_secondary_sort(client: TestClient, migrated_database: str) -> None:
    first_id = _insert_item(migrated_database, external_key="synthetic-same-1", name="Same")
    second_id = _insert_item(migrated_database, external_key="synthetic-same-2", name="Same")

    response = client.get("/api/v1/items?sort=name&order=desc")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [first_id, second_id]


def test_item_list_rejects_invalid_sort(client: TestClient) -> None:
    response = client.get("/api/v1/items?sort=price")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_sort"


def test_item_list_returns_only_latest_snapshot(client: TestClient, migrated_database: str) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-latest", name="Latest")
    _insert_snapshot(
        migrated_database,
        item_id=item_id,
        observed_at="2026-06-27T00:00:00Z",
        best_ask="1.000000",
    )
    _insert_snapshot(
        migrated_database,
        item_id=item_id,
        observed_at="2026-06-27T01:00:00Z",
        best_ask="2.000000",
    )

    response = client.get("/api/v1/items")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["latest_snapshot"]["observed_at"] == "2026-06-27T01:00:00Z"
    assert item["latest_snapshot"]["best_ask"] == "2.000000"


def test_item_without_snapshots_has_null_latest_snapshot(
    client: TestClient, migrated_database: str
) -> None:
    _insert_item(migrated_database, external_key="synthetic-no-snapshots", name="No Snapshots")

    response = client.get("/api/v1/items")

    assert response.status_code == 200
    assert response.json()["items"][0]["latest_snapshot"] is None


def test_item_detail_success(client: TestClient, migrated_database: str) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-detail", name="Detail")
    _insert_snapshot(
        migrated_database,
        item_id=item_id,
        observed_at="2026-06-27T00:00:00Z",
        best_ask="3.000000",
    )

    response = client.get(f"/api/v1/items/{item_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == item_id
    assert body["external_key"] == "synthetic-detail"
    assert body["latest_snapshot"]["best_ask"] == "3.000000"


def test_missing_item_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/items/999999")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "item_not_found",
            "message": "The requested item was not found.",
        }
    }


def test_item_detail_snapshot_statistics_are_correct(
    client: TestClient, migrated_database: str
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-stats", name="Stats")
    _insert_snapshot(
        migrated_database,
        item_id=item_id,
        observed_at="2026-06-27T00:00:00Z",
        best_ask="1.000000",
    )
    _insert_snapshot(
        migrated_database,
        item_id=item_id,
        observed_at="2026-06-27T02:00:00Z",
        best_ask="2.000000",
    )

    response = client.get(f"/api/v1/items/{item_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_count"] == 2
    assert body["first_snapshot_at"] == "2026-06-27T00:00:00Z"
    assert body["last_snapshot_at"] == "2026-06-27T02:00:00Z"


def test_snapshots_sort_ascending_and_descending(client: TestClient, migrated_database: str) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-history", name="History")
    _insert_snapshot(
        migrated_database,
        item_id=item_id,
        observed_at="2026-06-27T00:00:00Z",
        best_ask="1.000000",
    )
    _insert_snapshot(
        migrated_database,
        item_id=item_id,
        observed_at="2026-06-27T01:00:00Z",
        best_ask="2.000000",
    )

    ascending = client.get(f"/api/v1/items/{item_id}/snapshots?order=asc").json()
    descending = client.get(f"/api/v1/items/{item_id}/snapshots?order=desc").json()

    assert [snapshot["best_ask"] for snapshot in ascending] == ["1.000000", "2.000000"]
    assert [snapshot["best_ask"] for snapshot in descending] == ["2.000000", "1.000000"]


def test_snapshots_filter_from_time(client: TestClient, migrated_database: str) -> None:
    item_id = _item_with_three_snapshots(migrated_database)

    response = client.get(f"/api/v1/items/{item_id}/snapshots?from=2026-06-27T01:00:00Z")

    assert response.status_code == 200
    assert [snapshot["best_ask"] for snapshot in response.json()] == ["2.000000", "3.000000"]


def test_snapshots_filter_to_time(client: TestClient, migrated_database: str) -> None:
    item_id = _item_with_three_snapshots(migrated_database)

    response = client.get(f"/api/v1/items/{item_id}/snapshots?to=2026-06-27T01:00:00Z")

    assert response.status_code == 200
    assert [snapshot["best_ask"] for snapshot in response.json()] == ["1.000000", "2.000000"]


def test_snapshots_filter_from_and_to_time(client: TestClient, migrated_database: str) -> None:
    item_id = _item_with_three_snapshots(migrated_database)

    response = client.get(
        f"/api/v1/items/{item_id}/snapshots"
        "?from=2026-06-27T00:30:00Z&to=2026-06-27T01:30:00Z"
    )

    assert response.status_code == 200
    assert [snapshot["best_ask"] for snapshot in response.json()] == ["2.000000"]


def test_snapshots_reject_time_without_timezone(client: TestClient, migrated_database: str) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-timezone", name="Timezone")

    response = client.get(f"/api/v1/items/{item_id}/snapshots?from=2026-06-27T00:00:00")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_time_range"


def test_snapshots_reject_from_later_than_to(client: TestClient, migrated_database: str) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-range", name="Range")

    response = client.get(
        f"/api/v1/items/{item_id}/snapshots"
        "?from=2026-06-28T00:00:00Z&to=2026-06-27T00:00:00Z"
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_time_range"


def test_snapshots_empty_history_returns_empty_array(
    client: TestClient, migrated_database: str
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-empty-history", name="Empty")

    response = client.get(f"/api/v1/items/{item_id}/snapshots")

    assert response.status_code == 200
    assert response.json() == []


def test_snapshots_limit_maximum_is_validated(client: TestClient, migrated_database: str) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-limit", name="Limit")

    response = client.get(f"/api/v1/items/{item_id}/snapshots?limit=2001")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_pagination"


def test_snapshot_decimal_fields_serialize_as_strings(
    client: TestClient, migrated_database: str
) -> None:
    item_id = _insert_item(migrated_database, external_key="synthetic-decimal", name="Decimal")
    import_job_id = _insert_import_job(migrated_database)
    _insert_snapshot(
        migrated_database,
        item_id=item_id,
        observed_at="2026-06-27T00:00:00Z",
        best_ask="12.340000",
        best_bid="11.110000",
        estimated_volume="44.500000",
        source_import_job_id=import_job_id,
    )

    response = client.get(f"/api/v1/items/{item_id}/snapshots")

    assert response.status_code == 200
    snapshot = response.json()[0]
    assert snapshot["best_ask"] == "12.340000"
    assert snapshot["best_bid"] == "11.110000"
    assert snapshot["estimated_volume"] == "44.500000"
    assert snapshot["source_import_job_id"] == import_job_id


def test_item_list_does_not_issue_n_plus_one_snapshot_queries(
    client: TestClient, migrated_database: str
) -> None:
    for index in range(5):
        item_id = _insert_item(
            migrated_database,
            external_key=f"synthetic-query-count-{index}",
            name=f"Query Count {index}",
        )
        _insert_snapshot(
            migrated_database,
            item_id=item_id,
            observed_at="2026-06-27T00:00:00Z",
            best_ask=f"{index + 1}.000000",
        )

    from api.db.session import engine

    statements: list[str] = []

    def count_selects(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count_selects)
    try:
        response = client.get("/api/v1/items?page_size=5")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_selects)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 5
    assert len(statements) <= 2


def test_missing_item_snapshots_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/items/999999/snapshots")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "item_not_found"


def _item_with_three_snapshots(database_url: str) -> int:
    item_id = _insert_item(database_url, external_key="synthetic-three", name="Three")
    for hour, ask in enumerate(["1.000000", "2.000000", "3.000000"]):
        _insert_snapshot(
            database_url,
            item_id=item_id,
            observed_at=f"2026-06-27T0{hour}:00:00Z",
            best_ask=ask,
        )
    return item_id
