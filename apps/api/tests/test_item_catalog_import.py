from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Awaitable
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.importers.item_catalog_csv import (
    ItemCatalogContractError,
    parse_item_catalog_csv,
)
from api.services.item_catalog_import import (
    ItemCatalogImportError,
    import_item_catalog,
    inspect_item_catalog,
)

API_SOURCE = Path(__file__).resolve().parents[1] / "src"
HEADER = b"external_key,name,category,rarity,is_active\n"
ROWS = b"item-alpha,Alpha,vehicle,rare,true\nitem-beta,Beta,coupon,,false\n"


def run[T](value: Awaitable[T]) -> T:
    return asyncio.run(value)


async def with_session(database_url: str, callback):
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await callback(session)
    finally:
        await engine.dispose()


def scalar(database_url: str, statement: str) -> int:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return int(connection.execute(text(statement)).scalar_one())
    finally:
        engine.dispose()


def cli_environment(database_url: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(API_SOURCE)
    return environment


def test_catalog_parser_is_strict_and_semantic_hash_is_order_independent() -> None:
    first = parse_item_catalog_csv(HEADER + ROWS)
    second = parse_item_catalog_csv(
        HEADER
        + b"item-beta,Beta,coupon,,false\n"
        + b"item-alpha,Alpha,vehicle,rare,true\n"
    )

    assert len(first.rows) == 2
    assert first.rows[0].rarity == "rare"
    assert first.rows[1].rarity is None
    assert first.rows[1].is_active is False
    assert first.source_file_sha256 != second.source_file_sha256
    assert first.normalized_catalog_sha256 == second.normalized_catalog_sha256


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"", "empty_file"),
        (
            b"name,external_key,category,rarity,is_active\nA,a,x,,true\n",
            "header_invalid",
        ),
        (HEADER + b"item-alpha,Alpha,vehicle,,yes\n", "is_active_invalid"),
        (
            HEADER + b"item-alpha,Alpha,vehicle,,true\nitem-alpha,A,vehicle,,true\n",
            "external_key_duplicate",
        ),
        (HEADER + b"item-alpha,Bad\x00Name,vehicle,,true\n", "name_invalid"),
    ],
)
def test_catalog_parser_rejects_unsafe_documents(content: bytes, code: str) -> None:
    with pytest.raises(ItemCatalogContractError) as exc_info:
        parse_item_catalog_csv(content)
    assert exc_info.value.code == code


def test_catalog_dry_run_is_read_only(migrated_database: str) -> None:
    document = parse_item_catalog_csv(HEADER + ROWS)

    async def operation(session):
        return await inspect_item_catalog(session=session, document=document)

    inspection = run(with_session(migrated_database, operation))

    assert inspection.row_count == 2
    assert inspection.create_count == 2
    assert inspection.reuse_count == 0
    assert scalar(migrated_database, "SELECT count(*) FROM items") == 0
    assert scalar(migrated_database, "SELECT count(*) FROM import_jobs") == 0


def test_catalog_write_creates_items_without_market_rows(
    migrated_database: str,
) -> None:
    document = parse_item_catalog_csv(HEADER + ROWS)

    async def operation(session):
        return await import_item_catalog(
            session=session,
            document=document,
            source_filename="reviewed-catalog.csv",
        )

    result = run(with_session(migrated_database, operation))

    assert result.created is True
    assert result.create_count == 2
    assert scalar(migrated_database, "SELECT count(*) FROM items") == 2
    assert scalar(migrated_database, "SELECT count(*) FROM import_jobs") == 1
    assert scalar(migrated_database, "SELECT count(*) FROM market_snapshots") == 0
    assert (
        scalar(migrated_database, "SELECT count(*) FROM manual_order_book_imports") == 0
    )


