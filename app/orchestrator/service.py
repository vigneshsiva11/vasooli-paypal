"""Orchestrator service: advances an event through recovery stages idempotently."""

from __future__ import annotations

from typing import Any
import logging
from bson import ObjectId
from pymongo import DESCENDING

from app.clock import Clock, get_clock
from app.db import get_database
from app.execution.service import execute as execute_service
from app.gateways.paypal.vault import get_active_vault_token
from app.models import DecisionRecord, DiagnosisRecord, RevenueEvent
from app.models.execution import require_authorized
from app.models.policy import PolicyVerdict, PolicyVerdictRecord
from app.orchestrator.runs import ensure_orchestrator_indexes, record_run
from app.policy.engine import evaluate
from app.policy.rules import (
    DO_NOT_RETRY_ADVICE_CODES,
    DO_NOT_RETRY_RESPONSE_CODES,
    current_rulebook,
)
from app.policy.store import gather_context, append as append_verdict

logger = logging.getLogger(__name__)


async def advance(
    event_id: str,
    *,
    clock: Clock | None = None,
    allow_non_paypal: bool = False,
    db: Any = None,
) -> dict[str, Any]:
    """Advance an event through recovery stages idempotently and resumably.

    Stages: diagnose -> decide -> policy -> execute.
    Stops at terminal states or when manual review/blocked.

    Args:
        event_id: The revenue event ID.
        clock: Optional Clock (defaults to MongoClock loaded from database).
        allow_non_paypal: If False, ignores events with gateway != 'paypal'.
        db: Optional database handle.

    Returns:
        Summary dict of orchestrator execution.
    """
    database = db if db is not None else get_database()
    if clock is None:
        clock = await get_clock(database)

    await ensure_orchestrator_indexes(database)

    event_doc = await database["events"].find_one({"event_id": event_id})
    if not event_doc:
        logger.warning("Orchestrator advance: event %s not found", event_id)
        return {"event_id": event_id, "status": "not_found", "stage": None}

    gateway = event_doc.get("gateway", "razorpay")
    if gateway != "paypal" and not allow_non_paypal:
        logger.info("Orchestrator advance: skipping non-paypal event %s (gateway=%s)", event_id, gateway)
        return {
            "event_id": event_id,
            "status": "skipped",
            "reason": "gateway_not_paypal",
            "gateway": gateway,
        }

    event_status = event_doc.get("status")
    if event_status != "at_risk":
        logger.info("Orchestrator advance: event %s is in terminal status %s", event_id, event_status)
        return {
            "event_id": event_id,
            "status": "terminal",
            "event_status": event_status,
        }

    event_dict = {k: v for k, v in event_doc.items() if k != "_id"}
    event_obj = RevenueEvent.model_validate(event_dict)

    # -----------------------------------------------------------------------
    # Stage 1: Diagnosis
    # -----------------------------------------------------------------------
    diag_doc = await database["diagnoses"].find_one(
        {"event_id": event_id}, sort=[("version", DESCENDING)]
    )
    if diag_doc is None:
        from app.diagnosis.service import diagnose as diagnose_service
        from app.diagnosis.store import append as append_diagnosis
        diag_model, method, llm_model = await diagnose_service(event_obj)
        diag_doc_id, diag_version = await append_diagnosis(diag_model, method=method, llm_model=llm_model)
        diag_doc = await database["diagnoses"].find_one({"_id": ObjectId(diag_doc_id)})
        diag_record = DiagnosisRecord.from_document(diag_doc)
        await record_run(
            event_id=event_id,
            stage="diagnose",
            record_version=diag_version,
            outcome=diag_record.root_cause,
            clock=clock,
            db=database,
        )
    else:
        diag_record = DiagnosisRecord.from_document(diag_doc)

    if not diag_record.recoverable:
        logger.info("Orchestrator advance: diagnosis unrecoverable for event %s", event_id)
        return {
            "event_id": event_id,
            "status": "stopped",
            "stage": "diagnose",
            "reason": "unrecoverable",
            "root_cause": diag_record.root_cause,
        }

    # -----------------------------------------------------------------------
    # Stage 2: Decision
    # -----------------------------------------------------------------------
    dec_doc = await database["decisions"].find_one(
        {"event_id": event_id, "diagnosis_version": diag_record.version},
        sort=[("version", DESCENDING)],
    )
    if dec_doc is None:
        customer_ref = event_doc.get("customer_ref", "")
        vault_token_doc = await get_active_vault_token(customer_ref, db=database)
        has_vault = bool(vault_token_doc)

        gw_detail = event_doc.get("gateway_detail") or {}
        advice_code = (
            gw_detail.get("processor_response", {}).get("payment_advice_code")
            or gw_detail.get("payment_advice_code")
        )
        advice_code_str = str(advice_code) if advice_code is not None else None
        is_do_not_retry = advice_code_str in DO_NOT_RETRY_ADVICE_CODES

        resp_code = (
            gw_detail.get("processor_response", {}).get("response_code")
            or gw_detail.get("response_code")
        )
        resp_code_str = str(resp_code) if resp_code is not None else None
        is_do_not_retry_resp = (
            resp_code_str in DO_NOT_RETRY_RESPONSE_CODES
            and event_obj.currency.upper() == "USD"
        )

        excluded_interventions: list[str] | None = None
        exclusion_reason: str | None = None
        if not has_vault:
            excluded_interventions = ["immediate_retry", "delayed_retry"]
            exclusion_reason = "no_active_vault_token"
        elif is_do_not_retry:
            excluded_interventions = ["immediate_retry", "delayed_retry"]
            exclusion_reason = f"payment_advice_{advice_code_str}_do_not_retry"
        elif is_do_not_retry_resp:
            excluded_interventions = ["immediate_retry", "delayed_retry"]
            exclusion_reason = f"processor_response_{resp_code_str}_do_not_retry"

        from app.decision.engine import decide
        from app.decision.store import append as append_decision
        dec_model = decide(
            event=event_obj,
            diagnosis=diag_record,
            excluded_interventions=excluded_interventions,
            exclusion_reason=exclusion_reason,
        )
        dec_doc_id, dec_version = await append_decision(dec_model)
        dec_doc = await database["decisions"].find_one({"_id": ObjectId(dec_doc_id)})
        dec_record = DecisionRecord.from_document(dec_doc)
        await record_run(
            event_id=event_id,
            stage="decide",
            record_version=dec_version,
            outcome=dec_record.recommended_intervention,
            clock=clock,
            db=database,
        )
    else:
        dec_record = DecisionRecord.from_document(dec_doc)

    if dec_record.recommended_intervention == "no_action":
        logger.info("Orchestrator advance: decision is no_action for event %s", event_id)
        return {
            "event_id": event_id,
            "status": "stopped",
            "stage": "decide",
            "reason": "no_action",
        }

    # -----------------------------------------------------------------------
    # Stage 3: Policy
    # -----------------------------------------------------------------------
    verdict_doc = await database["policy_verdicts"].find_one(
        {"decision_id": str(dec_doc["_id"])}
    )
    if verdict_doc is None:
        rulebook = current_rulebook(event_doc.get("currency") or "USD")
        context = await gather_context(
            decision=dec_record,
            customer_ref=event_doc.get("customer_ref", ""),
            now=clock.now(),
            rulebook=rulebook,
        )
        verdict_record = evaluate(decision=dec_record, context=context, rulebook=rulebook)
        verdict_doc_id, verdict_version = await append_verdict(verdict_record)
        verdict_doc = await database["policy_verdicts"].find_one(
            {"_id": ObjectId(verdict_doc_id)}
        )
        await record_run(
            event_id=event_id,
            stage="policy",
            record_version=verdict_version,
            outcome=verdict_record.verdict,
            clock=clock,
            db=database,
        )
    else:
        verdict_record = PolicyVerdictRecord.from_document(verdict_doc)

    if verdict_record.verdict in ("blocked", "requires_manual_review"):
        logger.info(
            "Orchestrator advance: policy verdict %s for event %s",
            verdict_record.verdict,
            event_id,
        )
        return {
            "event_id": event_id,
            "status": "stopped",
            "stage": "policy",
            "verdict": verdict_record.verdict,
            "reason": getattr(verdict_record, "reason", None),
        }

    # -----------------------------------------------------------------------
    # Stage 4: Execution
    # -----------------------------------------------------------------------
    if verdict_record.verdict != "authorized":
        return {
            "event_id": event_id,
            "status": "stopped",
            "stage": "policy",
            "verdict": verdict_record.verdict,
        }

    existing_exec = await database["executions"].find_one(
        {"policy_verdict_id": str(verdict_doc["_id"])}
    )
    if existing_exec is not None:
        return {
            "event_id": event_id,
            "status": "already_executed",
            "stage": "execute",
            "execution_id": str(existing_exec["_id"]),
            "execution_status": existing_exec.get("status"),
        }

    authorized_verdict = require_authorized(verdict_doc)

    vault_token_str = None
    if dec_record.recommended_intervention in ("immediate_retry", "delayed_retry"):
        vault_doc = await get_active_vault_token(
            event_doc.get("customer_ref", ""), db=database
        )
        vault_token_str = vault_doc.vault_id if vault_doc else None

    outcome = await execute_service(
        authorized_verdict,
        vault_token=vault_token_str,
        simulated_time=clock.is_simulated(),
    )
    await record_run(
        event_id=event_id,
        stage="execute",
        record_version=verdict_doc.get("version", 1),
        outcome=outcome.record.status,
        clock=clock,
        db=database,
    )

    return {
        "event_id": event_id,
        "status": "completed",
        "stage": "execute",
        "execution_id": outcome.record.id,
        "execution_status": outcome.record.status,
        "intervention": outcome.record.intervention,
    }
