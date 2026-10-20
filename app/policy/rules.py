"""Policy parameters and the classification of interventions (Stage 4).

Every number here is a ratified policy choice, not a tuning constant. They are
gathered in one module so the whole authority envelope of the agent can be read
at once, and so widening it is a visible one-line diff rather than a change
buried inside branching logic.

These are the values in force *now*. `current_rulebook()` snapshots them into a
`Rulebook`, whose fingerprint every verdict records, and `app/policy/rulebook.py`
archives the sets that have been superseded. Amending anything below therefore
changes the fingerprint, which is what lets the audit tell an old verdict judged
under an older rulebook apart from one that would be decided differently today.

Nothing in this module performs I/O, calls an LLM, or executes anything.
"""

from __future__ import annotations

from datetime import timedelta
from typing import get_args

from app.models import ALLOWED_INTERVENTIONS, NO_ACTION_INTERVENTIONS
from app.models.policy import POLICY_CHECKS, REASON_PRECEDENCE, REASON_VERDICT
from app.policy.rulebook import (
    COOLDOWN_FROM_EXECUTION,
    SUPERSEDED_RULEBOOKS,
    AutonomyTier,
    Rulebook,
    fingerprint_of,
)

# ---------------------------------------------------------------------------
# Economics: the minimum ERV worth acting on.
# ---------------------------------------------------------------------------

#: Below this expected recovery value, acting is not worth the operational
#: overhead even though the arithmetic is positive. Ratified at 25.00 INR.
#:
#: Stage 3 deliberately left this unresolved: it computes whether an action is
#: profitable, which is an arithmetic question, and profitability alone does not
#: make an action worth taking. Judging "worth it" against a threshold is a policy
#: question, so the threshold lives here.
MINIMUM_ERV = 25.0

#: Ratified: zero-cost interventions are EXEMPT from the floor.
#:
#: The floor exists to stop the agent from spending real money chasing trivial
#: upside. A zero-cost intervention (`immediate_retry`, `delayed_retry`) reuses an
#: existing mandate and spends nothing, so there is no downside to weigh against
#: a small gain — its ERV equals its upside and cannot be negative. Applying the
#: floor to it would decline free money.
#:
#: The cost, not the intervention name, is what triggers the exemption: if a retry
#: ever acquires a cost in the matrix, the floor starts applying to it
#: automatically rather than silently continuing to exempt it.
ZERO_COST_EXEMPT_FROM_ERV_FLOOR = True

# ---------------------------------------------------------------------------
# Autonomy: how much money the agent may commit without a human.
# ---------------------------------------------------------------------------

# `AutonomyTier` is defined in `app/policy/rulebook.py`, beside the `tier_for`
# that returns it, and re-exported here so this module still reads as the one
# place the authority envelope is described.

#: Amounts strictly below this may be acted on autonomously.
#: Ratified at 5,000 INR, checked against the real event distribution:
#: min 80, median 2,449.75, mean 18,087.17, max 125,000. This leaves the everyday
#: mass of failures (15 of 22 events) inside autonomous reach.
AUTO_AUTHORIZE_BELOW = 5_000.0

#: Amounts at or above this are never acted on autonomously, whatever the ERV.
#: Ratified at 25,000 INR. The four largest events (48,000 / 75,000 / 90,000 /
#: 125,000) sit above it; the gap between the two thresholds captures the
#: mid-sized tail (6,500 / 7,800 / 22,000).
NEVER_AUTO_AT_OR_ABOVE = 25_000.0

#: Thresholds are compared against `Decision.revenue_at_risk`, which Stage 3
#: pins to the event's `amount` in the event's own currency. Non-INR events are
#: not FX-converted anywhere in the pipeline, so a large foreign-currency amount
#: is tiered by its face value. Every event currently in the system is INR; this
#: is recorded as a known limitation rather than papered over with a guessed rate.
TIER_CURRENCY = "INR"

# ---------------------------------------------------------------------------
# Customer protection: how often a person may be contacted.
# ---------------------------------------------------------------------------

#: Interventions that put a message in front of a human being.
#:
#: Both payment links are RATIFIED as contact-type, on the principle that any link
#: sent to a customer is outreach. This resolves a contradiction between the
#: Stage 4 brief — which named only the three below and explicitly excluded
#: `payment_method_update_link` — and Stage 3's own catalogue, where
#: `app/models/decision.py` groups both links under "customer-contact
#: interventions" and prices them at messaging spend, because a link has to be
#: delivered to somebody. The catalogue wins: sending someone a link is contacting
#: them, so consent gates it and it consumes one of their three contacts.
#:
#: This set and the catalogue's own grouping now agree completely. Every
#: non-zero-cost intervention whose cost is messaging spend is here; the only
#: interventions outside it are the two free retries, which touch the payment rail
#: rather than the person, and the three ways of doing nothing.
CONTACT_INTERVENTIONS: frozenset[str] = frozenset(
    {
        "reminder",
        "escalating_reminder_sequence",
        "manual_escalation",
        "payment_method_update_link",
        "recovery_payment_link",
    }
)

