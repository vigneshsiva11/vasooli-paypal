"""PayPal webhook signature verification — Phase 2 gateway module.

Mirrors app/webhooks/signature.py in contract:
  accept() → VerifiedPayPalWebhook | raises WebhookRejected subclass

**Verification is server-side, not local HMAC.**  PayPal requires POST to
/v1/notifications/verify-webhook-signature with the transmission headers
and the raw event body.  We parse the body once from the raw bytes, then
pass it as-is to the verify endpoint — never mutated or re-serialised.

**Fail-closed on missing PAYPAL_WEBHOOK_ID.**  If the setting is empty the
function raises PayPalWebhookNotConfigured, which the route answers 401.
No secret value is logged anywhere.

**Event id for deduplication.**  PayPal puts the event id at the top level of
the event body as `id`.  That is the idempotency key used by the
paypal_events collection.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.gateways.paypal.client import (
    PayPalCallFailed,
    describe_error,
    get_access_token,
    SANDBOX_BASE,
    REQUEST_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

VERIFY_URL = f"{SANDBOX_BASE}/v1/notifications/verify-webhook-signature"

#: PayPal transmission header names (case-insensitive in HTTP, stored as-is).
TRANSMISSION_ID_HEADER = "paypal-transmission-id"
TRANSMISSION_TIME_HEADER = "paypal-transmission-time"
CERT_URL_HEADER = "paypal-cert-url"
AUTH_ALGO_HEADER = "paypal-auth-algo"
TRANSMISSION_SIG_HEADER = "paypal-transmission-sig"


# ---------------------------------------------------------------------------
# Error hierarchy (mirrors app/webhooks/signature.py)
# ---------------------------------------------------------------------------


class PayPalWebhookRejected(ValueError):
    """Base for inbound requests that must be answered 401 and not processed."""


class PayPalWebhookNotConfigured(RuntimeError):
    """PAYPAL_WEBHOOK_ID is not set.

    An operator problem, not a bad request.  Answered 401 rather than 400 so
    that a misconfiguration is visible and does not look like a forgery.
    """


class PayPalMissingHeaders(PayPalWebhookRejected):
    """One or more required PayPal transmission headers are absent."""


class PayPalMalformedBody(PayPalWebhookRejected):
    """The body is not a JSON object or has no event id."""


class PayPalVerificationFailed(PayPalWebhookRejected):
    """PayPal's verify endpoint returned a non-SUCCESS status."""


# ---------------------------------------------------------------------------
# Verified webhook token (frozen, like VerifiedWebhook in signature.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifiedPayPalWebhook:
    """A webhook whose signature PayPal has confirmed.

    Constructed in exactly one place — accept() below, after the verify
    endpoint returns SUCCESS.  The route and the store accept this type only.
    """

    #: Top-level `id` from the event body.  The deduplication key.
    paypal_event_id: str
    #: event_type string, e.g. "INVOICING.INVOICE.PAID".
    event_type: str
    #: The full event body as parsed from the raw bytes (never mutated).
    payload: dict[str, Any]
    #: Raw body length, for logging.
    body_bytes: int


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _webhook_id() -> str:
    """Return the configured PAYPAL_WEBHOOK_ID.

    Raises:
        PayPalWebhookNotConfigured: if absent.  Fail-closed.
    """
    webhook_id = get_settings().paypal_webhook_id
    if not webhook_id or not webhook_id.strip():
        raise PayPalWebhookNotConfigured(
            "PAYPAL_WEBHOOK_ID is not set in the environment.  PayPal webhook "
            "signature verification cannot proceed.  Set it in .env and restart."
        )
    return webhook_id.strip()


