"""PayPal Orders v2 — create, capture, vault-retry.

Covers spike steps S3, S4, S5:
  - create_and_capture_order: Orders v2 with card payment_source and optional
    vault-on-success (attributes.vault.store_in_vault=ON_SUCCESS)
  - create_vaulted_order: second order using a stored vault token (no browser)

PayPal sandbox test cards and decline triggers:
  https://developer.paypal.com/tools/sandbox/card-testing/

All create calls include PayPal-Request-Id for idempotency.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.gateways.paypal.client import PayPalCallFailed, describe_error, paypal_client

logger = logging.getLogger(__name__)

ORDERS_URL = "/v2/checkout/orders"

# ---------------------------------------------------------------------------
# PayPal sandbox test card (Visa, no 3DS required in sandbox)
# ---------------------------------------------------------------------------
#
# Source: https://developer.paypal.com/tools/sandbox/card-testing/
# This is the PayPal-documented test Visa that passes in sandbox without
# requiring a browser challenge.  Do not log or commit real card numbers.
#
SANDBOX_TEST_VISA = {
    "number": "4111111111111111",  # sandbox test Visa — valid sandbox test card
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

# ---------------------------------------------------------------------------
# Decline simulation triggers (S5)
# Source: https://developer.paypal.com/tools/sandbox/card-testing/
# In sandbox, passing CCREJECT-* in the name field triggers specific decline codes.
# ---------------------------------------------------------------------------
DECLINE_TEST_CARDS: list[dict[str, Any]] = [
    {
        "trigger": "Insufficient funds",
        "number": "4111111111111111",
        "expiry": "2028-12",
        "security_code": "123",
        "name": "CCREJECT-IF",  # triggers INSUFFICIENT_FUNDS (5120)
        "billing_address": SANDBOX_TEST_VISA["billing_address"],
    },
    {
        "trigger": "Do not honor",
        "number": "4111111111111111",
        "expiry": "2028-12",
        "security_code": "123",
        "name": "CCREJECT-REFUSED",  # triggers DO_NOT_HONOR (0500)
        "billing_address": SANDBOX_TEST_VISA["billing_address"],
    },
    {
        "trigger": "CVV check failure",
        "number": "4111111111111111",
        "expiry": "2028-12",
        "security_code": "123",
        "name": "CCREJECT-CVV_F",  # triggers CVV2_FAILURE (00N7)
        "billing_address": SANDBOX_TEST_VISA["billing_address"],
    },
]


def _card_payment_source(
    card: dict[str, Any],
    *,
    vault_on_success: bool = False,
    customer_id: str | None = None,
) -> dict[str, Any]:
    """Build the payment_source.card block for an Orders v2 request."""
    card_block: dict[str, Any] = {
        "number": card["number"],
        "expiry": card["expiry"],
        "security_code": card["security_code"],
        "name": card.get("name", "Test Buyer"),
        "billing_address": card.get("billing_address", {}),
    }
    if vault_on_success:
        card_block["attributes"] = {
            "vault": {"store_in_vault": "ON_SUCCESS"},
        }
        if customer_id:
            card_block["attributes"]["customer"] = {"id": customer_id}
    return {"card": card_block}


async def create_and_capture_order(
    *,
    amount: float,
    currency: str = "USD",
    reference_id: str | None = None,
    description: str = "Vasooli recovery payment",
    vault_on_success: bool = True,
    card: dict[str, Any] | None = None,
    customer_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create an Orders v2 order and immediately capture it (server-side, no browser).

    Uses the PayPal sandbox test Visa by default.  Optionally vaults the card
    on a successful capture.

    Args:
        amount: Charge amount in major units (dollars).
        currency: ISO 4217.
        reference_id: Our idempotency reference stored in the order.
        description: Item description.
        vault_on_success: If True, requests card vaulting via
            attributes.vault.store_in_vault=ON_SUCCESS.
        card: Override the default sandbox test card dict.
        customer_id: PayPal customer id for vault attribution.
        idempotency_key: Deterministic key for PayPal-Request-Id.

    Returns:
        The full capture response body from PayPal.

    Raises:
        PayPalCallFailed: on HTTP error at either step.
    """
    req_id = idempotency_key or str(uuid.uuid4())
    ref = reference_id or req_id
    card_data = card or SANDBOX_TEST_VISA

    order_payload: dict[str, Any] = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": ref[:127],
                "description": description[:127],
                "amount": {
                    "currency_code": currency,
                    "value": f"{amount:.2f}",
                },
            }
        ],
        "payment_source": _card_payment_source(
            card_data,
            vault_on_success=vault_on_success,
            customer_id=customer_id,
        ),
    }

    async with paypal_client() as client:
        create_resp = await client.post(
            ORDERS_URL,
            json=order_payload,
            headers={"PayPal-Request-Id": req_id},
        )

    if create_resp.status_code not in (200, 201):
        raise PayPalCallFailed(
            f"create_order failed: {describe_error(create_resp)}"
        )

    order = create_resp.json()
    order_id = order.get("id", "")
    order_status = order.get("status", "")

    logger.info("Created PayPal order %s status=%s", order_id, order_status)

    # If the order was already COMPLETED (e.g. sandbox auto-capture), return it.
    if order_status == "COMPLETED":
        return order

    # Otherwise, capture.
    async with paypal_client() as client:
        capture_resp = await client.post(
            f"{ORDERS_URL}/{order_id}/capture",
            json={},
            headers={"PayPal-Request-Id": f"{req_id}-capture"},
        )

    if capture_resp.status_code not in (200, 201):
        raise PayPalCallFailed(
            f"capture_order({order_id}) failed: {describe_error(capture_resp)}"
        )

    captured = capture_resp.json()
    logger.info(
        "Captured PayPal order %s status=%s",
        order_id,
        captured.get("status"),
    )
    return captured


