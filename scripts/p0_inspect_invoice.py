"""Inspects a PayPal invoice on sandbox without importing from app/."""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_env(path: Path) -> dict:
    values = {}
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
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        sys.exit("Usage: python scripts/p0_inspect_invoice.py <invoice_id>")

    invoice_id = sys.argv[1].strip()

    env = load_env(Path(".env"))
    client_id = env.get("PAYPAL_CLIENT_ID") or os.environ.get("PAYPAL_CLIENT_ID", "")
    secret = env.get("PAYPAL_CLIENT_SECRET") or os.environ.get("PAYPAL_CLIENT_SECRET", "")
    mode = (env.get("PAYPAL_ENV") or "sandbox").lower()

    if mode != "sandbox":
        sys.exit("FAIL: PAYPAL_ENV must be 'sandbox' for this project.")
    if not client_id or not secret:
        sys.exit("FAIL: PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET is empty in .env")

    # Step 1: Obtain OAuth2 token
    auth = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    token_req = urllib.request.Request(
        "https://api-m.sandbox.paypal.com/v1/oauth2/token",
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(token_req, timeout=20) as resp:
            token_data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"FAIL: OAuth HTTP {e.code}")
    except Exception as e:
        sys.exit(f"FAIL: could not reach PayPal OAuth: {e}")

    access_token = token_data.get("access_token")
    if not access_token:
        sys.exit("FAIL: No access_token returned")

    # Step 2: GET /v2/invoicing/invoices/{id}
    inv_req = urllib.request.Request(
        f"https://api-m.sandbox.paypal.com/v2/invoicing/invoices/{invoice_id}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(inv_req, timeout=20) as resp:
            inv = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"FAIL: Invoices API HTTP {e.code}")
    except Exception as e:
        sys.exit(f"FAIL: could not fetch invoice: {e}")

    # Extract required fields:
    # status, total, paid amount, due amount, and for each entry in payments.transactions: type, method, amount, date
    status = inv.get("status", "")

    amount_obj = inv.get("amount") or {}
    total_val = amount_obj.get("value", "") if isinstance(amount_obj, dict) else str(amount_obj)
    total_curr = amount_obj.get("currency_code", "") if isinstance(amount_obj, dict) else ""
    total = f"{total_val} {total_curr}".strip()

    payments_obj = inv.get("payments") or {}
    paid_obj = payments_obj.get("paid_amount") or {}
    paid_val = paid_obj.get("value", "0.00") if isinstance(paid_obj, dict) else str(paid_obj)
    paid_curr = paid_obj.get("currency_code", "") if isinstance(paid_obj, dict) else ""
    if not paid_curr:
        paid_curr = total_curr
    paid_amount = f"{paid_val} {paid_curr}".strip()

    due_obj = inv.get("due_amount") or {}
    due_val = due_obj.get("value", "0.00") if isinstance(due_obj, dict) else str(due_obj)
    due_curr = due_obj.get("currency_code", "") if isinstance(due_obj, dict) else ""
    if not due_curr:
        due_curr = total_curr
    due_amount = f"{due_val} {due_curr}".strip()

    print(f"status: {status}")
    print(f"total: {total}")
    print(f"paid amount: {paid_amount}")
    print(f"due amount: {due_amount}")

    transactions = payments_obj.get("transactions", [])
    for tx in transactions:
        tx_type = tx.get("type", "")
        tx_method = tx.get("method", "")
        amt = tx.get("amount") or {}
        amt_val = amt.get("value", "") if isinstance(amt, dict) else str(amt)
        amt_curr = amt.get("currency_code", "") if isinstance(amt, dict) else ""
        tx_amt = f"{amt_val} {amt_curr}".strip()
        tx_date = tx.get("payment_date_time") or tx.get("payment_date") or ""

        print(f"type: {tx_type}")
        print(f"method: {tx_method}")
        print(f"amount: {tx_amt}")
        print(f"date: {tx_date}")


if __name__ == "__main__":
    main()
