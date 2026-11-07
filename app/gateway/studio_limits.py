"""Rate limiting and counters for Studio AI assistant LLM calls.

Enforces:
- 20 calls/hour per client key (rightmost X-Forwarded-For, else socket address)
- 100 calls/hour globally
- 500 calls/day globally
- Per-turn guard: at most 6 calls per conversation id per 10 minutes
Persisted in `studio_ai_counters` with TTL index, backed by in-memory fallback for offline test suites.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from pymongo import ASCENDING, DESCENDING

from app.config import get_settings
from app.db import get_database

logger = logging.getLogger(__name__)

STUDIO_COUNTERS_COLLECTION = "studio_ai_counters"
CLIENT_LIMIT_PER_HOUR = 20
CLIENT_WINDOW_SECONDS = 3600
GLOBAL_LIMIT_PER_HOUR = 100
GLOBAL_WINDOW_SECONDS = 3600
GLOBAL_LIMIT_PER_DAY = 500
GLOBAL_DAY_WINDOW_SECONDS = 86400
TURN_LIMIT_PER_10MIN = 6
TURN_WINDOW_SECONDS = 600

# In-memory storage for offline testing
_OFFLINE_STUDIO_COUNTERS: list[dict[str, Any]] = []


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def extract_client_key(request: Request) -> str:
    """Extract client key using the rightmost X-Forwarded-For IP, or socket address.

    Spoofed leftmost IPs are discarded; only the rightmost entry added by the
    trusted boundary proxy/CDN is used.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def clear_offline_counters() -> None:
    """Clear in-memory offline counters (for unit tests)."""
    global _OFFLINE_STUDIO_COUNTERS
    _OFFLINE_STUDIO_COUNTERS = []


async def ensure_indexes(db: Any = None) -> None:
    """Ensure TTL and query indexes on studio_ai_counters collection."""
    try:
        database = db if db is not None else get_database()
        if database is None:
            return
        col = database[STUDIO_COUNTERS_COLLECTION]
        # TTL index expiring after 86400 seconds (24 hours)
        await col.create_index(
            [("created_at", ASCENDING)],
            expireAfterSeconds=86400,
            name="ttl_studio_ai_counters_86400",
        )
        await col.create_index(
            [("key", ASCENDING), ("created_at", DESCENDING)],
            name="idx_studio_ai_counters_key_created_at",
        )
        logger.info("Ensured indexes on %s", STUDIO_COUNTERS_COLLECTION)
    except Exception as exc:
        logger.warning("Could not ensure indexes on %s: %s", STUDIO_COUNTERS_COLLECTION, exc)


