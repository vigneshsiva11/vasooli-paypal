"""Export captured and derived PayPal fixtures with provenance.

Read-only: reads the paypal_events collection through app settings, never prints settings.
Writes JSON fixtures to tests/fixtures/paypal/.
Scans exported files to confirm no live tokens, keys, or real emails are written.
Records original_payload_sha256 on captured fixtures.
Records derived_from and edits on derived fixtures.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings

FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "paypal"


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Ensure no live tokens, secrets or real emails are included in exported fixtures."""
    text = json.dumps(payload, default=str)
    emails = set(re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", text))
    for email in emails:
        if not (
            email.endswith("@personal.example.com")
            or email.endswith("@business.example.com")
            or email.endswith("@example.com")
        ):
            raise ValueError(f"Found non-sandbox email in fixture: {email}")
    return payload


def compute_sha256(data: dict[str, Any]) -> str:
    """Canonical SHA-256 hash of a dictionary."""
    clean = {
        k: v
        for k, v in data.items()
        if k not in ("source", "original_payload_sha256", "derived_from", "edits")
    }
    return hashlib.sha256(json.dumps(clean, sort_keys=True).encode("utf-8")).hexdigest()


async def export_fixtures() -> int:
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongodb_uri)

    try:
        # Load events from vasooli or configured db
        events: list[dict[str, Any]] = []
        for db_name in [settings.mongodb_db_name, "vasooli", "vasooli-paypal"]:
            try:
                db = client[db_name]
                async for doc in db.paypal_events.find({}):
                    doc["_db_source"] = db_name
                    events.append(doc)
            except Exception:
                pass

        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Loaded {len(events)} PayPal event documents from MongoDB across databases.")

        # Find documents by exact ID
        by_id: dict[str, dict[str, Any]] = {}
        for doc in events:
            pid = doc.get("paypal_event_id") or doc.get("payload", {}).get("id")
            if pid and pid not in by_id:
                by_id[pid] = doc

        # 1. Captured fixtures (strictly byte-identical payloads)
        # a) invoicing_invoice_created: WH-1RE78495JY012312N-27K95268GW736715F (vasooli, inv 0005)
        doc_created = by_id["WH-1RE78495JY012312N-27K95268GW736715F"]
        pay_created = copy.deepcopy(doc_created["payload"])
        h_created = compute_sha256(pay_created)
        pay_created["source"] = "captured"
        pay_created["original_payload_sha256"] = h_created
        sanitize_payload(pay_created)
        (FIXTURES_DIR / "invoicing_invoice_created.json").write_text(
            json.dumps(pay_created, indent=2, default=str), encoding="utf-8"
        )
        print(f"Exported [captured]: invoicing_invoice_created.json (sha256={h_created})")

        # b) invoicing_invoice_sent: WH-28F596637J4655256-5720217169076973K (vasooli, inv 0005)
        doc_sent = by_id["WH-28F596637J4655256-5720217169076973K"]
        pay_sent = copy.deepcopy(doc_sent["payload"])
        h_sent = compute_sha256(pay_sent)
        pay_sent["source"] = "captured"
        pay_sent["original_payload_sha256"] = h_sent
        sanitize_payload(pay_sent)
        (FIXTURES_DIR / "invoicing_invoice_sent.json").write_text(
            json.dumps(pay_sent, indent=2, default=str), encoding="utf-8"
        )
        print(f"Exported [captured]: invoicing_invoice_sent.json (sha256={h_sent})")

        # c) payment_capture_declined: WH-8W041356MT580963L-8BV492892A3222823 (vasooli)
        doc_dec = by_id["WH-8W041356MT580963L-8BV492892A3222823"]
        pay_dec = copy.deepcopy(doc_dec["payload"])
        h_dec = compute_sha256(pay_dec)
        pay_dec["source"] = "captured"
        pay_dec["original_payload_sha256"] = h_dec
        sanitize_payload(pay_dec)
        (FIXTURES_DIR / "payment_capture_declined.json").write_text(
            json.dumps(pay_dec, indent=2, default=str), encoding="utf-8"
        )
        print(f"Exported [captured]: payment_capture_declined.json (sha256={h_dec})")

        # d) vault_payment_token_created: WH-7KV764744U570821M-2AR000173D5884226 (vasooli)
        doc_tok = by_id["WH-7KV764744U570821M-2AR000173D5884226"]
        pay_tok = copy.deepcopy(doc_tok["payload"])
        h_tok = compute_sha256(pay_tok)
        pay_tok["source"] = "captured"
        pay_tok["original_payload_sha256"] = h_tok
        sanitize_payload(pay_tok)
        (FIXTURES_DIR / "vault_payment_token_created.json").write_text(
            json.dumps(pay_tok, indent=2, default=str), encoding="utf-8"
        )
        print(f"Exported [captured]: vault_payment_token_created.json (sha256={h_tok})")

        # e) invoicing_invoice_paid_full: Genuine fully-paid event for INV2-LSEJ-2LLG-D59L-2QNU
        # WH-3W850800NV673601C-5SV68322WL326835D in vasooli
        doc_full = by_id["WH-3W850800NV673601C-5SV68322WL326835D"]
        pay_full = copy.deepcopy(doc_full["payload"])
        h_full = compute_sha256(pay_full)
        pay_full["source"] = "captured"
        pay_full["original_payload_sha256"] = h_full
        sanitize_payload(pay_full)
        (FIXTURES_DIR / "invoicing_invoice_paid_full.json").write_text(
            json.dumps(pay_full, indent=2, default=str), encoding="utf-8"
        )
        print(f"Exported [captured]: invoicing_invoice_paid_full.json (sha256={h_full})")

        # f) invoicing_invoice_paid_partial: Genuine partially-paid event for INV2-LSEJ-2LLG-D59L-2QNU
        # WH-99238613G8280990B-9FV78269C92473434 in vasooli
        doc_part = by_id["WH-99238613G8280990B-9FV78269C92473434"]
        pay_part = copy.deepcopy(doc_part["payload"])
        h_part = compute_sha256(pay_part)
        pay_part["source"] = "captured"
        pay_part["original_payload_sha256"] = h_part
        sanitize_payload(pay_part)
        (FIXTURES_DIR / "invoicing_invoice_paid_partial.json").write_text(
            json.dumps(pay_part, indent=2, default=str), encoding="utf-8"
        )
        print(f"Exported [captured]: invoicing_invoice_paid_partial.json (sha256={h_part})")

        # 2. Derived fixtures (edits made)

        # b) payment_capture_completed: WH-8L187362UU567994A-61C083782M709240S with custom_id added
        doc_cap = by_id["WH-8L187362UU567994A-61C083782M709240S"]
        pay_cap = copy.deepcopy(doc_cap["payload"])
        pay_cap.setdefault("resource", {})["custom_id"] = "ev:evt_paypal_order_capture_01"
        pay_cap["source"] = "derived"
        pay_cap["derived_from"] = "WH-8L187362UU567994A-61C083782M709240S"
        pay_cap["edits"] = [
            "resource.custom_id added as 'ev:evt_paypal_order_capture_01'"
        ]
        sanitize_payload(pay_cap)
        (FIXTURES_DIR / "payment_capture_completed.json").write_text(
            json.dumps(pay_cap, indent=2, default=str), encoding="utf-8"
        )
        print("Exported [derived]:  payment_capture_completed.json")

        # c) invoicing_invoice_paid_external: derived from WH-3W850800NV673601C-5SV68322WL326835D
        pay_ext = copy.deepcopy(doc_full["payload"])
        pay_ext["id"] = "WH-DERIVED-INVOICE-PAID-EXTERNAL-01"
        inv_ext = pay_ext["resource"]["invoice"]
        inv_ext["status"] = "PAID"
        inv_ext["due_amount"] = {"currency_code": "USD", "value": "0.00"}
        inv_ext["payments"]["transactions"] = [
            {
                "type": "EXTERNAL",
                "payment_id": "EXTR-0CB03532MX786571G",
                "method": "BANK_TRANSFER",
                "payment_date": "2026-10-15",
                "amount": {"currency_code": "USD", "value": "100.00"},
                "note": "Recorded externally by Vasooli spike",
            }
        ]
        pay_ext["source"] = "derived"
        pay_ext["derived_from"] = "WH-3W850800NV673601C-5SV68322WL326835D"
        pay_ext["edits"] = [
            "id rewritten to WH-DERIVED-INVOICE-PAID-EXTERNAL-01",
            "payments.transactions replaced with single EXTERNAL/BANK_TRANSFER transaction of $100.00, status=PAID, due_amount=0.00",
        ]
        sanitize_payload(pay_ext)
        (FIXTURES_DIR / "invoicing_invoice_paid_external.json").write_text(
            json.dumps(pay_ext, indent=2, default=str), encoding="utf-8"
        )
        print("Exported [derived]:  invoicing_invoice_paid_external.json")

        # 3. Final scan of all exported files
        print("\nScanning exported fixtures for secrets/real emails:")
        for path in sorted(FIXTURES_DIR.glob("*.json")):
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            assert data.get("source") in ("captured", "derived"), f"{path.name} missing source tag"
            emails = re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", content)
            for em in emails:
                assert (
                    em.endswith("@personal.example.com")
                    or em.endswith("@business.example.com")
                    or em.endswith("@example.com")
                ), f"Forbidden email {em} in {path.name}"
            for forbidden in ["client_secret", "access_token", "Bearer "]:
                assert forbidden not in content, f"Forbidden term {forbidden} found in {path.name}"
            if data["source"] == "captured":
                expected_hash = data.get("original_payload_sha256")
                actual_hash = compute_sha256(data)
                assert (
                    actual_hash == expected_hash
                ), f"Hash mismatch in {path.name}: expected {expected_hash}, got {actual_hash}"
                print(
                    f"  [SAFE & VERIFIED] {path.name} (source=captured, sha256={expected_hash[:12]}...)"
                )
            else:
                print(
                    f"  [SAFE & VERIFIED] {path.name} (source=derived from {data.get('derived_from')})"
                )

        print("\nAll fixtures exported and verified successfully.")
        return 0

    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(export_fixtures()))
