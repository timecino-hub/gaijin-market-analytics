from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, text


API_SOURCE = Path(__file__).resolve().parents[1] / "src"


def payload(item_key: str = "History Test Item") -> dict[str, object]:
    return {
        "schema_version": "gaijin_trade_history_v1",
        "item_key": item_key,
        "source_url": f"https://trade.gaijin.net/market/1067/{quote(item_key)}",
        "captured_at": "2026-07-11T00:00:00Z",
        "series": {
            "1h": [[1783728000, 1000000, 1], [1783731600, 1200000, 1]],
            "1d": [[1783728000, 1100000, 2]],
        },
    }


def run_cli(database_url: str, path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(API_SOURCE)
    return subprocess.run(
        [sys.executable, "-m", "api.historical_trade_import_cli", str(path), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def insert_item(database_url: str, external_key: str = "History Test Item") -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO items (external_key, name, category) VALUES (:key, :key, 'vehicle')"),
                {"key": external_key},
            )
    finally:
        engine.dispose()


def scalar(database_url: str, statement: str) -> int:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return int(connection.execute(text(statement)).scalar_one())
    finally:
        engine.dispose()


def test_offline_history_cli_dry_run_and_write_are_explicit(
    migrated_database: str,
    tmp_path: Path,
) -> None:
    insert_item(migrated_database)
    path = tmp_path / "history.json"
    path.write_text(json.dumps(payload()), encoding="utf-8")

    dry_run = run_cli(migrated_database, path)
    assert dry_run.returncode == 0
    dry_summary = json.loads(dry_run.stdout)
    assert dry_summary["mode"] == "dry_run"
    assert dry_summary["would_create"] is True
    assert dry_summary["database_written"] is False
    assert scalar(migrated_database, "SELECT count(*) FROM historical_trade_imports") == 0

    write = run_cli(migrated_database, path, "--write")
    assert write.returncode == 0
    write_summary = json.loads(write.stdout)
    assert write_summary["database_written"] is True
    assert write_summary["inserted_count"] == 3
    assert scalar(migrated_database, "SELECT count(*) FROM historical_trade_imports") == 1
    assert scalar(migrated_database, "SELECT count(*) FROM historical_trade_buckets") == 3


def test_offline_history_cli_rejects_duplicate_keys_without_database(
    migrated_database: str,
    tmp_path: Path,
) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version":"gaijin_trade_history_v1","schema_version":"bad"}', encoding="utf-8")

    result = run_cli(migrated_database, path)

    assert result.returncode == 2
    assert json.loads(result.stderr)["error_code"] == "duplicate_object_key"
    assert scalar(migrated_database, "SELECT count(*) FROM historical_trade_imports") == 0
