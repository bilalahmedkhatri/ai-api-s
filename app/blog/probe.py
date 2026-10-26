"""Explicit bounded read-only checks. Never invoked by the catalog or editor."""

import asyncio

from sqlalchemy import text

from app.blog.config import BlogRegistry, BlogTarget
from app.blog.database import session_for_app

BLOG_TABLES = ("blog_posts", "blog_categories", "blog_assets")


async def check_target(registry: BlogRegistry, target: BlogTarget) -> dict:
    result = {"key": target.key, "configured": target.ready}
    if not target.ready:
        return {**result, "status": "not_configured"}
    try:
        async with asyncio.timeout(20):
            async with session_for_app(registry, target.key) as session:
                # This is the first SQL statement, before any SELECT in the transaction.
                await session.execute(text("SET TRANSACTION READ ONLY"))
                readonly = (
                    await session.execute(text("SELECT current_setting('transaction_read_only')"))
                ).scalar_one()
                if readonly != "on":
                    return {**result, "status": "failed", "error_type": "ReadOnlyRequired"}
                await session.execute(text("SELECT 1"))
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT table_name FROM information_schema.tables "
                                "WHERE table_schema = 'public' AND table_name IN "
                                "('blog_posts', 'blog_categories', 'blog_assets')"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
        present = sorted(rows)
        return {
            **result,
            "status": "connected_read_only",
            "existing_blog_tables": present,
            "missing_blog_tables": [name for name in BLOG_TABLES if name not in present],
        }
    except Exception as error:
        # Driver exception messages/tracebacks may contain server addresses or credentials.
        return {**result, "status": "failed", "error_type": type(error).__name__}


async def check_connections(registry: BlogRegistry) -> list[dict]:
    return list(await asyncio.gather(*(check_target(registry, item) for item in registry.targets)))
