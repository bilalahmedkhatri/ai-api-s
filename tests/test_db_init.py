"""Integration test: DB initialises, WAL is enabled, and all three tables exist."""

import asyncio

import pytest
from app.db.database import engine, init_db
from sqlalchemy import text


@pytest.mark.asyncio
async def test_db_init_and_wal():
    await init_db()
    async with engine.connect() as conn:
        row = await conn.execute(text("PRAGMA journal_mode"))
        assert row.scalar() == "wal"

        for table in ("queries", "search_results", "final_responses"):
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            )
            assert result.scalar() == table, f"Table '{table}' not found"
