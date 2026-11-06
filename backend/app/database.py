from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from app.config import settings

def normalize_asyncpg_url(raw_url: str) -> str:
    url = raw_url
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if "sslmode=" in url:
        url = url.replace("sslmode=require", "ssl=require")
        url = url.replace("sslmode=disable", "")
    return url


db_url = normalize_asyncpg_url(settings.DATABASE_URL)

engine = create_async_engine(
    db_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=AsyncSession,
)

_direct_engine = None


def get_direct_engine():
    """
    Returns the direct database engine for workers and session advisory locks.
    Lazily initialized using settings.get_direct_database_url().
    Raises ValueError if DIRECT_DATABASE_URL is missing or uses port 6543 (transaction pooling).

    Uses NullPool because Dramatiq workers execute tasks via asyncio.run(), which
    creates and closes an event loop per task. A connection pool (such as QueuePool)
    retains connections tied to closed event loops, triggering 'RuntimeError: Event loop is closed'
    when reused in subsequent tasks. NullPool ensures each direct connection is created
    and closed strictly within the caller's active event loop.
    """
    global _direct_engine
    if _direct_engine is None:
        direct_url = normalize_asyncpg_url(settings.get_direct_database_url())
        _direct_engine = create_async_engine(
            direct_url,
            echo=False,
            poolclass=NullPool,
        )
    return _direct_engine


async def get_direct_connection():
    """Returns a dedicated direct AsyncConnection from direct_engine."""
    eng = get_direct_engine()
    return await eng.connect()


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
