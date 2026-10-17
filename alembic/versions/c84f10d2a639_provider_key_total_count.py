"""Add lifetime provider-key allocation count."""

import sqlalchemy as sa
from alembic import op

revision = "c84f10d2a639"
down_revision = "ab92d18e740c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("provider_api_keys", sa.Column("total_count", sa.BigInteger(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("provider_api_keys") as batch:
        batch.drop_column("total_count")
