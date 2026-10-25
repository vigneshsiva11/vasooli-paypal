"""Simulation clock management endpoints."""

from __future__ import annotations

import hmac
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.clock import MongoClock, advance_mongo_clock, get_clock
from app.config import get_settings

router = APIRouter(prefix="/sim", tags=["simulation"])


def require_admin_if_public_demo(request: Request) -> None:
    """Ensure admin token is provided when public demo mode is enabled."""
    settings = get_settings()
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


class SimAdvanceRequest(BaseModel):
    hours: float = Field(default=0.0, description="Number of hours to advance the clock")


@router.post("/advance", dependencies=[Depends(require_admin_if_public_demo)])
async def advance_clock(
    payload: SimAdvanceRequest | None = None,
    hours: float | None = None,
) -> dict[str, Any]:
    """Advance the simulated clock by the specified number of hours."""
    effective_hours = payload.hours if payload is not None else (hours or 0.0)
    clock = await advance_mongo_clock(effective_hours)
    return {
        "now": clock.now(),
        "is_simulated": clock.is_simulated(),
        "offset_seconds": clock.offset_seconds,
        "offset_hours": clock.offset_seconds / 3600.0,
    }


@router.get("/clock", dependencies=[Depends(require_admin_if_public_demo)])
async def get_sim_clock() -> dict[str, Any]:
    """Retrieve the current clock time and simulation status."""
    clock = await get_clock()
    return {
        "now": clock.now(),
        "is_simulated": clock.is_simulated(),
        "offset_seconds": clock.offset_seconds,
        "offset_hours": clock.offset_seconds / 3600.0,
    }
