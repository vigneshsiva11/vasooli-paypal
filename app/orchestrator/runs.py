"""Orchestrator run records and idempotency tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import logging
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.clock import Clock, _utc_now
from app.db import get_database

logger = logging.getLogger(__name__)

RUNS_COLLECTION = "orchestrator_runs"


async def ensure_orchestrator_indexes(db: Any = None) -> None:
    """Ensure unique index on (event_id, stage, record_version)."""
    database = db if db is not None else get_database()
    await database[RUNS_COLLECTION].create_index(
        [
            ("event_id", ASCENDING),
            ("stage", ASCENDING),
            ("record_version", ASCENDING),
        ],
        unique=True,
        name="orchestrator_runs_idempotency_idx",
    )


async def record_run(
    *,
    event_id: str,
    stage: str,
    record_version: int,
    outcome: str,
    clock: Clock,
    db: Any = None,
) -> bool:
    """Record an orchestrator stage execution.

    Returns:
        True if inserted, False if already recorded.
    """
    database = db if db is not None else get_database()
    doc = {
        "event_id": event_id,
        "stage": stage,
        "record_version": record_version,
        "outcome": outcome,
        "real_time": _utc_now(),
        "clock_now": clock.now(),
        "simulated_time": clock.is_simulated(),
    }
    try:
        await database[RUNS_COLLECTION].insert_one(doc)
        return True
    except DuplicateKeyError:
        logger.info(
            "Orchestrator run already recorded: event=%s stage=%s version=%s",
            event_id,
            stage,
            record_version,
        )
        return False
