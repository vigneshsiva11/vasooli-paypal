"""Phase 2 — PayPal inbound webhook receiver.

POST /webhooks/paypal

Contract:
  1. Read the raw body (never parse via FastAPI — signature is over the bytes).
  2. Verify via PayPal's server-side verify-webhook-signature API.
  3. Reject non-SUCCESS with 401 (not 400 — PayPal documents 401 for failed
     webhook auth so their retry logic handles it correctly).
  4. Store the event in `paypal_events` with a UNIQUE index on the event id
     (idempotent — duplicate delivery returns 200 immediately).
  5. Return 200 quickly.  No business logic.

Design decisions that mirror app/routes/webhooks.py:
  * The router carries NO `database_ready` dependency so that verification
    rejection is unconditional — an unsigned/unknown request is rejected
    even when Mongo is down.
  * The raw body is read with `await request.body()` before anything else.
  * PayPalWebhookNotConfigured → 401 (operator error, not a bad request).
  * PayPalWebhookRejected subclasses → 401.
  * PayPalCallFailed (verify API unreachable) → 503 (our fault, not theirs).

Note: PayPal's verify endpoint does NOT work for manually posted mock events.
Test with real sandbox events only (ngrok + PayPal Developer Dashboard webhook).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, Response, status
from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.db import get_database
from app.gateways.paypal.webhooks import (
    PayPalCallFailed,
    PayPalWebhookNotConfigured,
    PayPalWebhookRejected,
    VerifiedPayPalWebhook,
    accept,
)

logger = logging.getLogger(__name__)

PAYPAL_EVENTS_COLLECTION = "paypal_events"

paypal_webhook_router = APIRouter(tags=["paypal-webhooks"])

# ---------------------------------------------------------------------------
# Collection + index (lazy, idempotent)
# ---------------------------------------------------------------------------

_index_ensured: bool = False


async def _collection() -> AsyncIOMotorCollection:
    """Return the paypal_events collection, ensuring the unique index exists."""
    global _index_ensured
    db = get_database()
    col = db[PAYPAL_EVENTS_COLLECTION]
    if not _index_ensured:
        await col.create_index(
            [("paypal_event_id", ASCENDING)],
            unique=True,
            name="uniq_paypal_event_id",
        )
        _index_ensured = True
    return col


async def _store_event(verified: VerifiedPayPalWebhook) -> bool:
    """Insert the verified event.  Returns True if inserted, False if duplicate."""
    col = await _collection()
    document: dict[str, Any] = {
        "paypal_event_id": verified.paypal_event_id,
        "event_type": verified.event_type,
        "payload": verified.payload,
        "received_at": datetime.now(tz=timezone.utc),
        "body_bytes": verified.body_bytes,
    }
    try:
        await col.insert_one(document)
        return True
    except DuplicateKeyError:
        return False


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@paypal_webhook_router.post(
    "/webhooks/paypal",
    summary="Receive and verify a PayPal sandbox webhook event",
    status_code=status.HTTP_200_OK,
)
async def receive_paypal_webhook(request: Request) -> Response:
    """Verify and store an inbound PayPal webhook event.

    Status codes:
    * **401** — PAYPAL_WEBHOOK_ID not configured, or PayPal returned
      non-SUCCESS verification status.  PayPal will retry.
    * **503** — PayPal's verify endpoint was unreachable.  PayPal retries.
    * **200** — Event accepted (new) or already stored (duplicate).
    """
    body = await request.body()
    # Lower-case all header names for consistent lookup in accept().
    headers = {k.lower(): v for k, v in request.headers.items()}

    # --- Verification ---
    try:
        verified = await accept(body=body, headers=headers)
    except PayPalWebhookNotConfigured as exc:
        logger.error(
            "PayPal webhook: PAYPAL_WEBHOOK_ID not configured -- "
            "cannot verify inbound events.  Set it in .env."
        )
        # 401 so PayPal retries; do NOT surface exc detail (it contains the
        # config-missing path which is harmless, but we keep logs clean).
        return Response(
            content='{"detail":"Webhook ID not configured"}',
            status_code=status.HTTP_401_UNAUTHORIZED,
            media_type="application/json",
        )
    except PayPalCallFailed as exc:
        logger.error("PayPal webhook: verify API call failed: %s", exc)
        return Response(
            content='{"detail":"Upstream verify call failed; retry later"}',
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
    except PayPalWebhookRejected as exc:
        logger.warning(
            "PayPal webhook rejected (%s): %s", type(exc).__name__, exc
        )
        return Response(
            content=f'{{"detail":"{type(exc).__name__}"}}',
            status_code=status.HTTP_401_UNAUTHORIZED,
            media_type="application/json",
        )

    # --- Storage ---
    inserted = await _store_event(verified)
    if inserted:
        logger.info(
            "Stored PayPal event %s type=%s",
            verified.paypal_event_id,
            verified.event_type,
        )
    else:
        logger.info(
            "Duplicate PayPal event %s — already stored, returning 200",
            verified.paypal_event_id,
        )

    return Response(
        content=f'{{"received":true,"event_id":"{verified.paypal_event_id}",'
                f'"duplicate":{str(not inserted).lower()}}}',
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )
