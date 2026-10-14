"""Backend-owned dashboard access and storage.

Revision ID: c93d5e71ab20
Revises: a71c942e106b
"""

import sqlalchemy as sa
from alembic import op

revision = "c93d5e71ab20"
down_revision = "a71c942e106b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approved_by", sa.Integer, sa.ForeignKey("memberships.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("access_expires_at", sa.DateTime(timezone=True)),
        sa.Column("permission_version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "membership_id",
            sa.Integer,
            sa.ForeignKey("memberships.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("resource_type", sa.String(24), nullable=False),
        sa.Column("resource_key", sa.String(512), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.UniqueConstraint("membership_id", "resource_type", "resource_key", "action"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "membership_id", sa.Integer, sa.ForeignKey("memberships.id"), nullable=False, index=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "oauth_transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("browser_binding_hash", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("pkce_verifier", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("completion_hash", sa.String(64), unique=True),
        sa.Column("identity_json", sa.Text),
    )
    op.create_table(
        "access_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("email_verified", sa.Boolean, nullable=False),
        sa.Column("reviewed_by", sa.Integer, sa.ForeignKey("memberships.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "email_challenges",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, index=True),
        sa.Column("challenge_hash", sa.String(64), nullable=False),
        sa.Column("identity_json", sa.Text),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "service_credentials",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("label", sa.String(80), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("encrypted_value", sa.Text, nullable=False),
        sa.Column("app_origin", sa.String(255)),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("memberships.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("actor_membership_id", sa.Integer, sa.ForeignKey("memberships.id"), index=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(512), nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    for table in (
        "audit_logs",
        "service_credentials",
        "email_challenges",
        "access_requests",
        "oauth_transactions",
        "sessions",
        "permissions",
        "memberships",
    ):
        op.drop_table(table)
