"""PayPal Webhook Reconciler.

Reconciles verified PayPal webhook events against executions and events:
1. PAYMENT.CAPTURE.COMPLETED:
   - Match execution by custom_id ("ev:<event_id>") and order_id == execution.gateway_ref.
   - Write a WebhookVerification (gateway=paypal, assertion=gateway_verified, outcome=recovered).
   - Transition event status to recovered.
2. INVOICING.INVOICE.PAID:
   - Classify latest payments.transactions[]:
     type PAYPAL -> gateway_verified
     type EXTERNAL -> merchant_asserted
   - Status PAID with due_amount 0 -> recovered, transition event status to recovered.
   - Status PARTIALLY_PAID -> record verified partial amount with amount_mismatch=True, leave event open.
   - Always use event create_time, never payment_date_time.
3. Unmatched events:
   - Stored and acknowledged without error, no verification record written.
4. Idempotency:
   - Replaying any event returns duplicate status; no second record created.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pydantic import BaseModel, ConfigDict
from pymongo.errors import DuplicateKeyError

from app.db import get_database
from app.models.decision import MONEY_PRECISION
from app.models.events import RevenueEvent, statuses_that_may_become
from app.models.verification import (
    GATEWAY_VERIFIED,
    MERCHANT_ASSERTED,
    RECOVERED_OUTCOME,
    WebhookVerification,
    amounts_differ,
)
from app.webhooks.store import DuplicateVerification

logger = logging.getLogger(__name__)


class PayPalReconcileResult(BaseModel):
    """Result of reconciling one PayPal webhook event."""

    model_config = ConfigDict(extra="forbid")

    received: bool = True
    processed: bool
    matched: bool
    duplicate: bool = False
    detail: str
    paypal_event_id: str | None = None
    event_type: str | None = None
    verification_id: str | None = None
    outcome: str | None = None
    amount_recovered: float | None = None
    assertion: str | None = None
    event_status: str | None = None
    revenue_event_id: str | None = None


def _utc_now() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def _parse_timestamp(iso_str: str | None) -> datetime:
    if not iso_str:
        return _utc_now()
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return _utc_now()


async def _db_collection_names(db: Any) -> list[str]:
    if hasattr(db, "list_collection_names"):
        return await db.list_collection_names()
    return []


async def _resolve_expected_amount(execution: dict[str, Any], db: Any) -> float:
    """Resolve expected amount at risk from verdict -> decision chain or fallbacks."""
    verdict_id = execution.get("policy_verdict_id")
    cols = await _db_collection_names(db)

    if verdict_id and "policy_verdicts" in cols and "decisions" in cols:
        try:
            verdict = await db.policy_verdicts.find_one({"_id": ObjectId(str(verdict_id))})
            if verdict and verdict.get("decision_id"):
                decision = await db.decisions.find_one({"_id": ObjectId(str(verdict["decision_id"]))})
                if decision and decision.get("revenue_at_risk") is not None:
                    return round(float(decision["revenue_at_risk"]), MONEY_PRECISION)
        except Exception:
            pass

    event_id = execution.get("event_id")
    if event_id and "events" in cols:
        try:
            event = await db.events.find_one({"event_id": event_id})
            if event and event.get("amount") is not None:
                return round(float(event["amount"]), MONEY_PRECISION)
        except Exception:
            pass

    if "amount_expected" in execution and execution["amount_expected"] is not None:
        return round(float(execution["amount_expected"]), MONEY_PRECISION)
    if "amount" in execution and execution["amount"] is not None:
        return round(float(execution["amount"]), MONEY_PRECISION)

    return 100.0


async def _get_event_status(event_id: str | None, db: Any) -> str | None:
    if not event_id:
        return None
    cols = await _db_collection_names(db)
    if "events" not in cols:
        return None
    doc = await db.events.find_one({"event_id": event_id}, {"status": 1})
    return str(doc.get("status")) if doc else None


async def _transition_event_status(event_id: str, target: str, db: Any) -> None:
    cols = await _db_collection_names(db)
    if "events" in cols:
        allowed = [str(s) for s in statuses_that_may_become(target)]
        await db.events.update_one(
            {"event_id": event_id, "status": {"$in": allowed}},
            {"$set": {"status": target}},
        )


async def reconcile_paypal_event(
    event_data: dict[str, Any],
    db: Any = None,
) -> PayPalReconcileResult:
    """Reconcile one verified PayPal webhook event.

    Args:
        event_data: PayPal webhook payload dictionary.
        db: Optional database handle (defaults to get_database()).

    Returns:
        PayPalReconcileResult describing what was performed.
    """
    if db is None:
        db = get_database()

    paypal_event_id = event_data.get("id")
    event_type = event_data.get("event_type")
    resource = event_data.get("resource", {})

    if not paypal_event_id or not event_type:
        return PayPalReconcileResult(
            processed=False,
            matched=False,
            detail="Missing event id or event_type",
            paypal_event_id=paypal_event_id,
            event_type=event_type,
        )

    # 1. Pre-flight check: idempotency on paypal_event_id
    existing = await db.verifications.find_one({"paypal_event_id": paypal_event_id})
    if existing:
        logger.info(
            "PayPal event %s already reconciled as verification %s",
            paypal_event_id,
            existing.get("_id"),
        )
        return PayPalReconcileResult(
            processed=True,
            matched=True,
            duplicate=True,
            detail=f"PayPal event {paypal_event_id} already recorded",
            paypal_event_id=paypal_event_id,
            event_type=event_type,
            verification_id=str(existing["_id"]),
            outcome=existing.get("outcome"),
            amount_recovered=existing.get("amount_recovered"),
            assertion=existing.get("assertion"),
            event_status=await _get_event_status(existing.get("event_id"), db),
        )

    create_time = event_data.get("create_time")
    # Always use event create_time, never payment_date_time
    verified_at = _parse_timestamp(create_time)

    # 2. Case: PAYMENT.CAPTURE.DECLINED (Step 2)
    if event_type == "PAYMENT.CAPTURE.DECLINED":
        custom_id = resource.get("custom_id")
        if custom_id and custom_id.startswith("ev:"):
            orig_event_id = custom_id[3:]
            order_id = (
                resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
                or resource.get("order_id")
            )
            exec_query: dict[str, Any] = {"gateway": "paypal"}
            if order_id:
                exec_query["gateway_ref"] = order_id
            if orig_event_id:
                exec_query["event_id"] = orig_event_id

            execution = await db.executions.find_one(exec_query)
            if not execution and orig_event_id:
                execution = await db.executions.find_one({"event_id": orig_event_id, "gateway": "paypal"})

            if execution:
                await db.executions.update_one(
                    {"_id": execution["_id"]},
                    {"$set": {"successful": False, "recovery_outcome": "not_recovered"}},
                )

                amount_expected = await _resolve_expected_amount(execution, db)
                record = WebhookVerification(
                    event_id=execution["event_id"],
                    execution_id=str(execution["_id"]),
                    gateway="paypal",
                    gateway_ref=order_id or execution.get("gateway_ref"),
                    assertion=GATEWAY_VERIFIED,
                    outcome="not_recovered",
                    paypal_event_id=paypal_event_id,
                    paypal_event="PAYMENT.CAPTURE.DECLINED",
                    amount_recovered=0.0,
                    amount_expected=amount_expected,
                    amount_mismatch=False,
                    verified_at=verified_at,
                )
                from app.webhooks.store import insert as insert_verification
                verif_id = await insert_verification(record)
                return PayPalReconcileResult(
                    processed=True,
                    matched=True,
                    duplicate=False,
                    detail=f"Loop guard: recovery capture declined for event {orig_event_id}, recorded verification {verif_id}",
                    paypal_event_id=paypal_event_id,
                    event_type=event_type,
                    verification_id=verif_id,
                    outcome="not_recovered",
                    amount_recovered=0.0,
                    assertion=GATEWAY_VERIFIED,
                    event_status=await _get_event_status(orig_event_id, db),
                )
            else:
                logger.warning(
                    "Loop guard: recovery capture declined with custom_id %s but no execution found",
                    custom_id,
                )
                return PayPalReconcileResult(
                    processed=False,
                    matched=False,
                    duplicate=False,
                    detail=f"Unmatched recovery capture decline: custom_id={custom_id}, order_id={order_id}",
                    paypal_event_id=paypal_event_id,
                    event_type=event_type,
                )

        capture_id = resource.get("id")
        if not capture_id:
            return PayPalReconcileResult(
                processed=False,
                matched=False,
                detail="Missing capture id in resource",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
            )
        event_id = f"pp_{capture_id}"

        # Idempotency check: has this event already been ingested?
        existing_event = await db.events.find_one({"event_id": event_id})
        if existing_event:
            return PayPalReconcileResult(
                processed=True,
                matched=True,
                duplicate=True,
                detail=f"PayPal decline {event_id} already ingested",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
                event_status=str(existing_event.get("status")),
            )

        order_id = (
            resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
            or resource.get("order_id")
        )
        amount_val = resource.get("amount", {}).get("value", "0.0")
        amount = float(amount_val)
        currency = resource.get("amount", {}).get("currency_code", "USD")
        proc_resp = resource.get("processor_response", {})
        response_code = proc_resp.get("response_code")
        payment_advice_code = proc_resp.get("payment_advice_code") or resource.get("payment_advice_code")

        gateway_detail: dict[str, Any] = {"processor_response": proc_resp}
        if payment_advice_code:
            gateway_detail["payment_advice_code"] = payment_advice_code

        raw_failure_reason = f"paypal_{response_code}" if response_code else "paypal_declined"

        # Resolve customer_ref: from custom_id "cust:" if present in payload, else charge_attempts
        custom_id = resource.get("custom_id")
        customer_ref = None
        if custom_id and custom_id.startswith("cust:"):
            customer_ref = custom_id[5:]

        if not customer_ref and order_id:
            attempt = await db.charge_attempts.find_one({"order_id": order_id})
            if attempt and attempt.get("customer_ref"):
                customer_ref = attempt["customer_ref"]

        if not customer_ref:
            # Cannot be resolved yet: store in paypal_events as unmatched, return 200
            await db.paypal_events.update_one(
                {"paypal_event_id": paypal_event_id},
                {"$set": {
                    "paypal_event_id": paypal_event_id,
                    "event_type": event_type,
                    "payload": event_data,
                    "unmatched": True,
                    "unmatched_reason": "customer_ref_unresolved",
                    "order_id": order_id,
                    "received_at": verified_at,
                }},
                upsert=True,
            )
            return PayPalReconcileResult(
                processed=False,
                matched=False,
                duplicate=False,
                detail=f"Unmatched decline: customer_ref unresolved for order {order_id}",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
            )

        rev_event = RevenueEvent(
            event_id=event_id,
            surface="payment",
            gateway="paypal",
            gateway_ref=order_id,
            amount=amount,
            currency=currency,
            customer_ref=customer_ref,
            raw_failure_reason=raw_failure_reason,
            gateway_detail=gateway_detail,
            status="at_risk",
            created_at=verified_at,
        )

        payload = rev_event.model_dump()
        insert_only = {"created_at": payload.pop("created_at"), "status": payload.pop("status")}
        await db.events.update_one(
            {"event_id": event_id},
            {"$set": payload, "$setOnInsert": insert_only},
            upsert=True,
        )

        # Mark matched in paypal_events if present
        await db.paypal_events.update_one(
            {"paypal_event_id": paypal_event_id},
            {"$set": {"unmatched": False, "matched_event_id": event_id}},
        )

        return PayPalReconcileResult(
            processed=True,
            matched=True,
            duplicate=False,
            detail="Ingested PAYMENT.CAPTURE.DECLINED as RevenueEvent",
            paypal_event_id=paypal_event_id,
            event_type=event_type,
            event_status="at_risk",
            revenue_event_id=event_id,
        )

    # 3. Case: VAULT.PAYMENT-TOKEN.CREATED (Step 4)
    if event_type == "VAULT.PAYMENT-TOKEN.CREATED":
        vault_id = resource.get("id")
        if not vault_id:
            return PayPalReconcileResult(
                processed=False,
                matched=False,
                detail="Missing vault token id in resource",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
            )

        # Idempotency check on vault_id
        if hasattr(db, "customer_vault"):
            existing_vault = await db.customer_vault.find_one({"vault_id": vault_id})
            if existing_vault:
                return PayPalReconcileResult(
                    processed=True,
                    matched=True,
                    duplicate=True,
                    detail=f"Vault token {vault_id} already recorded",
                    paypal_event_id=paypal_event_id,
                    event_type=event_type,
                )

        card_info = resource.get("payment_source", {}).get("card", {})
        card_brand = card_info.get("brand", "UNKNOWN")
        last_digits = card_info.get("last_digits", "")

        # Resolve customer_ref: prefer charge_attempts from metadata.order_id, else custom_id
        customer_ref = None
        order_id = resource.get("metadata", {}).get("order_id")
        if order_id and hasattr(db, "charge_attempts"):
            attempt = await db.charge_attempts.find_one({"order_id": order_id})
            if attempt and attempt.get("customer_ref"):
                customer_ref = attempt["customer_ref"]

        if not customer_ref:
            custom_id = resource.get("custom_id")
            if custom_id and custom_id.startswith("cust:"):
                customer_ref = custom_id[5:]

        if not customer_ref:
            logger.info("Unmatched vault token %s (no customer_ref resolved)", vault_id)
            return PayPalReconcileResult(
                processed=False,
                matched=False,
                duplicate=False,
                detail=f"Unmatched vault token: customer_ref unresolved for order {order_id}",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
            )

        from app.gateways.paypal.vault import store_vault_token
        doc, inserted = await store_vault_token(
            customer_ref=customer_ref,
            vault_id=vault_id,
            card_brand=card_brand,
            last_digits=last_digits,
            source_event_id=paypal_event_id,
            status="active",
            created_at=verified_at,
            db=db,
        )

        return PayPalReconcileResult(
            processed=True,
            matched=True,
            duplicate=not inserted,
            detail="Stored active vault payment token",
            paypal_event_id=paypal_event_id,
            event_type=event_type,
        )

    # 4. Case: PAYMENT.CAPTURE.COMPLETED
    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        order_id = (
            resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
            or resource.get("order_id")
        )
        custom_id = resource.get("custom_id")
        event_id = None
        if custom_id and custom_id.startswith("ev:"):
            event_id = custom_id[3:]

        exec_query: dict[str, Any] = {"gateway": "paypal"}
        if order_id and event_id:
            exec_query["gateway_ref"] = order_id
            exec_query["event_id"] = event_id
        elif order_id:
            exec_query["gateway_ref"] = order_id
        elif event_id:
            exec_query["event_id"] = event_id
        else:
            return PayPalReconcileResult(
                processed=False,
                matched=False,
                duplicate=False,
                detail="Missing order_id and event_id for capture completion matching",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
            )

        execution = await db.executions.find_one(exec_query)
        if not execution:
            logger.info(
                "Unmatched PayPal capture %s (order_id=%s, custom_id=%s); acknowledging without verification",
                paypal_event_id,
                order_id,
                custom_id,
            )
            return PayPalReconcileResult(
                processed=False,
                matched=False,
                duplicate=False,
                detail=f"Unmatched capture: order_id={order_id}, custom_id={custom_id}",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
            )

        amount_val = resource.get("amount", {}).get("value", "0.0")
        amount_recovered = float(amount_val)
        amount_expected = await _resolve_expected_amount(execution, db)
        amount_mismatch = amounts_differ(amount_recovered, amount_expected)

        record = WebhookVerification(
            event_id=execution["event_id"],
            execution_id=str(execution["_id"]),
            gateway="paypal",
            gateway_ref=order_id,
            assertion=GATEWAY_VERIFIED,
            outcome=RECOVERED_OUTCOME,
            paypal_event_id=paypal_event_id,
            paypal_event="PAYMENT.CAPTURE.COMPLETED",
            amount_recovered=amount_recovered,
            amount_expected=amount_expected,
            amount_mismatch=amount_mismatch,
            verified_at=verified_at,
        )

        try:
            res = await db.verifications.insert_one(record.model_dump(exclude_none=True))
            vid = str(res.inserted_id)
        except DuplicateKeyError:
            existing = await db.verifications.find_one({"paypal_event_id": paypal_event_id})
            return PayPalReconcileResult(
                processed=True,
                matched=True,
                duplicate=True,
                detail=f"PayPal event {paypal_event_id} already recorded (concurrent race)",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
                verification_id=str(existing["_id"]) if existing else None,
                outcome=record.outcome,
                amount_recovered=amount_recovered,
                assertion=GATEWAY_VERIFIED,
                event_status=await _get_event_status(execution["event_id"], db),
            )

        await db.executions.update_one(
            {"_id": execution["_id"]},
            {"$set": {"successful": True, "recovery_outcome": "recovered"}},
        )
        await _transition_event_status(execution["event_id"], "recovered", db)
        logger.info(
            "Reconciled PAYMENT.CAPTURE.COMPLETED %s for event %s (execution %s, amount %.2f)",
            paypal_event_id,
            execution["event_id"],
            execution["_id"],
            amount_recovered,
        )
        return PayPalReconcileResult(
            processed=True,
            matched=True,
            duplicate=False,
            detail="Reconciled PAYMENT.CAPTURE.COMPLETED",
            paypal_event_id=paypal_event_id,
            event_type=event_type,
            verification_id=vid,
            outcome=RECOVERED_OUTCOME,
            amount_recovered=amount_recovered,
            assertion=GATEWAY_VERIFIED,
            event_status="recovered",
        )

    # 3. Case: INVOICING.INVOICE.PAID
    if event_type == "INVOICING.INVOICE.PAID":
        invoice = resource.get("invoice", {})
        invoice_id = invoice.get("id")
        if not invoice_id:
            return PayPalReconcileResult(
                processed=False,
                matched=False,
                detail="Missing invoice id in resource",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
            )

        execution = await db.executions.find_one({"gateway": "paypal", "gateway_ref": invoice_id})
        if not execution:
            logger.info(
                "Unmatched PayPal invoice %s (%s); acknowledging without verification",
                paypal_event_id,
                invoice_id,
            )
            return PayPalReconcileResult(
                processed=False,
                matched=False,
                duplicate=False,
                detail=f"Unmatched invoice: {invoice_id}",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
            )

        # Classify latest transaction: PAYPAL -> gateway_verified, EXTERNAL -> merchant_asserted
        transactions = invoice.get("payments", {}).get("transactions", [])
        latest_tx = transactions[-1] if transactions else {}
        tx_type = latest_tx.get("type", "PAYPAL")
        assertion = MERCHANT_ASSERTED if tx_type == "EXTERNAL" else GATEWAY_VERIFIED

        paid_val = invoice.get("payments", {}).get("paid_amount", {}).get("value")
        if paid_val is not None:
            amount_recovered = float(paid_val)
        else:
            amount_val = latest_tx.get("amount", {}).get("value", "0.0")
            amount_recovered = float(amount_val)
        due_val = float(invoice.get("due_amount", {}).get("value", 0.0))
        amount_expected = await _resolve_expected_amount(execution, db)
        inv_status = invoice.get("status")

        if inv_status == "PAID" and due_val == 0.0:
            outcome = RECOVERED_OUTCOME
            amount_mismatch = amounts_differ(amount_recovered, amount_expected)

            record = WebhookVerification(
                event_id=execution["event_id"],
                execution_id=str(execution["_id"]),
                gateway="paypal",
                gateway_ref=invoice_id,
                assertion=assertion,
                outcome=RECOVERED_OUTCOME,
                paypal_event_id=paypal_event_id,
                paypal_event="INVOICING.INVOICE.PAID",
                amount_recovered=amount_recovered,
                amount_expected=amount_expected,
                amount_mismatch=amount_mismatch,
                verified_at=verified_at,
            )

            try:
                res = await db.verifications.insert_one(record.model_dump(exclude_none=True))
                vid = str(res.inserted_id)
            except DuplicateKeyError:
                existing = await db.verifications.find_one({"paypal_event_id": paypal_event_id})
                return PayPalReconcileResult(
                    processed=True,
                    matched=True,
                    duplicate=True,
                    detail=f"PayPal event {paypal_event_id} already recorded (concurrent race)",
                    paypal_event_id=paypal_event_id,
                    event_type=event_type,
                    verification_id=str(existing["_id"]) if existing else None,
                    outcome=record.outcome,
                    amount_recovered=amount_recovered,
                    assertion=assertion,
                    event_status=await _get_event_status(execution["event_id"], db),
                )

            await _transition_event_status(execution["event_id"], "recovered", db)
            logger.info(
                "Reconciled fully paid invoice %s for event %s (assertion=%s, amount=%.2f)",
                paypal_event_id,
                execution["event_id"],
                assertion,
                amount_recovered,
            )
            return PayPalReconcileResult(
                processed=True,
                matched=True,
                duplicate=False,
                detail=f"Reconciled fully paid invoice ({assertion})",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
                verification_id=vid,
                outcome=RECOVERED_OUTCOME,
                amount_recovered=amount_recovered,
                assertion=assertion,
                event_status="recovered",
            )

        elif inv_status == "PARTIALLY_PAID":
            # Record verified partial amount with amount_mismatch true and leave event open
            record = WebhookVerification(
                event_id=execution["event_id"],
                execution_id=str(execution["_id"]),
                gateway="paypal",
                gateway_ref=invoice_id,
                assertion=assertion,
                outcome=RECOVERED_OUTCOME,
                paypal_event_id=paypal_event_id,
                paypal_event="INVOICING.INVOICE.PAID",
                amount_recovered=amount_recovered,
                amount_expected=amount_expected,
                amount_mismatch=True,
                verified_at=verified_at,
            )

            try:
                res = await db.verifications.insert_one(record.model_dump(exclude_none=True))
                vid = str(res.inserted_id)
            except DuplicateKeyError:
                existing = await db.verifications.find_one({"paypal_event_id": paypal_event_id})
                return PayPalReconcileResult(
                    processed=True,
                    matched=True,
                    duplicate=True,
                    detail=f"PayPal event {paypal_event_id} already recorded (concurrent race)",
                    paypal_event_id=paypal_event_id,
                    event_type=event_type,
                    verification_id=str(existing["_id"]) if existing else None,
                    outcome=record.outcome,
                    amount_recovered=amount_recovered,
                    assertion=assertion,
                    event_status=await _get_event_status(execution["event_id"], db),
                )

            # Leave the event open — do NOT transition to recovered
            # Step 5: A recovered event never regresses to open. A partial event arriving after the full one is stored but changes nothing.
            current_st = await _get_event_status(execution["event_id"], db)
            logger.info(
                "Recorded verified partial payment %s for event %s (amount=%.2f, event remains %s)",
                paypal_event_id,
                execution["event_id"],
                amount_recovered,
                current_st,
            )
            detail_msg = (
                "Recorded verified partial amount with amount_mismatch=True; event already recovered"
                if current_st == "recovered"
                else "Recorded verified partial amount with amount_mismatch=True; event left open"
            )
            return PayPalReconcileResult(
                processed=True,
                matched=True,
                duplicate=False,
                detail=detail_msg,
                paypal_event_id=paypal_event_id,
                event_type=event_type,
                verification_id=vid,
                outcome=RECOVERED_OUTCOME,
                amount_recovered=amount_recovered,
                assertion=assertion,
                event_status=current_st,
            )

        else:
            return PayPalReconcileResult(
                processed=False,
                matched=True,
                detail=f"Invoice status {inv_status} with due {due_val} did not trigger settlement",
                paypal_event_id=paypal_event_id,
                event_type=event_type,
            )

    # 4. Any other PayPal event type
    logger.info("No verification record required for event_type %s", event_type)
    return PayPalReconcileResult(
        processed=False,
        matched=False,
        detail=f"No verification record required for {event_type}",
        paypal_event_id=paypal_event_id,
        event_type=event_type,
    )


async def reprocess_unmatched_declines(db: Any = None) -> list[PayPalReconcileResult]:
    """Reprocess any previously unmatched decline events from paypal_events.

    Retries reconciliation when charge_attempts may have been recorded
    after the webhook arrived.
    """
    if db is None:
        db = get_database()
    cursor = db.paypal_events.find({
        "event_type": "PAYMENT.CAPTURE.DECLINED",
        "unmatched": True,
    })
    results: list[PayPalReconcileResult] = []
    async for doc in cursor:
        res = await reconcile_paypal_event(doc.get("payload", {}), db=db)
        results.append(res)
    return results

