"""Dashboard membership and persistence owned exclusively by the gateway."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db_models import Base


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    designation: Mapped[str | None] = mapped_column(String(160), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("memberships.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    master_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_keys_unlocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    api_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("authorized_apps.id"), unique=True, nullable=True
    )
    permission_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("membership_id", "resource_type", "resource_key", "action"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    membership_id: Mapped[int] = mapped_column(
        ForeignKey("memberships.id", ondelete="CASCADE"), index=True
    )
    resource_type: Mapped[str] = mapped_column(String(24))
    resource_key: Mapped[str] = mapped_column(String(512))
    action: Mapped[str] = mapped_column(String(16), default="view")


class DashboardSession(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    membership_id: Mapped[int] = mapped_column(ForeignKey("memberships.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthTransaction(Base):
    __tablename__ = "oauth_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    browser_binding_hash: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(128))
    pkce_verifier: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    identity_json: Mapped[str | None] = mapped_column(Text)


class AccessRequest(Base):
    __tablename__ = "access_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("memberships.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailChallenge(Base):
    __tablename__ = "email_challenges"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    challenge_hash: Mapped[str] = mapped_column(String(64))
    identity_json: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceCredential(Base):
    __tablename__ = "service_credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    membership_id: Mapped[int | None] = mapped_column(ForeignKey("memberships.id"), unique=True)
    authorized_app_id: Mapped[int | None] = mapped_column(ForeignKey("authorized_apps.id"))
    access_api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("access_apikey.id"), unique=True
    )
    label: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    encrypted_value: Mapped[str] = mapped_column(Text)
    app_origin: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int] = mapped_column(ForeignKey("memberships.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_membership_id: Mapped[int | None] = mapped_column(
        ForeignKey("memberships.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(512))
    status: Mapped[int] = mapped_column(Integer)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
