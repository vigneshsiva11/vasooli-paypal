"""PayPal sandbox gateway — additive companion to the Razorpay executor.

Layout mirrors the existing app/execution/ pattern:

* client.py     — OAuth token lifecycle + httpx client factory
* invoicing.py  — Invoices v2: create, send, record-payment, get
* orders.py     — Orders v2: create, capture, vault-retry
* webhooks.py   — Inbound webhook verification via PayPal's verify API

Nothing in this package imports from app/execution/, app/webhooks/, or
app/models/verification.py.  It is purely additive.
"""

from app.gateways.paypal.client import (
    SANDBOX_BASE,
    PayPalCallFailed,
    PayPalCredentials,
    PayPalNotConfigured,
    credentials_from_settings,
    get_access_token,
    paypal_client,
)

from app.gateways.paypal.charge_attempts import (
    get_charge_attempt,
    record_charge_attempt,
)
from app.gateways.paypal.vault import (
    get_active_vault_token,
    store_vault_token,
)

__all__ = [
    "SANDBOX_BASE",
    "PayPalCallFailed",
    "PayPalCredentials",
    "PayPalNotConfigured",
    "credentials_from_settings",
    "get_access_token",
    "paypal_client",
    "get_charge_attempt",
    "record_charge_attempt",
    "get_active_vault_token",
    "store_vault_token",
]
