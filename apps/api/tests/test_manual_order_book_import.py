from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Awaitable
from inspect import signature
from pathlib import Path
from typing import Any, TypeVar

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.importers.manual_order_book_json import (
    compute_normalized_capture_fingerprint,
    parse_manual_order_book_json,
)
from api.services import manual_order_book_import as service
from api import manual_order_book_import_cli as cli
from api.services.manual_order_book_import import (
    MANUAL_RESPONSE_JSON_SOURCE_TYPE,
    ORDER_BOOK_PRICE_SEMANTICS,
    ManualOrderBookImportError,
    import_manual_order_book,
    inspect_manual_order_book_import,
    record_invalid_manual_order_book_attempt,
    _safe_filename,
)


T = TypeVar("T")
FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "manual-orderbook"
    / "confirmed-orderbook-capture.json"
)
API_SOURCE = Path(__file__).resolve().parents[1] / "src"


def run(value: Awaitable[T]) -> T:
    return asyncio.run(value)


def fixture_content() -> bytes:
    return FIXTURE.read_bytes()


def fixture_payload() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def fixture_capture():
    return parse_manual_order_book_json(fixture_content())


def fixture_sha256() -> str:
    return hashlib.sha256(fixture_content()).hexdigest()


def fixture_with_reported_depth(*, buy: int, sell: int) -> bytes:
    payload = fixture_payload()
    payload["order_book"]["reported_depth"] = {"BUY": buy, "SELL": sell}
    payload["source"]["normalized_capture_fingerprint"] = (
        compute_normalized_capture_fingerprint(payload)
    )
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def insert_item(
    database_url: str,
    *,
    external_key: str = "id50280_f_14a_iriaf_usa",
    name: str = "Fixture item",
) -> int:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            return int(
                connection.execute(
                    text(
                        """
                        INSERT INTO items (external_key, name, category, is_active)
                        VALUES (:external_key, :name, 'vehicle', true)
                        RETURNING id
                        """
                    ),
                    {"external_key": external_key, "name": name},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def scalar(database_url: str, statement: str, params: dict[str, Any] | None = None) -> Any:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text(statement), params or {}).scalar_one()
    finally:
        engine.dispose()


def cli_environment(database_url: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(API_SOURCE)
    return environment


def run_cli(
    database_url: str,
    path: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "api.manual_order_book_import_cli",
            str(path),
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=cli_environment(database_url),
    )


async def with_session(database_url: str, callback):
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await callback(session)
    finally:
        await engine.dispose()


async def write_fixture(
    database_url: str,
    *,
    content: bytes | None = None,
    source_filename: str = "confirmed-orderbook-capture.json",
):
    encoded = fixture_content() if content is None else content

    async def operation(session):
        return await import_manual_order_book(
            session=session,
            content=encoded,
            source_filename=source_filename,
        )

    return await with_session(database_url, operation)


def test_migration_exposes_raw_capture_tables_without_price_scale(
    migrated_database: str,
) -> None:
    engine = create_engine(migrated_database)
    try:
        inspector = inspect(engine)
        assert {
            "manual_order_book_imports",
            "manual_order_book_levels",
        }.issubset(inspector.get_table_names())
        columns = {
            column["name"]
            for column in inspector.get_columns("manual_order_book_imports")
        }
    finally:
        engine.dispose()

    assert "price_scale" not in columns
    assert "reported_price" not in columns
    assert {"best_buy_price_raw", "best_sell_price_raw"}.issubset(columns)


def test_public_persistence_entries_bind_capture_payload_and_hash_to_bytes() -> None:
    assert set(signature(import_manual_order_book).parameters) == {
        "session",
        "content",
        "source_filename",
    }
    assert set(signature(inspect_manual_order_book_import).parameters) == {
        "session",
        "content",
    }


def test_dry_run_resolves_exact_existing_item_without_writes(
    migrated_database: str,
) -> None:
    item_id = insert_item(migrated_database)

    async def operation(session):
        return await inspect_manual_order_book_import(
            session=session,
            content=fixture_content(),
        )

    result = run(with_session(migrated_database, operation))

    assert result.database_item_id == item_id
    assert result.decoded_external_key == "id50280_f_14a_iriaf_usa"
    assert result.would_create is True
    assert result.existing_import_id is None
    assert scalar(migrated_database, "SELECT count(*) FROM import_jobs") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 0


def test_write_persists_audited_raw_capture_and_ordered_levels_only(
    migrated_database: str,
) -> None:
    item_id = insert_item(migrated_database)

    result = run(
        write_fixture(
            migrated_database,
            source_filename=r"C:\sensitive\confirmed-orderbook-capture.json",
        )
    )

    assert result.created is True
    assert result.database_item_id == item_id
    engine = create_engine(migrated_database)
    try:
        with engine.connect() as connection:
            imported = connection.execute(
                text("SELECT * FROM manual_order_book_imports")
            ).mappings().one()
            levels = connection.execute(
                text(
                    """
                    SELECT side, level_index, price_raw, quantity
                    FROM manual_order_book_levels
                    ORDER BY CASE side WHEN 'BUY' THEN 0 ELSE 1 END, level_index
                    """
                )
            ).mappings().all()
            job = connection.execute(text("SELECT * FROM import_jobs")).mappings().one()
    finally:
        engine.dispose()

    assert imported["source_type"] == MANUAL_RESPONSE_JSON_SOURCE_TYPE
    assert imported["price_semantics"] == ORDER_BOOK_PRICE_SEMANTICS
    assert imported["source_filename"] == "confirmed-orderbook-capture.json"
    assert imported["source_file_sha256"] == fixture_sha256()
    assert imported["capture_payload"] == fixture_payload()
    assert imported["item_id"] == item_id
    assert imported["capture_id"] == "123e4567-e89b-42d3-a456-426614174000"
    assert imported["captured_at"].isoformat() == "2026-07-13T12:00:00+00:00"
    assert imported["buy_level_count"] == 41
    assert imported["sell_level_count"] == 70
    assert imported["best_buy_price_raw"] == 1_710_300
    assert imported["best_sell_price_raw"] == 2_180_000
    assert len(levels) == 111
    assert levels[0] == {
        "side": "BUY",
        "level_index": 0,
        "price_raw": 1_710_300,
        "quantity": 1,
    }
    assert levels[40]["side"] == "BUY"
    assert levels[41]["side"] == "SELL"
    assert job["status"] == "completed"
    assert job["source_type"] == MANUAL_RESPONSE_JSON_SOURCE_TYPE
    assert job["row_count"] == 111
    assert job["valid_row_count"] == 111
    assert scalar(migrated_database, "SELECT count(*) FROM market_snapshots") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM order_book_observations") == 0


@pytest.mark.parametrize(("buy_depth", "sell_depth"), [(0, 0), (1, 2)])
def test_reported_depth_is_preserved_without_level_count_inference(
    migrated_database: str,
    buy_depth: int,
    sell_depth: int,
) -> None:
    insert_item(migrated_database)
    content = fixture_with_reported_depth(buy=buy_depth, sell=sell_depth)

    capture = parse_manual_order_book_json(content)
    result = run(write_fixture(migrated_database, content=content))

    assert result.created is True
    assert capture.order_book.reported_depth.buy == buy_depth
    assert capture.order_book.reported_depth.sell == sell_depth
    engine = create_engine(migrated_database)
    try:
        with engine.connect() as connection:
            imported = connection.execute(
                text(
                    "SELECT buy_depth, sell_depth, buy_level_count, "
                    "sell_level_count, capture_payload "
                    "FROM manual_order_book_imports"
                )
            ).mappings().one()
            level_count = connection.execute(
                text("SELECT count(*) FROM manual_order_book_levels")
            ).scalar_one()
    finally:
        engine.dispose()

    assert imported["buy_depth"] == buy_depth
    assert imported["sell_depth"] == sell_depth
    assert imported["buy_level_count"] == 41
    assert imported["sell_level_count"] == 70
    assert imported["capture_payload"] == json.loads(content.decode("utf-8"))
    assert level_count == 111


def test_exact_retry_creates_duplicate_job_without_duplicate_capture_or_levels(
    migrated_database: str,
) -> None:
    insert_item(migrated_database)

    first = run(write_fixture(migrated_database))
    second = run(write_fixture(migrated_database, source_filename="retry.json"))

    assert first.created is True
    assert second.created is False
    assert second.manual_order_book_import_id == first.manual_order_book_import_id
    assert second.import_job_id != first.import_job_id
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 1
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_levels") == 111
    assert scalar(migrated_database, "SELECT count(*) FROM import_jobs") == 2
    assert scalar(
        migrated_database,
        "SELECT count(*) FROM import_jobs WHERE status = 'duplicate'",
    ) == 1


def test_reformatted_same_capture_deduplicates_by_normalized_fingerprint(
    migrated_database: str,
) -> None:
    insert_item(migrated_database)
    run(write_fixture(migrated_database))
    reformatted = json.dumps(
        fixture_payload(), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert hashlib.sha256(reformatted).hexdigest() != fixture_sha256()

    duplicate = run(write_fixture(migrated_database, content=reformatted))

    assert duplicate.created is False
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 1


@pytest.mark.parametrize(
    ("external_key", "name"),
    [
        ("different-key", "Fixture item"),
        ("different-key", "id50280_f_14a_iriaf_usa"),
    ],
)
def test_unknown_key_and_name_only_match_are_rejected_without_item_creation(
    migrated_database: str,
    external_key: str,
    name: str,
) -> None:
    insert_item(migrated_database, external_key=external_key, name=name)

    with pytest.raises(ManualOrderBookImportError) as exc_info:
        run(write_fixture(migrated_database))

    assert exc_info.value.code == "existing_item_required"
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_levels") == 0
    assert scalar(
        migrated_database,
        "SELECT count(*) FROM import_jobs WHERE status = 'failed'",
    ) == 1


def test_same_capture_id_with_changed_valid_content_is_a_conflict(
    migrated_database: str,
) -> None:
    insert_item(migrated_database)
    run(write_fixture(migrated_database))
    payload = fixture_payload()
    payload["captured_at"] = "2026-07-13T12:00:01.000Z"
    source = payload["source"]
    assert isinstance(source, dict)
    source["normalized_capture_fingerprint"] = compute_normalized_capture_fingerprint(
        payload
    )
    changed = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )

    with pytest.raises(ManualOrderBookImportError) as exc_info:
        run(write_fixture(migrated_database, content=changed))

    assert exc_info.value.code == "capture_id_conflict"
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 1
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_levels") == 111


def test_level_failure_rolls_back_capture_and_records_failed_audit(
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    insert_item(migrated_database)

    async def fail_levels(*_args, **_kwargs) -> None:
        raise SQLAlchemyError("synthetic level failure")

    monkeypatch.setattr(service, "_persist_levels", fail_levels)

    with pytest.raises(ManualOrderBookImportError) as exc_info:
        run(write_fixture(migrated_database))

    assert exc_info.value.code == "database_error"
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_levels") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM import_jobs") == 1
    assert scalar(
        migrated_database,
        "SELECT count(*) FROM import_jobs WHERE status = 'failed'",
    ) == 1


def test_invalid_attempt_audit_contains_only_stable_error_code(
    migrated_database: str,
) -> None:
    async def operation(session):
        return await record_invalid_manual_order_book_attempt(
            session=session,
            source_filename=r"C:\private\bad.json",
            source_file_sha256="a" * 64,
            error_code="invalid_json",
        )

    job_id = run(with_session(migrated_database, operation))
    engine = create_engine(migrated_database)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT filename, status, error_report FROM import_jobs WHERE id = :id"),
                {"id": job_id},
            ).mappings().one()
    finally:
        engine.dispose()

    assert row["filename"] == "bad.json"
    assert row["status"] == "failed"
    assert row["error_report"] == {
        "errors": [{"field": "file", "error_code": "invalid_json"}],
        "warnings": [],
    }


def test_concurrent_exact_retry_serializes_to_completed_and_duplicate(
    migrated_database: str,
) -> None:
    insert_item(migrated_database)

    async def concurrent():
        return await asyncio.gather(
            write_fixture(migrated_database, source_filename="first.json"),
            write_fixture(migrated_database, source_filename="second.json"),
        )

    results = run(concurrent())

    assert sorted(result.created for result in results) == [False, True]
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 1
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_levels") == 111
    assert scalar(
        migrated_database,
        "SELECT count(*) FROM import_jobs WHERE status = 'completed'",
    ) == 1
    assert scalar(
        migrated_database,
        "SELECT count(*) FROM import_jobs WHERE status = 'duplicate'",
    ) == 1


@pytest.mark.parametrize(
    ("source_filename", "expected"),
    [
        (r"C:\private\bad.json", "bad.json"),
        (r"D:\captures\confirmed.json", "confirmed.json"),
        ("/private/captures/confirmed.json", "confirmed.json"),
        (r"mixed/path\confirmed.json", "confirmed.json"),
    ],
)
def test_safe_filename_is_platform_independent(
    source_filename: str,
    expected: str,
) -> None:
    assert _safe_filename(source_filename) == expected


@pytest.mark.parametrize(
    "source_filename",
    ["中文订单簿.json", "日本語の注文板.json", "café-Δ-😀.json"],
)
def test_safe_filename_accepts_valid_utf8(source_filename: str) -> None:
    assert _safe_filename(source_filename) == source_filename


@pytest.mark.parametrize(
    "source_filename",
    [
        "",
        "/",
        "\\",
        ".",
        "..",
        "a" * 256,
        "bad\x00.json",
        "bad\n.json",
        "bad\ud800.json",
        "bad\udcff.json",
        "bad\u202ejson",
        "bad\u2066json",
        "bad\ufeffjson",
    ],
)
def test_safe_filename_rejects_unsafe_basename(source_filename: str) -> None:
    with pytest.raises(ManualOrderBookImportError) as exc_info:
        _safe_filename(source_filename)

    assert exc_info.value.code == "source_filename_invalid"


@pytest.mark.parametrize("source_filename", ["bad\ud800.json", "bad\udcff.json"])
def test_write_rejects_surrogate_filename_without_audit_or_capture_rows(
    migrated_database: str,
    source_filename: str,
) -> None:
    insert_item(migrated_database)

    with pytest.raises(ManualOrderBookImportError) as exc_info:
        run(write_fixture(migrated_database, source_filename=source_filename))

    assert exc_info.value.code == "source_filename_invalid"
    assert scalar(migrated_database, "SELECT count(*) FROM import_jobs") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_levels") == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX raw filename bytes only")