async def check_and_record_studio_attempt(
    client_key: str,
    conversation_id: str = "default",
    db: Any = None,
    now: datetime | None = None,
) -> tuple[bool, str | None, int | None]:
    """Check rate limits and record call atomically.

    Returns:
        (allowed: bool, error_message: str | None, retry_after_seconds: int | None)
    """
    settings = get_settings()
    client_limit = getattr(settings, "studio_ai_client_rate_limit", CLIENT_LIMIT_PER_HOUR)
    global_hr_limit = getattr(settings, "studio_ai_global_rate_limit_hour", GLOBAL_LIMIT_PER_HOUR)
    global_day_limit = getattr(settings, "studio_ai_global_rate_limit_day", GLOBAL_LIMIT_PER_DAY)
    turn_limit = getattr(settings, "studio_ai_turn_rate_limit", TURN_LIMIT_PER_10MIN)

    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    client_key_hash = hashlib.sha256(client_key.encode("utf-8")).hexdigest()[:16]
    client_counter_key = f"client:{client_key_hash}"
    turn_counter_key = f"turn:{conversation_id}"
    global_counter_key = "global"

    one_hour_ago = current_time - timedelta(seconds=CLIENT_WINDOW_SECONDS)
    one_day_ago = current_time - timedelta(seconds=GLOBAL_DAY_WINDOW_SECONDS)
    ten_mins_ago = current_time - timedelta(seconds=TURN_WINDOW_SECONDS)

    database = db
    if database is None:
        try:
            database = get_database()
        except Exception:
            database = None

    if database is not None:
        col = database[STUDIO_COUNTERS_COLLECTION]

        # 1. Turn guard: max 6 calls per conversation id per 10 minutes
        turn_count = await col.count_documents(
            {"key": turn_counter_key, "created_at": {"$gte": ten_mins_ago}}
        )
        if turn_count >= turn_limit:
            oldest = await col.find_one(
                {"key": turn_counter_key, "created_at": {"$gte": ten_mins_ago}},
                sort=[("created_at", ASCENDING)],
            )
            created = oldest["created_at"] if oldest else ten_mins_ago
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            delta = (current_time - created).total_seconds()
            retry_after = max(1, int(TURN_WINDOW_SECONDS - delta))
            return (
                False,
                f"Conversation turn limit exceeded (maximum {turn_limit} calls per 10 minutes).",
                retry_after,
            )

        # 2. Per-client limit: 20 per hour
        client_count = await col.count_documents(
            {"key": client_counter_key, "created_at": {"$gte": one_hour_ago}}
        )
        if client_count >= client_limit:
            oldest = await col.find_one(
                {"key": client_counter_key, "created_at": {"$gte": one_hour_ago}},
                sort=[("created_at", ASCENDING)],
            )
            created = oldest["created_at"] if oldest else one_hour_ago
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            delta = (current_time - created).total_seconds()
            retry_after = max(1, int(CLIENT_WINDOW_SECONDS - delta))
            return (
                False,
                f"Client rate limit exceeded ({client_limit} calls/hour).",
                retry_after,
            )

        # 3. Global limit: 100 per hour
        global_hr_count = await col.count_documents(
            {"key": global_counter_key, "created_at": {"$gte": one_hour_ago}}
        )
        if global_hr_count >= global_hr_limit:
            oldest = await col.find_one(
                {"key": global_counter_key, "created_at": {"$gte": one_hour_ago}},
                sort=[("created_at", ASCENDING)],
            )
            created = oldest["created_at"] if oldest else one_hour_ago
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            delta = (current_time - created).total_seconds()
            retry_after = max(1, int(GLOBAL_WINDOW_SECONDS - delta))
            return (
                False,
                f"Global hourly rate limit exceeded ({global_hr_limit} calls/hour).",
                retry_after,
            )

        # 4. Global limit: 500 per day
        global_day_count = await col.count_documents(
            {"key": global_counter_key, "created_at": {"$gte": one_day_ago}}
        )
        if global_day_count >= global_day_limit:
            oldest = await col.find_one(
                {"key": global_counter_key, "created_at": {"$gte": one_day_ago}},
                sort=[("created_at", ASCENDING)],
            )
            created = oldest["created_at"] if oldest else one_day_ago
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            delta = (current_time - created).total_seconds()
            retry_after = max(1, int(GLOBAL_DAY_WINDOW_SECONDS - delta))
            return (
                False,
                f"Global daily rate limit exceeded ({global_day_limit} calls/day).",
                retry_after,
            )

        # Record attempt across all 3 dimensions
        docs = [
            {"key": client_counter_key, "type": "client", "created_at": current_time},
            {"key": global_counter_key, "type": "global", "created_at": current_time},
            {"key": turn_counter_key, "type": "turn", "created_at": current_time},
        ]
        await col.insert_many(docs)
        return True, None, None

    # Offline / in-memory fallback
    # 1. Turn guard
    turn_attempts = [
        c for c in _OFFLINE_STUDIO_COUNTERS
        if c["key"] == turn_counter_key and c["created_at"] >= ten_mins_ago
    ]
    if len(turn_attempts) >= turn_limit:
        oldest_dt = min(c["created_at"] for c in turn_attempts)
        delta = (current_time - oldest_dt).total_seconds()
        retry_after = max(1, int(TURN_WINDOW_SECONDS - delta))
        return (
            False,
            f"Conversation turn limit exceeded (maximum {turn_limit} calls per 10 minutes).",
            retry_after,
        )

    # 2. Client limit
    client_attempts = [
        c for c in _OFFLINE_STUDIO_COUNTERS
        if c["key"] == client_counter_key and c["created_at"] >= one_hour_ago
    ]
    if len(client_attempts) >= client_limit:
        oldest_dt = min(c["created_at"] for c in client_attempts)
        delta = (current_time - oldest_dt).total_seconds()
        retry_after = max(1, int(CLIENT_WINDOW_SECONDS - delta))
        return (
            False,
            f"Client rate limit exceeded ({client_limit} calls/hour).",
            retry_after,
        )

    # 3. Global hour limit
    global_hr_attempts = [
        c for c in _OFFLINE_STUDIO_COUNTERS
        if c["key"] == global_counter_key and c["created_at"] >= one_hour_ago
    ]
    if len(global_hr_attempts) >= global_hr_limit:
        oldest_dt = min(c["created_at"] for c in global_hr_attempts)
        delta = (current_time - oldest_dt).total_seconds()
        retry_after = max(1, int(GLOBAL_WINDOW_SECONDS - delta))
        return (
            False,
            f"Global hourly rate limit exceeded ({global_hr_limit} calls/hour).",
            retry_after,
        )

    # 4. Global day limit
    global_day_attempts = [
        c for c in _OFFLINE_STUDIO_COUNTERS
        if c["key"] == global_counter_key and c["created_at"] >= one_day_ago
    ]
    if len(global_day_attempts) >= global_day_limit:
        oldest_dt = min(c["created_at"] for c in global_day_attempts)
        delta = (current_time - oldest_dt).total_seconds()
        retry_after = max(1, int(GLOBAL_DAY_WINDOW_SECONDS - delta))
        return (
            False,
            f"Global daily rate limit exceeded ({global_day_limit} calls/day).",
            retry_after,
        )

    _OFFLINE_STUDIO_COUNTERS.extend([
        {"key": client_counter_key, "type": "client", "created_at": current_time},
        {"key": global_counter_key, "type": "global", "created_at": current_time},
        {"key": turn_counter_key, "type": "turn", "created_at": current_time},
    ])
    return True, None, None

