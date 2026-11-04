"""Core agent tools, pure dry-run evaluators, and secure execution gate.

Functions:
- list_at_risk_events: lists events at risk, sanitized, zero raw customer text.
- get_event_audit: retrieves complete audit trail for an event, sanitized, zero raw customer text.
- get_active_rules: retrieves currently active policy rules and parameters, sanitized.
- propose_recovery: dry-run evaluation of recovery intervention using real pure functions.
- propose_contact: dry-run evaluation of contact intervention using real pure functions.
- execute_recovery: strictly guarded execution requiring AuthorizedVerdict through structural path.
- list_attack_scenarios: lists in-memory catalogue attack scenarios, dry-run only.
- run_attack_scenario: executes a catalogue attack scenario in-memory, dry-run only.
- dispatch_tool: tool registry dispatcher that audits and refuses unknown tools.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from bson import ObjectId
import pydantic
from pydantic import ValidationError

from app.config import get_settings
from app.db import get_database
from app.decision.engine import decide
from app.decision.matrix import candidates_for, cost_of
from app.diagnosis.rules import classify
from app.gateway.audit import record_audit
from app.models import DecisionRecord, DiagnosisRecord, RevenueEvent, expected_recovery_value
from app.models.decision import Decision
from app.models.diagnosis import is_recoverable
from app.models.execution import AuthorizedVerdict, NotAuthorized, require_authorized
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
    "list_attack_scenarios",
    "run_attack_scenario",
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


def format_pydantic_error(exc: pydantic.ValidationError) -> str:
    """Format a Pydantic ValidationError into clean, plain text with field names only.

    Never includes class names, raw Pydantic messages, or URLs.
    Example output: 'invalid input: missing fields event_id, surface, amount, customer_ref'
    """
    missing: list[str] = []
    invalid: list[str] = []

    for err in exc.errors():
        loc = err.get("loc", ())
        field_name = str(loc[-1]) if loc else "field"
        err_type = str(err.get("type", ""))
        if "missing" in err_type:
            if field_name not in missing:
                missing.append(field_name)
        else:
            if field_name not in invalid and field_name not in missing:
                invalid.append(field_name)

    parts: list[str] = []
    if missing:
        parts.append(f"missing fields {', '.join(missing)}")
    if invalid:
        parts.append(f"invalid fields {', '.join(invalid)}")

    detail = "; ".join(parts) if parts else "malformed payload"
    return f"invalid input: {detail}"


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


async def list_at_risk_events(limit: int | Any = 50, db: Any = None) -> list[dict[str, Any]] | dict[str, Any]:
    """List revenue events currently in 'at_risk' status, sanitized and stripped of customer text.

    Input shape:
    {"limit": 50}

    Example:
    {"limit": 10}
    """
    try:
        capped_limit = max(1, min(int(limit), 50))
    except (ValueError, TypeError):
        return {
            "outcome": "refused",
            "reason": "invalid input: invalid fields limit",
            "verdict": "refused",
            "check": "input_validation",
        }

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


async def get_event_audit(event_id: str | Any = None, db: Any = None) -> dict[str, Any] | None:
    """Retrieve complete audit history for event_id, sanitized, stripped of customer text.

    Input shape:
    {"event_id": "<event_id_string>"}

    Example:
    {"event_id": "evt_12345"}
    """
    if event_id is None or event_id == "":
        return {
            "outcome": "refused",
            "reason": "invalid input: missing fields event_id",
            "verdict": "refused",
            "check": "input_validation",
        }
    if not isinstance(event_id, str):
        return {
            "outcome": "refused",
            "reason": "invalid input: invalid fields event_id",
            "verdict": "refused",
            "check": "input_validation",
        }
    clean_event_id = event_id.strip()
    if DANGEROUS_PATTERNS.search(clean_event_id) or not SAFE_ID_PATTERN.match(clean_event_id) or len(clean_event_id) > 128:
        return {
            "outcome": "refused",
            "reason": "invalid input: invalid characters in event_id",
            "verdict": "refused",
            "check": "input_validation",
        }

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


async def get_active_rules(currency: str | Any = "USD", db: Any = None) -> dict[str, Any]:
    """Retrieve active policy rulebook parameters for currency, sanitized.

    Input shape:
    {"currency": "USD"}

    Example:
    {"currency": "USD"}
    """
    if currency is None or currency == "":
        return {
            "outcome": "refused",
            "reason": "invalid input: missing fields currency",
            "verdict": "refused",
            "check": "input_validation",
        }
    if not isinstance(currency, str):
        return {
            "outcome": "refused",
            "reason": "invalid input: invalid fields currency",
            "verdict": "refused",
            "check": "input_validation",
        }
    clean_curr = currency.strip()
    if len(clean_curr) > 3 or DANGEROUS_PATTERNS.search(clean_curr) or not SAFE_ID_PATTERN.match(clean_curr):
        return {
            "outcome": "refused",
            "reason": "invalid input: invalid fields currency",
            "verdict": "refused",
            "check": "input_validation",
        }
    clean_curr = clean_curr.upper()
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
    event: RevenueEvent | dict[str, Any] | Any = None,
    context: PolicyContext | dict[str, Any] | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Dry run: real diagnosis rules, decision engine, and policy evaluate with ACTIVE rulebook.

    Writes ZERO records to events, executions, verdicts, ledger, or metrics.
    Returns: verdict, reason, the six checks, fingerprint.

    Input shape:
    {
      "event": {
        "event_id": "<string>",
        "surface": "payment|subscription|checkout|receivable",
        "amount": <number>,
        "customer_ref": "<string>",
        "currency": "<optional_iso_code>",
        "raw_failure_reason": "<optional_string>",
        "gateway": "<optional_razorpay_or_paypal>"
      },
      "context": {
        "customer_ref": "<optional_string>",
        "customer_opted_out": <optional_bool>,
        "prior_authorized_contacts": <optional_int>,
        "has_vault_token": <optional_bool>,
        "processor_response_code": "<optional_string>"
      }
    }

    Example:
    {
      "event": {
        "event_id": "evt_rec_001",
        "surface": "subscription",
        "amount": 49.0,
        "currency": "USD",
        "customer_ref": "cust_123"
      }
    }
    """
    if event is None or (not isinstance(event, (dict, RevenueEvent))):
        return {
            "outcome": "refused",
            "reason": "invalid input: missing fields event",
            "verdict": "refused",
            "check": "input_validation",
        }

    if isinstance(event, dict):
        try:
            event_obj = RevenueEvent.model_validate(event)
        except ValidationError as exc:
            return {
                "outcome": "refused",
                "reason": format_pydantic_error(exc),
                "verdict": "refused",
                "check": "input_validation",
            }
        except Exception:
            return {
                "outcome": "refused",
                "reason": "invalid input: invalid fields event",
                "verdict": "refused",
                "check": "input_validation",
            }
    else:
        event_obj = event

    # Validate context if provided
    if context is not None:
        if not isinstance(context, (dict, PolicyContext)):
            return {
                "outcome": "refused",
                "reason": "invalid input: invalid fields context",
                "verdict": "refused",
                "check": "input_validation",
            }
        if isinstance(context, dict):
            try:
                pol_context = PolicyContext(**context)
            except ValidationError as exc:
                return {
                    "outcome": "refused",
                    "reason": format_pydantic_error(exc),
                    "verdict": "refused",
                    "check": "input_validation",
                }
            except Exception:
                return {
                    "outcome": "refused",
                    "reason": "invalid input: invalid fields context",
                    "verdict": "refused",
                    "check": "input_validation",
                }
        else:
            pol_context = context
    else:
        pol_context = PolicyContext(
            customer_ref=event_obj.customer_ref,
            customer_opted_out=False,
            prior_authorized_contacts=0,
            last_authorized_contact_at=None,
            has_vault_token=True,
            now=_utc_now(),
        )

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

    # 4. Pure policy evaluate
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
    event: RevenueEvent | dict[str, Any] | Any = None,
    context: PolicyContext | dict[str, Any] | None = None,
    intervention: str | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Dry run: evaluate contact intervention with real pure functions under ACTIVE rulebook.

    Writes ZERO records to events, executions, verdicts, ledger, or metrics.
    Returns: verdict, reason, the six checks, fingerprint.

    Input shape:
    {
      "event": {
        "event_id": "<string>",
        "surface": "payment|subscription|checkout|receivable",
        "amount": <number>,
        "customer_ref": "<string>",
        "currency": "<optional_iso_code>"
      },
      "context": {
        "customer_ref": "<optional_string>",
        "customer_opted_out": <optional_bool>,
        "prior_authorized_contacts": <optional_int>
      },
      "intervention": "<optional_contact_intervention_name>"
    }

    Example:
    {
      "event": {
        "event_id": "evt_con_001",
        "surface": "subscription",
        "amount": 49.0,
        "currency": "USD",
        "customer_ref": "cust_123"
      },
      "intervention": "payment_method_update_link"
    }
    """
    if event is None or (not isinstance(event, (dict, RevenueEvent))):
        return {
            "outcome": "refused",
            "reason": "invalid input: missing fields event",
            "verdict": "refused",
            "check": "input_validation",
        }

    if isinstance(event, dict):
        try:
            event_obj = RevenueEvent.model_validate(event)
        except ValidationError as exc:
            return {
                "outcome": "refused",
                "reason": format_pydantic_error(exc),
                "verdict": "refused",
                "check": "input_validation",
            }
        except Exception:
            return {
                "outcome": "refused",
                "reason": "invalid input: invalid fields event",
                "verdict": "refused",
                "check": "input_validation",
            }
    else:
        event_obj = event

    if context is not None:
        if not isinstance(context, (dict, PolicyContext)):
            return {
                "outcome": "refused",
                "reason": "invalid input: invalid fields context",
                "verdict": "refused",
                "check": "input_validation",
            }
        if isinstance(context, dict):
            try:
                pol_context = PolicyContext(**context)
            except ValidationError as exc:
                return {
                    "outcome": "refused",
                    "reason": format_pydantic_error(exc),
                    "verdict": "refused",
                    "check": "input_validation",
                }
            except Exception:
                return {
                    "outcome": "refused",
                    "reason": "invalid input: invalid fields context",
                    "verdict": "refused",
                    "check": "input_validation",
                }
        else:
            pol_context = context
    else:
        pol_context = PolicyContext(
            customer_ref=event_obj.customer_ref,
            customer_opted_out=False,
            prior_authorized_contacts=0,
            last_authorized_contact_at=None,
            has_vault_token=False,
            now=_utc_now(),
        )

    allowed_contacts = {"reminder", "escalating_reminder_sequence", "manual_escalation", "payment_method_update_link"}
    if intervention is not None:
        if not isinstance(intervention, str) or intervention not in allowed_contacts:
            return {
                "outcome": "refused",
                "reason": "invalid input: invalid fields intervention",
                "verdict": "refused",
                "check": "input_validation",
            }

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

    # 4. Pure policy evaluate
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
    verdict: AuthorizedVerdict | dict[str, Any] | Any = None,
    event_id: str | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Execute recovery: requires AuthorizedVerdict, sandbox only, refuses attack-range, 1 per 10 mins.

    Input shape:
    {
      "verdict": {
        "event_id": "<string>",
        "decision_id": "<24_hex_id>",
        "decision_version": <int>,
        "verdict": "authorized",
        "reason": "ok",
        "checks_performed": ["<check_name: PASS (ok)>"],
        "rulebook_fingerprint": "<string>"
      },
      "event_id": "<optional_string>"
    }

    Example:
    {
      "verdict": {
        "event_id": "evt_safe_001",
        "decision_id": "64b1f2000000000000000001",
        "decision_version": 1,
        "verdict": "authorized",
        "reason": "ok",
        "checks_performed": ["amount_tier: PASS (ok)"],
        "rulebook_fingerprint": "rb1_8221b1b9069ffc80"
      }
    }
    """
    if verdict is None:
        return {
            "outcome": "refused",
            "reason": "invalid input: missing fields verdict",
            "verdict": "refused",
            "check": "input_validation",
        }

    if not isinstance(verdict, (dict, AuthorizedVerdict)):
        return {
            "outcome": "refused",
            "reason": "invalid input: invalid fields verdict",
            "verdict": "refused",
            "check": "input_validation",
        }

    # 1. Structural path verification
    try:
        if isinstance(verdict, dict):
            # Check for required fields before narrowing
            required_fields = ("event_id", "decision_id", "verdict", "reason")
            missing_fields = [f for f in required_fields if f not in verdict or verdict[f] is None]
            if missing_fields:
                return {
                    "outcome": "refused",
                    "reason": f"invalid input: missing fields {', '.join(missing_fields)}",
                    "verdict": "refused",
                    "check": "input_validation",
                }
            auth_verdict = require_authorized(verdict)
        elif isinstance(verdict, AuthorizedVerdict):
            auth_verdict = verdict
        else:
            auth_verdict = require_authorized(verdict.model_dump())
    except ValidationError as exc:
        return {
            "outcome": "refused",
            "reason": format_pydantic_error(exc),
            "verdict": "refused",
            "check": "input_validation",
        }
    except NotAuthorized:
        return {
            "outcome": "refused",
            "reason": "not_authorized",
            "detail": "Structural path refusal: verdict is not authorized.",
            "verdict": str(getattr(verdict, "verdict", "unauthorized") if hasattr(verdict, "verdict") else verdict.get("verdict", "unauthorized")),
            "check": "structural_authorization",
        }
    except Exception:
        return {
            "outcome": "refused",
            "reason": "invalid input: malformed payload",
            "verdict": "refused",
            "check": "input_validation",
        }

    target_event_id = event_id or auth_verdict.event_id
    if not isinstance(target_event_id, str):
        return {
            "outcome": "refused",
            "reason": "invalid input: invalid fields event_id",
            "verdict": "refused",
            "check": "input_validation",
        }
    clean_target_id = target_event_id.strip()
    if DANGEROUS_PATTERNS.search(clean_target_id) or not SAFE_ID_PATTERN.match(clean_target_id) or len(clean_target_id) > 128:
        return {
            "outcome": "refused",
            "reason": "invalid input: invalid characters in event_id",
            "verdict": "refused",
            "check": "input_validation",
        }

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
    if is_attack_range_identifier(clean_target_id):
        return {
            "outcome": "refused",
            "reason": "attack_range_scenario_refused",
            "detail": f"Execution refused: event {clean_target_id!r} is within the adversarial attack range.",
            "verdict": "refused",
            "check": "attack_range_guard",
        }

    # 4. Cooldown: one execution per event per 10 minutes
    now = _utc_now()
    ten_minutes_ago = now - timedelta(seconds=EXECUTION_COOLDOWN_SECONDS)

    # Check in-memory timestamps
    if clean_target_id in _EVENT_EXECUTION_TIMESTAMPS:
        last_exec = _EVENT_EXECUTION_TIMESTAMPS[clean_target_id]
        if last_exec >= ten_minutes_ago:
            return {
                "outcome": "refused",
                "reason": "rate_limited_per_event",
                "detail": f"Execution refused: event {clean_target_id!r} was executed within the last 10 minutes.",
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
                "event_id": clean_target_id,
                "executed_at": {"$gte": ten_minutes_ago},
            })
            if recent:
                return {
                    "outcome": "refused",
                    "reason": "rate_limited_per_event",
                    "detail": f"Execution refused: event {clean_target_id!r} was executed within the last 10 minutes.",
                    "verdict": "refused",
                    "check": "execution_cooldown_10m",
                }
        except Exception as exc:
            logger.warning("Error checking prior executions for %s: %s", clean_target_id, exc)

    # 5. Invoke real execution service
    from app.execution.service import execute

    try:
        outcome = await execute(auth_verdict)
        _EVENT_EXECUTION_TIMESTAMPS[clean_target_id] = now
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


