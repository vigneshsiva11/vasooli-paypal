"""Authentication and privileged route access control dependencies."""

from __future__ import annotations

import hmac
import logging
import os
from fastapi import HTTPException, Request, status

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _resolve_settings(request: Request) -> Settings:
    """Resolve active Settings from request app state or process singleton."""
    app_state = getattr(getattr(request, "app", None), "state", None)
    state_settings = getattr(app_state, "settings", None) if app_state is not None else None
    if isinstance(state_settings, Settings):
        return state_settings
    return get_settings()


def require_admin_privileged(request: Request) -> None:
    """Verify admin token for privileged routes.

    Privileged routes (/demo/reset, /demo/scenarios/*, /sim/advance, /orchestrator/*)
    must verify the admin token themselves through this dependency whenever
    admin_token is configured or the app is in production (constant-time compare;
    503 if unset in production), independent of public_demo.
    """
    settings = _resolve_settings(request)
    expected = (settings.admin_token or "").strip()
    is_prod = settings.is_production

    # 1. In production: admin_token MUST be configured; 503 if unset
    if is_prod and not expected:
        logger.warning("Privileged route rejected: admin_token unset in production (503 fail-closed)")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable: admin token not configured in production",
        )

    # 2. When admin_token is empty in non-production
    if not expected:
        if settings.public_demo:
            logger.warning("Privileged route rejected: admin_token not configured in public demo mode (503)")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service unavailable: operations disabled in public demo mode",
            )
        # Local development / regression gates without admin_token configured -> allowed
        return

    # 3. admin_token is configured (or production with admin_token)
    token = request.headers.get("X-Admin-Token")
    if not token:
        logger.warning("Privileged route rejected: missing X-Admin-Token header (401)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or missing X-Admin-Token",
        )

    if not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        logger.warning("Privileged route rejected: invalid X-Admin-Token (401)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or missing X-Admin-Token",
        )


def require_admin_if_public_demo(request: Request) -> None:
    """Ensure admin token is provided when public demo mode is enabled."""
    settings = _resolve_settings(request)
    if not settings.public_demo:
        return

    expected = (settings.admin_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable: operations disabled in public demo mode",
        )
    token = request.headers.get("X-Admin-Token")
    if not token or not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or missing X-Admin-Token",
        )
