"""Add nullable membership avatar and author description without rewriting profiles."""

import sqlalchemy as sa
from alembic import op

revision = "b2034d6e8f01"
down_revision = "b1023c5d7e90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memberships", sa.Column("avatar_url", sa.String(512), nullable=True))
    op.add_column("memberships", sa.Column("description", sa.Text(), nullable=True))


def downgrade():
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM memberships WHERE avatar_url IS NOT NULL OR description IS NOT NULL"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("Profile data exists; export/reconcile before downgrade.")
    with op.batch_alter_table("memberships") as batch:
        batch.drop_column("description")
        batch.drop_column("avatar_url")
