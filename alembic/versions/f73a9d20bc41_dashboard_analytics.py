"""Record elapsed time for dashboard API executions."""

import sqlalchemy as sa
from alembic import op

revision = "f73a9d20bc41"
down_revision = "f53c9e012d74"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("audit_logs", sa.Column("elapsed_ms", sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_column("elapsed_ms")
