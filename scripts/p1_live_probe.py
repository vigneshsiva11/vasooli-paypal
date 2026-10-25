"""Part 1: Live PayPal Sandbox Probe.

Executes Orders A, B, and C against PayPal Sandbox.
Strict constraints:
- Must only run if PayPal environment is 'sandbox' (abort otherwise).
- At most 8 API calls.
- Zero secrets printed or written to disk.
- Never open, load, or print any .env file.
- Poll vasooli_demo.paypal_events read-only for up to 4 minutes.
- Record findings and export captured events as fixtures under tests/fixtures/paypal/live/.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.gateways.paypal.client import (
    SANDBOX_BASE,
    TOKEN_URL,
    PayPalCallFailed,
    describe_error,
    get_access_token,
    paypal_client,
)
from app.gateways.paypal.orders import (
    ORDERS_URL,
    SANDBOX_TEST_VISA,
    _extract_vault_id,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("p1_live_probe")

MAX_API_CALLS = 8
api_calls_made = 0


def track_call(description: str) -> None:
    global api_calls_made
    api_calls_made += 1
    logger.info("PayPal API Call #%d / %d: %s", api_calls_made, MAX_API_CALLS, description)
    if api_calls_made > MAX_API_CALLS:
        raise RuntimeError(f"ABORT: Exceeded maximum allowed API calls ({MAX_API_CALLS})")


async def run_live_orders(token: str) -> tuple[dict[str, Any], str | None]:
    """Execute Orders A, B, and C against PayPal Sandbox."""
    results: dict[str, Any] = {
        "order_a": {},
        "order_b": {},
        "order_c": {},
    }

    # 3. Order A: Card payment, normal test card, store_in_vault ON_SUCCESS, custom_id "cust:probe1", $5.00 USD
    logger.info("\n--- ORDER A: Normal Test Card with Vault-on-Success ---")
    req_id_a = f"probe-a-{uuid.uuid4().hex[:8]}"
    card_a = dict(SANDBOX_TEST_VISA)
    card_a_block = {
        "number": card_a["number"],
        "expiry": card_a["expiry"],
        "security_code": card_a["security_code"],
        "name": card_a.get("name", "Test Buyer"),
        "billing_address": card_a.get("billing_address", {}),
        "attributes": {
            "vault": {"store_in_vault": "ON_SUCCESS"},
        },
    }
    payload_a = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": f"{req_id_a}-ref",
                "description": "Probe Order A - Normal Card Vaulted",
                "custom_id": "cust:probe1",
                "amount": {
                    "currency_code": "USD",
                    "value": "5.00",
                },
            }
        ],
        "payment_source": {"card": card_a_block},
    }

    async with httpx.AsyncClient(base_url=SANDBOX_BASE, timeout=30.0) as client:
        track_call("POST /v2/checkout/orders (Order A create)")
        resp_a = await client.post(
            ORDERS_URL,
            json=payload_a,
            headers={"Authorization": f"Bearer {token}", "PayPal-Request-Id": req_id_a},
        )
        http_code_a = resp_a.status_code
        body_a = resp_a.json()
        order_id_a = body_a.get("id", "")
        order_status_a = body_a.get("status", "")
        logger.info("Order A create: HTTP %d, id=%s, status=%s", http_code_a, order_id_a, order_status_a)

        capture_status_a = None
        vault_id_a = None
        capture_id_a = None

        if order_status_a != "COMPLETED":
            track_call(f"POST /v2/checkout/orders/{order_id_a}/capture (Order A capture)")
            cap_resp_a = await client.post(
                f"{ORDERS_URL}/{order_id_a}/capture",
                json={},
                headers={"Authorization": f"Bearer {token}", "PayPal-Request-Id": f"{req_id_a}-cap"},
            )
            http_code_cap_a = cap_resp_a.status_code
            cap_body_a = cap_resp_a.json()
            order_status_a = cap_body_a.get("status", order_status_a)
            vault_id_a = _extract_vault_id(cap_body_a)
            for unit in cap_body_a.get("purchase_units", []):
                for cap in unit.get("payments", {}).get("captures", []):
                    capture_status_a = cap.get("status")
                    capture_id_a = cap.get("id")
            logger.info("Order A capture: HTTP %d, status=%s, capture_status=%s, vault_id=%s",
                        http_code_cap_a, order_status_a, capture_status_a, vault_id_a)
            results["order_a"] = {
                "http_status": http_code_cap_a,
                "order_id": order_id_a,
                "capture_id": capture_id_a,
                "order_status": order_status_a,
                "capture_status": capture_status_a,
                "vault_id": vault_id_a,
            }
        else:
            vault_id_a = _extract_vault_id(body_a)
            for unit in body_a.get("purchase_units", []):
                for cap in unit.get("payments", {}).get("captures", []):
                    capture_status_a = cap.get("status")
                    capture_id_a = cap.get("id")
            results["order_a"] = {
                "http_status": http_code_a,
                "order_id": order_id_a,
                "capture_id": capture_id_a,
                "order_status": order_status_a,
                "capture_status": capture_status_a,
                "vault_id": vault_id_a,
            }

    # 4. Order B: Card payment with cardholder name CCREJECT-IF, not vaulted, custom_id "cust:probe1", $5.00
    logger.info("\n--- ORDER B: Decline Trigger CCREJECT-IF (Not Vaulted) ---")
    req_id_b = f"probe-b-{uuid.uuid4().hex[:8]}"
    card_b = dict(SANDBOX_TEST_VISA)
    card_b["name"] = "CCREJECT-IF"
    card_b_block = {
        "number": card_b["number"],
        "expiry": card_b["expiry"],
        "security_code": card_b["security_code"],
        "name": "CCREJECT-IF",
        "billing_address": card_b.get("billing_address", {}),
    }
    payload_b = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": f"{req_id_b}-ref",
                "description": "Probe Order B - CCREJECT-IF Decline",
                "custom_id": "cust:probe1",
                "amount": {
                    "currency_code": "USD",
                    "value": "5.00",
                },
            }
        ],
        "payment_source": {"card": card_b_block},
    }

    async with httpx.AsyncClient(base_url=SANDBOX_BASE, timeout=30.0) as client:
        track_call("POST /v2/checkout/orders (Order B create)")
        resp_b = await client.post(
            ORDERS_URL,
            json=payload_b,
            headers={"Authorization": f"Bearer {token}", "PayPal-Request-Id": req_id_b},
        )
        http_code_b = resp_b.status_code
        body_b = resp_b.json()
        order_id_b = body_b.get("id", "")
        order_status_b = body_b.get("status", "")
        logger.info("Order B create: HTTP %d, id=%s, status=%s", http_code_b, order_id_b, order_status_b)

        if order_status_b != "COMPLETED" and order_id_b:
            track_call(f"POST /v2/checkout/orders/{order_id_b}/capture (Order B capture)")
            cap_resp_b = await client.post(
                f"{ORDERS_URL}/{order_id_b}/capture",
                json={},
                headers={"Authorization": f"Bearer {token}", "PayPal-Request-Id": f"{req_id_b}-cap"},
            )
            http_code_cap_b = cap_resp_b.status_code
            cap_body_b = cap_resp_b.json()
            order_status_b = cap_body_b.get("status", order_status_b)
            capture_status_b = None
            capture_id_b = None
            processor_response_b = None
            for unit in cap_body_b.get("purchase_units", []):
                for cap in unit.get("payments", {}).get("captures", []):
                    capture_status_b = cap.get("status")
                    capture_id_b = cap.get("id")
                    processor_response_b = cap.get("processor_response")
            logger.info("Order B capture: HTTP %d, order_status=%s, capture_status=%s, processor_response=%s",
                        http_code_cap_b, order_status_b, capture_status_b, processor_response_b)
            results["order_b"] = {
                "create_http_status": http_code_b,
                "capture_http_status": http_code_cap_b,
                "order_id": order_id_b,
                "capture_id": capture_id_b,
                "order_status": order_status_b,
                "capture_status": capture_status_b,
                "body_status": cap_body_b.get("status"),
                "processor_response": processor_response_b,
                "error_details": cap_body_b.get("details"),
                "raw_body": cap_body_b,
            }
        else:
            capture_status_b = None
            capture_id_b = None
            processor_response_b = None
            for unit in body_b.get("purchase_units", []):
                for cap in unit.get("payments", {}).get("captures", []):
                    capture_status_b = cap.get("status")
                    capture_id_b = cap.get("id")
                    processor_response_b = cap.get("processor_response")
            results["order_b"] = {
                "create_http_status": http_code_b,
                "order_id": order_id_b,
                "capture_id": capture_id_b,
                "order_status": order_status_b,
                "capture_status": capture_status_b,
                "body_status": body_b.get("status"),
                "processor_response": processor_response_b,
                "error_details": body_b.get("details"),
                "raw_body": body_b,
            }

    # 5. Order C: Charge vault id from Order A with custom_id "ev:probe_evt", $5.00 USD
    logger.info("\n--- ORDER C: Vault Charge with custom_id ev:probe_evt ---")
    if not vault_id_a:
        logger.warning("Order A did not return a vault id! Cannot run vaulted charge Order C.")
        results["order_c"] = {"error": "no_vault_id_from_order_a"}
    else:
        req_id_c = f"probe-c-{uuid.uuid4().hex[:8]}"
        payload_c = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": f"{req_id_c}-ref",
                    "description": "Probe Order C - Vaulted Charge",
                    "custom_id": "ev:probe_evt",
                    "amount": {
                        "currency_code": "USD",
                        "value": "5.00",
                    },
                }
            ],
            "payment_source": {
                "card": {
                    "vault_id": vault_id_a,
                }
            },
        }

        async with httpx.AsyncClient(base_url=SANDBOX_BASE, timeout=30.0) as client:
            track_call("POST /v2/checkout/orders (Order C vaulted create)")
            resp_c = await client.post(
                ORDERS_URL,
                json=payload_c,
                headers={"Authorization": f"Bearer {token}", "PayPal-Request-Id": req_id_c},
            )
            http_code_c = resp_c.status_code
            body_c = resp_c.json()
            order_id_c = body_c.get("id", "")
            order_status_c = body_c.get("status", "")
            logger.info("Order C create: HTTP %d, id=%s, status=%s", http_code_c, order_id_c, order_status_c)

            capture_status_c = None
            capture_id_c = None
            if order_status_c != "COMPLETED":
                track_call(f"POST /v2/checkout/orders/{order_id_c}/capture (Order C capture)")
                cap_resp_c = await client.post(
                    f"{ORDERS_URL}/{order_id_c}/capture",
                    json={},
                    headers={"Authorization": f"Bearer {token}", "PayPal-Request-Id": f"{req_id_c}-cap"},
                )
                http_code_cap_c = cap_resp_c.status_code
                cap_body_c = cap_resp_c.json()
                order_status_c = cap_body_c.get("status", order_status_c)
                for unit in cap_body_c.get("purchase_units", []):
                    for cap in unit.get("payments", {}).get("captures", []):
                        capture_status_c = cap.get("status")
                        capture_id_c = cap.get("id")
                logger.info("Order C capture: HTTP %d, status=%s, capture_status=%s",
                            http_code_cap_c, order_status_c, capture_status_c)
                results["order_c"] = {
                    "http_status": http_code_cap_c,
                    "order_id": order_id_c,
                    "capture_id": capture_id_c,
                    "order_status": order_status_c,
                    "capture_status": capture_status_c,
                }
            else:
                for unit in body_c.get("purchase_units", []):
                    for cap in unit.get("payments", {}).get("captures", []):
                        capture_status_c = cap.get("status")
                        capture_id_c = cap.get("id")
                results["order_c"] = {
                    "http_status": http_code_c,
                    "order_id": order_id_c,
                    "capture_id": capture_id_c,
                    "order_status": order_status_c,
                    "capture_status": capture_status_c,
                }

    return results, vault_id_a


async def main() -> int:
    settings = get_settings()

    # 1. Environment validation: MUST BE SANDBOX
    env_setting = os.getenv("PAYPAL_ENVIRONMENT", os.getenv("PAYPAL_ENV", settings.paypal_env))
    if str(env_setting).strip().lower() != "sandbox":
        logger.error("ABORT: PayPal environment setting is '%s', not 'sandbox'", env_setting)
        sys.exit(1)

    logger.info("PayPal environment verified as 'sandbox'. Safe to proceed.")

    # 2. Check live service health URL if configured
    health_url = os.getenv("LIVE_SERVICE_HEALTH_URL", os.getenv("SERVICE_HEALTH_URL", ""))
    if health_url:
        logger.info("Pinging live service health URL: %s", health_url)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(health_url)
                logger.info("Live service health returned HTTP %d", res.status_code)
        except Exception as exc:
            logger.warning("Could not reach live service health URL: %s", exc)
    else:
        logger.info("Live service health URL is not configured; skipping awake health check.")

    if "--export-only" in sys.argv:
        logger.info("Running in --export-only mode with recorded probe orders.")
        vault_id_a = "3w783067vb799012h"
        results = {
            "order_a": {
                "http_status": 201,
                "order_id": "8YF00147EB635281R",
                "capture_id": "28C544276S4714412",
                "order_status": "COMPLETED",
                "capture_status": "COMPLETED",
                "vault_id": vault_id_a,
            },
            "order_b": {
                "create_http_status": 201,
                "order_id": "2CG62440G8279605F",
                "capture_id": "5LG46948JJ571544V",
                "order_status": "COMPLETED",
                "capture_status": "DECLINED",
                "body_status": "COMPLETED",
                "processor_response": {
                    "avs_code": "G",
                    "cvv_code": "P",
                    "response_code": "5120",
                },
            },
            "order_c": {
                "http_status": 201,
                "order_id": "29935697HU833963D",
                "capture_id": "64P52986GA335712Y",
                "order_status": "COMPLETED",
                "capture_status": "COMPLETED",
            },
        }
    else:
        token = await get_access_token()
        track_call("POST /v1/oauth2/token (OAuth token fetch)")
        results, vault_id_a = await run_live_orders(token)

    logger.info("\nTotal PayPal API calls made: %d / %d", api_calls_made, MAX_API_CALLS)

    # 6. Poll live demo database READ-ONLY (vasooli_demo.paypal_events) for up to 4 minutes
    logger.info("\n--- POLLING LIVE DEMO DATABASE READ-ONLY (vasooli_demo) ---")
    mongo_uri = settings.mongodb_uri
    client_demo = AsyncIOMotorClient(mongo_uri)
    db_demo = client_demo["vasooli_demo"]

    expected_ids = {
        "order_a": results["order_a"].get("order_id"),
        "capture_a": results["order_a"].get("capture_id"),
        "order_b": results["order_b"].get("order_id"),
        "capture_b": results["order_b"].get("capture_id"),
        "order_c": results["order_c"].get("order_id"),
        "capture_c": results["order_c"].get("capture_id"),
        "vault_id_a": vault_id_a,
    }
    logger.info("Tracking identifiers: %s", expected_ids)

    poll_timeout_seconds = 240 if "--export-only" not in sys.argv else 15
    poll_interval = 2
    start_poll_time = time.time()
    found_events: dict[str, Any] = {}

    fixtures_out_dir = PROJECT_ROOT / "tests" / "fixtures" / "paypal" / "live"
    fixtures_out_dir.mkdir(parents=True, exist_ok=True)
    exported_fixtures: dict[str, dict[str, str]] = {}

    while time.time() - start_poll_time < poll_timeout_seconds:
        elapsed = int(time.time() - start_poll_time)
        cursor = db_demo.paypal_events.find({}).sort("_id", -1).limit(50)
        docs = await cursor.to_list(length=50)

        for d in docs:
            eid = d.get("paypal_event_id") or d.get("id")
            etype = d.get("event_type")
            payload = d.get("payload") if isinstance(d.get("payload"), dict) else d
            res = payload.get("resource") or d.get("resource") or {}

            matched_key = None
            res_id = res.get("id")
            custom_id = res.get("custom_id")
            supplementary = res.get("supplementary_data", {}).get("related_ids", {})
            order_id = supplementary.get("order_id") or res.get("metadata", {}).get("order_id")

            if etype == "VAULT.PAYMENT-TOKEN.CREATED" and "vault_payment_token_created" not in found_events:
                if res_id == expected_ids.get("vault_id_a") or order_id == expected_ids.get("order_a"):
                    matched_key = "vault_payment_token_created"
            elif etype == "PAYMENT.CAPTURE.COMPLETED":
                if res_id == expected_ids.get("capture_a") or (expected_ids.get("order_a") and order_id == expected_ids.get("order_a")):
                    matched_key = "payment_capture_completed_order_a"
                elif res_id == expected_ids.get("capture_c") or (expected_ids.get("order_c") and order_id == expected_ids.get("order_c")) or custom_id == "ev:probe_evt":
                    matched_key = "payment_capture_completed_order_c"
            elif etype == "PAYMENT.CAPTURE.DECLINED":
                if res_id == expected_ids.get("capture_b") or (expected_ids.get("order_b") and order_id == expected_ids.get("order_b")) or custom_id == "cust:probe1":
                    matched_key = "payment_capture_declined_order_b"

            if matched_key and matched_key not in found_events:
                found_events[matched_key] = d
                logger.info("Found webhook event [%s]: type=%s, id=%s", matched_key, etype, eid)

                filename = f"{matched_key}.json"
                file_path = fixtures_out_dir / filename
                export_dict = d.get("payload") if isinstance(d.get("payload"), dict) else {k: v for k, v in d.items() if k != "_id"}
                content_bytes = json.dumps(export_dict, indent=2, sort_keys=True, default=str).encode("utf-8")
                file_path.write_bytes(content_bytes)
                sha256 = hashlib.sha256(content_bytes).hexdigest()
                exported_fixtures[matched_key] = {
                    "file": str(file_path.relative_to(PROJECT_ROOT)),
                    "sha256": sha256,
                    "event_type": etype,
                    "paypal_event_id": eid,
                }
                logger.info("Exported fixture: %s (sha256: %s)", filename, sha256)

        if len(found_events) >= 4:
            logger.info("All 4 expected webhook events captured and exported!")
            break

        await asyncio.sleep(poll_interval)
        logger.info("Polling vasooli_demo.paypal_events... elapsed %ds, captured %d/4", elapsed, len(found_events))

    client_demo.close()

    # 7. Write findings and summary
    logger.info("\n--- SUMMARY OF RESULTS ---")
    logger.info("Order A: %s", results["order_a"])
    logger.info("Order B: %s", results["order_b"])
    logger.info("Order C: %s", results["order_c"])
    logger.info("Captured webhooks: %d found", len(found_events))
    logger.info("Exported fixtures: %s", exported_fixtures)

    probe_results_path = PROJECT_ROOT / "docs" / "spike" / "p1_probe_output.json"
    probe_results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(probe_results_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "api_calls_made": api_calls_made,
                "order_a": results["order_a"],
                "order_b": {k: v for k, v in results["order_b"].items() if k != "raw_body"},
                "order_b_raw_body": results["order_b"].get("raw_body"),
                "order_c": results["order_c"],
                "found_events_keys": list(found_events.keys()),
                "exported_fixtures": exported_fixtures,
            },
            f,
            indent=2,
            default=str,
        )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
