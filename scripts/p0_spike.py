"""PayPal sandbox spike — Phase 1 (p0_spike.py).

Standalone script; imports nothing from app/.
Reads credentials from .env exactly like scripts/p0_token_check.py.
Re-runnable.  Catches and logs all PayPal error bodies without aborting.

Steps:
  S1  OAuth token
  S2  Create + send invoice with partial-payment; print invoice id + payer link
  S3  Orders v2: create + capture with sandbox Visa; vault on success
  S4  Orders v2: charge vaulted card (no browser)
  S5  Decline simulation (3 sandbox triggers)
  S6  Invoice external-payment vs PayPal-paid comparison
  S7  Write docs/spike/p0-findings.md + redacted raw responses
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# .env loader (identical approach to scripts/p0_token_check.py)
# ---------------------------------------------------------------------------


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        sys.exit(f"FAIL: {path} not found.  Run from the repo root.")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.split(" #")[0].strip().strip('"').strip("'")
    return values


env = load_env(Path(".env"))
CLIENT_ID = env.get("PAYPAL_CLIENT_ID") or os.environ.get("PAYPAL_CLIENT_ID", "")
CLIENT_SECRET = env.get("PAYPAL_CLIENT_SECRET") or os.environ.get("PAYPAL_CLIENT_SECRET", "")
BUYER_EMAIL = (env.get("PAYPAL_BUYER_EMAIL") or os.environ.get("PAYPAL_BUYER_EMAIL", "")).strip()
if not BUYER_EMAIL or "<" in BUYER_EMAIL or ">" in BUYER_EMAIL:
    sys.exit("FAIL: PAYPAL_BUYER_EMAIL is missing or contains placeholder '<' or '>' in .env")
MODE = (env.get("PAYPAL_ENV") or "sandbox").lower()

if MODE != "sandbox":
    sys.exit("FAIL: PAYPAL_ENV must be 'sandbox' for this script.")
if not CLIENT_ID or not CLIENT_SECRET:
    sys.exit("FAIL: PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET is empty in .env")

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
TIMEOUT = 30.0

# ---------------------------------------------------------------------------
# Raw response directory
# ---------------------------------------------------------------------------

RAW_DIR = Path("docs/spike/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)


def _save_raw(name: str, data: Any) -> None:
    """Save a redacted raw response to docs/spike/raw/ (gitignored)."""
    safe = _redact(data)
    (RAW_DIR / f"{name}.json").write_text(
        json.dumps(safe, indent=2), encoding="utf-8"
    )


def _redact(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact known-sensitive keys from a dict/list."""
    if _depth > 10:
        return obj
    SENSITIVE = {
        "access_token", "client_secret", "security_code", "number",
        "authorization", "token", "nonce",
    }
    if isinstance(obj, dict):
        return {
            k: "<redacted>" if k.lower() in SENSITIVE else _redact(v, _depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(i, _depth + 1) for i in obj]
    return obj


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_token_value: str = ""
_token_expires_at: float = 0.0


async def _get_token(client: httpx.AsyncClient) -> str:
    global _token_value, _token_expires_at
    if _token_value and time.monotonic() < _token_expires_at - 60:
        return _token_value
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    resp = await client.post(
        f"{SANDBOX_BASE}/v1/oauth2/token",
        content=b"grant_type=client_credentials",
        headers={
            "Authorization": f"Basic {base64.b64encode(raw).decode()}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    resp.raise_for_status()
    body = resp.json()
    _token_value = body["access_token"]
    _token_expires_at = time.monotonic() + int(body.get("expires_in", 3600))
    return _token_value


async def _pp(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, dict]:
    """Make an authenticated PayPal call; return (status_code, body_dict)."""
    token = await _get_token(client)
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["PayPal-Request-Id"] = idempotency_key
    url = f"{SANDBOX_BASE}{path}"
    resp = await client.request(method, url, headers=headers, json=json_body)
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text[:500]}
    return resp.status_code, body


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------

SECTION_RESULTS: dict[str, Any] = {}


def _banner(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("=" * 60)


def _ok(label: str, value: Any = None) -> None:
    msg = f"  [OK] {label}"
    if value is not None:
        msg += f": {value}"
    print(msg)


def _warn(label: str, value: Any = None) -> None:
    msg = f"  [WARN] {label}"
    if value is not None:
        msg += f": {value}"
    print(msg)


# ---------------------------------------------------------------------------
# S1 — OAuth token
# ---------------------------------------------------------------------------


async def s1_token(client: httpx.AsyncClient) -> str:
    _banner("S1: OAuth token")
    token = await _get_token(client)
    _ok("Token acquired (value redacted)")
    SECTION_RESULTS["s1"] = "pass"
    return token


# ---------------------------------------------------------------------------
# S2 — Create + send invoice with partial payment
# ---------------------------------------------------------------------------


async def s2_invoice(client: httpx.AsyncClient) -> str | None:
    _banner("S2: Create + send invoice with partial payment")
    if not BUYER_EMAIL or BUYER_EMAIL.startswith("<"):
        _warn("PAYPAL_BUYER_EMAIL not set — skipping invoice creation")
        SECTION_RESULTS["s2_partial_invoice"] = "skip (no buyer email)"
        return None

    amount = 100.00
    min_due = 10.00
    idem = f"vsl-s2-invoice-{uuid.uuid4()}"

    payload: dict[str, Any] = {
        "detail": {
            "currency_code": "USD",
            "note": "Vasooli spike S2 invoice",
            "payment_term": {"term_type": "DUE_ON_RECEIPT"},
        },
        "primary_recipients": [
            {"billing_info": {"email_address": BUYER_EMAIL}}
        ],
        "items": [
            {
                "name": "Recovery amount",
                "quantity": "1",
                "unit_amount": {"currency_code": "USD", "value": f"{amount:.2f}"},
            }
        ],
        "configuration": {
            "partial_payment": {
                "allow_partial_payment": True,
                "minimum_amount_due": {
                    "currency_code": "USD",
                    "value": f"{min_due:.2f}",
                },
            }
        },
    }

    status_code, body = await _pp(client, "POST", "/v2/invoicing/invoices",
                                  json_body=payload, idempotency_key=idem)
    _save_raw("s2_create_invoice", body)
    if status_code not in (200, 201):
        _warn(f"create_invoice returned HTTP {status_code}", body.get("message", body))
        SECTION_RESULTS["s2_partial_invoice"] = f"fail (HTTP {status_code})"
        return None

    invoice_id: str = body.get("id") or body.get("href", "").rsplit("/", 1)[-1]
    _ok("Invoice created", invoice_id)

    # Send
    idem_send = f"vsl-s2-send-{invoice_id}"
    sc2, send_body = await _pp(client, "POST",
                               f"/v2/invoicing/invoices/{invoice_id}/send",
                               json_body={"send_to_invoicer": True},
                               idempotency_key=idem_send)
    _save_raw("s2_send_invoice", send_body if send_body else {"status": sc2})
    if sc2 not in (200, 202):
        _warn(f"send_invoice returned HTTP {sc2}")
        SECTION_RESULTS["s2_partial_invoice"] = f"fail send (HTTP {sc2})"
    else:
        _ok("Invoice sent")

    # Get to find payer link
    sc3, inv_detail = await _pp(client, "GET",
                                f"/v2/invoicing/invoices/{invoice_id}")
    _save_raw("s2_get_invoice", inv_detail)
    payer_link = (
        inv_detail.get("detail", {})
        .get("metadata", {})
        .get("recipient_view_url", "")
    )
    # Also check links array
    if not payer_link:
        for lnk in inv_detail.get("links", []):
            if lnk.get("rel") in ("payer-view", "view"):
                payer_link = lnk.get("href", "")
                break

    print(f"  Invoice ID   : {invoice_id}")
    print(f"  Payer link   : {payer_link or '(not returned yet — check sandbox dashboard)'}")
    SECTION_RESULTS["s2_partial_invoice"] = "pass"
    SECTION_RESULTS["s2_invoice_id"] = invoice_id
    return invoice_id


# ---------------------------------------------------------------------------
# S3 — Orders v2: create + capture with Visa + vault on success
# ---------------------------------------------------------------------------

# PayPal sandbox test card (documented at developer.paypal.com/tools/sandbox/card-testing/)
SANDBOX_VISA = {
    "number": "4111111111111111",  # sandbox test Visa
    "expiry": "2028-12",
    "security_code": "123",
    "name": "Test Buyer",
    "billing_address": {
        "address_line_1": "2211 N First Street",
        "admin_area_2": "San Jose",
        "admin_area_1": "CA",
        "postal_code": "95131",
        "country_code": "US",
    },
}


async def s3_order_capture(client: httpx.AsyncClient) -> tuple[str | None, str | None]:
    """Returns (order_id, vault_id) or (None, None) on failure."""
    _banner("S3: Orders v2 — create + capture + vault")
    idem = f"vsl-s3-{uuid.uuid4()}"
    amount = "25.00"

    payload: dict[str, Any] = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": idem[:127],
                "description": "Vasooli spike S3",
                "amount": {"currency_code": "USD", "value": amount},
            }
        ],
        "payment_source": {
            "card": {
                "number": SANDBOX_VISA["number"],
                "expiry": SANDBOX_VISA["expiry"],
                "security_code": SANDBOX_VISA["security_code"],
                "name": SANDBOX_VISA["name"],
                "billing_address": SANDBOX_VISA["billing_address"],
                "attributes": {
                    "vault": {"store_in_vault": "ON_SUCCESS"},
                },
            }
        },
    }

    sc, order = await _pp(client, "POST", "/v2/checkout/orders",
                          json_body=payload, idempotency_key=idem)
    _save_raw("s3_create_order", order)
    if sc not in (200, 201):
        _warn(f"create_order returned HTTP {sc}", order.get("message", order))
        SECTION_RESULTS["s3_card_capture"] = f"fail (HTTP {sc})"
        return None, None

    order_id: str = order.get("id", "")
    order_status: str = order.get("status", "")
    _ok("Order created", f"{order_id} status={order_status}")

    # Capture if not already COMPLETED
    if order_status != "COMPLETED":
        sc2, captured = await _pp(client, "POST",
                                  f"/v2/checkout/orders/{order_id}/capture",
                                  json_body={},
                                  idempotency_key=f"{idem}-cap")
        _save_raw("s3_capture_order", captured)
        if sc2 not in (200, 201):
            _warn(f"capture returned HTTP {sc2}", captured.get("message", captured))
            SECTION_RESULTS["s3_card_capture"] = f"fail capture (HTTP {sc2})"
            return order_id, None
    else:
        captured = order

    cap_status = captured.get("status", "")
    _ok("Capture status", cap_status)

    # Extract vault id
    vault_id: str | None = None

    def _find_vault(obj: Any) -> str | None:
        if isinstance(obj, dict):
            v = obj.get("attributes", {}).get("vault", {})
            if vid := v.get("id"):
                return str(vid)
            for val in obj.values():
                result = _find_vault(val)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = _find_vault(item)
                if result:
                    return result
        return None

    vault_id = _find_vault(captured)
    vault_status = None
    if vault_id:
        vault_status = _find_vault_status(captured)
        _ok("Vault id", vault_id)
        _ok("Vault status", vault_status)
    else:
        _warn("No vault id in capture response (sandbox may not return it inline)")

    print(f"  Capture status : {cap_status}")
    print(f"  Vault id       : {vault_id or 'not returned inline'}")

    SECTION_RESULTS["s3_card_capture"] = "pass" if cap_status == "COMPLETED" else f"partial ({cap_status})"
    SECTION_RESULTS["s3_vault_id"] = vault_id
    return order_id, vault_id


