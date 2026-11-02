"""Audit logging and persistence for agent tool calls and scenario attempts.

Maintains the `agent_audit` collection with structured records, indexed for
fast lookups and bound by a 30-day TTL index.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING

from app.db import get_database
from app.sanitizer import sanitize_public_dict

logger = logging.getLogger(__name__)

AUDIT_COLLECTION = "agent_audit"
TTL_30_DAYS_SECONDS = 30 * 24 * 3600  # 2,592,000 seconds

OutcomeType = Literal["allowed", "blocked", "manual_review", "refused", "error"]

# In-memory buffer for offline test execution when MongoDB is not connected
_OFFLINE_AUDIT_LOG: list[dict[str, Any]] = []


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def hash_args(args: Any) -> str:
    """Generate deterministic SHA256 hex digest of arguments."""
    try:
        serialized = json.dumps(args, sort_keys=True, default=str)
    except Exception:
        serialized = str(args)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class AgentAuditRecord(BaseModel):
    """Structured record stored in `agent_audit` collection."""

    model_config = ConfigDict(extra="ignore")

    agent_id: str = Field(default="gateway_agent", description="Identifier of the executing agent.")
    tool: str = Field(..., description="Tool or capability invoked.")
    event_or_scenario: str = Field(..., description="Target event ID or catalogue scenario ID.")
    args_hash: str = Field(..., description="SHA256 hex digest of serialized arguments.")
    outcome: OutcomeType = Field(..., description="Result outcome of the call.")
    reason: str = Field(..., description="Primary reason code or refusal explanation.")
    check: Optional[str] = Field(default=None, description="Check name contributing to decision if applicable.")
    verdict: Optional[str] = Field(default=None, description="Policy verdict string if applicable.")
    rulebook_fingerprint: Optional[str] = Field(default=None, description="Rulebook fingerprint if applicable.")
    at: datetime = Field(default_factory=_utc_now, description="Timestamp in UTC.")
    client_key_hash: str = Field(default="local", description="Salted hash of client IP.")


async def ensure_indexes(db: Any = None) -> None:
    """Ensure compound and TTL indexes on agent_audit collection."""
    try:
        database = db if db is not None else get_database()
        if database is None:
            return
        col = database[AUDIT_COLLECTION]
        # 30-day TTL index on 'at'
        await col.create_index(
            [("at", ASCENDING)],
            expireAfterSeconds=TTL_30_DAYS_SECONDS,
            name="ttl_agent_audit_30d",
        )
        await col.create_index(
            [("client_key_hash", ASCENDING), ("at", DESCENDING)],
            name="idx_agent_audit_client_at",
        )
        await col.create_index([("event_or_scenario", ASCENDING)], name="idx_agent_audit_event_scenario")
        await col.create_index([("tool", ASCENDING)], name="idx_agent_audit_tool")
        await col.create_index([("at", DESCENDING)], name="idx_agent_audit_at_desc")
        logger.info("Ensured indexes on %s", AUDIT_COLLECTION)
    except Exception as exc:
        logger.warning("Could not ensure indexes on %s: %s", AUDIT_COLLECTION, exc)


async def record_audit(
    *,
    tool: str,
    event_or_scenario: str,
    args: Any,
    outcome: OutcomeType,
    reason: str,
    check: str | None = None,
    verdict: str | None = None,
    rulebook_fingerprint: str | None = None,
    client_key_hash: str = "local",
    agent_id: str = "gateway_agent",
    at: datetime | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Record an audit row to agent_audit collection (or in-memory if offline)."""
    ts = at or _utc_now()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    args_h = hash_args(args)
    record = AgentAuditRecord(
        agent_id=agent_id,
        tool=tool,
        event_or_scenario=event_or_scenario,
        args_hash=args_h,
        outcome=outcome,
        reason=reason,
        check=check,
        verdict=verdict,
        rulebook_fingerprint=rulebook_fingerprint,
        at=ts,
        client_key_hash=client_key_hash,
    )
    doc = record.model_dump()

    database = db
    if database is None:
        try:
            database = get_database()
        except Exception:
            database = None

    if database is not None:
        try:
            await database[AUDIT_COLLECTION].insert_one(dict(doc))
        except Exception as exc:
            logger.warning("Failed to persist agent_audit record: %s", exc)
            _OFFLINE_AUDIT_LOG.append(dict(doc))
    else:
        _OFFLINE_AUDIT_LOG.append(dict(doc))

    return doc


async def get_recent_audit_records(limit: int = 50, db: Any = None) -> list[dict[str, Any]]:
    """Retrieve recent audit records up to limit (max 50), sanitized."""
    capped_limit = max(1, min(limit, 50))
    database = db
    if database is None:
        try:
            database = get_database()
        except Exception:
            database = None

    results: list[dict[str, Any]] = []
    if database is not None:
        try:
            cursor = database[AUDIT_COLLECTION].find({}, {"_id": 0}).sort("at", DESCENDING).limit(capped_limit)
            results = await cursor.to_list(length=capped_limit)
        except Exception as exc:
            logger.warning("Error reading agent_audit: %s", exc)
            results = list(reversed(_OFFLINE_AUDIT_LOG))[:capped_limit]
    else:
        results = list(reversed(_OFFLINE_AUDIT_LOG))[:capped_limit]

    return [sanitize_public_dict(r) for r in results]


def clear_offline_audit_log() -> None:
    """Clear in-memory audit log for testing."""
    _OFFLINE_AUDIT_LOG.clear()

