"""Model Context Protocol (MCP) server for Vasooli Agent Gateway.

Exposes EXACTLY the tools in the gateway registry (ALLOWED_TOOLS) and nothing else.
Mounted at /mcp over Streamable HTTP with Bearer authentication and rate limiting.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from app.gateway import tools

logger = logging.getLogger(__name__)


def create_mcp_server() -> MCPServer:
    """Create and configure a fresh MCPServer instance with gateway tools registered."""
    server = MCPServer(
        name="vasooli_agent_gateway",
        instructions="Vasooli Agent Gateway: strictly audited, policy-gated revenue recovery tools.",
    )

    @server.tool()
    async def list_at_risk_events(limit: int = 50) -> dict[str, Any]:
        """List revenue events currently at risk of abandonment, sanitized of customer text.

        Input shape:
        {"limit": 50}

        Example:
        {"limit": 10}
        """
        return await tools.dispatch_tool("list_at_risk_events", {"limit": limit}, agent_id="mcp_gateway")

    @server.tool()
    async def get_event_audit(event_id: Any = None) -> dict[str, Any]:
        """Retrieve complete audit history and timeline for an event, sanitized of customer text.

        Input shape:
        {"event_id": "<event_id_string>"}

        Example:
        {"event_id": "evt_12345"}
        """
        return await tools.dispatch_tool("get_event_audit", {"event_id": event_id}, agent_id="mcp_gateway")

    @server.tool()
    async def get_active_rules(currency: Any = "USD") -> dict[str, Any]:
        """Retrieve active policy rulebook parameters and thresholds for a currency.

        Input shape:
        {"currency": "USD"}

        Example:
        {"currency": "USD"}
        """
        return await tools.dispatch_tool("get_active_rules", {"currency": currency}, agent_id="mcp_gateway")

    @server.tool()
    async def propose_recovery(
        event: Any = None,
        context: Any = None,
    ) -> dict[str, Any]:
        """Dry-run evaluate payment recovery intervention using real pure functions under active rulebook.
        Writes zero records to events, executions, verdicts, ledger, or metrics.
        Returns verdict, reason, checks, and fingerprint.

        Input shape:
        {
          "event": {
            "event_id": "<string>",
            "surface": "payment|subscription|checkout|receivable",
            "amount": <number>,
            "customer_ref": "<string>",
            "currency": "<optional_iso_code>",
            "raw_failure_reason": "<optional_string>",
            "gateway": "<optional_razorpay_or_paypal>"
          },
          "context": {
            "customer_ref": "<optional_string>",
            "customer_opted_out": <optional_bool>,
            "prior_authorized_contacts": <optional_int>,
            "has_vault_token": <optional_bool>,
            "processor_response_code": "<optional_string>"
          }
        }

        Example:
        {
          "event": {
            "event_id": "evt_rec_001",
            "surface": "subscription",
            "amount": 49.0,
            "currency": "USD",
            "customer_ref": "cust_123"
          }
        }
        """
        return await tools.dispatch_tool(
            "propose_recovery",
            {"event": event, "context": context},
            agent_id="mcp_gateway",
        )

    @server.tool()
    async def propose_contact(
        event: Any = None,
        context: Any = None,
        intervention: Any = None,
    ) -> dict[str, Any]:
        """Dry-run evaluate customer contact intervention using real pure functions under active rulebook.
        Writes zero records to events, executions, verdicts, ledger, or metrics.
        Returns verdict, reason, checks, and fingerprint.

        Input shape:
        {
          "event": {
            "event_id": "<string>",
            "surface": "payment|subscription|checkout|receivable",
            "amount": <number>,
            "customer_ref": "<string>",
            "currency": "<optional_iso_code>"
          },
          "context": {
            "customer_ref": "<optional_string>",
            "customer_opted_out": <optional_bool>,
            "prior_authorized_contacts": <optional_int>
          },
          "intervention": "<optional_contact_intervention_name>"
        }

        Example:
        {
          "event": {
            "event_id": "evt_con_001",
            "surface": "subscription",
            "amount": 49.0,
            "currency": "USD",
            "customer_ref": "cust_123"
          },
          "intervention": "payment_method_update_link"
        }
        """
        return await tools.dispatch_tool(
            "propose_contact",
            {"event": event, "context": context, "intervention": intervention},
            agent_id="mcp_gateway",
        )

    @server.tool()
    async def execute_recovery(
        verdict: Any = None,
        event_id: Any = None,
    ) -> dict[str, Any]:
        """Execute recovery for an AuthorizedVerdict through structural path.
        Refuses unauthorized verdicts, attack-range scenarios, non-sandbox executions,
        and enforces a 10-minute per-event cooldown.

        Input shape:
        {
          "verdict": {
            "event_id": "<string>",
            "decision_id": "<24_hex_id>",
            "decision_version": <int>,
            "verdict": "authorized",
            "reason": "ok",
            "checks_performed": ["<check_name: PASS (ok)>"],
            "rulebook_fingerprint": "<string>"
          },
          "event_id": "<optional_string>"
        }

        Example:
        {
          "verdict": {
            "event_id": "evt_safe_001",
            "decision_id": "64b1f2000000000000000001",
            "decision_version": 1,
            "verdict": "authorized",
            "reason": "ok",
            "checks_performed": ["amount_tier: PASS (ok)"],
            "rulebook_fingerprint": "rb1_8221b1b9069ffc80"
          }
        }
        """
        return await tools.dispatch_tool(
            "execute_recovery",
            {"verdict": verdict, "event_id": event_id},
            agent_id="mcp_gateway",
        )

    @server.tool()
    async def list_attack_scenarios() -> dict[str, Any]:
        """List all 7 in-memory attack and control scenarios from the gateway catalogue.
        Dry-run only: writes zero records to events, executions, verdicts, ledger, or metrics.
        Writes only agent_audit rows.

        Input shape:
        {}

        Example:
        {}
        """
        return await tools.dispatch_tool(
            "list_attack_scenarios",
            {},
            agent_id="mcp_gateway",
        )

    @server.tool()
    async def run_attack_scenario(scenario_id: Any = None) -> dict[str, Any]:
        """Execute a catalogue attack scenario in-memory through real pure functions.
        Dry-run only: writes zero records to events, executions, verdicts, ledger, or metrics.
        Writes only an agent_audit row.

        Input shape:
        {"scenario_id": "<scenario_id_string>"}

        Example:
        {"scenario_id": "retry_on_9500"}
        """
        return await tools.dispatch_tool(
            "run_attack_scenario",
            {"scenario_id": scenario_id},
            agent_id="mcp_gateway",
        )

    return server


# Initialize default MCP server instance
mcp_server = create_mcp_server()

_mcp_starlette_app: Starlette | None = None


def get_mcp_app() -> Starlette:
    """Return the Starlette StreamableHTTP application for MCP mounting."""
    global _mcp_starlette_app
    if _mcp_starlette_app is None:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
        _mcp_starlette_app = mcp_server.streamable_http_app(
            streamable_http_path="/",
            transport_security=security,
        )
    return _mcp_starlette_app
