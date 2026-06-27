from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from api.main import app
from api.services import csv_import
from api.services.csv_import import CSV_UPLOAD_SOURCE_TYPE, advisory_lock_key_for_import

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "synthetic"
HEADER = (
    "item_key,item_name,category,observed_at,best_ask,best_bid,"
    "ask_count,bid_count,estimated_volume\n"
)

def _post_csv(client: TestClient, content: bytes | str, filename: str = "synthetic.csv"):
    data = content.encode("utf-8") if isinstance(content, str) else content
    return client.post(
        "/api/v1/imports/csv",
        files={"file": (filename, data, "text/csv")},
    )


def _valid_row(**overrides: str) -> str:
    row = {
        "item_key": "synthetic-item-alpha",
        "item_name": "Synthetic Alpha",
        "category": "vehicle",
        "observed_at": "2026-06-27T00:00:00Z",
        "best_ask": "12.340000",
        "best_bid": "11.100000",
        "ask_count": "3",
        "bid_count": "2",
        "estimated_volume": "44.500000",
    }
    row.update(overrides)
    return ",".join(row[field] for field in HEADER.strip().split(",")) + "\n"


def _snapshot_count(database_url: str) -> int:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT count(*) FROM market_snapshots")).scalar_one()
    finally:
        engine.dispose()


def _scalar(database_url: str, statement: str, params: dict[str, object] | None = None) -> object:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(statement), params or {}).scalar_one()
    finally:
        engine.dispose()


def _concurrent_posts(payloads: list[tuple[str, bytes | str]]) -> list[tuple[int, dict[str, object]]]:
    barrier = Barrier(len(payloads))

    def post(payload: tuple[str, bytes | str]) -> tuple[int, dict[str, object]]:
        filename, content = payload
        with TestClient(app) as thread_client:
            barrier.wait(timeout=10)
            response = _post_csv(thread_client, content, filename=filename)
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
        futures = [executor.submit(post, payload) for payload in payloads]
        return [future.result(timeout=30) for future in futures]


def _non_terminal_job_count(database_url: str) -> int:
    return int(
        _scalar(
            database_url,
            "SELECT count(*) FROM import_jobs WHERE status IN ('pending', 'processing')",
        )
    )


def test_advisory_lock_key_is_stable_and_distinguishes_common_checksums() -> None:
    checksum_a = "a" * 64
    checksum_b = "b" * 64

    first = advisory_lock_key_for_import(CSV_UPLOAD_SOURCE_TYPE, checksum_a)
    second = advisory_lock_key_for_import(CSV_UPLOAD_SOURCE_TYPE, checksum_a)
    different_checksum = advisory_lock_key_for_import(CSV_UPLOAD_SOURCE_TYPE, checksum_b)
    different_source = advisory_lock_key_for_import("other_source", checksum_a)

    assert first == second
    assert first != different_checksum
    assert first != different_source
    assert -(2**63) <= first <= 2**63 - 1


def test_valid_csv_imports_items_snapshots_and_job(client: TestClient, migrated_database: str) -> None:
    content = (FIXTURE_ROOT / "valid_market_data.csv").read_bytes()

    response = _post_csv(client, content)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["row_count"] == 2
    assert body["valid_row_count"] == 2
    assert body["invalid_row_count"] == 0
    assert body["checksum"]
    assert _snapshot_count(migrated_database) == 2
    assert _non_terminal_job_count(migrated_database) == 0

    detail = client.get(f"/api/v1/imports/{body['job_id']}").json()
    assert detail["source_type"] == "csv_upload"
    assert detail["error_report"] == {
        "errors": [],
        "warnings": [],
        "duplicate_of_job_id": None,
    }


def test_utf8_bom_csv_imports_successfully(client: TestClient) -> None:
    response = _post_csv(client, b"\xef\xbb\xbf" + (HEADER + _valid_row()).encode("utf-8"))

    assert response.status_code == 201
    assert response.json()["status"] == "completed"


@pytest.mark.parametrize(
    ("content", "error_code"),
    [
        (b"", "empty_file"),
        (HEADER, "empty_file"),
        (b"\xff\xfe\x00\x00", "decode_error"),
        ("item_key,item_name\nsynthetic,Synthetic\n", "missing_header"),
    ],
)
def test_unrecoverable_csv_contract_errors_create_failed_job(
    client: TestClient, content: bytes | str, error_code: str
) -> None:
    response = _post_csv(client, content)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    detail = client.get(f"/api/v1/imports/{body['job_id']}").json()
    assert detail["error_report"]["errors"][0]["error_code"] == error_code


