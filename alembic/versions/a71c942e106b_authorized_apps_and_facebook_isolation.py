"""Gateway owner permissions, authorized apps, and app-scoped Facebook tokens.

Revision ID: a71c942e106b
Revises: de25f5fb5538
"""

import sqlalchemy as sa
from alembic import op

revision = "a71c942e106b"
down_revision = "de25f5fb5538"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("api_access_enabled", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        "authorized_apps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("allowed_origin", sa.String(512), nullable=False),
        sa.Column("allowed_endpoints_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_approved", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_authorized_apps_owner_user_id", "authorized_apps", ["owner_user_id"])
    op.create_index("ix_authorized_apps_allowed_origin", "authorized_apps", ["allowed_origin"])
    for table in ("access_apikey", "facebook_page_tokens"):
        # Legacy records remain unassigned; never invent an owner or grant.
        op.add_column(table, sa.Column("authorized_app_id", sa.Integer(), nullable=True))
        op.create_foreign_key(f"fk_{table}_authorized_app", table, "authorized_apps", ["authorized_app_id"], ["id"], ondelete="CASCADE")
        op.create_index(f"ix_{table}_authorized_app_id", table, ["authorized_app_id"])
    op.drop_constraint("uq_user_page", "facebook_page_tokens", type_="unique")
    op.create_unique_constraint("uq_app_facebook_page", "facebook_page_tokens", ["authorized_app_id", "page_id"])


def downgrade() -> None:
    # Restoring user/page uniqueness would discard distinct app connections.
    # Refuse rollback until those conflicts are resolved explicitly by an admin.
    connection = op.get_bind()
    conflicts = connection.execute(sa.text(
        "SELECT 1 FROM facebook_page_tokens WHERE user_id IS NOT NULL "
        "GROUP BY user_id, page_id HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if conflicts:
        raise RuntimeError("Resolve duplicate user/page connections before downgrading")
    op.drop_constraint("uq_app_facebook_page", "facebook_page_tokens", type_="unique")
    op.create_unique_constraint("uq_user_page", "facebook_page_tokens", ["user_id", "page_id"])
    for table in ("facebook_page_tokens", "access_apikey"):
        op.drop_index(f"ix_{table}_authorized_app_id", table_name=table)
        op.drop_constraint(f"fk_{table}_authorized_app", table, type_="foreignkey")
        op.drop_column(table, "authorized_app_id")
    op.drop_table("authorized_apps")
    op.drop_column("users", "api_access_enabled")
