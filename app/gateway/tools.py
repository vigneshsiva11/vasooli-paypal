"""Core agent tools, pure dry-run evaluators, and secure execution gate.

Functions:
- list_at_risk_events: lists events at risk, sanitized, zero raw customer text.
- get_event_audit: retrieves complete audit trail for an event, sanitized, zero raw customer text.
- get_active_rules: retrieves currently active policy rules and parameters, sanitized.
- propose_recovery: dry-run evaluation of recovery intervention using real pure functions.
- propose_contact: dry-run evaluation of contact intervention using real pure functions.
- execute_recovery: strictly guarded execution requiring AuthorizedVerdict through structural path.
- dispatch_tool: tool registry dispatcher that audits and refuses unknown tools.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from bson import ObjectId

from app.config import get_settings
from app.db import get_database
from app.decision.engine import decide
from app.decision.matrix import candidates_for, cost_of
from app.diagnosis.rules import classify
from app.gateway.audit import record_audit
from app.models import DecisionRecord, DiagnosisRecord, RevenueEvent, expected_recovery_value
from app.models.decision import Decision
from app.models.diagnosis import is_recoverable
from app.models.execution import AuthorizedVerdict, require_authorized
from app.models.policy import PolicyVerdict
from app.policy.engine import PolicyContext, evaluate as evaluate_policy
from app.policy.rulebook_store import get_active_usd_rulebook
from app.policy.rules import current_inr_rulebook, unknown_currency_rulebook
from app.sanitizer import sanitize_public_dict

logger = logging.getLogger(__name__)

ALLOWED_TOOLS = frozenset({
    "list_at_risk_events",
    "get_event_audit",
    "get_active_rules",
    "propose_recovery",
    "propose_contact",
    "execute_recovery",
})

RAW_CUSTOMER_TEXT_KEYS = frozenset({
    "raw_failure_reason",
    "raw_text",
    "customer_text",
    "notes",
    "customer_notes",
    "message",
    "contact_message_summary",
    "communication",
    "feedback",
    "comment",
    "body",
})

SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")
DANGEROUS_PATTERNS = re.compile(r"[<>\'\"\;\$\{\}\\\x00]|--|\/\*", re.IGNORECASE)

EXECUTION_COOLDOWN_SECONDS = 600.0  # 10 minutes

# In-memory execution timestamps per event for cooldown tracking (event_id -> datetime)
_EVENT_EXECUTION_TIMESTAMPS: dict[str, datetime] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_safe_string(val: Any, field_name: str = "id", max_len: int = 128) -> str:
    """Validate string argument against injection payloads, control characters, and size caps."""
    if not isinstance(val, str):
        raise ValueError(f"Argument '{field_name}' must be a string, got {type(val).__name__}")
    s = val.strip()
    if not s:
        raise ValueError(f"Argument '{field_name}' cannot be empty")
    if len(s) > max_len:
        raise ValueError(f"Argument '{field_name}' length {len(s)} exceeds maximum permitted {max_len}")
    if DANGEROUS_PATTERNS.search(s) or not SAFE_ID_PATTERN.match(s):
        raise ValueError(f"Argument '{field_name}' contains invalid or injected characters")
    return s


def sanitize_agent_output(data: Any) -> Any:
    """Sanitize output via sanitize_public_dict and recursively remove raw customer text."""
    base_clean = sanitize_public_dict(data)

    def _strip_customer_text(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                k: _strip_customer_text(v)
                for k, v in item.items()
                if str(k).lower() not in RAW_CUSTOMER_TEXT_KEYS
            }
        if isinstance(item, list):
            return [_strip_customer_text(x) for x in item]
        if isinstance(item, tuple):
            return tuple(_strip_customer_text(x) for x in item)
        return item

    return _strip_customer_text(base_clean)


def is_attack_range_identifier(identifier: str) -> bool:
    """Identify whether an event_id or scenario_id belongs to the adversarial attack range."""
    if not identifier:
        return False
    low = identifier.lower().strip()
    if low.startswith("atk_") or low.startswith("attack_") or low.startswith("scenario_"):
        return True
    if any(k in low for k in ("attack", "exploit", "adversarial", "injection", "malicious")):
        return True
    return False


async def list_at_risk_events(limit: int = 50, db: Any = None) -> list[dict[str, Any]]:
    """List events currently in 'at_risk' status, sanitized and stripped of customer text."""
    capped_limit = max(1, min(limit, 50))
    database = db
    if database is None:
        try:
            database = get_database()
        except Exception:
            database = None

    if database is None:
        return []

    try:
        cursor = database["events"].find({"status": "at_risk"}, {"_id": 0}).limit(capped_limit)
        events = await cursor.to_list(length=capped_limit)
    except Exception as exc:
        logger.warning("Error querying at-risk events: %s", exc)
        return []

    return [sanitize_agent_output(evt) for evt in events]


async def get_event_audit(event_id: str, db: Any = None) -> dict[str, Any] | None:
    """Retrieve complete audit history for event_id, sanitized, stripped of customer text."""
    clean_event_id = validate_safe_string(event_id, field_name="event_id", max_len=128)

    from app import metrics

    try:
        snapshot = await metrics.load_snapshot(clean_event_id)
        trail = metrics.assemble(snapshot, clean_event_id)
        return sanitize_agent_output(trail.model_dump())
    except metrics.EventNotFound:
        return None
    except Exception as exc:
        logger.warning("Failed to assemble audit trail for event %s: %s", clean_event_id, exc)
        return None


async def _resolve_rulebook(currency: str, db: Any = None):
    curr = currency.upper()
    if curr == "USD":
        try:
            return await get_active_usd_rulebook(db=db)
        except Exception:
            from app.policy.rules import current_usd_rulebook
            return current_usd_rulebook()
    elif curr == "INR":
        return current_inr_rulebook()
    else:
        return unknown_currency_rulebook(curr)


async def get_active_rules(currency: str = "USD", db: Any = None) -> dict[str, Any]:
    """Retrieve active policy rulebook parameters for currency, sanitized."""
    clean_curr = validate_safe_string(currency, field_name="currency", max_len=3).upper()
    rulebook = await _resolve_rulebook(clean_curr, db=db)

    rule_dict = {
        "fingerprint": rulebook.fingerprint,
        "tier_currency": rulebook.tier_currency,
        "minimum_erv": rulebook.minimum_erv,
        "auto_authorize_below": rulebook.auto_authorize_below,
        "never_auto_at_or_above": rulebook.never_auto_at_or_above,
        "max_contacts_per_event": rulebook.max_contacts_per_event,
        "cooldown_hours": rulebook.cooldown_hours,
        "max_retries_per_event": rulebook.max_retries_per_event,
        "retry_cooldown_hours": rulebook.retry_cooldown_hours,
        "do_not_retry_response_codes": sorted(rulebook.do_not_retry_response_codes or []),
    }
    return sanitize_agent_output(rule_dict)


async def propose_recovery(
    event: RevenueEvent | dict[str, Any],
    context: PolicyContext | dict[str, Any] | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Dry run: real diagnosis rules, decision engine, and policy evaluate with ACTIVE rulebook.

    Writes ZERO records to events, executions, verdicts, ledger, or metrics.
    Returns: verdict, reason, the six checks, fingerprint.
    """
    if isinstance(event, dict):
        event_obj = RevenueEvent.model_validate(event)
    else:
        event_obj = event

    # 1. Pure diagnosis rules
    match = classify(event_obj)
    root_cause = match.root_cause if match else "unknown"
    confidence = match.confidence if match else 0.50
    evidence = match.evidence if match else ("rules had no exact match",)

    diag_rec = DiagnosisRecord(
        id=str(ObjectId()),
        event_id=event_obj.event_id,
        version=1,
        surface=event_obj.surface,
        root_cause=root_cause,
        recoverable=is_recoverable(root_cause),
        confidence=confidence,
        method="rules" if match else "fallback",
        evidence=evidence,
        diagnosed_at=_utc_now(),
    )

    # 2. Pure decision engine
    dec = decide(diagnosis=diag_rec, event=event_obj)
    dec_rec = DecisionRecord(
        id=str(ObjectId()),
        version=1,
        **dec.model_dump(),
    )

    # 3. Active rulebook
    rulebook = await _resolve_rulebook(event_obj.currency, db=db)

    # 4. Policy context
    if context is None:
        pol_context = PolicyContext(
            customer_ref=event_obj.customer_ref,
            customer_opted_out=False,
            prior_authorized_contacts=0,
            last_authorized_contact_at=None,
            has_vault_token=True,
            now=_utc_now(),
        )
    elif isinstance(context, dict):
        pol_context = PolicyContext(**context)
    else:
        pol_context = context

    # 5. Pure policy evaluate
    verdict: PolicyVerdict = evaluate_policy(
        decision=dec_rec,
        context=pol_context,
        rulebook=rulebook,
    )

    return {
        "verdict": verdict.verdict,
        "reason": verdict.reason,
        "checks": list(verdict.checks_performed),
        "rulebook_fingerprint": verdict.rulebook_fingerprint,
        "decision": sanitize_agent_output(dec_rec.model_dump()),
        "diagnosis": sanitize_agent_output(diag_rec.model_dump()),
    }


