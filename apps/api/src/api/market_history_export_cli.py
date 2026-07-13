from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from gaijin_market_analytics.datasets.market_history_export import (
    MarketHistoryExportProfile,
    MarketHistoryExportValidationError,
    item_mapping_manifest_sha256,
    load_item_mapping_manifest,
    market_history_export_json_bytes,
)
from sqlalchemy.exc import SQLAlchemyError



EXIT_PARAMETER_ERROR = 2
EXIT_DATABASE_UNAVAILABLE = 3
EXIT_EXPORT_CONFLICT = 4
EXIT_OUTPUT_ERROR = 5


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    from api.services.market_history_export import MarketHistoryExportError
    mapping_fingerprint = None
    if args.item_mapping is not None:
        try:
            mapping_fingerprint = item_mapping_manifest_sha256(
                load_item_mapping_manifest(args.item_mapping)
            )
        except MarketHistoryExportValidationError as exc:
            _exit(EXIT_PARAMETER_ERROR, f"Invalid item mapping manifest: {exc.code}")

    try:
        payload = asyncio.run(
            _run(
                export_profile=args.profile,
                item_ids=args.item_id,
                item_mapping_fingerprint=mapping_fingerprint,
                allow_exclusions=args.allow_exclusions,
            )
        )
        encoded = market_history_export_json_bytes(payload, pretty=args.pretty)
        _write_atomic(args.output, encoded)
    except (MarketHistoryExportError, MarketHistoryExportValidationError) as exc:
        _exit(EXIT_EXPORT_CONFLICT, f"Market history export failed: {exc.code}")
    except SQLAlchemyError:
        _exit(EXIT_DATABASE_UNAVAILABLE, "Database is unavailable.")
    except OSError:
        _exit(EXIT_OUTPUT_ERROR, "The export file could not be written.")

    print(json.dumps(_summary(payload, args.output), sort_keys=True))


async def _run(
    *,
    export_profile: str,
    item_ids: list[int] | None,
    item_mapping_fingerprint: str | None,
    allow_exclusions: bool,
) -> dict[str, Any]:
    from api.db.session import async_session_factory
    from api.services.market_history_export import export_market_history_from_database

    async with async_session_factory() as session:
        result = await export_market_history_from_database(
            session,
            export_profile=export_profile,
            item_ids=item_ids,
            item_mapping_fingerprint=item_mapping_fingerprint,
            allow_exclusions=allow_exclusions,
        )
    return result.payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export confirmed screen-review market observations in a read-only "
            "deterministic format."
        )
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--profile",
        choices=[value.value for value in MarketHistoryExportProfile],
        default=MarketHistoryExportProfile.POINT_IN_TIME_PRIMARY.value,
    )
    parser.add_argument("--item-id", action="append", type=_positive_item_id)
    parser.add_argument("--item-mapping", type=Path)
    parser.add_argument("--allow-exclusions", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def _positive_item_id(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("item id must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("item id must be a positive integer")
    return parsed


def _write_atomic(path: Path, encoded: bytes) -> None:
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _summary(payload: dict[str, Any], output: Path) -> dict[str, Any]:
    diagnostics = payload["diagnostics"]
    return {
        "schema_version": payload["schema_version"],
        "export_profile": payload["export_profile"],
        "included_record_count": diagnostics["included_record_count"],
        "excluded_record_count": diagnostics["excluded_record_count"],
        "source_fingerprint": payload["source_fingerprint"],
        "typed_history_fingerprint": payload["typed_history_fingerprint"],
        "output": str(output),
        "read_only": True,
    }


def _exit(code: int, message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
