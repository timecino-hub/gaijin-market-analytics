import os
from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy.engine import URL, make_url

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://gaijin_market:gaijin_market_dev@localhost:5432/gaijin_market_analytics"
)


def _derive_test_url() -> str:
    url = make_url(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL))
    database = url.database or "gaijin_market_analytics"
    if database.endswith("_test"):
        return url.render_as_string(hide_password=False)
    return url.set(database=f"{database}_test").render_as_string(hide_password=False)


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", _derive_test_url())
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


def _psycopg_conninfo(url: URL) -> str:
    return url.render_as_string(hide_password=False).replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    url = make_url(TEST_DATABASE_URL)
    database = url.database
    if not database or not database.endswith("_test"):
        raise RuntimeError("Refusing to run database tests without an isolated *_test database.")

    admin_url = make_url(os.environ.get("TEST_DATABASE_ADMIN_URL", url.set(database="postgres").render_as_string(hide_password=False)))

    try:
        with psycopg.connect(_psycopg_conninfo(admin_url), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{database}"')
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL test database is unavailable: {exc}")

    yield TEST_DATABASE_URL

    with psycopg.connect(_psycopg_conninfo(admin_url), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
