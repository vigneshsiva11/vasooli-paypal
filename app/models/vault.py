"""Domain models for customer vault tokens (Step 4)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

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
    source_event_id: str = Field(
        ...,
        min_length=1,
        description="PayPal webhook event ID that created this token.",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the token record was created.",
    )
