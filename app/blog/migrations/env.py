"""Requires an explicit caller-owned connection; no implicit DATABASE_URL fallback."""

from alembic import context

from app.blog.migrations.schema_v3 import build_metadata

connection = context.config.attributes.get("connection")
if context.is_offline_mode():
    context.configure(
        url="postgresql://",
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="blog_schema_version",
        version_table_schema="public",
    )
elif connection is not None:
    context.configure(
        connection=connection,
        target_metadata=build_metadata(
            "public" if connection.dialect.name == "postgresql" else None
        ),
        version_table="blog_schema_version",
        version_table_schema="public" if connection.dialect.name == "postgresql" else None,
    )
else:
    raise RuntimeError("An explicit blog database connection is required.")

with context.begin_transaction():
    context.run_migrations()