async def list_attack_scenarios(db: Any = None) -> dict[str, Any]:
    """List all 7 in-memory attack and control scenarios from the gateway catalogue.

    Dry-run only: writes zero records to events, executions, verdicts, ledger, or metrics.
    Writes only agent_audit rows when dispatched through gateway.

    Input shape:
    {}

    Example:
    {}
    """
    from app.gateway.catalogue import list_scenarios
    scenarios = list_scenarios()
    return {
        "outcome": "allowed",
        "reason": "ok",
        "verdict": "authorized",
        "scenarios": [sanitize_agent_output(s) for s in scenarios],
    }


async def run_attack_scenario(
    scenario_id: Any = None,
    client_key_hash: str = "local",
    db: Any = None,
) -> dict[str, Any]:
    """Execute a catalogue attack scenario in-memory through real pure functions.

    Dry-run only: writes zero records to events, executions, verdicts, ledger, or metrics.
    Writes only an agent_audit row when dispatched through gateway.

    Input shape:
    {"scenario_id": "<scenario_id_string>"}

    Example:
    {"scenario_id": "retry_on_9500"}
    """
    from app.gateway.catalogue import ATTACK_SCENARIOS, run_scenario

    if scenario_id is None or scenario_id == "":
        return {
            "outcome": "refused",
            "reason": "invalid input: missing fields scenario_id",
            "verdict": "refused",
            "check": "input_validation",
        }
    if not isinstance(scenario_id, str):
        return {
            "outcome": "refused",
            "reason": "invalid input: invalid fields scenario_id",
            "verdict": "refused",
            "check": "input_validation",
        }
    clean_id = scenario_id.strip()
    if DANGEROUS_PATTERNS.search(clean_id) or not SAFE_ID_PATTERN.match(clean_id):
        return {
            "outcome": "refused",
            "reason": "invalid input: invalid characters in scenario_id",
            "verdict": "refused",
            "check": "input_validation",
        }
    if clean_id not in ATTACK_SCENARIOS:
        return {
            "outcome": "refused",
            "reason": f"invalid input: unknown scenario_id '{clean_id}'",
            "verdict": "refused",
            "check": "input_validation",
        }
    return await run_scenario(clean_id, client_key_hash=client_key_hash, record_audit_log=False, db=db)