def test_posix_non_utf8_filename_never_enters_database(
    migrated_database: str,
    tmp_path: Path,
) -> None:
    insert_item(migrated_database)
    raw_path = os.fsencode(tmp_path) + b"/capture-\xff.json"
    descriptor = os.open(raw_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, fixture_content())
    finally:
        os.close(descriptor)
    source_filename = Path(os.fsdecode(raw_path)).name

    with pytest.raises(ManualOrderBookImportError) as exc_info:
        run(write_fixture(migrated_database, source_filename=source_filename))

    assert exc_info.value.code == "source_filename_invalid"
    assert scalar(migrated_database, "SELECT count(*) FROM import_jobs") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_levels") == 0


def test_cli_defaults_to_database_read_only_dry_run(migrated_database: str) -> None:
    item_id = insert_item(migrated_database)

    result = run_cli(migrated_database, FIXTURE)

    assert result.returncode == 0
    assert result.stderr == ""
    summary = json.loads(result.stdout)
    assert summary["mode"] == "dry_run"
    assert summary["validation_status"] == "valid"
    assert summary["database_written"] is False
    assert summary["database_item_id"] == item_id
    assert summary["would_create"] is True
    assert "buy_levels" not in summary
    assert "sell_levels" not in summary
    assert str(FIXTURE) not in result.stdout
    assert scalar(migrated_database, "SELECT count(*) FROM import_jobs") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 0


