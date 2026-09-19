from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from api.importers.manual_order_book_json import (
    ManualOrderBookValidationError,
    confirm_pending_manual_order_book_json,
    parse_manual_order_book_json,
    read_manual_order_book_file,
)
from api.services.manual_order_book_import import (
    ManualOrderBookImportError,
    import_manual_order_book,
    import_pending_manual_order_book,
    inspect_manual_order_book_import,
    inspect_pending_manual_order_book_import,
    record_invalid_manual_order_book_attempt,
)


EXIT_VALIDATION_ERROR = 2
EXIT_IMPORT_CONFLICT = 3
EXIT_DATABASE_UNAVAILABLE = 4


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        content = read_manual_order_book_file(args.file)
    except ManualOrderBookValidationError as exc:
        _write_error(exc.code, "invalid_document")
        return EXIT_VALIDATION_ERROR

    source_file_sha256 = hashlib.sha256(content).hexdigest()
    try:
        if args.confirm_pending:
            confirm_pending_manual_order_book_json(content)
        else:
            parse_manual_order_book_json(content)
    except ManualOrderBookValidationError as exc:
        if args.write:
            try:
                asyncio.run(
                    _record_invalid_attempt(
                        source_filename=args.file.name,
                        source_file_sha256=source_file_sha256,
                        error_code=exc.code,
                    )
                )
            except ManualOrderBookImportError as audit_error:
                if audit_error.code == "database_error":
                    _write_error("database_error", "database_operational_error")
                    return EXIT_DATABASE_UNAVAILABLE
                _write_error(audit_error.code, "invalid_document")
                return EXIT_VALIDATION_ERROR
            except SQLAlchemyError:
                _write_error("database_error", "database_operational_error")
                return EXIT_DATABASE_UNAVAILABLE
        _write_error(exc.code, "invalid_document")
        return EXIT_VALIDATION_ERROR

    try:
        summary = asyncio.run(
            _run(
                content=content,
                source_filename=args.file.name,
                write=args.write,
                operator_confirmed_pending=args.confirm_pending,
            )
        )
    except ManualOrderBookImportError as exc:
        if exc.code == "database_error":
            _write_error(exc.code, "database_operational_error")
            return EXIT_DATABASE_UNAVAILABLE
        if exc.code == "source_filename_invalid":
            _write_error(exc.code, "invalid_document")
            return EXIT_VALIDATION_ERROR
        _write_error(exc.code, "import_conflict")
        return EXIT_IMPORT_CONFLICT
    except SQLAlchemyError:
        _write_error("database_error", "database_operational_error")
        return EXIT_DATABASE_UNAVAILABLE

    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


async def _run(
    *,
    content: bytes,
    source_filename: str,
    write: bool,
    operator_confirmed_pending: bool,
) -> dict[str, Any]:
    from api.db.session import async_session_factory

    async with async_session_factory() as session:
        if not write:
            inspection = await (
                inspect_pending_manual_order_book_import(
                    session=session,
                    content=content,
                )
                if operator_confirmed_pending
                else inspect_manual_order_book_import(
                    session=session,
                    content=content,
                )
            )
            return {
                "mode": "dry_run",
                "validation_status": "valid",
                "database_written": False,
                "database_item_id": inspection.database_item_id,
                "decoded_external_key": inspection.decoded_external_key,
                "existing_import_id": inspection.existing_import_id,
                "would_create": inspection.would_create,
                "capture_id": inspection.capture_id,
                "normalized_capture_fingerprint": (
                    inspection.normalized_capture_fingerprint
                ),
                "buy_level_count": inspection.buy_level_count,
                "sell_level_count": inspection.sell_level_count,
                "operator_confirmed_pending": operator_confirmed_pending,
            }

        result = await (
            import_pending_manual_order_book(
                session=session,
                content=content,
                source_filename=source_filename,
            )
            if operator_confirmed_pending
            else import_manual_order_book(
                session=session,
                content=content,
                source_filename=source_filename,
            )
        )
        return {
            "mode": "write",
            "validation_status": "valid",
            "database_written": result.created,
            "created": result.created,
            "database_item_id": result.database_item_id,
            "import_job_id": result.import_job_id,
            "manual_order_book_import_id": result.manual_order_book_import_id,
            "capture_id": result.capture_id,
            "normalized_capture_fingerprint": (
                result.normalized_capture_fingerprint
            ),
            "buy_level_count": result.buy_level_count,
            "sell_level_count": result.sell_level_count,
            "operator_confirmed_pending": operator_confirmed_pending,
        }


async def _record_invalid_attempt(
    *,
    source_filename: str,
    source_file_sha256: str,
    error_code: str,
) -> None:
    from api.db.session import async_session_factory

    async with async_session_factory() as session:
        await record_invalid_manual_order_book_attempt(
            session=session,
            source_filename=source_filename,
            source_file_sha256=source_file_sha256,
            error_code=error_code,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and map one confirmed manual order-book JSON file. "
            "The default mode is a database-read-only dry run; --write is required "
            "to persist raw capture and level records."
        )
    )
    parser.add_argument("file", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and resolve the existing item without database writes (default).",
    )
    mode.add_argument(
        "--write",
        action="store_true",
        help="Persist an audited raw capture; does not create market snapshots.",
    )
    parser.add_argument(
        "--confirm-pending",
        action="store_true",
        help=(
            "Promote one intact pending_user_confirmation export, recompute "
            "its fingerprint, and run the normal strict validator."
        ),
    )
    return parser


def _write_error(code: str, error_type: str) -> None:
    print(
        json.dumps(
            {"error_code": code, "error_type": error_type},
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
