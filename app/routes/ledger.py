"""Ledger route (public, sanitized; default currency from settings).

Provides GET /ledger with one row per event, newest first, limited to 200 rows.
The ledger's totals equal /metrics/summary for the same currency.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.metrics.reader import (
    distinct_recoveries,
    latest_per_event,
    load_snapshot,
    money,
)
from app.models.verification import assertion_of
from app.routes.events import database_ready
from app.sanitizer import sanitize_public_dict

router = APIRouter(tags=["ledger"])


class LedgerRow(BaseModel):
    """One row in the verified-vs-asserted ledger."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    event_id: str = Field(..., description="The unique event identifier.")
    customer_ref: str = Field(..., description="Merchant customer reference.")
    created_at: datetime = Field(..., description="Timestamp when event was ingested.")
    amount: float = Field(..., description="Revenue at risk in major units.")
    amount_at_risk: float = Field(..., description="Alias for amount.")
    currency: str = Field(default="INR", description="ISO currency code.")
    status: str = Field(..., description="Current event lifecycle status.")
    root_cause: str | None = Field(default=None, description="Diagnosed root cause.")
    diagnosis_method: str | None = Field(default=None, description="Diagnosis method (rules or llm).")
    intervention: str | None = Field(default=None, description="Recommended intervention.")
    policy_verdict: str | None = Field(default=None, description="Policy authorization verdict.")
    verdict: str | None = Field(default=None, description="Alias for policy_verdict.")
    gateway: str = Field(default="razorpay", description="Payment gateway (razorpay or paypal).")
    execution_ref: str | None = Field(default=None, description="Gateway or execution reference.")
    verified_amount: float = Field(default=0.0, ge=0, description="Deduplicated recovered amount.")
    assertion: str | None = Field(default=None, description="gateway_verified or merchant_asserted.")
    verified_at: datetime | None = Field(default=None, description="Verification timestamp.")


@router.get(
    "/ledger",
    response_model=list[LedgerRow],
    dependencies=[Depends(database_ready)],
    summary="Public sanitized ledger comparing asserted vs gateway-verified recovery",
)
async def get_ledger(
    currency: str | None = Query(default=None, description="Currency filter (e.g. USD, INR)"),
) -> list[LedgerRow]:
    """Return one row per event, newest first, limit 200.

    Sanitized and public. Totals equal /metrics/summary for the same currency.
    """
    settings = get_settings()
    curr = currency if currency is not None else settings.default_currency
    snapshot = await load_snapshot(currency=curr)

    latest_diagnoses = latest_per_event(snapshot.diagnoses)
    latest_decisions = latest_per_event(snapshot.decisions)
    latest_verdicts = latest_per_event(snapshot.verdicts)
    latest_executions = latest_per_event(snapshot.executions, key="executed_at")
    survivors, _ = distinct_recoveries(snapshot.verifications)

    # Group surviving verification records by event_id
    survivors_by_event: dict[str, list[dict[str, Any]]] = {}
    for doc in survivors.values():
        eid = doc.get("event_id")
        if eid:
            survivors_by_event.setdefault(eid, []).append(doc)

    rows: list[LedgerRow] = []
    # Sort events newest first by created_at
    sorted_events = sorted(
        snapshot.events,
        key=lambda e: e.get("created_at") or datetime.min,
        reverse=True,
    )

    for event in sorted_events[:200]:
        event_id = event["event_id"]
        diag = latest_diagnoses.get(event_id)
        dec = latest_decisions.get(event_id)
        ver = latest_verdicts.get(event_id)
        exe = latest_executions.get(event_id)
        ev_survivors = survivors_by_event.get(event_id, [])

        verified_amt = money(sum(s.get("amount_recovered", 0.0) for s in ev_survivors))
        assertion = assertion_of(ev_survivors[0]) if ev_survivors and verified_amt > 0 else None
        verified_time = (
            max(s.get("verified_at") for s in ev_survivors if s.get("verified_at"))
            if ev_survivors and verified_amt > 0
            else None
        )

        exec_ref = None
        if exe:
            exec_ref = exe.get("gateway_ref") or exe.get("razorpay_payment_link_id") or str(exe.get("_id"))

        amt = money(event.get("amount", 0.0))
        verdict_val = ver.get("verdict") if ver else None

        row = LedgerRow(
            event_id=event_id,
            customer_ref=event.get("customer_ref", ""),
            created_at=event.get("created_at"),
            amount=amt,
            amount_at_risk=amt,
            currency=event.get("currency", curr),
            status=event.get("status", "at_risk"),
            root_cause=diag.get("root_cause") if diag else None,
            diagnosis_method=diag.get("method") if diag else None,
            intervention=dec.get("intervention") if dec else None,
            policy_verdict=verdict_val,
            verdict=verdict_val,
            gateway=event.get("gateway", "razorpay"),
            execution_ref=exec_ref,
            verified_amount=verified_amt,
            assertion=assertion,
            verified_at=verified_time,
        )
        rows.append(row)

    # Sanitize rows to ensure no forbidden credential keys
    sanitized = [LedgerRow(**sanitize_public_dict(r.model_dump())) for r in rows]
    return sanitized
