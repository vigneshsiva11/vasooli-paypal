"""Orchestrator HTTP endpoints."""

from __future__ import annotations

import hmac
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.auth import require_admin_if_public_demo, require_admin_privileged
from app.config import get_settings
from app.db import get_database
from app.orchestrator.service import advance

router = APIRouter(
    prefix="/orchestrator",
    tags=["orchestrator"],
    dependencies=[Depends(require_admin_privileged)],
)


class TickRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=100, description="Maximum at_risk events to process")


@router.post("/run/{event_id}", dependencies=[Depends(require_admin_privileged)])
async def run_orchestrator_for_event(event_id: str) -> dict[str, Any]:
    """Manually advance an event through the recovery orchestrator."""
    return await advance(event_id)


@router.post("/tick", dependencies=[Depends(require_admin_privileged)])
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
