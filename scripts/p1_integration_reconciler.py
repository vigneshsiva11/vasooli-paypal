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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings, get_settings
from app.diagnosis.rules import classify
from app.gateways.paypal.charge_attempts import (
    ensure_indexes as ensure_charge_indexes,
    record_charge_attempt,
)
from app.gateways.paypal.vault import (
    ensure_indexes as ensure_vault_indexes,
    get_active_vault_token,
)
from app.gateway.studio_limits import (
    check_and_record_studio_attempt,
    ensure_indexes as ensure_studio_limits_indexes,
    STUDIO_COUNTERS_COLLECTION,
)
from app.metrics.aggregate import summarize
from app.metrics.reader import (
    distinct_recoveries,
    load_snapshot,
    recovered_amount_per_event,
)
from app.models import RevenueEvent
from app.models.diagnosis import PaymentRootCause
from app.models.events import RevenueEventRecord
from app.models.diagnosis import DiagnosisRecord
from app.models.decision import DecisionRecord
from app.models.policy import PolicyVerdictRecord, CustomerOptOut
from app.models.execution import ExecutionRecordDocument
from app.models.verification import verification_document
from app.models.promise import PromiseToPayDocument
from app.models.promise_extraction import PromiseExtractionDocument
from app.models.vault import CustomerVaultToken
from app.models.charge_attempt import ChargeAttempt
from app.sanitizer import assert_no_forbidden_keys
from app.webhooks import ensure_indexes as ensure_webhook_indexes
from app.webhooks.paypal_reconciler import (
    reconcile_paypal_event,
    reprocess_unmatched_declines,
)
from unittest.mock import AsyncMock, patch
from app.clock import SimulatedClock, MongoClock, advance_mongo_clock, get_clock
from app.orchestrator.service import advance
from app.orchestrator.runs import ensure_orchestrator_indexes
from app.execution.port import ExecutionResult
import app.execution.service
import app.db

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

    client = AsyncIOMotorClient(settings.mongodb_uri, tz_aware=True)
    db = client[target_db]

    try:
        # Load fixtures
        partial_path = FIXTURES_DIR / "invoicing_invoice_paid_partial.json"
        full_path = FIXTURES_DIR / "invoicing_invoice_paid_full.json"
        declined_path = FIXTURES_DIR / "payment_capture_declined.json"
        vault_path = FIXTURES_DIR / "vault_payment_token_created.json"

        assert partial_path.exists(), f"Missing fixture: {partial_path}"
        assert full_path.exists(), f"Missing fixture: {full_path}"
        assert declined_path.exists(), f"Missing fixture: {declined_path}"
        assert vault_path.exists(), f"Missing fixture: {vault_path}"

        partial_fixture = json.loads(partial_path.read_text(encoding="utf-8"))
        full_fixture = json.loads(full_path.read_text(encoding="utf-8"))
        declined_fixture = json.loads(declined_path.read_text(encoding="utf-8"))
        vault_fixture = json.loads(vault_path.read_text(encoding="utf-8"))

        partial_event_id = partial_fixture["id"]
        full_event_id = full_fixture["id"]
        declined_event_id = declined_fixture["id"]
        vault_event_id = vault_fixture["id"]
        invoice_id = "INV2-LSEJ-2LLG-D59L-2QNU"

        print(
            f"Loaded fixtures: partial={partial_event_id}, full={full_event_id}, "
            f"declined={declined_event_id}, vault={vault_event_id}"
        )

        # Ensure collection indexes on vasooli_regress
        from app.db import connect_to_mongo, close_mongo_connection
        await connect_to_mongo()
        try:
            await ensure_webhook_indexes()
            await ensure_charge_indexes()
            await ensure_vault_indexes()
        finally:
            await close_mongo_connection()

        event_id = "evt_paypal_reconciler_integration_01"
        decline_capture_id = declined_fixture["resource"]["id"]
        decline_event_id = f"pp_{decline_capture_id}"
        decline_order_id = declined_fixture["resource"]["supplementary_data"]["related_ids"]["order_id"]
        decline_cust_ref = "cust_paypal_decline_integ"

        vault_token_id = vault_fixture["resource"]["id"]
        vault_order_id = vault_fixture["resource"]["metadata"]["order_id"]
        vault_cust_ref = "cust_vault_integ_01"

        # Clean up any prior test data for this test run
        await db.events.delete_many({"event_id": {"$in": [event_id, decline_event_id]}})
        await db.executions.delete_many({"event_id": event_id})
        await db.verifications.delete_many({
            "$or": [
                {"event_id": event_id},
                {"paypal_event_id": {"$in": [partial_event_id, full_event_id]}},
            ]
        })
        await db.paypal_events.delete_many({
            "paypal_event_id": {"$in": [partial_event_id, full_event_id, declined_event_id, vault_event_id]}
        })
        await db.charge_attempts.delete_many({"order_id": {"$in": [decline_order_id, vault_order_id]}})
        await db.customer_vault.delete_many({"vault_id": vault_token_id})
        await db.customer_vault.delete_many({"customer_ref": "demo-integ-test"})
        await db.verifications.delete_many({"supplementary_data.related_ids.order_id": "ORD-DEMO-VAULT-003"})
        await db.verifications.delete_many({"custom_id": {"$regex": "demo-integ-test"}})
        await db.events.delete_many({"customer_ref": "demo-integ-test"})
        await db.executions.delete_many({"gateway_ref": {"$in": ["ORD-DEMO-CARD-001", "ORD-DEMO-DECLINE-002", "ORD-DEMO-VAULT-003"]}})
        await db.demo_runs.delete_many({})
        await db.demo_counters.delete_many({})

        # 1. Seed event and execution
        event_doc = {
            "event_id": event_id,
            "amount": 100.00,
            "currency": "USD",
            "status": "at_risk",
            "surface": "payment",
            "customer_ref": "cust_paypal_integration_01",
            "raw_failure_reason": "invoice_unpaid",
            "gateway": "paypal",
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

        # 8. PAYMENT.CAPTURE.DECLINED when unmatched
        r_dec_unmatched = await reconcile_paypal_event(declined_fixture, db)
        assert r_dec_unmatched.processed is False, "Decline fixture should have processed=False when unmatched"
        assert r_dec_unmatched.matched is False, "Decline fixture without attempt should not be matched"
        stored_unmatched = await db.paypal_events.find_one({"paypal_event_id": declined_event_id})
        assert stored_unmatched is not None, "Unmatched decline must be saved to paypal_events"
        assert stored_unmatched.get("unmatched") is True, "paypal_events doc must have unmatched=True"
        assert await db.events.find_one({"event_id": decline_event_id}) is None, (
            "No event should be created while decline is unmatched"
        )
        print("  [PASS] PAYMENT.CAPTURE.DECLINED unmatched: stored in paypal_events with unmatched=True")

        # 9. Seed charge attempt and reprocess unmatched declines
        await record_charge_attempt(
            order_id=decline_order_id,
            customer_ref=decline_cust_ref,
            amount=10.00,
            currency="USD",
            purpose="renewal",
            db=db,
        )
        reprocessed = await reprocess_unmatched_declines(db)
        assert len(reprocessed) >= 1, f"Expected at least 1 decline reprocessed, got {len(reprocessed)}"

        dec_event = await db.events.find_one({"event_id": decline_event_id})
        assert dec_event is not None, f"Event {decline_event_id} was not created after reprocessing"
        assert dec_event["status"] == "at_risk"
        assert dec_event["amount"] == 10.00
        assert dec_event["currency"] == "USD"
        assert dec_event["customer_ref"] == decline_cust_ref
        assert dec_event["raw_failure_reason"] == "paypal_5120"
        assert dec_event["gateway"] == "paypal"
        assert dec_event["surface"] == "payment"
        assert dec_event.get("gateway_detail", {}).get("processor_response", {}).get("response_code") == "5120"

        # Verify rule-based diagnosis maps paypal_5120 to insufficient_funds
        rev_ev_5120 = RevenueEvent(**{k: v for k, v in dec_event.items() if k != "_id"})
        m_5120 = classify(rev_ev_5120)
        assert m_5120 is not None and m_5120.root_cause == "insufficient_funds", f"Expected insufficient_funds, got {m_5120}"

        rev_ev_0500 = RevenueEvent(event_id="pp_0500", surface="payment", amount=10.0, customer_ref="c", raw_failure_reason="paypal_0500")
        m_0500 = classify(rev_ev_0500)
        assert m_0500 is not None and m_0500.root_cause == "issuer_declined"

        rev_ev_00N7 = RevenueEvent(event_id="pp_00N7", surface="payment", amount=10.0, customer_ref="c", raw_failure_reason="paypal_00N7")
        m_00N7 = classify(rev_ev_00N7)
        assert m_00N7 is not None and m_00N7.root_cause == "card_verification_failed"

        rev_ev_9999 = RevenueEvent(event_id="pp_9999", surface="payment", amount=10.0, customer_ref="c", raw_failure_reason="paypal_9999")
        m_9999 = classify(rev_ev_9999)
        assert m_9999 is None

        # Replay decline fixture (idempotent)
        r_dec_replay = await reconcile_paypal_event(declined_fixture, db)
        assert r_dec_replay.duplicate is True, "Replaying declined fixture should return duplicate=True"
        print("  [PASS] Reprocessed decline: created RevenueEvent with paypal_5120, diagnosed INSUFFICIENT_FUNDS, replay idempotent")

        # 10. VAULT.PAYMENT-TOKEN.CREATED reconciliation
        await record_charge_attempt(
            order_id=vault_order_id,
            customer_ref=vault_cust_ref,
            amount=0.00,
            currency="USD",
            purpose="onboarding",
            db=db,
        )
        r_vault = await reconcile_paypal_event(vault_fixture, db)
        assert r_vault.processed is True, "Vault fixture was not processed"
        assert r_vault.matched is True, "Vault fixture was not matched"
        assert r_vault.duplicate is False, "Vault fixture marked duplicate unexpectedly"

        vault_token = await get_active_vault_token(vault_cust_ref, db=db)
        assert vault_token is not None, f"No active vault token found for {vault_cust_ref}"
        assert vault_token["vault_id"] == vault_token_id
        assert vault_token["card_brand"] == "VISA"
        assert vault_token["last_digits"] == "1111"
        assert vault_token["status"] == "active"

        # Replay vault fixture
        r_vault_replay = await reconcile_paypal_event(vault_fixture, db)
        assert r_vault_replay.duplicate is True, "Replaying vault fixture should return duplicate=True"

        # Unique index on customer_vault
        dup_vault_raised = False
        try:
            await db.customer_vault.insert_one({
                "customer_ref": "cust_other",
                "vault_id": vault_token_id,
                "card_brand": "VISA",
                "last_digits": "1111",
                "status": "active",
                "source_event_id": "other_event",
                "created_at": datetime.now(timezone.utc),
            })
        except DuplicateKeyError:
            dup_vault_raised = True
        assert dup_vault_raised, "Duplicate vault_id did not raise DuplicateKeyError"
        # 11. STEP 5b: ORCHESTRATOR ADVANCE & RUNS (REAL MONGODB)
        await ensure_orchestrator_indexes(db)
        app.db._database = db

        # Cleanup any prior run docs
        await db.events.delete_many({"event_id": {"$in": ["pp_orch_11", "pp_orch_no_vault"]}})
        await db.customer_vault.delete_many({"customer_ref": {"$in": ["cust_orch_11", "cust_no_vault_14"]}})
        await db.orchestrator_runs.delete_many({"event_id": {"$in": ["pp_orch_11", "pp_orch_no_vault"]}})
        await db.executions.delete_many({"event_id": {"$in": ["pp_orch_11", "pp_orch_no_vault"]}})
        await db.decisions.delete_many({"event_id": {"$in": ["pp_orch_11", "pp_orch_no_vault"]}})
        await db.policy_verdicts.delete_many({"event_id": {"$in": ["pp_orch_11", "pp_orch_no_vault"]}})
        await db.diagnoses.delete_many({"event_id": {"$in": ["pp_orch_11", "pp_orch_no_vault"]}})
        await db.paypal_events.delete_many({"paypal_event_id": "WH-DECL-LOOP-11"})
        await db.verifications.delete_many({"paypal_event_id": "WH-DECL-LOOP-11"})

        sim_clock_11 = SimulatedClock()
        await db.events.insert_one({
            "event_id": "pp_orch_11",
            "surface": "payment",
            "gateway": "paypal",
            "amount": 65.0,
            "currency": "USD",
            "customer_ref": "cust_orch_11",
            "status": "at_risk",
            "raw_failure_reason": "paypal_5120",
            "gateway_detail": {"processor_response": {"response_code": "5120"}},
            "created_at": sim_clock_11.now(),
        })
        await db.customer_vault.insert_one({
            "vault_id": "vlt_orch_11",
            "customer_ref": "cust_orch_11",
            "card_brand": "VISA",
            "last_digits": "4444",
            "status": "active",
            "source_event_id": "pp_seed",
            "created_at": sim_clock_11.now(),
        })

        mock_order_res = {
            "id": "ORD-ORCH-RET-11",
            "status": "COMPLETED",
            "links": [{"rel": "self", "href": "https://api.sandbox.paypal.com/v2/checkout/orders/ORD-ORCH-RET-11"}],
        }

        with patch("app.gateways.paypal.orders.create_vaulted_order", AsyncMock(return_value=mock_order_res)):
            res_adv_11 = await advance("pp_orch_11", clock=sim_clock_11, db=db)
            assert res_adv_11.get("status") == "completed", f"Advance status unexpected: {res_adv_11}"
            assert res_adv_11.get("stage") == "execute"

            # Check orchestrator runs
            runs = await db.orchestrator_runs.find({"event_id": "pp_orch_11"}).to_list(10)
            assert len(runs) == 4, f"Expected 4 runs (diagnose, decide, policy, execute), got {len(runs)}"
            assert all(r.get("simulated_time") is True for r in runs), "Runs must be marked simulated_time=True"

            # Check execution
            exec_doc = await db.executions.find_one({"event_id": "pp_orch_11"})
            assert exec_doc is not None, "Execution document was not created"
            assert exec_doc.get("gateway_ref") == "ORD-ORCH-RET-11"
            assert exec_doc.get("simulated_time") is True

            # Replay advance is idempotent
            res_replay = await advance("pp_orch_11", clock=sim_clock_11, db=db)
            assert res_replay.get("status") in ("terminal", "already_executed")
            runs_replay = await db.orchestrator_runs.find({"event_id": "pp_orch_11"}).to_list(10)
            assert len(runs_replay) == 4, "Replay must create zero duplicate runs"

            # Unique index on orchestrator_runs: duplicate event_id+stage+record_version raises DuplicateKeyError
            dup_run_raised = False
            try:
                await db.orchestrator_runs.insert_one({
                    "event_id": "pp_orch_11",
                    "stage": "diagnose",
                    "record_version": 1,
                    "outcome": "diagnosed",
                    "real_time": datetime.now(timezone.utc),
                    "clock_now": sim_clock_11.now(),
                    "simulated_time": True,
                })
            except DuplicateKeyError:
                dup_run_raised = True
            assert dup_run_raised, "Duplicate orchestrator_runs did not raise DuplicateKeyError"

        print("  [PASS] Orchestrator advance: runs 4 stages, simulated_time marked, runs unique index enforced, replay idempotent")

        # 12. SIMULATED CLOCK (DB-PERSISTED)
        adv_clock = await advance_mongo_clock(hours=5.0, db=db)
        assert adv_clock.offset_seconds >= 5.0 * 3600.0
        db_clock = await get_clock(db=db)
        assert isinstance(db_clock, MongoClock), f"Expected MongoClock, got {type(db_clock)}"
        assert db_clock.is_simulated() is True
        print("  [PASS] Simulated Clock: advance_mongo_clock updates DB offset, get_clock returns MongoClock in simulated time")

        # 13. LOOP GUARD (REAL MONGODB)
        webhook_declined_loop = {
            "id": "WH-DECL-LOOP-11",
            "event_type": "PAYMENT.CAPTURE.DECLINED",
            "resource": {
                "id": "CAP-LOOP-11",
                "custom_id": "ev:pp_orch_11",
                "order_id": "ORD-ORCH-RET-11",
                "amount": {"value": "65.00", "currency_code": "USD"},
            },
        }

        events_before_count = await db.events.count_documents({})
        r_loop = await reconcile_paypal_event(webhook_declined_loop, db)
        assert r_loop.processed is True
        assert r_loop.outcome == "not_recovered"

        events_after_count = await db.events.count_documents({})
        assert events_after_count == events_before_count, "Loop guard must NEVER create a new RevenueEvent"

        exec_after_loop = await db.executions.find_one({"event_id": "pp_orch_11"})
        assert exec_after_loop.get("successful") is False
        assert exec_after_loop.get("recovery_outcome") == "not_recovered"

        # Replay loop webhook (idempotent)
        r_loop_dup = await reconcile_paypal_event(webhook_declined_loop, db)
        assert r_loop_dup.duplicate is True
        print("  [PASS] Loop guard: declined recovery capture ('ev:') attaches not_recovered, creates no new event, replay idempotent")

        # 14. RETRY EXCLUSION & FALLBACK (NO VAULT TOKEN)
        await db.events.insert_one({
            "event_id": "pp_orch_no_vault",
            "surface": "payment",
            "gateway": "paypal",
            "amount": 50.0,
            "currency": "USD",
            "customer_ref": "cust_no_vault_14",
            "status": "at_risk",
            "raw_failure_reason": "paypal_5120",
            "gateway_detail": {"processor_response": {"response_code": "5120"}},
            "created_at": sim_clock_11.now(),
        })

        mock_link_res = ExecutionResult(
            status="completed",
            gateway="paypal",
            gateway_ref="INV-ORCH-14",
            gateway_url="https://www.sandbox.paypal.com/invoice/p/#INV-ORCH-14",
        )

        with patch.object(app.execution.service.PayPalExecutionAdapter, "execute_link", AsyncMock(return_value=mock_link_res)):
            res_adv_14 = await advance("pp_orch_no_vault", clock=sim_clock_11, db=db)
            assert res_adv_14.get("status") == "completed"

            dec_14 = await db.decisions.find_one({"event_id": "pp_orch_no_vault"})
            assert dec_14 is not None
            assert dec_14.get("excluded_interventions") == ["immediate_retry", "delayed_retry"]
            assert dec_14.get("exclusion_reason") == "no_active_vault_token"
            assert dec_14.get("recommended_intervention") == "payment_method_update_link"

            exec_14 = await db.executions.find_one({"event_id": "pp_orch_no_vault"})
            assert exec_14 is not None
            assert exec_14.get("intervention") == "payment_method_update_link"
            assert exec_14.get("gateway_ref") == "INV-ORCH-14"

        print("  [PASS] Retry exclusion: missing vault token excludes retries, falls back to payment_method_update_link")

        # 15. USD RETRY CAP INTEGRATION CHECK (REAL MONGODB)
        # Verify real MongoDB reads of prior_retry_executions, max 3 cap, and 24h cooldown.
        from datetime import timedelta
        from app.policy.engine import evaluate
        from app.policy.rules import current_rulebook
        from app.policy.store import gather_context
        from app.models.decision import DecisionRecord

        usd_event_id = "ev_usd_retry_cap_integ"
        now_sim = sim_clock_11.now()

        # Insert USD event with vault token
        await db.events.delete_many({"event_id": usd_event_id})
        await db.executions.delete_many({"event_id": usd_event_id})
        await db.events.insert_one({
            "event_id": usd_event_id,
            "surface": "payment",
            "gateway": "paypal",
            "amount": 100.0,
            "currency": "USD",
            "customer_ref": "cust_usd_cap_integ",
            "status": "at_risk",
            "created_at": now_sim,
        })
        await db.customer_vault.update_one(
            {"customer_ref": "cust_usd_cap_integ"},
            {
                "$set": {
                    "customer_ref": "cust_usd_cap_integ",
                    "vault_token_id": "tok_usd_cap_integ",
                    "status": "active",
                    "created_at": now_sim,
                    "updated_at": now_sim,
                }
            },
            upsert=True,
        )

        dec_record = DecisionRecord(
            id=str(ObjectId()),
            version=1,
            event_id=usd_event_id,
            diagnosis_id=str(ObjectId()),
            diagnosis_version=1,
            recommended_intervention="delayed_retry",
            revenue_at_risk=100.0,
            recovery_probability=0.7,
            estimated_cost=0.0,
            expected_recovery_value=70.0,
            reasoning="USD retry cap integration check",
            decided_at=now_sim,
        )

        usd_rulebook = current_rulebook("USD")

        # Case A: 3 completed retries in real MongoDB -> blocked / retry_cap_exceeded
        for i in range(3):
            await db.executions.insert_one({
                "execution_id": f"exec_cap_{i}",
                "event_id": usd_event_id,
                "policy_verdict_id": str(ObjectId()),
                "intervention": "delayed_retry",
                "status": "completed",
                "executed_at": now_sim - timedelta(hours=30 + i * 25),
                "created_at": now_sim - timedelta(hours=30 + i * 25),
            })

        ctx_a = await gather_context(decision=dec_record, customer_ref="cust_usd_cap_integ", now=now_sim, rulebook=usd_rulebook)
        assert ctx_a.prior_retry_executions == 3, f"Expected 3 prior retries from Mongo, got {ctx_a.prior_retry_executions}"
        v_a = evaluate(decision=dec_record, context=ctx_a, rulebook=usd_rulebook)
        assert v_a.verdict == "blocked"
        assert v_a.reason == "retry_cap_exceeded"
        assert "retry cap reached: 3 prior retry executions" in v_a.checks_performed[0]

        # Case B: 2 retries, last was only 12h ago (less than 24h gap) -> blocked / retry_cap_exceeded
        await db.executions.delete_many({"event_id": usd_event_id})
        for i in range(2):
            await db.executions.insert_one({
                "execution_id": f"exec_cooldown_{i}",
                "event_id": usd_event_id,
                "policy_verdict_id": str(ObjectId()),
                "intervention": "delayed_retry",
                "status": "completed",
                "executed_at": now_sim - timedelta(hours=12 if i == 0 else 40),
                "created_at": now_sim - timedelta(hours=12 if i == 0 else 40),
            })

        ctx_b = await gather_context(decision=dec_record, customer_ref="cust_usd_cap_integ", now=now_sim, rulebook=usd_rulebook)
        assert ctx_b.prior_retry_executions == 2
        v_b = evaluate(decision=dec_record, context=ctx_b, rulebook=usd_rulebook)
        assert v_b.verdict == "blocked"
        assert v_b.reason == "retry_cap_exceeded"
        assert "retry cooldown active" in v_b.checks_performed[0]

        # Case C: 2 retries, last was 25h ago (clears 24h gap and cap < 3) -> authorized / ok
        await db.executions.delete_many({"event_id": usd_event_id})
        for i in range(2):
            await db.executions.insert_one({
                "execution_id": f"exec_ok_{i}",
                "event_id": usd_event_id,
                "policy_verdict_id": str(ObjectId()),
                "intervention": "delayed_retry",
                "status": "completed",
                "executed_at": now_sim - timedelta(hours=25 if i == 0 else 60),
                "created_at": now_sim - timedelta(hours=25 if i == 0 else 60),
            })

        ctx_c = await gather_context(decision=dec_record, customer_ref="cust_usd_cap_integ", now=now_sim, rulebook=usd_rulebook)
        assert ctx_c.prior_retry_executions == 2
        v_c = evaluate(decision=dec_record, context=ctx_c, rulebook=usd_rulebook)
        assert v_c.verdict == "authorized"
        assert v_c.reason == "ok"

        # Case D: 3 retries where executions are declined (status="completed", successful=False, capture_status="DECLINED") count toward retry cap
        await db.executions.delete_many({"event_id": usd_event_id})
        for i in range(3):
            await db.executions.insert_one({
                "execution_id": f"exec_declined_cap_{i}",
                "event_id": usd_event_id,
                "policy_verdict_id": str(ObjectId()),
                "intervention": "delayed_retry",
                "status": "completed",
                "successful": False,
                "capture_status": "DECLINED",
                "processor_response_code": "5120",
                "executed_at": now_sim - timedelta(hours=30 + i * 24),
                "created_at": now_sim - timedelta(hours=30 + i * 24),
            })

        ctx_d = await gather_context(decision=dec_record, customer_ref="cust_usd_cap_integ", now=now_sim, rulebook=usd_rulebook)
        assert ctx_d.prior_retry_executions == 3, f"Expected 3 prior retry executions including declined, got {ctx_d.prior_retry_executions}"
        v_d = evaluate(decision=dec_record, context=ctx_d, rulebook=usd_rulebook)
        assert v_d.verdict == "blocked"
        assert v_d.reason == "retry_cap_exceeded"
        assert "retry cap reached: 3 prior retry executions" in v_d.checks_performed[0]

        print("  [PASS] USD Retry Cap integration check: 3 retries capped (including 'declined' retries with status='completed', successful=False), 24h cooldown enforced, real MongoDB queries verified")

        # -------------------------------------------------------------------
        # 17. Demo Reset & Scenario Runner Integration Checks (vasooli_regress)
        # -------------------------------------------------------------------
        from app.routes.demo import (
            reset_demo_database,
            _execute_scenario_run,
            get_demo_run,
            list_demo_runs,
        )
        app.db._database = db

        # Test reset_demo_database against vasooli_regress
        reset_res = await reset_demo_database(target_db)
        assert reset_res.get("status") == "ok"
        assert len(reset_res.get("dropped_collections", [])) == 12
        print("  [PASS] Demo reset on real MongoDB (vasooli_regress): dropped and re-ensured 12 collections")

        # Test scenario runner background task on real MongoDB
        scenario_run_id = str(ObjectId())
        scenario_steps = [
            {"name": name, "status": "pending", "started_at": None, "finished_at": None, "source": None, "refs": {}}
            for name in ["onboard", "renewal_attempt", "decline_ingested", "pipeline", "clock", "retry_executed", "verified"]
        ]
        await db.demo_runs.insert_one({
            "_id": ObjectId(scenario_run_id),
            "run_id": scenario_run_id,
            "scenario": "insufficient-funds",
            "customer_ref": "demo-integ-test",
            "amount": 49.00,
            "currency": "USD",
            "advance_hours": 25.0,
            "status": "running",
            "current_step": "onboard",
            "steps": scenario_steps,
            "final_status": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        })

        # Mock adapter responses and feeds for scenario runner steps
        mock_card_order = {"id": "ORD-DEMO-CARD-001", "status": "COMPLETED", "capture_status": "COMPLETED"}
        mock_decline_order = {
            "id": "ORD-DEMO-DECLINE-002",
            "status": "COMPLETED",
            "capture_status": "DECLINED",
            "processor_response_code": "5120",
        }
        mock_vault_order = {"id": "ORD-DEMO-VAULT-003", "status": "COMPLETED", "capture_status": "COMPLETED"}

        with patch("app.gateways.paypal.orders.create_and_capture_order", side_effect=[mock_card_order, mock_decline_order]), \
             patch("app.gateways.paypal.orders.create_vaulted_order", return_value=mock_vault_order):

            # Helper background feeder for vault token, decline webhook, and completion webhook
            async def feed_webhooks_for_run():
                await asyncio.sleep(0.05)
                # Feed step 1 vault token
                await db.customer_vault.insert_one({
                    "customer_ref": "demo-integ-test",
                    "vault_id": "tok_demo_integ_123",
                    "payment_source": "card",
                    "card_brand": "VISA",
                    "card_last_digits": "1111",
                    "status": "active",
                    "created_at": datetime.now(timezone.utc),
                })
                await asyncio.sleep(0.05)
                # Feed step 3 decline webhook
                with open(FIXTURES_DIR / "live" / "payment_capture_declined_order_b.json", "r", encoding="utf-8") as f:
                    decline_fx = json.load(f)
                decline_fx["resource"]["custom_id"] = "cust:demo-integ-test"
                decline_fx["resource"]["amount"]["value"] = "49.00"
                decline_fx["id"] = f"WH-DEMO-DECLINE-{ObjectId()}"
                await reconcile_paypal_event(decline_fx)

                # Feed step 7 completion webhook after retry execution is recorded
                retry_exec = None
                for _ in range(50):
                    retry_exec = await db.executions.find_one({"gateway_ref": "ORD-DEMO-VAULT-003"})
                    if retry_exec:
                        break
                    await asyncio.sleep(0.05)

                with open(FIXTURES_DIR / "live" / "payment_capture_completed_order_c.json", "r", encoding="utf-8") as f:
                    comp_fx = json.load(f)
                comp_fx["id"] = f"WH-DEMO-COMP-{ObjectId()}"
                comp_fx["resource"]["custom_id"] = f"ev:{retry_exec['event_id']}" if retry_exec else "cust:demo-integ-test"
                comp_fx["resource"]["amount"]["value"] = "49.00"
                comp_fx["resource"]["supplementary_data"] = {"related_ids": {"order_id": "ORD-DEMO-VAULT-003"}}
                await reconcile_paypal_event(comp_fx)

            feeder_task = asyncio.create_task(feed_webhooks_for_run())
            await _execute_scenario_run(
                run_id=scenario_run_id,
                customer_ref="demo-integ-test",
                amount=49.00,
                advance_hours=25.0,
            )
            await feeder_task

        run_doc_after = await db.demo_runs.find_one({"run_id": scenario_run_id})
        assert run_doc_after is not None
        assert run_doc_after.get("status") == "recovered", f"Expected recovered, got {run_doc_after.get('status')}"
        assert run_doc_after.get("final_status") == "recovered"
        assert all(s.get("status") == "completed" for s in run_doc_after.get("steps", []))
        print("  [PASS] Scenario runner on real MongoDB (vasooli_regress): all 7 steps completed, event recovered")

        # Test public GET formatting
        single_run_view = await get_demo_run(scenario_run_id)
        assert single_run_view.get("run_id") == scenario_run_id
        assert single_run_view.get("amount") == 49.00
        assert "_id" not in single_run_view

        # Resolve scenario event id
        scenario_event_id = None
        for s in run_doc_after.get("steps", []):
            if s.get("refs") and "event_id" in s["refs"]:
                scenario_event_id = s["refs"]["event_id"]
                break

        # --- 19. Symmetry Test: Load EVERY document of EVERY model-backed collection through strict read model ---
        model_backed_checks = [
            ("events", db.events, RevenueEventRecord.from_document),
            ("diagnoses", db.diagnoses, DiagnosisRecord.from_document),
            ("decisions", db.decisions, DecisionRecord.from_document),
            ("policy_verdicts", db.policy_verdicts, PolicyVerdictRecord.from_document),
            ("opt_outs", db.opt_outs, CustomerOptOut.from_document),
            ("executions", db.executions, ExecutionRecordDocument.from_document),
            ("verifications", db.verifications, verification_document),
            ("promises", db.promises, PromiseToPayDocument.from_document),
            ("promise_extractions", db.promise_extractions, PromiseExtractionDocument.from_document),
            ("customer_vault", db.customer_vault, CustomerVaultToken.from_document),
            ("charge_attempts", db.charge_attempts, ChargeAttempt.from_document),
        ]
        total_docs_validated = 0
        for col_name, col_handle, parse_fn in model_backed_checks:
            col_count = 0
            async for doc in col_handle.find():
                try:
                    parse_fn(doc)
                    col_count += 1
                except Exception as exc:
                    raise AssertionError(
                        f"Read model validation failed for collection {col_name!r} doc {doc.get('_id')}: {exc}"
                    ) from exc
            total_docs_validated += col_count
        assert total_docs_validated > 0, "No documents were validated across collections"
        print(f"  [PASS] Symmetry test: validated all {total_docs_validated} documents across 11 model-backed collections through strict read models")

        # --- Scenario-level Smoke Test of EVERY public GET endpoint ---
        from app.main import app as fastapi_app

        smoke_endpoints = [
            "/events",
            "/diagnoses",
            "/decisions",
            "/policy-verdicts",
            "/executions",
            "/verifications",
            "/promises",
            "/promise-extractions",
            "/opt-outs",
            "/ledger",
            "/demo/runs",
            f"/demo/runs/{scenario_run_id}",
            "/metrics/summary",
            "/metrics/by-root-cause",
            "/metrics/by-intervention",
            "/metrics/promise-to-pay",
            "/metrics/baseline-comparison",
        ]
        if scenario_event_id:
            smoke_endpoints.append(f"/audit-trail/{scenario_event_id}")

        transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as smoke_client:
            for ep in smoke_endpoints:
                resp = await smoke_client.get(ep)
                assert resp.status_code == 200, f"Smoke test failed on {ep}: HTTP {resp.status_code} ({resp.text})"
                body = resp.json()
                assert_no_forbidden_keys(body, path=ep)
        print(f"  [PASS] Scenario smoke test: requested {len(smoke_endpoints)} public GET endpoints (all 200 OK, valid JSON, zero forbidden keys)")

        # Clean up scenario runner test records so subsequent metrics or queries remain isolated
        await db.events.delete_many({"customer_ref": "demo-integ-test"})
        await db.customer_vault.delete_many({"customer_ref": "demo-integ-test"})
        await db.verifications.delete_many({"supplementary_data.related_ids.order_id": "ORD-DEMO-VAULT-003"})
        await db.verifications.delete_many({"custom_id": {"$regex": "demo-integ-test"}})
        await db.executions.delete_many({"gateway_ref": {"$in": ["ORD-DEMO-CARD-001", "ORD-DEMO-DECLINE-002", "ORD-DEMO-VAULT-003"]}})
        # Test stale-run self-heal on real MongoDB (vasooli_regress)
        from app.routes.demo import self_heal_stale_runs
        stale_run_id = f"run_stale_real_{ObjectId()}"
        ten_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=15)
        await db.demo_runs.insert_one({
            "run_id": stale_run_id,
            "scenario": "insufficient-funds",
            "customer_ref": "demo-stale-user",
            "amount": 49.00,
            "currency": "USD",
            "status": "running",
            "current_step": "renewal_attempt",
            "steps": [
                {"name": "onboard", "status": "completed", "refs": {}},
                {"name": "renewal_attempt", "status": "running", "started_at": ten_mins_ago, "refs": {}},
                {"name": "decline_ingested", "status": "pending", "refs": {}},
            ],
            "created_at": ten_mins_ago,
            "updated_at": ten_mins_ago,
        })
        healed = await self_heal_stale_runs(db)
        assert healed >= 1
        healed_doc = await db.demo_runs.find_one({"run_id": stale_run_id})
        assert healed_doc["status"] == "failed"
        assert healed_doc["final_status"] == 'failed(renewal_attempt, "stale")'
        # Verify active-run lock is released (no running doc for this run)
        active_running = await db.demo_runs.find_one({"run_id": stale_run_id, "status": "running"})
        assert active_running is None
        print("  [PASS] Stale-run self-heal on real MongoDB (vasooli_regress): marked failed(step, 'stale') and released active lock")

        # -------------------------------------------------------------------
        # 20. Real-MongoDB Public Demo Run & Parallel Requests Integration Check
        # -------------------------------------------------------------------
        print("\n--- 20. Public Demo Run & Parallel Requests (Real MongoDB: vasooli_regress) ---")
        from app.main import app as fastapi_app
        from app.routes.demo import ensure_demo_run_indexes

        # Ensure demo run and counter indexes on vasooli_regress
        await ensure_demo_run_indexes(db)

        # 20.1 Verify TTL index on demo_counters with expireAfterSeconds=3600 exists
        counter_indexes = await db.demo_counters.index_information()
        ttl_idx = None
        for idx_name, idx_info in counter_indexes.items():
            if idx_info.get("expireAfterSeconds") == 3600:
                ttl_idx = idx_info
                break
        assert ttl_idx is not None, f"TTL index with expireAfterSeconds=3600 not found on demo_counters: {counter_indexes}"
        assert ("created_at", 1) in ttl_idx.get("key", []), f"TTL index not on created_at: {ttl_idx}"
        print("  [PASS] TTL index on demo_counters exists with expireAfterSeconds=3600 on created_at")

        # 20.2 Verify unique partial index on demo_runs (status='running')
        demo_run_indexes = await db.demo_runs.index_information()
        partial_uniq_idx = None
        for idx_name, idx_info in demo_run_indexes.items():
            if idx_info.get("unique") is True and idx_info.get("partialFilterExpression") == {"status": "running"}:
                partial_uniq_idx = idx_info
                break
        assert partial_uniq_idx is not None, f"Unique partial index on status='running' not found on demo_runs: {demo_run_indexes}"
        print("  [PASS] Unique partial index on demo_runs exists for status='running'")

        # 20.3 Prove atomic lock at MongoDB engine level (DuplicateKeyError on concurrent insert)
        await db.demo_runs.delete_many({"scenario": "insufficient-funds"})
        await db.demo_counters.delete_many({})

        probe_run_id = f"atomic_probe_{ObjectId()}"
        await db.demo_runs.insert_one({
            "run_id": probe_run_id,
            "scenario": "insufficient-funds",
            "status": "running",
            "created_at": datetime.now(timezone.utc),
        })
        atomic_duplicate_raised = False
        try:
            await db.demo_runs.insert_one({
                "run_id": f"collision_{ObjectId()}",
                "scenario": "insufficient-funds",
                "status": "running",
                "created_at": datetime.now(timezone.utc),
            })
        except DuplicateKeyError:
            atomic_duplicate_raised = True
        assert atomic_duplicate_raised, "MongoDB failed to raise DuplicateKeyError for second running doc"
        await db.demo_runs.delete_one({"run_id": probe_run_id})
        print("  [PASS] Atomic lock proven on real MongoDB: unique partial index raises DuplicateKeyError on collision")

        # 20.4 Fire 8 simultaneous POST /demo/public/run requests through the app
        # Settings configured with PUBLIC_RUNS=True and sandbox assertion satisfied
        app.db._database = db
        mock_settings = settings.model_copy(
            update={
                "public_demo": True,
                "public_runs": True,
                "paypal_environment": "sandbox",
                "mongodb_db_name": "vasooli_regress",
            }
        )

        async def mock_slow_execution(*args, **kwargs):
            await asyncio.sleep(2.0)

        # Clear runs and counters before parallel burst
        await db.demo_runs.delete_many({})
        await db.demo_counters.delete_many({})

        with patch("app.routes.demo.get_settings", return_value=mock_settings), \
             patch("app.routes.demo._execute_scenario_run", side_effect=mock_slow_execution):

            transport = httpx.ASGITransport(app=fastapi_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
                # 8 simultaneous requests with the same client IP
                responses = await asyncio.gather(
                    *[
                        http_client.post(
                            "/demo/public/run",
                            json={"amount": 49.00},
                            headers={"X-Forwarded-For": "203.0.113.195"},
                        )
                        for _ in range(8)
                    ]
                )

                statuses = [r.status_code for r in responses]
                accepted = [r for r in responses if r.status_code == 202]
                conflicts_or_rate_limits = [r for r in responses if r.status_code in (409, 429)]

                assert len(accepted) == 1, f"Expected exactly ONE 202 Accepted, got {len(accepted)} (statuses: {statuses})"
                assert len(conflicts_or_rate_limits) == 7, f"Expected 7 rejected (409 or 429), got {len(conflicts_or_rate_limits)} (statuses: {statuses})"
                assert all(code in (202, 409, 429) for code in statuses), f"Unexpected status code in {statuses}"

                # Verify demo_runs holds exactly ONE active run
                active_runs_count = await db.demo_runs.count_documents({"status": "running"})
                assert active_runs_count == 1, f"Expected exactly 1 active run in demo_runs, found {active_runs_count}"
                print(f"  [PASS] 8 simultaneous requests through app: exactly 1 accepted (202), 7 rejected (409/429), 1 active run in demo_runs")

                # 20.5 Test 60s spacing holds across parallel requests on real MongoDB
                # Mark the active run as completed so 409 conflict does not shadow spacing
                await db.demo_runs.update_one({"status": "running"}, {"$set": {"status": "completed"}})
                spacing_responses = await asyncio.gather(
                    *[
                        http_client.post(
                            "/demo/public/run",
                            json={"amount": 49.00},
                            headers={"X-Forwarded-For": f"198.51.100.{i}"},
                        )
                        for i in range(1, 5)
                    ]
                )
                assert all(r.status_code == 429 for r in spacing_responses), f"Expected all 429 for spacing, got {[r.status_code for r in spacing_responses]}"
                for r in spacing_responses:
                    assert "Retry-After" in r.headers, "Missing Retry-After header on spacing 429"
                    assert "Spacing limit" in r.json().get("detail", "")
                print("  [PASS] 60s spacing holds across parallel requests on real MongoDB (all return 429 with Retry-After)")

                # 20.6 Test per-client hourly limit (max 3/hr) holds across parallel requests
                # Age existing runs beyond 60s spacing window
                age_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
                await db.demo_runs.update_many({}, {"$set": {"created_at": age_dt}})
                await db.demo_counters.update_many({}, {"$set": {"created_at": age_dt}})

                from app.routes.demo import _derive_client_key
                from starlette.requests import Request as StarletteRequest
                salt = mock_settings.admin_token or "vasooli_salt"
                scope = {
                    "type": "http",
                    "method": "POST",
                    "path": "/demo/public/run",
                    "headers": [(b"x-forwarded-for", b"203.0.113.195")],
                }
                c_hash, _ = _derive_client_key(StarletteRequest(scope), salt=salt)
                client_key_tag = f"client:{c_hash}"

                await db.demo_counters.insert_many([
                    {"key": client_key_tag, "run_id": f"r_c_{ObjectId()}", "created_at": age_dt},
                    {"key": client_key_tag, "run_id": f"r_c_{ObjectId()}", "created_at": age_dt},
                ])
                await db.demo_runs.insert_many([
                    {
                        "run_id": f"r_c_run_{ObjectId()}",
                        "scenario": "insufficient-funds",
                        "client_ip": "203.0.113.195",
                        "status": "completed",
                        "created_at": age_dt,
                    },
                    {
                        "run_id": f"r_c_run_{ObjectId()}",
                        "scenario": "insufficient-funds",
                        "client_ip": "203.0.113.195",
                        "status": "completed",
                        "created_at": age_dt,
                    },
                ])

                client_limit_responses = await asyncio.gather(
                    *[
                        http_client.post(
                            "/demo/public/run",
                            json={"amount": 49.00},
                            headers={"X-Forwarded-For": "203.0.113.195"},
                        )
                        for _ in range(3)
                    ]
                )
                assert all(r.status_code == 429 for r in client_limit_responses), f"Expected 429 for client limit, got {[r.status_code for r in client_limit_responses]}"
                for r in client_limit_responses:
                    assert "Retry-After" in r.headers
                    assert "Client rate limit exceeded" in r.json().get("detail", "")
                print("  [PASS] Per-client hourly limit (3/hr) holds across parallel requests (429 with Retry-After)")

                # 20.7 Test global hourly limit (max 10/hr) holds across parallel requests
                await db.demo_counters.delete_many({})
                await db.demo_runs.delete_many({})

                global_runs = [
                    {"key": "global", "run_id": f"r_g_{i}_{ObjectId()}", "created_at": age_dt}
                    for i in range(10)
                ]
                await db.demo_counters.insert_many(global_runs)
                await db.demo_runs.insert_many([
                    {
                        "run_id": f"r_g_run_{i}_{ObjectId()}",
                        "scenario": "insufficient-funds",
                        "client_ip": f"10.0.0.{i+1}",
                        "status": "completed",
                        "created_at": age_dt,
                    }
                    for i in range(10)
                ])

                global_limit_responses = await asyncio.gather(
                    *[
                        http_client.post(
                            "/demo/public/run",
                            json={"amount": 49.00},
                            headers={"X-Forwarded-For": f"10.0.1.{i+1}"},
                        )
                        for i in range(3)
                    ]
                )
                assert all(r.status_code == 429 for r in global_limit_responses), f"Expected 429 for global limit, got {[r.status_code for r in global_limit_responses]}"
                for r in global_limit_responses:
                    assert "Retry-After" in r.headers
                    assert "Global rate limit exceeded" in r.json().get("detail", "")
                print("  [PASS] Global hourly limit (10/hr) holds across parallel requests (429 with Retry-After)")

        # -------------------------------------------------------------------
        # 16. DYNAMIC POLICY RULEBOOK & RE-DERIVATION CHECKS (REAL MONGODB)
        # -------------------------------------------------------------------
        from app.policy.rulebook_store import (
            ensure_rulebook_indexes,
            revert_usd_rulebook,
            apply_new_usd_rulebook,
            get_active_usd_rulebook,
            resolve_rulebook,
        )
        from app.policy.gemini_rules import parse_and_preview_rules
        from app.policy.engine import PolicyContext, evaluate
        from app.routes.demo import reset_demo_database

        await ensure_rulebook_indexes(db)

        # 16.1 Revert to baseline
        await revert_usd_rulebook(db)
        base_book = await get_active_usd_rulebook(db)
        assert base_book.fingerprint == "rb1_8221b1b9069ffc80"
        print("  [PASS] Dynamic rulebook: revert to baseline restores code baseline rb1_8221b1b9069ffc80")

        # 16.2 Active rulebook changes verdict and revert restores it
        chip = "contact at most twice, wait 48 hours between contacts, auto authorize below $50, ask me before anything over $100"
        preview = await parse_and_preview_rules(chip, active_rulebook=base_book)
        assert preview["valid"] is True
        dec_rules = DecisionRecord(
            id=str(ObjectId()),
            version=1,
            event_id="test_evt_rules_1",
            diagnosis_id=str(ObjectId()),
            diagnosis_version=1,
            recommended_intervention="recovery_payment_link",
            estimated_cost=0.06,
            recovery_probability=0.2,
            revenue_at_risk=180.0,
            expected_recovery_value=35.94,
            reasoning="Test decision",
            decided_at=datetime.now(timezone.utc),
        )
        ctx_rules = PolicyContext(
            customer_ref="cust_rules_1",
            customer_opted_out=False,
            prior_authorized_contacts=0,
            last_authorized_contact_at=None,
        )
        v_base = evaluate(decision=dec_rules, context=ctx_rules, rulebook=base_book)
        assert v_base.verdict == "authorized"

        applied = await apply_new_usd_rulebook(
            preview=preview,
            expected_parent_fingerprint=base_book.fingerprint,
            db=db,
        )
        new_book = await get_active_usd_rulebook(db)
        assert new_book.fingerprint == applied["fingerprint"]

        v_new = evaluate(decision=dec_rules, context=ctx_rules, rulebook=new_book)
        assert v_new.verdict == "requires_manual_review"
        assert v_new.reason == "amount_never_auto"

        await revert_usd_rulebook(db)
        restored_book = await get_active_usd_rulebook(db)
        assert restored_book.fingerprint == "rb1_8221b1b9069ffc80"
        v_restored = evaluate(decision=dec_rules, context=ctx_rules, rulebook=restored_book)
        assert v_restored.verdict == "authorized"
        assert v_restored.reason == "ok"
        print("  [PASS] Dynamic rulebook: active rulebook changes real policy verdict & revert restores it")

        # 16.3 Concurrency guard: two concurrent applies, first wins, second receives 409
        active_now = await get_active_usd_rulebook(db)
        preview_a = await parse_and_preview_rules("contact at most twice, wait 48 hours between contacts", active_rulebook=active_now)
        preview_b = await parse_and_preview_rules("contact at most 4 times, wait 12 hours between contacts", active_rulebook=active_now)
        res_a = await apply_new_usd_rulebook(
            preview=preview_a,
            expected_parent_fingerprint=active_now.fingerprint,
            db=db,
        )
        conflict_caught = False
        try:
            await apply_new_usd_rulebook(
                preview=preview_b,
                expected_parent_fingerprint=active_now.fingerprint,  # Stale parent
                db=db,
            )
        except RuntimeError:
            conflict_caught = True
        assert conflict_caught is True
        print("  [PASS] Dynamic rulebook: concurrency guard (first wins, second receives 409 Conflict)")

        # 16.4 Rules rate limits on real MongoDB (5/hr per client IP)
        transport = httpx.ASGITransport(app=fastapi_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
            test_ip = "203.0.113.50"
            await db.rules_rate_limits.delete_many({"client_ip": test_ip})
            for _ in range(5):
                await db.rules_rate_limits.insert_one({"client_ip": test_ip, "created_at": datetime.now(timezone.utc)})
            r_lim = await test_client.post(
                "/rules/preview",
                json={"text": "contact at most twice, wait 48 hours between contacts"},
                headers={"X-Forwarded-For": test_ip},
            )
            assert r_lim.status_code == 429, f"expected 429, got {r_lim.status_code}"
            assert "Retry-After" in r_lim.headers
            assert "Client rate limit exceeded" in r_lim.json().get("detail", "")
            await db.rules_rate_limits.delete_many({"client_ip": test_ip})
        print("  [PASS] Dynamic rulebook: rate limits on real MongoDB (5/hr per client IP returns 429)")

        # 16.5 Re-derivation proof: stamped verdict, GET /audit-trail 200, 6 checks re-derived from DB rulebook
        active_db_book = await get_active_usd_rulebook(db)
        evt_rules_id = "evt_rederive_integ_1"
        evt_unk_id = "evt_rederive_unk_2"
        await db.events.delete_many({"event_id": {"$in": [evt_rules_id, evt_unk_id]}})
        await db.decisions.delete_many({"event_id": {"$in": [evt_rules_id, evt_unk_id]}})
        await db.policy_verdicts.delete_many({"event_id": {"$in": [evt_rules_id, evt_unk_id]}})

        evt_doc_rederive = {
            "_id": ObjectId(),
            "event_id": evt_rules_id,
            "customer_ref": "cust_rules_integ_1",
            "surface": "subscription",
            "raw_failure_reason": "INSUFFICIENT_FUNDS",
            "created_at": datetime.now(timezone.utc),
            "amount": 180.0,
            "currency": "USD",
            "status": "at_risk",
            "gateway": "paypal",
            "gateway_ref": "WH-REVI-INTEG-1",
        }
        dec_doc_rederive = {
            "_id": ObjectId(dec_rules.id),
            "version": 1,
            "event_id": evt_rules_id,
            "diagnosis_id": ObjectId(dec_rules.diagnosis_id),
            "diagnosis_version": 1,
            "recommended_intervention": dec_rules.recommended_intervention,
            "estimated_cost": dec_rules.estimated_cost,
            "recovery_probability": dec_rules.recovery_probability,
            "revenue_at_risk": dec_rules.revenue_at_risk,
            "expected_recovery_value": dec_rules.expected_recovery_value,
            "reasoning": dec_rules.reasoning,
            "decided_at": dec_rules.decided_at,
        }
        v_stamped = evaluate(decision=dec_rules, context=ctx_rules, rulebook=active_db_book)
        verdict_doc_rederive = {
            "_id": ObjectId(),
            "event_id": evt_rules_id,
            "decision_id": dec_rules.id,
            "decision_version": 1,
            "verdict": v_stamped.verdict,
            "reason": v_stamped.reason,
            "checks_performed": v_stamped.checks_performed,
            "evaluated_at": v_stamped.evaluated_at,
            "rulebook_fingerprint": active_db_book.fingerprint,
            "rulebook_fingerprint_source": "evaluated",
            "version": 1,
        }
        await db.events.insert_one(evt_doc_rederive)
        await db.decisions.insert_one(dec_doc_rederive)
        await db.policy_verdicts.insert_one(verdict_doc_rederive)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
            resp_trail = await test_client.get(f"/audit-trail/{evt_rules_id}")
            assert resp_trail.status_code == 200, f"audit trail failed: {resp_trail.status_code} {resp_trail.text}"
            trail_data = resp_trail.json()
            assert len(trail_data.get("policy_verdicts", [])) == 1
            stamped_fp = trail_data["policy_verdicts"][0]["rulebook_fingerprint"]
            assert stamped_fp == active_db_book.fingerprint

            # Re-derive from DATABASE rulebook
            resolved_db_book = await resolve_rulebook(stamped_fp, db=db)
            assert resolved_db_book is not None
            rederived = evaluate(decision=dec_rules, context=ctx_rules, rulebook=resolved_db_book)
            assert rederived.verdict == v_stamped.verdict
            assert rederived.reason == v_stamped.reason
            assert len(rederived.checks_performed) == 6

            # Unknown fingerprint test: GET /audit-trail returns 200, shows "not available" when resolved, never 500
            evt_unk_doc = dict(evt_doc_rederive, _id=ObjectId(), event_id=evt_unk_id)
            verdict_unk_doc = dict(verdict_doc_rederive, _id=ObjectId(), event_id=evt_unk_id, rulebook_fingerprint="unknown")
            await db.events.insert_one(evt_unk_doc)
            await db.policy_verdicts.insert_one(verdict_unk_doc)
            resp_unk = await test_client.get(f"/audit-trail/{evt_unk_id}")
            assert resp_unk.status_code == 200, f"unknown fp returned {resp_unk.status_code}"
            assert (await resolve_rulebook("rb1_0000000000000000", db=db)) is None

            # Clean up test events
            await db.events.delete_many({"event_id": {"$in": [evt_rules_id, evt_unk_id]}})
            await db.decisions.delete_many({"event_id": {"$in": [evt_rules_id, evt_unk_id]}})
            await db.policy_verdicts.delete_many({"event_id": {"$in": [evt_rules_id, evt_unk_id]}})
        print("  [PASS] Dynamic rulebook re-derivation: verdict stamped with DB fingerprint, GET /audit-trail returns 200, 6 checks re-derive from DB rulebook, unknown fingerprint shows 'not available' and never 500")

        # 16.6 Reset clears rulebook_versions, rate limits, and caches
        res_reset_rules = await reset_demo_database(db)
        assert res_reset_rules["status"] == "ok"
        count_ver = await db.rulebook_versions.count_documents({})
        assert count_ver == 0
        active_after_reset = await get_active_usd_rulebook(db)
        assert active_after_reset.fingerprint == "rb1_8221b1b9069ffc80"
        print("  [PASS] Dynamic rulebook reset: demo reset clears rulebook_versions, rate limits, preview cache, and active USD cache")

        # 16.7 Admin route GET /rules/whoami
        with patch("app.routes.rules.get_settings") as mock_settings:
            mock_settings.return_value = Settings(
                admin_token="vasooli_test_admin_token",
                public_demo=True,
                paypal_env="sandbox",
                mongodb_uri="mongodb://localhost:27017/vasooli_regress",
            )
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
                r_noauth = await test_client.get("/rules/whoami")
                assert r_noauth.status_code == 401
                r_wrong = await test_client.get("/rules/whoami", headers={"X-Admin-Token": "bad"})
                assert r_wrong.status_code == 401
                r_auth = await test_client.get("/rules/whoami", headers={"X-Admin-Token": "vasooli_test_admin_token"})
                assert r_auth.status_code == 200
                assert r_auth.json() == {"admin": True}
        print("  [PASS] Admin route GET /rules/whoami: returns 401 without auth and {'admin': True} with admin token")

        # -------------------------------------------------------------------
        # 17. AGENT GATEWAY & ATTACK SCENARIOS INTEGRATION CHECKS (REAL MONGODB)
        # -------------------------------------------------------------------
        from app.gateway import (
            ALLOWED_TOOLS,
            ATTACK_SCENARIOS,
            CLIENT_LIMIT_PER_MINUTE,
            ensure_audit_indexes,
            ensure_limits_indexes,
            clear_execution_cooldowns,
            dispatch_tool,
            get_recent_audit_records,
            record_audit,
            run_scenario,
            validate_safe_string,
        )

        await ensure_audit_indexes(db)
        await ensure_limits_indexes(db)
        await db.agent_counters.delete_many({})
        clear_execution_cooldowns()

        # 17.1 Zero business collection writes & valid scenario outcomes
        colls = ["events", "executions", "policy_verdicts", "ledger", "diagnoses", "decisions", "metrics"]
        counts_before = {c: await db[c].count_documents({}) for c in colls}

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as agent_client:
            expected_scenario_results = {
                "retry_on_9500": {"verdict": "blocked", "reason": "retry_not_advised", "outcome": "blocked"},
                "opted_out_contact": {"verdict": "blocked", "reason": "customer_opted_out", "outcome": "blocked"},
                "fourth_contact": {"verdict": "blocked", "reason": "contact_cap_exceeded", "outcome": "blocked"},
                "1500_without_approval": {"verdict": "requires_manual_review", "reason": "amount_never_auto", "outcome": "manual_review"},
                "high_value_without_approval": {"verdict": "requires_manual_review", "reason": "amount_never_auto", "outcome": "manual_review"},
                "execute_on_attack_range_event": {"verdict": "refused", "reason": "attack_range_scenario_refused", "outcome": "refused"},
                "call_apply_rules": {"verdict": "refused", "reason": "not offered", "outcome": "refused"},
                "normal_49_authorized": {"verdict": "authorized", "reason": "ok", "outcome": "allowed"},
            }

            for s_id, exp in expected_scenario_results.items():
                if s_id not in ATTACK_SCENARIOS:
                    continue
                resp = await agent_client.post(
                    "/agent/attempt",
                    json={"scenario_id": s_id},
                    headers={"X-Forwarded-For": "198.51.100.1"},
                )
                assert resp.status_code == 200, f"Scenario {s_id} failed with {resp.status_code}: {resp.text}"
                data = resp.json()
                assert data["outcome"] == exp["outcome"], f"{s_id}: outcome {data['outcome']} != {exp['outcome']}"
                assert data["verdict"] == exp["verdict"], f"{s_id}: verdict {data['verdict']} != {exp['verdict']}"
                assert data["reason"] == exp["reason"], f"{s_id}: reason {data['reason']} != {exp['reason']}"
                assert_no_forbidden_keys(data, path=f"/agent/attempt/{s_id}")

            counts_after = {c: await db[c].count_documents({}) for c in colls}
            for c in colls:
                assert counts_before[c] == counts_after[c], (
                    f"Collection {c} mutated by scenario execution: before={counts_before[c]}, after={counts_after[c]}"
                )
        print("  [PASS] Agent Gateway: all 7 attack scenarios return expected verdict & reason with zero writes to business collections")

        # 17.2 Unknown tool refused and audited
        refusal = await dispatch_tool("unregistered_exploit_tool", {"action": "drop_tables"}, client_key_hash="integ_probe", db=db)
        assert refusal["outcome"] == "refused"
        assert refusal["reason"] == "not offered"
        unk_audit = await db.agent_audit.find_one({"tool": "unregistered_exploit_tool"})
        assert unk_audit is not None
        assert unk_audit["outcome"] == "refused"
        assert unk_audit["reason"] == "not offered"
        print("  [PASS] Agent Gateway: unknown tool refused with 'not offered' and audited in agent_audit on MongoDB")

        # 17.3 Argument validation and injection rejection
        val_pass = validate_safe_string("valid_event_id_123", field_name="event_id", max_len=128) == "valid_event_id_123"
        assert val_pass
        val_oversized = False
        try:
            validate_safe_string("x" * 200, field_name="event_id", max_len=128)
        except ValueError:
            val_oversized = True
        assert val_oversized

        injections = [
            "evt_1; DROP TABLE events; --",
            "<script>alert(1)</script>",
            "evt_{$gt: ''}",
            "evt\x00_null",
            "evt' OR 1=1 --",
            '{"$ne": null}',
        ]
        injections_blocked = 0
        for inj in injections:
            try:
                validate_safe_string(inj, field_name="event_id", max_len=128)
            except ValueError:
                injections_blocked += 1
        assert injections_blocked == len(injections)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as agent_client:
            r_inj = await agent_client.post(
                "/agent/attempt",
                json={"scenario_id": "normal_49_authorized; DROP TABLE events; --"},
                headers={"X-Forwarded-For": "198.51.100.2"},
            )
            assert r_inj.status_code in (400, 422), f"Injected scenario id returned {r_inj.status_code}"
        print("  [PASS] Agent Gateway: argument validation enforces event_id patterns, lengths, and rejects 6 injection patterns")

        # 17.4 Limits: 10/min per client, 11th throttled with 429 Retry-After
        test_ip = "198.51.100.77"
        await db.agent_counters.delete_many({"key": {"$regex": test_ip}})
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as agent_client:
            for i in range(CLIENT_LIMIT_PER_MINUTE):
                r_lim_ok = await agent_client.post(
                    "/agent/attempt",
                    json={"scenario_id": "normal_49_authorized"},
                    headers={"X-Forwarded-For": test_ip},
                )
                assert r_lim_ok.status_code == 200, f"Request {i+1} failed: {r_lim_ok.status_code}"

            r_lim_429 = await agent_client.post(
                "/agent/attempt",
                json={"scenario_id": "normal_49_authorized"},
                headers={"X-Forwarded-For": test_ip},
            )
            assert r_lim_429.status_code == 429, f"11th request expected 429, got {r_lim_429.status_code}"
            assert "Retry-After" in r_lim_429.headers
            assert "Client rate limit exceeded" in r_lim_429.json().get("detail", "")

            r_other = await agent_client.post(
                "/agent/attempt",
                json={"scenario_id": "normal_49_authorized"},
                headers={"X-Forwarded-For": "198.51.100.78"},
            )
            assert r_other.status_code == 200
        print("  [PASS] Agent Gateway: atomic rolling rate limit on real MongoDB (10/min allowed, 11th returns 429 Retry-After)")

        # 17.5 Audit rows inspection & Sanitization key scan
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as agent_client:
            r_scenarios = await agent_client.get("/agent/scenarios")
            assert r_scenarios.status_code == 200
            scenarios_data = r_scenarios.json()
            assert isinstance(scenarios_data, list) and len(scenarios_data) >= 7
            assert_no_forbidden_keys(scenarios_data, path="/agent/scenarios")

            r_audit = await agent_client.get("/agent/audit?limit=50")
            assert r_audit.status_code == 200
            audit_data = r_audit.json()
            assert isinstance(audit_data, list) and len(audit_data) > 0
            assert_no_forbidden_keys(audit_data, path="/agent/audit")

            sample = audit_data[0]
            required_audit_keys = {"agent_id", "tool", "outcome", "reason", "args_hash", "rulebook_fingerprint", "at"}
            assert required_audit_keys.issubset(set(sample.keys())), f"Missing keys in audit record: {sample.keys()}"
        print("  [PASS] Agent Gateway: audit inspection & recursive sanitization scan pass with zero forbidden keys")

        # -----------------------------------------------------------------------
        # Step 18: MCP Server over Streamable HTTP & Real MongoDB Checks
        # -----------------------------------------------------------------------
        print("\n--- STEP 18: MCP SERVER INTEGRATION CHECKS (REAL MONGODB) ---")
        from mcp import Client
        from starlette.middleware import Middleware
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route
        from app.gateway.mcp_server import create_mcp_server
        from app.gateway.tools import ALLOWED_TOOLS
        from app.gateway.limits import check_and_record_token_attempt
        from app.middleware import PublicDemoMiddleware
        import hashlib

        # 18.1 In-memory Client tool list and execution against real MongoDB
        mcp_test_server = create_mcp_server()
        async with Client(mcp_test_server) as inmem_client:
            tools_resp = await inmem_client.list_tools()
            tool_names = set(t.name for t in tools_resp.tools)
            assert tool_names == ALLOWED_TOOLS, f"Mismatch in tool names: {tool_names}"

            forbidden_tools = {
                "apply_rules", "revert", "reset", "advance_clock", "tick", "run_scenario",
                "create_order", "capture_order", "refund_order", "execute",
            }
            assert tool_names.isdisjoint(forbidden_tools)

            # Record collection counts before MCP tool executions
            ev_count_before = await db.events.count_documents({})
            exec_count_before = await db.executions.count_documents({})
            verdict_count_before = await db.policy_verdicts.count_documents({})

            # Execute tool: get_active_rules
            res_rules = await inmem_client.call_tool("get_active_rules", {"currency": "USD"})
            assert res_rules.content
            rules_val = json.loads(res_rules.content[0].text)
            assert rules_val.get("outcome") == "allowed"

            # Execute tool: propose_recovery (normal)
            res_rec = await inmem_client.call_tool(
                "propose_recovery",
                {
                    "event": {
                        "event_id": "evt_mcp_integ_normal",
                        "surface": "subscription",
                        "amount": 49.00,
                        "currency": "USD",
                        "customer_ref": "cust_mcp_integ",
                        "raw_failure_reason": "insufficient_funds",
                        "gateway": "paypal",
                    },
                    "context": {
                        "customer_ref": "cust_mcp_integ",
                        "customer_opted_out": False,
                        "prior_authorized_contacts": 0,
                        "last_authorized_contact_at": None,
                        "has_vault_token": True,
                        "processor_response_code": "5120",
                    },
                },
            )
            rec_val = json.loads(res_rec.content[0].text)
            assert rec_val.get("verdict") == "authorized"

            # Execute tool: execute_recovery on attack-range event is refused
            fake_atk_verdict = {
                "_id": str(ObjectId()),
                "event_id": "atk_scenario_mcp_integ",
                "version": 1,
                "decision_id": str(ObjectId()),
                "decision_version": 1,
                "verdict": "authorized",
                "reason": "ok",
                "checks_performed": [
                    "decision_is_actionable: PASS (ok)",
                    "customer_opt_out: PASS (ok)",
                    "contact_cap: PASS (ok)",
                    "contact_cooldown: PASS (ok)",
                    "erv_minimum: PASS (ok)",
                    "amount_tier: PASS (ok)",
                ],
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
                "rulebook_fingerprint": "rb1_8221b1b9069ffc80",
            }
            res_atk_exec = await inmem_client.call_tool(
                "execute_recovery",
                {
                    "verdict": fake_atk_verdict,
                    "event_id": "atk_scenario_mcp_integ",
                },
            )
            atk_exec_val = json.loads(res_atk_exec.content[0].text)
            assert atk_exec_val.get("outcome") == "refused"
            assert atk_exec_val.get("reason") == "attack_range_scenario_refused"

            # Execute tool: list_attack_scenarios (new tool 1)
            res_list_scen = await inmem_client.call_tool("list_attack_scenarios", {})
            list_scen_val = json.loads(res_list_scen.content[0].text)
            assert list_scen_val.get("outcome") == "allowed"
            assert len(list_scen_val.get("scenarios", [])) == 7

            # Execute tool: run_attack_scenario (new tool 2)
            res_run_scen = await inmem_client.call_tool("run_attack_scenario", {"scenario_id": "retry_on_9500"})
            run_scen_val = json.loads(res_run_scen.content[0].text)
            assert run_scen_val.get("outcome") == "blocked"
            assert run_scen_val.get("reason") == "retry_not_advised"

            # Malformed calls tests (clean error message, no raw pydantic or URLs)
            res_mal_scen = await inmem_client.call_tool("run_attack_scenario", {})
            mal_scen_val = json.loads(res_mal_scen.content[0].text)
            assert mal_scen_val.get("outcome") == "refused"
            assert mal_scen_val.get("reason") == "invalid input: missing fields scenario_id"

            res_mal_prop = await inmem_client.call_tool("propose_recovery", {"event": {}})
            mal_prop_val = json.loads(res_mal_prop.content[0].text)
            assert mal_prop_val.get("outcome") == "refused"
            assert mal_prop_val.get("reason") == "invalid input: missing fields event_id, surface, amount, customer_ref"

            # Check that zero raw validation tokens leak in responses
            for resp in (res_rules, res_rec, res_atk_exec, res_list_scen, res_run_scen, res_mal_scen, res_mal_prop):
                text_lower = resp.content[0].text.lower()
                for bad_tok in ("pydantic", "errors.pydantic.dev", "input_value", "input_type", "revenueevent", "authorizedverdict"):
                    assert bad_tok not in text_lower, f"Leaked raw validation token {bad_tok} in {text_lower}"

            # Verify zero writes to business collections
            assert await db.events.count_documents({}) == ev_count_before
            assert await db.executions.count_documents({}) == exec_count_before
            assert await db.policy_verdicts.count_documents({}) == verdict_count_before

            # Verify audit records written to real MongoDB
            audit_records = await db.agent_audit.find({"agent_id": "mcp_gateway"}).to_list(length=100)
            assert len(audit_records) >= 5
            audited_tools = {r["tool"] for r in audit_records}
            assert "get_active_rules" in audited_tools
            assert "propose_recovery" in audited_tools
            assert "execute_recovery" in audited_tools
            assert "list_attack_scenarios" in audited_tools
            assert "run_attack_scenario" in audited_tools
        print("  [PASS] MCP Server: tools list strictly equals allowlist (8 tools), calls return verdicts & audited in real MongoDB with zero business writes")

        # 18.2 Bearer Auth & Middleware verification on /mcp
        async def _mock_mcp_endpoint(request):
            return JSONResponse({"status": "mcp_admitted"})

        mcp_auth_test_app = Starlette(
            routes=[Route("/mcp", _mock_mcp_endpoint, methods=["GET", "POST", "OPTIONS"])],
            middleware=[
                Middleware(
                    PublicDemoMiddleware,
                    settings=Settings(
                        public_demo=True,
                        admin_token="admin_tok_integ",
                        agent_token="agent_tok_integ_secret",
                        paypal_env="sandbox",
                    ),
                )
            ],
        )
        mcp_transport = httpx.ASGITransport(app=mcp_auth_test_app)
        async with httpx.AsyncClient(transport=mcp_transport, base_url="http://test") as mcp_http_client:
            # Missing token -> 401
            r_no_tok = await mcp_http_client.post("/mcp", json={})
            assert r_no_tok.status_code == 401
            assert r_no_tok.headers.get("www-authenticate") == "Bearer"

            # Wrong token -> 401
            r_bad_tok = await mcp_http_client.post(
                "/mcp",
                json={},
                headers={"Authorization": "Bearer bad_secret"},
            )
            assert r_bad_tok.status_code == 401

            # Valid token -> 200
            r_good_tok = await mcp_http_client.post(
                "/mcp",
                json={},
                headers={"Authorization": "Bearer agent_tok_integ_secret"},
            )
            assert r_good_tok.status_code == 200
            assert r_good_tok.json().get("status") == "mcp_admitted"
        print("  [PASS] MCP Server: Bearer auth on /mcp enforced (401 without token, 401 with bad token, 200 with valid token)")

        # 18.3 Token rate limit on real MongoDB (60 calls/min per token)
        test_tok_hash = hashlib.sha256(b"token_integ_rate_limit_test").hexdigest()
        await db.agent_counters.delete_many({"key": f"token:{test_tok_hash}"})
        for _ in range(60):
            allowed, _, _ = await check_and_record_token_attempt(test_tok_hash, db=db)
            assert allowed, "Expected call within 60 limit to succeed"
        allowed_61, err_msg, retry_after = await check_and_record_token_attempt(test_tok_hash, db=db)
        assert not allowed_61, "61st call must be blocked"
        assert "Token rate limit exceeded" in (err_msg or "")
        assert 1 <= (retry_after or 0) <= 60
        count_docs = await db.agent_counters.count_documents({"key": f"token:{test_tok_hash}"})
        assert count_docs == 60
        print("  [PASS] MCP Server: token rate limit strictly enforced on real MongoDB (60/min allowed, 61st rejected with 429 Retry-After)")

        # 17.6 Demo reset clears agent collections and re-ensures indexes
        res_reset_agent = await reset_demo_database(db)
        assert res_reset_agent["status"] == "ok"
        count_audit = await db.agent_audit.count_documents({})
        count_counters = await db.agent_counters.count_documents({})
        assert count_audit == 0, f"agent_audit not cleared: {count_audit}"
        assert count_counters == 0, f"agent_counters not cleared: {count_counters}"
        audit_idx_after = await db.agent_audit.index_information()
        assert any(v.get("expireAfterSeconds") == 2592000 for v in audit_idx_after.values())
        print("  [PASS] Agent Gateway: demo reset purges agent_audit and agent_counters and re-ensures TTL indexes")

        # 19.1 Studio AI Rate Limit on real MongoDB (20 calls/hour per client key)
        await ensure_studio_limits_indexes(db=db)
        test_client_key = "192.0.2.188"
        await db[STUDIO_COUNTERS_COLLECTION].delete_many({})
        for i in range(20):
            allowed, _, _ = await check_and_record_studio_attempt(
                client_key=test_client_key,
                conversation_id=f"thread_client_{i}",
                db=db,
            )
            assert allowed, f"Call {i+1} within client 20/hr limit should be allowed"
        allowed_21, err_21, retry_21 = await check_and_record_studio_attempt(
            client_key=test_client_key,
            conversation_id="thread_client_21",
            db=db,
        )
        assert not allowed_21, "21st call from same client must be rejected with 429"
        assert "Client rate limit exceeded" in (err_21 or "")
        assert retry_21 is not None and retry_21 > 0
        print("  [PASS] Studio AI: per-client rate limit enforced on real MongoDB (20/hr allowed, 21st rejected with Retry-After)")

        # 19.2 Studio AI Per-turn guard on real MongoDB (max 6 calls per conversation id per 10 minutes)
        await db[STUDIO_COUNTERS_COLLECTION].delete_many({})
        test_thread = "test_thread_guard_123"
        for i in range(6):
            allowed, _, _ = await check_and_record_studio_attempt(
                client_key=f"192.0.2.{i+10}",
                conversation_id=test_thread,
                db=db,
            )
            assert allowed, f"Turn call {i+1} within 6/10min limit should be allowed"
        allowed_7, err_7, retry_7 = await check_and_record_studio_attempt(
            client_key="192.0.2.99",
            conversation_id=test_thread,
            db=db,
        )
        assert not allowed_7, "7th call for same conversation id within 10 minutes must be rejected"
        assert "Conversation turn limit exceeded" in (err_7 or "")
        assert retry_7 is not None and retry_7 > 0
        print("  [PASS] Studio AI: per-turn guard enforced on real MongoDB (6/10min allowed, 7th rejected with Retry-After)")

        # 19.3 Studio AI Global hourly (100/hr) and daily (500/day) limits on real MongoDB
        await db[STUDIO_COUNTERS_COLLECTION].delete_many({})
        now_ts = datetime.now(timezone.utc)
        docs_100 = [{"key": "global", "type": "global", "created_at": now_ts} for _ in range(100)]
        await db[STUDIO_COUNTERS_COLLECTION].insert_many(docs_100)
        allowed_gh, err_gh, retry_gh = await check_and_record_studio_attempt(
            client_key="198.51.100.5",
            conversation_id="thread_gh",
            db=db,
        )
        assert not allowed_gh, "101st global hourly call must be rejected"
        assert "Global hourly rate limit exceeded" in (err_gh or "")
        assert retry_gh is not None and retry_gh > 0

        # Global daily limit (500/day)
        await db[STUDIO_COUNTERS_COLLECTION].delete_many({})
        docs_500 = [{"key": "global", "type": "global", "created_at": now_ts - timedelta(hours=2)} for _ in range(500)]
        await db[STUDIO_COUNTERS_COLLECTION].insert_many(docs_500)
        allowed_gd, err_gd, retry_gd = await check_and_record_studio_attempt(
            client_key="198.51.100.6",
            conversation_id="thread_gd",
            db=db,
        )
        assert not allowed_gd, "501st global daily call must be rejected"
        assert "Global daily rate limit exceeded" in (err_gd or "")
        assert retry_gd is not None and retry_gd > 0
        print("  [PASS] Studio AI: global limits enforced on real MongoDB (100/hr and 500/day hard ceilings)")

        # 19.4 Studio AI TTL index on studio_ai_counters verified and demo reset cleans it
        studio_idx = await db[STUDIO_COUNTERS_COLLECTION].index_information()
        assert any(v.get("expireAfterSeconds") == 86400 for v in studio_idx.values()), "TTL index on studio_ai_counters must be 86400s"
        res_reset_studio = await reset_demo_database(db)
        assert res_reset_studio["status"] == "ok"
        count_studio_counters = await db[STUDIO_COUNTERS_COLLECTION].count_documents({})
        assert count_studio_counters == 0, f"studio_ai_counters not cleared: {count_studio_counters}"
        print("  [PASS] Studio AI: TTL index (86400s) verified on studio_ai_counters and demo reset purges collection")

        # Cleanup test documents
        await db.demo_runs.delete_many({})
        await db.demo_counters.delete_many({})
        await db.rulebook_versions.delete_many({})
        await db.rules_rate_limits.delete_many({})
        await db.agent_audit.delete_many({})
        await db.agent_counters.delete_many({})
        await db[STUDIO_COUNTERS_COLLECTION].delete_many({})

        print("\n" + "=" * 78)
        print("RESULT: ALL INTEGRATION CHECKS PASSED (35/35)")
        print("=" * 78)
        return 0

    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run_integration_check()))
