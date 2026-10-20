"""PayPal Invoicing v2 — create, send, record-payment, poll.

Covers spike steps S2 and S6:
  - create_invoice: draft invoice with partial-payment support
  - send_invoice: move draft → SENT, generating the payer-view link
  - record_external_payment: POST /v2/invoicing/invoices/{id}/payments
  - get_invoice: GET full invoice object (payment transactions included)

PayPal-Request-Id header is sent on every create/mutating call for
idempotency (rule 3).  Its value is derived from the caller-supplied
idempotency_key so it is deterministic on retries.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.gateways.paypal.client import PayPalCallFailed, describe_error, paypal_client

logger = logging.getLogger(__name__)

INVOICES_BASE = "/v2/invoicing/invoices"


async def create_invoice(
    *,
    buyer_email: str,
    amount: float,
    currency: str = "USD",
    note: str = "Vasooli recovery invoice",
    reference: str | None = None,
    allow_partial_payment: bool = True,
    minimum_amount_due: float | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Draft an invoice and return the full PayPal response body.

    Args:
        buyer_email: PayPal account email of the payer.
        amount: Total amount due, in major units (dollars).
        currency: ISO 4217 code.
        note: Shown on the invoice.
        reference: Merchant reference for the invoice (e.g. vsl_<policy_verdict_id>).
        allow_partial_payment: Enables partial_payment on the invoice.
        minimum_amount_due: Only used when allow_partial_payment is True.
        idempotency_key: Deterministic key for PayPal-Request-Id.  A UUID4
            is generated if not supplied.

    Returns:
        The raw PayPal invoice object (draft state, no payer link yet).

    Raises:
        PayPalCallFailed: on any HTTP error or unexpected response shape.
        ValueError: if payload contains amount.breakdown.custom.
    """
    req_id = idempotency_key or str(uuid.uuid4())

    detail: dict[str, Any] = {
        "currency_code": currency,
        "note": note,
        "payment_term": {"term_type": "DUE_ON_RECEIPT"},
    }
    if reference:
        detail["reference"] = reference

    payload: dict[str, Any] = {
        "detail": detail,
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
                    "currency_code": currency,
                    "value": f"{amount:.2f}",
                },
            }
        ],
    }

    if (
        isinstance(payload.get("amount"), dict)
        and isinstance(payload["amount"].get("breakdown"), dict)
        and "custom" in payload["amount"]["breakdown"]
    ):
        raise ValueError(
            "amount.breakdown.custom is forbidden: PayPal adds custom amount to item_total, causing double counting"
        )

    if allow_partial_payment:
        min_due = minimum_amount_due if minimum_amount_due is not None else round(amount * 0.1, 2)
        payload["configuration"] = {
            "partial_payment": {
                "allow_partial_payment": True,
                "minimum_amount_due": {
                    "currency_code": currency,
                    "value": f"{min_due:.2f}",
                },
            }
        }

    async with paypal_client() as client:
        response = await client.post(
            INVOICES_BASE,
            json=payload,
            headers={"PayPal-Request-Id": req_id},
        )

    if response.status_code not in (200, 201):
        raise PayPalCallFailed(
            f"create_invoice failed: {describe_error(response)}"
        )

    body = response.json()
    invoice_id = body.get("id") or body.get("href", "").rsplit("/", 1)[-1]
    body["id"] = invoice_id
    logger.info("Created PayPal invoice %s (idempotency=%s)", invoice_id, req_id)
    return body


async def send_invoice(invoice_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
    """Send a draft invoice; returns the PayPal response body.

    After send the invoice moves to SENT state and PayPal generates the
    payer-view link under `detail.metadata.recipient_view_url`.

    Raises:
        PayPalCallFailed: on HTTP error.
    """
    req_id = idempotency_key or str(uuid.uuid4())

    async with paypal_client() as client:
        response = await client.post(
            f"{INVOICES_BASE}/{invoice_id}/send",
            json={"send_to_invoicer": True},
            headers={"PayPal-Request-Id": req_id},
        )

    if response.status_code not in (200, 202):
        raise PayPalCallFailed(
            f"send_invoice({invoice_id}) failed: {describe_error(response)}"
        )

    body = response.json() if response.content else {}
    logger.info("Sent PayPal invoice %s", invoice_id)
    return body


async def get_invoice(invoice_id: str) -> dict[str, Any]:
    """GET the full invoice object, including payment transactions.

    Raises:
        PayPalCallFailed: on HTTP error.
    """
    async with paypal_client() as client:
        response = await client.get(f"{INVOICES_BASE}/{invoice_id}")

    if response.status_code != 200:
        raise PayPalCallFailed(
            f"get_invoice({invoice_id}) failed: {describe_error(response)}"
        )
    return response.json()


async def record_external_payment(
    invoice_id: str,
    *,
    amount: float,
    currency: str = "USD",
    payment_method: str = "BANK_TRANSFER",
    note: str = "Recorded externally by Vasooli",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """POST an external payment record to a SENT invoice.

    PayPal accepts BANK_TRANSFER, CHECK, CREDIT_CARD, DEBIT_CARD, PAYPAL,
    WIRE_TRANSFER, OTHER.  The response body carries the payment transaction id.

    Raises:
        PayPalCallFailed: on HTTP error.
    """
    req_id = idempotency_key or str(uuid.uuid4())
    payload: dict[str, Any] = {
        "method": payment_method,
        "amount": {
            "currency_code": currency,
            "value": f"{amount:.2f}",
        },
        "note": note,
    }

    async with paypal_client() as client:
        response = await client.post(
            f"{INVOICES_BASE}/{invoice_id}/payments",
            json=payload,
            headers={"PayPal-Request-Id": req_id},
        )

    if response.status_code not in (200, 201):
        raise PayPalCallFailed(
            f"record_external_payment({invoice_id}) failed: {describe_error(response)}"
        )

    body = response.json() if response.content else {}
    logger.info("Recorded external payment on invoice %s", invoice_id)
    return body
