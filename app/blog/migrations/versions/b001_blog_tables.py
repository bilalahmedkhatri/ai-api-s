"""Create the common blog tables; no author table or external user/app FKs."""

import sqlalchemy as sa
from alembic import op
from alembic.operations.ops import CreateIndexOp, CreateTableOp
from app.blog.migrations.schema_v1 import build_metadata

revision = "blog_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    metadata = build_metadata("public" if op.get_bind().dialect.name == "postgresql" else None)
    for table in metadata.sorted_tables:
        if op.get_bind().dialect.name != "postgresql":
            # Alembic copies constraints without SQLAlchemy ddl_if conditions.
            for constraint in list(table.constraints):
                if constraint.name in {"ck_blog_posts_document", "ck_blog_posts_metadata"}:
                    table.constraints.remove(constraint)
        op.invoke(CreateTableOp.from_table(table))
        for index in sorted(table.indexes, key=lambda item: item.name):
            op.invoke(CreateIndexOp.from_index(index))


def downgrade():
    metadata = build_metadata("public" if op.get_bind().dialect.name == "postgresql" else None)
    # Refuse destructive rollback once ANY blog data exists.
    for table in metadata.sorted_tables:
        if op.get_bind().execute(sa.select(sa.func.count()).select_from(table)).scalar_one():
            raise RuntimeError(
                "Blog data exists; export/reconcile before any destructive downgrade."
            )
    for table in reversed(metadata.sorted_tables):
        op.drop_table(table.name, schema=table.schema)