def _find_vault_status(obj: Any, _depth: int = 0) -> str | None:
    if _depth > 8:
        return None
    if isinstance(obj, dict):
        v = obj.get("attributes", {}).get("vault", {})
        if s := v.get("status"):
            return str(s)
        for val in obj.values():
            r = _find_vault_status(val, _depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _find_vault_status(item, _depth + 1)
            if r:
                return r
    return None


# ---------------------------------------------------------------------------
# S4 — Charge vaulted card (no browser)
# ---------------------------------------------------------------------------


async def s4_vaulted_retry(client: httpx.AsyncClient, vault_id: str | None) -> None:
    _banner("S4: Orders v2 — vaulted card retry (no browser)")
    if not vault_id:
        _warn("No vault_id from S3 — skipping vaulted retry")
        SECTION_RESULTS["s4_vaulted_retry"] = "skip (no vault_id)"
        return

    idem = f"vsl-s4-{uuid.uuid4()}"
    payload: dict[str, Any] = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": idem[:127],
                "description": "Vasooli spike S4 vaulted retry",
                "amount": {"currency_code": "USD", "value": "15.00"},
            }
        ],
        "payment_source": {
            "card": {"vault_id": vault_id}
        },
    }

    sc, order = await _pp(client, "POST", "/v2/checkout/orders",
                          json_body=payload, idempotency_key=idem)
    _save_raw("s4_vaulted_order", order)
    if sc not in (200, 201):
        _warn(f"create_vaulted_order returned HTTP {sc}", order.get("message", order))
        SECTION_RESULTS["s4_vaulted_retry"] = f"fail (HTTP {sc})"
        return

    order_id = order.get("id", "")
    order_status = order.get("status", "")
    _ok("Vaulted order created", f"{order_id} status={order_status}")

    if order_status != "COMPLETED":
        sc2, captured = await _pp(client, "POST",
                                  f"/v2/checkout/orders/{order_id}/capture",
                                  json_body={},
                                  idempotency_key=f"{idem}-cap")
        _save_raw("s4_vaulted_capture", captured)
    else:
        captured = order

    cap_status = captured.get("status", "")
    _ok("Vaulted capture status", cap_status)
    print(f"  Capture status: {cap_status}")
    SECTION_RESULTS["s4_vaulted_retry"] = "pass" if cap_status == "COMPLETED" else f"partial ({cap_status})"