async def propose_contact(
    event: RevenueEvent | dict[str, Any],
    context: PolicyContext | dict[str, Any] | None = None,
    intervention: str | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Dry run: evaluate contact intervention with real pure functions under ACTIVE rulebook.

    Writes ZERO records to events, executions, verdicts, ledger, or metrics.
    Returns: verdict, reason, the six checks, fingerprint.
    """
    if isinstance(event, dict):
        event_obj = RevenueEvent.model_validate(event)
    else:
        event_obj = event

    # 1. Pure diagnosis rules
    match = classify(event_obj)
    root_cause = match.root_cause if match else "card_expired"
    confidence = match.confidence if match else 0.90
    evidence = match.evidence if match else ("contact evaluation rules",)

    diag_rec = DiagnosisRecord(
        id=str(ObjectId()),
        event_id=event_obj.event_id,
        version=1,
        surface=event_obj.surface,
        root_cause=root_cause,
        recoverable=is_recoverable(root_cause),
        confidence=confidence,
        method="rules",
        evidence=evidence,
        diagnosed_at=_utc_now(),
    )

    # 2. Chosen contact intervention (must be an executable contact intervention)
    chosen_intervention = intervention or "payment_method_update_link"
    allowed_contacts = {"reminder", "escalating_reminder_sequence", "manual_escalation", "payment_method_update_link"}
    if chosen_intervention not in allowed_contacts:
        chosen_intervention = "payment_method_update_link"

    cost = cost_of(chosen_intervention, currency=event_obj.currency.upper())
    erv = expected_recovery_value(event_obj.amount, 0.70, cost)
    dec_contact = Decision(
        event_id=event_obj.event_id,
        diagnosis_id=diag_rec.id,
        diagnosis_version=diag_rec.version,
        recommended_intervention=chosen_intervention,
        estimated_cost=cost,
        recovery_probability=0.70,
        revenue_at_risk=event_obj.amount,
        expected_recovery_value=erv,
        reasoning=f"Contact evaluated: {chosen_intervention}",
    )
    dec_rec = DecisionRecord(
        id=str(ObjectId()),
        version=1,
        **dec_contact.model_dump(),
    )

    # 3. Active rulebook
    rulebook = await _resolve_rulebook(event_obj.currency, db=db)

    # 4. Policy context
    if context is None:
        pol_context = PolicyContext(
            customer_ref=event_obj.customer_ref,
            customer_opted_out=False,
            prior_authorized_contacts=0,
            last_authorized_contact_at=None,
            has_vault_token=False,
            now=_utc_now(),
        )
    elif isinstance(context, dict):
        pol_context = PolicyContext(**context)
    else:
        pol_context = context

    # 5. Pure policy evaluate
    verdict: PolicyVerdict = evaluate_policy(
        decision=dec_rec,
        context=pol_context,
        rulebook=rulebook,
    )

    return {
        "verdict": verdict.verdict,
        "reason": verdict.reason,
        "checks": list(verdict.checks_performed),
        "rulebook_fingerprint": verdict.rulebook_fingerprint,
        "decision": sanitize_agent_output(dec_rec.model_dump()),
        "diagnosis": sanitize_agent_output(diag_rec.model_dump()),
    }


async def execute_recovery(
    verdict: AuthorizedVerdict | dict[str, Any],
    event_id: str | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Execute recovery: requires AuthorizedVerdict, sandbox only, refuses attack-range, 1 per 10 mins."""
    # 1. Structural path verification
    try:
        if isinstance(verdict, dict):
            auth_verdict = require_authorized(verdict)
        elif isinstance(verdict, AuthorizedVerdict):
            auth_verdict = verdict
        else:
            auth_verdict = require_authorized(verdict.model_dump())
    except Exception as exc:
        return {
            "outcome": "refused",
            "reason": "not_authorized",
            "detail": f"Structural path refusal: verdict is not authorized: {exc}",
            "verdict": getattr(verdict, "verdict", "unauthorized") if hasattr(verdict, "verdict") else "unauthorized",
            "check": "structural_authorization",
        }

    target_event_id = event_id or auth_verdict.event_id

    # 2. Sandbox only check
    settings = get_settings()
    if getattr(settings, "paypal_env", "").lower() != "sandbox" and settings.is_production:
        return {
            "outcome": "refused",
            "reason": "sandbox_only",
            "detail": "Live execution is prohibited; execution is permitted in sandbox only.",
            "verdict": "refused",
            "check": "environment_sandbox",
        }

    # 3. Attack-range scenarios refusal
    if is_attack_range_identifier(target_event_id):
        return {
            "outcome": "refused",
            "reason": "attack_range_scenario_refused",
            "detail": f"Execution refused: event {target_event_id!r} is within the adversarial attack range.",
            "verdict": "refused",
            "check": "attack_range_guard",
        }

    # 4. Cooldown: one execution per event per 10 minutes
    now = _utc_now()
    ten_minutes_ago = now - timedelta(seconds=EXECUTION_COOLDOWN_SECONDS)

    # Check in-memory timestamps
    if target_event_id in _EVENT_EXECUTION_TIMESTAMPS:
        last_exec = _EVENT_EXECUTION_TIMESTAMPS[target_event_id]
        if last_exec >= ten_minutes_ago:
            return {
                "outcome": "refused",
                "reason": "rate_limited_per_event",
                "detail": f"Execution refused: event {target_event_id!r} was executed within the last 10 minutes.",
                "verdict": "refused",
                "check": "execution_cooldown_10m",
            }

    # Check database if connected
    database = db
    if database is None:
        try:
            database = get_database()
        except Exception:
            database = None

    if database is not None:
        try:
            recent = await database["executions"].find_one({
                "event_id": target_event_id,
                "executed_at": {"$gte": ten_minutes_ago},
            })
            if recent:
                return {
                    "outcome": "refused",
                    "reason": "rate_limited_per_event",
                    "detail": f"Execution refused: event {target_event_id!r} was executed within the last 10 minutes.",
                    "verdict": "refused",
                    "check": "execution_cooldown_10m",
                }
        except Exception as exc:
            logger.warning("Error checking prior executions for %s: %s", target_event_id, exc)

    # 5. Invoke real execution service
    from app.execution.service import execute

    try:
        outcome = await execute(auth_verdict)
        _EVENT_EXECUTION_TIMESTAMPS[target_event_id] = now
        return {
            "outcome": "allowed",
            "reason": "ok",
            "verdict": "authorized",
            "execution": sanitize_agent_output(outcome.record.model_dump()),
            "created": outcome.created,
        }
    except Exception as exc:
        logger.warning("Execution service error: %s", exc)
        return {
            "outcome": "error",
            "reason": str(exc),
            "verdict": "failed",
            "detail": f"Execution failed: {exc}",
        }


async def dispatch_tool(
    tool_name: str,
    args: dict[str, Any],
    client_key_hash: str = "local",
    agent_id: str = "gateway_agent",
    db: Any = None,
) -> dict[str, Any]:
    """Dispatch tool call through registry. Unknown tools are refused with 'not offered' and audited."""
    # Check registry of allowed tool names
    if tool_name not in ALLOWED_TOOLS:
        refusal = {
            "outcome": "refused",
            "reason": "not offered",
            "verdict": "refused",
            "check": "tool_registry",
            "detail": f"Tool '{tool_name}' is not offered by the agent gateway.",
        }
        await record_audit(
            agent_id=agent_id,
            tool=tool_name,
            event_or_scenario=str(args.get("scenario_id") or args.get("event_id") or "none"),
            args=args,
            outcome="refused",
            reason="not offered",
            check="tool_registry",
            verdict="refused",
            client_key_hash=client_key_hash,
            db=db,
        )
        return refusal

    # Dispatch known tools
    try:
        if tool_name == "list_at_risk_events":
            limit = int(args.get("limit", 50))
            data = await list_at_risk_events(limit=limit, db=db)
            res = {"outcome": "allowed", "reason": "ok", "verdict": "authorized", "events": data}

        elif tool_name == "get_event_audit":
            event_id = str(args["event_id"])
            data = await get_event_audit(event_id=event_id, db=db)
            res = {"outcome": "allowed", "reason": "ok", "verdict": "authorized", "audit_trail": data}

        elif tool_name == "get_active_rules":
            curr = str(args.get("currency", "USD"))
            data = await get_active_rules(currency=curr, db=db)
            res = {"outcome": "allowed", "reason": "ok", "verdict": "authorized", "rules": data}

        elif tool_name == "propose_recovery":
            res = await propose_recovery(
                event=args["event"],
                context=args.get("context"),
                db=db,
            )
            # Map verdict to outcome
            v = res.get("verdict")
            outcome = "allowed" if v == "authorized" else ("manual_review" if v == "requires_manual_review" else "blocked")
            res["outcome"] = outcome

        elif tool_name == "propose_contact":
            res = await propose_contact(
                event=args["event"],
                context=args.get("context"),
                intervention=args.get("intervention"),
                db=db,
            )
            v = res.get("verdict")
            outcome = "allowed" if v == "authorized" else ("manual_review" if v == "requires_manual_review" else "blocked")
            res["outcome"] = outcome

        elif tool_name == "execute_recovery":
            res = await execute_recovery(
                verdict=args["verdict"],
                event_id=args.get("event_id"),
                db=db,
            )

        else:
            res = {"outcome": "refused", "reason": "not offered", "verdict": "refused"}

        # Audit allowed or rejected outcome
        await record_audit(
            agent_id=agent_id,
            tool=tool_name,
            event_or_scenario=str(args.get("scenario_id") or args.get("event_id") or "operational"),
            args=args,
            outcome=res.get("outcome", "allowed"),
            reason=res.get("reason", "ok"),
            check=res.get("check"),
            verdict=res.get("verdict"),
            rulebook_fingerprint=res.get("rulebook_fingerprint"),
            client_key_hash=client_key_hash,
            db=db,
        )
        return res

    except Exception as exc:
        err_res = {
            "outcome": "error",
            "reason": str(exc),
            "verdict": "error",
            "detail": f"Tool execution failed: {exc}",
        }
        await record_audit(
            agent_id=agent_id,
            tool=tool_name,
            event_or_scenario=str(args.get("scenario_id") or args.get("event_id") or "error"),
            args=args,
            outcome="error",
            reason=str(exc),
            verdict="error",
            client_key_hash=client_key_hash,
            db=db,
        )
        return err_res


def clear_execution_cooldowns() -> None:
    """Clear in-memory execution timestamps for testing."""
    _EVENT_EXECUTION_TIMESTAMPS.clear()
