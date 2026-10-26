"""Engine lifetime and pooler policy regressions without configured DB access."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.blog.config import load_registry
from app.blog.connections import BlogEngines
from app.blog.database import create_blog_engine, session_for_app
from sqlalchemy.pool import NullPool


def registry(password="fake"):
    return load_registry(
        {
            "BLOG_AZEEMLAB_DATABASE_URL": f"postgresql://user:{password}@a.neon.tech/blog",
            "BLOG_AILETTERGEN_DATABASE_URL": "postgresql://user:fake@b.neon.tech/blog",
        }
    )


@pytest.mark.asyncio
async def test_lifetime_reuses_engine_but_not_sessions_and_rotates_credentials():
    engines = BlogEngines()
    first, other, rotated = [MagicMock(dispose=AsyncMock()) for _ in range(3)]
    session_context = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    with (
        patch(
            "app.blog.connections.create_blog_engine", side_effect=[first, other, rotated]
        ) as create,
        patch(
            "app.blog.database.async_sessionmaker", return_value=lambda: session_context
        ) as sessions,
    ):
        for _ in range(2):
            async with session_for_app(registry(), "azeemlab", engines):
                pass
        assert create.call_count == 1
        assert sessions.call_count == 2
        first.dispose.assert_not_awaited()
        assert await engines.get(registry().for_app("ailettergen")) is other
        assert await engines.get(registry("changed").for_app("azeemlab")) is rotated
        first.dispose.assert_awaited_once()
        await engines.close()
        other.dispose.assert_awaited_once()
        rotated.dispose.assert_awaited_once()
        assert not engines._engines


@pytest.mark.parametrize(
    "host,pooled",
    [
        ("a-pooler.neon.tech", True),
        ("a.neon.tech", True),
        ("aws.pooler.supabase.com", False),
        ("unknown.test", False),
        ("neon.tech.attacker.test", False),
    ],
)
def test_bounded_neon_pool_and_conservative_unknown_pooler_policy(host, pooled):
    with patch("app.blog.database.create_async_engine") as create:
        create_blog_engine(f"postgresql+asyncpg://user:fake@{host}/blog", reusable=True)
    options = create.call_args.kwargs
    if pooled:
        assert options["pool_size"] == 2 and options["max_overflow"] == 1
        assert options["pool_pre_ping"] is True
        assert options["pool_timeout"] == 10
    else:
        assert options["poolclass"] is NullPool
    names = options["connect_args"]["prepared_statement_name_func"]
    assert names() != names()
    assert options["hide_parameters"] is True
