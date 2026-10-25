"""Persistence and helpers for PayPal customer vault tokens (Step 4).

Owns the `customer_vault` collection, unique indexing on `vault_id`,
and the `get_active_vault_token(customer_ref)` lookup helper.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.db import get_database
from app.models.vault import CustomerVaultToken

logger = logging.getLogger(__name__)

COLLECTION_NAME = "customer_vault"
VAULT_ID_INDEX = "uniq_vault_id"
CUSTOMER_INDEX = "idx_customer_vault_active"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VaultTokenDoc(dict):
    """Dictionary subclass supporting attribute access for convenience."""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)


def collection(db: Any = None) -> AsyncIOMotorCollection:
    """Return the customer_vault collection."""
    database = db if db is not None else get_database()
    return database[COLLECTION_NAME]


async def ensure_indexes(db: Any = None) -> None:
    """Ensure unique index on vault_id and query index on customer_ref + status."""
    col = collection(db)
    await col.create_index(
        [("vault_id", ASCENDING)],
        unique=True,
        name=VAULT_ID_INDEX,
    )
    await col.create_index(
        [("customer_ref", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
        name=CUSTOMER_INDEX,
    )
    logger.info("Ensured indexes %r/%r on %s", VAULT_ID_INDEX, CUSTOMER_INDEX, COLLECTION_NAME)


async def store_vault_token(
    *,
    customer_ref: str,
    vault_id: str,
    card_brand: str,
    last_digits: str,
    source_event_id: str,
    status: str = "active",
    created_at: datetime | None = None,
    db: Any = None,
) -> tuple[dict[str, Any], bool]:
    """Insert a vaulted card token.

    Never stores full card numbers.
    Returns (document, inserted_bool).
    """
    col = collection(db)
    token = CustomerVaultToken(
        customer_ref=customer_ref,
        vault_id=vault_id,
        card_brand=card_brand,
        last_digits=last_digits[-4:],
        status=status,  # type: ignore[arg-type]
        source_event_id=source_event_id,
        created_at=created_at or _utc_now(),
    )
    doc = token.model_dump()
    try:
        res = await col.insert_one(doc)
        doc["_id"] = res.inserted_id
        logger.info(
            "Stored vault token %s for customer %s (%s ...%s)",
            vault_id,
            customer_ref,
            card_brand,
            last_digits,
        )
        return VaultTokenDoc(doc), True
    except DuplicateKeyError:
        existing = await col.find_one({"vault_id": vault_id})
        logger.info("Vault token %s already recorded; returning existing document", vault_id)
        return VaultTokenDoc(existing or doc), False


async def get_active_vault_token(
    customer_ref: str, db: Any = None
) -> VaultTokenDoc | None:
    """Retrieve the most recent active vault token for a customer.

    Args:
        customer_ref: Merchant customer reference.
        db: Optional database handle.

    Returns:
        VaultTokenDoc with vault token details, or None if no active token exists.
    """
    col = collection(db)
    doc = await col.find_one(
        {"customer_ref": customer_ref, "status": "active"},
        sort=[("created_at", DESCENDING)],
    )
    if not doc:
        return None
    return VaultTokenDoc(doc)
