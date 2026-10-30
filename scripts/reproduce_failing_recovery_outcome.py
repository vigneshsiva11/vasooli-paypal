"""Reproduce the bug where reconcile_paypal_event writes recovery_outcome to executions,
causing ExecutionRecordDocument.from_document, GET /executions, and GET /audit-trail/{event_id}
to fail with ValidationError / HTTP 500.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db import connect_to_mongo, close_mongo_connection, get_database
from app.main import app as fastapi_app
from app.models.execution import ExecutionRecord, ExecutionRecordDocument
from app.webhooks.paypal_reconciler import reconcile_paypal_event

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "paypal" / "payment_capture_completed.json"


async def main() -> int:
    settings = get_settings()
    env_db = os.environ.get("MONGODB_DB_NAME")
    target_db = env_db or settings.mongodb_db_name

    if target_db != "vasooli_regress":
        print(f"Must run against vasooli_regress, got: {target_db}", file=sys.stderr)
        return 1

    print("=" * 70)
    print("REPRODUCE FAILING RECOVERY_OUTCOME ON VASOOLI_REGRESS")
    print("=" * 70)

    await connect_to_mongo()
    db = get_database()
    try:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        order_id = fixture["resource"]["supplementary_data"]["related_ids"]["order_id"]
        paypal_event_id = fixture["id"]
        event_id = "evt_paypal_order_capture_01"

        # Clean prior test state
        await db.events.delete_many({"event_id": event_id})
        await db.executions.delete_many({"event_id": event_id})
        await db.verifications.delete_many({"$or": [{"event_id": event_id}, {"paypal_event_id": paypal_event_id}]})

        # Insert event
        await db.events.insert_one({
            "event_id": event_id,
            "surface": "payment",
            "gateway": "paypal",
            "gateway_ref": order_id,
            "amount": 25.00,
            "currency": "USD",
            "customer_ref": "cust_repro_1",
            "raw_failure_reason": "paypal_declined",
            "status": "at_risk",
            "created_at": datetime.now(timezone.utc),
        })

        # Create valid initial execution record
        exec_record = ExecutionRecord(
            event_id=event_id,
            policy_verdict_id=str(ObjectId()),
            policy_verdict_version=1,
            intervention="recovery_payment_link",
            action_type="payment_link_generated",
            gateway="paypal",
            gateway_ref=order_id,
            gateway_url="https://www.paypal.com/checkout/123",
            status="completed",
            executed_at=datetime.now(timezone.utc),
        )
        exec_dict = exec_record.model_dump()
        res = await db.executions.insert_one(exec_dict)
        print(f"Inserted execution {res.inserted_id} for event {event_id} (valid model)")

        # Verify ExecutionRecordDocument.from_document works BEFORE reconciliation
        doc_before = await db.executions.find_one({"_id": res.inserted_id})
        rec_doc_before = ExecutionRecordDocument.from_document(doc_before)
        print(f"Pre-reconcile check: ExecutionRecordDocument loaded successfully: id={rec_doc_before.id}")

        # Reconcile completion fixture onto it with REAL reconciler
        reconcile_res = await reconcile_paypal_event(fixture, db)
        print(f"Reconciler result: processed={reconcile_res.processed}, matched={reconcile_res.matched}, outcome={reconcile_res.outcome}")

        # Read back raw document from MongoDB
        doc_after = await db.executions.find_one({"_id": res.inserted_id})
        print(f"Stored document after reconcile: recovery_outcome={doc_after.get('recovery_outcome')}, successful={doc_after.get('successful')}")

        # 1. Direct model read back: expect ValidationError!
        model_raised = False
        try:
            ExecutionRecordDocument.from_document(doc_after)
            print("  [UNEXPECTED] ExecutionRecordDocument.from_document DID NOT RAISE!")
        except ValidationError as exc:
            model_raised = True
            print(f"  [REPRODUCED 1/3] ExecutionRecordDocument.from_document raised ValidationError:\n    {exc}")

        # 2. GET /executions via HTTP: expect 500!
        transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            resp_exec = await http_client.get(f"/executions?event_id={event_id}")
            print(f"GET /executions status: {resp_exec.status_code}")
            if resp_exec.status_code == 500:
                print(f"  [REPRODUCED 2/3] GET /executions returned 500 Internal Server Error!")
            else:
                print(f"  [UNEXPECTED] GET /executions returned {resp_exec.status_code}: {resp_exec.text}")

            # 3. GET /audit-trail/{event_id} via HTTP: expect 500!
            resp_audit = await http_client.get(f"/audit-trail/{event_id}")
            print(f"GET /audit-trail/{event_id} status: {resp_audit.status_code}")
            if resp_audit.status_code == 500:
                print(f"  [REPRODUCED 3/3] GET /audit-trail/{event_id} returned 500 Internal Server Error!")
            else:
                print(f"  [UNEXPECTED] GET /audit-trail/{event_id} returned {resp_audit.status_code}: {resp_audit.text}")

        all_reproduced = model_raised and resp_exec.status_code == 500 and resp_audit.status_code == 500
        if all_reproduced:
            print("\n>>> BUG SUCCESSFULLY REPRODUCED ON REAL MONGODB (vasooli_regress)! <<<")
            return 0
        else:
            print("\n>>> FAILED TO REPRODUCE ALL 3 FAILURE MODES <<<")
            return 1
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
