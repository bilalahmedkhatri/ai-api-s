"""Explicit operator migration for the existing central membership designation field."""

import argparse
import asyncio
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.blog.migrate import BlogMigrationError

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "f820b1c943ae"
REVISION = "b1023c5d7e90"


def verify_designation(connection):
    schema = "public" if connection.dialect.name == "postgresql" else None
    columns = {
        item["name"]: item
        for item in sa.inspect(connection).get_columns("memberships", schema=schema)
    }
    column = columns.get("designation")
    if (
        column is None
        or not column["nullable"]
        or not isinstance(column["type"], sa.String)
        or column["type"].length != 160
    ):
        raise BlogMigrationError("Central designation column differs from the migration contract.")
    revision = MigrationContext.configure(connection).get_current_revision()
    if revision != REVISION:
        raise BlogMigrationError("Unexpected central revision.")
    return {"revision": revision, "designation": "nullable VARCHAR(160)"}


def upgrade_designation(connection):
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SET LOCAL search_path = public, pg_catalog"))
        connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(sa.text("SET LOCAL statement_timeout = '20s'"))
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext('ai-gateway-designation-schema'))")
        )
    revision = MigrationContext.configure(connection).get_current_revision()
    if revision == REVISION:
        return {"status": "already_current", **verify_designation(connection)}
    if revision != PREVIOUS:
        raise BlogMigrationError(
            "Central DB is not at the expected parent; no other migrations will be applied."
        )
    schema = "public" if connection.dialect.name == "postgresql" else None
    if any(
        item["name"] == "designation"
        for item in sa.inspect(connection).get_columns("memberships", schema=schema)
    ):
        raise BlogMigrationError("Untracked designation column already exists.")
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, REVISION)
    return {"status": "upgraded", **verify_designation(connection)}


async def run():
    # Import settings only for an explicitly requested operator upgrade.
    from app.core.config import settings

    engine = create_async_engine(
        settings.database_url,
        echo=False,
        hide_parameters=True,
        poolclass=NullPool,
        connect_args={"timeout": 10, "command_timeout": 30},
    )
    try:
        async with asyncio.timeout(60):
            async with engine.begin() as connection:
                return await connection.run_sync(upgrade_designation)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upgrade", action="store_true", required=True)
    parser.parse_args()
    try:
        result = asyncio.run(run())
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
