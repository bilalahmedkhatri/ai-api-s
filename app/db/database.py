"""Async SQLAlchemy engine + session factory + WAL initialisation."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings

# StaticPool is required for in-process SQLite so every async task shares one connection.
engine = create_async_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=settings.debug,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Create all tables and enable WAL mode."""
    from app.models.db_models import Base  # local import avoids circular refs

    async with engine.begin() as conn:
        # WAL mode: readers and writers don't block each other.
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session
