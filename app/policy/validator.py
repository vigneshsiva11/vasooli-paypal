"""Validation, safety caps, and invariant guards for merchant plain-English rule proposals.

Pure, deterministic, offline.
Zero LLM, zero network, zero database.
Rejects violations with explicit explanations — never silently clamps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.policy.rulebook import (
    COOLDOWN_FROM_EXECUTION,
    Rulebook,
    fingerprint_of,
)
from app.policy.rules import (
    CONTACT_INTERVENTIONS,
    DO_NOT_RETRY_RESPONSE_CODES,
    NO_ACTION_INTERVENTIONS,
    POLICY_CHECKS,
    USD_FX_RATE_INR_PER_USD,
    USD_INTERVENTION_COSTS_TUPLE,
    USD_REASON_PRECEDENCE,
    USD_REASON_VERDICT,
    current_usd_rulebook,
)

# ---------------------------------------------------------------------------
# Hard Caps and Floors (Enforced in Code, Never Clamped)
# ---------------------------------------------------------------------------

CAPS = {
    "max_contacts_per_event": {"min": 1, "max": 5},
    "cooldown_hours": {"min": 6, "max": 168},
    "auto_authorize_below": {"min": 0.0, "max": 500.0},
    "never_auto_at_or_above": {"min": 50.0, "max": 5000.0},
    "minimum_erv": {"min": 0.0, "max": 50.0},
    "max_retries_per_event": {"min": 1, "max": 5},
    "retry_cooldown_hours": {"min": 12, "max": 168},
}

SUPPORTED_PARAM_NAMES = frozenset(CAPS.keys())

# Words to numbers for source-phrase cross-checks
WORD_TO_NUMBER: dict[str, float] = {
    "zero": 0, "one": 1, "once": 1, "two": 2, "twice": 2, "three": 3, "thrice": 3,
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "twelve": 12, "twenty": 20, "twenty-four": 24, "forty-eight": 48, "seventy-two": 72,
    "hundred": 100, "thousand": 1000,
}


@dataclass(frozen=True)
class RulebookProposalValidation:
    valid: bool
    proposed_parameters: dict[str, Any]
    parameter_sources: dict[str, str]
    errors: list[str]
    diff: dict[str, dict[str, Any]]
    current_fingerprint: str
    prospective_fingerprint: str | None
    not_applied: list[dict[str, str]]


def extract_numbers_from_phrase(phrase: str) -> list[float]:
    """Find all numbers in phrase as digits, decimals, currencies, or English words."""
    cleaned = phrase.lower().replace("$", " ").replace("₹", " ")
    # Match digit sequences like 48, 2.5, 100
    digit_matches = re.findall(r"\b\d+(?:\.\d+)?\b", cleaned)
    found = [float(x) for x in digit_matches]

    # Handle 'days' conversion (e.g. '2 days' -> 48)
    day_matches = re.findall(r"(\d+(?:\.\d+)?)\s*(?:day|days)\b", cleaned)
    for dm in day_matches:
        found.append(float(dm) * 24.0)

    # Word-based numbers
    words = re.findall(r"\b[a-z\-]+\b", cleaned)
    for i, w in enumerate(words):
        if w in WORD_TO_NUMBER:
            val = WORD_TO_NUMBER[w]
            found.append(val)
            # e.g., 'two days'
            if i + 1 < len(words) and words[i + 1] in ("day", "days"):
                found.append(val * 24.0)
    return found


def cross_check_number_in_source(val: float | int, phrase: str, param_name: str) -> bool:
    """Ensure the proposed numeric value is grounded in the source phrase."""
    target = float(val)
    candidates = extract_numbers_from_phrase(phrase)
    # Check direct match with reasonable tolerance for floats
    for c in candidates:
        if abs(c - target) < 1e-4:
            return True
    return False


def validate_rule_proposal(
    *,
    raw_text: str,
    proposed_parameters: dict[str, Any],
    parameter_sources: dict[str, str],
    not_applied: list[dict[str, str]] | None = None,
    active_rulebook: Rulebook | None = None,
) -> RulebookProposalValidation:
    """Deterministically validate proposed parameters against hard caps and invariants.

    Never clamps values; violations produce explicit errors.
    """
    errors: list[str] = []
    base_book = active_rulebook if active_rulebook is not None else current_usd_rulebook()
    current_fp = base_book.fingerprint

    # 1. Invariant: Currency must be USD, never INR
    raw_lower = raw_text.lower()
    if re.search(r"\b(?:inr|rupee|rupees|₹)\b", raw_lower):
        errors.append("INR rulebook is immutable and cannot be targeted or modified.")

    # 2. Invariant: Customer opt-out and policy checks cannot be disabled
    if any(term in raw_lower for term in [
        "disable opt-out", "remove opt-out", "ignore opt-out", "skip opt-out",
        "disable policy checks", "bypass policy", "admin mode"
    ]):
        errors.append("Customer opt-out and core safety checks are immutable invariants and cannot be disabled.")

    validated_params: dict[str, Any] = {}
    validated_sources: dict[str, str] = {}

    # Check each parameter
    for param, val in proposed_parameters.items():
        if val is None:
            continue
        if param not in SUPPORTED_PARAM_NAMES:
            errors.append(f"Parameter '{param}' is unsupported or unknown.")
            continue

        phrase = parameter_sources.get(param, "")
        if not phrase or not phrase.strip():
            errors.append(f"Parameter '{param}' is missing a verbatim source phrase.")
            continue

        # Check verbatim substring in raw_text (normalized whitespace and case)
        norm_raw = " ".join(raw_text.lower().split())
        norm_phrase = " ".join(phrase.lower().split())
        if norm_phrase not in norm_raw:
            errors.append(
                f"Source phrase '{phrase}' for parameter '{param}' does not appear verbatim in input text."
            )
            continue

        # Check numeric grounding
        if not cross_check_number_in_source(val, phrase, param):
            errors.append(
                f"Proposed value {val} for '{param}' cannot be grounded in source phrase '{phrase}'."
            )
            continue

        # Check hard caps (Never clamp!)
        cap = CAPS[param]
        fval = float(val)
        if fval < cap["min"] or fval > cap["max"]:
            errors.append(
                f"Parameter '{param}' value {val} violates safety limits: must be between {cap['min']} and {cap['max']}."
            )
            continue

        validated_params[param] = int(val) if isinstance(cap["min"], int) and isinstance(cap["max"], int) and float(val).is_integer() else fval
        validated_sources[param] = phrase

    # Cross-parameter sanity checks: never_auto must exceed auto_authorize_below
    auto_auth = validated_params.get("auto_authorize_below", base_book.auto_authorize_below)
    never_auto = validated_params.get("never_auto_at_or_above", base_book.never_auto_at_or_above)
    if never_auto <= auto_auth:
        errors.append(
            f"never_auto_at_or_above ({never_auto}) must exceed auto_authorize_below ({auto_auth})."
        )

    # Compute diff
    diff: dict[str, dict[str, Any]] = {}
    for p, nval in validated_params.items():
        oval = getattr(base_book, p, None)
        if oval != nval:
            diff[p] = {"current": oval, "proposed": nval}

    # Prospective Rulebook and Fingerprint
    prospective_fp: str | None = None
    if not errors:
        try:
            prospective_book = Rulebook(
                minimum_erv=float(validated_params.get("minimum_erv", base_book.minimum_erv)),
                zero_cost_exempt_from_erv_floor=base_book.zero_cost_exempt_from_erv_floor,
                auto_authorize_below=float(validated_params.get("auto_authorize_below", base_book.auto_authorize_below)),
                never_auto_at_or_above=float(validated_params.get("never_auto_at_or_above", base_book.never_auto_at_or_above)),
                tier_currency="USD",
                contact_interventions=base_book.contact_interventions,
                max_contacts_per_event=int(validated_params.get("max_contacts_per_event", base_book.max_contacts_per_event)),
                cooldown_hours=int(validated_params.get("cooldown_hours", base_book.cooldown_hours)),
                cooldown_measured_from=base_book.cooldown_measured_from,
                no_action_interventions=base_book.no_action_interventions,
                policy_checks=base_book.policy_checks,
                reason_precedence=base_book.reason_precedence,
                reason_verdict=base_book.reason_verdict,
                fx_rate_inr_per_usd=base_book.fx_rate_inr_per_usd,
                intervention_costs=base_book.intervention_costs,
                do_not_retry_response_codes=base_book.do_not_retry_response_codes,
                max_retries_per_event=(
                    int(validated_params["max_retries_per_event"])
                    if "max_retries_per_event" in validated_params
                    else getattr(base_book, "max_retries_per_event", None)
                ),
                retry_cooldown_hours=(
                    int(validated_params["retry_cooldown_hours"])
                    if "retry_cooldown_hours" in validated_params
                    else getattr(base_book, "retry_cooldown_hours", None)
                ),
                note=f"USD profile updated via merchant proposal",
            )
            prospective_fp = prospective_book.fingerprint
        except Exception as e:
            errors.append(f"Structural rulebook construction error: {e}")

    return RulebookProposalValidation(
        valid=len(errors) == 0,
        proposed_parameters=validated_params if not errors else {},
        parameter_sources=validated_sources if not errors else {},
        errors=errors,
        diff=diff if not errors else {},
        current_fingerprint=current_fp,
        prospective_fingerprint=prospective_fp,
        not_applied=not_applied or [],
    )
