"""Customer master-key activation and stable API ownership."""
import sqlalchemy as sa
from alembic import op

revision = "ab92d18e740c"
down_revision = "f73a9d20bc41"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("memberships") as batch:
        batch.add_column(sa.Column("master_key_encrypted", sa.Text(), nullable=True))
        batch.add_column(sa.Column("api_keys_unlocked", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("api_project_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_membership_api_project", "authorized_apps", ["api_project_id"], ["id"])
        batch.create_unique_constraint("uq_membership_api_project", ["api_project_id"])


def downgrade():
    # Customer ownership must never silently become a legacy app after downgrade.
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM memberships WHERE api_project_id IS NOT NULL")).scalar():
        raise RuntimeError("Reconcile customer projects before downgrading API key ownership")
    with op.batch_alter_table("memberships") as batch:
        batch.drop_constraint("uq_membership_api_project", type_="unique")
        batch.drop_constraint("fk_membership_api_project", type_="foreignkey")
        batch.drop_column("api_project_id")
        batch.drop_column("api_keys_unlocked")
        batch.drop_column("master_key_encrypted")