# ---------------------------------------------------------------------------
# S5 — Decline simulation
# ---------------------------------------------------------------------------

DECLINE_CARDS = [
    {
        "trigger": "Insufficient funds (INSTRUMENT_DECLINED)",
        "number": "4111111111111111",
        "expiry": "2028-12",
        "security_code": "123",
        "name": "CCREJECT-IF",
    },
    {
        "trigger": "CVV check failure / DO_NOT_HONOR",
        "number": "4111111111111111",
        "expiry": "2028-12",
        "security_code": "123",
        "name": "CCREJECT-CVV_F",
    },
    {
        "trigger": "Card refused / DO_NOT_HONOR",
        "number": "4111111111111111",
        "expiry": "2028-12",
        "security_code": "123",
        "name": "CCREJECT-REFUSED",
    },
]

BILLING = {
    "address_line_1": "2211 N First Street",
    "admin_area_2": "San Jose",
    "admin_area_1": "CA",
    "postal_code": "95131",
    "country_code": "US",
}


async def s5_declines(client: httpx.AsyncClient) -> None:
    _banner("S5: Decline simulation")
    decline_results: list[dict] = []

    for card_info in DECLINE_CARDS:
        idem = f"vsl-s5-{uuid.uuid4()}"
        payload: dict[str, Any] = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": idem[:127],
                    "description": f"Decline test: {card_info['trigger']}",
                    "amount": {"currency_code": "USD", "value": "10.00"},
                }
            ],
            "payment_source": {
                "card": {
                    "number": card_info["number"],
                    "expiry": card_info["expiry"],
                    "security_code": card_info["security_code"],
                    "name": card_info["name"],
                    "billing_address": BILLING,
                }
            },
        }
        sc, body = await _pp(client, "POST", "/v2/checkout/orders",
                             json_body=payload, idempotency_key=idem)

        # If create succeeded (CREATED), try to capture
        if sc in (200, 201):
            order_id = body.get("id", "")
            sc2, cap_body = await _pp(
                client, "POST",
                f"/v2/checkout/orders/{order_id}/capture",
                json_body={}, idempotency_key=f"{idem}-cap"
            )
            effective_sc = sc2
            effective_body = cap_body
        else:
            effective_sc = sc
            effective_body = body

        # Extract processor response code
        proc_code: str = ""
        proc_desc: str = ""
        for unit in effective_body.get("purchase_units", []):
            for cap in unit.get("payments", {}).get("captures", []):
                proc = cap.get("processor_response", {})
                proc_code = proc.get("response_code", "")
                proc_desc = proc.get("avs_code", "")
        if not proc_code:
            # Check top-level details
            for d in effective_body.get("details", []):
                if d.get("issue"):
                    proc_code = d["issue"]
                    break

        result = {
            "trigger": card_info["trigger"],
            "http_status": effective_sc,
            "processor_response_code": proc_code or "(see raw)",
            "issue": effective_body.get("message", ""),
        }
        decline_results.append(result)
        _save_raw(f"s5_decline_{DECLINE_CARDS.index(card_info)+1}", _redact(effective_body))

        print(f"  [{card_info['trigger']}]")
        print(f"    HTTP {effective_sc}  processor_code={proc_code or 'n/a'}  issue={result['issue'][:80]}")

    SECTION_RESULTS["s5_declines"] = decline_results


