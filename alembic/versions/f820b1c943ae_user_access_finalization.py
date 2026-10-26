"""Add credential bindings and media tenant scope without guessing legacy ownership."""

import sqlalchemy as sa
from alembic import op

revision = "f820b1c943ae"
down_revision = "e610a2c947bf"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("session_version", sa.Integer(), nullable=False, server_default="0")
        )
    with op.batch_alter_table("access_apikey") as batch:
        batch.add_column(
            sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("service_credentials") as batch:
        batch.add_column(sa.Column("membership_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("authorized_app_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("access_api_key_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_credential_member", "memberships", ["membership_id"], ["id"])
        batch.create_foreign_key(
            "fk_credential_app", "authorized_apps", ["authorized_app_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_credential_key", "access_apikey", ["access_api_key_id"], ["id"]
        )
        batch.create_unique_constraint("uq_credential_member", ["membership_id"])
        batch.create_unique_constraint("uq_credential_key", ["access_api_key_id"])
    with op.batch_alter_table("extracted_media") as batch:
        batch.add_column(sa.Column("authorized_app_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_extracted_media_app", "authorized_apps", ["authorized_app_id"], ["id"]
        )
        batch.create_index("ix_extracted_media_authorized_app_id", ["authorized_app_id"])
    # Never synthesize grants, adopt projects, or assign unscoped media.


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM users WHERE session_version <> 0")).scalar():
        raise RuntimeError("Reconcile invalidated cookie sessions before downgrade")
    if (
        bind.execute(
            sa.text("SELECT COUNT(*) FROM extracted_media WHERE authorized_app_id IS NOT NULL")
        ).scalar()
        or bind.execute(
            sa.text("SELECT COUNT(*) FROM service_credentials WHERE authorized_app_id IS NOT NULL")
        ).scalar()
        or bind.execute(
            sa.text("SELECT COUNT(*) FROM access_apikey WHERE is_internal = true")
        ).scalar()
    ):
        raise RuntimeError("Reconcile scoped media and credentials before downgrade")
    with op.batch_alter_table("extracted_media") as batch:
        batch.drop_index("ix_extracted_media_authorized_app_id")
        batch.drop_constraint("fk_extracted_media_app", type_="foreignkey")
        batch.drop_column("authorized_app_id")
    with op.batch_alter_table("service_credentials") as batch:
        for name in ("uq_credential_member", "uq_credential_key"):
            batch.drop_constraint(name, type_="unique")
        for name in ("fk_credential_member", "fk_credential_app", "fk_credential_key"):
            batch.drop_constraint(name, type_="foreignkey")
        for name in ("membership_id", "authorized_app_id", "access_api_key_id"):
            batch.drop_column(name)
    with op.batch_alter_table("access_apikey") as batch:
        batch.drop_column("is_internal")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("session_version")
