from collections.abc import AsyncIterator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from .settings import config

# The API is async; the CLI and alembic are sync. psycopg3 speaks both, so the
# same postgresql+psycopg:// URL drives either engine. Engines connect lazily,
# so the unused one in each process costs nothing.
async_engine = create_async_engine(config["DATABASE_URL"], pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)

sync_engine = create_engine(config["DATABASE_URL"], pool_pre_ping=True)
SessionLocal = sessionmaker(sync_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