def test_cli_write_is_explicit_and_retry_is_idempotent(migrated_database: str) -> None:
    insert_item(migrated_database)

    first = run_cli(migrated_database, FIXTURE, "--write")
    second = run_cli(migrated_database, FIXTURE, "--write")

    assert first.returncode == 0
    assert second.returncode == 0
    assert first.stderr == second.stderr == ""
    first_summary = json.loads(first.stdout)
    second_summary = json.loads(second.stdout)
    assert first_summary["mode"] == second_summary["mode"] == "write"
    assert first_summary["created"] is True
    assert first_summary["database_written"] is True
    assert second_summary["created"] is False
    assert second_summary["database_written"] is False
    assert (
        first_summary["manual_order_book_import_id"]
        == second_summary["manual_order_book_import_id"]
    )
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 1
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_levels") == 111


def test_cli_invalid_file_is_narrow_and_does_not_access_database(
    migrated_database: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "sensitive-invalid.json"
    sentinel = "must-not-be-echoed"
    path.write_text(json.dumps({"payload": sentinel}), encoding="utf-8")

    result = run_cli(migrated_database, path)

    assert result.returncode == 2
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error_code": "top_level_keys_invalid",
        "error_type": "invalid_document",
    }
    assert sentinel not in result.stderr
    assert str(path) not in result.stderr
    assert "Traceback" not in result.stderr
    assert scalar(migrated_database, "SELECT count(*) FROM import_jobs") == 0


