"""Read-only, metadata-only impact report. Does not migrate, provision or decrypt keys."""

import asyncio
import json

from app.db.database import AsyncSessionLocal
from app.models.dashboard import Membership
from app.models.db_models import AuthorizedApp
from app.services.access_policy import APP_OPERATIONS, effective_app_grants
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import load_only


async def report(db):
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SET TRANSACTION READ ONLY"))
    members = (
        (
            await db.execute(
                select(Membership)
                .options(
                    load_only(
                        Membership.id,
                        Membership.user_id,
                        Membership.api_project_id,
                        Membership.role,
                        Membership.status,
                        Membership.access_expires_at,
                    )
                )
                .order_by(Membership.id)
            )
        )
        .scalars()
        .all()
    )
    output = []
    for m in members:
        effective = set(await effective_app_grants(db, m))
        apps = (
            (
                await db.execute(
                    select(AuthorizedApp.id, AuthorizedApp.allowed_endpoints_json).where(
                        AuthorizedApp.owner_user_id == m.user_id
                    )
                )
            ).all()
            if m.user_id
            else []
        )
        projects = []
        for identifier, stored in apps:
            try:
                current = json.loads(stored)
                current = set(current) if isinstance(current, list) else set()
            except (ValueError, TypeError):
                current = set()
            projects.append(
                {
                    "project_id": identifier,
                    "removed_routes": sorted((current & APP_OPERATIONS.keys()) - effective),
                    "unknown_route_count": len(current - APP_OPERATIONS.keys()),
                    "allowed_routes": sorted(current & effective),
                }
            )
        output.append(
            {
                "membership_id": m.id,
                "role": m.role,
                "status": m.status,
                "missing_explicit_grants": not effective,
                "projects": projects,
            }
        )
    # Works before migration too: never assume the new tenant column exists.
    connection = await db.connection()
    scoped = await connection.run_sync(
        lambda c: (
            "authorized_app_id"
            in {column["name"] for column in inspect(c).get_columns("extracted_media")}
        )
    )
    query = "SELECT COUNT(*) FROM extracted_media" + (
        " WHERE authorized_app_id IS NULL" if scoped else ""
    )
    unassigned = (await db.execute(text(query))).scalar_one()
    return {"members": output, "unassigned_media_count": unassigned, "writes_performed": False}


async def main():
    async with AsyncSessionLocal() as db:
        print(json.dumps(await report(db), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
