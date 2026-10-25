"""Persistence and helpers for PayPal charge attempts (Step 1).

Owns the `charge_attempts` collection and indexing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.db import get_database
from app.models.charge_attempt import ChargeAttempt, ChargeAttemptPurpose

logger = logging.getLogger(__name__)

COLLECTION_NAME = "charge_attempts"
ORDER_ID_INDEX = "uniq_order_id"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def collection(db: Any = None) -> AsyncIOMotorCollection:
    """Return the charge_attempts collection."""
    database = db if db is not None else get_database()
    return database[COLLECTION_NAME]


async def ensure_indexes(db: Any = None) -> None:
    """Ensure unique index on order_id."""
    col = collection(db)
    await col.create_index(
        [("order_id", ASCENDING)],
        unique=True,
        name=ORDER_ID_INDEX,
    )
    logger.info("Ensured unique index %r on %s.order_id", ORDER_ID_INDEX, COLLECTION_NAME)


async def record_charge_attempt(
    *,
    order_id: str,
    customer_ref: str,
    amount: float,
    currency: str = "USD",
    purpose: ChargeAttemptPurpose = "onboarding",
    created_at: datetime | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Record a PayPal charge attempt after an order is created.

    Idempotent on order_id.
    """
    col = collection(db)
    attempt = ChargeAttempt(
        order_id=order_id,
        customer_ref=customer_ref,
        amount=amount,
        currency=currency,
        purpose=purpose,
        created_at=created_at or _utc_now(),
    )
    doc = attempt.model_dump()
    try:
        res = await col.insert_one(doc)
        doc["_id"] = res.inserted_id
    except DuplicateKeyError:
        existing = await col.find_one({"order_id": order_id})
        return existing or doc

    logger.info(
        "Recorded charge attempt for order %s customer %s (%s %.2f, %s)",
        order_id,
        customer_ref,
        currency,
        amount,
        purpose,
    )
    return doc


async def get_charge_attempt(order_id: str, db: Any = None) -> dict[str, Any] | None:
    """Look up a charge attempt by order_id."""
    col = collection(db)
    return await col.find_one({"order_id": order_id})