#: Maximum number of authorized contacts for one revenue event, ever.
#: Ratified at 3. Scope is per `event_id` across ALL decision and diagnosis
#: versions: the cap protects a person from being chased about one debt, and
#: re-diagnosing the same failure does not reset how many messages they received.
MAX_CONTACTS_PER_EVENT = 3

#: Minimum gap between authorized contacts for the same event. Ratified at 24h.
COOLDOWN_HOURS = 24
COOLDOWN = timedelta(hours=COOLDOWN_HOURS)

#: RATIFIED, Stage 5: the cooldown is measured from `ExecutionRecord.executed_at` —
#: when a message actually went out — not from when permission was granted.
#:
#: Stage 4 measured from `verdict.evaluated_at` because it sent nothing and had no
#: send timestamp to read. Now there is one, and the difference is not cosmetic: an
#: authorization that is executed an hour late would, under the old anchor, let the
#: next contact go out an hour early.
#:
#: This value is no longer declarative. `prior_authorized_contacts` below branches on
#: it, and so does `scripts/s4_replay.py`, so a verdict evaluated under an archived
#: rulebook is still re-derived against the anchor that judged it rather than against
#: this one. That branch is the whole reason this field was hashed into the
#: fingerprint back in Stage 4 while nothing yet read it.
#:
#: What counts, precisely, once a send time exists:
#:
#:   * authorized but not yet executed — counts against the cap as a RESERVATION,
#:     anchored at `evaluated_at`. Permission to contact someone has been issued and
#:     may still be used, so it cannot be free;
#:   * executed successfully — counts against the cap, anchored at `executed_at`;
#:   * executed and FAILED — counts against neither. Nothing reached the customer,
#:     so nothing should be spent or timed out on their behalf. This is what makes
#:     re-authorization a usable recovery path after a failed send.
COOLDOWN_MEASURED_FROM = COOLDOWN_FROM_EXECUTION


# ---------------------------------------------------------------------------
# USD Profile Parameters (Step 2)
# ---------------------------------------------------------------------------

#: Minimum ERV worth acting on for USD events. Ratified at 1.00 USD.
USD_MINIMUM_ERV = 1.00

#: Amounts strictly below this may be acted on autonomously in USD. Ratified at 250.00 USD.
USD_AUTO_AUTHORIZE_BELOW = 250.0

#: Amounts at or above this are never acted on autonomously in USD. Ratified at 1,000.00 USD.
USD_NEVER_AUTO_AT_OR_ABOVE = 1000.0

USD_TIER_CURRENCY = "USD"

#: Fixed documented exchange rate used for converting intervention costs from INR: 1 USD = 83.00 INR
USD_FX_RATE_INR_PER_USD = 83.0

from app.decision.matrix import USD_INTERVENTION_COSTS

USD_INTERVENTION_COSTS_TUPLE: tuple[tuple[str, float], ...] = tuple(
    sorted(USD_INTERVENTION_COSTS.items())
)

# Preserved historical tables for the INR profile to guarantee byte-identical digest
INR_REASON_PRECEDENCE: tuple[str, ...] = (
    "no_action_recommended",
    "customer_opted_out",
    "contact_cap_exceeded",
    "cooldown_active",
    "erv_below_minimum",
    "amount_never_auto",
    "amount_requires_approval",
)

INR_REASON_VERDICT: tuple[tuple[str, str], ...] = (
    ("amount_never_auto", "requires_manual_review"),
    ("amount_requires_approval", "requires_manual_review"),
    ("contact_cap_exceeded", "blocked"),
    ("cooldown_active", "blocked"),
    ("customer_opted_out", "blocked"),
    ("erv_below_minimum", "blocked"),
    ("no_action_recommended", "blocked"),
    ("ok", "authorized"),
)


# ---------------------------------------------------------------------------
# The rulebook in force.
# ---------------------------------------------------------------------------


