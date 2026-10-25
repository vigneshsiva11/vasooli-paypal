"""Domain models for PayPal charge attempts (Step 1)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


ChargeAttemptPurpose = Literal["onboarding", "renewal"]


class ChargeAttempt(BaseModel):
    """A record of an initiated or created PayPal charge attempt."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(
        ...,
        min_length=1,
        description="PayPal order identifier (unique).",
    )
    customer_ref: str = Field(
        ...,
        min_length=1,
        description="Merchant customer reference.",
    )
    amount: float = Field(
        ...,
        ge=0,
        description="Charge attempt amount in major units.",
    )
    currency: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
        description="ISO 4217 currency code.",
    )
    purpose: ChargeAttemptPurpose = Field(
        ...,
        description="Business purpose of the charge attempt: 'onboarding' or 'renewal'.",
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        description="When the attempt was recorded (UTC).",
    )
