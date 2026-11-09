"""Explicit additive migration. Never runs on startup or during an API request."""

import argparse
import asyncio
import json

import sqlalchemy as sa
from app.blog.config import APP_KEYS, load_registry
from app.blog.database import session_for_app
from app.blog.migrate import BlogMigrationError, current_revision
from sqlalchemy.dialects.postgresql import JSONB

COLUMN = "blog_details_json"
SQL = """ALTER TABLE public.blog_posts
ADD COLUMN blog_details_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.blog_posts ADD CONSTRAINT ck_blog_posts_details_v11
CHECK (jsonb_typeof(blog_details_json) = 'object');"""


def verify_schema(connection):
    schema = "public" if connection.dialect.name == "postgresql" else None
    if current_revision(connection) != "blog_0003":
        raise BlogMigrationError("V1.1 requires the existing blog_0003 base schema.")
    inspector = sa.inspect(connection)
    column = next(
        (
            item
            for item in inspector.get_columns("blog_posts", schema=schema)
            if item["name"] == COLUMN
        ),
        None,
    )
    if column is None or column["nullable"] or not column.get("default"):
        raise BlogMigrationError("V1.1 details column is missing or invalid.")
    if not isinstance(column["type"], (sa.JSON, JSONB)):
        raise BlogMigrationError("V1.1 details column must use JSON storage.")
    if connection.dialect.name == "postgresql" and not any(
        item["name"] == "ck_blog_posts_details_v11"
        for item in inspector.get_check_constraints("blog_posts", schema=schema)
    ):
        raise BlogMigrationError("V1.1 details validation constraint is missing.")
    return {"base_revision": "blog_0003", "extension": "blog_v1_1", "column": COLUMN}


def upgrade_schema(connection):
    if current_revision(connection) != "blog_0003":
        raise BlogMigrationError("Apply and verify the base blog_0003 migration first.")
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(sa.text("SET LOCAL statement_timeout = '20s'"))
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext('ai-gateway-blog-schema'))")
        )
    schema = "public" if postgres else None
    columns = sa.inspect(connection).get_columns("blog_posts", schema=schema)
    if not any(item["name"] == COLUMN for item in columns):
        if postgres:
            for statement in SQL.split(";"):
                if statement.strip():
                    connection.execute(sa.text(statement))
        else:
            connection.execute(
                sa.text(
                    "ALTER TABLE blog_posts ADD COLUMN blog_details_json JSON NOT NULL DEFAULT '{}'"
                )
            )
    return verify_schema(connection)


async def run_target(key, action):
    async with asyncio.timeout(60):
        async with session_for_app(load_registry(), key) as session:
            if action == "verify":
                await session.execute(sa.text("SET TRANSACTION READ ONLY"))
            connection = await session.connection()
            result = await connection.run_sync(
                upgrade_schema if action == "upgrade" else verify_schema
            )
            if action == "upgrade":
                await session.commit()
            return {"app": key, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=APP_KEYS)
    parser.add_argument("--action", choices=("verify", "upgrade"), default="verify")
    parser.add_argument("--sql", action="store_true")
    args = parser.parse_args()
    if args.sql:
        print(SQL)
        return 0
    if not args.app:
        parser.error("--app is required; no automatic all-app upgrades")
    try:
        print(json.dumps(asyncio.run(run_target(args.app, args.action))))
        return 0
    except Exception as error:
        print(json.dumps({"app": args.app, "status": "failed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