def test_cli_invalid_write_records_failed_audit_without_echoing_input(
    migrated_database: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid-write.json"
    path.write_bytes(b'{"payload":"private-sentinel"}')

    result = run_cli(migrated_database, path, "--write")

    assert result.returncode == 2
    assert result.stdout == ""
    assert json.loads(result.stderr)["error_code"] == "top_level_keys_invalid"
    assert "private-sentinel" not in result.stderr
    assert str(path) not in result.stderr
    assert scalar(
        migrated_database,
        "SELECT count(*) FROM import_jobs WHERE status = 'failed'",
    ) == 1


@pytest.mark.parametrize("source_filename", ["capture-\ud800.json", "capture-\udcff.json"])
def test_cli_valid_document_with_surrogate_filename_is_narrow_validation_error(
    migrated_database: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source_filename: str,
) -> None:
    insert_item(migrated_database)
    monkeypatch.setattr(cli, "read_manual_order_book_file", lambda _path: fixture_content())

    exit_code = cli.main([source_filename, "--write"])
    captured = capsys.readouterr()

    assert exit_code == cli.EXIT_VALIDATION_ERROR
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": "source_filename_invalid",
        "error_type": "invalid_document",
    }
    assert "Traceback" not in captured.err
    assert "manual_order_book_import.py" not in captured.err
    assert "123e4567-e89b-42d3-a456-426614174000" not in captured.err
    assert scalar(migrated_database, "SELECT count(*) FROM import_jobs") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM manual_order_book_levels") == 0


