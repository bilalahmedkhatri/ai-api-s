"""Explicit, BondsGame-only games schema migration; never run at app startup."""

import argparse
import asyncio
import json

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.games.config import games_database_url
from app.games.database import create_games_engine
from app.games.schema import game_comments, games, metadata

REVISION = "games_0001"
VERSION_TABLE = sa.Table(
    "games_schema_version",
    sa.MetaData(),
    sa.Column("version_num", sa.String(32), primary_key=True),
    schema="public",
)
MANAGED_TABLES = {"games", "game_comments"}
POLICY_SQL = (
    "ALTER TABLE public.games ENABLE ROW LEVEL SECURITY;",
    "ALTER TABLE public.game_comments ENABLE ROW LEVEL SECURITY;",
    "CREATE POLICY games_public_published_read ON public.games FOR SELECT USING (publish_status = 'published');",
    "CREATE POLICY game_comments_public_approved_read ON public.game_comments FOR SELECT USING "
    "(moderation_status = 'approved' AND EXISTS (SELECT 1 FROM public.games "
    "WHERE games.id = game_comments.game_id AND games.publish_status = 'published'));",
)


class GamesMigrationError(RuntimeError):
    """Credential-free migration refusal or schema mismatch."""


def inspect_schema(connection):
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names(schema="public"))
    existing = tables & MANAGED_TABLES
    version = None
    if "games_schema_version" in tables:
        version = connection.execute(sa.select(VERSION_TABLE.c.version_num)).scalar_one_or_none()
    return {"revision": version, "existing_managed_tables": sorted(existing)}


def verify_schema(connection):
    state = inspect_schema(connection)
    if state["revision"] != REVISION or state["existing_managed_tables"] != sorted(MANAGED_TABLES):
        raise GamesMigrationError("Games schema revision or managed table inventory is unexpected.")
    inspector = sa.inspect(connection)
    for table in (games, game_comments):
        actual_columns = {item["name"]: item for item in inspector.get_columns(table.name, schema="public")}
        if set(actual_columns) != set(table.c.keys()):
            raise GamesMigrationError("Games schema columns do not match the versioned contract.")
        for column in table.c:
            actual = actual_columns[column.name]
            expected_type = column.type.compile(dialect=connection.dialect)
            actual_type = actual["type"].compile(dialect=connection.dialect)
            if expected_type.lower() != actual_type.lower() or actual["nullable"] != column.nullable:
                raise GamesMigrationError("Games schema type or nullability differs from the contract.")
    constraints = {
        item["name"]
        for table_name in MANAGED_TABLES
        for item in inspector.get_check_constraints(table_name, schema="public")
    }
    required_checks = {
        constraint.name
        for table in (games, game_comments)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    if not required_checks <= constraints:
        raise GamesMigrationError("Games schema check constraints are missing.")
    policy_rows = connection.execute(sa.text(
        "SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename IN ('games', 'game_comments')"
    )).scalars().all()
    if not {"games_public_published_read", "game_comments_public_approved_read"} <= set(policy_rows):
        raise GamesMigrationError("Games public-read RLS policies are missing.")
    protected = connection.execute(sa.text(
        "SELECT relname FROM pg_class JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace "
        "WHERE nspname = 'public' AND relname IN ('games', 'game_comments') AND relrowsecurity"
    )).scalars().all()
    if set(protected) != MANAGED_TABLES:
        raise GamesMigrationError("Row-level security is not enabled on both games tables.")
    indexes = {
        (item["name"], item.get("column_names"), item.get("dialect_options", {}).get("postgresql_using"))
        for item in inspector.get_indexes("games", schema="public")
    }
    if not any(name == "ix_games_genres_gin" and using == "gin" for name, _, using in indexes):
        raise GamesMigrationError("Games genre GIN index is missing.")
    comment_indexes = {item["name"] for item in inspector.get_indexes("game_comments", schema="public")}
    if "ix_game_comments_game_created" not in comment_indexes:
        raise GamesMigrationError("Game comment pagination index is missing.")
    return {"revision": REVISION, "tables": sorted(MANAGED_TABLES), "status": "verified"}


def upgrade_schema(connection):
    state = inspect_schema(connection)
    if state["revision"] == REVISION and state["existing_managed_tables"] == sorted(MANAGED_TABLES):
        return verify_schema(connection)
    if state["revision"] is not None or state["existing_managed_tables"]:
        raise GamesMigrationError("Existing games tables/data detected; refusing an implicit replacement or adoption.")
    connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(sa.text("SET LOCAL statement_timeout = '20s'"))
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('ai-gateway-games-schema'))"))
    VERSION_TABLE.create(connection)
    metadata.create_all(connection)
    connection.execute(sa.text("ALTER TABLE public.games ENABLE ROW LEVEL SECURITY"))
    connection.execute(sa.text("ALTER TABLE public.game_comments ENABLE ROW LEVEL SECURITY"))
    connection.execute(sa.text(
        "CREATE POLICY games_public_published_read ON public.games FOR SELECT USING (publish_status = 'published')"
    ))
    connection.execute(sa.text(
        "CREATE POLICY game_comments_public_approved_read ON public.game_comments FOR SELECT USING "
        "(moderation_status = 'approved' AND EXISTS (SELECT 1 FROM public.games "
        "WHERE games.id = game_comments.game_id AND games.publish_status = 'published'))"
    ))
    connection.execute(VERSION_TABLE.insert().values(version_num=REVISION))
    return verify_schema(connection)


def schema_sql():
    dialect = postgresql.dialect()
    statements = [str(CreateTable(VERSION_TABLE).compile(dialect=dialect))]
    statements.extend(str(CreateTable(table).compile(dialect=dialect)) for table in metadata.sorted_tables)
    statements.extend(
        str(CreateIndex(index).compile(dialect=dialect))
        for table in metadata.tables.values()
        for index in table.indexes
    )
    statements.extend(POLICY_SQL)
    statements.append(
        "INSERT INTO public.games_schema_version (version_num) VALUES ('games_0001');"
    )
    return "\n\n".join(statement.rstrip(";") + ";" for statement in statements)


async def run(action):
    engine = create_games_engine(games_database_url().get_secret_value())
    try:
        async with engine.begin() as connection:
            result = await connection.run_sync(verify_schema if action == "verify" else upgrade_schema)
        return result
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("upgrade", "verify"), default="verify")
    parser.add_argument("--sql", action="store_true", help="Print the PostgreSQL DDL without connecting.")
    args = parser.parse_args()
    if args.sql:
        print(schema_sql())
        return 0
    try:
        print(json.dumps(asyncio.run(run(args.action))))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