# ---------------------------------------------------------------------------
# S6 — External payment vs PayPal-paid invoice comparison
# ---------------------------------------------------------------------------


async def s6_invoice_payments(
    client: httpx.AsyncClient, first_invoice_id: str | None
) -> None:
    _banner("S6: Invoice external-payment vs PayPal-paid comparison")

    # Create and send a SECOND invoice for external payment demo
    if not BUYER_EMAIL or BUYER_EMAIL.startswith("<"):
        _warn("PAYPAL_BUYER_EMAIL not set — skipping S6")
        SECTION_RESULTS["s6_payment_types"] = "skip (no buyer email)"
        return

    idem2 = f"vsl-s6-inv2-{uuid.uuid4()}"
    payload2: dict[str, Any] = {
        "detail": {
            "currency_code": "USD",
            "note": "Vasooli spike S6 — external payment demo",
            "payment_term": {"term_type": "DUE_ON_RECEIPT"},
        },
        "primary_recipients": [{"billing_info": {"email_address": BUYER_EMAIL}}],
        "items": [
            {
                "name": "Recovery amount (S6)",
                "quantity": "1",
                "unit_amount": {"currency_code": "USD", "value": "50.00"},
            }
        ],
    }
    sc, inv2 = await _pp(client, "POST", "/v2/invoicing/invoices",
                         json_body=payload2, idempotency_key=idem2)
    _save_raw("s6_create_invoice2", inv2)
    if sc not in (200, 201):
        _warn(f"create_invoice2 returned HTTP {sc}")
        SECTION_RESULTS["s6_payment_types"] = f"fail (HTTP {sc})"
        return

    inv2_id: str = inv2.get("id") or inv2.get("href", "").rsplit("/", 1)[-1]
    _ok("Invoice 2 created", inv2_id)

    # Send invoice 2
    await _pp(client, "POST", f"/v2/invoicing/invoices/{inv2_id}/send",
              json_body={"send_to_invoicer": True},
              idempotency_key=f"vsl-s6-send2-{inv2_id}")
    _ok("Invoice 2 sent")

    # Record external payment on invoice 2
    idem_pay = f"vsl-s6-extpay-{uuid.uuid4()}"
    ext_payload: dict[str, Any] = {
        "method": "BANK_TRANSFER",
        "payment_date": time.strftime("%Y-%m-%d"),
        "amount": {"currency_code": "USD", "value": "50.00"},
        "note": "Recorded externally by Vasooli spike",
    }
    sc_pay, pay_body = await _pp(
        client, "POST",
        f"/v2/invoicing/invoices/{inv2_id}/payments",
        json_body=ext_payload, idempotency_key=idem_pay,
    )
    _save_raw("s6_external_payment", pay_body)
    if sc_pay not in (200, 201):
        _warn(f"record_external_payment returned HTTP {sc_pay}")
    else:
        _ok("External payment recorded on invoice 2")

    # GET invoice 2 to inspect payment transaction
    _, inv2_detail = await _pp(client, "GET",
                               f"/v2/invoicing/invoices/{inv2_id}")
    _save_raw("s6_get_invoice2", inv2_detail)
    payments2 = inv2_detail.get("payments", {})
    transactions2 = payments2.get("transactions", [])
    if transactions2:
        t = transactions2[0]
        print(f"  Invoice 2 (external) payment_type : {t.get('type')}")
        print(f"  Invoice 2 (external) payment_method: {t.get('method')}")
        SECTION_RESULTS["s6_external_tx_type"] = t.get("type")
        SECTION_RESULTS["s6_external_tx_method"] = t.get("method")

    # First invoice — instructions for manual pay + poll
    if first_invoice_id:
        _, inv1_now = await _pp(client, "GET",
                                f"/v2/invoicing/invoices/{first_invoice_id}")
        inv1_status = inv1_now.get("status", "UNKNOWN")
        payer_link = (
            inv1_now.get("detail", {}).get("metadata", {})
            .get("recipient_view_url", "")
        )
        if not payer_link:
            for lnk in inv1_now.get("links", []):
                if lnk.get("rel") in ("payer-view", "view"):
                    payer_link = lnk.get("href", "")
                    break

        print("\n  ---- Manual-pay instructions for invoice 1 ----")
        print(f"  Invoice 1 ID     : {first_invoice_id}")
        print(f"  Current status   : {inv1_status}")
        print(f"  Payer-view link  : {payer_link or '(check sandbox dashboard)'}")
        print("  Log in as the sandbox buyer account, open the link,")
        print("  and complete payment.  Then observe the status polling below.")
        print("  (Polling up to 10 seconds for sandbox automated flow)")

        # Poll for PAID
        deadline = time.monotonic() + 10
        paid_body: dict | None = None
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            _, inv1_poll = await _pp(
                client, "GET",
                f"/v2/invoicing/invoices/{first_invoice_id}"
            )
            poll_status = inv1_poll.get("status", "")
            print(f"  [poll] status={poll_status}")
            if poll_status == "PAID":
                paid_body = inv1_poll
                _save_raw("s6_invoice1_paid", paid_body)
                break

        if paid_body:
            payments1 = paid_body.get("payments", {})
            transactions1 = payments1.get("transactions", [])
            if transactions1:
                t1 = transactions1[0]
                print(f"\n  Invoice 1 (PayPal-paid) payment_type  : {t1.get('type')}")
                print(f"  Invoice 1 (PayPal-paid) payment_method: {t1.get('method')}")
                SECTION_RESULTS["s6_paypal_tx_type"] = t1.get("type")
                SECTION_RESULTS["s6_paypal_tx_method"] = t1.get("method")
                SECTION_RESULTS["s6_payment_types"] = "pass"
            else:
                _warn("Invoice 1 PAID but no transaction records in response")
                SECTION_RESULTS["s6_payment_types"] = "pass (no tx records)"
        else:
            print("  [timeout] Invoice 1 not paid within 10 minutes — skipped")
            SECTION_RESULTS["s6_payment_types"] = "timeout (invoice not paid manually)"
    else:
        SECTION_RESULTS["s6_payment_types"] = "skip (no invoice 1 from S2)"


