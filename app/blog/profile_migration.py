"""Explicit, single-revision membership profile upgrade; never run on requests/startup."""

import asyncio
import json

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext

from app.blog.central_migration import ROOT
from app.blog.migrate import BlogMigrationError

PREVIOUS = "b1023c5d7e90"
REVISION = "b2034d6e8f01"


def verify(connection):
    columns = {item["name"]: item for item in sa.inspect(connection).get_columns("memberships")}
    for name, kind in (("avatar_url", sa.String), ("description", sa.Text)):
        item = columns.get(name)
        if not item or not item["nullable"] or not isinstance(item["type"], kind):
            raise BlogMigrationError(
                "Membership profile schema differs from the migration contract."
            )
    if (
        columns["avatar_url"]["type"].length != 512
        or MigrationContext.configure(connection).get_current_revision() != REVISION
    ):
        raise BlogMigrationError("Unexpected profile revision or column size.")
    return {
        "revision": REVISION,
        "avatar_url": "nullable VARCHAR(512)",
        "description": "nullable TEXT",
    }


def upgrade(connection):
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SET LOCAL search_path = public, pg_catalog"))
        connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(sa.text("SET LOCAL statement_timeout = '20s'"))
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext('ai-gateway-profile-schema'))")
        )
    current = MigrationContext.configure(connection).get_current_revision()
    if current == REVISION:
        return {"status": "already_current", **verify(connection)}
    if current != PREVIOUS:
        raise BlogMigrationError("Unexpected migration parent; no migrations were applied.")
    existing = {item["name"] for item in sa.inspect(connection).get_columns("memberships")}
    if existing & {"avatar_url", "description"}:
        raise BlogMigrationError("Untracked profile columns already exist.")
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, REVISION)
    return {"status": "upgraded", **verify(connection)}


async def run():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

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
                return await connection.run_sync(upgrade)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upgrade", required=True, action="store_true")
    parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run())))
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
