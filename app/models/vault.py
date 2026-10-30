"""Domain models for customer vault tokens (Step 4)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CustomerVaultToken(BaseModel):
    """A vaulted payment token record. Never stores full card numbers."""

    model_config = ConfigDict(extra="forbid")

    customer_ref: str = Field(
        ...,
        min_length=1,
        description="Merchant customer reference.",
    )
    vault_id: str = Field(
        ...,
        min_length=1,
        description="Unique PayPal vault token ID.",
    )
    card_brand: str = Field(
        ...,
        min_length=1,
        description="Card brand (e.g. VISA, MASTERCARD).",
    )
    last_digits: str = Field(
        ...,
        min_length=1,
        max_length=4,
        description="Last digits only (never full card numbers).",
    )
    status: Literal["active", "inactive", "revoked"] = Field(
        default="active",
        description="Current token status.",
    )
    payment_source: str | None = Field(
        default=None,
        description="Payment source type (e.g. card). Optional.",
    )
    card_last_digits: str | None = Field(
        default=None,
        description="Optional alias for last_digits.",
    )
    source_event_id: str = Field(
        default="",
        description="PayPal webhook event ID that created this token.",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the token record was created.",
    )

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "CustomerVaultToken":
        """Build a token record from a raw MongoDB document."""
        fields = {k: v for k, v in document.items() if k != "_id"}
        if "card_last_digits" in fields and "last_digits" not in fields:
            fields["last_digits"] = fields["card_last_digits"]
        return cls(**fields)
