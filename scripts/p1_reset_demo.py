"""Reset demo database (Part 2B).

Drops and re-ensures indexes for all operational collections:
(events, diagnoses, decisions, policy verdicts, executions, verifications,
 charge_attempts, customer_vault, orchestrator_runs, demo_runs, paypal_events, sim clock).

Refuses unless target database is in {"vasooli_demo", "vasooli_regress"}
AND PayPal env is sandbox.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.routes.demo import ALLOWED_DEMO_DBS, reset_demo_database


async def reset_demo(target_db: str | None = None) -> int:
    settings = get_settings()
    db_name = target_db or os.environ.get("MONGODB_DB_NAME") or settings.mongodb_db_name

    if db_name not in ALLOWED_DEMO_DBS:
        print(
            f"REFUSAL: Target database {db_name!r} is not in allowlist {sorted(ALLOWED_DEMO_DBS)}!",
            file=sys.stderr,
        )
        return 1

    if settings.paypal_environment != "sandbox":
        print(
            f"REFUSAL: PayPal environment is {settings.paypal_environment!r}, must be 'sandbox'!",
            file=sys.stderr,
        )
        return 1

    print(f"Connecting to MongoDB at {settings.mongodb_uri} (db: {db_name})...")
    client = AsyncIOMotorClient(settings.mongodb_uri)
    try:
        db = client[db_name]
        from app.db import connect_to_mongo, close_mongo_connection
        # Connect the app layer so the helper stores can bind
        os.environ["MONGODB_DB_NAME"] = db_name
        await connect_to_mongo()
        try:
            print(f"Executing reset_demo_database on {db_name}...")
            res = await reset_demo_database(db)
            print(f"SUCCESS: Reset {db_name} completed.")
            print(f"Dropped collections: {', '.join(res['dropped_collections'])}")
            print("All indexes re-ensured and sim clock reset.")
            return 0
        finally:
            await close_mongo_connection()
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset demo database collections and indexes.")
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Target database name (must be in {'vasooli_demo', 'vasooli_regress'})",
    )
    args = parser.parse_args()
    return asyncio.run(reset_demo(args.db))


if __name__ == "__main__":
    sys.exit(main())
