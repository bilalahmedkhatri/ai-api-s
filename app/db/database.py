"""Async SQLAlchemy engine + session factory + WAL initialisation."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# asyncpg-level connect args — keepalives_* are libpq-only and not accepted by asyncpg.
# Stability is handled instead via pool_pre_ping (validates at checkout) +
# pool_recycle (cycles connections before cloud PG evicts them server-side).
_ASYNCPG_CONNECT_ARGS = {
    "server_settings": {"application_name": "ai-gateway"},
    "command_timeout": 60,
}

# PostgreSQL Async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,   # validate connection at checkout
    pool_timeout=30,
    pool_recycle=300,     # recycle connections every 5 min before cloud PG evicts them
    connect_args=_ASYNCPG_CONNECT_ARGS,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Create all tables."""
    from app.models.db_models import Base  # local import avoids circular refs

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session
