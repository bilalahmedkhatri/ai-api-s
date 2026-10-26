"""Add nullable author designation to existing dashboard memberships."""

import sqlalchemy as sa
from alembic import op

revision = "b1023c5d7e90"
down_revision = "f820b1c943ae"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memberships", sa.Column("designation", sa.String(160), nullable=True))


def downgrade():
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM memberships WHERE designation IS NOT NULL AND trim(designation) <> ''"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("Designation data exists; export/reconcile before downgrade.")
    with op.batch_alter_table("memberships") as batch:
        batch.drop_column("designation")
