"""Additional live race/rollback checks using the bounded acceptance session bridge."""

import asyncio
import base64
import json
import uuid
from pathlib import Path
from urllib.parse import unquote

import httpx


async def main():
    report = json.loads(Path("app/blog/phase6-resume-verification.json").read_text())
    run = report["run_id"]
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        bridge = await client.get("http://localhost:8017/session")
        token = json.loads(
            base64.b64decode(unquote(bridge.cookies.get("gateway_dashboard_session")))
        )
        headers = {"Authorization": "Bearer " + token, "Origin": "http://localhost:3014"}
        base = "http://127.0.0.1:8016/api/v1/dashboard/blog"
        checks = {}
        denied = await client.patch(
            base + "/profile",
            headers=headers | {"Origin": "https://invalid.example"},
            json={"designation": "must-not-save"},
        )
        checks["actual_patch_origin_denied"] = denied.status_code == 403
        category = "Acceptance Race " + run + " " + uuid.uuid4().hex[:8]
        rows = await asyncio.gather(
            *(
                client.post(
                    base + "/apps/genzee_video/categories", headers=headers, json={"name": category}
                )
                for _ in range(2)
            )
        )
        checks["postgres_category_create_race"] = (
            sorted(row.status_code for row in rows) == [200, 201]
            and len({row.json()["category"]["id"] for row in rows}) == 1
        )
        data = Path("admin/output/phase6/test.png").read_bytes()
        upload_headers = headers | {
            "Content-Type": "image/png",
            "Idempotency-Key": str(uuid.uuid4()),
        }
        filename = "acceptance-concurrent-" + run + ".png"
        assets = await asyncio.gather(
            *(
                client.post(
                    base + "/apps/genzee_video/assets",
                    params={"filename": filename},
                    headers=upload_headers,
                    content=data,
                )
                for _ in range(2)
            )
        )
        checks["postgres_b2_upload_race"] = (
            all(row.status_code == 201 for row in assets)
            and len({row.json()["asset"]["id"] for row in assets}) == 1
            and sorted(row.json()["reused"] for row in assets) == [False, True]
        )
        asset_id = assets[0].json()["asset"]["id"]
        rollback_category = "Acceptance Rollback " + run
        response = await client.post(
            base + "/apps/bondsgame/posts",
            headers=headers,
            json={
                "title": "[ACCEPTANCE TEST] " + run,
                "category_name": rollback_category,
                "cover_asset_id": asset_id,
                "content_json": {"type": "doc", "content": []},
            },
        )
        cats = await client.get(
            base + "/apps/bondsgame/categories",
            params={"search": rollback_category},
            headers=headers,
        )
        checks["foreign_asset_and_category_rollback"] = (
            response.status_code == 422 and cats.json()["total"] == 0
        )
        response = await client.delete(
            base + "/apps/genzee_video/posts/" + str(report["apps"]["genzee_video"]["post_ids"][0]),
            headers=headers,
        )
        checks["admin_delete_denied"] = response.status_code == 405
        result = {
            "run_id": run,
            "checks": checks,
            "additional_genzee_video_asset_id": asset_id,
            "additional_genzee_video_category_id": rows[0].json()["category"]["id"],
        }
        Path("app/blog/phase6-extra-verification.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf8"
        )
        for name, value in checks.items():
            print(("PASS " if value else "FAIL ") + name, flush=True)
        if not all(checks.values()):
            raise RuntimeError("Additional acceptance failed")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(main())
