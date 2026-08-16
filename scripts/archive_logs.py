#!/usr/bin/env python3
"""scripts/archive_logs.py — Archive log files older than N days.

Compresses matching files with gzip and moves them to an archive/ subdirectory.
Run manually or via cron:

    # Daily at 02:00 — archive logs older than 30 days
    0 2 * * * /app/.venv/bin/python /app/scripts/archive_logs.py --days 30

Uses only stdlib: gzip, shutil, pathlib, argparse — no new deps.
"""

import argparse
import gzip
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def archive_logs(log_dir: Path, days: int, dry_run: bool = False) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    archive_dir = log_dir / "archive"

    if not dry_run:
        archive_dir.mkdir(exist_ok=True)

    archived = skipped = 0
    for path in sorted(log_dir.glob("*.jsonl*")):
        if path.suffix == ".gz":
            continue  # already compressed
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if mtime >= cutoff:
            skipped += 1
            continue

        dest = archive_dir / (path.name + ".gz")
        if dry_run:
            log.info("[DRY RUN] Would archive %s → %s", path.name, dest.name)
            archived += 1
            continue

        # Compress → move → remove original
        tmp = dest.with_suffix(".tmp")
        try:
            with path.open("rb") as src, gzip.open(tmp, "wb", compresslevel=6) as gz:
                shutil.copyfileobj(src, gz)
            tmp.rename(dest)
            path.unlink()
            log.info("Archived %s → %s (%.1f KB saved)",
                     path.name, dest.name,
                     (path.stat().st_size - dest.stat().st_size) / 1024
                     if dest.exists() else 0)
            archived += 1
        except Exception as exc:
            log.error("Failed to archive %s: %s", path.name, exc)
            tmp.unlink(missing_ok=True)

    log.info("Done. Archived=%d  Skipped=%d (newer than %d days)", archived, skipped, days)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Archive old JSON log files")
    parser.add_argument("--log-dir", default="logs", help="Path to log directory")
    parser.add_argument("--days", type=int, default=30, help="Archive files older than N days")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    args = parser.parse_args()

    archive_logs(Path(args.log_dir), args.days, args.dry_run)
