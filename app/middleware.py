"""Public demo guard and access control middlewares."""

from __future__ import annotations

import hmac
import logging
from typing import Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class PublicDemoMiddleware(BaseHTTPMiddleware):
    """Guard write operations when public demo mode is enabled.

    When `public_demo` is true:
    - GET, HEAD, OPTIONS requests remain public.
    - POST /webhooks/paypal bypasses admin token verification (protected by PayPal signatures).
    - If `admin_token` is empty or not configured, write requests fail closed with 503.
    - All other write requests require an exact, constant-time match on header `X-Admin-Token`.
    - Never logs the token value.
    """

    def __init__(
        self,
        app,
        settings: Optional[Settings] = None,
        settings_getter: Optional[Callable[[], Settings]] = None,
    ):
        super().__init__(app)
        self._settings = settings
        self._settings_getter = settings_getter

    def _get_settings(self) -> Settings:
        if self._settings is not None:
            return self._settings
        if self._settings_getter is not None:
            return self._settings_getter()
        return get_settings()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        current_settings = self._get_settings()
        if not current_settings.public_demo:
            return await call_next(request)

        # GET, HEAD, OPTIONS stay public
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        # POST /webhooks/paypal bypasses the token check (PayPal signatures protect it)
        path = request.url.path.rstrip("/")
        if request.method == "POST" and path == "/webhooks/paypal":
            return await call_next(request)

        # Exact-path allowlist for public demo run and rules preview route
        if request.method == "POST" and path in ("/demo/public/run", "/rules/preview"):
            return await call_next(request)

        # Fail closed if admin_token is empty
        expected_token = (current_settings.admin_token or "").strip()
        if not expected_token:
            logger.warning("Public demo mode write rejected: admin_token not configured (503 fail-closed)")
            detail = (
                "Promise extraction write operations are disabled in public demo mode"
                if path == "/promises/from-text"
                else "Service unavailable: write operations disabled in public demo mode"
            )
            return JSONResponse(
                status_code=503,
                content={"detail": detail, "code": "write_disabled_in_demo"},
            )

        token = request.headers.get("X-Admin-Token")
        if not token:
            logger.warning("Public demo mode write rejected: missing X-Admin-Token header (401)")
            detail = (
                "Extracting commitments from customer text requires an admin token in public demo mode."
                if path == "/promises/from-text"
                else "Unauthorized: X-Admin-Token header required"
            )
            return JSONResponse(
                status_code=401,
                content={"detail": detail, "code": "admin_token_required"},
            )

        if not hmac.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8")):
            logger.warning("Public demo mode write rejected: invalid X-Admin-Token header (401)")
            detail = (
                "Invalid admin token: extracting commitments from customer text requires a valid admin token."
                if path == "/promises/from-text"
                else "Unauthorized: invalid X-Admin-Token"
            )
            return JSONResponse(
                status_code=401,
                content={"detail": detail, "code": "invalid_admin_token"},
            )

        return await call_next(request)
