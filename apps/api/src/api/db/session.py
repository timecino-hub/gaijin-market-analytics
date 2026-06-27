from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.config import get_settings
from api.db.asyncio_compat import ensure_psycopg_asyncio_compatibility

ensure_psycopg_asyncio_compatibility()


def create_engine_from_settings():
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


engine = create_engine_from_settings()
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
