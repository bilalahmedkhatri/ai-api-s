"""Persist the verified author's biography without guessing legacy authors."""

import sqlalchemy as sa
from alembic import op

revision = "blog_0003"
down_revision = "blog_0002"
branch_labels = depends_on = None


def upgrade():
    schema = "public" if op.get_bind().dialect.name == "postgresql" else None
    op.add_column(
        "blog_posts", sa.Column("author_description", sa.Text, nullable=True), schema=schema
    )


def downgrade():
    schema = "public" if op.get_bind().dialect.name == "postgresql" else None
    for name in ("blog_posts", "blog_assets", "blog_categories"):
        table = sa.table(name, schema=schema)
        if op.get_bind().execute(sa.select(sa.func.count()).select_from(table)).scalar_one():
            raise RuntimeError("Blog data exists; refusing to discard author descriptions schema.")
    op.drop_column("blog_posts", "author_description", schema=schema)
