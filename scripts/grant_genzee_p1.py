"""Add only the three reviewed P1 grants to existing approved GenZee apps."""
import argparse
import asyncio
import json
import logging
from pathlib import Path

from app.db.database import AsyncSessionLocal, engine
from app.models.db_models import AccessAPIKey, AuthorizedApp, User
from app.services.api_key_service import hash_key
from dotenv import dotenv_values
from sqlalchemy import select

REQUIRED = {
    "GET /api/v1/facebook/capabilities",
    "PATCH /api/v1/facebook/posts/{post_id}",
    "DELETE /api/v1/facebook/posts/{post_id}",
}
FILES = {".env.local": "http://localhost:3000", ".env.production.local": "https://www.genzee.video"}


async def grant(frontend_root, apply=False):
    from main import app

    logging.disable(logging.CRITICAL)
    valid = {f"{method.upper()} {route}" for route, methods in app.openapi()["paths"].items() for method in methods}
    if REQUIRED - valid:
        raise ValueError("Required P1 routes are not registered")
    summary = []
    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                for filename, origin in FILES.items():
                    raw = (dotenv_values(frontend_root / filename).get("GATEWAY_API_KEY") or "").strip()
                    if not raw:
                        raise ValueError("Existing app credential is missing")
                    key = (await db.execute(select(AccessAPIKey).where(
                        AccessAPIKey.hashed_key == hash_key(raw), AccessAPIKey.is_active.is_(True),
                    ))).scalar_one_or_none()
                    if not key or not key.authorized_app_id:
                        raise ValueError("Existing app key was not found")
                    registered = (await db.execute(select(AuthorizedApp).where(
                        AuthorizedApp.id == key.authorized_app_id,
                    ).with_for_update())).scalar_one()
                    owner = await db.get(User, registered.owner_user_id)
                    if (registered.allowed_origin != origin or not registered.is_active or
                            not registered.is_approved or not owner or not owner.is_active or
                            not owner.api_access_enabled):
                        raise ValueError("Existing app authorization cannot be changed")
                    previous = set(json.loads(registered.allowed_endpoints_json or "[]"))
                    added = REQUIRED - previous
                    if apply:
                        registered.allowed_endpoints_json = json.dumps(sorted(previous | REQUIRED))
                    summary.append({"app_id": registered.id, "origin": origin,
                                    "added": sorted(added), "applied": apply})
                if not apply:
                    await db.rollback()
            print(json.dumps(summary))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    try:
        asyncio.run(grant(arguments.frontend_root, arguments.apply))
    except Exception:
        print("P1 grant verification failed; no credentials were printed.")
        raise SystemExit(1) from None