def test_catalog_semantic_retry_is_audited_duplicate(migrated_database: str) -> None:
    first_document = parse_item_catalog_csv(HEADER + ROWS)
    second_document = parse_item_catalog_csv(
        HEADER
        + b"item-beta,Beta,coupon,,false\n"
        + b"item-alpha,Alpha,vehicle,rare,true\n"
    )

    async def operation(session, document, filename):
        return await import_item_catalog(
            session=session,
            document=document,
            source_filename=filename,
        )

    first = run(
        with_session(
            migrated_database,
            lambda session: operation(session, first_document, "first.csv"),
        )
    )
    second = run(
        with_session(
            migrated_database,
            lambda session: operation(session, second_document, "second.csv"),
        )
    )

    assert first.created is True
    assert second.created is False
    assert second.existing_import_job_id == first.import_job_id
    assert scalar(migrated_database, "SELECT count(*) FROM items") == 2
    assert scalar(migrated_database, "SELECT count(*) FROM import_jobs") == 2
    assert (
        scalar(
            migrated_database,
            "SELECT count(*) FROM import_jobs WHERE status = 'duplicate'",
        )
        == 1
    )


def test_catalog_conflict_never_updates_existing_item(migrated_database: str) -> None:
    engine = create_engine(migrated_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO items (external_key, name, category, rarity, is_active) "
                    "VALUES ('item-alpha', 'Original', 'vehicle', 'rare', true)"
                )
            )
    finally:
        engine.dispose()
    document = parse_item_catalog_csv(
        HEADER + b"item-alpha,Changed,vehicle,rare,true\n"
    )

    async def operation(session):
        return await import_item_catalog(
            session=session,
            document=document,
            source_filename="conflict.csv",
        )

    with pytest.raises(ItemCatalogImportError) as exc_info:
        run(with_session(migrated_database, operation))

    assert exc_info.value.code == "item_metadata_conflict"
    engine = create_engine(migrated_database)
    try:
        with engine.connect() as connection:
            name = connection.execute(
                text("SELECT name FROM items WHERE external_key = 'item-alpha'")
            ).scalar_one()
    finally:
        engine.dispose()
    assert name == "Original"
    assert (
        scalar(
            migrated_database,
            "SELECT count(*) FROM import_jobs WHERE status = 'failed'",
        )
        == 1
    )


def test_catalog_cli_defaults_to_dry_run_and_requires_explicit_write(
    migrated_database: str, tmp_path: Path
) -> None:
    path = tmp_path / "catalog.csv"
    path.write_bytes(HEADER + ROWS)
    command = [sys.executable, "-m", "api.item_catalog_import_cli", str(path)]

    dry_run = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=cli_environment(migrated_database),
    )
    write = subprocess.run(
        [*command, "--write"],
        check=False,
        capture_output=True,
        text=True,
        env=cli_environment(migrated_database),
    )

    assert dry_run.returncode == 0
    assert json.loads(dry_run.stdout)["mode"] == "dry_run"
    assert json.loads(dry_run.stdout)["database_written"] is False
    assert write.returncode == 0
    assert json.loads(write.stdout)["mode"] == "write"
    assert json.loads(write.stdout)["database_written"] is True
    assert scalar(migrated_database, "SELECT count(*) FROM items") == 2


def test_catalog_cli_invalid_write_records_narrow_audit(
    migrated_database: str, tmp_path: Path
) -> None:
    path = tmp_path / "invalid.csv"
    path.write_bytes(HEADER + b"item-alpha,private-sentinel,vehicle,,maybe\n")

    result = subprocess.run(
        [sys.executable, "-m", "api.item_catalog_import_cli", str(path), "--write"],
        check=False,
        capture_output=True,
        text=True,
        env=cli_environment(migrated_database),
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error_code": "is_active_invalid",
        "error_type": "invalid_document",
    }
    assert "private-sentinel" not in result.stderr
    assert str(path) not in result.stderr
    assert scalar(migrated_database, "SELECT count(*) FROM items") == 0
    assert (
        scalar(
            migrated_database,
            "SELECT count(*) FROM import_jobs WHERE status = 'failed'",
        )
        == 1
    )
