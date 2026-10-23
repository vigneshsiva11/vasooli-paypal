"""Integration check for PayPal webhook reconciliation against real MongoDB.

Runs exclusively against 'vasooli_regress' (refuses any other database name).
Zero PayPal API calls, zero HTTP server calls.
Tests:
1. Seed one event and one PayPal execution for invoice INV2-LSEJ-2LLG-D59L-2QNU.
2. Reconcile captured PARTIAL fixture:
   - Exactly one verification created with amount_mismatch=True.
   - Event remains open ('at_risk').
3. Reconcile captured FULL fixture:
   - Event transitions to 'recovered'.
4. Replaying both fixtures twice creates no duplicate records.
5. Direct duplicate insert of paypal_event_id raises DuplicateKeyError (unique index).
6. Metrics calculation for USD: recovered revenue is 100.00, NOT 110.00 (partial and
   full verifications are deduplicated per event; cumulative paid_amount decides).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.metrics.aggregate import summarize
from app.metrics.reader import (
    distinct_recoveries,
    load_snapshot,
    recovered_amount_per_event,
)
from app.webhooks import ensure_indexes as ensure_webhook_indexes
from app.webhooks.paypal_reconciler import reconcile_paypal_event

FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "paypal"


async def run_integration_check() -> int:
    settings = get_settings()
    env_db = os.environ.get("MONGODB_DB_NAME")
    target_db = env_db or settings.mongodb_db_name

    # Refuse any database name other than 'vasooli_regress'
    if target_db != "vasooli_regress":
        print(
            f"REFUSAL: Target database is {target_db!r}. "
            "p1_integration_reconciler.py refuses to run against any database other than 'vasooli_regress'!",
            file=sys.stderr,
        )
        return 1

    print("=" * 78)
    print("PAYPAL RECONCILIATION INTEGRATION CHECK (REAL MONGODB: vasooli_regress)")
    print("=" * 78)

    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client[target_db]

    try:
        # Load fixtures
        partial_path = FIXTURES_DIR / "invoicing_invoice_paid_partial.json"
        full_path = FIXTURES_DIR / "invoicing_invoice_paid_full.json"
        assert partial_path.exists(), f"Missing fixture: {partial_path}"
        assert full_path.exists(), f"Missing fixture: {full_path}"

        partial_fixture = json.loads(partial_path.read_text(encoding="utf-8"))
        full_fixture = json.loads(full_path.read_text(encoding="utf-8"))

        partial_event_id = partial_fixture["id"]
        full_event_id = full_fixture["id"]
        invoice_id = "INV2-LSEJ-2LLG-D59L-2QNU"

        print(f"Loaded fixtures: partial={partial_event_id}, full={full_event_id}")

        # Ensure collection indexes on vasooli_regress
        from app.db import connect_to_mongo, close_mongo_connection
        await connect_to_mongo()
        try:
            await ensure_webhook_indexes()
        finally:
            await close_mongo_connection()

        event_id = "evt_paypal_reconciler_integration_01"

        # Clean up any prior test data for this event / fixture
        await db.events.delete_many({"event_id": event_id})
        await db.executions.delete_many({"event_id": event_id})
        await db.verifications.delete_many({
            "$or": [
                {"event_id": event_id},
                {"paypal_event_id": {"$in": [partial_event_id, full_event_id]}},
            ]
        })
        await db.paypal_events.delete_many({
            "paypal_event_id": {"$in": [partial_event_id, full_event_id]}
        })

        # 1. Seed event and execution
        event_doc = {
            "event_id": event_id,
            "amount": 100.00,
            "currency": "USD",
            "status": "at_risk",
            "customer_id": "cust_paypal_integration_01",
            "created_at": datetime.now(timezone.utc),
        }
        await db.events.insert_one(event_doc)

        exec_id = ObjectId()
        exec_doc = {
            "_id": exec_id,
            "event_id": event_id,
            "gateway": "paypal",
            "gateway_ref": invoice_id,
            "action_type": "payment_link",
            "amount": 100.00,
            "currency": "USD",
            "status": "completed",
            "executed_at": datetime.now(timezone.utc),
        }
        await db.executions.insert_one(exec_doc)
        print("  [PASS] Seeded event and PayPal execution for invoice " + invoice_id)

        # 2. Reconcile PARTIAL fixture
        r_part = await reconcile_paypal_event(partial_fixture, db)
        assert r_part.processed is True, "Partial event not processed"
        assert r_part.matched is True, "Partial event not matched"
        assert r_part.duplicate is False, "Partial event marked duplicate unexpectedly"
        assert r_part.amount_recovered == 10.0, f"Expected recovered 10.0, got {r_part.amount_recovered}"

        v_part = await db.verifications.find_one({"paypal_event_id": partial_event_id})
        assert v_part is not None, "Verification record not created for partial payment"
        assert v_part["amount_mismatch"] is True, "amount_mismatch must be True for partial payment"

        ev_after_part = await db.events.find_one({"event_id": event_id})
        assert ev_after_part["status"] == "at_risk", (
            f"Event status must remain 'at_risk' after partial payment, got {ev_after_part['status']}"
        )
        print("  [PASS] Partial fixture reconciled: amount_mismatch=True, event remains at_risk")

        # 3. Replay PARTIAL fixture (idempotency)
        r_part_replay = await reconcile_paypal_event(partial_fixture, db)
        assert r_part_replay.duplicate is True, "Partial replay was not detected as duplicate"
        assert await db.verifications.count_documents({"event_id": event_id}) == 1, (
            "Partial replay created a duplicate verification record"
        )
        print("  [PASS] Partial fixture replay: duplicate detected, exactly 1 verification record")

        # 4. Reconcile FULL fixture
        r_full = await reconcile_paypal_event(full_fixture, db)
        assert r_full.processed is True, "Full event not processed"
        assert r_full.matched is True, "Full event not matched"
        assert r_full.duplicate is False, "Full event marked duplicate unexpectedly"
        assert r_full.outcome == "recovered", f"Expected outcome recovered, got {r_full.outcome}"
        assert r_full.amount_recovered == 100.0, f"Expected amount_recovered 100.0, got {r_full.amount_recovered}"

        ev_after_full = await db.events.find_one({"event_id": event_id})
        assert ev_after_full["status"] == "recovered", (
            f"Event status must be 'recovered' after full payment, got {ev_after_full['status']}"
        )
        print("  [PASS] Full fixture reconciled: event transitioned to recovered (amount=100.00)")

        # 5. Replay FULL fixture (idempotency)
        r_full_replay = await reconcile_paypal_event(full_fixture, db)
        assert r_full_replay.duplicate is True, "Full replay was not detected as duplicate"
        assert await db.verifications.count_documents({"event_id": event_id}) == 2, (
            "Full replay created a duplicate verification record"
        )
        print("  [PASS] Full fixture replay: duplicate detected, exactly 2 verification records total")

        # 6. Direct duplicate insert of paypal_event_id raises DuplicateKeyError (unique index)
        duplicate_raised = False
        try:
            await db.verifications.insert_one({
                "event_id": event_id,
                "execution_id": str(exec_id),
                "gateway": "paypal",
                "paypal_event_id": full_event_id,
                "outcome": "recovered",
                "amount_recovered": 100.0,
                "amount_expected": 100.0,
                "amount_mismatch": False,
                "verified_at": datetime.now(timezone.utc),
                "assertion": "gateway_verified",
                "source": "webhook",
            })
        except DuplicateKeyError:
            duplicate_raised = True
        assert duplicate_raised, "Direct duplicate insert of paypal_event_id did NOT raise DuplicateKeyError"
        print("  [PASS] Direct duplicate insert of paypal_event_id raised DuplicateKeyError (unique index enforced)")

        # 7. Metrics code for currency=USD
        await connect_to_mongo()
        try:
            snapshot = await load_snapshot(currency="USD")
            summary = summarize(snapshot)
            recovered_by_event = recovered_amount_per_event(snapshot.verifications)
            survivors, dupes_ignored = distinct_recoveries(snapshot.verifications)

            event_recovered = recovered_by_event.get(event_id, 0.0)
            assert event_recovered == 100.00, (
                f"Recovered revenue for event must be 100.00, got {event_recovered} (double counting detected?)"
            )
            assert event_recovered != 110.00, (
                "Recovered revenue for event is 110.00! Partial and full were incorrectly summed!"
            )
            assert summary.total_revenue_recovered == 100.00, (
                f"Expected total_revenue_recovered=100.00 in USD, got {summary.total_revenue_recovered}"
            )
            print(
                f"  [PASS] Metrics for USD: recovered={event_recovered:.2f} (NOT 110.00), "
                f"survivors={len(survivors)}, dupes_ignored={dupes_ignored}"
            )
        finally:
            await close_mongo_connection()

        print("\n" + "=" * 78)
        print("RESULT: ALL INTEGRATION CHECKS PASSED")
        print("=" * 78)
        return 0

    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run_integration_check()))
