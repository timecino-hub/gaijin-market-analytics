from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from api.schemas.historical_trades import HistoricalTradeImportRequest
from api.services.historical_trades import (
    HistoricalTradeImportError,
    import_historical_trades,
    inspect_historical_trades,
)

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
EXIT_VALIDATION_ERROR = 2
EXIT_IMPORT_ERROR = 3
EXIT_DATABASE_UNAVAILABLE = 4
OFFLINE_PAIRING_ID = "offline_operator_file"
OFFLINE_EXTENSION_VERSION = "offline-history-json-v1"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        content = _read_file(args.file)
        request = _parse_request(content)
    except ValueError as exc:
        _error(str(exc), "invalid_document")
        return EXIT_VALIDATION_ERROR

    try:
        summary = asyncio.run(_run(request=request, write=args.write))
    except HistoricalTradeImportError as exc:
        _error(exc.code, "import_error")
        return EXIT_IMPORT_ERROR
    except SQLAlchemyError:
        _error("database_error", "database_operational_error")
        return EXIT_DATABASE_UNAVAILABLE
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


async def _run(
    *,
    request: HistoricalTradeImportRequest,
    write: bool,
) -> dict[str, Any]:
    from api.db.session import async_session_factory

    async with async_session_factory() as session:
        if not write:
            result = await inspect_historical_trades(session=session, request=request)
            return {
                "mode": "dry_run",
                "database_written": False,
                "database_item_id": result.database_item_id,
                "item_key": result.item_key,
                "source_series_sha256": result.source_series_sha256,
                "point_count_1h": result.point_count_1h,
                "point_count_1d": result.point_count_1d,
                "overlap_day_count": result.overlap_day_count,
                "overlap_mismatch_count": result.overlap_mismatch_count,
                "existing_import_id": result.existing_import_id,
                "would_create": result.would_create,
            }
        result = await import_historical_trades(
            session=session,
            request=request,
            pairing_id=OFFLINE_PAIRING_ID,
            extension_version=OFFLINE_EXTENSION_VERSION,
        )
        return {
            "mode": "write",
            "database_written": not result.deduplicated,
            "deduplicated": result.deduplicated,
            "import_id": result.import_record.id,
            "database_item_id": result.item.id,
            "item_key": result.item.external_key,
            "source_series_sha256": result.import_record.source_series_sha256,
            "point_count_1h": result.import_record.point_count_1h,
            "point_count_1d": result.import_record.point_count_1d,
            "inserted_count": result.inserted_count,
            "updated_count": result.updated_count,
            "unchanged_count": result.unchanged_count,
        }


def _read_file(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_DOCUMENT_BYTES + 1)
    except FileNotFoundError as exc:
        raise ValueError("file_not_found") from exc
    except OSError as exc:
        raise ValueError("file_read_error") from exc
    if not content:
        raise ValueError("empty_file")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("file_too_large")
    return content


def _parse_request(content: bytes) -> HistoricalTradeImportRequest:
    try:
        text = content.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_without_duplicate_keys,
            parse_constant=_reject_constant,
        )
        return HistoricalTradeImportRequest.model_validate(payload)
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_utf8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_json") from exc
    except ValidationError as exc:
        raise ValueError("historical_trade_contract_invalid") from exc


def _without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_object_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("invalid_json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or import one offline gaijin_trade_history_v1 JSON file."
    )
    parser.add_argument("file", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    return parser


def _error(code: str, error_type: str) -> None:
    print(
        json.dumps({"error_code": code, "error_type": error_type}, sort_keys=True),
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
