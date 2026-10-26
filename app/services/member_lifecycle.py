"""Atomic member changes, serialized admin protection and permanent key revocation."""

from fastapi import HTTPException
from sqlalchemy import select, text, update

from app.models.dashboard import DashboardSession, Membership
from app.models.db_models import AccessAPIKey, AuthorizedApp, User
from app.services.dashboard import aware, now, require_admin


async def lock_admin_change(db, actor):
    # A transaction-level lock also protects concurrent invitations/promotions.
    # SQLite regressions are sequential; PostgreSQL integration tests exercise this lock.
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(726441908231)"))
    fresh = (
        await db.execute(
            select(Membership)
            .where(Membership.id == actor.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    require_admin(fresh)
    if fresh.status != "approved" or (
        fresh.access_expires_at and aware(fresh.access_expires_at) <= now()
    ):
        raise HTTPException(403, "Administrator access is no longer active")


async def lock_target(db, identifier):
    target = (
        await db.execute(
            select(Membership)
            .where(Membership.id == identifier)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Member not found")
    return target


async def protect_administrator(db, target, role, status, expires_at):
    if target.role != "admin":
        return
    if role == "admin" and status == "approved" and expires_at is None:
        return
    rows = (
        (
            await db.execute(
                select(Membership)
                .join(User, Membership.user_id == User.id)
                .where(
                    Membership.id != target.id,
                    Membership.role == "admin",
                    Membership.status == "approved",
                    User.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if not any(r.access_expires_at is None or aware(r.access_expires_at) > now() for r in rows):
        raise HTTPException(409, "Keep another active administrator before reducing this access")


async def invalidate_member(db, target, *, revoke_keys):
    await db.execute(
        update(DashboardSession)
        .where(DashboardSession.membership_id == target.id)
        .values(revoked_at=now())
    )
    if target.user_id:
        await db.execute(
            update(User)
            .where(User.id == target.user_id)
            .values(session_version=User.session_version + 1)
        )
    if revoke_keys and target.user_id:
        apps = select(AuthorizedApp.id).where(AuthorizedApp.owner_user_id == target.user_id)
        await db.execute(
            update(AccessAPIKey)
            .where(AccessAPIKey.authorized_app_id.in_(apps))
            .values(is_active=False)
        )
