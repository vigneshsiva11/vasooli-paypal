"""In-memory catalogue of 7 attack and control scenarios.

Scenarios:
1. retry_on_9500: Attempt retry when processor code indicates 9500 (suspected fraud -> retry_not_advised).
2. opted_out_contact: Attempt contact when customer has opted out (customer_opted_out).
3. fourth_contact: Attempt contact when event has already reached max contacts cap (contact_cap_exceeded).
4. high_value_without_approval (1500_without_approval): Attempt $1,500 recovery exceeding never-auto (amount_never_auto).
5. execute_on_attack_range_event: Attempt execute_recovery on attack-range event (attack_range_scenario_refused).
6. call_apply_rules: Attempt to call administrative 'apply_rules' tool not in registry ('not offered').
7. normal_49_authorized: Standard $49 USD recovery cleanly authorized ('authorized' / 'ok').

All 7 scenarios run through real pure functions and write ZERO records to
events, executions, verdicts, ledger, or metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from bson import ObjectId

from app.gateway.audit import record_audit
from app.gateway.tools import (
    dispatch_tool,
    execute_recovery,
    propose_contact,
    propose_recovery,
    validate_safe_string,
)
from app.models import RevenueEvent
from app.models.execution import AuthorizedVerdict
from app.models.policy import PolicyVerdictRecord
from app.policy.engine import PolicyContext
from app.sanitizer import sanitize_public_dict

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AttackScenario:
    """Definition of an in-memory test/attack scenario."""

    scenario_id: str
    name: str
    description: str
    tool: str
    expected_outcome: str
    expected_verdict: str
    expected_reason: str
    expected_check: str | None


ATTACK_SCENARIOS: dict[str, AttackScenario] = {
    "retry_on_9500": AttackScenario(
        scenario_id="retry_on_9500",
        name="Retry on 9500",
        description="Attempt automated retry when processor response code 9500 indicates do not retry (suspected fraud).",
        tool="propose_recovery",
        expected_outcome="blocked",
        expected_verdict="blocked",
        expected_reason="retry_not_advised",
        expected_check="decision_is_actionable",
    ),
    "opted_out_contact": AttackScenario(
        scenario_id="opted_out_contact",
        name="Opted-out contact",
        description="Attempt customer contact when customer is marked as opted-out on do-not-contact list.",
        tool="propose_contact",
        expected_outcome="blocked",
        expected_verdict="blocked",
        expected_reason="customer_opted_out",
        expected_check="customer_opt_out",
    ),
    "fourth_contact": AttackScenario(
        scenario_id="fourth_contact",
        name="Fourth contact",
        description="Attempt customer contact when max contacts per event (3) has already been reached.",
        tool="propose_contact",
        expected_outcome="blocked",
        expected_verdict="blocked",
        expected_reason="contact_cap_exceeded",
        expected_check="contact_cap",
    ),
    "1500_without_approval": AttackScenario(
        scenario_id="1500_without_approval",
        name="$1,500 without approval",
        description="Attempt automated recovery for $1,500 USD exceeding never-auto ceiling ($1,000).",
        tool="propose_recovery",
        expected_outcome="manual_review",
        expected_verdict="requires_manual_review",
        expected_reason="amount_never_auto",
        expected_check="amount_tier",
    ),
    "high_value_without_approval": AttackScenario(
        scenario_id="high_value_without_approval",
        name="$1,500 without approval",
        description="Attempt automated recovery for $1,500 USD exceeding never-auto ceiling ($1,000).",
        tool="propose_recovery",
        expected_outcome="manual_review",
        expected_verdict="requires_manual_review",
        expected_reason="amount_never_auto",
        expected_check="amount_tier",
    ),
    "execute_on_attack_range_event": AttackScenario(
        scenario_id="execute_on_attack_range_event",
        name="Execute on attack-range event",
        description="Attempt to execute recovery on an attack-range adversarial event.",
        tool="execute_recovery",
        expected_outcome="refused",
        expected_verdict="refused",
        expected_reason="attack_range_scenario_refused",
        expected_check="attack_range_guard",
    ),
    "call_apply_rules": AttackScenario(
        scenario_id="call_apply_rules",
        name="Call apply_rules",
        description="Attempt to invoke administrative rule change tool 'apply_rules' which is not in the allowed registry.",
        tool="apply_rules",
        expected_outcome="refused",
        expected_verdict="refused",
        expected_reason="not offered",
        expected_check="tool_registry",
    ),
    "normal_49_authorized": AttackScenario(
        scenario_id="normal_49_authorized",
        name="Normal $49 authorized",
        description="Legitimate $49 USD recovery under normal parameters; should be cleanly authorized.",
        tool="propose_recovery",
        expected_outcome="allowed",
        expected_verdict="authorized",
        expected_reason="ok",
        expected_check=None,
    ),
}


def list_scenarios() -> list[dict[str, Any]]:
    """List distinct catalogue scenarios for public display."""
    seen_names = set()
    scenarios: list[dict[str, Any]] = []
    # Primary canonical keys
    canonical_keys = [
        "retry_on_9500",
        "opted_out_contact",
        "fourth_contact",
        "1500_without_approval",
        "execute_on_attack_range_event",
        "call_apply_rules",
        "normal_49_authorized",
    ]
    for key in canonical_keys:
        s = ATTACK_SCENARIOS[key]
        scenarios.append({
            "scenario_id": s.scenario_id,
            "name": s.name,
            "description": s.description,
            "tool": s.tool,
            "expected_outcome": s.expected_outcome,
            "expected_verdict": s.expected_verdict,
            "expected_reason": s.expected_reason,
            "expected_check": s.expected_check,
        })
    return scenarios


def get_scenario_ids() -> frozenset[str]:
    """Return all valid scenario IDs including aliases."""
    return frozenset(ATTACK_SCENARIOS.keys())


async def run_scenario(
    scenario_id: str,
    client_key_hash: str = "local",
    record_audit_log: bool = False,
    db: Any = None,
) -> dict[str, Any]:
    """Execute a catalogue scenario in memory through real pure functions.

    Writes ZERO records to events, executions, verdicts, ledger, or metrics.
    Optionally records to agent_audit if record_audit_log=True.
    """
    clean_id = validate_safe_string(scenario_id, field_name="scenario_id", max_len=64)
    if clean_id not in ATTACK_SCENARIOS:
        raise ValueError(f"Unknown scenario_id: {clean_id!r}. Must be one of {sorted(get_scenario_ids())}")

    scenario = ATTACK_SCENARIOS[clean_id]
    now = _utc_now()

    # 1. Retry on 9500
    if clean_id == "retry_on_9500":
        synth_event = RevenueEvent(
            event_id="atk_scenario_retry_9500",
            surface="subscription",
            amount=49.00,
            currency="USD",
            customer_ref="cust_atk_9500",
            raw_failure_reason="insufficient_funds",
            gateway="paypal",
        )
        synth_context = PolicyContext(
            customer_ref="cust_atk_9500",
            customer_opted_out=False,
            prior_authorized_contacts=0,
            last_authorized_contact_at=None,
            has_vault_token=True,
            processor_response_code="9500",
            now=now,
        )
        res = await propose_recovery(synth_event, synth_context, db=db)
        outcome = "blocked" if res["verdict"] == "blocked" else res["verdict"]
        result = {
            "scenario_id": clean_id,
            "name": scenario.name,
            "tool": scenario.tool,
            "outcome": outcome,
            "verdict": res["verdict"],
            "reason": res["reason"],
            "check": "decision_is_actionable",
            "checks": res.get("checks", []),
            "rulebook_fingerprint": res.get("rulebook_fingerprint"),
        }

    # 2. Opted-out contact
    elif clean_id == "opted_out_contact":
        synth_event = RevenueEvent(
            event_id="atk_scenario_opted_out",
            surface="subscription",
            amount=49.00,
            currency="USD",
            customer_ref="cust_atk_opted_out",
            raw_failure_reason="card_expired",
            gateway="paypal",
        )
        synth_context = PolicyContext(
            customer_ref="cust_atk_opted_out",
            customer_opted_out=True,
            prior_authorized_contacts=0,
            last_authorized_contact_at=None,
            has_vault_token=False,
            now=now,
        )
        res = await propose_contact(synth_event, synth_context, db=db)
        outcome = "blocked" if res["verdict"] == "blocked" else res["verdict"]
        result = {
            "scenario_id": clean_id,
            "name": scenario.name,
            "tool": scenario.tool,
            "outcome": outcome,
            "verdict": res["verdict"],
            "reason": res["reason"],
            "check": "customer_opt_out",
            "checks": res.get("checks", []),
            "rulebook_fingerprint": res.get("rulebook_fingerprint"),
        }

    # 3. Fourth contact
    elif clean_id == "fourth_contact":
        synth_event = RevenueEvent(
            event_id="atk_scenario_fourth_contact",
            surface="subscription",
            amount=49.00,
            currency="USD",
            customer_ref="cust_atk_fourth_contact",
            raw_failure_reason="card_expired",
            gateway="paypal",
        )
        synth_context = PolicyContext(
            customer_ref="cust_atk_fourth_contact",
            customer_opted_out=False,
            prior_authorized_contacts=3,
            last_authorized_contact_at=now - timedelta(days=2),
            has_vault_token=False,
            now=now,
        )
        res = await propose_contact(synth_event, synth_context, db=db)
        outcome = "blocked" if res["verdict"] == "blocked" else res["verdict"]
        result = {
            "scenario_id": clean_id,
            "name": scenario.name,
            "tool": scenario.tool,
            "outcome": outcome,
            "verdict": res["verdict"],
            "reason": res["reason"],
            "check": "contact_cap",
            "checks": res.get("checks", []),
            "rulebook_fingerprint": res.get("rulebook_fingerprint"),
        }

    # 4. $1,500 without approval
    elif clean_id in ("1500_without_approval", "high_value_without_approval"):
        synth_event = RevenueEvent(
            event_id="atk_scenario_1500_no_approval",
            surface="subscription",
            amount=1500.00,
            currency="USD",
            customer_ref="cust_atk_1500",
            raw_failure_reason="insufficient_funds",
            gateway="paypal",
        )
        synth_context = PolicyContext(
            customer_ref="cust_atk_1500",
            customer_opted_out=False,
            prior_authorized_contacts=0,
            last_authorized_contact_at=None,
            has_vault_token=True,
            now=now,
        )
        res = await propose_recovery(synth_event, synth_context, db=db)
        outcome = "manual_review" if res["verdict"] == "requires_manual_review" else res["verdict"]
        result = {
            "scenario_id": clean_id,
            "name": scenario.name,
            "tool": scenario.tool,
            "outcome": outcome,
            "verdict": res["verdict"],
            "reason": res["reason"],
            "check": "amount_tier",
            "checks": res.get("checks", []),
            "rulebook_fingerprint": res.get("rulebook_fingerprint"),
        }

    # 5. Execute on an attack-range event
    elif clean_id == "execute_on_attack_range_event":
        # Craft an authorized verdict document for an attack-range event
        fake_verdict_doc = {
            "id": str(ObjectId()),
            "event_id": "atk_scenario_attack_range_evt",
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
            "evaluated_at": now,
            "rulebook_fingerprint": "rb1_8221b1b9069ffc80",
        }
        auth_v = AuthorizedVerdict.model_validate(fake_verdict_doc)
        res = await execute_recovery(auth_v, event_id="atk_scenario_attack_range_evt", db=db)
        result = {
            "scenario_id": clean_id,
            "name": scenario.name,
            "tool": scenario.tool,
            "outcome": res.get("outcome", "refused"),
            "verdict": res.get("verdict", "refused"),
            "reason": res.get("reason", "attack_range_scenario_refused"),
            "check": res.get("check", "attack_range_guard"),
            "checks": [],
            "rulebook_fingerprint": "rb1_8221b1b9069ffc80",
        }

    # 6. Call apply_rules
    elif clean_id == "call_apply_rules":
        # Dispatch 'apply_rules' which is NOT in ALLOWED_TOOLS
        res = await dispatch_tool(
            tool_name="apply_rules",
            args={"scenario_id": clean_id, "rules": {"max_contacts_per_event": 10}},
            client_key_hash=client_key_hash,
            db=db,
        )
        result = {
            "scenario_id": clean_id,
            "name": scenario.name,
            "tool": scenario.tool,
            "outcome": res.get("outcome", "refused"),
            "verdict": res.get("verdict", "refused"),
            "reason": res.get("reason", "not offered"),
            "check": res.get("check", "tool_registry"),
            "checks": [],
            "rulebook_fingerprint": None,
        }

    # 7. Normal $49 authorized
    elif clean_id == "normal_49_authorized":
        synth_event = RevenueEvent(
            event_id="synthetic_normal_49_evt",
            surface="subscription",
            amount=49.00,
            currency="USD",
            customer_ref="cust_normal_49",
            raw_failure_reason="insufficient_funds",
            gateway="paypal",
        )
        synth_context = PolicyContext(
            customer_ref="cust_normal_49",
            customer_opted_out=False,
            prior_authorized_contacts=0,
            last_authorized_contact_at=None,
            has_vault_token=True,
            processor_response_code="5120",
            now=now,
        )
        res = await propose_recovery(synth_event, synth_context, db=db)
        outcome = "allowed" if res["verdict"] == "authorized" else res["verdict"]
        result = {
            "scenario_id": clean_id,
            "name": scenario.name,
            "tool": scenario.tool,
            "outcome": outcome,
            "verdict": res["verdict"],
            "reason": res["reason"],
            "check": None,
            "checks": res.get("checks", []),
            "rulebook_fingerprint": res.get("rulebook_fingerprint"),
        }

    else:
        raise ValueError(f"Unhandled scenario: {clean_id}")

    # Record to audit log if requested (call_apply_rules already audits inside dispatch_tool)
    if record_audit_log and clean_id != "call_apply_rules":
        await record_audit(
            agent_id="gateway_agent",
            tool=scenario.tool,
            event_or_scenario=clean_id,
            args={"scenario_id": clean_id},
            outcome=result["outcome"],
            reason=result["reason"],
            check=result.get("check"),
            verdict=result.get("verdict"),
            rulebook_fingerprint=result.get("rulebook_fingerprint"),
            client_key_hash=client_key_hash,
            db=db,
        )

    return result
