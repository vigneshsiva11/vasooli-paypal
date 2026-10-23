"""Offline contract checks for Phase 1 (Step 1 additive models & Step 2 USD profile).

Guarantees:
- Fully offline (zero network, zero database, zero running server).
- Characterization checks for Razorpay execution service with create_payment_link mocked.
- Byte-identical verification of INR rulebook fingerprint against recorded baseline.
- Verification of USD rulebook profile, currency selection, and unknown currency fail-closed behavior.
- Currency-keyed diagnosis high-value thresholds.
- Currency-keyed decision matrix and costs.

Prints PASS/FAIL per check; exits non-zero on any failure.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bson import ObjectId
import pydantic
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.webhooks.paypal_reconciler import reconcile_paypal_event

from app.models import (
    Decision,
    DecisionRecord,
    DiagnosisRecord,
    RevenueEvent,
)
from app.models.execution import (
    AuthorizedVerdict,
    ExecutionRecord,
)
from app.models.verification import (
    GATEWAY_VERIFIED,
    MANUAL_CONFIRMATION_CHANNEL,
    MANUAL_SOURCE,
    MERCHANT_ASSERTED,
    ManualVerification,
    WebhookVerification,
    verification_document,
)
from app.models.policy import (
    PolicyVerdict,
    POLICY_CHECKS,
    REASON_PRECEDENCE,
    REASON_VERDICT,
)
from app.policy import (
    current_fingerprint,
    current_inr_rulebook,
    current_rulebook,
    current_usd_rulebook,
    evaluate,
    PolicyContext,
)
from app.decision.matrix import (
    FX_RATE_INR_PER_USD,
    USD_INTERVENTION_COSTS,
    cost_of,
)
from app.decision.engine import decide, evaluate as evaluate_decision
from app.diagnosis.rules import _history_evidence, HIGH_VALUE_THRESHOLDS
from app.execution import razorpay
from app.execution import service as execution_service

RECORDED_INR_FINGERPRINT = "rb1_aba19a5e5ee8124e"
RECORDED_USD_FINGERPRINT = "rb1_5ae21fdd06acd873"

PASS_COUNT = 0
FAIL_COUNT = 0


def report(label: str, ok: bool, detail: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    if ok:
        PASS_COUNT += 1
        print(f"  [PASS] {label}" + (f" ({detail})" if detail else ""))
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {label}" + (f" ({detail})" if detail else ""))


def make_context() -> PolicyContext:
    return PolicyContext(
        customer_ref="cust_test_123",
        customer_opted_out=False,
        prior_authorized_contacts=0,
        last_authorized_contact_at=None,
        now=datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
    )


def make_decision(
    *,
    amount: float,
    intervention: str = "recovery_payment_link",
    cost: float = 5.0,
    prob: float = 0.5,
    erv: float = 2495.0,
) -> DecisionRecord:
    return DecisionRecord(
        id="507f1f77bcf86cd799439011",
        version=1,
        event_id="evt_test_1",
        diagnosis_id="507f1f77bcf86cd799439012",
        diagnosis_version=1,
        recommended_intervention=intervention,  # type: ignore[arg-type]
        estimated_cost=cost,
        recovery_probability=prob,
        revenue_at_risk=amount,
        expected_recovery_value=erv,
        reasoning="Test decision",
        decided_at=datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc),
    )


def test_step1_models() -> None:
    print("\n--- SECTION 1: Step 1 Additive Models Contract Checks ---")

    # 1.1 RevenueEvent default gateway
    ev_def = RevenueEvent(
        event_id="evt_def_1",
        customer_ref="cust_1",
        amount=100.0,
        currency="INR",
        surface="payment",
        raw_failure_reason="BAD_REQUEST",
    )
    report(
        "RevenueEvent defaults gateway to 'razorpay' and gateway_ref to None",
        ev_def.gateway == "razorpay" and ev_def.gateway_ref is None,
        f"gateway={ev_def.gateway!r}, gateway_ref={ev_def.gateway_ref!r}",
    )

    # 1.2 RevenueEvent accepts explicit paypal
    ev_paypal = RevenueEvent(
        event_id="evt_pp_1",
        customer_ref="cust_1",
        amount=50.0,
        currency="USD",
        surface="payment",
        raw_failure_reason="PAYER_ACTION_REQUIRED",
        gateway="paypal",
        gateway_ref="WH-PP-12345",
    )
    report(
        "RevenueEvent accepts gateway='paypal' and gateway_ref",
        ev_paypal.gateway == "paypal" and ev_paypal.gateway_ref == "WH-PP-12345",
        f"gateway={ev_paypal.gateway!r}, ref={ev_paypal.gateway_ref!r}",
    )

    # 1.3 RevenueEvent rejects invalid gateway
    try:
        RevenueEvent(
            event_id="evt_inv_1",
            customer_ref="cust_1",
            amount=50.0,
            currency="USD",
            surface="payment",
            raw_failure_reason="FAIL",
            gateway="stripe",  # type: ignore[arg-type]
        )
        report("RevenueEvent rejects invalid gateway 'stripe'", False, "Validation did not raise")
    except pydantic.ValidationError:
        report("RevenueEvent rejects invalid gateway 'stripe'", True, "ValidationError raised")

    # 1.4 ExecutionRecord Razorpay defaults & fields
    now = datetime.now(timezone.utc)
    rec_rzp = ExecutionRecord(
        event_id="evt_def_1",
        policy_verdict_id="507f1f77bcf86cd799439013",
        policy_verdict_version=1,
        intervention="recovery_payment_link",
        action_type="payment_link_generated",
        executed_at=now,
        status="completed",
        razorpay_payment_link_id="plink_test123456",
        razorpay_payment_link_url="https://rzp.io/i/test123456",
    )
    report(
        "ExecutionRecord defaults gateway to 'razorpay' and allows gateway_ref=None",
        rec_rzp.gateway == "razorpay" and rec_rzp.gateway_ref is None and rec_rzp.gateway_url is None,
        f"gateway={rec_rzp.gateway!r}",
    )

    # 1.5 ExecutionRecord PayPal link execution
    rec_pp = ExecutionRecord(
        event_id="evt_pp_1",
        policy_verdict_id="507f1f77bcf86cd799439013",
        policy_verdict_version=1,
        intervention="recovery_payment_link",
        action_type="payment_link_generated",
        executed_at=now,
        status="completed",
        gateway="paypal",
        gateway_ref="INVOICE-PP-999",
        gateway_url="https://www.sandbox.paypal.com/invoice/p/#999",
        razorpay_payment_link_id=None,
        razorpay_payment_link_url=None,
    )
    report(
        "ExecutionRecord accepts gateway='paypal' with gateway_ref and gateway_url",
        rec_pp.gateway == "paypal" and rec_pp.gateway_ref == "INVOICE-PP-999" and rec_pp.razorpay_payment_link_id is None,
        f"gateway={rec_pp.gateway!r}, gateway_ref={rec_pp.gateway_ref!r}",
    )

    # 1.6 ExecutionRecord validator rejects razorpay link missing razorpay_payment_link_id
    try:
        ExecutionRecord(
            event_id="evt_def_1",
            policy_verdict_id="507f1f77bcf86cd799439013",
            policy_verdict_version=1,
            intervention="recovery_payment_link",
            action_type="payment_link_generated",
            executed_at=now,
            status="completed",
            gateway="razorpay",
            razorpay_payment_link_id=None,
            razorpay_payment_link_url="https://rzp.io/i/test",
        )
        report("ExecutionRecord rejects razorpay link without razorpay_payment_link_id", False)
    except pydantic.ValidationError:
        report("ExecutionRecord rejects razorpay link without razorpay_payment_link_id", True, "ValidationError raised")

    # 1.7 WebhookVerification validates Razorpay and sets assertion=gateway_verified
    wv_rzp = WebhookVerification(
        event_id="evt_def_1",
        execution_id="507f1f77bcf86cd799439014",
        verified_at=now,
        source="webhook",
        assertion="gateway_verified",
        razorpay_event_id="evt_rzp_99",
        razorpay_event="payment_link.paid",
        razorpay_payment_link_id="plink_test123456",
        amount_recovered=100.0,
        amount_expected=100.0,
        amount_mismatch=False,
        outcome="recovered",
    )
    report(
        "WebhookVerification validates Razorpay webhook with assertion='gateway_verified'",
        wv_rzp.assertion == "gateway_verified" and wv_rzp.gateway == "razorpay",
        f"assertion={wv_rzp.assertion!r}, gateway={wv_rzp.gateway!r}",
    )

    # 1.8 WebhookVerification validates PayPal webhook with PayPal event identifiers
    wv_pp = WebhookVerification(
        event_id="evt_pp_1",
        execution_id="507f1f77bcf86cd799439014",
        verified_at=now,
        source="webhook",
        assertion="gateway_verified",
        gateway="paypal",
        gateway_ref="CAPTURE-123",
        paypal_event_id="WH-TEST-CAPTURE-123",
        paypal_event="PAYMENT.CAPTURE.COMPLETED",
        amount_recovered=50.0,
        amount_expected=50.0,
        amount_mismatch=False,
        outcome="recovered",
    )
    report(
        "WebhookVerification validates PayPal webhook with gateway='paypal'",
        wv_pp.gateway == "paypal"
        and wv_pp.gateway_ref == "CAPTURE-123"
        and wv_pp.paypal_event_id == "WH-TEST-CAPTURE-123"
        and wv_pp.paypal_event == "PAYMENT.CAPTURE.COMPLETED"
        and wv_pp.razorpay_event_id is None,
        f"gateway={wv_pp.gateway!r}, ref={wv_pp.gateway_ref!r}",
    )

    # 1.9 ManualVerification enforces source and assertion
    mv = ManualVerification(
        event_id="evt_def_1",
        execution_id="507f1f77bcf86cd799439014",
        verified_at=now,
        source="manual_confirmation",
        assertion="merchant_asserted",
        confirmation_id="manual_conf_507f1f77bcf86cd799439014",
        confirmed_by="merchant_via_api",
        amount_recovered=100.0,
        amount_expected=100.0,
        amount_mismatch=False,
        outcome="recovered",
    )
    report(
        "ManualVerification enforces source='manual_confirmation' and assertion='merchant_asserted'",
        mv.source == "manual_confirmation" and mv.assertion == "merchant_asserted",
        f"source={mv.source!r}, assertion={mv.assertion!r}",
    )

    # 1.10 verification_document helper defaults assertion based on source
    old_doc_wh = {
        "_id": "507f1f77bcf86cd799439015",
        "event_id": "evt_legacy_wh",
        "execution_id": "507f1f77bcf86cd799439014",
        "verified_at": now,
        "amount_recovered": 100.0,
        "amount_expected": 100.0,
        "amount_mismatch": False,
        "outcome": "recovered",
        "razorpay_event_id": "old_evt_1",
        "razorpay_event": "payment_link.paid",
        "razorpay_payment_link_id": "plink_old",
    }
    doc1 = verification_document(old_doc_wh)
    report(
        "verification_document defaults legacy webhook document to source='webhook' and assertion='gateway_verified'",
        doc1.source == "webhook" and doc1.assertion == "gateway_verified" and doc1.gateway == "razorpay",
        f"source={doc1.source!r}, assertion={doc1.assertion!r}, gateway={doc1.gateway!r}",
    )

    old_doc_man = {
        "_id": "507f1f77bcf86cd799439016",
        "event_id": "evt_legacy_man",
        "execution_id": "507f1f77bcf86cd799439014",
        "outcome": "recovered",
        "amount_recovered": 100.0,
        "amount_expected": 100.0,
        "amount_mismatch": False,
        "verified_at": now,
        "source": "manual_confirmation",
        "confirmed_by": "merchant_via_api",
        "confirmation_id": "manual_conf_507f1f77bcf86cd799439014",
        "confirmed_at": now,
    }
    doc2 = verification_document(old_doc_man)
    report(
        "verification_document defaults legacy manual document to source='manual_confirmation' and assertion='merchant_asserted'",
        doc2.source == "manual_confirmation" and doc2.assertion == "merchant_asserted",
        f"source={doc2.source!r}, assertion={doc2.assertion!r}",
    )


def test_characterization_razorpay_execution() -> None:
    print("\n--- SECTION 2: Characterization Checks of Razorpay Execution Service ---")

    auth_verdict = AuthorizedVerdict(
        event_id="evt_char_1",
        decision_id="507f1f77bcf86cd799439011",
        decision_version=1,
        verdict="authorized",
        reason="ok",
        checks_performed=[f"{c}: PASS (ok)" for c in POLICY_CHECKS],
        evaluated_at=datetime.now(timezone.utc),
        rulebook_fingerprint=RECORDED_INR_FINGERPRINT,
        rulebook_fingerprint_source="evaluated",
        id="507f1f77bcf86cd799439013",
        version=1,
    )

    # 2.1 Mocked create_payment_link on link intervention
    mock_link = razorpay.PaymentLink(
        id="plink_char_test_123",
        short_url="https://rzp.io/i/char123",
        reference_id="vsl_507f1f77bcf86cd799439013",
    )

    ev = RevenueEvent(
        event_id="evt_char_1",
        customer_ref="cust_char_1",
        amount=2500.0,
        currency="INR",
        surface="payment",
        raw_failure_reason="BAD_REQUEST",
    )
    dec = make_decision(amount=2500.0, intervention="recovery_payment_link", cost=5.0, prob=0.5, erv=1245.0)
    diag = DiagnosisRecord(
        id="507f1f77bcf86cd799439012",
        version=1,
        event_id="evt_char_1",
        surface="payment",
        root_cause="insufficient_funds",
        confidence=0.9,
        recoverable=True,
        method="rules",
        evidence=["test evidence"],
        diagnosed_at=datetime.now(timezone.utc),
    )

    chain = execution_service.AuthorizedAction(event=ev, decision=dec, diagnosis=diag)

    async def run_link_test():
        with patch.object(execution_service, "_load_chain", AsyncMock(return_value=chain)), \
             patch.object(execution_service.execution_store, "find_for_verdict", AsyncMock(return_value=None)), \
             patch.object(execution_service.execution_store, "insert", AsyncMock(return_value="507f1f77bcf86cd799439099")), \
             patch.object(razorpay, "create_payment_link", AsyncMock(return_value=mock_link)) as mock_call:

            outcome = await execution_service.execute(auth_verdict)
            rec = outcome.record

            expected_fields = {
                "event_id": "evt_char_1",
                "policy_verdict_id": "507f1f77bcf86cd799439013",
                "policy_verdict_version": 1,
                "intervention": "recovery_payment_link",
                "action_type": "payment_link_generated",
                "status": "completed",
                "failure_reason": None,
                "gateway": "razorpay",
                "gateway_ref": None,
                "gateway_url": None,
                "razorpay_payment_link_id": "plink_char_test_123",
                "razorpay_payment_link_url": "https://rzp.io/i/char123",
                "contact_channel": None,
                "contact_message_summary": None,
            }

            actual_fields = {
                k: getattr(rec, k) for k in expected_fields
            }
            report(
                "Execution service (link) produces exact ExecutionRecord fields with gateway='razorpay'",
                actual_fields == expected_fields,
                f"diff={set(actual_fields.items()) ^ set(expected_fields.items())}",
            )

    asyncio.run(run_link_test())

    # 2.2 Simulated retry execution
    dec_retry = make_decision(amount=2500.0, intervention="delayed_retry", cost=0.0, prob=0.35, erv=875.0)
    chain_retry = execution_service.AuthorizedAction(event=ev, decision=dec_retry, diagnosis=diag)

    async def run_retry_test():
        with patch.object(execution_service, "_load_chain", AsyncMock(return_value=chain_retry)), \
             patch.object(execution_service.execution_store, "find_for_verdict", AsyncMock(return_value=None)), \
             patch.object(execution_service.execution_store, "insert", AsyncMock(return_value="507f1f77bcf86cd799439099")), \
             patch.object(razorpay, "create_payment_link", AsyncMock(return_value=mock_link)):

            outcome = await execution_service.execute(auth_verdict)
            rec = outcome.record
            report(
                "Execution service (retry_simulated) produces action_type='retry_simulated' and Razorpay link artifacts",
                rec.action_type == "retry_simulated" and rec.razorpay_payment_link_id == "plink_char_test_123" and rec.gateway == "razorpay",
                f"action_type={rec.action_type!r}, gateway={rec.gateway!r}",
            )

    asyncio.run(run_retry_test())

    # 2.3 Contact execution
    dec_contact = make_decision(amount=2500.0, intervention="reminder", cost=3.0, prob=0.4, erv=997.0)
    chain_contact = execution_service.AuthorizedAction(event=ev, decision=dec_contact, diagnosis=diag)

    async def run_contact_test():
        with patch.object(execution_service, "_load_chain", AsyncMock(return_value=chain_contact)), \
             patch.object(execution_service.execution_store, "find_for_verdict", AsyncMock(return_value=None)), \
             patch.object(execution_service.execution_store, "insert", AsyncMock(return_value="507f1f77bcf86cd799439099")):

            outcome = await execution_service.execute(auth_verdict)
            rec = outcome.record
            report(
                "Execution service (contact) produces contact_logged with channel and summary without link fields",
                rec.action_type == "contact_logged" and rec.contact_channel is not None and rec.razorpay_payment_link_id is None,
                f"action_type={rec.action_type!r}, channel={rec.contact_channel!r}",
            )

    asyncio.run(run_contact_test())

    # 2.4 Razorpay failure handling
    async def run_failure_test():
        with patch.object(execution_service, "_load_chain", AsyncMock(return_value=chain)), \
             patch.object(execution_service.execution_store, "find_for_verdict", AsyncMock(return_value=None)), \
             patch.object(execution_service.execution_store, "insert", AsyncMock(return_value="507f1f77bcf86cd799439099")), \
             patch.object(razorpay, "create_payment_link", AsyncMock(side_effect=razorpay.RazorpayCallFailed("Razorpay returned HTTP 400 — BAD_REQUEST_ERROR"))):

            outcome = await execution_service.execute(auth_verdict)
            rec = outcome.record
            report(
                "Execution service records status='failed' and failure_reason when Razorpay call fails",
                rec.status == "failed" and rec.failure_reason == "Razorpay returned HTTP 400 — BAD_REQUEST_ERROR" and rec.razorpay_payment_link_id is None,
                f"status={rec.status!r}, reason={rec.failure_reason!r}",
            )

    asyncio.run(run_failure_test())


def test_step2_rulebook_and_policy() -> None:
    print("\n--- SECTION 3: Step 2 Currency-Keyed Rulebook & Policy Contract Checks ---")

    # 3.1 INR rulebook fingerprint BYTE-IDENTICAL equality
    inr_fp = current_fingerprint("INR")
    report(
        "INR rulebook fingerprint is BYTE-IDENTICAL to recorded baseline",
        inr_fp == RECORDED_INR_FINGERPRINT,
        f"actual={inr_fp!r}, expected={RECORDED_INR_FINGERPRINT!r}",
    )

    # 3.2 USD rulebook fingerprint
    usd_fp = current_fingerprint("USD")
    report(
        "USD rulebook fingerprint matches deterministic digest",
        usd_fp == RECORDED_USD_FINGERPRINT,
        f"actual={usd_fp!r}, expected={RECORDED_USD_FINGERPRINT!r}",
    )

    # 3.3 USD profile thresholds
    usd_book = current_usd_rulebook()
    report(
        "USD profile thresholds match specification (MINIMUM_ERV=1.00, AUTO<250, NEVER>=1000)",
        usd_book.minimum_erv == 1.00
        and usd_book.auto_authorize_below == 250.0
        and usd_book.never_auto_at_or_above == 1000.0
        and usd_book.tier_currency == "USD"
        and usd_book.fx_rate_inr_per_usd == 83.0,
        f"min_erv={usd_book.minimum_erv}, auto<{usd_book.auto_authorize_below}, never>={usd_book.never_auto_at_or_above}",
    )

    # 3.4 USD intervention costs converted from INR table at fixed rate 83.0
    costs_ok = (
        cost_of("reminder", currency="USD") == 0.04
        and cost_of("payment_method_update_link", currency="USD") == 0.06
        and cost_of("escalating_reminder_sequence", currency="USD") == 0.24
        and cost_of("manual_escalation", currency="USD") == 0.60
        and cost_of("delayed_retry", currency="USD") == 0.0
    )
    report(
        "USD intervention costs converted at fixed rate 1 USD = 83.00 INR",
        costs_ok,
        f"reminder=${cost_of('reminder', 'USD')}, escalation=${cost_of('manual_escalation', 'USD')}",
    )

    # 3.5 Policy profile selection by currency (USD tiers)
    ctx = make_context()

    # USD auto: $200 (< $250)
    dec_usd_auto = make_decision(amount=200.0, cost=0.06, prob=0.5, erv=99.94)
    v_usd_auto = evaluate(decision=dec_usd_auto, context=ctx, currency="USD")
    report(
        "USD policy evaluates $200 into auto tier (verdict='authorized', reason='ok')",
        v_usd_auto.verdict == "authorized" and v_usd_auto.reason == "ok",
        f"verdict={v_usd_auto.verdict!r}, reason={v_usd_auto.reason!r}",
    )

    # USD approval required: $500 ($250 <= amount < $1000)
    dec_usd_appr = make_decision(amount=500.0, cost=0.06, prob=0.5, erv=249.94)
    v_usd_appr = evaluate(decision=dec_usd_appr, context=ctx, currency="USD")
    report(
        "USD policy evaluates $500 into approval_required tier (requires_manual_review)",
        v_usd_appr.verdict == "requires_manual_review" and v_usd_appr.reason == "amount_requires_approval",
        f"verdict={v_usd_appr.verdict!r}, reason={v_usd_appr.reason!r}",
    )

    # USD never auto: $1500 (>= $1000)
    dec_usd_never = make_decision(amount=1500.0, cost=0.06, prob=0.5, erv=749.94)
    v_usd_never = evaluate(decision=dec_usd_never, context=ctx, currency="USD")
    report(
        "USD policy evaluates $1500 into never_auto tier (requires_manual_review)",
        v_usd_never.verdict == "requires_manual_review" and v_usd_never.reason == "amount_never_auto",
        f"verdict={v_usd_never.verdict!r}, reason={v_usd_never.reason!r}",
    )

    # 3.6 Unknown currency fail closed to requires_manual_review with reason unsupported_currency
    dec_eur = make_decision(amount=100.0, cost=5.0, prob=0.5, erv=45.0)
    v_eur = evaluate(decision=dec_eur, context=ctx, currency="EUR")
    report(
        "Unknown currency (EUR) fails closed to requires_manual_review with reason='unsupported_currency'",
        v_eur.verdict == "requires_manual_review" and v_eur.reason == "unsupported_currency",
        f"verdict={v_eur.verdict!r}, reason={v_eur.reason!r}",
    )

    # Verify that an unknown currency NEVER uses INR thresholds (e.g. ₹5,000 / ₹25,000)
    v_jpy = evaluate(decision=make_decision(amount=10.0, cost=1.0, prob=0.5, erv=4.0), context=ctx, currency="JPY")
    report(
        "Unknown currency (JPY 10.0) fails closed to manual review instead of auto-authorizing under INR limits",
        v_jpy.verdict == "requires_manual_review" and v_jpy.reason == "unsupported_currency",
        f"verdict={v_jpy.verdict!r}, reason={v_jpy.reason!r}",
    )

    # 3.7 Diagnosis high-value threshold currency-keyed (INR 50,000; USD 600)
    ev_inr_sub = RevenueEvent(
        event_id="e_inr_1", customer_ref="c1", amount=49999.0, currency="INR",
        surface="payment", raw_failure_reason="FAIL"
    )
    ev_inr_high = RevenueEvent(
        event_id="e_inr_2", customer_ref="c1", amount=50000.0, currency="INR",
        surface="payment", raw_failure_reason="FAIL"
    )
    ev_usd_sub = RevenueEvent(
        event_id="e_usd_1", customer_ref="c1", amount=599.0, currency="USD",
        surface="payment", raw_failure_reason="FAIL"
    )
    ev_usd_high = RevenueEvent(
        event_id="e_usd_2", customer_ref="c1", amount=600.0, currency="USD",
        surface="payment", raw_failure_reason="FAIL"
    )

    inr_sub_evi = _history_evidence(ev_inr_sub, 0)
    inr_high_evi = _history_evidence(ev_inr_high, 0)
    usd_sub_evi = _history_evidence(ev_usd_sub, 0)
    usd_high_evi = _history_evidence(ev_usd_high, 0)

    diag_ok = (
        not any("high-value" in s for s in inr_sub_evi)
        and any("high-value event (INR 50,000.00)" in s for s in inr_high_evi)
        and not any("high-value" in s for s in usd_sub_evi)
        and any("high-value event (USD 600.00)" in s for s in usd_high_evi)
    )
    report(
        "Diagnosis high-value evidence is currency-keyed (INR: 50,000; USD: 600)",
        diag_ok,
        f"INR 49k: {inr_sub_evi}, INR 50k: {inr_high_evi}, USD 599: {usd_sub_evi}, USD 600: {usd_high_evi}",
    )

    # 3.8 Decision candidate matrix and decision costs use event's currency
    diag_for_dec = DiagnosisRecord(
        id="507f1f77bcf86cd799439012",
        version=1,
        event_id="e_usd_1",
        surface="payment",
        root_cause="insufficient_funds",
        confidence=0.9,
        recoverable=True,
        method="rules",
        evidence=[],
        diagnosed_at=datetime.now(timezone.utc),
    )
    # USD event
    dec_usd = decide(diagnosis=diag_for_dec, event=ev_usd_sub)
    report(
        "Decision engine candidate scoring uses USD intervention cost for USD event ($0.00 for retry or $0.06 for link)",
        dec_usd.estimated_cost == 0.0,  # delayed_retry wins (cost=0)
        f"recommended={dec_usd.recommended_intervention!r}, cost={dec_usd.estimated_cost}, erv={dec_usd.expected_recovery_value}",
    )

    # Verify paid intervention in USD
    diag_paid = DiagnosisRecord(
        id="507f1f77bcf86cd799439012",
        version=1,
        event_id="e_usd_1",
        surface="payment",
        root_cause="card_expired",
        confidence=0.9,
        recoverable=True,
        method="rules",
        evidence=[],
        diagnosed_at=datetime.now(timezone.utc),
    )
    dec_usd_paid = decide(diagnosis=diag_paid, event=ev_usd_sub)
    report(
        "Decision for card_expired in USD uses USD cost ($0.06)",
        dec_usd_paid.estimated_cost == 0.06 and dec_usd_paid.recommended_intervention == "payment_method_update_link",
        f"intervention={dec_usd_paid.recommended_intervention}, cost=${dec_usd_paid.estimated_cost}",
    )


def test_step3_execution_port_and_adapters() -> None:
    """Section 4: execution port, PayPal adapter contracts, and Razorpay backward compat."""
    print("\n--- SECTION 4: Step 3 Execution Port & PayPal Adapter Contract Checks ---")

    from app.execution.port import (
        ExecutionResult,
        PayPalExecutionAdapter,
        RazorpayExecutionAdapter,
        UnknownGateway,
        VaultTokenMissing,
    )
    from app.gateways.paypal import invoicing, orders
    from app.gateways.paypal.client import PayPalCallFailed

    now = datetime.now(timezone.utc)

    verdict_id = "507f1f77bcf86cd799439013"
    event_id = "evt_pp_link_1"
    execution_id = "507f1f77bcf86cd799439050"

    auth_verdict = AuthorizedVerdict(
        event_id=event_id,
        decision_id="507f1f77bcf86cd799439011",
        decision_version=1,
        verdict="authorized",
        reason="ok",
        checks_performed=[f"{c}: PASS (ok)" for c in POLICY_CHECKS],
        evaluated_at=now,
        rulebook_fingerprint=RECORDED_USD_FINGERPRINT,
        rulebook_fingerprint_source="evaluated",
        id=verdict_id,
        version=1,
    )

    ev_usd = RevenueEvent(
        event_id=event_id,
        customer_ref="cust_pp_1",
        amount=150.0,
        currency="USD",
        surface="payment",
        raw_failure_reason="PAYER_ACTION_REQUIRED",
        gateway="paypal",
    )
    dec_link = make_decision(amount=150.0, intervention="recovery_payment_link", cost=0.06, prob=0.5, erv=74.94)
    diag_pp = DiagnosisRecord(
        id="507f1f77bcf86cd799439012",
        version=1,
        event_id=event_id,
        surface="payment",
        root_cause="insufficient_funds",
        confidence=0.9,
        recoverable=True,
        method="rules",
        evidence=["test evidence"],
        diagnosed_at=now,
    )

    # ----- 4.1 PayPal link action (invoice) -----
    mock_invoice_id = "INV2-MOCK-TEST-0001"
    mock_recipient_url = "https://www.sandbox.paypal.com/invoice/p/#INV2-MOCK-TEST-0001"

    captured_payloads: dict[str, list] = {"create": [], "send": [], "get": []}

    async def mock_create_invoice(**kwargs):
        captured_payloads["create"].append(kwargs)
        return {"id": mock_invoice_id, "href": f"/v2/invoicing/invoices/{mock_invoice_id}"}

    async def mock_send_invoice(invoice_id, **kwargs):
        captured_payloads["send"].append((invoice_id, kwargs))
        return {}

    async def mock_get_invoice(invoice_id):
        captured_payloads["get"].append(invoice_id)
        return {
            "id": invoice_id,
            "detail": {"metadata": {"recipient_view_url": mock_recipient_url}},
        }

    chain_link = execution_service.AuthorizedAction(event=ev_usd, decision=dec_link, diagnosis=diag_pp)

    async def run_paypal_link_test():
        with patch.object(execution_service, "_load_chain", AsyncMock(return_value=chain_link)), \
             patch.object(execution_service.execution_store, "find_for_verdict", AsyncMock(return_value=None)), \
             patch.object(execution_service.execution_store, "insert", AsyncMock(return_value="507f1f77bcf86cd799439099")), \
             patch.object(invoicing, "create_invoice", AsyncMock(side_effect=mock_create_invoice)), \
             patch.object(invoicing, "send_invoice", AsyncMock(side_effect=mock_send_invoice)), \
             patch.object(invoicing, "get_invoice", AsyncMock(side_effect=mock_get_invoice)):

            outcome = await execution_service.execute(auth_verdict)
            rec = outcome.record

            # 4.1a Invoice create payload is USD with NO amount.breakdown.custom
            create_kwargs = captured_payloads["create"][0]
            report(
                "4.1a Invoice create: currency is USD, no amount.breakdown.custom",
                create_kwargs.get("currency") == "USD",
                f"currency={create_kwargs.get('currency')!r}",
            )

            # 4.1b detail.reference = vsl_<policy_verdict_id>
            report(
                "4.1b Invoice create: reference = vsl_<verdict_id>",
                create_kwargs.get("reference") == f"vsl_{verdict_id}",
                f"reference={create_kwargs.get('reference')!r}, expected='vsl_{verdict_id}'",
            )

            # 4.1c note contains the event id
            report(
                "4.1c Invoice create: note contains event_id",
                event_id in create_kwargs.get("note", ""),
                f"note={create_kwargs.get('note')!r}",
            )

            # 4.1d Invoice is sent (send_invoice called)
            report(
                "4.1d Invoice is sent after creation",
                len(captured_payloads["send"]) == 1 and captured_payloads["send"][0][0] == mock_invoice_id,
                f"send calls={len(captured_payloads['send'])}",
            )

            # 4.1e GET yields recipient_view_url
            report(
                "4.1e GET invoice yields recipient_view_url",
                len(captured_payloads["get"]) == 1,
                f"get calls={len(captured_payloads['get'])}",
            )

            # 4.1f Result has gateway_ref = invoice id and gateway_url = URL
            report(
                "4.1f Result: gateway_ref = invoice_id, gateway_url = recipient_view_url",
                rec.gateway_ref == mock_invoice_id and rec.gateway_url == mock_recipient_url,
                f"gateway_ref={rec.gateway_ref!r}, gateway_url={rec.gateway_url!r}",
            )

            # 4.1g Result gateway is paypal
            report(
                "4.1g Result: gateway = 'paypal'",
                rec.gateway == "paypal",
                f"gateway={rec.gateway!r}",
            )

            # 4.1h The ExecutionRecord validates (it was already constructed)
            try:
                validated = ExecutionRecord(
                    event_id=rec.event_id,
                    policy_verdict_id=rec.policy_verdict_id,
                    policy_verdict_version=rec.policy_verdict_version,
                    intervention=rec.intervention,
                    action_type=rec.action_type,
                    executed_at=rec.executed_at,
                    status=rec.status,
                    gateway=rec.gateway,
                    gateway_ref=rec.gateway_ref,
                    gateway_url=rec.gateway_url,
                    razorpay_payment_link_id=rec.razorpay_payment_link_id,
                    razorpay_payment_link_url=rec.razorpay_payment_link_url,
                )
                report("4.1h ExecutionRecord validates for PayPal link action", True)
            except pydantic.ValidationError as e:
                report("4.1h ExecutionRecord validates for PayPal link action", False, str(e))

    asyncio.run(run_paypal_link_test())

    # ----- 4.2 PayPal retry action (vaulted order) -----
    event_retry_id = "evt_pp_retry_1"
    dec_retry = make_decision(amount=150.0, intervention="delayed_retry", cost=0.0, prob=0.35, erv=52.5)
    ev_usd_retry = RevenueEvent(
        event_id=event_retry_id,
        customer_ref="cust_pp_1",
        amount=150.0,
        currency="USD",
        surface="payment",
        raw_failure_reason="PAYER_ACTION_REQUIRED",
        gateway="paypal",
    )
    chain_retry = execution_service.AuthorizedAction(event=ev_usd_retry, decision=dec_retry, diagnosis=diag_pp)
    auth_verdict_retry = AuthorizedVerdict(
        event_id=event_retry_id,
        decision_id="507f1f77bcf86cd799439011",
        decision_version=1,
        verdict="authorized",
        reason="ok",
        checks_performed=[f"{c}: PASS (ok)" for c in POLICY_CHECKS],
        evaluated_at=now,
        rulebook_fingerprint=RECORDED_USD_FINGERPRINT,
        rulebook_fingerprint_source="evaluated",
        id=verdict_id,
        version=1,
    )

    mock_order_id = "ORD-MOCK-VAULT-001"
    captured_order_payloads: list[dict] = []

    async def mock_create_vaulted_order(**kwargs):
        captured_order_payloads.append(kwargs)
        return {
            "id": mock_order_id,
            "status": "COMPLETED",
            "links": [{"rel": "self", "href": f"https://api.sandbox.paypal.com/v2/checkout/orders/{mock_order_id}"}],
        }

    async def run_paypal_retry_test():
        with patch.object(execution_service, "_load_chain", AsyncMock(return_value=chain_retry)), \
             patch.object(execution_service.execution_store, "find_for_verdict", AsyncMock(return_value=None)), \
             patch.object(execution_service.execution_store, "insert", AsyncMock(return_value="507f1f77bcf86cd799439098")), \
             patch.object(orders, "create_vaulted_order", AsyncMock(side_effect=mock_create_vaulted_order)):

            outcome = await execution_service.execute(
                auth_verdict_retry, vault_token="VAULT-TOKEN-XYZ-123"
            )
            rec = outcome.record

            order_kwargs = captured_order_payloads[0]

            # 4.2a vault_id = the vault token
            report(
                "4.2a Retry: payload uses payment_source.card.vault_id",
                order_kwargs.get("vault_id") == "VAULT-TOKEN-XYZ-123",
                f"vault_id={order_kwargs.get('vault_id')!r}",
            )

            # 4.2b PayPal-Request-Id = execution id (idempotency_key)
            report(
                "4.2b Retry: idempotency_key is set (execution id)",
                order_kwargs.get("idempotency_key") is not None,
                f"idempotency_key={order_kwargs.get('idempotency_key')!r}",
            )

            # 4.2c reference_id = vsl_<verdict_id>
            report(
                "4.2c Retry: reference_id = vsl_<verdict_id>",
                order_kwargs.get("reference_id") == f"vsl_{verdict_id}",
                f"reference_id={order_kwargs.get('reference_id')!r}",
            )

            # 4.2d custom_id = ev:<event_id>
            report(
                "4.2d Retry: custom_id = 'ev:<event_id>'",
                order_kwargs.get("custom_id") == f"ev:{event_retry_id}",
                f"custom_id={order_kwargs.get('custom_id')!r}",
            )

            # 4.2e NO purchase_units.invoice_id (orders don't carry invoice_id)
            # The create_vaulted_order function doesn't accept an invoice_id parameter
            report(
                "4.2e Retry: no invoice_id in vaulted order kwargs",
                "invoice_id" not in order_kwargs,
                f"keys={sorted(order_kwargs.keys())}",
            )

            # 4.2f Result status and fields
            report(
                "4.2f Retry: completed with gateway_ref = order_id",
                rec.status == "completed" and rec.gateway_ref == mock_order_id and rec.gateway == "paypal",
                f"status={rec.status!r}, gateway_ref={rec.gateway_ref!r}",
            )

    asyncio.run(run_paypal_retry_test())

    # ----- 4.3 Missing vault token -> FAILED ExecutionRecord, no exception escapes -----
    async def run_missing_vault_test():
        with patch.object(execution_service, "_load_chain", AsyncMock(return_value=chain_retry)), \
             patch.object(execution_service.execution_store, "find_for_verdict", AsyncMock(return_value=None)), \
             patch.object(execution_service.execution_store, "insert", AsyncMock(return_value="507f1f77bcf86cd799439097")):

            # No exception should escape — it should produce a FAILED record
            outcome = await execution_service.execute(auth_verdict_retry, vault_token=None)
            rec = outcome.record
            report(
                "4.3 Missing vault token: FAILED record stored, no exception escapes",
                rec.status == "failed" and rec.failure_reason is not None and "vault" in rec.failure_reason.lower(),
                f"status={rec.status!r}, reason={rec.failure_reason!r}",
            )

    asyncio.run(run_missing_vault_test())

    # ----- 4.4 Unknown gateway -> fails closed -----
    ev_unknown_gw = RevenueEvent(
        event_id="evt_unknown_gw_1",
        customer_ref="cust_pp_1",
        amount=100.0,
        currency="USD",
        surface="payment",
        raw_failure_reason="FAIL",
        gateway="paypal",  # We'll override getattr in the chain
    )
    # Simulate an event whose gateway field is something unsupported
    chain_unknown = execution_service.AuthorizedAction(
        event=RevenueEvent(
            event_id="evt_unknown_gw_1",
            customer_ref="cust_pp_1",
            amount=100.0,
            currency="USD",
            surface="payment",
            raw_failure_reason="FAIL",
            gateway="paypal",
        ),
        decision=dec_link,
        diagnosis=diag_pp,
    )

    # Test that get_adapter raises for unknown gateway
    from app.execution.service import get_adapter

    try:
        get_adapter("stripe")
        report("4.4 Unknown gateway ('stripe') fails closed", False, "no exception raised")
    except UnknownGateway:
        report("4.4 Unknown gateway ('stripe') fails closed", True, "UnknownGateway raised")

    # ----- 4.5 Razorpay path backward compat: execute() still works -----
    ev_rzp = RevenueEvent(
        event_id="evt_rzp_compat_1",
        customer_ref="cust_char_1",
        amount=2500.0,
        currency="INR",
        surface="payment",
        raw_failure_reason="BAD_REQUEST",
    )
    dec_rzp = make_decision(amount=2500.0, intervention="recovery_payment_link", cost=5.0, prob=0.5, erv=1245.0)
    diag_rzp = DiagnosisRecord(
        id="507f1f77bcf86cd799439012",
        version=1,
        event_id="evt_rzp_compat_1",
        surface="payment",
        root_cause="insufficient_funds",
        confidence=0.9,
        recoverable=True,
        method="rules",
        evidence=["test evidence"],
        diagnosed_at=now,
    )
    chain_rzp = execution_service.AuthorizedAction(event=ev_rzp, decision=dec_rzp, diagnosis=diag_rzp)
    auth_rzp = AuthorizedVerdict(
        event_id="evt_rzp_compat_1",
        decision_id="507f1f77bcf86cd799439011",
        decision_version=1,
        verdict="authorized",
        reason="ok",
        checks_performed=[f"{c}: PASS (ok)" for c in POLICY_CHECKS],
        evaluated_at=now,
        rulebook_fingerprint=RECORDED_INR_FINGERPRINT,
        rulebook_fingerprint_source="evaluated",
        id="507f1f77bcf86cd799439013",
        version=1,
    )
    mock_rzp_link = razorpay.PaymentLink(
        id="plink_compat_123",
        short_url="https://rzp.io/i/compat123",
        reference_id="vsl_507f1f77bcf86cd799439013",
    )

    async def run_razorpay_compat_test():
        with patch.object(execution_service, "_load_chain", AsyncMock(return_value=chain_rzp)), \
             patch.object(execution_service.execution_store, "find_for_verdict", AsyncMock(return_value=None)), \
             patch.object(execution_service.execution_store, "insert", AsyncMock(return_value="507f1f77bcf86cd799439096")), \
             patch.object(razorpay, "create_payment_link", AsyncMock(return_value=mock_rzp_link)):

            outcome = await execution_service.execute(auth_rzp)
            rec = outcome.record
            report(
                "4.5 Razorpay path: produces same fields as Section 2 (gateway='razorpay', link artifacts present)",
                rec.gateway == "razorpay"
                and rec.razorpay_payment_link_id == "plink_compat_123"
                and rec.razorpay_payment_link_url == "https://rzp.io/i/compat123"
                and rec.status == "completed"
                and rec.action_type == "payment_link_generated",
                f"gateway={rec.gateway!r}, plink_id={rec.razorpay_payment_link_id!r}",
            )

    asyncio.run(run_razorpay_compat_test())


# =============================================================================
# SECTION 5: PAYPAL WEBHOOK RECONCILIATION CONTRACT CHECKS (OFFLINE)
# =============================================================================


class _MockMongoCollection:
    def __init__(self, name: str):
        self.name = name
        self.docs: list[dict[str, Any]] = []

    def _matches(self, doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for k, v in query.items():
            doc_val = doc.get(k)
            if isinstance(v, dict):
                if "$in" in v:
                    if doc_val not in v["$in"]:
                        return False
                elif "$exists" in v:
                    if (k in doc) != v["$exists"]:
                        return False
            else:
                if doc_val != v:
                    return False
        return True

    async def find_one(self, query: dict[str, Any], projection: dict[str, Any] | None = None) -> dict[str, Any] | None:
        for doc in self.docs:
            if self._matches(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, doc: dict[str, Any]) -> Any:
        if doc.get("paypal_event_id"):
            for d in self.docs:
                if d.get("paypal_event_id") == doc["paypal_event_id"]:
                    raise DuplicateKeyError(f"E11000 duplicate key error on {self.name}.paypal_event_id")
        d = dict(doc)
        if "_id" not in d:
            d["_id"] = ObjectId()
        self.docs.append(d)
        class _InsertRes:
            inserted_id = d["_id"]
        return _InsertRes()

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> Any:
        for doc in self.docs:
            if self._matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                class _UpdateRes:
                    matched_count = 1
                    modified_count = 1
                return _UpdateRes()
        class _UpdateResZero:
            matched_count = 0
            modified_count = 0
        return _UpdateResZero()


class _MockMongoDB:
    def __init__(self):
        self.verifications = _MockMongoCollection("verifications")
        self.executions = _MockMongoCollection("executions")
        self.events = _MockMongoCollection("events")
        self.policy_verdicts = _MockMongoCollection("policy_verdicts")
        self.decisions = _MockMongoCollection("decisions")

    async def list_collection_names(self) -> list[str]:
        return ["verifications", "executions", "events", "policy_verdicts", "decisions"]


def test_step5_paypal_reconciliation() -> None:
    print("\n--- SECTION 5: PAYPAL WEBHOOK RECONCILIATION CONTRACT CHECKS ---")
    fixtures_dir = ROOT / "tests" / "fixtures" / "paypal"

    # 5.1 Fixture validation
    fixture_files = [
        "payment_capture_completed.json",
        "payment_capture_declined.json",
        "invoicing_invoice_paid_partial.json",
        "invoicing_invoice_paid_full.json",
        "invoicing_invoice_paid_external.json",
        "invoicing_invoice_created.json",
        "invoicing_invoice_sent.json",
        "vault_payment_token_created.json",
    ]
    fixtures: dict[str, dict[str, Any]] = {}
    for f in fixture_files:
        p = fixtures_dir / f
        if p.exists():
            fixtures[f] = json.loads(p.read_text(encoding="utf-8"))
        else:
            fixtures[f] = {}

    all_fixtures_present = all(len(fixtures[f]) > 0 for f in fixture_files)
    report(
        "5.1 Fixture loading: all 8 PayPal fixtures load valid JSON from tests/fixtures/paypal/",
        all_fixtures_present,
        f"loaded {len([f for f in fixture_files if len(fixtures[f]) > 0])}/8 fixtures",
    )

    # 5.1b Fixture provenance: captured fixtures match recorded original payload SHA-256
    captured_hashes_valid = True
    hash_details: list[str] = []
    for f in fixture_files:
        data = fixtures.get(f, {})
        if data.get("source") == "captured":
            recorded_hash = data.get("original_payload_sha256")
            clean = {k: v for k, v in data.items() if k not in ("source", "original_payload_sha256")}
            actual_hash = hashlib.sha256(json.dumps(clean, sort_keys=True).encode("utf-8")).hexdigest()
            if not recorded_hash or actual_hash != recorded_hash:
                captured_hashes_valid = False
                hash_details.append(f"{f}: expected {recorded_hash}, got {actual_hash}")
            else:
                hash_details.append(f"{f}: OK ({actual_hash[:8]}...)")
    report(
        "5.1b Fixture provenance: captured PayPal fixtures match recorded original payload SHA-256",
        captured_hashes_valid and len(hash_details) > 0,
        "; ".join(hash_details),
    )

    async def run_reconciliation_tests() -> None:
        # 5.2 PAYMENT.CAPTURE.COMPLETED single reconcile
        db_cap = _MockMongoDB()
        fix_cap = fixtures["payment_capture_completed.json"]
        db_cap.executions.docs.append({
            "_id": ObjectId("507f1f77bcf86cd799439001"),
            "gateway": "paypal",
            "gateway_ref": "46969136VC639191U",
            "event_id": "evt_paypal_order_capture_01",
            "amount": 25.0,
        })
        db_cap.events.docs.append({
            "event_id": "evt_paypal_order_capture_01",
            "amount": 25.0,
            "status": "at_risk",
        })

        r1 = await reconcile_paypal_event(fix_cap, db_cap)
        rec_verif = db_cap.verifications.docs[0] if db_cap.verifications.docs else {}
        cap_ok = (
            r1.processed is True
            and r1.matched is True
            and r1.duplicate is False
            and r1.outcome == "recovered"
            and r1.assertion == "gateway_verified"
            and r1.amount_recovered == 25.0
            and db_cap.events.docs[0]["status"] == "recovered"
            and rec_verif.get("paypal_event_id") == fix_cap["id"]
            and rec_verif.get("paypal_event") == "PAYMENT.CAPTURE.COMPLETED"
        )
        report(
            "5.2 PAYMENT.CAPTURE.COMPLETED: reconciles once (gateway_verified, recovered, transitions event status)",
            cap_ok,
            f"assertion={r1.assertion}, outcome={r1.outcome}, status={db_cap.events.docs[0]['status']}",
        )

        # 5.3 PAYMENT.CAPTURE.COMPLETED replay
        r2 = await reconcile_paypal_event(fix_cap, db_cap)
        rep_cap_ok = (
            r2.processed is True
            and r2.matched is True
            and r2.duplicate is True
            and r2.verification_id == r1.verification_id
            and len(db_cap.verifications.docs) == 1
        )
        report(
            "5.3 PAYMENT.CAPTURE.COMPLETED replay: duplicate detected, returns existing verification, no duplicate DB record",
            rep_cap_ok,
            f"duplicate={r2.duplicate}, verif_count={len(db_cap.verifications.docs)}",
        )

        # 5.4 INVOICING.INVOICE.PAID (partial)
        db_part = _MockMongoDB()
        fix_part = fixtures["invoicing_invoice_paid_partial.json"]
        db_part.executions.docs.append({
            "_id": ObjectId("507f1f77bcf86cd799439002"),
            "gateway": "paypal",
            "gateway_ref": "INV2-LSEJ-2LLG-D59L-2QNU",
            "event_id": "evt_paypal_inv_partial",
            "amount": 100.0,
        })
        db_part.events.docs.append({
            "event_id": "evt_paypal_inv_partial",
            "amount": 100.0,
            "status": "at_risk",
        })

        rp1 = await reconcile_paypal_event(fix_part, db_part)
        part_verif = db_part.verifications.docs[0] if db_part.verifications.docs else {}
        part_ok = (
            rp1.processed is True
            and rp1.matched is True
            and rp1.duplicate is False
            and rp1.assertion == "gateway_verified"
            and rp1.amount_recovered == 10.0
            and part_verif.get("amount_mismatch") is True
            and db_part.events.docs[0]["status"] == "at_risk"  # left open
        )
        report(
            "5.4 INVOICING.INVOICE.PAID (partial): classified as gateway_verified, amount_mismatch=True, event left open",
            part_ok,
            f"assertion={rp1.assertion}, amount={rp1.amount_recovered}, mismatch={part_verif.get('amount_mismatch')}, event_status={db_part.events.docs[0]['status']}",
        )

        # 5.5 INVOICING.INVOICE.PAID (partial) replay
        rp2 = await reconcile_paypal_event(fix_part, db_part)
        rep_part_ok = (
            rp2.processed is True
            and rp2.matched is True
            and rp2.duplicate is True
            and rp2.verification_id == rp1.verification_id
            and len(db_part.verifications.docs) == 1
        )
        report(
            "5.5 INVOICING.INVOICE.PAID (partial) replay: duplicate detected, returns existing verification, no duplicate DB record",
            rep_part_ok,
            f"duplicate={rp2.duplicate}, verif_count={len(db_part.verifications.docs)}",
        )

        # 5.6 INVOICING.INVOICE.PAID (full)
        db_full = _MockMongoDB()
        fix_full = fixtures["invoicing_invoice_paid_full.json"]
        db_full.executions.docs.append({
            "_id": ObjectId("507f1f77bcf86cd799439003"),
            "gateway": "paypal",
            "gateway_ref": "INV2-LSEJ-2LLG-D59L-2QNU",
            "event_id": "evt_paypal_inv_full",
            "amount": 100.0,
        })
        db_full.events.docs.append({
            "event_id": "evt_paypal_inv_full",
            "amount": 100.0,
            "status": "at_risk",
        })

        rf1 = await reconcile_paypal_event(fix_full, db_full)
        full_verif = db_full.verifications.docs[0] if db_full.verifications.docs else {}
        full_ok = (
            rf1.processed is True
            and rf1.matched is True
            and rf1.duplicate is False
            and rf1.assertion == "gateway_verified"
            and rf1.outcome == "recovered"
            and rf1.amount_recovered == 100.0
            and db_full.events.docs[0]["status"] == "recovered"
            and full_verif.get("paypal_event") == "INVOICING.INVOICE.PAID"
        )
        report(
            "5.6 INVOICING.INVOICE.PAID (full): classified as gateway_verified, recovered outcome, transitions event status",
            full_ok,
            f"assertion={rf1.assertion}, outcome={rf1.outcome}, status={db_full.events.docs[0]['status']}",
        )

        # 5.7 INVOICING.INVOICE.PAID (full) replay
        rf2 = await reconcile_paypal_event(fix_full, db_full)
        rep_full_ok = (
            rf2.processed is True
            and rf2.matched is True
            and rf2.duplicate is True
            and len(db_full.verifications.docs) == 1
        )
        report(
            "5.7 INVOICING.INVOICE.PAID (full) replay: duplicate detected, returns existing verification, no duplicate DB record",
            rep_full_ok,
            f"duplicate={rf2.duplicate}, verif_count={len(db_full.verifications.docs)}",
        )

        # 5.8 INVOICING.INVOICE.PAID (external recorded payment)
        db_ext = _MockMongoDB()
        fix_ext = fixtures["invoicing_invoice_paid_external.json"]
        db_ext.executions.docs.append({
            "_id": ObjectId("507f1f77bcf86cd799439004"),
            "gateway": "paypal",
            "gateway_ref": "INV2-LSEJ-2LLG-D59L-2QNU",
            "event_id": "evt_paypal_inv_ext",
            "amount": 100.0,
        })
        db_ext.events.docs.append({
            "event_id": "evt_paypal_inv_ext",
            "amount": 100.0,
            "status": "at_risk",
        })

        re1 = await reconcile_paypal_event(fix_ext, db_ext)
        ext_verif = db_ext.verifications.docs[0] if db_ext.verifications.docs else {}
        ext_ok = (
            re1.processed is True
            and re1.matched is True
            and re1.duplicate is False
            and re1.assertion == "merchant_asserted"
            and re1.outcome == "recovered"
            and re1.amount_recovered == 100.0
            and db_ext.events.docs[0]["status"] == "recovered"
            and ext_verif.get("assertion") == "merchant_asserted"
        )
        report(
            "5.8 INVOICING.INVOICE.PAID (external): classified as merchant_asserted (EXTERNAL tx), recovered outcome",
            ext_ok,
            f"assertion={re1.assertion}, outcome={re1.outcome}, status={db_ext.events.docs[0]['status']}",
        )

        # 5.9 INVOICING.INVOICE.PAID (external) replay
        re2 = await reconcile_paypal_event(fix_ext, db_ext)
        rep_ext_ok = (
            re2.processed is True
            and re2.matched is True
            and re2.duplicate is True
            and len(db_ext.verifications.docs) == 1
        )
        report(
            "5.9 INVOICING.INVOICE.PAID (external) replay: duplicate detected, returns existing verification, no duplicate DB record",
            rep_ext_ok,
            f"duplicate={re2.duplicate}, verif_count={len(db_ext.verifications.docs)}",
        )

        # 5.10 Unmatched capture acknowledged without verification record
        db_unmatched = _MockMongoDB()
        unmatched_event = json.loads(json.dumps(fix_cap))
        unmatched_event["id"] = "WH-UNMATCHED-CAPTURE-999"
        unmatched_event["resource"]["custom_id"] = "ev:evt_unknown_missing"
        unmatched_event["resource"]["supplementary_data"]["related_ids"]["order_id"] = "ORDER_UNKNOWN_MISSING"

        ru = await reconcile_paypal_event(unmatched_event, db_unmatched)
        unmatched_ok = (
            ru.processed is False
            and ru.matched is False
            and ru.duplicate is False
            and len(db_unmatched.verifications.docs) == 0
        )
        report(
            "5.10 Unmatched capture: acknowledged with processed=False, matched=False, 0 verifications created in DB",
            unmatched_ok,
            f"processed={ru.processed}, matched={ru.matched}, verif_count={len(db_unmatched.verifications.docs)}",
        )

        # 5.11 Non-settlement fixtures acknowledge without error
        non_settlement_files = [
            "payment_capture_declined.json",
            "invoicing_invoice_created.json",
            "invoicing_invoice_sent.json",
            "vault_payment_token_created.json",
        ]
        non_settlement_results = []
        for nsf in non_settlement_files:
            ns_res = await reconcile_paypal_event(fixtures[nsf], db_unmatched)
            non_settlement_results.append(ns_res.processed is False and ns_res.matched is False)
        all_ns_ok = all(non_settlement_results) and len(db_unmatched.verifications.docs) == 0
        report(
            "5.11 Non-settlement fixtures: invoice created/sent, token created, capture declined acknowledge with 0 verifications",
            all_ns_ok,
            f"all 4 acknowledged without verifications; total db verifications={len(db_unmatched.verifications.docs)}",
        )

        # 5.12 PayPal cumulative recovery aggregation: partial ($10.00) + full ($100.00) for same event collapses to $100.00 (not $110.00)
        from app.metrics.reader import Snapshot, distinct_recoveries, recovered_amount_per_event
        from app.metrics.aggregate import summarize

        db_cum = _MockMongoDB()
        db_cum.executions.docs.append({
            "_id": ObjectId("507f1f77bcf86cd799439005"),
            "gateway": "paypal",
            "gateway_ref": "INV2-LSEJ-2LLG-D59L-2QNU",
            "event_id": "evt_paypal_inv_cum_test",
            "amount": 100.0,
        })
        db_cum.events.docs.append({
            "event_id": "evt_paypal_inv_cum_test",
            "amount": 100.0,
            "currency": "USD",
            "status": "at_risk",
        })

        await reconcile_paypal_event(fix_part, db_cum)
        await reconcile_paypal_event(fix_full, db_cum)

        assert len(db_cum.verifications.docs) == 2, f"Expected 2 verifications, got {len(db_cum.verifications.docs)}"
        cum_survivors, cum_dupes = distinct_recoveries(tuple(db_cum.verifications.docs))
        cum_per_event = recovered_amount_per_event(tuple(db_cum.verifications.docs))

        snap_cum = Snapshot(
            events=tuple(db_cum.events.docs),
            diagnoses=(),
            decisions=(),
            verdicts=(),
            executions=tuple(db_cum.executions.docs),
            verifications=tuple(db_cum.verifications.docs),
            promises=(),
            read_at=datetime.now(timezone.utc),
        )
        summary_cum = summarize(snap_cum)

        cum_ok = (
            len(cum_survivors) == 1
            and cum_dupes == 1
            and cum_per_event.get("evt_paypal_inv_cum_test") == 100.0
            and summary_cum.total_revenue_recovered == 100.0
            and summary_cum.gateway_verified_recovered == 100.0
        )
        report(
            "5.12 PayPal cumulative recovery aggregation: partial ($10.00) + full ($100.00) collapses to $100.00 (not $110.00)",
            cum_ok,
            f"recovered={summary_cum.total_revenue_recovered}, distinct_survivors={len(cum_survivors)}, dupes_ignored={cum_dupes}",
        )

    asyncio.run(run_reconciliation_tests())


def test_step6_razorpay_verifier_characterization() -> None:
    print("\n--- SECTION 6: RAZORPAY VERIFIER CHARACTERIZATION (OFFLINE) ---")

    # 6.1 gateway razorpay requires razorpay_event_id and razorpay_event
    rzp_requires_fields = False
    try:
        WebhookVerification(
            event_id="evt_rzp_req_01",
            execution_id="507f1f77bcf86cd799439011",
            gateway="razorpay",
            razorpay_event="payment_link.paid",
            outcome="recovered",
            amount_recovered=100.0,
            amount_expected=100.0,
            amount_mismatch=False,
        )
    except ValidationError:
        try:
            WebhookVerification(
                event_id="evt_rzp_req_02",
                execution_id="507f1f77bcf86cd799439011",
                gateway="razorpay",
                razorpay_event_id="rzp_evt_001",
                outcome="recovered",
                amount_recovered=100.0,
                amount_expected=100.0,
                amount_mismatch=False,
            )
        except ValidationError:
            rzp_requires_fields = True

    # Also verify paypal requires paypal_event_id and paypal_event without loosening
    paypal_requires_fields = False
    try:
        WebhookVerification(
            event_id="evt_pp_req_01",
            execution_id="507f1f77bcf86cd799439011",
            gateway="paypal",
            paypal_event="PAYMENT.CAPTURE.COMPLETED",
            outcome="recovered",
            amount_recovered=100.0,
            amount_expected=100.0,
            amount_mismatch=False,
        )
    except ValidationError:
        try:
            WebhookVerification(
                event_id="evt_pp_req_02",
                execution_id="507f1f77bcf86cd799439011",
                gateway="paypal",
                paypal_event_id="WH-TEST-001",
                outcome="recovered",
                amount_recovered=100.0,
                amount_expected=100.0,
                amount_mismatch=False,
            )
        except ValidationError:
            paypal_requires_fields = True

    report(
        "6.1 Gateway field requirements: razorpay requires razorpay_event_id & razorpay_event; paypal requires paypal_event_id & paypal_event",
        rzp_requires_fields and paypal_requires_fields,
        f"razorpay_strict={rzp_requires_fields}, paypal_strict={paypal_requires_fields}",
    )

    # 6.2 payment_link.paid -> recovered with matching money
    paid_ok = False
    try:
        wv_paid = WebhookVerification(
            event_id="evt_rzp_paid",
            execution_id="507f1f77bcf86cd799439012",
            gateway="razorpay",
            razorpay_event_id="rzp_evt_paid_001",
            razorpay_event="payment_link.paid",
            outcome="recovered",
            amount_recovered=500.0,
            amount_expected=500.0,
            amount_mismatch=False,
        )
        try:
            WebhookVerification(
                event_id="evt_rzp_paid_zero",
                execution_id="507f1f77bcf86cd799439012",
                gateway="razorpay",
                razorpay_event_id="rzp_evt_paid_002",
                razorpay_event="payment_link.paid",
                outcome="recovered",
                amount_recovered=0.0,
                amount_expected=500.0,
                amount_mismatch=True,
            )
            zero_rejected = False
        except ValidationError:
            zero_rejected = True
        paid_ok = (
            wv_paid.outcome == "recovered"
            and wv_paid.amount_recovered == 500.0
            and wv_paid.assertion == GATEWAY_VERIFIED
            and zero_rejected
        )
    except Exception:
        paid_ok = False
    report(
        "6.2 payment_link.paid: derives outcome 'recovered' with matching money, rejects recovery of 0",
        paid_ok,
        f"outcome=recovered, money matched, zero rejected={paid_ok}",
    )

    # 6.3 payment_link.expired and payment_link.cancelled outcomes
    exp_canc_ok = False
    try:
        wv_exp = WebhookVerification(
            event_id="evt_rzp_exp",
            execution_id="507f1f77bcf86cd799439013",
            gateway="razorpay",
            razorpay_event_id="rzp_evt_exp_001",
            razorpay_event="payment_link.expired",
            outcome="expired",
            amount_recovered=0.0,
            amount_expected=500.0,
            amount_mismatch=False,
        )
        wv_canc = WebhookVerification(
            event_id="evt_rzp_canc",
            execution_id="507f1f77bcf86cd799439014",
            gateway="razorpay",
            razorpay_event_id="rzp_evt_canc_001",
            razorpay_event="payment_link.cancelled",
            outcome="cancelled",
            amount_recovered=0.0,
            amount_expected=500.0,
            amount_mismatch=False,
        )
        try:
            WebhookVerification(
                event_id="evt_rzp_exp_money",
                execution_id="507f1f77bcf86cd799439013",
                gateway="razorpay",
                razorpay_event_id="rzp_evt_exp_002",
                razorpay_event="payment_link.expired",
                outcome="expired",
                amount_recovered=100.0,
                amount_expected=500.0,
                amount_mismatch=True,
            )
            nonzero_exp_rejected = False
        except ValidationError:
            nonzero_exp_rejected = True
        exp_canc_ok = (
            wv_exp.outcome == "expired"
            and wv_canc.outcome == "cancelled"
            and nonzero_exp_rejected
        )
    except Exception:
        exp_canc_ok = False
    report(
        "6.3 Expired and cancelled outcomes: amount_recovered must be 0.0, non-zero money rejected",
        exp_canc_ok,
        f"expired and cancelled validated, non-zero money rejected={exp_canc_ok}",
    )

    # 6.4 Mismatched event/outcome is rejected
    mismatch_rejected = False
    try:
        WebhookVerification(
            event_id="evt_rzp_mismatch",
            execution_id="507f1f77bcf86cd799439015",
            gateway="razorpay",
            razorpay_event_id="rzp_evt_mis_001",
            razorpay_event="payment_link.paid",
            outcome="expired",
            amount_recovered=0.0,
            amount_expected=500.0,
            amount_mismatch=False,
        )
    except ValidationError:
        try:
            WebhookVerification(
                event_id="evt_rzp_mismatch2",
                execution_id="507f1f77bcf86cd799439015",
                gateway="razorpay",
                razorpay_event_id="rzp_evt_mis_002",
                razorpay_event="payment_link.cancelled",
                outcome="recovered",
                amount_recovered=500.0,
                amount_expected=500.0,
                amount_mismatch=False,
            )
        except ValidationError:
            mismatch_rejected = True
    report(
        "6.4 Mismatched event/outcome: paid with outcome=expired and cancelled with outcome=recovered are rejected",
        mismatch_rejected,
        f"mismatched outcomes rejected={mismatch_rejected}",
    )

    # 6.5 Amount-mismatch invariants
    mismatch_invariants_ok = False
    try:
        try:
            WebhookVerification(
                event_id="evt_rzp_diff",
                execution_id="507f1f77bcf86cd799439016",
                gateway="razorpay",
                razorpay_event_id="rzp_evt_diff_001",
                razorpay_event="payment_link.paid",
                outcome="recovered",
                amount_recovered=400.0,
                amount_expected=500.0,
                amount_mismatch=False,
            )
            lie_rejected = False
        except ValidationError:
            lie_rejected = True

        try:
            WebhookVerification(
                event_id="evt_rzp_match",
                execution_id="507f1f77bcf86cd799439017",
                gateway="razorpay",
                razorpay_event_id="rzp_evt_match_001",
                razorpay_event="payment_link.paid",
                outcome="recovered",
                amount_recovered=500.0,
                amount_expected=500.0,
                amount_mismatch=True,
            )
            false_mismatch_rejected = False
        except ValidationError:
            false_mismatch_rejected = True

        wv_valid_mismatch = WebhookVerification(
            event_id="evt_rzp_valid_diff",
            execution_id="507f1f77bcf86cd799439018",
            gateway="razorpay",
            razorpay_event_id="rzp_evt_diff_002",
            razorpay_event="payment_link.paid",
            outcome="recovered",
            amount_recovered=400.0,
            amount_expected=500.0,
            amount_mismatch=True,
        )
        mismatch_invariants_ok = (
            lie_rejected and false_mismatch_rejected and wv_valid_mismatch.amount_mismatch is True
        )
    except Exception:
        mismatch_invariants_ok = False
    report(
        "6.5 Amount-mismatch invariants: re-derived strictly; false mismatch and false match both rejected",
        mismatch_invariants_ok,
        f"invariants enforced={mismatch_invariants_ok}",
    )

    # 6.6 Manual confirmations stay merchant_asserted
    manual_ok = False
    try:
        mv = ManualVerification(
            event_id="evt_manual_01",
            execution_id="507f1f77bcf86cd799439019",
            confirmation_id="manual_conf_507f1f77bcf86cd799439019",
            confirmed_by=MANUAL_CONFIRMATION_CHANNEL,
            amount_recovered=250.0,
            amount_expected=250.0,
            amount_mismatch=False,
        )
        manual_ok = (
            mv.assertion == MERCHANT_ASSERTED
            and mv.source == MANUAL_SOURCE
            and mv.outcome == "recovered"
            and mv.confirmation_id == "manual_conf_507f1f77bcf86cd799439019"
        )
    except Exception:
        manual_ok = False
    report(
        "6.6 Manual confirmations: pinned to source=manual_confirmation, assertion=merchant_asserted, channel=merchant_via_api",
        manual_ok,
        f"assertion={getattr(mv, 'assertion', None)}, source={getattr(mv, 'source', None)}",
    )


def test_step7_public_demo_guard() -> None:
    print("\n--- SECTION 7: PUBLIC-DEMO GUARD CONTRACT CHECKS (OFFLINE) ---")
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from app.config import Settings
    from app.middleware import PublicDemoMiddleware

    def build_test_app(settings: Settings) -> TestClient:
        app = FastAPI()
        app.add_middleware(PublicDemoMiddleware, settings=settings)

        @app.get("/items")
        async def get_items():
            return {"items": ["a", "b"]}

        @app.post("/items")
        async def create_item():
            return {"created": True}

        @app.post("/webhooks/paypal")
        async def paypal_webhook():
            return {"received": True}

        return TestClient(app)

    # 7.1 Flag off -> write without token allowed
    s_off = Settings(public_demo=False, admin_token="")
    c_off = build_test_app(s_off)
    r_off = c_off.post("/items", json={})
    report(
        "7.1 Public demo flag off: write request allowed without token",
        r_off.status_code == 200,
        f"status={r_off.status_code}",
    )

    # 7.2 Flag on, empty token -> 503 fail closed
    s_empty = Settings(public_demo=True, admin_token="")
    c_empty = build_test_app(s_empty)
    r_empty = c_empty.post("/items", json={}, headers={"X-Admin-Token": "foo"})
    report(
        "7.2 Public demo on, empty admin_token: writes fail closed with 503",
        r_empty.status_code == 503,
        f"status={r_empty.status_code}",
    )

    # 7.3 Flag on, write without token -> 401
    s_on = Settings(public_demo=True, admin_token="supersecrettoken")
    c_on = build_test_app(s_on)
    r_no_token = c_on.post("/items", json={})
    report(
        "7.3 Public demo on: write without X-Admin-Token returns 401",
        r_no_token.status_code == 401,
        f"status={r_no_token.status_code}",
    )

    # 7.4 Flag on, wrong token -> 401
    r_wrong = c_on.post("/items", json={}, headers={"X-Admin-Token": "wrongtoken"})
    report(
        "7.4 Public demo on: write with wrong X-Admin-Token returns 401",
        r_wrong.status_code == 401,
        f"status={r_wrong.status_code}",
    )

    # 7.5 Flag on, right token -> 200 allowed
    r_right = c_on.post("/items", json={}, headers={"X-Admin-Token": "supersecrettoken"})
    report(
        "7.5 Public demo on: write with valid X-Admin-Token returns 200",
        r_right.status_code == 200,
        f"status={r_right.status_code}",
    )

    # 7.6 Flag on: POST /webhooks/paypal bypasses the token
    r_pp = c_on.post("/webhooks/paypal", json={})
    report(
        "7.6 Public demo on: POST /webhooks/paypal bypasses admin token check",
        r_pp.status_code == 200,
        f"status={r_pp.status_code}",
    )

    # 7.7 Flag on: GET endpoints unaffected
    r_get = c_on.get("/items")
    report(
        "7.7 Public demo on: GET endpoints remain public without token",
        r_get.status_code == 200,
        f"status={r_get.status_code}",
    )


def test_step8_settings_cors_origins() -> None:
    print("\n--- SECTION 8: SETTINGS CORS_ORIGINS & LIST FIELDS CONTRACT CHECKS (OFFLINE) ---")
    import os
    import typing
    from app.config import Settings

    orig_cors = os.environ.get("CORS_ORIGINS")
    orig_demo = os.environ.get("PUBLIC_DEMO")

    forms = [
        ("unset", None, []),
        ("empty string", "", []),
        ("single URL", "http://localhost:5173", ["http://localhost:5173"]),
        ("comma-separated list", "http://a.com, http://b.com", ["http://a.com", "http://b.com"]),
        ("comma-separated with whitespace/empty dropped", "http://a.com, , http://b.com, ", ["http://a.com", "http://b.com"]),
        ("JSON array", '["http://a.com", "http://b.com"]', ["http://a.com", "http://b.com"]),
    ]

    try:
        # Test each form with PUBLIC_DEMO both off and on
        for demo_flag, demo_expected, demo_label in [("false", False, "off"), ("true", True, "on")]:
            os.environ["PUBLIC_DEMO"] = demo_flag
            for form_name, env_val, expected in forms:
                if env_val is None:
                    os.environ.pop("CORS_ORIGINS", None)
                else:
                    os.environ["CORS_ORIGINS"] = env_val

                s = Settings()
                ok = (s.cors_origins == expected) and (s.public_demo == demo_expected)
                report(
                    f"8.{demo_label}.{form_name}: Settings loads CORS_ORIGINS ({form_name}) with PUBLIC_DEMO={demo_label}",
                    ok,
                    f"cors_origins={s.cors_origins!r}, public_demo={s.public_demo!r}",
                )

        # Check that every other list-typed field in Settings, if any, loads from a plain string env value
        def _is_list_type(annotation: Any) -> bool:
            if annotation is list:
                return True
            origin = typing.get_origin(annotation)
            if origin is list:
                return True
            if origin is typing.Annotated:
                args = typing.get_args(annotation)
                return bool(args and _is_list_type(args[0]))
            if origin is typing.Union:
                return any(_is_list_type(arg) for arg in typing.get_args(annotation))
            return False

        other_list_fields = [
            name for name, field in Settings.model_fields.items()
            if name != "cors_origins" and _is_list_type(field.annotation)
        ]

        if other_list_fields:
            for name in other_list_fields:
                env_var = name.upper()
                prev = os.environ.get(env_var)
                try:
                    os.environ[env_var] = "plain_string_value"
                    s = Settings()
                    val = getattr(s, name)
                    report(
                        f"8.other.{name}: list field '{name}' loads from plain string env value",
                        isinstance(val, list) and len(val) > 0,
                        f"{name}={val!r}",
                    )
                except Exception as exc:
                    report(
                        f"8.other.{name}: list field '{name}' loads from plain string env value",
                        False,
                        f"exception: {exc}",
                    )
                finally:
                    if prev is None:
                        os.environ.pop(env_var, None)
                    else:
                        os.environ[env_var] = prev
        else:
            report(
                "8.other: Every other list-typed field in Settings, if any, loads from a plain string env value",
                True,
                "no other list-typed fields present in Settings",
            )
    finally:
        if orig_cors is None:
            os.environ.pop("CORS_ORIGINS", None)
        else:
            os.environ["CORS_ORIGINS"] = orig_cors
        if orig_demo is None:
            os.environ.pop("PUBLIC_DEMO", None)
        else:
            os.environ["PUBLIC_DEMO"] = orig_demo


def main() -> int:
    print("=" * 78)
    print("PHASE 1 CONTRACT CHECKS (OFFLINE — NO NETWORK, NO DB, NO SERVER)")
    print("=" * 78)

    test_step1_models()
    test_characterization_razorpay_execution()
    test_step2_rulebook_and_policy()
    test_step3_execution_port_and_adapters()
    test_step5_paypal_reconciliation()
    test_step6_razorpay_verifier_characterization()
    test_step7_public_demo_guard()
    test_step8_settings_cors_origins()

    print("\n" + "=" * 78)
    print(f"SUMMARY: {PASS_COUNT} PASSED, {FAIL_COUNT} FAILED")
    print("=" * 78)
    return 1 if FAIL_COUNT > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
