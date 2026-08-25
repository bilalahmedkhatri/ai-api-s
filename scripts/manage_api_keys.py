"""
scripts/manage_api_keys.py — CLI tool to generate, list, and revoke third-party AccessAPIKeys.

Usage:
  python scripts/manage_api_keys.py create --name "Acme Web App" [--domain "acme.com"]
  python scripts/manage_api_keys.py list
  python scripts/manage_api_keys.py revoke --id 1
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add project root directory to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.database import AsyncSessionLocal, init_db
from app.services.api_key_service import generate_api_key, list_api_keys, revoke_api_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def run_create(client_name: str, allowed_domain: str | None):
    await init_db()
    async with AsyncSessionLocal() as db:
        raw_key, record = await generate_api_key(
            db=db, client_name=client_name, allowed_domain=allowed_domain
        )
        print("\n=======================================================")
        print("         AccessAPIKey Created Successfully             ")
        print("=======================================================")
        print(f"  ID:             {record.id}")
        print(f"  Client Name:    {record.client_name}")
        print(f"  Allowed Domain: {record.allowed_domain or 'Any (*)'}")
        print(f"  Key Prefix:     {record.key_prefix}")
        print("-------------------------------------------------------")
        print(f"  RAW SECRET KEY: {raw_key}")
        print("  (STORE THIS KEY SECURELY! IT WILL NOT BE SHOWN AGAIN)")
        print("=======================================================\n")


async def run_list():
    await init_db()
    async with AsyncSessionLocal() as db:
        keys = await list_api_keys(db)
        print("\n==================================================================================================")
        print(" ID  | Prefix        | Client Name                  | Domain             | Status   | Created At")
        print("--------------------------------------------------------------------------------------------------")
        if not keys:
            print(" No AccessAPIKeys found in database.")
        for k in keys:
            status_str = "ACTIVE" if k.is_active else "REVOKED"
            domain_str = k.allowed_domain or "*"
            created_str = k.created_at.strftime("%Y-%m-%d %H:%M") if k.created_at else "-"
            print(f" {k.id:<3} | {k.key_prefix:<13} | {k.client_name:<28} | {domain_str:<18} | {status_str:<8} | {created_str}")
        print("==================================================================================================\n")


async def run_revoke(key_id: int):
    await init_db()
    async with AsyncSessionLocal() as db:
        success = await revoke_api_key(db, key_id)
        if success:
            print(f"\nSuccessfully revoked AccessAPIKey ID={key_id}\n")
        else:
            print(f"\nError: AccessAPIKey ID={key_id} not found.\n")


def main():
    parser = argparse.ArgumentParser(description="Manage Third-Party AccessAPIKeys for AI Gateway")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create
    create_parser = subparsers.add_parser("create", help="Create a new AccessAPIKey")
    create_parser.add_argument("--name", required=True, help="Client application / website name")
    create_parser.add_argument("--domain", default=None, help="Domain restriction e.g. client.com (optional)")

    # list
    subparsers.add_parser("list", help="List all AccessAPIKeys")

    # revoke
    revoke_parser = subparsers.add_parser("revoke", help="Revoke/deactivate an AccessAPIKey")
    revoke_parser.add_argument("--id", type=int, required=True, help="AccessAPIKey ID to revoke")

    args = parser.parse_args()

    if args.command == "create":
        asyncio.run(run_create(args.name, args.domain))
    elif args.command == "list":
        asyncio.run(run_list())
    elif args.command == "revoke":
        asyncio.run(run_revoke(args.id))


if __name__ == "__main__":
    main()
