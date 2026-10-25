"""Orchestrator HTTP endpoints."""

from __future__ import annotations

import hmac
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import get_database
from app.orchestrator.service import advance

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


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


class TickRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=100, description="Maximum at_risk events to process")


@router.post("/run/{event_id}", dependencies=[Depends(require_admin_if_public_demo)])
async def run_orchestrator_for_event(event_id: str) -> dict[str, Any]:
    """Manually advance an event through the recovery orchestrator."""
    return await advance(event_id)


@router.post("/tick", dependencies=[Depends(require_admin_if_public_demo)])
async def orchestrator_tick(
    payload: TickRequest | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
) -> dict[str, Any]:
    """Process up to N at_risk PayPal events through the orchestrator."""
    effective_limit = payload.limit if payload is not None and payload.limit != 10 else (limit or 10)
    db = get_database()
    cursor = db.events.find(
        {"gateway": "paypal", "status": "at_risk"},
        {"event_id": 1},
    ).limit(effective_limit)

    event_ids = [doc["event_id"] async for doc in cursor]
    results = []
    for ev_id in event_ids:
        res = await advance(ev_id, db=db)
        results.append(res)

    return {
        "processed": len(results),
        "limit": effective_limit,
        "results": results,
    }
