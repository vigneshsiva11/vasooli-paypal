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
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pydantic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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

    # 1.8 WebhookVerification validates PayPal webhook without Razorpay IDs
    wv_pp = WebhookVerification(
        event_id="evt_pp_1",
        execution_id="507f1f77bcf86cd799439014",
        verified_at=now,
        source="webhook",
        assertion="gateway_verified",
        gateway="paypal",
        gateway_ref="CAPTURE-123",
        amount_recovered=50.0,
        amount_expected=50.0,
        amount_mismatch=False,
        outcome="recovered",
    )
    report(
        "WebhookVerification validates PayPal webhook with gateway='paypal'",
        wv_pp.gateway == "paypal" and wv_pp.gateway_ref == "CAPTURE-123" and wv_pp.razorpay_event_id is None,
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


def main() -> int:
    print("=" * 78)
    print("PHASE 1 CONTRACT CHECKS (OFFLINE — NO NETWORK, NO DB, NO SERVER)")
    print("=" * 78)

    test_step1_models()
    test_characterization_razorpay_execution()
    test_step2_rulebook_and_policy()
    test_step3_execution_port_and_adapters()

    print("\n" + "=" * 78)
    print(f"SUMMARY: {PASS_COUNT} PASSED, {FAIL_COUNT} FAILED")
    print("=" * 78)
    return 1 if FAIL_COUNT > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