@pytest.mark.parametrize(
    ("overrides", "field", "error_code"),
    [
        ({"best_ask": "not-a-decimal"}, "best_ask", "invalid_decimal"),
        ({"best_ask": "0"}, "best_ask", "must_be_positive"),
        ({"best_bid": "-1"}, "best_bid", "must_be_non_negative"),
        ({"observed_at": "2026-06-27T00:00:00"}, "observed_at", "timezone_required"),
        ({"ask_count": "-1"}, "ask_count", "must_be_non_negative"),
        ({"bid_count": "-1"}, "bid_count", "must_be_non_negative"),
    ],
)
def test_row_level_field_errors_continue_import(
    client: TestClient, overrides: dict[str, str], field: str, error_code: str
) -> None:
    content = HEADER + _valid_row(item_key="valid-after-error") + _valid_row(**overrides)

    response = _post_csv(client, content)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["row_count"] == 2
    assert body["valid_row_count"] == 1
    assert body["invalid_row_count"] == 1
    detail = client.get(f"/api/v1/imports/{body['job_id']}").json()
    assert detail["error_report"]["errors"][0]["field"] == field
    assert detail["error_report"]["errors"][0]["error_code"] == error_code


def test_estimated_volume_empty_imports_as_null(client: TestClient, migrated_database: str) -> None:
    response = _post_csv(client, HEADER + _valid_row(estimated_volume=""))

    assert response.status_code == 201
    assert response.json()["status"] == "completed"

    engine = create_engine(migrated_database)
    try:
        with engine.connect() as conn:
            value = conn.execute(text("SELECT estimated_volume FROM market_snapshots")).scalar_one()
    finally:
        engine.dispose()
    assert value is None


def test_non_utc_timezone_is_stored_as_utc(client: TestClient, migrated_database: str) -> None:
    response = _post_csv(client, HEADER + _valid_row(observed_at="2026-06-27T08:30:00+08:00"))

    assert response.status_code == 201
    assert response.json()["status"] == "completed"

    engine = create_engine(migrated_database)
    try:
        with engine.connect() as conn:
            observed_at = conn.execute(
                text("SELECT observed_at FROM market_snapshots")
            ).scalar_one()
    finally:
        engine.dispose()
    assert observed_at.isoformat() == "2026-06-27T00:30:00+00:00"


def test_duplicate_file_checksum_creates_duplicate_job_without_new_snapshots(
    client: TestClient, migrated_database: str
) -> None:
    content = HEADER + _valid_row()
    first = _post_csv(client, content).json()
    second_response = _post_csv(client, content)

    assert second_response.status_code == 201
    second = second_response.json()
    assert second["status"] == "duplicate"
    assert second["duplicate_of_job_id"] == first["job_id"]
    assert _snapshot_count(migrated_database) == 1
    assert _non_terminal_job_count(migrated_database) == 0


def test_concurrent_same_checksum_creates_one_completed_and_one_duplicate(
    migrated_database: str,
) -> None:
    content = HEADER + _valid_row(item_key="synthetic-concurrent-same")

    responses = _concurrent_posts(
        [
            ("same-a.csv", content),
            ("same-b.csv", content),
        ]
    )

    assert [status_code for status_code, _body in responses] == [201, 201]
    bodies = [body for _status_code, body in responses]
    completed = [body for body in bodies if body["status"] == "completed"]
    duplicate = [body for body in bodies if body["status"] == "duplicate"]
    failed = [body for body in bodies if body["status"] == "failed"]

    assert len(completed) == 1
    assert len(duplicate) == 1
    assert failed == []
    assert duplicate[0]["duplicate_of_job_id"] == completed[0]["job_id"]
    assert _snapshot_count(migrated_database) == 1
    assert _non_terminal_job_count(migrated_database) == 0


def test_concurrent_different_files_reuse_same_new_item_key(
    migrated_database: str,
) -> None:
    first = HEADER + _valid_row(
        item_key="synthetic-concurrent-item",
        observed_at="2026-06-27T00:00:00Z",
    )
    second = HEADER + _valid_row(
        item_key="synthetic-concurrent-item",
        observed_at="2026-06-27T01:00:00Z",
        best_ask="13.000000",
    )

    responses = _concurrent_posts(
        [
            ("item-a.csv", first),
            ("item-b.csv", second),
        ]
    )

    assert [status_code for status_code, _body in responses] == [201, 201]
    assert {body["status"] for _status_code, body in responses} == {"completed"}
    assert (
        _scalar(
            migrated_database,
            "SELECT count(*) FROM items WHERE external_key = :external_key",
            {"external_key": "synthetic-concurrent-item"},
        )
        == 1
    )
    assert _snapshot_count(migrated_database) == 2
    assert _non_terminal_job_count(migrated_database) == 0


