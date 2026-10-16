"""PayPal OAuth 2 client — the only module that touches credentials.

Mirrors app/execution/razorpay.py in spirit:

* All outbound calls flow through `paypal_client()`, an async context manager
  that injects the Bearer token automatically.
* The token is cached in a module-level singleton and refreshed ~60 seconds
  before expiry, so callers never manage token lifecycle.
* No secret ever reaches a log line.  `_redact()` is applied to anything
  derived from an exception.
* `PayPalNotConfigured` is an operator error (no credentials set).
* `PayPalCallFailed` is a gateway error (call was made and failed).

All calls use the SANDBOX base URL.  Currency: USD.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
TOKEN_URL = f"{SANDBOX_BASE}/v1/oauth2/token"

#: Refresh the cached token this many seconds before PayPal says it expires.
TOKEN_REFRESH_BUFFER_SECONDS = 60

REQUEST_TIMEOUT_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class PayPalNotConfigured(RuntimeError):
    """Raised when credentials are absent.

    An operator problem, not a gateway problem — nothing was attempted, so
    callers must not record a failed execution with a misleading reason.
    """


class PayPalCallFailed(RuntimeError):
    """Raised when a PayPal API call was made and did not succeed.

    The message is safe to store verbatim in failure fields.
    """


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PayPalCredentials:
    """A client_id / client_secret pair.

    Passed as an argument rather than read inside a request, so a test can
    supply a wrong key without mutating a module global — the same lesson
    recorded in app/execution/razorpay.py.
    """

    client_id: str
    client_secret: str


def credentials_from_settings() -> PayPalCredentials:
    """Read the configured sandbox credentials.

    Raises:
        PayPalNotConfigured: if either value is missing.
    """
    settings = get_settings()
    if not settings.paypal_client_id or not settings.paypal_client_secret:
        missing = [
            name
            for name, value in (
                ("PAYPAL_CLIENT_ID", settings.paypal_client_id),
                ("PAYPAL_CLIENT_SECRET", settings.paypal_client_secret),
            )
            if not value
        ]
        raise PayPalNotConfigured(
            f"{', '.join(missing)} not set in the environment; PayPal calls "
            "cannot proceed.  Set them in .env and restart."
        )
    return PayPalCredentials(
        client_id=settings.paypal_client_id,
        client_secret=settings.paypal_client_secret,
    )


# ---------------------------------------------------------------------------
# In-memory token cache
# ---------------------------------------------------------------------------


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float  # monotonic clock seconds


_token_cache: _CachedToken | None = None
_token_lock: asyncio.Lock | None = None  # created lazily (event-loop-safe)


def _get_lock() -> asyncio.Lock:
    global _token_lock
    if _token_lock is None:
        _token_lock = asyncio.Lock()
    return _token_lock


def _token_is_fresh(cache: _CachedToken) -> bool:
    return time.monotonic() < cache.expires_at - TOKEN_REFRESH_BUFFER_SECONDS


async def _fetch_token(credentials: PayPalCredentials) -> _CachedToken:
    """Call the OAuth endpoint and return a new cached token.

    Never logs the token value; only logs the expiry window for ops visibility.
    """
    raw = f"{credentials.client_id}:{credentials.client_secret}".encode()
    auth_header = f"Basic {base64.b64encode(raw).decode()}"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                TOKEN_URL,
                content=b"grant_type=client_credentials",
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
    except httpx.HTTPError as exc:
        raise PayPalCallFailed(
            f"Network error fetching PayPal token: {type(exc).__name__}"
        ) from exc

    if response.status_code >= 400:
        raise PayPalCallFailed(
            f"PayPal token endpoint returned HTTP {response.status_code}"
        )

    body = response.json()
    token = body.get("access_token", "")
    expires_in = int(body.get("expires_in", 3600))
    if not token:
        raise PayPalCallFailed("PayPal token response carried no access_token")

    logger.info(
        "Fetched new PayPal access token; expires_in=%ds, "
        "will refresh %ds before expiry",
        expires_in,
        TOKEN_REFRESH_BUFFER_SECONDS,
    )
    return _CachedToken(
        access_token=token,
        expires_at=time.monotonic() + expires_in,
    )


async def get_access_token(
    credentials: PayPalCredentials | None = None,
) -> str:
    """Return a valid Bearer token, fetching or refreshing as needed.

    Thread-safe: uses a module-level asyncio.Lock so concurrent callers
    do not race into parallel token fetches.

    Args:
        credentials: Defaults to the configured pair.

    Raises:
        PayPalNotConfigured: if credentials are absent.
        PayPalCallFailed: on a network or HTTP error.
    """
    global _token_cache

    keys = credentials_from_settings() if credentials is None else credentials
    lock = _get_lock()

    async with lock:
        if _token_cache is not None and _token_is_fresh(_token_cache):
            return _token_cache.access_token
        _token_cache = await _fetch_token(keys)
        return _token_cache.access_token


# ---------------------------------------------------------------------------
# HTTP client factory
# ---------------------------------------------------------------------------


@asynccontextmanager
async def paypal_client(
    credentials: PayPalCredentials | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Async context manager yielding an httpx client pre-configured for PayPal.

    Injects:
      * Authorization: Bearer <token>  (fetched/cached by get_access_token)
      * Content-Type: application/json
      * base_url: SANDBOX_BASE

    Usage::

        async with paypal_client() as client:
            resp = await client.get("/v2/invoicing/invoices")

    Raises:
        PayPalNotConfigured: no credentials.
        PayPalCallFailed: token fetch failed.
    """
    token = await get_access_token(credentials)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(
        base_url=SANDBOX_BASE,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Helpers shared across gateway modules
# ---------------------------------------------------------------------------


def describe_error(response: httpx.Response) -> str:
    """Turn a non-2xx PayPal response into a reason string safe to store/log.

    PayPal nests its error detail under top-level 'message' and 'details'.
    Falls back to the raw body truncated so a proxy error is still readable.
    Never includes the credential values.
    """
    try:
        payload: dict[str, Any] = response.json()
    except ValueError:
        return f"PayPal HTTP {response.status_code}: {response.text[:300]}"

    parts: list[str] = [f"PayPal HTTP {response.status_code}"]
    if msg := payload.get("message"):
        parts.append(str(msg))
    if details := payload.get("details"):
        if isinstance(details, list) and details:
            first = details[0]
            if isinstance(first, dict):
                issue = first.get("issue") or first.get("description", "")
                if issue:
                    parts.append(str(issue))
    return " — ".join(parts)[:400]
