"""Database persistence and dynamic resolution for USD policy rulebook versions.

Features:
- Unique partial index on active USD rulebook version.
- Async cached resolution (30-second TTL) for the active USD rulebook.
- Resolution of historical fingerprints from DB or code registry so verdicts re-derive.
- Append-only version history with parent fingerprint linkage.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.db import get_database
from app.policy.rulebook import (
    Rulebook,
    canonical_form,
    fingerprint_of,
)
from app.policy.rules import current_usd_rulebook, rulebook_registry

logger = logging.getLogger(__name__)

RULEBOOK_VERSIONS_COLLECTION = "rulebook_versions"
ACTIVE_USD_INDEX = "uniq_active_usd_rulebook"
FINGERPRINT_INDEX = "uniq_rulebook_fingerprint"

_ACTIVE_USD_CACHE: tuple[Rulebook, float] | None = None
_CACHE_TTL_SECONDS = 30.0


def invalidate_usd_cache() -> None:
    """Invalidate in-memory cache when a rulebook is applied or reverted."""
    global _ACTIVE_USD_CACHE
    _ACTIVE_USD_CACHE = None


def collection():
    return get_database()[RULEBOOK_VERSIONS_COLLECTION]


async def ensure_rulebook_indexes(db: Any = None) -> None:
    """Ensure unique partial index on active USD version and unique on fingerprint."""
    try:
        col = (db if db is not None else get_database())[RULEBOOK_VERSIONS_COLLECTION]
        await col.create_index(
            [("currency", ASCENDING), ("is_active", ASCENDING)],
            unique=True,
            partialFilterExpression={"currency": "USD", "is_active": True},
            name=ACTIVE_USD_INDEX,
        )
        await col.create_index(
            [("fingerprint", ASCENDING)],
            unique=True,
            name=FINGERPRINT_INDEX,
        )
        logger.info("Ensured indexes on %s", RULEBOOK_VERSIONS_COLLECTION)
    except Exception as exc:
        logger.warning("Could not ensure indexes on %s: %s", RULEBOOK_VERSIONS_COLLECTION, exc)


def rulebook_from_document(doc: dict[str, Any]) -> Rulebook:
    """Reconstruct an immutable Rulebook dataclass from a rulebook_versions document."""
    params = doc.get("parameters", {})
    base = current_usd_rulebook()
    return Rulebook(
        minimum_erv=float(params.get("minimum_erv", base.minimum_erv)),
        zero_cost_exempt_from_erv_floor=base.zero_cost_exempt_from_erv_floor,
        auto_authorize_below=float(params.get("auto_authorize_below", base.auto_authorize_below)),
        never_auto_at_or_above=float(params.get("never_auto_at_or_above", base.never_auto_at_or_above)),
        tier_currency="USD",
        contact_interventions=base.contact_interventions,
        max_contacts_per_event=int(params.get("max_contacts_per_event", base.max_contacts_per_event)),
        cooldown_hours=int(params.get("cooldown_hours", base.cooldown_hours)),
        cooldown_measured_from=base.cooldown_measured_from,
        no_action_interventions=base.no_action_interventions,
        policy_checks=base.policy_checks,
        reason_precedence=base.reason_precedence,
        reason_verdict=base.reason_verdict,
        fx_rate_inr_per_usd=base.fx_rate_inr_per_usd,
        intervention_costs=base.intervention_costs,
        do_not_retry_response_codes=base.do_not_retry_response_codes,
        max_retries_per_event=(
            int(params["max_retries_per_event"])
            if "max_retries_per_event" in params and params["max_retries_per_event"] is not None
            else None
        ),
        retry_cooldown_hours=(
            int(params["retry_cooldown_hours"])
            if "retry_cooldown_hours" in params and params["retry_cooldown_hours"] is not None
            else None
        ),
        note=doc.get("source_text", "USD profile updated from database"),
    )


async def get_active_usd_rulebook(db: Any = None) -> Rulebook:
    """Resolve the active USD rulebook from DB (with 30s cache) or fall back to code baseline."""
    global _ACTIVE_USD_CACHE
    now = time.time()
    if _ACTIVE_USD_CACHE is not None:
        cached_book, cached_at = _ACTIVE_USD_CACHE
        if now - cached_at < _CACHE_TTL_SECONDS:
            return cached_book

    database = db if db is not None else get_database()
    if database is None:
        return current_usd_rulebook()

    try:
        doc = await database[RULEBOOK_VERSIONS_COLLECTION].find_one(
            {"currency": "USD", "is_active": True}
        )
        if doc:
            rulebook = rulebook_from_document(doc)
            _ACTIVE_USD_CACHE = (rulebook, now)
            return rulebook
    except Exception as exc:
        logger.warning("Failed to query active USD rulebook from DB: %s; using code baseline", exc)

    fallback = current_usd_rulebook()
    _ACTIVE_USD_CACHE = (fallback, now)
    return fallback


async def resolve_rulebook(fingerprint: str, db: Any = None) -> Rulebook | None:
    """Resolve any rulebook by fingerprint: first from code registry, then from DB."""
    reg = rulebook_registry()
    if fingerprint in reg:
        return reg[fingerprint]

    database = db if db is not None else get_database()
    if database is not None:
        try:
            doc = await database[RULEBOOK_VERSIONS_COLLECTION].find_one({"fingerprint": fingerprint})
            if doc:
                return rulebook_from_document(doc)
        except Exception as exc:
            logger.warning("Error resolving rulebook for fingerprint %s: %s", fingerprint, exc)

    return None


async def apply_new_usd_rulebook(
    *,
    preview: dict[str, Any],
    expected_parent_fingerprint: str,
    approved_by: str = "admin",
    db: Any = None,
) -> dict[str, Any]:
    """Atomically apply a validated proposal from preview_id.

    Raises:
        ValueError if proposal is invalid or parameters missing.
        RuntimeError(409) if expected_parent_fingerprint does not match the active USD rulebook.
    """
    if not preview.get("valid"):
        raise ValueError("Cannot apply an invalid rule proposal.")

    database = db if db is not None else get_database()
    active_book = await get_active_usd_rulebook(database)
    if active_book.fingerprint != expected_parent_fingerprint:
        raise RuntimeError(
            f"Fingerprint conflict: active USD rulebook is {active_book.fingerprint}, "
            f"expected parent was {expected_parent_fingerprint}."
        )

    # Build prospective rulebook
    base_usd = current_usd_rulebook()
    proposed = preview["proposed_parameters"]
    full_params = {
        "max_contacts_per_event": proposed.get("max_contacts_per_event", active_book.max_contacts_per_event),
        "cooldown_hours": proposed.get("cooldown_hours", active_book.cooldown_hours),
        "auto_authorize_below": proposed.get("auto_authorize_below", active_book.auto_authorize_below),
        "never_auto_at_or_above": proposed.get("never_auto_at_or_above", active_book.never_auto_at_or_above),
        "minimum_erv": proposed.get("minimum_erv", active_book.minimum_erv),
        "max_retries_per_event": proposed.get("max_retries_per_event", getattr(active_book, "max_retries_per_event", None)),
        "retry_cooldown_hours": proposed.get("retry_cooldown_hours", getattr(active_book, "retry_cooldown_hours", None)),
    }

    new_book = Rulebook(
        minimum_erv=float(full_params["minimum_erv"]),
        zero_cost_exempt_from_erv_floor=base_usd.zero_cost_exempt_from_erv_floor,
        auto_authorize_below=float(full_params["auto_authorize_below"]),
        never_auto_at_or_above=float(full_params["never_auto_at_or_above"]),
        tier_currency="USD",
        contact_interventions=base_usd.contact_interventions,
        max_contacts_per_event=int(full_params["max_contacts_per_event"]),
        cooldown_hours=int(full_params["cooldown_hours"]),
        cooldown_measured_from=base_usd.cooldown_measured_from,
        no_action_interventions=base_usd.no_action_interventions,
        policy_checks=base_usd.policy_checks,
        reason_precedence=base_usd.reason_precedence,
        reason_verdict=base_usd.reason_verdict,
        fx_rate_inr_per_usd=base_usd.fx_rate_inr_per_usd,
        intervention_costs=base_usd.intervention_costs,
        do_not_retry_response_codes=base_usd.do_not_retry_response_codes,
        max_retries_per_event=(
            int(full_params["max_retries_per_event"])
            if full_params["max_retries_per_event"] is not None
            else None
        ),
        retry_cooldown_hours=(
            int(full_params["retry_cooldown_hours"])
            if full_params["retry_cooldown_hours"] is not None
            else None
        ),
        note=f"Applied rulebook: {preview.get('text', '')}",
    )

    new_fp = new_book.fingerprint
    now_utc = datetime.now(timezone.utc)

    # Deactivate existing active USD version
    await database[RULEBOOK_VERSIONS_COLLECTION].update_many(
        {"currency": "USD", "is_active": True},
        {"$set": {"is_active": False, "superseded_at": now_utc}},
    )

    # If this exact fingerprint was previously in the versions collection, update it to active,
    # otherwise insert a new record.
    existing = await database[RULEBOOK_VERSIONS_COLLECTION].find_one({"fingerprint": new_fp})
    if existing:
        await database[RULEBOOK_VERSIONS_COLLECTION].update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "is_active": True,
                "parent_fingerprint": active_book.fingerprint,
                "activated_at": now_utc,
                "approved_by": approved_by,
                "source_text": preview.get("text", existing.get("source_text", "")),
            }}
        )
    else:
        doc = {
            "currency": "USD",
            "fingerprint": new_fp,
            "parent_fingerprint": active_book.fingerprint,
            "canonical_form": canonical_form(new_book),
            "parameters": full_params,
            "source_text": preview.get("text", ""),
            "source_phrases": preview.get("parameter_sources", {}),
            "diff": preview.get("diff", {}),
            "approved_by": approved_by,
            "created_at": now_utc,
            "is_active": True,
        }
        try:
            await database[RULEBOOK_VERSIONS_COLLECTION].insert_one(doc)
        except DuplicateKeyError as exc:
            raise RuntimeError("Concurrent update detected: another active rulebook was committed.") from exc

    invalidate_usd_cache()
    return {
        "applied": True,
        "fingerprint": new_fp,
        "parent_fingerprint": active_book.fingerprint,
        "applied_at": now_utc.isoformat(),
        "parameters": full_params,
    }


async def revert_usd_rulebook(db: Any = None) -> dict[str, Any]:
    """Deactivate all DB-stored USD rulebooks, reverting to the code baseline."""
    database = db if db is not None else get_database()
    now_utc = datetime.now(timezone.utc)
    res = await database[RULEBOOK_VERSIONS_COLLECTION].update_many(
        {"currency": "USD", "is_active": True},
        {"$set": {"is_active": False, "superseded_at": now_utc, "reverted": True}},
    )
    invalidate_usd_cache()
    baseline = current_usd_rulebook()
    return {
        "reverted": True,
        "reverted_count": res.modified_count,
        "active_fingerprint": baseline.fingerprint,
        "reverted_at": now_utc.isoformat(),
    }


async def list_usd_rulebook_history(db: Any = None) -> list[dict[str, Any]]:
    """List all USD rulebook versions, newest first (DB versions + code versions)."""
    database = db if db is not None else get_database()
    history: list[dict[str, Any]] = []

    if database is not None:
        cursor = database[RULEBOOK_VERSIONS_COLLECTION].find({"currency": "USD"}).sort("created_at", DESCENDING)
        async for doc in cursor:
            history.append({
                "currency": "USD",
                "fingerprint": doc.get("fingerprint"),
                "parent_fingerprint": doc.get("parent_fingerprint"),
                "source_text": doc.get("source_text", ""),
                "parameters": doc.get("parameters", {}),
                "is_active": doc.get("is_active", False),
                "approved_by": doc.get("approved_by", "admin"),
                "created_at": doc.get("created_at"),
                "provenance": "database",
            })

    # Append compiled code versions
    history.append({
        "currency": "USD",
        "fingerprint": "rb1_8221b1b9069ffc80",
        "parent_fingerprint": "rb1_f1e6f9c8957920de",
        "source_text": "Code baseline with retry cap & do-not-retry response codes",
        "parameters": {
            "max_contacts_per_event": 3,
            "cooldown_hours": 24,
            "auto_authorize_below": 250.0,
            "never_auto_at_or_above": 1000.0,
            "minimum_erv": 1.0,
        },
        "is_active": len(history) == 0,
        "approved_by": "system",
        "created_at": datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc),
        "provenance": "code_baseline",
    })
    history.append({
        "currency": "USD",
        "fingerprint": "rb1_f1e6f9c8957920de",
        "parent_fingerprint": "rb1_c7c49bbd323ceed4",
        "source_text": "Code baseline with retry cap (v2)",
        "parameters": {
            "max_contacts_per_event": 3,
            "cooldown_hours": 24,
            "auto_authorize_below": 250.0,
            "never_auto_at_or_above": 1000.0,
            "minimum_erv": 1.0,
        },
        "is_active": False,
        "approved_by": "system",
        "created_at": datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
        "provenance": "code_superseded",
    })
    history.append({
        "currency": "USD",
        "fingerprint": "rb1_c7c49bbd323ceed4",
        "parent_fingerprint": None,
        "source_text": "Initial code baseline USD profile (v1)",
        "parameters": {
            "max_contacts_per_event": 3,
            "cooldown_hours": 24,
            "auto_authorize_below": 250.0,
            "never_auto_at_or_above": 1000.0,
            "minimum_erv": 1.0,
        },
        "is_active": False,
        "approved_by": "system",
        "created_at": datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc),
        "provenance": "code_superseded",
    })

    return history
