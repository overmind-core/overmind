from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from overmind_core.config import settings
import logging

logger = logging.getLogger(__name__)

_engine = None
_AsyncSessionLocal = None


def get_engine():
    """Get or create the async SQLAlchemy engine lazily."""
    global _engine
    if _engine is None:
        logger.info("Creating async database engine")
        _engine = create_async_engine(
            settings.database_url,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_pre_ping=True,
            pool_recycle=300,
            echo=settings.debug,
            pool_use_lifo=True,
            pool_reset_on_return="rollback",
        )
        logger.info("Async database engine created successfully")
    return _engine


def get_session_local():
    """Get or create the AsyncSessionLocal class lazily."""
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        engine = get_engine()
        _AsyncSessionLocal = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
        logger.info("AsyncSessionLocal created successfully")
    return _AsyncSessionLocal


async def get_db():
    """Get async database session — the main FastAPI dependency."""
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def dispose_engine():
    """Dispose of the database engine and close all connections."""
    global _engine, _AsyncSessionLocal
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _AsyncSessionLocal = None


def get_engine_instance():
    """Get the database engine instance."""
    return get_engine()
