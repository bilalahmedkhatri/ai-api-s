"""Integration test: DB initialises and all three tables exist."""

import pytest
from sqlalchemy import inspect


@pytest.mark.asyncio
async def test_db_init(unit_db):
    tables = inspect(unit_db.session.bind).get_table_names()
    for table in ("queries", "search_results", "final_responses", "users", "authorized_apps", "access_apikey", "facebook_page_tokens"):
        assert table in tables, f"Table '{table}' not found"
