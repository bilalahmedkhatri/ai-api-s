"""Explicit stale orphan reconciliation, never a blog/registered-asset deletion API."""

import argparse
import asyncio
import re
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.blog.config import APP_KEYS, load_registry
from app.blog.database import session_for_app
from app.blog.media import lookup
from app.blog.storage import storage_configuration


async def reconcile(app_key, object_key, delete=False):
    if not re.fullmatch(
        r"blogs/" + re.escape(app_key) + r"/[0-9a-f-]{36}\.(png|jpg|webp|mp4|webm|mp3|wav|ogg)",
        object_key,
    ):
        raise ValueError("Use the exact app-scoped object key from a cleanup log.")
    storage = storage_configuration()
    async with session_for_app(load_registry(), app_key) as db:
        await db.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
        await db.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))").bindparams(
                key=object_key
            )
        )
        if await lookup(db, object_key):
            return {"status": "registered_preserved"}
        async with storage.client() as client:
            head = await client.head_object(Bucket=storage.bucket, Key=object_key)
            modified = head.get("LastModified")
            if modified is None or modified > datetime.now(UTC) - timedelta(hours=24):
                return {"status": "recent_preserved"}
            if not delete:
                return {"status": "stale_orphan", "delete_requested": False}
            version = head.get("VersionId")
            kwargs = {"VersionId": version} if version else {}
            await client.delete_object(Bucket=storage.bucket, Key=object_key, **kwargs)
            return {"status": "stale_orphan_deleted"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, choices=APP_KEYS)
    parser.add_argument("--object-key", required=True)
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete only an unregistered object older than 24 hours.",
    )
    args = parser.parse_args()
    try:
        print(asyncio.run(reconcile(args.app, args.object_key, args.delete)))
    except Exception as error:
        print({"status": "failed", "error_type": type(error).__name__})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