async def dispatch_tool(
    tool_name: str,
    args: dict[str, Any] | Any,
    client_key_hash: str = "local",
    agent_id: str = "gateway_agent",
    db: Any = None,
) -> dict[str, Any]:
    """Dispatch tool call through registry. Unknown tools are refused with 'not offered' and audited."""
    call_args = args if isinstance(args, dict) else {}

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
            event_or_scenario=str(call_args.get("scenario_id") or call_args.get("event_id") or "none"),
            args=call_args,
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
            raw_limit = call_args.get("limit", 50)
            data = await list_at_risk_events(limit=raw_limit, db=db)
            if isinstance(data, dict) and data.get("outcome") == "refused":
                res = data
            else:
                res = {"outcome": "allowed", "reason": "ok", "verdict": "authorized", "events": data}

        elif tool_name == "get_event_audit":
            raw_event_id = call_args.get("event_id")
            data = await get_event_audit(event_id=raw_event_id, db=db)
            if isinstance(data, dict) and data.get("outcome") == "refused":
                res = data
            else:
                res = {"outcome": "allowed", "reason": "ok", "verdict": "authorized", "audit_trail": data}

        elif tool_name == "get_active_rules":
            raw_curr = call_args.get("currency", "USD")
            data = await get_active_rules(currency=raw_curr, db=db)
            if isinstance(data, dict) and data.get("outcome") == "refused":
                res = data
            else:
                res = {"outcome": "allowed", "reason": "ok", "verdict": "authorized", "rules": data}

        elif tool_name == "propose_recovery":
            res = await propose_recovery(
                event=call_args.get("event"),
                context=call_args.get("context"),
                db=db,
            )
            if res.get("outcome") != "refused":
                v = res.get("verdict")
                res["outcome"] = "allowed" if v == "authorized" else ("manual_review" if v == "requires_manual_review" else "blocked")

        elif tool_name == "propose_contact":
            res = await propose_contact(
                event=call_args.get("event"),
                context=call_args.get("context"),
                intervention=call_args.get("intervention"),
                db=db,
            )
            if res.get("outcome") != "refused":
                v = res.get("verdict")
                res["outcome"] = "allowed" if v == "authorized" else ("manual_review" if v == "requires_manual_review" else "blocked")

        elif tool_name == "execute_recovery":
            res = await execute_recovery(
                verdict=call_args.get("verdict"),
                event_id=call_args.get("event_id"),
                db=db,
            )

        elif tool_name == "list_attack_scenarios":
            res = await list_attack_scenarios(db=db)

        elif tool_name == "run_attack_scenario":
            res = await run_attack_scenario(
                scenario_id=call_args.get("scenario_id"),
                client_key_hash=client_key_hash,
                db=db,
            )

        else:
            res = {"outcome": "refused", "reason": "not offered", "verdict": "refused"}

    except ValidationError as exc:
        res = {
            "outcome": "refused",
            "reason": format_pydantic_error(exc),
            "verdict": "refused",
            "check": "input_validation",
        }
    except ValueError as exc:
        clean_msg = str(exc)
        if "http" in clean_msg or "class " in clean_msg or len(clean_msg) > 80:
            clean_msg = "malformed payload"
        res = {
            "outcome": "refused",
            "reason": f"invalid input: {clean_msg}",
            "verdict": "refused",
            "check": "input_validation",
        }
    except Exception:
        res = {
            "outcome": "refused",
            "reason": "invalid input: malformed payload",
            "verdict": "refused",
            "check": "input_validation",
        }

    # Extract target identifier for audit log
    event_or_scenario = str(
        call_args.get("scenario_id")
        or call_args.get("event_id")
        or (call_args.get("event", {}).get("event_id") if isinstance(call_args.get("event"), dict) else None)
        or (res.get("scenario_id") if isinstance(res, dict) else None)
        or "operational"
    )

    await record_audit(
        agent_id=agent_id,
        tool=tool_name,
        event_or_scenario=event_or_scenario,
        args=call_args,
        outcome=res.get("outcome", "allowed"),
        reason=res.get("reason", "ok"),
        check=res.get("check"),
        verdict=res.get("verdict"),
        rulebook_fingerprint=res.get("rulebook_fingerprint"),
        client_key_hash=client_key_hash,
        db=db,
    )
    return res


def clear_execution_cooldowns() -> None:
    """Clear in-memory execution timestamps for testing."""
    _EVENT_EXECUTION_TIMESTAMPS.clear()