def test_duplicate_item_observed_at_within_file_is_idempotent(
    client: TestClient, migrated_database: str
) -> None:
    response = _post_csv(client, HEADER + _valid_row() + _valid_row(best_ask="13.000000"))

    assert response.status_code == 201
    body = response.json()
    assert body["valid_row_count"] == 2
    assert _snapshot_count(migrated_database) == 1
    detail = client.get(f"/api/v1/imports/{body['job_id']}").json()
    assert detail["error_report"]["warnings"][0]["error_code"] == "duplicate_snapshot_in_file"


def test_duplicate_snapshot_across_different_files_is_idempotent(
    client: TestClient, migrated_database: str
) -> None:
    first = HEADER + _valid_row()
    second = HEADER + _valid_row(best_ask="13.000000") + "\n"

    assert _post_csv(client, first, filename="first.csv").json()["status"] == "completed"
    response = _post_csv(client, second, filename="second.csv")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert _snapshot_count(migrated_database) == 1
    detail = client.get(f"/api/v1/imports/{body['job_id']}").json()
    assert detail["error_report"]["warnings"][0]["error_code"] == "duplicate_snapshot_existing"


def test_existing_item_key_reuses_item_without_overwriting_metadata(
    client: TestClient, migrated_database: str
) -> None:
    assert _post_csv(client, HEADER + _valid_row()).json()["status"] == "completed"
    response = _post_csv(
        client,
        HEADER
        + _valid_row(
            item_name="Renamed Synthetic",
            category="different",
            observed_at="2026-06-28T00:00:00Z",
        )
        + "\n",
        filename="renamed.csv",
    )

    assert response.status_code == 201
    body = response.json()
    detail = client.get(f"/api/v1/imports/{body['job_id']}").json()
    assert detail["error_report"]["warnings"][0]["error_code"] == "item_metadata_mismatch"

    engine = create_engine(migrated_database)
    try:
        with engine.connect() as conn:
            item = conn.execute(
                text("SELECT name, category FROM items WHERE external_key = 'synthetic-item-alpha'")
            ).one()
    finally:
        engine.dispose()
    assert item == ("Synthetic Alpha", "vehicle")


def test_missing_import_job_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/imports/999999")

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "import_job_not_found"


def test_file_larger_than_limit_is_rejected(client: TestClient) -> None:
    response = _post_csv(client, b"x" * (10 * 1024 * 1024 + 1))

    assert response.status_code == 413
    assert response.json()["detail"]["error_code"] == "file_too_large"


def test_extension_and_mime_are_checked(client: TestClient) -> None:
    bad_extension = client.post(
        "/api/v1/imports/csv",
        files={"file": ("synthetic.txt", b"not,csv\n", "text/csv")},
    )
    bad_mime = client.post(
        "/api/v1/imports/csv",
        files={"file": ("synthetic.csv", b"not,csv\n", "application/json")},
    )

    assert bad_extension.status_code == 400
    assert bad_mime.status_code == 415


def test_database_error_marks_job_failed(
    client: TestClient, migrated_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_insert_snapshot(self: object, **values: object) -> bool:
        raise SQLAlchemyError("synthetic database failure")

    monkeypatch.setattr(csv_import.CsvImportService, "_insert_snapshot", broken_insert_snapshot)

    response = _post_csv(client, HEADER + _valid_row())

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    detail = client.get(f"/api/v1/imports/{body['job_id']}").json()
    assert detail["error_report"]["errors"][0]["error_code"] == "database_error"
    assert _snapshot_count(migrated_database) == 0
    assert (
        _scalar(
            migrated_database,
            "SELECT count(*) FROM items WHERE external_key = :external_key",
            {"external_key": "synthetic-item-alpha"},
        )
        == 0
    )
    assert _non_terminal_job_count(migrated_database) == 0


def test_partial_valid_and_invalid_rows_are_reported(client: TestClient) -> None:
    content = HEADER + _valid_row(item_key="valid") + _valid_row(best_bid="-0.01")

    response = _post_csv(client, content)

    assert response.status_code == 201
    body = response.json()
    assert body["valid_row_count"] == 1
    assert body["invalid_row_count"] == 1
    detail = client.get(f"/api/v1/imports/{body['job_id']}").json()
    assert detail["error_report"]["errors"][0]["row_number"] == 3
