"""Local-only bootstrap. Never auto-approve a public Google login."""

import asyncio
import os

from app.db.database import AsyncSessionLocal
from app.models.dashboard import Membership
from app.services.dashboard import now
from sqlalchemy import select


async def main():
    email = os.environ.get("DASHBOARD_BOOTSTRAP_EMAIL", "").lower().strip()
    if not email or "@" not in email:
        raise SystemExit("Set DASHBOARD_BOOTSTRAP_EMAIL to the approved administrator email")
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(Membership).where(Membership.email == email))
        ).scalar_one_or_none()
        if existing:
            raise SystemExit("Membership exists; use an existing administrator to change it")
        db.add(
            Membership(
                email=email,
                name="Administrator",
                role="admin",
                status="approved",
                approved_at=now(),
                created_at=now(),
            )
        )
        await db.commit()
    print(
        "Approved dashboard membership created. Google identity will be bound after verification."
    )


if __name__ == "__main__":
    asyncio.run(main())
