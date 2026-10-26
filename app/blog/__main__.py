"""Offline status by default; --check explicitly performs read-only connections."""

import argparse
import asyncio
import json

from app.blog.config import BlogConfigurationError, load_registry
from app.blog.probe import check_connections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check configured DBs read-only.")
    args = parser.parse_args()
    try:
        registry = load_registry()
    except BlogConfigurationError as error:
        print(str(error))
        return 1
    if args.check:
        results = asyncio.run(check_connections(registry))
        print(json.dumps(results, indent=2))
        return int(any(item["status"] == "failed" for item in results))
    for target in registry.targets:
        status = "configured (not connectivity-tested)" if target.ready else "missing DATABASE_URL"
        print(f"{target.key}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