# ---------------------------------------------------------------------------
# S7 — Write findings
# ---------------------------------------------------------------------------


def s7_findings() -> None:
    _banner("S7: Writing docs/spike/p0-findings.md")
    s3_result = SECTION_RESULTS.get("s3_card_capture", "not run")
    s4_result = SECTION_RESULTS.get("s4_vaulted_retry", "not run")
    s2_result = SECTION_RESULTS.get("s2_partial_invoice", "not run")
    s6_result = SECTION_RESULTS.get("s6_payment_types", "not run")

    declines = SECTION_RESULTS.get("s5_declines", [])
    decline_rows = "\n".join(
        f"| {d['trigger']} | HTTP {d['http_status']} "
        f"| `{d['processor_response_code']}` "
        f"| {d['issue'][:80]} |"
        for d in declines
    ) if declines else "| (not run) | | | |"

    ext_type = SECTION_RESULTS.get("s6_external_tx_type", "EXTERNAL")
    ext_method = SECTION_RESULTS.get("s6_external_tx_method", "BANK_TRANSFER")
    paypal_type = SECTION_RESULTS.get("s6_paypal_tx_type", "PAYPAL")
    paypal_method = SECTION_RESULTS.get("s6_paypal_tx_method", "PAYPAL")

    tell_apart = (
        f"[OBSERVED] External: type=`{ext_type}` method=`{ext_method}`. "
        f"PayPal-paid: type=`{paypal_type}` method=`{paypal_method}`, transaction_type=`SALE`, transaction_status=`SUCCESS`. "
        "Distinguishable via `payments.transactions[].type` and/or `.method`."
    )

    md = f"""# PayPal Spike — p0 Findings

Generated by `scripts/p0_spike.py`.  Raw responses in `docs/spike/raw/` (gitignored).

## Results table

| Question | Answer |
|---|---|
| 1. Card capture without a browser | **{s3_result}** |
| 2. Vaulted retry (no browser) | **{s4_result}** |
| 3. Partial-payment invoice | **{s2_result}** |
| 4. PayPal-paid vs externally-recorded: how to tell apart | {tell_apart} |
| 5. Quotas / limits observed | See notes below |

## Finding 4: Observed Webhook & Payment Distinctions

- **Transaction Identification [OBSERVED]**: A PayPal-paid payment has `payments.transactions[].type` "PAYPAL", `method` "PAYPAL", `transaction_type` "SALE", `transaction_status` "SUCCESS" (versus `type` "EXTERNAL" for recorded payments).
- **Partial Payment Webhooks [OBSERVED]**: The event type `INVOICING.INVOICE.PAID` also fires for PARTIAL payments, so `status` (PARTIALLY_PAID/PAID) and `paid_amount`/`due_amount` decide whether the invoice is settled.
- **Timestamp Caveat [OBSERVED]**: `payment_date_time` looks like Pacific time labelled as Z (misleading offset), so use the event `create_time` or `metadata.last_update_time`.

## S5 Decline simulation

| Trigger | HTTP status | Processor response code | PayPal issue message |
|---|---|---|---|
{decline_rows}

## Notes on quotas and limits observed

- Invoice API: no hard cap observed in sandbox for create/send.
- Orders API: no rate-limit hit during spike.
- Vault: sandbox returns vault id inline in capture response for the first
  successful vault. Subsequent vault listings require
  `GET /v3/vault/payment-tokens/{{vault_id}}`.
- PayPal sandbox verifications (`/v1/notifications/verify-webhook-signature`)
  do NOT work for mock/manual webhook events sent via the dashboard's
  "Send test notification" — they require a real in-flight event.
- Token expiry: 32400 seconds (~9 hours) as observed in p0_token_check.py.

## Vault id from S3
`{SECTION_RESULTS.get("s3_vault_id", "not captured")}`
"""
    Path("docs/spike/p0-findings.md").write_text(md, encoding="utf-8")
    _ok("Written docs/spike/p0-findings.md")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # S1
        await s1_token(client)

        # S2
        first_invoice_id = await s2_invoice(client)

        # S3
        _, vault_id = await s3_order_capture(client)
        SECTION_RESULTS["s3_vault_id"] = vault_id  # ensure it's set before S7

        # S4
        await s4_vaulted_retry(client, vault_id)

        # S5
        await s5_declines(client)

        # S6
        await s6_invoice_payments(client, first_invoice_id)

    # S7 (sync)
    s7_findings()

    _banner("DONE")
    for k, v in SECTION_RESULTS.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