async def accept(
    *,
    body: bytes,
    headers: dict[str, str],
) -> VerifiedPayPalWebhook:
    """Verify an inbound PayPal webhook via the server-side API.

    Order is load-bearing:
      1. Check PAYPAL_WEBHOOK_ID is configured (fail-closed).
      2. Validate required transmission headers are present.
      3. Parse the body JSON (size guard first).
      4. Call PayPal's verify endpoint with the original parsed body (not
         re-serialised) and the transmission headers.
      5. Reject if verification_status != SUCCESS.
      6. Return VerifiedPayPalWebhook.

    Args:
        body: Raw request bytes, exactly as received.
        headers: Dict of header name → value (lowercased keys recommended).

    Raises:
        PayPalWebhookNotConfigured: WEBHOOK_ID missing — answer 401.
        PayPalWebhookRejected (or subclass): bad request — answer 401.
        PayPalCallFailed: verify API call itself failed — answer 500.
    """
    # 1. Fail-closed: webhook id must be configured.
    webhook_id = _webhook_id()

    # 2. Required transmission headers.
    def _h(name: str) -> str | None:
        return headers.get(name) or headers.get(name.lower())

    transmission_id = _h(TRANSMISSION_ID_HEADER)
    transmission_time = _h(TRANSMISSION_TIME_HEADER)
    cert_url = _h(CERT_URL_HEADER)
    auth_algo = _h(AUTH_ALGO_HEADER)
    transmission_sig = _h(TRANSMISSION_SIG_HEADER)

    missing = [
        n
        for n, v in [
            (TRANSMISSION_ID_HEADER, transmission_id),
            (TRANSMISSION_TIME_HEADER, transmission_time),
            (CERT_URL_HEADER, cert_url),
            (AUTH_ALGO_HEADER, auth_algo),
            (TRANSMISSION_SIG_HEADER, transmission_sig),
        ]
        if not v
    ]
    if missing:
        raise PayPalMissingHeaders(
            f"Missing required PayPal transmission headers: {missing}"
        )

    # 3. Parse body JSON once.  Never re-serialise before passing to verify.
    if len(body) > 512 * 1024:
        raise PayPalMalformedBody(
            f"Request body is {len(body)} bytes; refusing to process."
        )
    try:
        event_body: dict[str, Any] = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PayPalMalformedBody(f"Body is not valid JSON: {exc}") from exc

    if not isinstance(event_body, dict):
        raise PayPalMalformedBody(
            f"Body is a {type(event_body).__name__}, not a JSON object"
        )

    event_id = event_body.get("id", "")
    event_type = event_body.get("event_type", "")
    if not event_id:
        raise PayPalMalformedBody(
            "Event body has no 'id' field; cannot deduplicate this request"
        )

    # 4. Call PayPal's verify endpoint.
    #    Pass the parsed event body as-is (the JSON object, not re-serialised
    #    bytes) so PayPal can compare it against their own record.
    verify_payload = {
        "auth_algo": auth_algo,
        "cert_url": cert_url,
        "transmission_id": transmission_id,
        "transmission_sig": transmission_sig,
        "transmission_time": transmission_time,
        "webhook_id": webhook_id,
        "webhook_event": event_body,  # original parsed object, not re-encoded
    }

    token = await get_access_token()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            verify_resp = await client.post(
                VERIFY_URL,
                json=verify_payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise PayPalCallFailed(
            f"Network error calling PayPal verify endpoint: {type(exc).__name__}"
        ) from exc

    if verify_resp.status_code >= 400:
        raise PayPalCallFailed(
            f"PayPal verify endpoint returned HTTP {verify_resp.status_code}: "
            f"{verify_resp.text[:200]}"
        )

    # 5. Check verification_status.
    verification_status = verify_resp.json().get("verification_status", "")
    if verification_status != "SUCCESS":
        logger.warning(
            "PayPal webhook %s failed verification: status=%s",
            event_id,
            verification_status,
        )
        raise PayPalVerificationFailed(
            f"PayPal returned verification_status={verification_status!r} for "
            f"event {event_id!r}; request rejected."
        )

    logger.info(
        "Verified PayPal webhook event_id=%s event_type=%s (%d bytes)",
        event_id,
        event_type,
        len(body),
    )

    return VerifiedPayPalWebhook(
        paypal_event_id=event_id,
        event_type=event_type,
        payload=event_body,
        body_bytes=len(body),
    )
