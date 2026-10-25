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

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response, status
from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.db import get_database
from app.gateways.paypal.webhooks import (
    PayPalCallFailed,
    PayPalWebhookNotConfigured,
    PayPalWebhookRejected,
    VerifiedPayPalWebhook,
    accept,
)
from app.orchestrator import advance
from app.webhooks.paypal_reconciler import reconcile_paypal_event

logger = logging.getLogger(__name__)

PAYPAL_EVENTS_COLLECTION = "paypal_events"

paypal_webhook_router = APIRouter(tags=["paypal-webhooks"])

_background_tasks: set[asyncio.Task[Any]] = set()


async def _safe_orchestrate(event_id: str) -> None:
    """Execute advance for event_id, holding strong reference and logging exception type only."""
    try:
        await advance(event_id)
    except Exception as exc:
        logger.error(
            "Auto-orchestration background task failed for event %s: %s",
            event_id,
            type(exc).__name__,
        )

# ---------------------------------------------------------------------------
# Collection + index (lazy, idempotent)
# ---------------------------------------------------------------------------

_index_ensured: bool = False


async def ensure_paypal_event_indexes(db: Any = None) -> None:
    """Ensure unique index on paypal_events collection."""
    database = db if db is not None else get_database()
    col = database[PAYPAL_EVENTS_COLLECTION]
    await col.create_index(
        [("paypal_event_id", ASCENDING)],
        unique=True,
        name="uniq_paypal_event_id",
    )


async def _collection() -> AsyncIOMotorCollection:
    """Return the paypal_events collection, ensuring the unique index exists."""
    global _index_ensured
    db = get_database()
    col = db[PAYPAL_EVENTS_COLLECTION]
    if not _index_ensured:
        await ensure_paypal_event_indexes(db)
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
async def receive_paypal_webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
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

    # --- Reconciliation ---
    reconcile_res = None
    try:
        reconcile_res = await reconcile_paypal_event(verified.payload)
    except Exception as exc:
        logger.error("Error reconciling PayPal event %s: %s", verified.paypal_event_id, exc)

    response_payload: dict[str, Any] = {
        "received": True,
        "event_id": verified.paypal_event_id,
        "duplicate": not inserted or (reconcile_res.duplicate if reconcile_res else False),
        "reconciled": reconcile_res.processed if reconcile_res else False,
        "matched": reconcile_res.matched if reconcile_res else False,
        "detail": reconcile_res.detail if reconcile_res else "Event stored",
    }
    if reconcile_res and reconcile_res.verification_id:
        response_payload["verification_id"] = reconcile_res.verification_id
    if reconcile_res and reconcile_res.outcome:
        response_payload["outcome"] = reconcile_res.outcome

    # Automatic background trigger after decline ingestion behind auto_orchestrate setting
    if (
        get_settings().auto_orchestrate
        and reconcile_res
        and reconcile_res.revenue_event_id
        and not reconcile_res.duplicate
    ):
        task = asyncio.create_task(_safe_orchestrate(reconcile_res.revenue_event_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return Response(
        content=json.dumps(response_payload),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )
