"""Integration test: DB initialises and all three tables exist."""

import pytest
from app.db.database import engine, init_db
from sqlalchemy import text


@pytest.mark.asyncio
async def test_db_init():
    await init_db()
    async with engine.connect() as conn:
        for table in ("queries", "search_results", "final_responses"):
            result = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=:t"),
                {"t": table},
            )
            assert result.scalar() == table, f"Table '{table}' not found"
