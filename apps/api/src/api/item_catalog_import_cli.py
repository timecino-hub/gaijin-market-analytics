from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from api.importers.item_catalog_csv import (
    MAX_CATALOG_BYTES,
    ItemCatalogContractError,
    parse_item_catalog_csv,
)
from api.services.item_catalog_import import (
    ItemCatalogImportError,
    import_item_catalog,
    inspect_item_catalog,
    record_invalid_item_catalog_attempt,
)

EXIT_VALIDATION_ERROR = 2
EXIT_IMPORT_CONFLICT = 3
EXIT_DATABASE_UNAVAILABLE = 4


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    content: bytes | None = None
    try:
        content = _read(args.file)
        document = parse_item_catalog_csv(content)
    except ItemCatalogContractError as exc:
        if args.write and content is not None:
            try:
                asyncio.run(
                    _record_invalid(
                        filename=args.file.name,
                        checksum=hashlib.sha256(content).hexdigest(),
                        error_code=exc.code,
                    )
                )
            except ItemCatalogImportError as audit_error:
                if audit_error.code == "source_filename_invalid":
                    _error(audit_error.code, "invalid_document")
                    return EXIT_VALIDATION_ERROR
                _error("database_error", "database_operational_error")
                return EXIT_DATABASE_UNAVAILABLE
            except SQLAlchemyError:
                _error("database_error", "database_operational_error")
                return EXIT_DATABASE_UNAVAILABLE
        _error(exc.code, "invalid_document")
        return EXIT_VALIDATION_ERROR

    try:
        summary = asyncio.run(
            _run(document=document, filename=args.file.name, write=args.write)
        )
    except ItemCatalogImportError as exc:
        error_type = (
            "database_operational_error"
            if exc.code == "database_error"
            else "import_conflict"
        )
        _error(exc.code, error_type)
        return (
            EXIT_DATABASE_UNAVAILABLE
            if exc.code == "database_error"
            else EXIT_IMPORT_CONFLICT
        )
    except SQLAlchemyError:
        _error("database_error", "database_operational_error")
        return EXIT_DATABASE_UNAVAILABLE
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


async def _run(*, document, filename: str, write: bool) -> dict[str, object]:
    from api.db.session import async_session_factory

    async with async_session_factory() as session:
        if write:
            result = await import_item_catalog(
                session=session, document=document, source_filename=filename
            )
            return {
                "mode": "write",
                "database_written": result.created,
                "import_job_id": result.import_job_id,
                "row_count": result.row_count,
                "create_count": result.create_count,
                "reuse_count": result.reuse_count,
                "normalized_catalog_sha256": document.normalized_catalog_sha256,
            }
        inspection = await inspect_item_catalog(session=session, document=document)
        return {
            "mode": "dry_run",
            "database_written": False,
            "row_count": inspection.row_count,
            "create_count": inspection.create_count,
            "reuse_count": inspection.reuse_count,
            "existing_import_job_id": inspection.existing_import_job_id,
            "normalized_catalog_sha256": document.normalized_catalog_sha256,
        }


async def _record_invalid(*, filename: str, checksum: str, error_code: str) -> None:
    from api.db.session import async_session_factory

    async with async_session_factory() as session:
        await record_invalid_item_catalog_attempt(
            session=session,
            source_filename=filename,
            source_file_sha256=checksum,
            error_code=error_code,
        )


def _read(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_CATALOG_BYTES + 1)
    except OSError as exc:
        raise ItemCatalogContractError("file_unreadable") from exc
    if len(content) > MAX_CATALOG_BYTES:
        raise ItemCatalogContractError("file_too_large")
    return content


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or import one reviewed item catalog CSV."
    )
    parser.add_argument("file", type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Create missing items and audit the import.",
    )
    return parser


def _error(code: str, error_type: str) -> None:
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
