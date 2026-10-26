"""Fence/archive acceptance rows and consolidate read-only evidence after preview shutdown."""

import argparse
import asyncio
import json
from pathlib import Path

import sqlalchemy as sa

from app.blog import service
from app.blog.config import load_registry
from app.blog.contracts import PostUpdate
from app.blog.database import session_for_app
from app.blog.schema import blog_assets, blog_categories, blog_posts
from app.blog.storage import storage_configuration
from app.db.database import AsyncSessionLocal, engine
from app.models.dashboard import DashboardSession
from app.services.dashboard import now

BASE = Path(__file__).parent


async def main():
    first = json.loads((BASE / "phase6-first-run.json").read_text())
    last = json.loads((BASE / "phase6-resume-verification.json").read_text())
    extra = json.loads((BASE / "phase6-extra-verification.json").read_text())
    registry = load_registry()
    result = {
        "completed_at": now().isoformat(),
        "scope": "local acceptance with configured databases/B2; deployment excluded by user",
        "apps": {},
        "checks": {},
        "prior_interrupted_attempt_preserved": "phase6-first-run.json",
        "published_test_posts": False,
        "legacy_import_performed": False,
    }
    for name, value in first["checks"].items():
        if not name.startswith("genzee_video_"):
            result["checks"][name] = value
    result["checks"].update(last["checks"])
    result["checks"].update(extra["checks"])
    result["browser"] = json.loads(
        Path("admin/output/phase6/browser-verification.json").read_text()
    )
    for key in (
        "actual_contract_loaded",
        "real_browser_image_upload",
        "real_browser_save",
        "archived_status_preserved",
        "media_retention_verified",
        "app_switching_isolated",
    ):
        result["checks"][key] = result["browser"][key]
    result["checks"]["mobile_no_horizontal_overflow"] = not result["browser"]["horizontal_overflow"]
    assets_to_verify = []
    for target in registry.targets:
        if not target.ready:
            result["apps"][target.key] = {"status": "unconfigured_skipped"}
            continue
        evidence = (last if target.key in last["apps"] else first)["apps"][target.key]
        ids = evidence.get("post_ids", [])
        asset_ids = set(evidence.get("asset_ids", []))
        if target.key == "genzee_video":
            asset_ids.add(extra["additional_genzee_video_asset_id"])
        async with session_for_app(registry, target.key) as db:
            records = []
            for identifier in ids:
                post = await service.find_post(db, identifier)
                if not (
                    "[ACCEPTANCE" in post["title"]
                    and (first["run_id"] in post["title"] or last["run_id"] in post["title"])
                ):
                    raise RuntimeError("Refusing to alter a record outside this acceptance run")
                # Use the same service to keep archived metadata/timestamps consistent.
                saved = await service.update_post(
                    db,
                    target,
                    identifier,
                    PostUpdate(version=post["version"], status="archived"),
                    post,
                )
                await db.commit()
                for node in service.document_nodes(saved["content_json"]):
                    if node.get("attrs", {}).get("asset_id"):
                        asset_ids.add(node["attrs"]["asset_id"])
                assert (
                    saved["status"] == "archived"
                    and saved["metadata_json"]["robots"]["index"] is False
                )
                assert saved["metadata_json"]["author"]["name"] == saved["author_name"]
                records.append(
                    {"id": identifier, "status": saved["status"], "version": saved["version"]}
                )
                if target.key == "genzee_video":
                    types = [node["type"] for node in service.document_nodes(saved["content_json"])]
                    result["checks"]["real_browser_save_persisted"] = (
                        saved["excerpt"]
                        == "Phase 6 corrected browser media retention and archived save verified."
                    )
                    result["checks"]["real_browser_media_roundtrip"] = (
                        "customAudio" in types and "customImage" in types
                    )
            rows = (
                (await db.execute(sa.select(blog_assets).where(blog_assets.c.id.in_(asset_ids))))
                .mappings()
                .all()
            )
            assert len(rows) == len(asset_ids)
            assets_to_verify.extend(dict(row) for row in rows)
            counts = {
                table.name: (
                    await db.execute(sa.select(sa.func.count()).select_from(table))
                ).scalar_one()
                for table in (blog_posts, blog_categories, blog_assets)
            }
            result["apps"][target.key] = {
                "status": "passed",
                "test_posts": records,
                "verified_test_asset_ids": sorted(asset_ids),
                "table_counts": counts,
            }
    config = storage_configuration()
    async with config.client() as client:
        heads = await asyncio.gather(
            *(
                client.head_object(Bucket=config.bucket, Key=asset["object_key"])
                for asset in assets_to_verify
            )
        )
        result["checks"]["all_retained_assets_exist_in_b2"] = all(
            head["ContentLength"] == asset["size_bytes"]
            and head["ContentType"] == asset["media_type"]
            for asset, head in zip(assets_to_verify, heads, strict=True)
        )
    session_ids = {21, 22, 23, last["session_id"]}
    async with AsyncSessionLocal() as db:
        sessions = (
            await db.execute(
                sa.select(DashboardSession.id, DashboardSession.revoked_at).where(
                    DashboardSession.id.in_(session_ids)
                )
            )
        ).all()
        result["checks"]["all_acceptance_sessions_revoked"] = len(sessions) == len(
            session_ids
        ) and all(row.revoked_at is not None for row in sessions)
        result["verified_revoked_session_ids"] = sorted(session_ids)
    result["checks"]["all_test_posts_archived"] = all(
        row["status"] == "archived"
        for app in result["apps"].values()
        for row in app.get("test_posts", [])
    )
    result["status"] = "passed" if all(result["checks"].values()) else "failed"
    (BASE / "phase6-verification.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf8"
    )
    await engine.dispose()
    print("Phase 6 consolidated status:", result["status"])
    print("Checks:", len(result["checks"]))
    print("Verified retained assets:", len(assets_to_verify))
    if result["status"] != "passed":
        raise RuntimeError("Acceptance verification incomplete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(main())
