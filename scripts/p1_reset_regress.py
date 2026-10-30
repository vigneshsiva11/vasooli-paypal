"""Reset the vasooli_regress database for clean gate runs.

Refuses to drop any database other than 'vasooli_regress'.
Re-ensures collection indexes after drop.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings


async def reset_regress_db() -> int:
    settings = get_settings()
    env_db = os.environ.get("MONGODB_DB_NAME")
    settings_db = settings.mongodb_db_name

    target_db = env_db or settings_db

    if target_db != "vasooli_regress":
        print(
            f"REFUSAL: Target database is {target_db!r}. "
            "p1_reset_regress.py refuses to drop any database other than 'vasooli_regress'!",
            file=sys.stderr,
        )
        return 1

    client = AsyncIOMotorClient(settings.mongodb_uri)
    try:
        print(f"Dropping database: {target_db}...")
        await client.drop_database(target_db)
        print(f"Successfully dropped database: {target_db}.")

        # Re-ensure indexes
        from app.db import connect_to_mongo, close_mongo_connection
        from app.decision import ensure_indexes as ensure_decision_indexes
        from app.diagnosis import ensure_indexes as ensure_diagnosis_indexes
        from app.execution import ensure_indexes as ensure_execution_indexes
        from app.ingestion import ensure_indexes as ensure_event_indexes
        from app.policy import ensure_indexes as ensure_policy_indexes
        from app.ptp import ensure_indexes as ensure_promise_indexes
        from app.webhooks import ensure_indexes as ensure_verification_indexes
        from app.gateways.paypal.charge_attempts import ensure_indexes as ensure_charge_indexes
        from app.gateways.paypal.vault import ensure_indexes as ensure_vault_indexes
        from app.orchestrator.runs import ensure_orchestrator_indexes

        await connect_to_mongo()
        try:
            await ensure_diagnosis_indexes()
            await ensure_decision_indexes()
            await ensure_event_indexes()
            await ensure_policy_indexes()
            await ensure_execution_indexes()
            await ensure_promise_indexes()
            await ensure_verification_indexes()
            await ensure_charge_indexes()
            await ensure_vault_indexes()
            await ensure_orchestrator_indexes()
            from app.policy.rulebook_store import ensure_rulebook_indexes, invalidate_usd_cache
            from app.policy.gemini_rules import clear_preview_cache
            invalidate_usd_cache()
            clear_preview_cache()
            await ensure_rulebook_indexes()
            print("Successfully initialized indexes on vasooli_regress.")
        finally:
            await close_mongo_connection()

        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(reset_regress_db()))
