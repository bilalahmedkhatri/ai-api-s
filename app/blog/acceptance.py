"""Explicit, bounded live blog acceptance. Never imported/run by regression tests.
Run only after authorizing test rows/uploads; leaves labelled posts archived.
"""

import argparse
import asyncio
import io
import json
import logging
import os
import secrets
import struct
import subprocess
import tempfile
import uuid
import wave
import zlib
from datetime import timedelta
from pathlib import Path

import httpx
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.blog.config import APP_KEYS, load_registry
from app.blog.database import session_for_app
from app.blog.schema import blog_assets, blog_categories, blog_posts
from app.db.database import AsyncSessionLocal, engine
from app.models.dashboard import DashboardSession, Membership
from app.models.db_models import User
from app.services.dashboard import audit, digest, now

REPORT = Path(__file__).with_name("phase6-verification.json")


def fixtures():
    def chunk(kind, data):
        return (
            struct.pack("!I", len(data))
            + kind
            + data
            + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0\0\0\0"))
        + chunk(b"IEND", b"")
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 8000, 800, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 800)
    with tempfile.TemporaryDirectory(prefix="blog-acceptance-") as directory:
        video = Path(directory) / "test.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=16x16:r=5",
                "-t",
                "0.4",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(video),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        return [
            ("image/png", "test.png", png, "customImage"),
            ("audio/wav", "test.wav", buffer.getvalue(), "customAudio"),
            ("video/mp4", "test.mp4", video.read_bytes(), "customVideo"),
        ]


