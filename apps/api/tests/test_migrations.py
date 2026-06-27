from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _table_names(database_url: str) -> set[str]:
    sync_url = database_url.replace("postgresql+psycopg://", "postgresql+psycopg://", 1)
    engine = create_engine(sync_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_initial_migration_upgrade_downgrade_and_upgrade_again(test_database_url: str) -> None:
    config = _alembic_config(test_database_url)

    command.upgrade(config, "head")
    assert {"items", "import_jobs", "market_snapshots"}.issubset(_table_names(test_database_url))

    command.downgrade(config, "base")
    assert not {"items", "import_jobs", "market_snapshots"}.intersection(
        _table_names(test_database_url)
    )

    command.upgrade(config, "head")
    assert {"items", "import_jobs", "market_snapshots"}.issubset(_table_names(test_database_url))
