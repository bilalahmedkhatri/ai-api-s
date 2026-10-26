"""Operator-only per-app migration CLI; never auto-run at request/startup time."""

import argparse
import asyncio
import io
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext

from app.blog.config import APP_KEYS, load_registry
from app.blog.database import session_for_app
from app.blog.migrations.schema_v1 import build_metadata as build_v1
from app.blog.migrations.schema_v2 import build_metadata as build_v2
from app.blog.migrations.schema_v3 import REVISION, build_metadata

VERSION_TABLE = "blog_schema_version"
MIGRATIONS = Path(__file__).resolve().parent / "migrations"


class BlogMigrationError(RuntimeError):
    """Credential-free migration refusal."""


def migration_config(connection=None, output=None):
    config = Config(output_buffer=output)
    config.set_main_option("script_location", MIGRATIONS.as_posix())
    config.attributes["connection"] = connection
    return config


def current_revision(connection):
    return MigrationContext.configure(
        connection,
        opts={
            "version_table": VERSION_TABLE,
            "version_table_schema": "public" if connection.dialect.name == "postgresql" else None,
        },
    ).get_current_revision()


def inspect_schema(connection):
    schema = "public" if connection.dialect.name == "postgresql" else None
    inspector = sa.inspect(connection)
    names = set(inspector.get_table_names(schema=schema))
    return {
        "revision": current_revision(connection),
        "existing_blog_tables": sorted(names & {"blog_posts", "blog_categories", "blog_assets"}),
        "other_table_count": len(
            names - {"blog_posts", "blog_categories", "blog_assets", VERSION_TABLE}
        ),
    }


def verify_schema(connection, revision=REVISION):
    schema = "public" if connection.dialect.name == "postgresql" else None
    expected = {"blog_0001": build_v1, "blog_0002": build_v2, REVISION: build_metadata}[revision](
        schema
    )
    inspector = sa.inspect(connection)
    if current_revision(connection) != revision:
        raise BlogMigrationError("Unexpected blog schema revision.")
    counts = {}
    for table in expected.sorted_tables:
        columns = {item["name"]: item for item in inspector.get_columns(table.name, schema=schema)}
        if set(columns) != set(table.c.keys()):
            raise BlogMigrationError("Blog column inventory differs from the migration contract.")
        for column in table.c:
            actual = columns[column.name]
            expected_type = str(column.type.compile(dialect=connection.dialect))
            actual_type = str(actual["type"].compile(dialect=connection.dialect))
            same_type = actual_type == expected_type or (
                isinstance(column.type, sa.Float) and isinstance(actual["type"], sa.Float)
            )
            if not same_type or actual["nullable"] != column.nullable:
                raise BlogMigrationError("Blog column type/nullability differs from the contract.")
        primary_key = inspector.get_pk_constraint(table.name, schema=schema)
        if tuple(primary_key["constrained_columns"]) != tuple(
            col.name for col in table.primary_key.columns
        ):
            raise BlogMigrationError("Blog primary-key constraints differ from the contract.")
        for column in table.c:
            if column.server_default is not None and columns[column.name].get("default") is None:
                raise BlogMigrationError("Blog server defaults are missing.")
        unique = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints(table.name, schema=schema)
        }
        required_unique = {
            tuple(col.name for col in item.columns)
            for item in table.constraints
            if isinstance(item, sa.UniqueConstraint)
        }
        if not required_unique <= unique:
            raise BlogMigrationError("Blog uniqueness constraints are missing.")
        checks = {
            item["name"] for item in inspector.get_check_constraints(table.name, schema=schema)
        }
        required_checks = {
            item.name
            for item in table.constraints
            if isinstance(item, sa.CheckConstraint)
            and (
                connection.dialect.name == "postgresql"
                or item.name not in {"ck_blog_posts_document", "ck_blog_posts_metadata"}
            )
        }
        if not required_checks <= checks:
            raise BlogMigrationError("Blog validation constraints are missing.")
        foreign_keys = inspector.get_foreign_keys(table.name, schema=schema)
        for constraint in table.foreign_key_constraints:
            if not any(
                tuple(item["constrained_columns"]) == tuple(col.name for col in constraint.columns)
                and item["referred_table"] == next(iter(constraint.elements)).column.table.name
                and tuple(item["referred_columns"])
                == tuple(element.column.name for element in constraint.elements)
                and item.get("referred_schema") in {schema, None}
                and item["options"].get("ondelete") == "RESTRICT"
                for item in foreign_keys
            ):
                raise BlogMigrationError(
                    "Blog local foreign-key constraints differ from the contract."
                )
        indexes = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_indexes(table.name, schema=schema)
        }
        if any(
            indexes.get(index.name) != tuple(col.name for col in index.columns)
            for index in table.indexes
        ):
            raise BlogMigrationError("Blog listing indexes are missing.")
        counts[table.name] = connection.execute(
            sa.select(sa.func.count()).select_from(table)
        ).scalar_one()
    return {"revision": revision, "rows": counts}


def upgrade_schema(connection):
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(sa.text("SET LOCAL statement_timeout = '20s'"))
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext('ai-gateway-blog-schema'))")
        )
    before = inspect_schema(connection)
    if before["revision"] == REVISION:
        return {"status": "already_current", **verify_schema(connection)}
    if before["revision"] in {"blog_0001", "blog_0002"}:
        verify_schema(connection, before["revision"])
    elif before["revision"] is not None:
        raise BlogMigrationError("Unknown blog migration revision; refusing automatic adoption.")
    if before["revision"] is None and before["existing_blog_tables"]:
        raise BlogMigrationError("Blog table names already exist without this migration marker.")
    command.upgrade(migration_config(connection), REVISION)
    after = inspect_schema(connection)
    if after["other_table_count"] != before["other_table_count"]:
        raise BlogMigrationError("Unrelated table inventory changed during blog migration.")
    return {"status": "upgraded", **verify_schema(connection)}


def render_sql():
    output = io.StringIO()
    command.upgrade(migration_config(output=output), REVISION, sql=True)
    return output.getvalue()


async def run_target(key, action):
    registry = load_registry()
    async with asyncio.timeout(60):
        async with session_for_app(registry, key) as session:
            if action != "upgrade":
                await session.execute(sa.text("SET TRANSACTION READ ONLY"))
            connection = await session.connection()
            operation = {
                "inspect": inspect_schema,
                "verify": verify_schema,
                "upgrade": upgrade_schema,
            }[action]
            result = await connection.run_sync(operation)
            if action == "upgrade":
                await session.commit()
            return {"app": key, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=APP_KEYS)
    parser.add_argument("--action", choices=("inspect", "verify", "upgrade"), default="inspect")
    parser.add_argument(
        "--sql", action="store_true", help="Render generic PostgreSQL upgrade SQL offline."
    )
    args = parser.parse_args()
    if args.sql:
        print(render_sql())
        return 0
    if not args.app:
        parser.error("--app is required; there is deliberately no all-app upgrade option")
    try:
        result = asyncio.run(run_target(args.app, args.action))
    except Exception as error:
        # Never print driver exception messages, traces, URLs, or credentials.
        result = {"app": args.app, "status": "failed", "error_type": type(error).__name__}
        print(json.dumps(result))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
