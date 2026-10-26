"""Track verified uploader; legacy rows remain unassigned."""

import sqlalchemy as sa
from alembic import op

revision = "blog_0002"
down_revision = "blog_0001"
branch_labels = depends_on = None


def upgrade():
    schema = "public" if op.get_bind().dialect.name == "postgresql" else None
    op.add_column(
        "blog_assets",
        sa.Column("uploaded_by_membership_id", sa.Integer, nullable=True),
        schema=schema,
    )
    op.create_index(
        "ix_blog_assets_uploader_id",
        "blog_assets",
        ["uploaded_by_membership_id", "id"],
        schema=schema,
    )


def downgrade():
    schema = "public" if op.get_bind().dialect.name == "postgresql" else None
    for name in ("blog_assets", "blog_posts", "blog_categories"):
        table = sa.table(name, schema=schema)
        if op.get_bind().execute(sa.select(sa.func.count()).select_from(table)).scalar_one():
            raise RuntimeError("Blog data exists; refusing to discard uploader schema.")
    op.drop_index("ix_blog_assets_uploader_id", table_name="blog_assets", schema=schema)
    op.drop_column("blog_assets", "uploaded_by_membership_id", schema=schema)