def test_cli_invalid_write_handles_filename_validation_from_failed_audit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = b'{"payload":"must-not-be-echoed"}'
    monkeypatch.setattr(cli, "read_manual_order_book_file", lambda _path: sentinel)

    async def reject_audit(**_kwargs) -> None:
        raise ManualOrderBookImportError(
            "source_filename_invalid",
            "The source filename is invalid.",
        )

    monkeypatch.setattr(cli, "_record_invalid_attempt", reject_audit)

    exit_code = cli.main(["private/control-name.json", "--write"])
    captured = capsys.readouterr()

    assert exit_code == cli.EXIT_VALIDATION_ERROR
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": "source_filename_invalid",
        "error_type": "invalid_document",
    }
    assert "Traceback" not in captured.err
    assert "private/control-name.json" not in captured.err
    assert "must-not-be-echoed" not in captured.err
    assert "manual_order_book_import.py" not in captured.err


@pytest.mark.parametrize(
    ("write", "raised", "expected_code", "expected_type", "expected_exit"),
    [
        (
            True,
            ManualOrderBookImportError("database_error", "narrow"),
            "database_error",
            "database_operational_error",
            cli.EXIT_DATABASE_UNAVAILABLE,
        ),
        (
            False,
            SQLAlchemyError("synthetic database failure"),
            "database_error",
            "database_operational_error",
            cli.EXIT_DATABASE_UNAVAILABLE,
        ),
        (
            True,
            ManualOrderBookImportError("import_identity_conflict", "narrow"),
            "import_identity_conflict",
            "import_conflict",
            cli.EXIT_IMPORT_CONFLICT,
        ),
    ],
)
def test_cli_classifies_operational_and_identity_errors_without_leakage(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    write: bool,
    raised: Exception,
    expected_code: str,
    expected_type: str,
    expected_exit: int,
) -> None:
    async def fail_run(**_kwargs):
        raise raised

    monkeypatch.setattr(cli, "_run", fail_run)
    arguments = [str(FIXTURE)] + (["--write"] if write else [])

    exit_code = cli.main(arguments)
    captured = capsys.readouterr()

    assert exit_code == expected_exit
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error_code": expected_code,
        "error_type": expected_type,
    }
    assert "Traceback" not in captured.err
    assert str(FIXTURE) not in captured.err
    assert "123e4567-e89b-42d3-a456-426614174000" not in captured.err
