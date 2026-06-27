import asyncio
import sys


def ensure_psycopg_asyncio_compatibility() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