async def run(keep_preview, only_app=None, preview_only=False):
    from main import app

    logging.getLogger("httpx").setLevel(logging.WARNING)
    registry = load_registry()
    run_id = now().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    report = {
        "run_id": run_id,
        "started_at": now().isoformat(),
        "scope": "configured local checkout and configured databases/B2; not deployment",
        "apps": {},
        "checks": {},
        "session_revoked": False,
        "published_test_posts": False,
    }
    if preview_only:
        previous = json.loads(REPORT.read_text())
        Path(__file__).with_name("phase6-resume-attempt.json").write_text(
            json.dumps(previous, indent=2) + "\n"
        )
        report = previous
        run_id = report["run_id"]
        report["previous_harness_error"] = report.pop("error_type", None)
        report["status"] = "browser_acceptance_running"
        report["session_revoked"] = False
    token = secrets.token_urlsafe(48)
    tracked = (
        [
            (key, identifier)
            for key, details in report["apps"].items()
            for identifier in details.get("post_ids", [])
        ]
        if preview_only
        else []
    )
    session_id = None
    servers = []
    tasks = []

    def save():
        REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def check(name, condition):
        report["checks"][name] = bool(condition)
        save()
        if not condition:
            raise RuntimeError("Acceptance assertion: " + name)
        print("PASS " + name, flush=True)

    try:
        async with AsyncSessionLocal() as db:
            member = (
                (
                    await db.execute(
                        sa.select(Membership)
                        .join(User, Membership.user_id == User.id)
                        .where(
                            Membership.role == "admin",
                            Membership.status == "approved",
                            User.is_active.is_(True),
                        )
                        .order_by(Membership.id)
                    )
                )
                .scalars()
                .first()
            )
            if member is None or member.access_expires_at and member.access_expires_at <= now():
                raise RuntimeError("No eligible approved administrator")
            session = DashboardSession(
                token_hash=digest(token),
                membership_id=member.id,
                created_at=now(),
                expires_at=now() + timedelta(minutes=20),
            )
            db.add(session)
            await audit(db, member, "BLOG_ACCEPTANCE_START", run_id)
            await db.commit()
            session_id = session.id
            report["session_id"] = session_id
            report["author_membership_id"] = member.id
            expected_author = member.name
            expected_designation = member.designation
            save()
        headers = {"Authorization": "Bearer " + token, "Origin": os.environ["DASHBOARD_ORIGIN"]}
        media = await asyncio.to_thread(fixtures)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://acceptance",
            headers=headers,
            timeout=160,
        ) as client:

            async def call(method, path, **kwargs):
                response = await client.request(method, "/api/v1/dashboard/blog" + path, **kwargs)
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"{method} {path.split('?')[0]} returned {response.status_code}"
                    )
                return response.json()

            async def create(key, body):
                row = (await call("POST", f"/apps/{key}/posts", json=body))["post"]
                tracked.append((key, row["id"]))
                report["apps"][key]["post_ids"].append(row["id"])
                save()
                return row

            check(
                "missing_session_denied",
                (
                    await client.get("/api/v1/dashboard/blog/apps", headers={"Authorization": ""})
                ).status_code
                == 401,
            )
            check(
                "foreign_origin_denied",
                (
                    await client.post(
                        "/api/v1/dashboard/blog/profile",
                        headers={"Origin": "https://invalid.example"},
                        json={},
                    )
                ).status_code
                in (403, 405),
            )
            for target in registry.targets:
                if preview_only:
                    continue
                if only_app and target.key != only_app:
                    continue
                if not target.ready:
                    report["apps"][target.key] = {"status": "unconfigured_skipped"}
                    save()
                    continue
                key = target.key
                report["apps"][key] = {"post_ids": [], "asset_ids": [], "status": "running"}
                async with session_for_app(registry, key) as db:
                    report["apps"][key]["before"] = {
                        table.name: (
                            await db.execute(sa.select(sa.func.count()).select_from(table))
                        ).scalar_one()
                        for table in (blog_posts, blog_categories, blog_assets)
                    }
                chosen = media if key == "azeemlab" else [media[1]]
                nodes = []
                for mime, filename, data, kind in chosen:
                    upload_token = str(uuid.uuid4())
                    response = await client.post(
                        f"/api/v1/dashboard/blog/apps/{key}/assets",
                        params={"filename": "acceptance-" + run_id + "-" + filename},
                        headers={"Content-Type": mime, "Idempotency-Key": upload_token},
                        content=data,
                    )
                    check(key + "_" + mime + "_upload", response.status_code == 201)
                    asset = response.json()["asset"]
                    report["apps"][key]["asset_ids"].append(asset["id"])
                    save()
                    check(
                        key + "_" + mime + "_object_scope",
                        asset["object_key"].startswith("blogs/" + key + "/"),
                    )
                    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as public:
                        download = await public.get(asset["public_url"])
                        check(
                            key + "_" + mime + "_public_bytes",
                            download.status_code == 200 and download.content == data,
                        )
                    retry = await client.post(
                        f"/api/v1/dashboard/blog/apps/{key}/assets",
                        params={"filename": "acceptance-" + run_id + "-" + filename},
                        headers={"Content-Type": mime, "Idempotency-Key": upload_token},
                        content=data,
                    )
                    check(
                        key + "_" + mime + "_retry",
                        retry.status_code == 201
                        and retry.json().get("reused") is True
                        and retry.json()["asset"]["id"] == asset["id"],
                    )
                    nodes.append({"type": kind, "attrs": {"asset_id": asset["id"]}})
                document = {
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Automated Phase 6 acceptance test "
                                    + run_id
                                    + ". Do not publish.",
                                }
                            ],
                        }
                    ]
                    + nodes,
                }
                body = {
                    "title": "[ACCEPTANCE TEST] " + run_id,
                    "category_name": "Acceptance Test " + run_id,
                    "content_json": document,
                    "locale": "en",
                }
                post = await create(key, body)
                check(
                    key + "_author_snapshot",
                    post["author_name"] == expected_author
                    and post["author_designation"] == expected_designation,
                )
                check(
                    key + "_draft_metadata",
                    post["status"] == "draft"
                    and not post["metadata_json"]["robots"]["index"]
                    and post["metadata_json"]["canonical_url"].startswith(
                        target.public_blog_url + "/"
                    ),
                )
                loaded = (await call("GET", f"/apps/{key}/posts/{post['id']}"))["post"]
                check(
                    key + "_roundtrip",
                    loaded["content_json"] == post["content_json"] and loaded["version"] == 1,
                )
                listing = await call("GET", f"/apps/{key}/posts?search={run_id}")
                check(
                    key + "_actual_list_contract",
                    len(listing["items"]) == 1 and listing["items"][0]["id"] == post["id"],
                )
                cats = await call("GET", f"/apps/{key}/categories?search={run_id}")
                check(
                    key + "_category_roundtrip",
                    len(cats["items"]) == 1 and cats["items"][0]["id"] == post["category_id"],
                )
                results = await asyncio.gather(
                    *(
                        client.patch(
                            f"/api/v1/dashboard/blog/apps/{key}/posts/{post['id']}",
                            json={"version": 1, "excerpt": "Acceptance concurrent edit " + str(i)},
                        )
                        for i in (1, 2)
                    )
                )
                check(
                    key + "_postgres_concurrent_version",
                    sorted(r.status_code for r in results) == [200, 409],
                )
                if key == "azeemlab":
                    copies = await asyncio.gather(
                        *(
                            create(key, {**body, "title": "[ACCEPTANCE COLLISION] " + run_id})
                            for _ in range(2)
                        )
                    )
                    check(
                        "postgres_concurrent_slug_collision",
                        len({row["slug"] for row in copies}) == 2,
                    )
                    for row in copies:
                        check(
                            "postgres_category_reuse_" + str(row["id"]),
                            row["category_id"] == post["category_id"],
                        )
                report["apps"][key]["status"] = "passed"
                save()
            # Keep the real app and test-only cookie bridge running only during browser acceptance.
            if keep_preview:
                bridge = FastAPI()

                @bridge.get("/session")
                async def preview_session():
                    import base64
                    from urllib.parse import quote

                    response = RedirectResponse("http://localhost:3014/blog-editor")
                    response.set_cookie(
                        "gateway_dashboard_session",
                        quote(base64.b64encode(json.dumps(token).encode()).decode()),
                        httponly=True,
                        samesite="lax",
                        max_age=600,
                        path="/blog-editor",
                    )
                    response.headers["Cache-Control"] = "no-store"
                    return response

                for application, port in ((app, 8016), (bridge, 8017)):
                    server = uvicorn.Server(
                        uvicorn.Config(
                            application,
                            host="127.0.0.1",
                            port=port,
                            lifespan="off",
                            access_log=False,
                            log_config=None,
                            log_level="warning",
                        )
                    )
                    servers.append(server)
                    tasks.append(asyncio.create_task(server.serve()))
                while not all(server.started for server in servers):
                    if any(task.done() for task in tasks):
                        raise RuntimeError("Preview startup failed")
                    await asyncio.sleep(0.1)
                print(
                    "PREVIEW_READY backend=8016 bridge=8017; send finish to archive/revoke",
                    flush=True,
                )
                marker = Path(__file__).with_name(".phase6-stop")
                if marker.exists():
                    marker.unlink()
                async with asyncio.timeout(900):
                    while not marker.exists():
                        await asyncio.sleep(0.5)
                marker.unlink()
    except BaseException as error:
        report["error_type"] = type(error).__name__
        report["status"] = "failed"
        save()
        print("Acceptance interrupted/failed error_type=" + type(error).__name__, flush=True)
        raise
    finally:
        # Archive only the exact posts created by this run; never delete existing records.
        report["archive_results"] = []
        for key, identifier in tracked:
            try:
                async with session_for_app(registry, key) as db:
                    result = await db.execute(
                        blog_posts.update()
                        .where(blog_posts.c.id == identifier, blog_posts.c.title.contains(run_id))
                        .values(
                            status="archived", updated_at=now(), version=blog_posts.c.version + 1
                        )
                        .returning(blog_posts.c.id)
                    )
                    archived = result.scalar_one_or_none() == identifier
                    await db.commit()
                    report["archive_results"].append(
                        {"app": key, "id": identifier, "archived": archived}
                    )
            except Exception as error:
                report["archive_results"].append(
                    {"app": key, "id": identifier, "error_type": type(error).__name__}
                )
            save()
        if session_id is not None:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    sa.update(DashboardSession)
                    .where(DashboardSession.id == session_id)
                    .values(revoked_at=now())
                )
                await db.commit()
                report["session_revoked"] = True
        for server in servers:
            server.should_exit = True
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        report["finished_at"] = now().isoformat()
        if "error_type" not in report:
            report["status"] = (
                "passed"
                if all(row.get("archived") for row in report["archive_results"])
                else "cleanup_incomplete"
            )
        save()
        await engine.dispose()
        print("REPORT " + str(REPORT) + " status=" + report["status"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", required=True)
    parser.add_argument("--keep-preview", action="store_true")
    parser.add_argument("--app", choices=APP_KEYS)
    parser.add_argument("--preview-only", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("DASHBOARD_ORIGIN", "http://localhost:3014")
    if args.app:
        REPORT = Path(__file__).with_name("phase6-resume-verification.json")
    asyncio.run(run(args.keep_preview, args.app, args.preview_only))
