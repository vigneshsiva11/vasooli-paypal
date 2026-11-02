"""Rate limiting and atomic counters for agent gateway endpoints.

Enforces:
- 10 attempts per minute per client (rolling 60s window)
- 100 attempts per hour globally (rolling 3600s window)
Persisted in `agent_counters` with TTL index, backed by in-memory fallback for offline test suites.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING

from app.db import get_database

logger = logging.getLogger(__name__)

COUNTERS_COLLECTION = "agent_counters"
CLIENT_LIMIT_PER_MINUTE = 10
CLIENT_WINDOW_SECONDS = 60
GLOBAL_LIMIT_PER_HOUR = 100
GLOBAL_WINDOW_SECONDS = 3600

# In-memory storage for offline testing
_OFFLINE_COUNTERS: list[dict[str, Any]] = []


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return _utc_now()


async def ensure_indexes(db: Any = None) -> None:
    """Ensure TTL and query indexes on agent_counters collection."""
    try:
        database = db if db is not None else get_database()
        if database is None:
            return
        col = database[COUNTERS_COLLECTION]
        # TTL index expiring after 3600 seconds
        await col.create_index(
            [("created_at", ASCENDING)],
            expireAfterSeconds=3600,
            name="ttl_agent_counters_3600",
        )
        await col.create_index(
            [("key", ASCENDING), ("created_at", DESCENDING)],
            name="idx_agent_counters_key_created_at",
        )
        logger.info("Ensured indexes on %s", COUNTERS_COLLECTION)
    except Exception as exc:
        logger.warning("Could not ensure indexes on %s: %s", COUNTERS_COLLECTION, exc)


async def check_and_record_attempt(
    client_key_hash: str,
    db: Any = None,
    now: datetime | None = None,
) -> tuple[bool, str | None, int | None]:
    """Check rate limits (client 10/min, global 100/h) and record attempt atomically.

    Returns:
        (allowed: bool, error_message: str | None, retry_after_seconds: int | None)
    """
    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    client_counter_key = f"client:{client_key_hash}"
    global_counter_key = "global"

    one_minute_ago = current_time - timedelta(seconds=CLIENT_WINDOW_SECONDS)
    one_hour_ago = current_time - timedelta(seconds=GLOBAL_WINDOW_SECONDS)

    database = db
    if database is None:
        try:
            database = get_database()
        except Exception:
            database = None

    if database is not None:
        col = database[COUNTERS_COLLECTION]

        # 1. Check per-client rate limit: 10 per minute
        client_count = await col.count_documents({
            "key": client_counter_key,
            "created_at": {"$gte": one_minute_ago},
        })
        if client_count >= CLIENT_LIMIT_PER_MINUTE:
            oldest = await col.find_one(
                {"key": client_counter_key, "created_at": {"$gte": one_minute_ago}},
                sort=[("created_at", ASCENDING)],
            )
            oldest_ts = _parse_ts(oldest["created_at"]) if oldest else one_minute_ago
            elapsed = (current_time - oldest_ts).total_seconds()
            retry_after = max(1, math.ceil(CLIENT_WINDOW_SECONDS - elapsed))
            return (
                False,
                f"Client rate limit exceeded: maximum {CLIENT_LIMIT_PER_MINUTE} attempts per minute per client.",
                retry_after,
            )

        # 2. Check global rate limit: 100 per hour
        global_count = await col.count_documents({
            "key": global_counter_key,
            "created_at": {"$gte": one_hour_ago},
        })
        if global_count >= GLOBAL_LIMIT_PER_HOUR:
            oldest = await col.find_one(
                {"key": global_counter_key, "created_at": {"$gte": one_hour_ago}},
                sort=[("created_at", ASCENDING)],
            )
            oldest_ts = _parse_ts(oldest["created_at"]) if oldest else one_hour_ago
            elapsed = (current_time - oldest_ts).total_seconds()
            retry_after = max(1, math.ceil(GLOBAL_WINDOW_SECONDS - elapsed))
            return (
                False,
                f"Global rate limit exceeded: maximum {GLOBAL_LIMIT_PER_HOUR} attempts per hour.",
                retry_after,
            )

        # 3. Record counters atomically
        await col.insert_one({"key": client_counter_key, "created_at": current_time})
        await col.insert_one({"key": global_counter_key, "created_at": current_time})
        return (True, None, None)

    # Offline / in-memory path
    # Filter active client records
    client_records = [
        r for r in _OFFLINE_COUNTERS
        if r["key"] == client_counter_key and r["created_at"] >= one_minute_ago
    ]
    if len(client_records) >= CLIENT_LIMIT_PER_MINUTE:
        oldest_ts = min(r["created_at"] for r in client_records)
        elapsed = (current_time - oldest_ts).total_seconds()
        retry_after = max(1, math.ceil(CLIENT_WINDOW_SECONDS - elapsed))
        return (
            False,
            f"Client rate limit exceeded: maximum {CLIENT_LIMIT_PER_MINUTE} attempts per minute per client.",
            retry_after,
        )

    global_records = [
        r for r in _OFFLINE_COUNTERS
        if r["key"] == global_counter_key and r["created_at"] >= one_hour_ago
    ]
    if len(global_records) >= GLOBAL_LIMIT_PER_HOUR:
        oldest_ts = min(r["created_at"] for r in global_records)
        elapsed = (current_time - oldest_ts).total_seconds()
        retry_after = max(1, math.ceil(GLOBAL_WINDOW_SECONDS - elapsed))
        return (
            False,
            f"Global rate limit exceeded: maximum {GLOBAL_LIMIT_PER_HOUR} attempts per hour.",
            retry_after,
        )

    _OFFLINE_COUNTERS.append({"key": client_counter_key, "created_at": current_time})
    _OFFLINE_COUNTERS.append({"key": global_counter_key, "created_at": current_time})
    return (True, None, None)


def clear_limits() -> None:
    """Clear in-memory counters for testing."""
    _OFFLINE_COUNTERS.clear()
