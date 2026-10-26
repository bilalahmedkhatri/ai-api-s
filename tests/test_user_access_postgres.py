"""Opt-in real PostgreSQL concurrency tests, isolated from the application database."""

import asyncio
import os
import uuid

import pytest
from app.core.config import settings
from app.models.dashboard import Membership
from app.models.db_models import AccessAPIKey, Base, User
from app.services.api_key_service import generate_api_key, verify_api_key
from app.services.customer_keys import ensure_project, locked_member
from app.services.dashboard import now
from app.services.member_lifecycle import invalidate_member, lock_admin_change, lock_target
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def pg_sessions():
    value = os.environ.get("TEST_ACCESS_POSTGRES_URL")
    if not value:
        pytest.skip("Dedicated TEST_ACCESS_POSTGRES_URL is not configured")
    url = make_url(value)
    configured = make_url(settings.database_url)
    if (url.host, url.port, url.database) == (
        configured.host,
        configured.port,
        configured.database,
    ):
        pytest.fail("Refusing concurrency tests against the configured application database")
    url = url.set(drivername="postgresql+asyncpg")
    schema = "access_test_" + uuid.uuid4().hex
    engine = create_async_engine(url)
    isolated = None
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        isolated = create_async_engine(
            url, connect_args={"server_settings": {"search_path": schema + ",pg_catalog"}}
        )
        async with isolated.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(isolated, expire_on_commit=False)
    finally:
        if isolated:
            await isolated.dispose()
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def seed(factory):
    async with factory() as db:
        members = []
        for name, role in (("admin", "admin"), ("other", "operator")):
            owner = User(
                google_id=name,
                email=name + "@example.test",
                is_active=True,
                api_access_enabled=True,
            )
            db.add(owner)
            await db.flush()
            m = Membership(
                user_id=owner.id,
                email=owner.email,
                name=name,
                role=role,
                status="approved",
                created_at=now(),
            )
            db.add(m)
            await db.flush()
            members.append(m.id)
        await db.commit()
        return members


async def test_concurrent_project_creation_is_idempotent(pg_sessions):
    _, target = await seed(pg_sessions)

    async def create():
        async with pg_sessions() as db:
            m = await locked_member(db, target)
            project = await ensure_project(db, m)
            await db.commit()
            return project.id

    assert len(set(await asyncio.gather(create(), create()))) == 1


async def test_revoke_serializes_with_inflight_key_issuance(pg_sessions):
    admin, target = await seed(pg_sessions)
    async with pg_sessions() as issuer:
        m = await locked_member(issuer, target)
        project = await ensure_project(issuer, m)
        raw, key = await generate_api_key(issuer, "Concurrent", project.id, commit=False)
        started = asyncio.Event()

        async def revoke():
            async with pg_sessions() as db:
                actor = await db.get(Membership, admin)
                await lock_admin_change(db, actor)
                started.set()
                victim = await lock_target(db, target)
                victim.status = "revoked"
                await invalidate_member(db, victim, revoke_keys=True)
                await db.commit()

        task = asyncio.create_task(revoke())
        await asyncio.wait_for(started.wait(), timeout=5)
        await issuer.commit()
        await asyncio.wait_for(task, timeout=5)
    async with pg_sessions() as db:
        assert not (await db.get(AccessAPIKey, key.id)).is_active
        assert await verify_api_key(db, raw) is None


async def test_concurrent_administrators_cannot_demote_each_other(pg_sessions):
    from app.services.member_lifecycle import protect_administrator

    a, b = await seed(pg_sessions)
    async with pg_sessions() as db:
        (await db.get(Membership, b)).role = "admin"
        await db.commit()

    async def demote(actor_id, target_id):
        async with pg_sessions() as db:
            actor = await db.get(Membership, actor_id)
            try:
                await lock_admin_change(db, actor)
                target = await lock_target(db, target_id)
                await protect_administrator(db, target, "viewer", "approved", None)
                target.role = "viewer"
                await db.commit()
                return True
            except HTTPException:
                await db.rollback()
                return False

    assert sum(await asyncio.gather(demote(a, b), demote(b, a))) == 1
    async with pg_sessions() as db:
        assert (
            len(
                (await db.execute(select(Membership).where(Membership.role == "admin")))
                .scalars()
                .all()
            )
            == 1
        )
