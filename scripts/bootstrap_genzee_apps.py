"""Approve GenZee gateway apps and save their keys without printing secrets."""

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import uuid
from pathlib import Path

from app.db.database import AsyncSessionLocal, engine
from app.models.db_models import AccessAPIKey, AuthorizedApp, User
from app.services.api_key_service import generate_api_key, hash_key
from dotenv import dotenv_values
from sqlalchemy import select

GENZEE_ENDPOINTS = [
    "GET /api/v1/audio/models",
    "GET /api/v1/audio/voices",
    "GET /api/v1/audio/sample",
    "POST /api/v1/audio/tts",
    "GET /api/v1/audio/tts/status/{job_id}",
    "POST /api/v1/audio/tts/retry/{job_id}",
    "GET /api/v1/audio/tts/stream/{job_id}",
    "POST /api/v1/audio/tts/renew-url",
    "POST /api/v1/facebook/store-token",
    "POST /api/v1/facebook/post-pages",
    "DELETE /api/v1/facebook/pages/{page_id}",
    "POST /api/v1/media/process-urls",
    "GET /api/v1/media/{item_id}",
    "DELETE /api/v1/media/",
]
ENVIRONMENTS = {
    "local": {"name": "GenZee local", "origin": "http://localhost:3000", "api_url": "http://localhost:8000", "file": ".env.local"},
    "production": {"name": "GenZee production", "origin": "https://www.genzee.video", "api_url": "https://ai-api.fastapicloud.dev", "file": ".env.production.local"},
}


async def configure_apps(db, owner_email: str, existing_keys: dict[str, str], valid_endpoints: set[str]):
    if set(GENZEE_ENDPOINTS) - valid_endpoints:
        raise ValueError("GenZee permissions do not match registered gateway routes")
    owner = (await db.execute(select(User).where(User.email == owner_email))).scalar_one_or_none()
    if not owner or not owner.is_active:
        raise ValueError("Sign in with an active gateway Google owner before onboarding apps")
    apps = {}
    # Validate ownership before granting access or modifying any registration.
    for environment, config in ENVIRONMENTS.items():
        matches = (await db.execute(select(AuthorizedApp).where(
            AuthorizedApp.allowed_origin == config["origin"]
        ))).scalars().all()
        if len(matches) > 1 or (matches and matches[0].owner_user_id != owner.id):
            raise ValueError("Existing origin registration has a different or ambiguous gateway owner")
        apps[environment] = matches[0] if matches else AuthorizedApp(
            name=config["name"], owner_user_id=owner.id, allowed_origin=config["origin"]
        )
    owner.api_access_enabled = True
    for registered in apps.values():
        registered.is_active = True
        registered.is_approved = True
        registered.allowed_endpoints_json = json.dumps(GENZEE_ENDPOINTS)
        db.add(registered)
    await db.commit()
    credentials = {}
    created_key_ids = []
    for environment, registered in apps.items():
        raw = existing_keys.get(environment, "").strip()
        key = None
        if raw:
            key = (await db.execute(select(AccessAPIKey).where(
                AccessAPIKey.hashed_key == hash_key(raw),
                AccessAPIKey.authorized_app_id == registered.id,
                AccessAPIKey.is_active.is_(True),
            ))).scalar_one_or_none()
        if key is None:
            raw, key = await generate_api_key(db, registered.name, registered.id)
            created_key_ids.append(key.id)
        credentials[environment] = {"app": registered, "key_id": key.id, "raw_key": raw}
    return owner, credentials, created_key_ids


def environment_content(content: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        line = name + "=" + json.dumps(value)
        pattern = rf"(?m)^{re.escape(name)}\s*=.*$"
        if re.search(pattern, content):
            content = re.sub(pattern, lambda _, replacement=line: replacement, content)
        else:
            content = content.rstrip("\r\n") + "\n" + line + "\n"
    return content


def write_environment(path: Path, values: dict[str, str]) -> None:
    content = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(environment_content(content, values), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


async def main(owner_email: str, frontend_root: Path):
    from main import app

    logging.disable(logging.CRITICAL)
    root = frontend_root.resolve(strict=True)
    if not (root / "package.json").is_file():
        raise ValueError("Frontend root must be the intended Next.js project")
    files = {environment: root / config["file"] for environment, config in ENVIRONMENTS.items()}
    for path in files.values():
        check = subprocess.run(["git", "check-ignore", "--quiet", str(path)], cwd=root, check=False)
        if check.returncode != 0:
            raise ValueError("Credential environment files must be git-ignored before onboarding")
    existing = {environment: (dotenv_values(path).get("GATEWAY_API_KEY") or "") if path.exists() else "" for environment, path in files.items()}
    valid = {f"{method.upper()} {path}" for path, methods in app.openapi()["paths"].items() for method in methods}
    try:
        async with AsyncSessionLocal() as db:
            owner, credentials, created = await configure_apps(db, owner_email, existing, valid)
            try:
                for environment, credential in credentials.items():
                    config = ENVIRONMENTS[environment]
                    write_environment(files[environment], {
                        "GATEWAY_API_KEY": credential["raw_key"],
                        "GATEWAY_APP_ORIGIN": config["origin"],
                        "VOICEOVER_API_URL": config["api_url"],
                    })
            except Exception:
                # Never leave a newly issued, unsaved secret usable after failure.
                for identifier in created:
                    key = await db.get(AccessAPIKey, identifier)
                    key.is_active = False
                await db.commit()
                raise
            print(json.dumps({
                "owner_user_id": owner.id, "api_access_enabled": owner.api_access_enabled,
                "apps": [{"environment": environment, "app_id": credential["app"].id,
                          "origin": ENVIRONMENTS[environment]["origin"], "key_id": credential["key_id"],
                          "endpoint_grants": len(GENZEE_ENDPOINTS), "environment_file": str(files[environment])}
                         for environment, credential in credentials.items()],
                "new_keys_created": len(created),
            }, indent=2))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--frontend-root", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(main(args.owner_email, args.frontend_root))
