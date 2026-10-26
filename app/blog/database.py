"""One generic async session connector for all configured blog databases."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.blog.config import BlogRegistry


def create_blog_engine(url: str, *, reusable: bool = False):
    # Only Neon's documented prepared-statement-aware pooler uses a bounded
    # local pool. Unknown/Supabase poolers retain the existing NullPool policy.
    host = make_url(url).host or ""
    pooled = reusable and host.endswith(".neon.tech")
    options = (
        {
            "pool_size": 2,
            "max_overflow": 1,
            "pool_timeout": 10,
            "pool_recycle": 300,
            "pool_pre_ping": True,
            "pool_use_lifo": True,
        }
        if pooled
        else {"poolclass": NullPool}
    )
    return create_async_engine(
        url,
        echo=False,
        hide_parameters=True,
        **options,
        connect_args={
            "prepared_statement_name_func": lambda: f"__blog_{uuid4().hex}__",
            "timeout": 10,
            "command_timeout": 30,
            "server_settings": {"application_name": "ai-gateway-blog"},
        },
    )


@asynccontextmanager
async def session_for_app(
    registry: BlogRegistry, app_key: str, engines=None
) -> AsyncIterator[AsyncSession]:
    """Fresh transaction per request; lifespan owns reusable engines, CLI owns its engine."""
    target = registry.for_app(app_key)
    assert target.database_url is not None
    engine = (
        await engines.get(target)
        if engines is not None
        else create_blog_engine(target.database_url.get_secret_value())
    )
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        if engines is None:
            await engine.dispose()
