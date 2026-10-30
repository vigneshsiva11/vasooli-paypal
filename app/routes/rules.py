"""Rules API routes: plain-English preview, admin apply, admin revert, active, and history.

Public endpoints:
- POST /rules/preview (rate limited: 5/h client, 30/h global; cached by text hash)
- GET /rules/active (sanitized active USD rulebook and provenance)
- GET /rules/history (sanitized chronological list of USD rulebook versions)

Admin-only endpoints:
- POST /rules/apply (atomic apply from preview_id, checks expected_parent_fingerprint)
- POST /rules/revert (reverts all DB overrides to code baseline)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import get_database
from app.policy.gemini_rules import (
    GeminiRulesUnavailable,
    get_preview,
    parse_and_preview_rules,
)
from app.policy.rulebook_store import (
    apply_new_usd_rulebook,
    get_active_usd_rulebook,
    list_usd_rulebook_history,
    revert_usd_rulebook,
)
from app.sanitizer import sanitize_public_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rules", tags=["rules"])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _get_client_ip(request: Request) -> str:
    """Extract client IP using rightmost XFF entry (or socket host)."""
    xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def require_admin_token(x_admin_token: str | None = Header(None, alias="X-Admin-Token")) -> str:
    """Validate X-Admin-Token using constant-time comparison."""
    settings = get_settings()
    configured_token = (settings.admin_token or "").strip()
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin operations are disabled (admin_token not configured).",
        )
    if not x_admin_token or not hmac.compare_digest(x_admin_token.strip(), configured_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or missing X-Admin-Token.",
        )
    return "admin"


class RulesPreviewRequest(BaseModel):
    text: str = Field(..., max_length=500, description="Plain English merchant rule description")


class RulesApplyRequest(BaseModel):
    preview_id: str = Field(..., description="ID returned by POST /rules/preview")
    expected_parent_fingerprint: str = Field(..., description="Active USD fingerprint expected to be replaced")


@router.post("/preview", summary="Preview merchant rules proposal (public, rate limited)")
async def preview_rules(req: RulesPreviewRequest, request: Request) -> dict[str, Any]:
    """Parse merchant plain-English rules and produce a structured proposal preview.

    Rate limits:
    - 5 per hour per client IP (429)
    - 30 per hour globally (429)
    Cached by text hash; strips control characters.
    """
    settings = get_settings()
    # 1. Strip control characters except newline and tab
    raw_text = req.text
    cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", raw_text).strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Rule text must not be empty.",
        )
    if len(cleaned) > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Rule text exceeds maximum length of 500 characters.",
        )

    # 2. Rate limiting check
    db = get_database()
    now = _utc_now()
    one_hour_ago = now - timedelta(hours=1)
    client_ip = _get_client_ip(request)

    if db is not None:
        client_count = await db.rules_rate_limits.count_documents(
            {"client_ip": client_ip, "created_at": {"$gte": one_hour_ago}}
        )
        if client_count >= 5:
            oldest = await db.rules_rate_limits.find_one(
                {"client_ip": client_ip, "created_at": {"$gte": one_hour_ago}},
                sort=[("created_at", 1)],
            )
            wait_s = 3600
            if oldest and oldest.get("created_at"):
                oldest_dt = oldest["created_at"]
                oldest_tz = oldest_dt.replace(tzinfo=timezone.utc) if oldest_dt.tzinfo is None else oldest_dt
                elapsed = (now - oldest_tz).total_seconds()
                wait_s = max(1, int(3600 - elapsed))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Client rate limit exceeded: maximum 5 rule previews per hour.",
                headers={"Retry-After": str(wait_s)},
            )

        global_count = await db.rules_rate_limits.count_documents(
            {"created_at": {"$gte": one_hour_ago}}
        )
        if global_count >= 30:
            oldest = await db.rules_rate_limits.find_one(
                {"created_at": {"$gte": one_hour_ago}},
                sort=[("created_at", 1)],
            )
            wait_s = 3600
            if oldest and oldest.get("created_at"):
                oldest_dt = oldest["created_at"]
                oldest_tz = oldest_dt.replace(tzinfo=timezone.utc) if oldest_dt.tzinfo is None else oldest_dt
                elapsed = (now - oldest_tz).total_seconds()
                wait_s = max(1, int(3600 - elapsed))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Global rate limit exceeded: maximum 30 rule previews per hour.",
                headers={"Retry-After": str(wait_s)},
            )

    # 3. Resolve active rulebook for base comparison
    active_book = await get_active_usd_rulebook(db)

    # 4. Parse & Preview (uses cache & fallback chips)
    try:
        preview_data = await parse_and_preview_rules(cleaned, active_rulebook=active_book)
    except GeminiRulesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    # Record rate limit hit
    if db is not None:
        try:
            await db.rules_rate_limits.insert_one({
                "client_ip": client_ip,
                "created_at": now,
            })
        except Exception:
            pass

    return sanitize_public_dict(preview_data)


@router.post("/apply", summary="Apply rulebook proposal (admin only)")
async def apply_rules(
    req: RulesApplyRequest,
    admin: str = Depends(require_admin_token),
) -> dict[str, Any]:
    """Atomically commit a validated preview as the active USD rulebook."""
    preview = get_preview(req.preview_id)
    if not preview:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Preview ID {req.preview_id!r} not found or expired.",
        )

    if not preview.get("valid"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot apply invalid preview: {', '.join(preview.get('errors', []))}",
        )

    db = get_database()
    try:
        res = await apply_new_usd_rulebook(
            preview=preview,
            expected_parent_fingerprint=req.expected_parent_fingerprint,
            approved_by=admin,
            db=db,
        )
        return res
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/revert", summary="Revert USD rulebook to code baseline (admin only)")
async def revert_rules(
    admin: str = Depends(require_admin_token),
) -> dict[str, Any]:
    """Deactivate all DB-stored USD rulebook versions, restoring the code baseline."""
    db = get_database()
    res = await revert_usd_rulebook(db)
    return res


@router.get("/active", summary="Get active USD rulebook (public, sanitized)")
async def get_active_rules() -> dict[str, Any]:
    """Return active USD rulebook parameters, fingerprint, and provenance."""
    db = get_database()
    active = await get_active_usd_rulebook(db)
    active_doc = None
    if db is not None:
        active_doc = await db.rulebook_versions.find_one({"currency": "USD", "is_active": True})

    provenance = "database" if active_doc else "code_baseline"
    return {
        "currency": "USD",
        "fingerprint": active.fingerprint,
        "is_active": True,
        "provenance": provenance,
        "parameters": {
            "max_contacts_per_event": active.max_contacts_per_event,
            "cooldown_hours": active.cooldown_hours,
            "auto_authorize_below": active.auto_authorize_below,
            "never_auto_at_or_above": active.never_auto_at_or_above,
            "minimum_erv": active.minimum_erv,
            "max_retries_per_event": getattr(active, "max_retries_per_event", None),
            "retry_cooldown_hours": getattr(active, "retry_cooldown_hours", None),
        },
        "source_text": active.note,
        "created_at": active_doc["created_at"].isoformat() if active_doc and "created_at" in active_doc else None,
    }


@router.get("/history", summary="Get USD rulebook version history (public, sanitized)")
async def get_rules_history() -> list[dict[str, Any]]:
    """Return chronological history of USD rulebooks."""
    db = get_database()
    history = await list_usd_rulebook_history(db)
    # Sanitize and ensure text fields are plain
    sanitized: list[dict[str, Any]] = []
    for item in history:
        s = sanitize_public_dict(item)
        if isinstance(s.get("created_at"), datetime):
            s["created_at"] = s["created_at"].isoformat()
        sanitized.append(s)
    return sanitized
@router.get("/whoami", summary="Verify admin status (admin only)")
async def rules_whoami(admin: str = Depends(require_admin_token)) -> dict[str, bool]:
    """Return admin status when authenticated with valid X-Admin-Token."""
    return {"admin": True}