def current_inr_rulebook() -> Rulebook:
    """Snapshot the ratified INR policy parameters. Fingerprint is byte-identical."""
    return Rulebook(
        minimum_erv=MINIMUM_ERV,
        zero_cost_exempt_from_erv_floor=ZERO_COST_EXEMPT_FROM_ERV_FLOOR,
        auto_authorize_below=AUTO_AUTHORIZE_BELOW,
        never_auto_at_or_above=NEVER_AUTO_AT_OR_ABOVE,
        tier_currency=TIER_CURRENCY,
        contact_interventions=frozenset(CONTACT_INTERVENTIONS),
        max_contacts_per_event=MAX_CONTACTS_PER_EVENT,
        cooldown_hours=COOLDOWN_HOURS,
        cooldown_measured_from=COOLDOWN_MEASURED_FROM,
        no_action_interventions=frozenset(NO_ACTION_INTERVENTIONS),
        policy_checks=tuple(POLICY_CHECKS),
        reason_precedence=INR_REASON_PRECEDENCE,
        reason_verdict=INR_REASON_VERDICT,
        note="INR profile in force",
    )


def current_usd_rulebook() -> Rulebook:
    """Snapshot the ratified USD policy parameters with fixed 1 USD = 83.00 INR conversion."""
    return Rulebook(
        minimum_erv=USD_MINIMUM_ERV,
        zero_cost_exempt_from_erv_floor=ZERO_COST_EXEMPT_FROM_ERV_FLOOR,
        auto_authorize_below=USD_AUTO_AUTHORIZE_BELOW,
        never_auto_at_or_above=USD_NEVER_AUTO_AT_OR_ABOVE,
        tier_currency=USD_TIER_CURRENCY,
        contact_interventions=frozenset(CONTACT_INTERVENTIONS),
        max_contacts_per_event=MAX_CONTACTS_PER_EVENT,
        cooldown_hours=COOLDOWN_HOURS,
        cooldown_measured_from=COOLDOWN_MEASURED_FROM,
        no_action_interventions=frozenset(NO_ACTION_INTERVENTIONS),
        policy_checks=tuple(POLICY_CHECKS),
        reason_precedence=tuple(REASON_PRECEDENCE),
        reason_verdict=tuple(sorted(REASON_VERDICT.items())),
        fx_rate_inr_per_usd=USD_FX_RATE_INR_PER_USD,
        intervention_costs=USD_INTERVENTION_COSTS_TUPLE,
        note=f"USD profile in force (fixed rate 1 USD = {USD_FX_RATE_INR_PER_USD:.2f} INR)",
    )


def unknown_currency_rulebook(currency: str) -> Rulebook:
    """Fallback rulebook for unsupported currencies that fails closed to manual review."""
    curr = (currency or "UNKNOWN").upper()
    return Rulebook(
        minimum_erv=0.0,
        zero_cost_exempt_from_erv_floor=True,
        auto_authorize_below=0.0,
        never_auto_at_or_above=0.01,
        tier_currency=curr,
        contact_interventions=frozenset(CONTACT_INTERVENTIONS),
        max_contacts_per_event=MAX_CONTACTS_PER_EVENT,
        cooldown_hours=COOLDOWN_HOURS,
        cooldown_measured_from=COOLDOWN_MEASURED_FROM,
        no_action_interventions=frozenset(NO_ACTION_INTERVENTIONS),
        policy_checks=tuple(POLICY_CHECKS),
        reason_precedence=tuple(REASON_PRECEDENCE),
        reason_verdict=tuple(sorted(REASON_VERDICT.items())),
        note=f"Unsupported currency profile: {curr} (fails closed to manual review)",
    )


def current_rulebook(currency: str = "INR") -> Rulebook:
    """Snapshot policy parameters for the requested currency (default INR)."""
    curr = (currency or "INR").upper()
    if curr == "USD":
        return current_usd_rulebook()
    if curr == "INR":
        return current_inr_rulebook()
    return unknown_currency_rulebook(curr)


def current_fingerprint(currency: str = "INR") -> str:
    """The fingerprint of the rulebook in force for the given currency."""
    return fingerprint_of(current_rulebook(currency))


def rulebook_registry() -> dict[str, Rulebook]:
    """Every rulebook this build can identify, keyed by fingerprint."""
    registry = {rulebook.fingerprint: rulebook for rulebook in SUPERSEDED_RULEBOOKS}
    inr = current_inr_rulebook()
    usd = current_usd_rulebook()
    eur = unknown_currency_rulebook("EUR")
    registry[inr.fingerprint] = inr
    registry[usd.fingerprint] = usd
    registry[eur.fingerprint] = eur
    return registry


# ---------------------------------------------------------------------------
# Convenience predicates against the rulebook in force.
# ---------------------------------------------------------------------------


def is_contact_intervention(intervention: str, currency: str = "INR") -> bool:
    """Whether the intervention puts a message in front of a customer, today."""
    return current_rulebook(currency).is_contact(intervention)


