"""Agent gateway REST routes.

Endpoints:
- POST /agent/attempt: Public scenario runner (catalogue scenarios only, rate limited).
- GET /agent/scenarios: Public listing of catalogue scenarios.
- GET /agent/audit: Public sanitized audit log of agent operations (max 50).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.gateway import audit, catalogue, limits
from app.gateway.tools import validate_safe_string
from app.sanitizer import sanitize_public_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


def _derive_client_key(request: Request, salt: str = "vasooli_salt") -> tuple[str, int]:
    """Derive short salted hash of client IP and count XFF entries. Never returns raw address."""
    xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    xff_count = 0
    client_ip = "127.0.0.1"
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        xff_count = len(parts)
        if parts:
            client_ip = parts[-1]  # Rightmost X-Forwarded-For entry
    elif request.client and request.client.host:
        client_ip = request.client.host

    salted = f"{salt}:{client_ip}".encode("utf-8")
    short_hash = hashlib.sha256(salted).hexdigest()[:16]
    return short_hash, xff_count


class AgentAttemptRequest(BaseModel):
    """Request payload for attempting a catalogue attack scenario."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="ID of the catalogue scenario to execute.",
    )


@router.post(
    "/attempt",
    summary="Attempt an in-memory catalogue scenario (public, rate limited)",
    responses={
        200: {"description": "Scenario executed; outcome returned."},
        400: {"description": "Scenario unknown or invalid arguments."},
        429: {"description": "Rate limit exceeded (10/min client, 100/h global)."},
    },
)
async def attempt_scenario(body: AgentAttemptRequest, request: Request) -> dict[str, Any]:
    """Execute a catalogue scenario in memory through real pure functions.

    Publicly accessible (exempt from public-demo admin token requirement).
    Rate limited to 10 attempts/min per client and 100/h globally.
    """
    # 1. Validate scenario_id
    try:
        clean_scenario_id = validate_safe_string(body.scenario_id, field_name="scenario_id", max_len=64)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid scenario_id argument: {exc}",
        ) from exc

    if clean_scenario_id not in catalogue.get_scenario_ids():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown scenario_id {clean_scenario_id!r}. Must be one of {sorted(catalogue.get_scenario_ids())}",
        )

    # 2. Extract client IP and derive key
    settings = get_settings()
    salt = settings.admin_token or "vasooli_salt"
    client_key_hash, _ = _derive_client_key(request, salt=salt)

    # 3. Check and increment atomic limits (10/min per client, 100/h global)
    allowed, err_msg, retry_after = await limits.check_and_record_attempt(client_key_hash)
    if not allowed:
        headers = {"Retry-After": str(retry_after or 60)}
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=err_msg or "Rate limit exceeded.",
            headers=headers,
        )

    # 4. Run scenario
    try:
        result = await catalogue.run_scenario(
            scenario_id=clean_scenario_id,
            client_key_hash=client_key_hash,
            record_audit_log=True,
        )
        return sanitize_public_dict(result)
    except Exception as exc:
        logger.warning("Scenario execution failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Scenario execution failed: {exc}",
        ) from exc


@router.get(
    "/scenarios",
    summary="List available in-memory attack scenarios",
)
async def get_scenarios() -> list[dict[str, Any]]:
    """Return all available in-memory catalogue scenarios."""
    scenarios = catalogue.list_scenarios()
    return sanitize_public_dict(scenarios)


@router.get(
    "/audit",
    summary="Get recent agent audit log entries (public, sanitized, max 50)",
)
async def get_audit(
    limit: int = Query(default=20, ge=1, le=50, description="Max entries to return (max 50)"),
) -> list[dict[str, Any]]:
    """Return recent agent audit entries, sanitized."""
    records = await audit.get_recent_audit_records(limit=limit)
    return sanitize_public_dict(records)