def _extract_vault_id(capture_response: dict[str, Any]) -> str | None:
    """Walk the capture response tree for the vault token id.

    PayPal buries the vault id under:
      payment_source.card.attributes.vault.id
    or under purchase_units[*].payments.captures[*].payment_source.card.attributes.vault.id
    """
    ps = capture_response.get("payment_source", {})
    vault = ps.get("card", {}).get("attributes", {}).get("vault", {})
    if vault_id := vault.get("id"):
        return str(vault_id)

    for unit in capture_response.get("purchase_units", []):
        for cap in unit.get("payments", {}).get("captures", []):
            vault = (
                cap.get("payment_source", {})
                .get("card", {})
                .get("attributes", {})
                .get("vault", {})
            )
            if vault_id := vault.get("id"):
                return str(vault_id)
    return None


async def create_vaulted_order(
    *,
    vault_id: str,
    amount: float,
    currency: str = "USD",
    reference_id: str | None = None,
    custom_id: str | None = None,
    description: str = "Vasooli vaulted retry",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Charge a previously vaulted card — no browser required.

    Uses the vault token id from a prior capture to build a server-side
    payment_source.card.vault_id order.

    Args:
        vault_id: The id returned under payment_source.card.attributes.vault
            after a successful vault-on-success capture.
        amount: Charge amount in major units.
        currency: ISO 4217.
        reference_id: Our idempotency reference.
        custom_id: Merchant tracking reference (e.g. ev:<event_id>).
        description: Item description.
        idempotency_key: PayPal-Request-Id value.

    Returns:
        The full capture response body.

    Raises:
        PayPalCallFailed: on HTTP error.
    """
    req_id = idempotency_key or str(uuid.uuid4())
    ref = reference_id or req_id

    pu: dict[str, Any] = {
        "reference_id": ref[:127],
        "description": description[:127],
        "amount": {
            "currency_code": currency,
            "value": f"{amount:.2f}",
        },
    }
    if custom_id:
        pu["custom_id"] = custom_id

    order_payload: dict[str, Any] = {
        "intent": "CAPTURE",
        "purchase_units": [pu],
        "payment_source": {
            "card": {
                "vault_id": vault_id,
            }
        },
    }

    async with paypal_client() as client:
        create_resp = await client.post(
            ORDERS_URL,
            json=order_payload,
            headers={"PayPal-Request-Id": req_id},
        )

    if create_resp.status_code not in (200, 201):
        raise PayPalCallFailed(
            f"create_vaulted_order failed: {describe_error(create_resp)}"
        )

    order = create_resp.json()
    order_id = order.get("id", "")
    order_status = order.get("status", "")
    logger.info("Created vaulted order %s status=%s", order_id, order_status)

    if order_status == "COMPLETED":
        return order

    async with paypal_client() as client:
        capture_resp = await client.post(
            f"{ORDERS_URL}/{order_id}/capture",
            json={},
            headers={"PayPal-Request-Id": f"{req_id}-capture"},
        )

    if capture_resp.status_code not in (200, 201):
        raise PayPalCallFailed(
            f"capture_vaulted_order({order_id}) failed: {describe_error(capture_resp)}"
        )

    captured = capture_resp.json()
    logger.info(
        "Captured vaulted order %s status=%s", order_id, captured.get("status")
    )
    return captured


__all__ = [
    "DECLINE_TEST_CARDS",
    "SANDBOX_TEST_VISA",
    "create_and_capture_order",
    "create_vaulted_order",
    "_extract_vault_id",
]
