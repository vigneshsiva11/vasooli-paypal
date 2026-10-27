"""Demo reset and scenario runner routes (Phase 2).

Provides:
- POST /demo/reset: Drops and re-ensures indexes for all operational collections.
  Refuses unless DB is in {"vasooli_demo", "vasooli_regress"} and PayPal env is sandbox.
- POST /demo/scenarios/insufficient-funds: Runs the automated 7-step demo scenario.
- GET /demo/runs/{id}: Public status of a scenario run (ids, amounts, status only).
- GET /demo/runs: Public list of recent scenario runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.clock import advance_mongo_clock, reset_mongo_clock, set_mongo_clock_offset, _utc_now, get_clock
from app.config import get_settings
from app.db import get_database
from app.sanitizer import sanitize_public_dict
from app.decision import ensure_indexes as ensure_decision_indexes
from app.diagnosis import ensure_indexes as ensure_diagnosis_indexes
from app.execution import ensure_indexes as ensure_execution_indexes
from app.gateways.paypal import orders
from app.gateways.paypal.charge_attempts import ensure_indexes as ensure_charge_indexes
from app.gateways.paypal.vault import ensure_indexes as ensure_vault_indexes
from app.ingestion import ensure_indexes as ensure_event_indexes
from app.auth import require_admin_privileged
from app.orchestrator.runs import ensure_orchestrator_indexes
from app.orchestrator.service import advance as orchestrator_advance
from app.policy import ensure_indexes as ensure_policy_indexes
from app.routes.paypal_webhooks import ensure_paypal_event_indexes
from app.webhooks import ensure_indexes as ensure_verification_indexes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["demo"])

ALLOWED_DEMO_DBS = {"vasooli_demo", "vasooli_regress"}

OPERATIONAL_COLLECTIONS = [
    "events",
    "diagnoses",
    "decisions",
    "policy_verdicts",
    "executions",
    "verifications",
    "charge_attempts",
    "customer_vault",
    "orchestrator_runs",
    "demo_runs",
    "paypal_events",
    "sim_clock",
]


# ---------------------------------------------------------------------------
# Timeout and Staleness Configuration
# ---------------------------------------------------------------------------
STALE_RUN_TIMEOUT_SECONDS = 600.0  # 10 minutes
WORST_OBSERVED_WEBHOOK_LATENCY_SECONDS = 61.85  # Order A live fixture
STEP_TIMEOUT_SECONDS = max(180.0, 3 * WORST_OBSERVED_WEBHOOK_LATENCY_SECONDS)  # 185.55s
TOTAL_RUN_TIMEOUT_SECONDS = 12 * 60.0  # 720.0s (12 minutes)


def _parse_ts(val: Any) -> datetime | None:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


async def self_heal_stale_runs(db: Any = None) -> int:
    """Self-heal any demo runs still marked 'running' whose last step update is older than 10 minutes.

    Marks them failed(step, "stale") and releases the active-run lock.
    """
    database = db if db is not None else get_database()
    now = _utc_now()
    threshold = now - timedelta(seconds=STALE_RUN_TIMEOUT_SECONDS)

    cursor = database.demo_runs.find({"status": "running"})
    running_runs = await cursor.to_list(length=100)
    healed_count = 0

    for run in running_runs:
        timestamps: list[datetime] = []
        for ts_val in (run.get("updated_at"), run.get("created_at")):
            parsed = _parse_ts(ts_val)
            if parsed:
                timestamps.append(parsed)

        for s in run.get("steps", []):
            for step_ts in (s.get("started_at"), s.get("finished_at")):
                parsed = _parse_ts(step_ts)
                if parsed:
                    timestamps.append(parsed)

        last_update = max(timestamps) if timestamps else None
        if last_update and last_update < threshold:
            curr_step = run.get("current_step") or "onboard"
            curr_idx = 0
            steps = run.get("steps", [])
            for i, s in enumerate(steps):
                if s.get("name") == curr_step:
                    curr_idx = i
                    break

            filter_q: dict[str, Any] = {"status": "running"}
            if "_id" in run:
                filter_q["_id"] = run["_id"]
            elif "run_id" in run:
                filter_q["run_id"] = run["run_id"]

            await database.demo_runs.update_one(
                filter_q,
                {
                    "$set": {
                        "status": "failed",
                        "final_status": f'failed({curr_step}, "stale")',
                        "updated_at": now,
                        f"steps.{curr_idx}.status": "failed",
                        f"steps.{curr_idx}.finished_at": now,
                        f"steps.{curr_idx}.refs.error": "stale",
                    }
                },
            )
            if run.get("initial_clock_offset") is not None:
                try:
                    await set_mongo_clock_offset(run["initial_clock_offset"], db=database)
                except Exception as exc:
                    logger.warning("Failed to restore clock offset during self-heal: %s", exc)
            healed_count += 1
            logger.info(
                "Self-healed stale demo run %s (stale at step %s, last update was %s)",
                run.get("run_id"),
                curr_step,
                last_update,
            )

    return healed_count


async def ensure_demo_run_indexes(db: Any = None) -> None:
    """Ensure indexes for demo_runs and demo_counters collections."""
    database = db if db is not None else get_database()
    await database["demo_runs"].create_index("run_id", unique=True)
    await database["demo_runs"].create_index([("created_at", -1)])
    await database["demo_runs"].create_index("status")
    await database["demo_runs"].create_index(
        [("status", 1)],
        unique=True,
        partialFilterExpression={"status": "running"},
        name="uniq_running_demo_run",
    )
    # TTL index on demo_counters (expires after 1 hour / 3600 seconds)
    await database["demo_counters"].create_index(
        [("created_at", 1)],
        expireAfterSeconds=3600,
        name="ttl_created_at_3600",
    )
    await database["demo_counters"].create_index([("key", 1), ("created_at", -1)])


async def reset_demo_database(db: Any = None) -> dict[str, Any]:
    """Drop and re-ensure indexes for all operational collections.

    Refuses unless DB is in ALLOWED_DEMO_DBS and PayPal env is sandbox.
    """
    settings = get_settings()
    if isinstance(db, str):
        db_name = db
        client = AsyncIOMotorClient(settings.mongodb_uri)
        database = client[db_name]
    else:
        database = db if db is not None else get_database()
        db_name = getattr(database, "name", str(database))

    if db_name not in ALLOWED_DEMO_DBS:
        raise ValueError(
            f"Reset refused: database {db_name!r} not in allowlist {sorted(ALLOWED_DEMO_DBS)}"
        )

    if settings.paypal_environment != "sandbox":
        raise ValueError(
            f"Reset refused: PayPal environment is {settings.paypal_environment!r}, must be 'sandbox'"
        )

    # 1. Drop operational collections
    for col_name in OPERATIONAL_COLLECTIONS:
        try:
            await database[col_name].drop()
        except Exception as exc:
            logger.debug("Drop collection %s: %s", col_name, exc)
    try:
        await database["demo_counters"].drop()
    except Exception as exc:
        logger.debug("Drop demo_counters: %s", exc)

    # 2. Re-ensure indexes
    await ensure_event_indexes()
    await ensure_diagnosis_indexes()
    await ensure_decision_indexes()
    await ensure_policy_indexes()
    await ensure_execution_indexes()
    await ensure_verification_indexes()
    await ensure_charge_indexes()
    await ensure_vault_indexes()
    await ensure_orchestrator_indexes(database)
    await ensure_paypal_event_indexes()
    await ensure_demo_run_indexes(database)
    await reset_mongo_clock(database)

    return {
        "status": "ok",
        "database": db_name,
        "dropped_collections": OPERATIONAL_COLLECTIONS,
    }


@router.post("/reset", dependencies=[Depends(require_admin_privileged)])
async def demo_reset() -> dict[str, Any]:
    """Admin-guarded demo database reset."""
    try:
        res = await reset_demo_database()
        return res
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


class InsufficientFundsScenarioRequest(BaseModel):
    customer_ref: str | None = Field(
        default=None,
        description="Customer reference, must start with 'demo-' if provided.",
    )
    amount: float = Field(
        default=49.00,
        description="Amount in USD, default 49.00, maximum 250.00.",
    )
    advance_hours: float = Field(
        default=0.0,
        description="Hours to advance simulated clock during step 5.",
    )


STEP_NAMES = [
    "onboard",
    "renewal_attempt",
    "decline_ingested",
    "pipeline",
    "clock",
    "retry_executed",
    "verified",
]


@router.post(
    "/scenarios/insufficient-funds",
    dependencies=[Depends(require_admin_privileged)],
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_insufficient_funds_scenario(
    payload: InsufficientFundsScenarioRequest | None = None,
) -> dict[str, Any]:
    """Initiate the server-side insufficient-funds scenario runner."""
    settings = get_settings()
    if settings.paypal_environment != "sandbox":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refused: PayPal environment must be 'sandbox'",
        )

    req = payload or InsufficientFundsScenarioRequest()
    amount = float(req.amount)
    if amount <= 0 or amount > 250.00:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount must be between 0.01 and 250.00 USD",
        )

    customer_ref = req.customer_ref
    if customer_ref is None:
        customer_ref = f"demo-{uuid.uuid4().hex[:8]}"
    else:
        if not customer_ref.startswith("demo-"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="customer_ref must start with 'demo-'",
            )

    db = get_database()
    await ensure_demo_run_indexes(db)

    # 0. Self-heal stale runs (>10m without step update)
    await self_heal_stale_runs(db)

    # 1. Single active run check
    active_run = await db.demo_runs.find_one({"status": "running"})
    if active_run:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A demo scenario run is already in progress",
        )

    # 2. Rate limit: at most 10 runs per hour
    one_hour_ago = _utc_now() - timedelta(hours=1)
    runs_in_last_hour = await db.demo_runs.count_documents(
        {"created_at": {"$gte": one_hour_ago}}
    )
    if runs_in_last_hour >= 10:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded: maximum 10 demo runs per hour",
        )

    run_id = str(ObjectId())
    steps = [
        {
            "name": name,
            "status": "pending",
            "started_at": None,
            "finished_at": None,
            "source": None,
            "refs": {},
        }
        for name in STEP_NAMES
    ]

    run_doc = {
        "_id": ObjectId(run_id),
        "run_id": run_id,
        "scenario": "insufficient-funds",
        "customer_ref": customer_ref,
        "amount": amount,
        "currency": "USD",
        "advance_hours": req.advance_hours,
        "status": "running",
        "current_step": "onboard",
        "steps": steps,
        "final_status": None,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    await db.demo_runs.insert_one(run_doc)

    asyncio.create_task(
        _execute_scenario_run(
            run_id=run_id,
            customer_ref=customer_ref,
            amount=amount,
            advance_hours=req.advance_hours,
        )
    )

    return {
        "run_id": run_id,
        "status": "running",
        "customer_ref": customer_ref,
        "amount": amount,
        "advance_hours": req.advance_hours,
    }


async def _execute_scenario_run(
    *,
    run_id: str,
    customer_ref: str,
    amount: float,
    advance_hours: float,
    restore_clock_offset: float | None = None,
) -> None:
    """Background task advancing the insufficient-funds demo run."""
    db = get_database()
    current_event_id: str | None = None
    step_idx = 0
    run_start_time = time.monotonic()

    async def update_step(idx: int, **fields: Any) -> None:
        set_dict = {f"steps.{idx}.{k}": v for k, v in fields.items()}
        set_dict["updated_at"] = _utc_now()
        if "name" in fields or idx < len(STEP_NAMES):
            set_dict["current_step"] = STEP_NAMES[idx]
        await db.demo_runs.update_one({"run_id": run_id}, {"$set": set_dict})

    async def set_run_final(final_status_val: str, final_status_label: str) -> None:
        await db.demo_runs.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    "status": final_status_val,
                    "final_status": final_status_label,
                    "updated_at": _utc_now(),
                }
            },
        )

    try:
        # -------------------------------------------------------------------
        # Step 1: onboard
        # -------------------------------------------------------------------
        step_idx = 0
        await update_step(step_idx, status="running", started_at=_utc_now(), source="paypal")
        vault_doc = await db.customer_vault.find_one(
            {"customer_ref": customer_ref, "status": "active"}
        )
        if vault_doc:
            await update_step(
                step_idx,
                status="completed",
                finished_at=_utc_now(),
                source="customer_vault",
                refs={
                    "vaulted": True,
                    "customer_ref": customer_ref,
                },
            )
        else:
            order = await orders.create_and_capture_order(
                amount=amount,
                currency="USD",
                customer_ref=customer_ref,
                custom_id=f"cust:{customer_ref}",
                vault_on_success=True,
                purpose="onboarding",
                db=db,
            )
            order_id = order.get("id")

            token_found = None
            start_t = time.monotonic()
            while time.monotonic() - start_t < STEP_TIMEOUT_SECONDS:
                if time.monotonic() - run_start_time >= TOTAL_RUN_TIMEOUT_SECONDS:
                    break
                token_found = await db.customer_vault.find_one(
                    {"customer_ref": customer_ref, "status": "active"}
                )
                if token_found:
                    break
                await asyncio.sleep(0.5)

            if not token_found:
                await update_step(
                    step_idx,
                    status="timeout",
                    finished_at=_utc_now(),
                    refs={"order_id": order_id, "customer_ref": customer_ref},
                )
                await set_run_final("timeout", "timeout(onboard)")
                return

            await update_step(
                step_idx,
                status="completed",
                finished_at=_utc_now(),
                refs={
                    "order_id": order_id,
                    "vaulted": True,
                    "customer_ref": customer_ref,
                },
            )

        # -------------------------------------------------------------------
        # Step 2: renewal_attempt
        # -------------------------------------------------------------------
        step_idx = 1
        if time.monotonic() - run_start_time >= TOTAL_RUN_TIMEOUT_SECONDS:
            await update_step(step_idx, status="timeout", finished_at=_utc_now(), refs={"error": "total_run_cap_exceeded"})
            await set_run_final("timeout", f"timeout({STEP_NAMES[step_idx]})")
            return

        await update_step(
            step_idx, status="running", started_at=_utc_now(), source="paypal_sandbox_simulation"
        )
        decline_card = {
            **orders.SANDBOX_TEST_VISA,
            "name": "CCREJECT-IF",
        }
        decline_order = await orders.create_and_capture_order(
            amount=amount,
            currency="USD",
            card=decline_card,
            customer_ref=customer_ref,
            custom_id=f"cust:{customer_ref}",
            purpose="renewal",
            vault_on_success=False,
            db=db,
        )
        decline_order_id = decline_order.get("id")
        cap_status = decline_order.get("capture_status")
        resp_code = decline_order.get("processor_response_code")
        await update_step(
            step_idx,
            status="completed",
            finished_at=_utc_now(),
            refs={
                "order_id": decline_order_id,
                "capture_status": cap_status,
                "processor_response_code": resp_code,
            },
        )

        # -------------------------------------------------------------------
        # Step 3: decline_ingested
        # -------------------------------------------------------------------
        step_idx = 2
        if time.monotonic() - run_start_time >= TOTAL_RUN_TIMEOUT_SECONDS:
            await update_step(step_idx, status="timeout", finished_at=_utc_now(), refs={"error": "total_run_cap_exceeded"})
            await set_run_final("timeout", f"timeout({STEP_NAMES[step_idx]})")
            return

        await update_step(
            step_idx, status="running", started_at=_utc_now(), source="paypal_webhook"
        )
        event_doc = None
        start_t = time.monotonic()
        while time.monotonic() - start_t < STEP_TIMEOUT_SECONDS:
            if time.monotonic() - run_start_time >= TOTAL_RUN_TIMEOUT_SECONDS:
                break
            event_doc = await db.events.find_one(
                {"customer_ref": customer_ref, "status": "at_risk"}
            )
            if event_doc:
                break
            await asyncio.sleep(0.5)

        if not event_doc:
            await update_step(
                step_idx,
                status="timeout",
                finished_at=_utc_now(),
                refs={"order_id": decline_order_id, "customer_ref": customer_ref},
            )
            await set_run_final("timeout", "timeout(decline_ingested)")
            return

        current_event_id = event_doc.get("event_id")
        await update_step(
            step_idx,
            status="completed",
            finished_at=_utc_now(),
            refs={"event_id": current_event_id, "order_id": decline_order_id},
        )

        # -------------------------------------------------------------------
        # Step 4: pipeline
        # -------------------------------------------------------------------
        step_idx = 3
        if time.monotonic() - run_start_time >= TOTAL_RUN_TIMEOUT_SECONDS:
            await update_step(step_idx, status="timeout", finished_at=_utc_now(), refs={"error": "total_run_cap_exceeded"})
            await set_run_final("timeout", f"timeout({STEP_NAMES[step_idx]})")
            return

        await update_step(
            step_idx, status="running", started_at=_utc_now(), source="orchestrator"
        )
        await orchestrator_advance(current_event_id, db=db)

        diag = await db.diagnoses.find_one(
            {"event_id": current_event_id}, sort=[("version", -1)]
        )
        dec = await db.decisions.find_one(
            {"event_id": current_event_id}, sort=[("version", -1)]
        )
        verdict = await db.policy_verdicts.find_one(
            {"event_id": current_event_id}, sort=[("version", -1)]
        )
        exec_doc = await db.executions.find_one({"event_id": current_event_id})

        pipeline_refs = {
            "diagnosis_id": str(diag["_id"]) if diag else None,
            "decision_id": str(dec["_id"]) if dec else None,
            "policy_verdict_id": str(verdict["_id"]) if verdict else None,
            "verdict": verdict.get("verdict") if verdict else None,
            "execution_id": str(exec_doc["_id"]) if exec_doc else None,
        }

        if verdict and verdict.get("verdict") in ("blocked", "disallowed"):
            await update_step(step_idx, status="completed", finished_at=_utc_now(), refs=pipeline_refs)
            await set_run_final("blocked", "blocked (verdict)")
            return

        if (dec and dec.get("requires_manual_review")) or (
            verdict and verdict.get("verdict") == "manual_review"
        ):
            await update_step(step_idx, status="completed", finished_at=_utc_now(), refs=pipeline_refs)
            await set_run_final("manual_review", "manual_review")
            return

        if not exec_doc:
            await update_step(step_idx, status="failed", finished_at=_utc_now(), refs=pipeline_refs)
            await set_run_final("failed", "failed(pipeline, no_execution)")
            return

        await update_step(step_idx, status="completed", finished_at=_utc_now(), refs=pipeline_refs)

        # -------------------------------------------------------------------
        # Step 5: clock
        # -------------------------------------------------------------------
        step_idx = 4
        if time.monotonic() - run_start_time >= TOTAL_RUN_TIMEOUT_SECONDS:
            await update_step(step_idx, status="timeout", finished_at=_utc_now(), refs={"error": "total_run_cap_exceeded"})
            await set_run_final("timeout", f"timeout({STEP_NAMES[step_idx]})")
            return

        if advance_hours > 0:
            await update_step(
                step_idx, status="running", started_at=_utc_now(), source="simulated_clock"
            )
            await advance_mongo_clock(advance_hours, db=db)
            await update_step(
                step_idx,
                status="completed",
                finished_at=_utc_now(),
                refs={"advance_hours": advance_hours},
            )
        else:
            await update_step(
                step_idx,
                status="skipped",
                started_at=_utc_now(),
                finished_at=_utc_now(),
                source="simulated_clock",
                refs={"advance_hours": 0},
            )

        # -------------------------------------------------------------------
        # Step 6: retry_executed
        # -------------------------------------------------------------------
        step_idx = 5
        if time.monotonic() - run_start_time >= TOTAL_RUN_TIMEOUT_SECONDS:
            await update_step(step_idx, status="timeout", finished_at=_utc_now(), refs={"error": "total_run_cap_exceeded"})
            await set_run_final("timeout", f"timeout({STEP_NAMES[step_idx]})")
            return

        await update_step(
            step_idx, status="running", started_at=_utc_now(), source="paypal"
        )
        retry_exec = await db.executions.find_one(
            {"event_id": current_event_id, "gateway": "paypal"}
        )
        if not retry_exec:
            await update_step(
                step_idx,
                status="failed",
                finished_at=_utc_now(),
                refs={"error": "no_execution_found"},
            )
            await set_run_final("failed", "failed(retry_executed, no_execution_found)")
            return

        retry_refs = {
            "execution_id": str(retry_exec["_id"]),
            "gateway_ref": retry_exec.get("gateway_ref"),
            "successful": retry_exec.get("successful", True),
            "capture_status": retry_exec.get("capture_status"),
            "processor_response_code": retry_exec.get("processor_response_code"),
        }
        await update_step(step_idx, status="completed", finished_at=_utc_now(), refs=retry_refs)

        # -------------------------------------------------------------------
        # Step 7: verified
        # -------------------------------------------------------------------
        step_idx = 6
        if time.monotonic() - run_start_time >= TOTAL_RUN_TIMEOUT_SECONDS:
            await update_step(step_idx, status="timeout", finished_at=_utc_now(), refs={"error": "total_run_cap_exceeded"})
            await set_run_final("timeout", f"timeout({STEP_NAMES[step_idx]})")
            return

        await update_step(
            step_idx, status="running", started_at=_utc_now(), source="paypal_webhook"
        )
        verif_doc = None
        ev = None
        start_t = time.monotonic()
        while time.monotonic() - start_t < STEP_TIMEOUT_SECONDS:
            if time.monotonic() - run_start_time >= TOTAL_RUN_TIMEOUT_SECONDS:
                break
            verif_doc = await db.verifications.find_one(
                {"event_id": current_event_id, "assertion": "gateway_verified"}
            )
            ev = await db.events.find_one({"event_id": current_event_id})
            if verif_doc and ev and ev.get("status") == "recovered":
                break
            await asyncio.sleep(0.5)

        if verif_doc and ev and ev.get("status") == "recovered":
            await update_step(
                step_idx,
                status="completed",
                finished_at=_utc_now(),
                refs={
                    "verification_id": str(verif_doc["_id"]),
                    "event_id": current_event_id,
                    "outcome": verif_doc.get("outcome"),
                },
            )
            await set_run_final("recovered", "recovered")
        else:
            await update_step(
                step_idx,
                status="timeout",
                finished_at=_utc_now(),
                refs={"event_id": current_event_id},
            )
            await set_run_final("timeout", "timeout(verified)")

    except Exception as exc:
        logger.error(
            "Demo run error: event_id=%s, error_type=%s",
            current_event_id,
            type(exc).__name__,
        )
        step_name = STEP_NAMES[step_idx] if step_idx < len(STEP_NAMES) else "unknown"
        await db.demo_runs.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    "status": "failed",
                    "final_status": f"failed({step_name}, {type(exc).__name__})",
                    f"steps.{step_idx}.status": "failed",
                    f"steps.{step_idx}.finished_at": _utc_now(),
                    f"steps.{step_idx}.refs.error": type(exc).__name__,
                    "updated_at": _utc_now(),
                }
            },
        )
    finally:
        if restore_clock_offset is not None:
            try:
                await set_mongo_clock_offset(restore_clock_offset, db=db)
            except Exception as exc:
                logger.warning(
                    "Failed to restore clock offset after demo run %s: %s",
                    run_id,
                    exc,
                )


FORBIDDEN_PUBLIC_KEYS = {
    "vault_token_id",
    "vault_id",
    "email",
    "buyer_email",
    "card",
    "card_digits",
    "last_digits",
    "card_number",
    "payer_id",
    "payer",
}


def _clean_step_refs(refs: Any) -> dict[str, Any]:
    if not isinstance(refs, dict):
        return {}
    cleaned = {}
    for k, v in refs.items():
        k_str = str(k)
        k_lower = k_str.lower()
        if k_str in FORBIDDEN_PUBLIC_KEYS or any(bad in k_lower for bad in ("vault", "email", "card", "payer", "token")):
            continue
        cleaned[k_str] = v
    return sanitize_public_dict(cleaned)


def _format_public_run(doc: dict[str, Any]) -> dict[str, Any]:
    """Strip Mongo internals and ensure only public ids/amounts are returned.

    Never exposes vault token ids, emails, card digits or payer ids.
    """
    clean_steps = []
    for s in doc.get("steps", []):
        if isinstance(s, dict):
            clean_steps.append({
                "name": s.get("name"),
                "status": s.get("status"),
                "started_at": s.get("started_at"),
                "finished_at": s.get("finished_at"),
                "source": s.get("source"),
                "refs": _clean_step_refs(s.get("refs")),
            })
        else:
            clean_steps.append(s)

    formatted = {
        "run_id": doc.get("run_id") or str(doc.get("_id")),
        "scenario": doc.get("scenario"),
        "customer_ref": doc.get("customer_ref"),
        "amount": doc.get("amount"),
        "currency": doc.get("currency", "USD"),
        "advance_hours": doc.get("advance_hours", 0.0),
        "status": doc.get("status"),
        "current_step": doc.get("current_step"),
        "steps": clean_steps,
        "final_status": doc.get("final_status"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }
    return sanitize_public_dict(formatted)


@router.get("/runs/{id}")
async def get_demo_run(id: str) -> dict[str, Any]:
    """Public GET for a single demo run (ids and amounts only, no secrets)."""
    db = get_database()
    await self_heal_stale_runs(db)
    query: dict[str, Any] = {"run_id": id}
    if ObjectId.is_valid(id):
        query = {"$or": [{"run_id": id}, {"_id": ObjectId(id)}]}

    doc = await db.demo_runs.find_one(query)
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Demo run {id!r} not found",
        )
    return _format_public_run(doc)


@router.get("/runs")
async def list_demo_runs() -> dict[str, Any]:
    """Public GET for listing recent demo runs (ids and amounts only, no secrets)."""
    db = get_database()
    await self_heal_stale_runs(db)
    cursor = db.demo_runs.find().sort("created_at", -1).limit(50)
    runs = [
        _format_public_run(doc)
        async for doc in cursor
    ]
    return {"runs": runs, "total": len(runs)}


def _get_client_ip(request: Request) -> str:
    """Extract client IP using the rightmost X-Forwarded-For entry (else socket address).

    Using the rightmost entry prevents client IP spoofing when behind a trusted reverse proxy.
    """
    xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def _derive_client_key(request: Request, salt: str = "vasooli_client_salt") -> tuple[str, int]:
    """Derive short salted hash of client key and count XFF entries. Never leaks the address."""
    xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    xff_count = 0
    client_ip = "127.0.0.1"
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        xff_count = len(parts)
        if parts:
            client_ip = parts[-1]
    elif request.client and request.client.host:
        client_ip = request.client.host

    salted = f"{salt}:{client_ip}".encode("utf-8")
    short_hash = hashlib.sha256(salted).hexdigest()[:16]
    return short_hash, xff_count


@router.get(
    "/client-key",
    dependencies=[Depends(require_admin_privileged)],
    summary="Get derived client key hash and XFF count (admin guarded)",
)
async def get_demo_client_key(request: Request) -> dict[str, Any]:
    """Return a short salted hash of the derived client key and XFF count. Never returns the address."""
    settings = get_settings()
    salt = settings.admin_token or "vasooli_salt"
    client_key_hash, xff_count = _derive_client_key(request, salt=salt)
    return {
        "client_key_hash": client_key_hash,
        "xff_entries": xff_count,
    }


class PublicDemoRunRequest(BaseModel):
    amount: float = Field(
        default=49.00,
        description="Amount in USD, 10.00 to 100.00.",
    )


@router.post(
    "/public/run",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a public rate-limited demo run",
)
async def run_public_demo(
    request: Request,
    payload: PublicDemoRunRequest | None = None,
) -> dict[str, Any]:
    """Public scenario runner endpoint.

    Requirements:
    - Disabled unless PUBLIC_RUNS=true (403).
    - Sandbox only (assert paypal_environment == 'sandbox').
    - Body ignored except amount (10.00 to 100.00 USD, default 49.00).
    - customer_ref always auto-generated 'demo-<8 hex>'.
    - Returns 202 with run_id immediately; runs in background.
    - One active run globally (409 Conflict).
    - At most 3 per hour per client and 10 per hour globally (429).
    - At least 60 seconds between runs globally (429 with Retry-After).
    - Client key is the RIGHTMOST X-Forwarded-For entry (else socket address).
    - Simulated clock offset recorded before run and restored afterwards on success,
      failure, and timeout (no reset ever).
    """
    settings = get_settings()
    if not getattr(settings, "public_runs", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public demo runs are disabled",
        )

    if settings.paypal_environment != "sandbox":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refused: PayPal environment must be 'sandbox'",
        )

    amount = float(payload.amount if payload is not None and payload.amount is not None else 49.00)
    if amount < 10.00 or amount > 100.00:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount must be between 10.00 and 100.00 USD",
        )

    customer_ref = f"demo-{uuid.uuid4().hex[:8]}"

    db = get_database()
    await ensure_demo_run_indexes(db)
    await self_heal_stale_runs(db)

    # 1. Fast pre-check: Single active run globally (409 Conflict)
    active_run = await db.demo_runs.find_one({"status": "running"})
    if active_run:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A demo scenario run is already in progress",
        )

    now = _utc_now()
    client_ip = _get_client_ip(request)
    client_key_hash, xff_entries = _derive_client_key(request, salt=settings.admin_token or "vasooli_salt")

    # 2. Spacing check: at least 60 seconds between runs globally (429 with Retry-After)
    latest_run = await db.demo_runs.find_one({}, sort=[("created_at", -1)])
    if latest_run and latest_run.get("created_at"):
        created_dt = _parse_ts(latest_run["created_at"])
        if created_dt:
            elapsed = (now - created_dt).total_seconds()
            if elapsed < 60.0:
                wait_seconds = max(1, int(60.0 - elapsed) + 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Spacing limit: please wait {wait_seconds}s before triggering another demo run",
                    headers={"Retry-After": str(wait_seconds)},
                )

    one_hour_ago = now - timedelta(hours=1)
    client_counter_key = f"client:{client_key_hash}"
    global_counter_key = "global"

    # 3. Per-client rate limit: at most 3 runs per hour per client
    c_count = await db.demo_counters.count_documents(
        {"key": client_counter_key, "created_at": {"$gte": one_hour_ago}}
    )
    r_count = await db.demo_runs.count_documents(
        {"client_ip": client_ip, "created_at": {"$gte": one_hour_ago}}
    )
    client_runs_last_hour = max(c_count, r_count)

    if client_runs_last_hour >= 3:
        oldest_client_run = await db.demo_counters.find_one(
            {"key": client_counter_key, "created_at": {"$gte": one_hour_ago}},
            sort=[("created_at", 1)],
        )
        if not oldest_client_run:
            oldest_client_run = await db.demo_runs.find_one(
                {"client_ip": client_ip, "created_at": {"$gte": one_hour_ago}},
                sort=[("created_at", 1)],
            )
        wait_s = 3600
        if oldest_client_run and oldest_client_run.get("created_at"):
            c_dt = _parse_ts(oldest_client_run["created_at"])
            if c_dt:
                wait_s = max(1, int(3600 - (now - c_dt).total_seconds()))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Client rate limit exceeded: maximum 3 demo runs per hour",
            headers={"Retry-After": str(wait_s)},
        )

    # 4. Global rate limit: at most 10 runs per hour globally
    gc_count = await db.demo_counters.count_documents(
        {"key": global_counter_key, "created_at": {"$gte": one_hour_ago}}
    )
    gr_count = await db.demo_runs.count_documents(
        {"created_at": {"$gte": one_hour_ago}}
    )
    global_runs_last_hour = max(gc_count, gr_count)

    if global_runs_last_hour >= 10:
        oldest_global_run = await db.demo_counters.find_one(
            {"key": global_counter_key, "created_at": {"$gte": one_hour_ago}},
            sort=[("created_at", 1)],
        )
        if not oldest_global_run:
            oldest_global_run = await db.demo_runs.find_one(
                {"created_at": {"$gte": one_hour_ago}},
                sort=[("created_at", 1)],
            )
        wait_s = 3600
        if oldest_global_run and oldest_global_run.get("created_at"):
            c_dt = _parse_ts(oldest_global_run["created_at"])
            if c_dt:
                wait_s = max(1, int(3600 - (now - c_dt).total_seconds()))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Global rate limit exceeded: maximum 10 demo runs per hour",
            headers={"Retry-After": str(wait_s)},
        )

    clock = await get_clock(db)
    initial_offset_seconds = clock.offset_seconds

    run_id = str(ObjectId())
    steps = [
        {
            "name": name,
            "status": "pending",
            "started_at": None,
            "finished_at": None,
            "source": None,
            "refs": {},
        }
        for name in STEP_NAMES
    ]

    run_doc = {
        "_id": ObjectId(run_id),
        "run_id": run_id,
        "scenario": "insufficient-funds",
        "customer_ref": customer_ref,
        "amount": amount,
        "currency": "USD",
        "advance_hours": 25.0,
        "client_ip": client_ip,
        "client_key": client_key_hash,
        "initial_clock_offset": initial_offset_seconds,
        "status": "running",
        "current_step": "onboard",
        "steps": steps,
        "final_status": None,
        "created_at": now,
        "updated_at": now,
    }

    # 5. Atomic insert guarded by unique partial index uniq_running_demo_run
    try:
        await db.demo_runs.insert_one(run_doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A demo scenario run is already in progress",
        )

    # Record in demo_counters collection (guarded by TTL index)
    try:
        await db.demo_counters.insert_one({
            "key": client_counter_key,
            "run_id": run_id,
            "created_at": now,
        })
        await db.demo_counters.insert_one({
            "key": global_counter_key,
            "run_id": run_id,
            "created_at": now,
        })
    except Exception as exc:
        logger.debug("Counter insertion note: %s", exc)

    asyncio.create_task(
        _execute_scenario_run(
            run_id=run_id,
            customer_ref=customer_ref,
            amount=amount,
            advance_hours=25.0,
            restore_clock_offset=initial_offset_seconds,
        )
    )

    return {
        "run_id": run_id,
        "status": "running",
        "customer_ref": customer_ref,
        "amount": amount,
        "advance_hours": 25.0,
    }

