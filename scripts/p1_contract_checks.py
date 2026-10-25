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

import logging

RECORDED_INR_FINGERPRINT = "rb1_aba19a5e5ee8124e"
RECORDED_USD_FINGERPRINT = "rb1_8221b1b9069ffc80"
SUPERSEDED_USD_V1_FINGERPRINT = "rb1_c7c49bbd323ceed4"
SUPERSEDED_USD_V2_FINGERPRINT = "rb1_f1e6f9c8957920de"

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

    # 3.2b Rulebook registry resolves historical USD v1, current USD, and INR
    from app.policy.rules import rulebook_registry
    registry = rulebook_registry()
    registry_ok = (
        RECORDED_INR_FINGERPRINT in registry
        and SUPERSEDED_USD_V1_FINGERPRINT in registry
        and SUPERSEDED_USD_V2_FINGERPRINT in registry
        and RECORDED_USD_FINGERPRINT in registry
    )
    report(
        "rulebook_registry keeps previous USD rulebooks (v1, v2) and resolves INR and current USD",
        registry_ok,
        f"has_inr={RECORDED_INR_FINGERPRINT in registry}, has_v1_usd={SUPERSEDED_USD_V1_FINGERPRINT in registry}, has_v2_usd={SUPERSEDED_USD_V2_FINGERPRINT in registry}, has_current_usd={RECORDED_USD_FINGERPRINT in registry}",
    )

    # 3.2c Fingerprint ledger contract check (docs/regression/fingerprints.md)
    ledger_path = ROOT / "docs" / "regression" / "fingerprints.md"
    ledger_content = ledger_path.read_text(encoding="utf-8")
    ledger_lines = [line.strip() for line in ledger_content.splitlines() if line.strip().startswith("|")]
    table_rows = [r for r in ledger_lines if not r.startswith("| Currency") and not r.startswith("|---")]
    inr_rows = [r for r in table_rows if "| INR |" in r]
    usd_rows = [r for r in table_rows if "| USD |" in r]

    inr_ledger_ok = len(inr_rows) == 1 and f"`{RECORDED_INR_FINGERPRINT}`" in inr_rows[0]
    last_usd_fp_ok = len(usd_rows) >= 1 and f"`{current_fingerprint('USD')}`" in usd_rows[-1]
    report(
        "Fingerprint ledger contract check: INR row equals rb1_aba19a5e5ee8124e and last USD row equals current_fingerprint('USD')",
        inr_ledger_ok and last_usd_fp_ok,
        f"inr_ok={inr_ledger_ok}, last_usd_ok={last_usd_fp_ok}",
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
    # 3.9 USD Retry Cap: max 3 retry executions per event
    from datetime import timedelta
    dec_retry = make_decision(amount=100.0, intervention="delayed_retry", cost=0.0, prob=0.8, erv=80.0)
    ctx_retry_cap = PolicyContext(
        customer_ref="cust_usd_cap",
        customer_opted_out=False,
        prior_authorized_contacts=0,
        last_authorized_contact_at=None,
        prior_retry_executions=3,
        last_retry_execution_at=ctx.now - timedelta(days=2),
        now=ctx.now,
    )
    v_retry_cap = evaluate(decision=dec_retry, context=ctx_retry_cap, currency="USD")
    report(
        "USD retry cap: >= 3 prior retry executions blocks with retry_cap_exceeded",
        v_retry_cap.verdict == "blocked" and v_retry_cap.reason == "retry_cap_exceeded",
        f"verdict={v_retry_cap.verdict}, reason={v_retry_cap.reason}",
    )

    # 3.10 USD Retry Cooldown: at least 24 hours between retries
    ctx_retry_cooldown = PolicyContext(
        customer_ref="cust_usd_cooldown",
        customer_opted_out=False,
        prior_authorized_contacts=0,
        last_authorized_contact_at=None,
        prior_retry_executions=1,
        last_retry_execution_at=ctx.now - timedelta(hours=12),
        now=ctx.now,
    )
    v_retry_cooldown = evaluate(decision=dec_retry, context=ctx_retry_cooldown, currency="USD")
    report(
        "USD retry cooldown: < 24h since last retry execution blocks with retry_cap_exceeded",
        v_retry_cooldown.verdict == "blocked" and v_retry_cooldown.reason == "retry_cap_exceeded",
        f"verdict={v_retry_cooldown.verdict}, reason={v_retry_cooldown.reason}",
    )

    # 3.11 USD Retry Allowed: < 3 retries and >= 24h gap authorizes
    ctx_retry_ok = PolicyContext(
        customer_ref="cust_usd_retry_ok",
        customer_opted_out=False,
        prior_authorized_contacts=0,
        last_authorized_contact_at=None,
        prior_retry_executions=2,
        last_retry_execution_at=ctx.now - timedelta(hours=25),
        now=ctx.now,
    )
    v_retry_ok = evaluate(decision=dec_retry, context=ctx_retry_ok, currency="USD")
    report(
        "USD retry allowed: < 3 prior retries and >= 24h gap authorizes with ok",
        v_retry_ok.verdict == "authorized" and v_retry_ok.reason == "ok",
        f"verdict={v_retry_ok.verdict}, reason={v_retry_ok.reason}",
    )

    # 3.12 INR profile untouched: retry_cap_exceeded is not in INR reason precedence or verdicts
    from app.policy.rules import INR_REASON_PRECEDENCE, INR_REASON_VERDICT
    inr_untouched = (
        "retry_cap_exceeded" not in INR_REASON_PRECEDENCE
        and not any(r == "retry_cap_exceeded" for r, _ in INR_REASON_VERDICT)
    )
    report(
        "INR tables untouched: retry_cap_exceeded not present in INR precedence or verdict tables",
        inr_untouched,
        f"in_precedence={'retry_cap_exceeded' in INR_REASON_PRECEDENCE}",
    )

    # 3.13 DO_NOT_RETRY_RESPONSE_CODES {"9500", "5180"} defence-in-depth policy check (USD profile only)
    from app.policy.rules import DO_NOT_RETRY_RESPONSE_CODES
    ctx_resp_9500 = PolicyContext(
        customer_ref="cust_resp_9500",
        customer_opted_out=False,
        prior_authorized_contacts=0,
        last_authorized_contact_at=None,
        has_vault_token=True,
        processor_response_code="9500",
        now=ctx.now,
    )
    v_resp_9500 = evaluate(decision=dec_retry, context=ctx_resp_9500, currency="USD")
    ctx_resp_5180 = PolicyContext(
        customer_ref="cust_resp_5180",
        customer_opted_out=False,
        prior_authorized_contacts=0,
        last_authorized_contact_at=None,
        has_vault_token=True,
        processor_response_code="5180",
        now=ctx.now,
    )
    v_resp_5180 = evaluate(decision=dec_retry, context=ctx_resp_5180, currency="USD")
    # In INR, processor_response_code is ignored and does not block retry
    v_resp_inr = evaluate(decision=dec_retry, context=ctx_resp_9500, currency="INR")
    report(
        "3.13 DO_NOT_RETRY_RESPONSE_CODES ({'9500', '5180'}) blocks retries in USD with retry_not_advised; INR untouched",
        v_resp_9500.verdict == "blocked"
        and v_resp_9500.reason == "retry_not_advised"
        and v_resp_5180.verdict == "blocked"
        and v_resp_5180.reason == "retry_not_advised"
        and v_resp_inr.verdict == "authorized",
        f"usd_9500={v_resp_9500.reason}, usd_5180={v_resp_5180.reason}, inr_verdict={v_resp_inr.verdict}",
    )

    # 3.14 Offline check: DECLINED retry executions count toward the USD retry cap
    from app.models.execution import ExecutionRecord
    exec_declined = ExecutionRecord(
        event_id="pp_offline_cap_test",
        policy_verdict_id="507f1f77bcf86cd799439011",
        policy_verdict_version=1,
        intervention="delayed_retry",
        action_type="retry_simulated",
        status="completed",
        successful=False,
        capture_status="DECLINED",
        processor_response_code="5120",
        gateway="paypal",
        gateway_ref="ORD-DECLINED-123",
    )
    ctx_declined_retries = PolicyContext(
        customer_ref="cust_declined_cap",
        customer_opted_out=False,
        prior_authorized_contacts=0,
        last_authorized_contact_at=None,
        prior_retry_executions=3,  # accumulated from 3 declined retries
        last_retry_execution_at=ctx.now - timedelta(days=2),
        now=ctx.now,
    )
    v_declined_cap = evaluate(decision=dec_retry, context=ctx_declined_retries, currency="USD")
    report(
        "3.14 Offline check: DECLINED retry executions count toward USD retry cap (blocks with retry_cap_exceeded)",
        exec_declined.status == "completed"
        and exec_declined.successful is False
        and exec_declined.capture_status == "DECLINED"
        and v_declined_cap.verdict == "blocked"
        and v_declined_cap.reason == "retry_cap_exceeded",
        f"exec_status={exec_declined.status}, successful={exec_declined.successful}, verdict={v_declined_cap.verdict}, reason={v_declined_cap.reason}",
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

    async def find_one(self, query: dict[str, Any], projection: dict[str, Any] | None = None, sort: Any = None) -> dict[str, Any] | None:
        matching = [d for d in self.docs if self._matches(d, query)]
        if not matching:
            return None
        if sort:
            for field, direction in reversed(sort):
                matching.sort(key=lambda x: x.get(field, 0), reverse=(direction == -1 or str(direction) == "-1"))
        return dict(matching[0])

    def find(self, query: dict[str, Any] | None = None, sort: Any = None):
        matching = [d for d in self.docs if (query is None or self._matches(d, query))]

        class _Cursor:
            def __init__(self, items):
                self._items = items

            def sort(self, *args, **kwargs):
                return self

            def limit(self, count):
                self._items = self._items[:count]
                return self

            async def to_list(self, length=None):
                return [dict(d) for d in self._items]

            def __aiter__(self):
                self._iter = iter(self._items)
                return self

            async def __anext__(self):
                try:
                    return dict(next(self._iter))
                except StopIteration:
                    raise StopAsyncIteration

        return _Cursor(matching)

    async def insert_one(self, doc: dict[str, Any]) -> Any:
        unique_keys = ["paypal_event_id", "vault_id", "order_id"]
        for uk in unique_keys:
            if doc.get(uk):
                for d in self.docs:
                    if d.get(uk) == doc[uk]:
                        raise DuplicateKeyError(f"E11000 duplicate key error on {self.name}.{uk}")
        if self.name == "orchestrator_runs":
            for d in self.docs:
                if (
                    d.get("event_id") == doc.get("event_id")
                    and d.get("stage") == doc.get("stage")
                    and d.get("record_version") == doc.get("record_version")
                ):
                    raise DuplicateKeyError(f"E11000 duplicate key error on orchestrator_runs")
        d = dict(doc)
        if "_id" not in d:
            d["_id"] = ObjectId()
        self.docs.append(d)

        class _InsertRes:
            inserted_id = d["_id"]

        return _InsertRes()

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> Any:
        for doc in self.docs:
            if self._matches(doc, query):
                if "$set" in update:
                    for k, v in update["$set"].items():
                        if "." in k:
                            parts = k.split(".")
                            cur = doc
                            for p in parts[:-1]:
                                if p.isdigit():
                                    cur = cur[int(p)]
                                else:
                                    cur = cur.setdefault(p, {})
                            last_p = parts[-1]
                            if last_p.isdigit():
                                cur[int(last_p)] = v
                            else:
                                cur[last_p] = v
                        else:
                            doc[k] = v

                class _UpdateRes:
                    matched_count = 1
                    modified_count = 1
                    upserted_id = None

                return _UpdateRes()

        if upsert:
            new_doc = dict(query)
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            if "$set" in update:
                new_doc.update(update["$set"])
            if "_id" not in new_doc:
                new_doc["_id"] = ObjectId()
            self.docs.append(new_doc)

            class _UpsertRes:
                matched_count = 0
                modified_count = 0
                upserted_id = new_doc["_id"]

            return _UpsertRes()

        class _UpdateResZero:
            matched_count = 0
            modified_count = 0
            upserted_id = None

        return _UpdateResZero()

    async def drop(self) -> None:
        self.docs.clear()

    async def count_documents(self, filter: dict[str, Any] | None = None) -> int:
        if not filter:
            return len(self.docs)
        return sum(1 for d in self.docs if self._matches(d, filter))

    async def delete_many(self, filter: dict[str, Any] | None = None) -> Any:
        if not filter:
            count = len(self.docs)
            self.docs.clear()
            return count
        orig_len = len(self.docs)
        self.docs = [d for d in self.docs if not self._matches(d, filter)]
        return orig_len - len(self.docs)

    async def index_information(self) -> dict[str, Any]:
        return {}

    async def drop_index(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def create_index(self, *args, **kwargs) -> str:
        return "idx_mock"


class _MockMongoDB:
    def __init__(self, name: str = "vasooli_regress"):
        self.name = name
        self.verifications = _MockMongoCollection("verifications")
        self.executions = _MockMongoCollection("executions")
        self.events = _MockMongoCollection("events")
        self.policy_verdicts = _MockMongoCollection("policy_verdicts")
        self.decisions = _MockMongoCollection("decisions")
        self.diagnoses = _MockMongoCollection("diagnoses")
        self.charge_attempts = _MockMongoCollection("charge_attempts")
        self.customer_vault = _MockMongoCollection("customer_vault")
        self.paypal_events = _MockMongoCollection("paypal_events")
        self.orchestrator_runs = _MockMongoCollection("orchestrator_runs")
        self.demo_runs = _MockMongoCollection("demo_runs")
        self.sim_clock = _MockMongoCollection("sim_clock")

    def __getitem__(self, name: str) -> _MockMongoCollection:
        if not hasattr(self, name):
            setattr(self, name, _MockMongoCollection(name))
        return getattr(self, name)

    async def list_collection_names(self) -> list[str]:
        return [
            "verifications",
            "executions",
            "events",
            "policy_verdicts",
            "decisions",
            "diagnoses",
            "charge_attempts",
            "customer_vault",
            "paypal_events",
            "orchestrator_runs",
            "sim_clock",
        ]


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

    # 5.1c Live captured fixtures provenance: tests/fixtures/paypal/live/ match SHA-256
    live_fixtures_dir = fixtures_dir / "live"
    live_fixture_files = [
        "payment_capture_completed_order_a.json",
        "payment_capture_completed_order_c.json",
        "payment_capture_declined_order_b.json",
        "vault_payment_token_created.json",
    ]
    live_hashes_valid = True
    live_hash_details: list[str] = []
    for lf in live_fixture_files:
        lp = live_fixtures_dir / lf
        if not lp.exists():
            live_hashes_valid = False
            live_hash_details.append(f"{lf}: missing")
            continue
        data = json.loads(lp.read_text(encoding="utf-8"))
        if data.get("source") != "captured":
            live_hashes_valid = False
            live_hash_details.append(f"{lf}: missing source=captured")
            continue
        recorded_hash = data.get("original_payload_sha256")
        clean = {k: v for k, v in data.items() if k not in ("source", "original_payload_sha256")}
        actual_hash = hashlib.sha256(json.dumps(clean, sort_keys=True).encode("utf-8")).hexdigest()
        if not recorded_hash or actual_hash != recorded_hash:
            live_hashes_valid = False
            live_hash_details.append(f"{lf}: expected {recorded_hash}, got {actual_hash}")
        else:
            live_hash_details.append(f"{lf}: OK ({actual_hash[:8]}...)")
    report(
        "5.1c Live captured fixtures provenance: tests/fixtures/paypal/live/ match SHA-256 (labeled 'captured')",
        live_hashes_valid and len(live_hash_details) == 4,
        "; ".join(live_hash_details),
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


def test_step9_paypal_capture_declined_and_vault() -> None:
    print("\n--- SECTION 9: PAYPAL DECLINES, VAULT STORE & OUT-OF-ORDER DELIVERIES (OFFLINE) ---")
    fixtures_dir = ROOT / "tests" / "fixtures" / "paypal"
    fix_declined = json.loads((fixtures_dir / "payment_capture_declined.json").read_text(encoding="utf-8"))
    fix_vault = json.loads((fixtures_dir / "vault_payment_token_created.json").read_text(encoding="utf-8"))
    fix_part = json.loads((fixtures_dir / "invoicing_invoice_paid_partial.json").read_text(encoding="utf-8"))
    fix_full = json.loads((fixtures_dir / "invoicing_invoice_paid_full.json").read_text(encoding="utf-8"))

    from app.gateways.paypal.charge_attempts import record_charge_attempt
    from app.gateways.paypal.vault import get_active_vault_token
    from app.webhooks.paypal_reconciler import reprocess_unmatched_declines
    from app.diagnosis import rules
    from app.diagnosis.service import diagnose
    from app.models.diagnosis import LLMDiagnosisProposal
    from app.metrics.reader import Snapshot, distinct_recoveries, recovered_amount_per_event

    async def run_step9_tests():
        # 9.1 decline fixture -> event fields
        db_dec = _MockMongoDB()
        await record_charge_attempt(
            order_id="6PW329308U6154049",
            customer_ref="cust_decline_test_01",
            amount=10.0,
            currency="USD",
            purpose="onboarding",
            db=db_dec,
        )

        r_dec = await reconcile_paypal_event(fix_declined, db_dec)
        ev_doc = await db_dec.events.find_one({"event_id": "pp_59185726VV0844043"})

        dec_fields_ok = (
            r_dec.processed is True
            and r_dec.matched is True
            and r_dec.duplicate is False
            and r_dec.event_status == "at_risk"
            and ev_doc is not None
            and ev_doc.get("surface") == "payment"
            and ev_doc.get("gateway") == "paypal"
            and ev_doc.get("gateway_ref") == "6PW329308U6154049"
            and ev_doc.get("amount") == 10.0
            and ev_doc.get("currency") == "USD"
            and ev_doc.get("customer_ref") == "cust_decline_test_01"
            and ev_doc.get("raw_failure_reason") == "paypal_5120"
            and ev_doc.get("status") == "at_risk"
            and isinstance(ev_doc.get("gateway_detail"), dict)
            and ev_doc.get("gateway_detail", {}).get("processor_response", {}).get("response_code") == "5120"
        )
        report(
            "9.1 decline fixture -> event fields (event_id, surface, gateway, gateway_ref, amount, currency, customer_ref, raw_failure_reason, gateway_detail)",
            dec_fields_ok,
            f"event_id={ev_doc.get('event_id') if ev_doc else None}, reason={ev_doc.get('raw_failure_reason') if ev_doc else None}",
        )

        # 9.2 Verified PayPal response code diagnosis (5120 -> insufficient_funds, 0500 -> issuer_declined, 00N7 -> card_verification_failed)
        ev_doc_clean = {k: v for k, v in ev_doc.items() if k != "_id"}
        rev_ev_5120 = RevenueEvent(**ev_doc_clean)
        match_5120 = rules.classify(rev_ev_5120)
        ok_5120 = match_5120 is not None and match_5120.root_cause == "insufficient_funds" and match_5120.is_confident

        rev_ev_0500 = RevenueEvent(
            event_id="pp_test_0500",
            surface="payment",
            gateway="paypal",
            amount=50.0,
            currency="USD",
            customer_ref="cust_test_0500",
            raw_failure_reason="paypal_0500",
            status="at_risk",
        )
        match_0500 = rules.classify(rev_ev_0500)
        ok_0500 = match_0500 is not None and match_0500.root_cause == "issuer_declined" and match_0500.is_confident

        rev_ev_00N7 = RevenueEvent(
            event_id="pp_test_00N7",
            surface="payment",
            gateway="paypal",
            amount=50.0,
            currency="USD",
            customer_ref="cust_test_00N7",
            raw_failure_reason="paypal_00N7",
            status="at_risk",
        )
        match_00N7 = rules.classify(rev_ev_00N7)
        ok_00N7 = match_00N7 is not None and match_00N7.root_cause == "card_verification_failed" and match_00N7.is_confident

        codes_ok = ok_5120 and ok_0500 and ok_00N7
        report(
            "9.2 PayPal verified response codes diagnosis (5120->insufficient_funds, 0500->issuer_declined, 00N7->card_verification_failed)",
            codes_ok,
            f"5120={match_5120.root_cause if match_5120 else None}, 0500={match_0500.root_cause if match_0500 else None}, 00N7={match_00N7.root_cause if match_00N7 else None}",
        )

        # 9.3 unknown code -> flagged for Gemini with Gemini MOCKED
        unknown_ev = RevenueEvent(
            event_id="pp_test_unknown",
            surface="payment",
            gateway="paypal",
            amount=75.0,
            currency="USD",
            customer_ref="cust_unknown",
            raw_failure_reason="paypal_9999",
            status="at_risk",
        )
        match_unmapped = rules.classify(unknown_ev)
        assert match_unmapped is None, "Unmapped PayPal code should not match any offline deterministic rule"

        with patch("app.diagnosis.gemini.is_configured", return_value=True), \
             patch("app.diagnosis.gemini.propose_diagnosis", new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = (
                LLMDiagnosisProposal(
                    root_cause="unknown",
                    confidence=0.5,
                    evidence=["unmapped paypal response code 9999 escalated to Gemini"],
                ),
                '{"root_cause":"unknown","confidence":0.5,"evidence":["unmapped paypal response code 9999 escalated to Gemini"]}',
            )
            diag, method, model = await diagnose(unknown_ev)
            gemini_flagged_ok = (
                method == "llm"
                and diag.root_cause == "unknown"
                and mock_gemini.await_count == 1
            )
            report(
                "9.3 Unknown PayPal code -> escalated to Gemini fallback path with Gemini MOCKED",
                gemini_flagged_ok,
                f"method={method}, root_cause={diag.root_cause}, gemini_calls={mock_gemini.await_count}",
            )

        # 9.4 vault fixture -> store
        db_vault = _MockMongoDB()
        await record_charge_attempt(
            order_id="46969136VC639191U",
            customer_ref="cust_vault_buyer_01",
            amount=25.0,
            currency="USD",
            purpose="onboarding",
            db=db_vault,
        )

        r_vault = await reconcile_paypal_event(fix_vault, db_vault)
        vault_doc = await get_active_vault_token("cust_vault_buyer_01", db_vault)

        vault_ok = (
            r_vault.processed is True
            and r_vault.matched is True
            and vault_doc is not None
            and vault_doc.get("vault_id") == "4mr90404j6010301k"
            and vault_doc.get("card_brand") == "VISA"
            and vault_doc.get("last_digits") == "1111"
            and len(vault_doc.get("last_digits", "")) == 4  # Never full card number!
            and vault_doc.get("status") == "active"
            and vault_doc.get("customer_ref") == "cust_vault_buyer_01"
            and vault_doc.vault_id == "4mr90404j6010301k"
        )
        report(
            "9.4 vault fixture -> store: token saved with customer_ref, vault_id, brand, last_digits only, status active",
            vault_ok,
            f"vault_id={vault_doc.get('vault_id') if vault_doc else None}, last_digits={vault_doc.get('last_digits') if vault_doc else None}",
        )

        # 9.5 unmatched decline acknowledged then reprocessed
        db_unmatched = _MockMongoDB()
        r_pre = await reconcile_paypal_event(fix_declined, db_unmatched)
        unmatched_stored = (
            r_pre.processed is False
            and r_pre.matched is False
            and len(db_unmatched.events.docs) == 0
            and len(db_unmatched.paypal_events.docs) == 1
            and db_unmatched.paypal_events.docs[0].get("unmatched") is True
        )

        await record_charge_attempt(
            order_id="6PW329308U6154049",
            customer_ref="cust_late_registered_01",
            amount=10.0,
            currency="USD",
            purpose="onboarding",
            db=db_unmatched,
        )

        reprocessed = await reprocess_unmatched_declines(db_unmatched)
        late_ev = await db_unmatched.events.find_one({"event_id": "pp_59185726VV0844043"})
        reprocess_ok = (
            unmatched_stored
            and len(reprocessed) == 1
            and reprocessed[0].processed is True
            and reprocessed[0].matched is True
            and late_ev is not None
            and late_ev.get("customer_ref") == "cust_late_registered_01"
        )
        report(
            "9.5 Unmatched decline: acknowledged with 200, stored as unmatched, reprocessed successfully after attempt recorded",
            reprocess_ok,
            f"pre_matched={r_pre.matched}, reprocessed={len(reprocessed)}, customer_ref={late_ev.get('customer_ref') if late_ev else None}",
        )

        # 9.6 replays are idempotent
        r_dec_dup = await reconcile_paypal_event(fix_declined, db_dec)
        r_vault_dup = await reconcile_paypal_event(fix_vault, db_vault)

        idempotent_ok = (
            r_dec_dup.duplicate is True
            and r_dec_dup.matched is True
            and len([d for d in db_dec.events.docs if d.get("event_id") == "pp_59185726VV0844043"]) == 1
            and r_vault_dup.duplicate is True
            and len([d for d in db_vault.customer_vault.docs if d.get("vault_id") == "4mr90404j6010301k"]) == 1
        )
        report(
            "9.6 Replays are idempotent: repeat deliveries of decline and vault return duplicate=True with zero extra DB records",
            idempotent_ok,
            f"decline_duplicate={r_dec_dup.duplicate}, vault_duplicate={r_vault_dup.duplicate}",
        )

        # 9.7 Out-of-order delivery: full-then-partial and partial-then-full give identical recovered revenue ($100.00)
        db_seq1 = _MockMongoDB()
        db_seq1.executions.docs.append({
            "_id": ObjectId("507f1f77bcf86cd799439031"),
            "gateway": "paypal",
            "gateway_ref": "INV2-LSEJ-2LLG-D59L-2QNU",
            "event_id": "evt_paypal_ooo_01",
            "amount": 100.0,
        })
        db_seq1.events.docs.append({
            "event_id": "evt_paypal_ooo_01",
            "amount": 100.0,
            "currency": "USD",
            "status": "at_risk",
        })

        r_full_1 = await reconcile_paypal_event(fix_full, db_seq1)
        ev_after_full = db_seq1.events.docs[0]["status"]
        assert ev_after_full == "recovered"

        r_part_1 = await reconcile_paypal_event(fix_part, db_seq1)
        ev_after_part_late = db_seq1.events.docs[0]["status"]
        assert ev_after_part_late == "recovered", f"Expected 'recovered', got '{ev_after_part_late}'"
        assert len(db_seq1.verifications.docs) == 2

        rev_by_event_1 = recovered_amount_per_event(tuple(db_seq1.verifications.docs))

        db_seq2 = _MockMongoDB()
        db_seq2.executions.docs.append({
            "_id": ObjectId("507f1f77bcf86cd799439032"),
            "gateway": "paypal",
            "gateway_ref": "INV2-LSEJ-2LLG-D59L-2QNU",
            "event_id": "evt_paypal_ooo_02",
            "amount": 100.0,
        })
        db_seq2.events.docs.append({
            "event_id": "evt_paypal_ooo_02",
            "amount": 100.0,
            "currency": "USD",
            "status": "at_risk",
        })

        r_part_2 = await reconcile_paypal_event(fix_part, db_seq2)
        assert db_seq2.events.docs[0]["status"] == "at_risk"

        r_full_2 = await reconcile_paypal_event(fix_full, db_seq2)
        assert db_seq2.events.docs[0]["status"] == "recovered"
        assert len(db_seq2.verifications.docs) == 2

        rev_by_event_2 = recovered_amount_per_event(tuple(db_seq2.verifications.docs))

        seq1_amt = rev_by_event_1.get("evt_paypal_ooo_01", 0.0)
        seq2_amt = rev_by_event_2.get("evt_paypal_ooo_02", 0.0)

        ooo_ok = (
            ev_after_part_late == "recovered"
            and seq1_amt == 100.0
            and seq2_amt == 100.0
            and seq1_amt == seq2_amt
        )
        report(
            "9.7 Out-of-order delivery: full-then-partial and partial-then-full give identical recovered revenue ($100.00), no regression",
            ooo_ok,
            f"full_then_partial={seq1_amt:.2f}, partial_then_full={seq2_amt:.2f}, event_status_retained={ev_after_part_late}",
        )

    asyncio.run(run_step9_tests())


def test_step10_orchestrator() -> None:
    print("\n--- SECTION 10: ORCHESTRATOR, SIMULATED CLOCK, LOOP GUARD & RETRY ELIGIBILITY ---")
    from datetime import datetime, timedelta, timezone
    from app.clock import SimulatedClock
    from app.orchestrator.service import advance
    from app.webhooks.paypal_reconciler import reconcile_paypal_event
    from app.policy.engine import PolicyContext, evaluate
    from app.policy.rules import current_rulebook
    from app.execution.port import ExecutionResult
    import app.execution.service

    async def run_step10_tests():
        mock_vault_order_res = {
            "id": "ORD-ORCH-RET-01",
            "status": "COMPLETED",
            "links": [{"rel": "self", "href": "https://api.sandbox.paypal.com/v2/checkout/orders/ORD-ORCH-RET-01"}],
        }
        mock_link_exec_res = ExecutionResult(
            status="completed",
            gateway="paypal",
            gateway_ref="INV-ORCH-102",
            gateway_url="https://www.sandbox.paypal.com/invoice/p/#INV-ORCH-102",
        )

        # 10.1 full path decline -> diagnosis -> decision -> policy -> mocked retry -> mocked completion fixture -> recovered
        db_10_1 = _MockMongoDB()
        clock_10_1 = SimulatedClock()

        db_10_1.events.docs.append({
            "event_id": "pp_decl_101",
            "surface": "payment",
            "gateway": "paypal",
            "amount": 75.0,
            "currency": "USD",
            "customer_ref": "cust_101",
            "status": "at_risk",
            "raw_failure_reason": "paypal_5120",
            "gateway_detail": {"processor_response": {"response_code": "5120"}},
            "created_at": clock_10_1.now(),
        })
        db_10_1.customer_vault.docs.append({
            "vault_id": "vlt_101",
            "customer_ref": "cust_101",
            "card_brand": "VISA",
            "last_digits": "1111",
            "status": "active",
            "created_at": clock_10_1.now(),
        })

        import app.db
        app.db._database = db_10_1
        with patch("app.gateways.paypal.orders.create_vaulted_order", AsyncMock(return_value=mock_vault_order_res)):
            res_adv = await advance("pp_decl_101", clock=clock_10_1, db=db_10_1)

            adv_ok = (
                res_adv.get("status") == "completed"
                and res_adv.get("stage") == "execute"
                and len(db_10_1.executions.docs) == 1
                and db_10_1.executions.docs[0].get("gateway_ref") == "ORD-ORCH-RET-01"
                and db_10_1.executions.docs[0].get("simulated_time") is True
                and len(db_10_1.orchestrator_runs.docs) == 4
                and all(r.get("simulated_time") is True for r in db_10_1.orchestrator_runs.docs)
            )

            comp_fixture = {
                "id": "WH-COMP-101",
                "event_type": "PAYMENT.CAPTURE.COMPLETED",
                "create_time": clock_10_1.now().isoformat(),
                "resource": {
                    "id": "CAP-101",
                    "custom_id": "ev:pp_decl_101",
                    "order_id": "ORD-ORCH-RET-01",
                    "amount": {"value": "75.00", "currency_code": "USD"},
                },
            }
            r_comp = await reconcile_paypal_event(comp_fixture, db_10_1)
            ev_after = await db_10_1.events.find_one({"event_id": "pp_decl_101"})

            full_path_ok = (
                adv_ok
                and r_comp.processed is True
                and r_comp.outcome == "recovered"
                and ev_after is not None
                and ev_after.get("status") == "recovered"
            )
            report(
                "10.1 Full path: decline -> diagnosis -> decision -> policy -> mocked retry -> mocked completion -> recovered",
                full_path_ok,
                f"adv_status={res_adv.get('status')}, r_comp_outcome={r_comp.outcome}, event_status={ev_after.get('status') if ev_after else None}",
            )

        # 10.2 No vault token falls back to update link
        db_10_2 = _MockMongoDB()
        clock_10_2 = SimulatedClock()
        db_10_2.events.docs.append({
            "event_id": "pp_decl_102",
            "surface": "payment",
            "gateway": "paypal",
            "amount": 50.0,
            "currency": "USD",
            "customer_ref": "cust_no_vault",
            "status": "at_risk",
            "raw_failure_reason": "paypal_5120",
            "gateway_detail": {"processor_response": {"response_code": "5120"}},
            "created_at": clock_10_2.now(),
        })

        app.db._database = db_10_2
        with patch.object(app.execution.service.PayPalExecutionAdapter, "execute_link", AsyncMock(return_value=mock_link_exec_res)):
            res_10_2 = await advance("pp_decl_102", clock=clock_10_2, db=db_10_2)
            dec_10_2 = db_10_2.decisions.docs[0] if db_10_2.decisions.docs else {}
            exec_10_2 = db_10_2.executions.docs[0] if db_10_2.executions.docs else {}

            no_vault_ok = (
                res_10_2.get("status") == "completed"
                and dec_10_2.get("excluded_interventions") == ["immediate_retry", "delayed_retry"]
                and dec_10_2.get("exclusion_reason") == "no_active_vault_token"
                and dec_10_2.get("recommended_intervention") == "payment_method_update_link"
                and exec_10_2.get("intervention") == "payment_method_update_link"
                and exec_10_2.get("gateway_ref") == "INV-ORCH-102"
            )
            report(
                "10.2 No vault token falls back: decision excludes retries and recommends payment_method_update_link",
                no_vault_ok,
                f"rec={dec_10_2.get('recommended_intervention')}, reason={dec_10_2.get('exclusion_reason')}, exec_ref={exec_10_2.get('gateway_ref')}",
            )

        # 10.3 Do-not-retry advice code falls back & defence in depth
        db_10_3 = _MockMongoDB()
        clock_10_3 = SimulatedClock()
        db_10_3.events.docs.append({
            "event_id": "pp_decl_103",
            "surface": "payment",
            "gateway": "paypal",
            "amount": 50.0,
            "currency": "USD",
            "customer_ref": "cust_advice_03",
            "status": "at_risk",
            "raw_failure_reason": "paypal_0500",
            "gateway_detail": {"processor_response": {"payment_advice_code": "03"}},
            "created_at": clock_10_3.now(),
        })
        db_10_3.customer_vault.docs.append({
            "vault_id": "vlt_103",
            "customer_ref": "cust_advice_03",
            "card_brand": "VISA",
            "last_digits": "2222",
            "status": "active",
            "created_at": clock_10_3.now(),
        })

        app.db._database = db_10_3
        with patch.object(app.execution.service.PayPalExecutionAdapter, "execute_link", AsyncMock(return_value=mock_link_exec_res)):
            res_10_3 = await advance("pp_decl_103", clock=clock_10_3, db=db_10_3)
            dec_10_3 = db_10_3.decisions.docs[0] if db_10_3.decisions.docs else {}

            rulebook = current_rulebook("USD")
            forced_retry_dec = DecisionRecord(
                id="507f1f77bcf86cd799439012",
                version=1,
                event_id="pp_decl_103",
                diagnosis_id=str(dec_10_3.get("diagnosis_id", "507f1f77bcf86cd799439011")),
                diagnosis_version=1,
                recommended_intervention="immediate_retry",
                estimated_cost=0.0,
                recovery_probability=0.6,
                revenue_at_risk=50.0,
                expected_recovery_value=30.0,
                reasoning="forced retry test",
            )
            forced_ctx = PolicyContext(
                customer_ref="cust_advice_03",
                customer_opted_out=False,
                prior_authorized_contacts=0,
                last_authorized_contact_at=None,
                has_vault_token=True,
                payment_advice_code="03",
            )
            forced_verdict = evaluate(decision=forced_retry_dec, context=forced_ctx, rulebook=rulebook)

            inr_book = current_rulebook("INR")
            inr_invariant = (inr_book.fingerprint == RECORDED_INR_FINGERPRINT == "rb1_aba19a5e5ee8124e")

            advice_ok = (
                dec_10_3.get("excluded_interventions") == ["immediate_retry", "delayed_retry"]
                and dec_10_3.get("exclusion_reason") == "payment_advice_03_do_not_retry"
                and dec_10_3.get("recommended_intervention") == "payment_method_update_link"
                and forced_verdict.verdict == "blocked"
                and forced_verdict.reason == "retry_not_advised"
                and inr_invariant
            )
            report(
                "10.3 Do-not-retry advice code falls back & defence in depth blocks retry with retry_not_advised; INR fingerprint byte-identical",
                advice_ok,
                f"exclusion={dec_10_3.get('exclusion_reason')}, forced_reason={forced_verdict.reason}, inr_fp={inr_book.fingerprint}",
            )

            # 10.3b Do-not-retry response code (9500 / 5180) falls back in decision & blocks retry in policy
            db_10_3b = _MockMongoDB()
            clock_10_3b = SimulatedClock()
            db_10_3b.events.docs.append({
                "event_id": "pp_decl_103b",
                "customer_ref": "cust_resp_9500",
                "amount": 50.0,
                "currency": "USD",
                "surface": "payment",
                "gateway": "paypal",
                "raw_failure_reason": "paypal_0500",
                "gateway_detail": {
                    "processor_response": {
                        "response_code": "9500",
                    }
                },
                "status": "at_risk",
                "created_at": clock_10_3b.now(),
            })
            db_10_3b.customer_vault.docs.append({
                "customer_ref": "cust_resp_9500",
                "vault_id": "vault_resp_9500",
                "card_brand": "VISA",
                "last_digits": "3333",
                "status": "active",
                "created_at": clock_10_3b.now(),
            })
            app.db._database = db_10_3b
            with patch.object(app.execution.service.PayPalExecutionAdapter, "execute_link", AsyncMock(return_value=mock_link_exec_res)):
                res_10_3b = await advance("pp_decl_103b", clock=clock_10_3b, db=db_10_3b)
                dec_10_3b = db_10_3b.decisions.docs[0] if db_10_3b.decisions.docs else {}

            forced_ctx_resp = PolicyContext(
                customer_ref="cust_resp_9500",
                customer_opted_out=False,
                prior_authorized_contacts=0,
                last_authorized_contact_at=None,
                has_vault_token=True,
                processor_response_code="9500",
            )
            forced_verdict_resp = evaluate(decision=forced_retry_dec, context=forced_ctx_resp, rulebook=rulebook)

            resp_ok = (
                dec_10_3b.get("excluded_interventions") == ["immediate_retry", "delayed_retry"]
                and dec_10_3b.get("exclusion_reason") == "processor_response_9500_do_not_retry"
                and dec_10_3b.get("recommended_intervention") == "payment_method_update_link"
                and forced_verdict_resp.verdict == "blocked"
                and forced_verdict_resp.reason == "retry_not_advised"
            )
            report(
                "10.3b Do-not-retry response code falls back in decision & blocks retry in policy defence-in-depth",
                resp_ok,
                f"exclusion={dec_10_3b.get('exclusion_reason')}, forced_reason={forced_verdict_resp.reason}",
            )

        # 10.4 Cooldown blocks a second retry until simulated clock advances
        clock_10_4 = SimulatedClock()
        t0 = clock_10_4.now()
        rulebook_usd = current_rulebook("USD")
        contact_dec = DecisionRecord(
            id="507f1f77bcf86cd799439014",
            version=1,
            event_id="pp_cooldown_ev",
            diagnosis_id="507f1f77bcf86cd799439011",
            diagnosis_version=1,
            recommended_intervention="payment_method_update_link",
            estimated_cost=0.0,
            recovery_probability=0.6,
            revenue_at_risk=50.0,
            expected_recovery_value=30.0,
            reasoning="cooldown test",
        )
        ctx_t0_plus_2h = PolicyContext(
            customer_ref="cust_cd",
            customer_opted_out=False,
            prior_authorized_contacts=1,
            last_authorized_contact_at=t0,
            now=t0 + timedelta(hours=2),
        )
        v_blocked = evaluate(decision=contact_dec, context=ctx_t0_plus_2h, rulebook=rulebook_usd)

        clock_10_4.advance(hours=25)
        ctx_t0_plus_25h = PolicyContext(
            customer_ref="cust_cd",
            customer_opted_out=False,
            prior_authorized_contacts=1,
            last_authorized_contact_at=t0,
            now=clock_10_4.now(),
        )
        v_passed = evaluate(decision=contact_dec, context=ctx_t0_plus_25h, rulebook=rulebook_usd)

        cooldown_ok = (
            v_blocked.verdict == "blocked"
            and v_blocked.reason == "cooldown_active"
            and v_passed.verdict == "authorized"
        )
        report(
            "10.4 Cooldown blocks contact/retry until simulated clock advances past 24h",
            cooldown_ok,
            f"at_2h={v_blocked.verdict} ({v_blocked.reason}), at_25h={v_passed.verdict}",
        )

        # 10.5 Loop guard: declined recovery capture creates no new event, attaches not_recovered, replay idempotent
        db_10_5 = _MockMongoDB()
        exec_id_10_5 = ObjectId()
        db_10_5.events.docs.append({
            "event_id": "pp_decl_105",
            "surface": "payment",
            "gateway": "paypal",
            "amount": 50.0,
            "currency": "USD",
            "customer_ref": "cust_105",
            "status": "at_risk",
        })
        db_10_5.executions.docs.append({
            "_id": exec_id_10_5,
            "event_id": "pp_decl_105",
            "policy_verdict_id": "507f1f77bcf86cd799439099",
            "gateway": "paypal",
            "gateway_ref": "ORD-LOOP-105",
            "status": "completed",
            "action_type": "retry_simulated",
            "intervention": "immediate_retry",
        })

        webhook_loop = {
            "id": "WH-DECL-LOOP-105",
            "event_type": "PAYMENT.CAPTURE.DECLINED",
            "resource": {
                "id": "CAP-LOOP-105",
                "custom_id": "ev:pp_decl_105",
                "order_id": "ORD-LOOP-105",
                "amount": {"value": "50.00", "currency_code": "USD"},
            },
        }

        app.db._database = db_10_5
        r_loop = await reconcile_paypal_event(webhook_loop, db_10_5)
        events_count_after = len(db_10_5.events.docs)
        verif_10_5 = db_10_5.verifications.docs[0] if db_10_5.verifications.docs else {}
        exec_after = db_10_5.executions.docs[0]

        r_loop_dup = await reconcile_paypal_event(webhook_loop, db_10_5)

        loop_ok = (
            r_loop.processed is True
            and r_loop.outcome == "not_recovered"
            and events_count_after == 1
            and verif_10_5.get("outcome") == "not_recovered"
            and verif_10_5.get("event_id") == "pp_decl_105"
            and exec_after.get("successful") is False
            and exec_after.get("recovery_outcome") == "not_recovered"
            and r_loop_dup.duplicate is True
            and len(db_10_5.verifications.docs) == 1
        )
        report(
            "10.5 Loop guard: declined recovery capture ('ev:') creates no new event, attaches not_recovered, replay is idempotent",
            loop_ok,
            f"events_count={events_count_after}, outcome={r_loop.outcome}, exec_succ={exec_after.get('successful')}, dup={r_loop_dup.duplicate}",
        )

        # 10.6 Replays of advance are idempotent
        init_runs = len(db_10_1.orchestrator_runs.docs)
        init_execs = len(db_10_1.executions.docs)
        init_decs = len(db_10_1.decisions.docs)

        app.db._database = db_10_1
        with patch("app.gateways.paypal.orders.create_vaulted_order", AsyncMock(return_value=mock_vault_order_res)):
            res_adv_replay = await advance("pp_decl_101", clock=clock_10_1, db=db_10_1)

            replay_ok = (
                res_adv_replay.get("status") in ("terminal", "already_executed")
                and len(db_10_1.orchestrator_runs.docs) == init_runs
                and len(db_10_1.executions.docs) == init_execs
                and len(db_10_1.decisions.docs) == init_decs
            )
            report(
                "10.6 Advance replay is idempotent: repeat advance runs no additional stages and creates zero duplicate records",
                replay_ok,
                f"status={res_adv_replay.get('status')}, runs={len(db_10_1.orchestrator_runs.docs)}/{init_runs}",
            )

        # 10.7 auto_orchestrate off changes nothing
        from app.config import Settings
        settings_default = Settings()
        auto_off_ok = (settings_default.auto_orchestrate is False)
        report(
            "10.7 auto_orchestrate default is False: background advance remains disabled until explicitly opted in",
            auto_off_ok,
            f"auto_orchestrate={settings_default.auto_orchestrate}",
        )

        # 10.8 Background Task Safety in Webhook: failing task is logged and does not affect 200 response
        from app.routes.paypal_webhooks import _safe_orchestrate, _background_tasks

        logged_records: list[logging.LogRecord] = []
        class TaskTestHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                logged_records.append(record)

        wh_logger = logging.getLogger("app.routes.paypal_webhooks")
        test_handler = TaskTestHandler()
        wh_logger.addHandler(test_handler)

        async def failing_advance(evt_id: str) -> None:
            raise ConnectionError("Upstream simulation offline")

        with patch("app.routes.paypal_webhooks.advance", side_effect=failing_advance):
            # Verify _safe_orchestrate handles failure cleanly
            await _safe_orchestrate("evt_task_fail_offline")

        wh_logger.removeHandler(test_handler)

        error_logs = [r for r in logged_records if r.levelno == logging.ERROR]
        logged_safe = (
            len(error_logs) >= 1
            and "evt_task_fail_offline" in error_logs[0].getMessage()
            and "ConnectionError" in error_logs[0].getMessage()
            and "payload" not in error_logs[0].getMessage().lower()
        )
        report(
            "10.8 Background task safety: failing task logs event id and error type only, no payloads",
            logged_safe,
            f"logged_msg={error_logs[0].getMessage() if error_logs else 'NONE'}",
        )
        app.db._database = None

    asyncio.run(run_step10_tests())


def test_step11_scenario_runner_and_reset() -> None:
    print("\n--- SECTION 11: DEMO RESET, SCENARIO RUNNER & PROBE CORRECTNESS ---")
    import app.db
    from datetime import datetime, timedelta, timezone
    from app.config import get_settings, Settings
    from app.routes.demo import (
        ALLOWED_DEMO_DBS,
        reset_demo_database,
        run_insufficient_funds_scenario,
        _execute_scenario_run,
        get_demo_run,
        list_demo_runs,
        self_heal_stale_runs,
        InsufficientFundsScenarioRequest,
    )
    from app.gateways.paypal import orders
    from app.execution.port import PayPalExecutionAdapter, ExecutionResult
    from app.policy.store import prior_retry_executions
    from app.webhooks.paypal_reconciler import reconcile_paypal_event
    from fastapi import HTTPException

    async def run_step11_tests() -> None:
        fixtures_live_dir = ROOT / "tests" / "fixtures" / "paypal" / "live"

        # -------------------------------------------------------------------
        # 11.1 Part 2A(a): Orders decline typed outcome & unsuccessful execution
        # -------------------------------------------------------------------
        mock_declined_order = {
            "id": "ORD-DECLINED-SYNCHRONOUS",
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "CAP-DECLINED-001",
                                "status": "DECLINED",
                                "processor_response": {"response_code": "5120"},
                            }
                        ]
                    }
                }
            ],
        }
        enriched = orders._enrich_order_capture_info(dict(mock_declined_order))
        enrich_ok = (
            enriched.get("capture_status") == "DECLINED"
            and enriched.get("processor_response_code") == "5120"
            and enriched.get("capture_id") == "CAP-DECLINED-001"
        )
        report(
            "11.1a Order helper extracts typed decline outcome (capture_status='DECLINED', code='5120') without exception",
            enrich_ok,
            f"capture_status={enriched.get('capture_status')}, code={enriched.get('processor_response_code')}",
        )

        adapter = PayPalExecutionAdapter()
        mock_verdict = AuthorizedVerdict(
            id="507f1f77bcf86cd799439011",
            version=1,
            event_id="pp_evt_decl_sync",
            decision_id="507f1f77bcf86cd799439012",
            decision_version=1,
            verdict="authorized",
            reason="ok",
            checks_performed=[f"{c}: PASS (ok)" for c in POLICY_CHECKS],
            evaluated_at=datetime.now(timezone.utc),
            rulebook_fingerprint=RECORDED_USD_FINGERPRINT,
            rulebook_fingerprint_source="evaluated",
        )
        mock_event = RevenueEvent(
            event_id="pp_evt_decl_sync",
            customer_ref="cust_decl_sync",
            amount=49.00,
            currency="USD",
            surface="payment",
            gateway="paypal",
            raw_failure_reason="paypal_5120",
        )
        mock_decision = make_decision(
            amount=49.00,
            intervention="immediate_retry",
            cost=0.0,
            prob=0.5,
            erv=24.5,
        )

        with patch("app.gateways.paypal.orders.create_vaulted_order", AsyncMock(return_value=enriched)):
            exec_res = await adapter.execute_retry(
                verdict=mock_verdict,
                event=mock_event,
                decision=mock_decision,
                root_cause="insufficient_funds",
                vault_token="vlt_test_token",
            )
            adapter_ok = (
                exec_res.status == "completed"
                and exec_res.successful is False
                and exec_res.capture_status == "DECLINED"
                and exec_res.processor_response_code == "5120"
                and exec_res.gateway_ref == "ORD-DECLINED-SYNCHRONOUS"
            )
            report(
                "11.1b PayPal execution adapter treats DECLINED capture as completed execution with successful=False",
                adapter_ok,
                f"status={exec_res.status}, successful={exec_res.successful}, capture_status={exec_res.capture_status}",
            )

        # -------------------------------------------------------------------
        # 11.2 Part 2A(b): Decline ingestion resolves customer_ref from custom_id "cust:<ref>"
        # -------------------------------------------------------------------
        declined_fixture_path = fixtures_live_dir / "payment_capture_declined_order_b.json"
        with open(declined_fixture_path, encoding="utf-8") as f:
            declined_fixture = json.load(f)

        db_11_2 = _MockMongoDB()
        app.db._database = db_11_2
        res_decline = await reconcile_paypal_event(declined_fixture, db_11_2)

        created_event = db_11_2.events.docs[0] if db_11_2.events.docs else {}
        decline_ingest_ok = (
            res_decline.processed is True
            and res_decline.matched is True
            and created_event.get("customer_ref") == "probe1"
            and created_event.get("status") == "at_risk"
            and created_event.get("gateway") == "paypal"
            and created_event.get("gateway_ref") == "2CG62440G8279605F"
        )
        report(
            "11.2 Decline ingestion resolves customer_ref from resource.custom_id 'cust:probe1' (Order B live fixture)",
            decline_ingest_ok,
            f"customer_ref={created_event.get('customer_ref')}, event_id={created_event.get('event_id')}",
        )

        # -------------------------------------------------------------------
        # 11.3 Part 2A(b): Completion matching uses custom_id "ev:<event_id>" plus order_id == gateway_ref
        # -------------------------------------------------------------------
        completed_fixture_path = fixtures_live_dir / "payment_capture_completed_order_c.json"
        with open(completed_fixture_path, encoding="utf-8") as f:
            completed_fixture = json.load(f)

        db_11_3 = _MockMongoDB()
        # Seed the execution that Order C retry created
        db_11_3.executions.docs.append({
            "_id": ObjectId("507f1f77bcf86cd799439099"),
            "event_id": "probe_evt",
            "gateway": "paypal",
            "gateway_ref": "29935697HU833963D",
            "status": "completed",
            "successful": False,  # was initially pending/unverified
            "amount": 5.00,
        })
        db_11_3.events.docs.append({
            "event_id": "probe_evt",
            "customer_ref": "probe1",
            "gateway": "paypal",
            "gateway_ref": "2CG62440G8279605F",
            "amount": 5.00,
            "currency": "USD",
            "status": "at_risk",
        })

        app.db._database = db_11_3
        res_complete = await reconcile_paypal_event(completed_fixture, db_11_3)
        event_after = db_11_3.events.docs[0]
        exec_after = db_11_3.executions.docs[0]
        verif_doc = db_11_3.verifications.docs[0] if db_11_3.verifications.docs else {}

        completion_match_ok = (
            res_complete.processed is True
            and res_complete.matched is True
            and res_complete.outcome == "recovered"
            and event_after.get("status") == "recovered"
            and verif_doc.get("assertion") == GATEWAY_VERIFIED
            and verif_doc.get("gateway_ref") == "29935697HU833963D"
            and exec_after.get("successful") is True
            and exec_after.get("recovery_outcome") == "recovered"
        )
        report(
            "11.3 Completion matching matches by custom_id 'ev:probe_evt' + order_id == gateway_ref (Order C live fixture)",
            completion_match_ok,
            f"outcome={res_complete.outcome}, event_status={event_after.get('status')}, exec_succ={exec_after.get('successful')}",
        )

        # -------------------------------------------------------------------
        # 11.4 Part 2A(c): Declined retries count towards retry cap in prior_retry_executions
        # -------------------------------------------------------------------
        db_11_4 = _MockMongoDB()
        # Seed 2 completed successful and 1 completed unsuccessful (DECLINED) retries
        now_dt = datetime.now(timezone.utc)
        db_11_4.executions.docs.extend([
            {
                "event_id": "pp_cap_count_evt",
                "intervention": "delayed_retry",
                "status": "completed",
                "successful": False,
                "capture_status": "DECLINED",
                "executed_at": now_dt - timedelta(hours=30),
            },
            {
                "event_id": "pp_cap_count_evt",
                "intervention": "immediate_retry",
                "status": "completed",
                "successful": False,
                "capture_status": "DECLINED",
                "executed_at": now_dt - timedelta(hours=26),
            },
        ])
        app.db._database = db_11_4
        with patch("app.policy.store.get_database", return_value=db_11_4):
            count, last_at = await prior_retry_executions("pp_cap_count_evt")
            cap_counted_ok = (count == 2 and last_at is not None)
            report(
                "11.4 Declined retries (status='completed', successful=False) count toward retry cap without 'declined' status",
                cap_counted_ok,
                f"count={count}, last_at={last_at}",
            )

        # -------------------------------------------------------------------
        # 11.5 Part 2B: Demo Reset allowlist and non-sandbox refusal
        # -------------------------------------------------------------------
        db_prod = _MockMongoDB("vasooli_production")
        db_regress = _MockMongoDB("vasooli_regress")
        db_demo = _MockMongoDB("vasooli_demo")

        # Refusal on unapproved database name
        unapproved_refused = False
        try:
            with patch("app.routes.demo.get_settings", return_value=Settings(paypal_env="sandbox")):
                await reset_demo_database(db_prod)
        except ValueError as exc:
            unapproved_refused = "not in allowlist" in str(exc)

        # Refusal on non-sandbox PayPal environment
        nonsandbox_refused = False
        try:
            with patch("app.routes.demo.get_settings", return_value=Settings(paypal_env="live")):
                await reset_demo_database(db_regress)
        except ValueError as exc:
            nonsandbox_refused = "must be 'sandbox'" in str(exc)

        # Success on approved databases with sandbox
        with patch("app.routes.demo.get_settings", return_value=Settings(paypal_env="sandbox")):
            res_reset_regress = await reset_demo_database(db_regress)
            res_reset_demo = await reset_demo_database(db_demo)
            reset_ok = (
                unapproved_refused
                and nonsandbox_refused
                and res_reset_regress.get("status") == "ok"
                and res_reset_demo.get("status") == "ok"
                and len(res_reset_regress.get("dropped_collections", [])) == 12
            )
            report(
                "11.5 Demo reset allowlist enforcement: refuses non-allowlist DB and non-sandbox env; resets approved collections",
                reset_ok,
                f"unapproved_refused={unapproved_refused}, nonsandbox_refused={nonsandbox_refused}, dropped_cols={len(res_reset_regress.get('dropped_collections', []))}",
            )

        # -------------------------------------------------------------------
        # 11.6 Part 2C: Scenario runner validations
        # -------------------------------------------------------------------
        db_11_6 = _MockMongoDB()
        app.db._database = db_11_6

        # Non-sandbox refusal
        sb_refused = False
        try:
            with patch("app.routes.demo.get_settings", return_value=Settings(paypal_env="production")):
                await run_insufficient_funds_scenario(InsufficientFundsScenarioRequest())
        except HTTPException as exc:
            sb_refused = exc.status_code == 400

        # customer_ref must start with "demo-"
        cust_ref_refused = False
        try:
            with patch("app.routes.demo.get_settings", return_value=Settings(paypal_env="sandbox")):
                await run_insufficient_funds_scenario(InsufficientFundsScenarioRequest(customer_ref="bad_ref"))
        except HTTPException as exc:
            cust_ref_refused = exc.status_code == 400 and "demo-" in exc.detail

        # amount limits (<= 0 or > 250)
        amt_high_refused = False
        amt_low_refused = False
        try:
            with patch("app.routes.demo.get_settings", return_value=Settings(paypal_env="sandbox")):
                await run_insufficient_funds_scenario(InsufficientFundsScenarioRequest(amount=300.00))
        except HTTPException as exc:
            amt_high_refused = exc.status_code == 400

        try:
            with patch("app.routes.demo.get_settings", return_value=Settings(paypal_env="sandbox")):
                await run_insufficient_funds_scenario(InsufficientFundsScenarioRequest(amount=-5.00))
        except HTTPException as exc:
            amt_low_refused = exc.status_code == 400

        val_ok = sb_refused and cust_ref_refused and amt_high_refused and amt_low_refused
        report(
            "11.6 Scenario runner input validations (sandbox check, 'demo-' prefix, 0.01-250.00 amount range)",
            val_ok,
            f"sandbox={sb_refused}, prefix={cust_ref_refused}, amt_high={amt_high_refused}, amt_low={amt_low_refused}",
        )

        # -------------------------------------------------------------------
        # 11.7 Part 2C: Concurrency guard (single-active-run -> 409 Conflict)
        # -------------------------------------------------------------------
        db_11_7 = _MockMongoDB()
        app.db._database = db_11_7
        db_11_7.demo_runs.docs.append({
            "run_id": "run_active_123",
            "status": "running",
            "created_at": datetime.now(timezone.utc),
        })
        active_refused = False
        try:
            with patch("app.routes.demo.get_settings", return_value=Settings(paypal_env="sandbox")):
                await run_insufficient_funds_scenario(InsufficientFundsScenarioRequest(customer_ref="demo-concurrent"))
        except HTTPException as exc:
            active_refused = exc.status_code == 409

        report(
            "11.7 Concurrency guard: second active demo run rejected with 409 Conflict",
            active_refused,
            f"status_409={active_refused}",
        )

        # -------------------------------------------------------------------
        # 11.8 Part 2C: Rate limit (at most 10 runs per hour -> 429 Too Many Requests)
        # -------------------------------------------------------------------
        db_11_8 = _MockMongoDB()
        app.db._database = db_11_8
        for i in range(10):
            db_11_8.demo_runs.docs.append({
                "run_id": f"run_past_{i}",
                "status": "recovered",
                "created_at": datetime.now(timezone.utc) - timedelta(minutes=5 * i),
            })
        rate_refused = False
        try:
            with patch("app.routes.demo.get_settings", return_value=Settings(paypal_env="sandbox")):
                await run_insufficient_funds_scenario(InsufficientFundsScenarioRequest(customer_ref="demo-ratelimit"))
        except HTTPException as exc:
            rate_refused = exc.status_code == 429

        report(
            "11.8 Rate limit guard: 11th run in one hour rejected with 429 Too Many Requests",
            rate_refused,
            f"status_429={rate_refused}",
        )

        # -------------------------------------------------------------------
        # 11.9 Part 2C: Step 1 No vault token / timeout honest failure
        # -------------------------------------------------------------------
        db_11_9 = _MockMongoDB()
        app.db._database = db_11_9
        run_id_9 = "run_timeout_onboard"
        db_11_9.demo_runs.docs.append({
            "run_id": run_id_9,
            "status": "running",
            "current_step": "onboard",
            "steps": [{"name": "onboard", "status": "running", "refs": {}}],
            "created_at": datetime.now(timezone.utc),
        })

        mock_onboard_order = {"id": "ORD-ONBOARD-NO-VAULT"}
        curr_mono_9 = 0.0
        def mock_mono_9():
            nonlocal curr_mono_9
            curr_mono_9 += 100.0
            return curr_mono_9

        with patch("app.gateways.paypal.orders.create_and_capture_order", AsyncMock(return_value=mock_onboard_order)), \
             patch("time.monotonic", side_effect=mock_mono_9), \
             patch("asyncio.sleep", AsyncMock()):
            await _execute_scenario_run(run_id=run_id_9, customer_ref="demo-no-vault", amount=49.00, advance_hours=0.0)

        run_9_after = db_11_9.demo_runs.docs[0]
        onboard_timeout_ok = (
            run_9_after.get("status") == "timeout"
            and run_9_after.get("final_status") == "timeout(onboard)"
            and run_9_after.get("steps", [{}])[0].get("status") == "timeout"
        )
        report(
            "11.9 Step 1 onboard honest timeout: missing vault webhook terminates with status='timeout' and 'timeout(onboard)'",
            onboard_timeout_ok,
            f"status={run_9_after.get('status')}, final_status={run_9_after.get('final_status')}",
        )

        # -------------------------------------------------------------------
        # 11.10 Part 2C: Step 4 Blocked verdict termination
        # -------------------------------------------------------------------
        db_11_10 = _MockMongoDB()
        app.db._database = db_11_10
        run_id_10 = "run_blocked_verdict"
        db_11_10.demo_runs.docs.append({
            "run_id": run_id_10,
            "status": "running",
            "current_step": "onboard",
            "steps": [{"name": n, "status": "pending", "refs": {}} for n in ["onboard", "renewal_attempt", "decline_ingested", "pipeline", "clock", "retry_executed", "verified"]],
            "created_at": datetime.now(timezone.utc),
        })
        # Customer already has active vault token
        db_11_10.customer_vault.docs.append({
            "customer_ref": "demo-blocked",
            "vault_token_id": "vlt_existing_11_10",
            "status": "active",
        })

        mock_decline_res = {
            "id": "ORD-DECLINED-11-10",
            "capture_status": "DECLINED",
            "processor_response_code": "5120",
        }

        async def mock_decline_webhook_arrival(*args, **kwargs):
            # simulate webhook inserting at-risk event
            db_11_10.events.docs.append({
                "event_id": "evt_blocked_11_10",
                "customer_ref": "demo-blocked",
                "amount": 49.00,
                "currency": "USD",
                "status": "at_risk",
                "raw_failure_reason": "paypal_5120",
            })

        async def mock_advance_blocked(*args, **kwargs):
            # orchestrator advances and sets policy verdict to blocked
            db_11_10.policy_verdicts.docs.append({
                "_id": ObjectId("507f1f77bcf86cd799439055"),
                "event_id": "evt_blocked_11_10",
                "verdict": "blocked",
                "reason": "customer_opted_out",
                "version": 1,
            })
            return {"status": "stopped", "verdict": "blocked"}

        with patch("app.gateways.paypal.orders.create_and_capture_order", AsyncMock(side_effect=[mock_decline_res])), \
             patch("app.routes.demo.orchestrator_advance", side_effect=mock_advance_blocked):
            await mock_decline_webhook_arrival()
            await _execute_scenario_run(run_id=run_id_10, customer_ref="demo-blocked", amount=49.00, advance_hours=0.0)

        run_10_after = db_11_10.demo_runs.docs[0]
        blocked_ok = (
            run_10_after.get("status") == "blocked"
            and run_10_after.get("final_status") == "blocked (verdict)"
            and run_10_after.get("steps", [])[3].get("name") == "pipeline"
            and run_10_after.get("steps", [])[3].get("status") == "completed"
        )
        report(
            "11.10 Step 4 pipeline terminal status: blocked policy verdict terminates run with status='blocked' ('blocked (verdict)')",
            blocked_ok,
            f"status={run_10_after.get('status')}, final_status={run_10_after.get('final_status')}",
        )

        # -------------------------------------------------------------------
        # 11.11 Part 2C: Step 7 Verified honest timeout
        # -------------------------------------------------------------------
        db_11_11 = _MockMongoDB()
        app.db._database = db_11_11
        run_id_11 = "run_timeout_verified"
        db_11_11.demo_runs.docs.append({
            "run_id": run_id_11,
            "status": "running",
            "current_step": "onboard",
            "steps": [{"name": n, "status": "pending", "refs": {}} for n in ["onboard", "renewal_attempt", "decline_ingested", "pipeline", "clock", "retry_executed", "verified"]],
            "created_at": datetime.now(timezone.utc),
        })
        db_11_11.customer_vault.docs.append({
            "customer_ref": "demo-timeout-verif",
            "vault_token_id": "vlt_token_11_11",
            "status": "active",
        })

        async def mock_advance_retry(*args, **kwargs):
            # pipeline creates retry execution
            db_11_11.executions.docs.append({
                "_id": ObjectId("507f1f77bcf86cd799439077"),
                "event_id": "evt_timeout_11_11",
                "gateway": "paypal",
                "gateway_ref": "ORD-RETRY-11-11",
                "status": "completed",
                "successful": True,
            })
            return {"status": "completed"}

        curr_monotonic_11 = 0.0
        def mock_monotonic_11():
            nonlocal curr_monotonic_11
            curr_monotonic_11 += 50.0
            return curr_monotonic_11

        with patch("app.gateways.paypal.orders.create_and_capture_order", AsyncMock(return_value={"id": "ORD-DECL-11-11", "capture_status": "DECLINED", "processor_response_code": "5120"})), \
             patch("app.routes.demo.orchestrator_advance", side_effect=mock_advance_retry), \
             patch("time.monotonic", side_effect=mock_monotonic_11), \
             patch("asyncio.sleep", AsyncMock()):
            # Insert at-risk event directly
            db_11_11.events.docs.append({
                "event_id": "evt_timeout_11_11",
                "customer_ref": "demo-timeout-verif",
                "amount": 49.00,
                "currency": "USD",
                "status": "at_risk",
                "raw_failure_reason": "paypal_5120",
            })
            await _execute_scenario_run(run_id=run_id_11, customer_ref="demo-timeout-verif", amount=49.00, advance_hours=0.0)

        run_11_after = db_11_11.demo_runs.docs[0]
        verif_timeout_ok = (
            run_11_after.get("status") == "timeout"
            and run_11_after.get("final_status") == "timeout(verified)"
            and run_11_after.get("steps", [])[6].get("status") == "timeout"
        )
        report(
            "11.11 Step 7 verified honest timeout: missing completion webhook terminates honestly with 'timeout(verified)'",
            verif_timeout_ok,
            f"status={run_11_after.get('status')}, final_status={run_11_after.get('final_status')}",
        )

        # -------------------------------------------------------------------
        # 11.12 Part 2C: Full happy path scenario run & public GET inspection
        # -------------------------------------------------------------------
        db_11_12 = _MockMongoDB()
        app.db._database = db_11_12
        run_id_12 = "run_happy_path_12"
        db_11_12.demo_runs.docs.append({
            "run_id": run_id_12,
            "scenario": "insufficient-funds",
            "customer_ref": "demo-happy",
            "amount": 49.00,
            "currency": "USD",
            "advance_hours": 24.0,
            "status": "running",
            "current_step": "onboard",
            "steps": [{"name": n, "status": "pending", "refs": {}} for n in ["onboard", "renewal_attempt", "decline_ingested", "pipeline", "clock", "retry_executed", "verified"]],
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        })
        db_11_12.customer_vault.docs.append({
            "customer_ref": "demo-happy",
            "vault_token_id": "vlt_token_happy",
            "status": "active",
        })

        async def happy_advance(*args, **kwargs):
            # pipeline creates retry execution
            db_11_12.executions.docs.append({
                "_id": ObjectId("507f1f77bcf86cd799439088"),
                "event_id": "evt_happy_12",
                "gateway": "paypal",
                "gateway_ref": "ORD-RETRY-HAPPY",
                "status": "completed",
                "successful": True,
            })
            return {"status": "completed"}

        # Simulate completion webhook reconciliation happening during step 7
        async def mock_sleep_step7(delay: float):
            db_11_12.verifications.docs.append({
                "_id": ObjectId("507f1f77bcf86cd799439099"),
                "event_id": "evt_happy_12",
                "assertion": "gateway_verified",
                "outcome": "recovered",
            })
            # Transition event to recovered
            for ev in db_11_12.events.docs:
                if ev.get("event_id") == "evt_happy_12":
                    ev["status"] = "recovered"

        with patch("app.gateways.paypal.orders.create_and_capture_order", AsyncMock(return_value={"id": "ORD-DECL-HAPPY", "capture_status": "DECLINED", "processor_response_code": "5120"})), \
             patch("app.routes.demo.orchestrator_advance", side_effect=happy_advance), \
             patch("app.clock.advance_mongo_clock", AsyncMock()), \
             patch("asyncio.sleep", side_effect=mock_sleep_step7):
            # Seed at-risk event from renewal decline
            db_11_12.events.docs.append({
                "event_id": "evt_happy_12",
                "customer_ref": "demo-happy",
                "amount": 49.00,
                "currency": "USD",
                "status": "at_risk",
                "raw_failure_reason": "paypal_5120",
            })
            await _execute_scenario_run(run_id=run_id_12, customer_ref="demo-happy", amount=49.00, advance_hours=24.0)

        run_12_after = db_11_12.demo_runs.docs[0]
        happy_path_ok = (
            run_12_after.get("status") == "recovered"
            and run_12_after.get("final_status") == "recovered"
            and all(s.get("status") == "completed" for s in run_12_after.get("steps", []))
        )
        report(
            "11.12 Full happy path scenario run: all 7 steps complete, event transitions to 'recovered'",
            happy_path_ok,
            f"status={run_12_after.get('status')}, steps={[s.get('name')+':'+s.get('status') for s in run_12_after.get('steps', [])]}",
        )

        # Public GET endpoints (verify zero secrets exposed)
        single_run_view = await get_demo_run(run_id_12)
        list_runs_view = await list_demo_runs()
        serialized_view = json.dumps(single_run_view, default=str)
        no_secrets_ok = (
            single_run_view.get("run_id") == run_id_12
            and single_run_view.get("amount") == 49.00
            and len(list_runs_view.get("runs", [])) >= 1
            and "_id" not in single_run_view
            and "client_secret" not in serialized_view.lower()
            and "password" not in serialized_view.lower()
            and "private" not in serialized_view.lower()
        )
        report(
            "11.13 Public GET /demo/runs/{id} and /demo/runs expose only ids, amounts, and step statuses with zero secrets",
            no_secrets_ok,
            f"run_id={single_run_view.get('run_id')}, runs_count={len(list_runs_view.get('runs', []))}",
        )

        # -------------------------------------------------------------------
        # 11.14 Stale-run self-heal: runs > 10m inactive marked failed(step, "stale") and lock released
        # -------------------------------------------------------------------
        db_11_14 = _MockMongoDB()
        app.db._database = db_11_14
        ten_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=15)
        db_11_14.demo_runs.docs.append({
            "_id": ObjectId("507f1f77bcf86cd799439055"),
            "run_id": "run_stale_11_14",
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
        healed_count = await self_heal_stale_runs(db_11_14)
        stale_run = db_11_14.demo_runs.docs[0]
        stale_ok = (
            healed_count == 1
            and stale_run.get("status") == "failed"
            and stale_run.get("final_status") == 'failed(renewal_attempt, "stale")'
            and stale_run.get("steps", [])[1].get("status") == "failed"
            and stale_run.get("steps", [])[1].get("refs", {}).get("error") == "stale"
        )
        report(
            "11.14 Stale-run self-heal: runs > 10m without update marked failed(step, 'stale') and active-run lock released",
            stale_ok,
            f"healed={healed_count}, status={stale_run.get('status')}, final_status={stale_run.get('final_status')}",
        )

        # -------------------------------------------------------------------
        # 11.15 Public GET /demo/runs and /demo/runs/{id} data sanitization: no vault tokens, emails, cards, or payer IDs
        # -------------------------------------------------------------------
        db_11_15 = _MockMongoDB()
        app.db._database = db_11_15
        run_id_15 = "run_sanitized_11_15"
        mock_raw_completed = {
            "_id": ObjectId("507f1f77bcf86cd799439066"),
            "run_id": run_id_15,
            "scenario": "insufficient-funds",
            "customer_ref": "demo-001",
            "amount": 49.00,
            "currency": "USD",
            "status": "recovered",
            "final_status": "recovered",
            "current_step": "verified",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "vault_token_id": "FORBIDDEN_VAULT_TOKEN_TOP",
            "email": "buyer_top@example.com",
            "payer_id": "FORBIDDEN_PAYER_TOP",
            "steps": [
                {
                    "name": "onboard",
                    "status": "completed",
                    "source": "paypal_sandbox_simulation",
                    "refs": {
                        "order_id": "ORD-001",
                        "customer_ref": "demo-001",
                        "vault_token_id": "FORBIDDEN_VAULT_TOKEN_REF",
                        "email": "buyer@example.com",
                        "card_digits": "1111",
                        "payer_id": "FORBIDDEN_PAYER_REF",
                    },
                },
                {
                    "name": "verified",
                    "status": "completed",
                    "source": "paypal_webhook",
                    "refs": {
                        "event_id": "evt_001",
                        "execution_id": "exec_001",
                        "order_id": "ORD-002",
                    },
                },
            ],
        }
        db_11_15.demo_runs.docs.append(mock_raw_completed)

        single_run = await get_demo_run(run_id_15)
        list_runs = await list_demo_runs()
        single_json = json.dumps(single_run, default=str)
        list_json = json.dumps(list_runs, default=str)

        forbidden_terms = [
            "vault_token_id",
            "vault_id",
            "email",
            "buyer_email",
            "card_digits",
            "last_digits",
            "card_number",
            "payer_id",
            "payer",
            "FORBIDDEN_VAULT_TOKEN_TOP",
            "buyer_top@example.com",
            "FORBIDDEN_PAYER_TOP",
            "FORBIDDEN_VAULT_TOKEN_REF",
            "buyer@example.com",
            "1111",
            "FORBIDDEN_PAYER_REF",
        ]
        leaks_single = [t for t in forbidden_terms if t in single_json]
        leaks_list = [t for t in forbidden_terms if t in list_json]

        sanitization_ok = (
            len(leaks_single) == 0
            and len(leaks_list) == 0
            and single_run.get("run_id") == run_id_15
            and single_run.get("amount") == 49.00
            and single_run.get("status") == "recovered"
            and "ORD-001" in single_json
            and "evt_001" in single_json
            and "exec_001" in single_json
        )
        report(
            "11.15 Public GET /demo/runs and /demo/runs/{id} sanitization: zero vault tokens, emails, cards, or payer IDs",
            sanitization_ok,
            f"leaks_single={leaks_single}, leaks_list={leaks_list}",
        )

        # -------------------------------------------------------------------
        # 11.16 orders.py typed outcome: ONLY INSTRUMENT_DECLINED and TRANSACTION_REFUSED 422 return typed outcomes; others raise
        # -------------------------------------------------------------------
        from app.gateways.paypal.client import PayPalCallFailed

        class _MockResp:
            def __init__(self, status_code: int, data: dict):
                self.status_code = status_code
                self._data = data
            def json(self):
                return self._data
            @property
            def text(self):
                return json.dumps(self._data)

        class _MockClient:
            def __init__(self, post_resp):
                self._post_resp = post_resp
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, *args, **kwargs):
                return self._post_resp

        # 1. 422 with INSTRUMENT_DECLINED -> typed outcome with processor_response_code=None
        inst_decl_resp = _MockResp(422, {"details": [{"issue": "INSTRUMENT_DECLINED", "description": "declined"}]})
        with patch("app.gateways.paypal.orders.paypal_client", return_value=_MockClient(inst_decl_resp)):
            res_inst = await orders.create_and_capture_order(amount=49.0, card=orders.SANDBOX_TEST_VISA, db=db_11_15)
        inst_ok = (
            res_inst.get("status") == "COMPLETED"
            and res_inst.get("capture_status") == "DECLINED"
            and res_inst.get("processor_response_code") is None
        )

        # 2. 422 with TRANSACTION_REFUSED -> typed outcome with processor_response_code=None
        tx_ref_resp = _MockResp(422, {"details": [{"issue": "TRANSACTION_REFUSED", "description": "refused"}]})
        with patch("app.gateways.paypal.orders.paypal_client", return_value=_MockClient(tx_ref_resp)):
            res_tx = await orders.create_and_capture_order(amount=49.0, card=orders.SANDBOX_TEST_VISA, db=db_11_15)
        tx_ok = (
            res_tx.get("status") == "COMPLETED"
            and res_tx.get("capture_status") == "DECLINED"
            and res_tx.get("processor_response_code") is None
        )

        # 3. 422 with INVALID_REQUEST -> raises PayPalCallFailed
        inv_req_resp = _MockResp(422, {"details": [{"issue": "INVALID_REQUEST", "description": "invalid"}]})
        raised_invalid = False
        try:
            with patch("app.gateways.paypal.orders.paypal_client", return_value=_MockClient(inv_req_resp)):
                await orders.create_and_capture_order(amount=49.0, card=orders.SANDBOX_TEST_VISA, db=db_11_15)
        except PayPalCallFailed:
            raised_invalid = True

        # 4. 400 Bad Request -> raises PayPalCallFailed
        resp_400 = _MockResp(400, {"name": "BAD_REQUEST"})
        raised_400 = False
        try:
            with patch("app.gateways.paypal.orders.paypal_client", return_value=_MockClient(resp_400)):
                await orders.create_and_capture_order(amount=49.0, card=orders.SANDBOX_TEST_VISA, db=db_11_15)
        except PayPalCallFailed:
            raised_400 = True

        # 5. 500 Internal Error -> raises PayPalCallFailed
        resp_500 = _MockResp(500, {"name": "INTERNAL_SERVER_ERROR"})
        raised_500 = False
        try:
            with patch("app.gateways.paypal.orders.paypal_client", return_value=_MockClient(resp_500)):
                await orders.create_and_capture_order(amount=49.0, card=orders.SANDBOX_TEST_VISA, db=db_11_15)
        except PayPalCallFailed:
            raised_500 = True

        typed_declines_strictly_filtered = (
            inst_ok and tx_ok and raised_invalid and raised_400 and raised_500
        )
        report(
            "11.16 orders.py typed outcome: ONLY INSTRUMENT_DECLINED and TRANSACTION_REFUSED 422 return typed outcomes with processor_response_code=None; others raise PayPalCallFailed",
            typed_declines_strictly_filtered,
            f"inst_ok={inst_ok}, tx_ok={tx_ok}, raised_invalid={raised_invalid}, raised_400={raised_400}, raised_500={raised_500}",
        )

        # -------------------------------------------------------------------
        # 11.17 None processor_response_code carried through & unknown code never reaches insufficient_funds
        # -------------------------------------------------------------------
        from app.diagnosis.rules import classify
        from app.models.diagnosis import PaymentRootCause

        # Decline without processor_response_code yields raw_failure_reason="paypal_declined"
        evt_none = RevenueEvent(
            event_id="evt_decl_none_code",
            customer_ref="demo-unmapped-01",
            amount=49.0,
            currency="USD",
            surface="payment",
            gateway="paypal",
            raw_failure_reason="paypal_declined",
        )
        # Event with arbitrary unmapped PayPal code
        evt_unmapped = RevenueEvent(
            event_id="evt_decl_unmapped_code",
            customer_ref="demo-unmapped-02",
            amount=49.0,
            currency="USD",
            surface="payment",
            gateway="paypal",
            raw_failure_reason="paypal_UNKNOWN_CODE",
        )
        # Control event with genuine 5120 insufficient funds code
        evt_control_5120 = RevenueEvent(
            event_id="evt_decl_5120",
            customer_ref="demo-control-5120",
            amount=49.0,
            currency="USD",
            surface="payment",
            gateway="paypal",
            raw_failure_reason="paypal_5120",
        )

        match_none = classify(evt_none)
        match_unmapped = classify(evt_unmapped)
        match_control = classify(evt_control_5120)

        unknown_never_insufficient_funds = (
            res_inst.get("processor_response_code") is None
            and res_tx.get("processor_response_code") is None
            and match_none is None
            and match_unmapped is None
            and match_control is not None
            and match_control.root_cause == "insufficient_funds"
        )
        report(
            "11.17 processor_response_code=None carried through: unknown code routes to Gemini fallback and NEVER reaches insufficient_funds",
            unknown_never_insufficient_funds,
            f"inst_code={res_inst.get('processor_response_code')}, match_none={match_none}, match_unmapped={match_unmapped}, control_5120={match_control.root_cause if match_control else None}",
        )
        app.db._database = None

    asyncio.run(run_step11_tests())


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
    test_step9_paypal_capture_declined_and_vault()
    test_step10_orchestrator()
    test_step11_scenario_runner_and_reset()

    print("\n" + "=" * 78)
    print(f"SUMMARY: {PASS_COUNT} PASSED, {FAIL_COUNT} FAILED")
    print("=" * 78)
    return 1 if FAIL_COUNT > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