def tier_for(amount: float, currency: str = "INR") -> AutonomyTier:
    """Return the autonomy tier for an amount at risk, under today's thresholds."""
    return current_rulebook(currency).tier_for(amount)


def erv_floor_applies(estimated_cost: float, currency: str = "INR") -> bool:
    """Whether today's minimum-ERV floor applies to an action of this cost."""
    return current_rulebook(currency).erv_floor_applies(estimated_cost)


def _validate_parameters() -> None:
    """Reject an internally inconsistent policy configuration at import time.

    The same discipline as the intervention matrix: a policy table that cannot be
    satisfied should fail on the way up, not silently mis-authorize later.
    """
    problems: list[str] = []

    if MINIMUM_ERV < 0:
        problems.append(f"MINIMUM_ERV must not be negative (got {MINIMUM_ERV})")

    if AUTO_AUTHORIZE_BELOW <= 0:
        problems.append(
            f"AUTO_AUTHORIZE_BELOW must be positive (got {AUTO_AUTHORIZE_BELOW})"
        )
    if NEVER_AUTO_AT_OR_ABOVE <= AUTO_AUTHORIZE_BELOW:
        problems.append(
            f"NEVER_AUTO_AT_OR_ABOVE ({NEVER_AUTO_AT_OR_ABOVE}) must exceed "
            f"AUTO_AUTHORIZE_BELOW ({AUTO_AUTHORIZE_BELOW}); otherwise the "
            "approval-required tier is empty and every mid-sized amount is "
            "silently refused rather than reviewed"
        )

    if MAX_CONTACTS_PER_EVENT < 1:
        problems.append(
            f"MAX_CONTACTS_PER_EVENT must be at least 1 (got "
            f"{MAX_CONTACTS_PER_EVENT}); a cap of 0 would block every contact "
            "and should be expressed as an opt-out, not a cap"
        )
    if COOLDOWN_HOURS < 0:
        problems.append(f"COOLDOWN_HOURS must not be negative (got {COOLDOWN_HOURS})")

    unknown = CONTACT_INTERVENTIONS - ALLOWED_INTERVENTIONS
    if unknown:
        problems.append(
            f"CONTACT_INTERVENTIONS names interventions outside the Stage 3 "
            f"catalogue: {sorted(unknown)}"
        )
    overlap = CONTACT_INTERVENTIONS & NO_ACTION_INTERVENTIONS
    if overlap:
        problems.append(
            f"CONTACT_INTERVENTIONS includes no-action variants {sorted(overlap)}; "
            "deciding to do nothing cannot contact anybody"
        )
    if not CONTACT_INTERVENTIONS:
        problems.append(
            "CONTACT_INTERVENTIONS is empty, which would disable the opt-out, "
            "cap and cooldown checks entirely"
        )

    if problems:
        # Raised before anything below builds a `Rulebook`, whose own structural
        # checks would otherwise fire first and report one problem instead of all
        # of them.
        raise RuntimeError(
            "Policy parameters are inconsistent:\n  - " + "\n  - ".join(problems)
        )

    tiers = set(get_args(AutonomyTier))
    reachable = {
        tier_for(AUTO_AUTHORIZE_BELOW - 0.01),
        tier_for(AUTO_AUTHORIZE_BELOW),
        tier_for(NEVER_AUTO_AT_OR_ABOVE),
    }
    if reachable != tiers:
        problems.append(
            f"the thresholds do not reach every tier; reachable={sorted(reachable)} "
            f"declared={sorted(tiers)}"
        )

    # The archive has to stay coherent with the present, or the fingerprint stops
    # meaning anything. Both checks below catch a real mistake in amending policy.
    archived: dict[str, str] = {}
    for rulebook in SUPERSEDED_RULEBOOKS:
        existing = archived.get(rulebook.fingerprint)
        if existing is not None:
            problems.append(
                f"two superseded rulebooks have the same fingerprint "
                f"{rulebook.fingerprint}: {existing!r} and {rulebook.note!r}; they "
                "describe one parameter set, not two"
            )
        archived[rulebook.fingerprint] = rulebook.note

    current = current_rulebook()
    if current.fingerprint in archived:
        problems.append(
            f"the rulebook in force has fingerprint {current.fingerprint}, which "
            f"the archive lists as superseded ({archived[current.fingerprint]!r}). "
            "Either an amendment was archived but never applied to the parameters "
            "above, or one was reverted without removing its archive entry"
        )

    if problems:
        raise RuntimeError(
            "Policy parameters are inconsistent:\n  - " + "\n  - ".join(problems)
        )


_validate_parameters()
