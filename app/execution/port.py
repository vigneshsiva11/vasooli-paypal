"""Execution port and gateway adapters (Stage 5 — Execution).

Defines the ExecutionPort interface, ExecutionResult outcome type, and adapters
for Razorpay and PayPal.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.execution import razorpay, templates
from app.gateways.paypal import invoicing, orders
from app.gateways.paypal.client import PayPalCallFailed
from app.models import DecisionRecord, RevenueEvent
from app.models.execution import AuthorizedVerdict

logger = logging.getLogger(__name__)


class ExecutionPortError(RuntimeError):
    """Base error for execution port failures."""


class VaultTokenMissing(ExecutionPortError):
    """Raised when a vaulted retry is attempted without a vault token."""


class UnknownGateway(ExecutionPortError):
    """Raised when an action is requested for an unknown gateway."""


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome produced by an execution adapter."""

    status: str = "completed"
    gateway: str = "razorpay"
    gateway_ref: str | None = None
    gateway_url: str | None = None
    razorpay_payment_link_id: str | None = None
    razorpay_payment_link_url: str | None = None
    failure_reason: str | None = None

    def to_record_fields(self) -> dict[str, Any]:
        """Convert result fields to ExecutionRecord field mapping."""
        fields: dict[str, Any] = {
            "gateway": self.gateway,
        }
        if self.gateway == "razorpay":
            fields["razorpay_payment_link_id"] = self.razorpay_payment_link_id
            fields["razorpay_payment_link_url"] = self.razorpay_payment_link_url
            fields["gateway_ref"] = self.gateway_ref
            fields["gateway_url"] = self.gateway_url
        elif self.gateway == "paypal":
            fields["gateway_ref"] = self.gateway_ref
            fields["gateway_url"] = self.gateway_url
            fields["razorpay_payment_link_id"] = None
            fields["razorpay_payment_link_url"] = None
        return fields


class ExecutionPort(ABC):
    """Port interface for gateway execution adapters."""

    gateway: str

    @abstractmethod
    async def execute_link(
        self,
        *,
        verdict: AuthorizedVerdict,
        event: RevenueEvent,
        decision: DecisionRecord,
        root_cause: str,
        execution_id: str | None = None,
        credentials: Any | None = None,
    ) -> ExecutionResult:
        """Create a payment link / invoice for payment recovery."""
        ...

    @abstractmethod
    async def execute_retry(
        self,
        *,
        verdict: AuthorizedVerdict,
        event: RevenueEvent,
        decision: DecisionRecord,
        root_cause: str,
        execution_id: str | None = None,
        vault_token: str | None = None,
        credentials: Any | None = None,
    ) -> ExecutionResult:
        """Execute a retry attempt on the payment rail."""
        ...


def _link_description(*, event: RevenueEvent, intervention: str, root_cause: str) -> str:
    """The description line shown on the payment page."""
    phrase = templates.CAUSE_PHRASES.get(root_cause, "this payment did not complete")
    if intervention == "payment_method_update_link":
        text = f"Update your payment method — {phrase}"
    elif intervention == "recovery_payment_link":
        text = f"Complete your payment — {phrase}"
    else:
        text = f"Payment for {event.event_id} — {phrase}"
    return text[:255]


class RazorpayExecutionAdapter(ExecutionPort):
    """Wraps Razorpay payment link creation with identical existing behavior."""

    gateway = "razorpay"

    async def execute_link(
        self,
        *,
        verdict: AuthorizedVerdict,
        event: RevenueEvent,
        decision: DecisionRecord,
        root_cause: str,
        execution_id: str | None = None,
        credentials: razorpay.RazorpayCredentials | None = None,
    ) -> ExecutionResult:
        link = await razorpay.create_payment_link(
            amount=decision.revenue_at_risk,
            currency=event.currency,
            description=_link_description(
                event=event,
                intervention=decision.recommended_intervention,
                root_cause=root_cause,
            ),
            reference_id=razorpay.reference_id_for(verdict.id),
            notes={
                "vasooli_event_id": event.event_id,
                "vasooli_customer_ref": event.customer_ref,
                "vasooli_policy_verdict_id": verdict.id,
                "vasooli_intervention": decision.recommended_intervention,
                "vasooli_root_cause": root_cause,
            },
            credentials=credentials,
        )
        return ExecutionResult(
            status="completed",
            gateway="razorpay",
            razorpay_payment_link_id=link.id,
            razorpay_payment_link_url=link.short_url,
        )

    async def execute_retry(
        self,
        *,
        verdict: AuthorizedVerdict,
        event: RevenueEvent,
        decision: DecisionRecord,
        root_cause: str,
        execution_id: str | None = None,
        vault_token: str | None = None,
        credentials: razorpay.RazorpayCredentials | None = None,
    ) -> ExecutionResult:
        # Razorpay retries are simulated as payment links (identical to execute_link)
        return await self.execute_link(
            verdict=verdict,
            event=event,
            decision=decision,
            root_cause=root_cause,
            execution_id=execution_id,
            credentials=credentials,
        )


class PayPalExecutionAdapter(ExecutionPort):
    """PayPal execution adapter for invoicing (links) and vaulted orders (retries)."""

    gateway = "paypal"

    async def execute_link(
        self,
        *,
        verdict: AuthorizedVerdict,
        event: RevenueEvent,
        decision: DecisionRecord,
        root_cause: str,
        execution_id: str | None = None,
        credentials: Any | None = None,
    ) -> ExecutionResult:
        buyer_email = get_settings().paypal_buyer_email or f"{event.customer_ref}@example.com"
        inv = await invoicing.create_invoice(
            buyer_email=buyer_email,
            amount=decision.revenue_at_risk,
            currency="USD",
            note=f"Vasooli recovery invoice for event {event.event_id}",
            reference=f"vsl_{verdict.id}",
            idempotency_key=f"vsl_{verdict.id}",
        )
        invoice_id = inv.get("id") or inv.get("href", "").rsplit("/", 1)[-1]
        await invoicing.send_invoice(invoice_id, idempotency_key=f"vsl_send_{verdict.id}")
        fetched = await invoicing.get_invoice(invoice_id)
        url = (
            fetched.get("detail", {}).get("metadata", {}).get("recipient_view_url")
            or fetched.get("recipient_view_url")
        )
        if not url:
            for link in fetched.get("links", []):
                if link.get("rel") == "payer-view":
                    url = link.get("href")
                    break
        return ExecutionResult(
            status="completed",
            gateway="paypal",
            gateway_ref=invoice_id,
            gateway_url=url,
        )

    async def execute_retry(
        self,
        *,
        verdict: AuthorizedVerdict,
        event: RevenueEvent,
        decision: DecisionRecord,
        root_cause: str,
        execution_id: str | None = None,
        vault_token: str | None = None,
        credentials: Any | None = None,
    ) -> ExecutionResult:
        if not vault_token:
            raise VaultTokenMissing(
                f"No vault token supplied for customer {event.customer_ref}; vaulted retry cannot proceed"
            )

        captured = await orders.create_vaulted_order(
            vault_id=vault_token,
            amount=decision.revenue_at_risk,
            currency="USD",
            reference_id=f"vsl_{verdict.id}",
            custom_id=f"ev:{event.event_id}",
            idempotency_key=execution_id,
        )
        order_id = captured.get("id", "")
        url = None
        for link in captured.get("links", []):
            if link.get("rel") in ("self", "approve"):
                url = link.get("href")
                break
        return ExecutionResult(
            status="completed",
            gateway="paypal",
            gateway_ref=order_id,
            gateway_url=url,
        )
