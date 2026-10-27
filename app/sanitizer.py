"""Public API response sanitization and forbidden keys scanner.

Ensures no credential-like or personal information (vault IDs/tokens, emails,
PayPal customer IDs, card brand or last digits, raw webhook payloads, invoice payer URLs)
leaks in public GET responses.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

# Keys that must never appear in public responses (case-insensitive substring match)
FORBIDDEN_KEY_SUBSTRINGS = (
    "vault",
    "token",
    "email",
    "card",
    "last_digits",
    "payer",
)

# Allowed identifiers that contain allowed patterns (e.g., event_id, execution_id)
ALLOWED_ID_KEYS = frozenset({
    "id",
    "_id",
    "event_id",
    "execution_id",
    "verification_id",
    "order_id",
    "invoice_id",
    "customer_ref",
    "policy_verdict_id",
    "decision_id",
    "diagnosis_id",
    "confirmation_id",
    "run_id",
})

# Invoice / payment link sensitive URL pattern
INVOICE_PAYER_URL_PATTERN = re.compile(
    r"(https?://[^\s]+/invoice/p/[^\s]+|https?://[^\s]+/invoicing/invoices/[^\s]+)"
)


def mask_invoice_payer_url(url: str | None) -> str | None:
    """Mask sensitive tokens or payer hash in invoice URLs while keeping safe prefix."""
    if not url or not isinstance(url, str):
        return url
    if "/invoice/p/" in url:
        base = url.split("/invoice/p/")[0]
        return f"{base}/invoice/p/[MASKED]"
    if "token=" in url:
        return re.sub(r"token=[^&\s]+", "token=[MASKED]", url)
    return url


def sanitize_public_dict(data: Any) -> Any:
    """Recursively strip forbidden credential and PII keys from dicts and lists.

    - Strips keys containing 'vault', 'token', 'email', 'card', 'last_digits', 'payer'
      (unless specifically in ALLOWED_ID_KEYS).
    - Strips 'payload', 'raw_payload', 'raw_webhook_payload', 'event_data'.
    - Strips nested 'customer.id' (e.g. {'customer': {'id': ...}}).
    - Masks invoice payer URLs in gateway_url and similar strings.
    """
    if isinstance(data, dict):
        cleaned: dict[str, Any] = {}
        for k, v in data.items():
            k_str = str(k)
            k_lower = k_str.lower()

            # Strip raw webhook payloads
            if k_lower in ("payload", "raw_payload", "raw_webhook_payload", "event_data"):
                continue

            # Strip internal client tracking IP if present
            if k_lower == "client_ip":
                continue

            # Strip nested customer.id (where customer is a dict containing 'id')
            if k_lower == "customer" and isinstance(v, dict):
                v_sanitized = sanitize_public_dict(v)
                v_sanitized.pop("id", None)
                if v_sanitized:
                    cleaned[k_str] = v_sanitized
                continue

            # Check if key contains forbidden substrings, unless it's in ALLOWED_ID_KEYS
            if k_str not in ALLOWED_ID_KEYS and any(bad in k_lower for bad in FORBIDDEN_KEY_SUBSTRINGS):
                continue

            # Mask invoice payer URLs in string values
            if isinstance(v, str) and ("invoice" in v or "token=" in v):
                cleaned[k_str] = mask_invoice_payer_url(v)
            else:
                cleaned[k_str] = sanitize_public_dict(v)
        return cleaned

    if isinstance(data, list):
        return [sanitize_public_dict(item) for item in data]

    if isinstance(data, tuple):
        return tuple(sanitize_public_dict(item) for item in data)

    return data


def assert_no_forbidden_keys(obj: Any, path: str = "root") -> list[str]:
    """Recursively inspect an object and return a list of violations.

    Raises AssertionError if forbidden keys (vault, token, email, card,
    last_digits, payer, customer.id) appear in dictionary keys.
    """
    violations: list[str] = []

    def _scan(curr: Any, cur_path: str) -> None:
        if isinstance(curr, dict):
            for k, v in curr.items():
                k_str = str(k)
                k_lower = k_str.lower()
                field_path = f"{cur_path}.{k_str}"

                # Check customer.id
                if k_lower == "customer" and isinstance(v, dict) and "id" in v:
                    violations.append(f"{field_path}.id (forbidden customer.id)")

                # Check forbidden substrings unless allowed ID
                if k_str not in ALLOWED_ID_KEYS:
                    for bad in FORBIDDEN_KEY_SUBSTRINGS:
                        if bad in k_lower:
                            violations.append(f"{field_path} (forbidden key pattern: '{bad}')")

                _scan(v, field_path)
        elif isinstance(curr, (list, tuple)):
            for idx, item in enumerate(curr):
                _scan(item, f"{cur_path}[{idx}]")

    _scan(obj, path)
    if violations:
        raise AssertionError(f"Forbidden keys found in public response: {violations}")
    return violations
