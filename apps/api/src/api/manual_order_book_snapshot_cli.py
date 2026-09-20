from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.config import get_settings
from api.services.manual_order_book_snapshots import promote_manual_order_book_snapshots


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promote audited manual order books into idempotent analysis snapshots."
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist snapshots. Without this flag the command is a dry run.",
    )
    args = parser.parse_args()
    return asyncio.run(_run(write=args.write))


async def _run(*, write: bool) -> int:
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await promote_manual_order_book_snapshots(
                session=session,
                write=write,
            )
    finally:
        await engine.dispose()
    print(
        json.dumps(
            {
                "mode": "write" if write else "dry_run",
                "examined": result.examined_count,
                "created": result.created_count,
                "existing": result.existing_count,
                "would_create": result.would_create_count,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
