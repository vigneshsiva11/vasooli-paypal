"""Creates and sends a standalone PayPal invoice and verifies its amount.

Standalone script: reads .env directly, imports nothing from app/.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        sys.exit(f"FAIL: {path} not found. Run this from the repo root.")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.split(" #")[0].strip().strip('"').strip("'")
    return values


def main() -> None:
    env = load_env(Path(".env"))
    client_id = env.get("PAYPAL_CLIENT_ID") or os.environ.get("PAYPAL_CLIENT_ID", "")
    secret = env.get("PAYPAL_CLIENT_SECRET") or os.environ.get("PAYPAL_CLIENT_SECRET", "")
    buyer_email = (env.get("PAYPAL_BUYER_EMAIL") or os.environ.get("PAYPAL_BUYER_EMAIL", "")).strip()
    mode = (env.get("PAYPAL_ENV") or "sandbox").lower()

    if mode != "sandbox":
        sys.exit("FAIL: PAYPAL_ENV must be 'sandbox' for this script.")
    if not client_id or not secret:
        sys.exit("FAIL: PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET is empty in .env")
    if not buyer_email or "<" in buyer_email or ">" in buyer_email:
        sys.exit("FAIL: PAYPAL_BUYER_EMAIL is missing or contains placeholder '<' or '>' in .env")

    base_url = "https://api-m.sandbox.paypal.com"

    # Step 1: Obtain OAuth2 token
    auth_bytes = f"{client_id}:{secret}".encode("utf-8")
    auth_header = base64.b64encode(auth_bytes).decode("ascii")
    token_req = urllib.request.Request(
        f"{base_url}/v1/oauth2/token",
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8"),
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_req, timeout=30) as resp:
            token_body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        sys.exit(f"FAIL: OAuth token request failed (HTTP {e.code}): {err}")
    except Exception as e:
        sys.exit(f"FAIL: OAuth network error: {e}")

    access_token = token_body.get("access_token")
    if not access_token:
        sys.exit("FAIL: No access_token returned by PayPal")

    # Step 2: Create draft invoice
    create_payload = {
        "detail": {
            "currency_code": "USD",
            "note": "Vasooli recovery invoice",
            "payment_term": {"term_type": "DUE_ON_RECEIPT"},
        },
        "primary_recipients": [
            {
                "billing_info": {
                    "email_address": buyer_email,
                }
            }
        ],
        "items": [
            {
                "name": "Recovery amount",
                "quantity": "1",
                "unit_amount": {
                    "currency_code": "USD",
                    "value": "100.00",
                },
            }
        ],
        "configuration": {
            "partial_payment": {
                "allow_partial_payment": True,
                "minimum_amount_due": {
                    "currency_code": "USD",
                    "value": "10.00",
                },
            }
        },
    }

    create_req = urllib.request.Request(
        f"{base_url}/v2/invoicing/invoices",
        data=json.dumps(create_payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": str(uuid.uuid4()),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(create_req, timeout=30) as resp:
            create_body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        sys.exit(f"FAIL: Create invoice failed (HTTP {e.code}): {err}")

    invoice_id = create_body.get("id") or create_body.get("href", "").rsplit("/", 1)[-1]
    if not invoice_id:
        sys.exit(f"FAIL: Could not determine invoice id from response: {create_body}")

    # Step 3: Send invoice
    send_req = urllib.request.Request(
        f"{base_url}/v2/invoicing/invoices/{invoice_id}/send",
        data=json.dumps({"send_to_invoicer": True}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": str(uuid.uuid4()),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(send_req, timeout=30) as resp:
            pass
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        sys.exit(f"FAIL: Send invoice failed (HTTP {e.code}): {err}")

    # Step 4: GET invoice
    get_req = urllib.request.Request(
        f"{base_url}/v2/invoicing/invoices/{invoice_id}",
        headers={
            "Authorization": f"Bearer {access_token}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(get_req, timeout=30) as resp:
            inv = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        sys.exit(f"FAIL: GET invoice failed (HTTP {e.code}): {err}")

    status = inv.get("status", "")
    amount_val = inv.get("amount", {}).get("value", "")
    due_amount_val = inv.get("due_amount", {}).get("value", "")

    payer_link = (
        inv.get("detail", {})
        .get("metadata", {})
        .get("recipient_view_url", "")
    )
    if not payer_link:
        for lnk in inv.get("links", []):
            if lnk.get("rel") in ("payer-view", "view"):
                payer_link = lnk.get("href", "")
                break

    print(f"Invoice ID       : {invoice_id}")
    print(f"Status           : {status}")
    print(f"Amount Value     : {amount_val}")
    print(f"Due Amount Value : {due_amount_val}")
    print(f"Payer Link       : {payer_link or '(none)'}")

    # Step 5: Validate amount.value == 100.00
    try:
        numeric_amount = float(amount_val)
    except (TypeError, ValueError):
        numeric_amount = None

    if numeric_amount != 100.00:
        print(f"FAIL: amount.value expected 100.00, got {amount_val}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
