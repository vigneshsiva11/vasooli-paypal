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
        """List revenue events currently at risk of abandonment, sanitized of customer text."""
        return await tools.dispatch_tool("list_at_risk_events", {"limit": limit}, agent_id="mcp_gateway")

    @server.tool()
    async def get_event_audit(event_id: str) -> dict[str, Any]:
        """Retrieve complete audit history and timeline for an event, sanitized of customer text."""
        return await tools.dispatch_tool("get_event_audit", {"event_id": event_id}, agent_id="mcp_gateway")

    @server.tool()
    async def get_active_rules(currency: str = "USD") -> dict[str, Any]:
        """Retrieve active policy rulebook parameters and thresholds for a currency."""
        return await tools.dispatch_tool("get_active_rules", {"currency": currency}, agent_id="mcp_gateway")

    @server.tool()
    async def propose_recovery(
        event: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dry-run evaluate payment recovery intervention using real pure functions under active rulebook.

        Writes ZERO records to events, executions, verdicts, ledger, or metrics.
        Returns verdict, reason, checks, and fingerprint.
        """
        return await tools.dispatch_tool(
            "propose_recovery",
            {"event": event, "context": context},
            agent_id="mcp_gateway",
        )

    @server.tool()
    async def propose_contact(
        event: dict[str, Any],
        context: dict[str, Any] | None = None,
        intervention: str | None = None,
    ) -> dict[str, Any]:
        """Dry-run evaluate customer contact intervention using real pure functions under active rulebook.

        Writes ZERO records to events, executions, verdicts, ledger, or metrics.
        Returns verdict, reason, checks, and fingerprint.
        """
        return await tools.dispatch_tool(
            "propose_contact",
            {"event": event, "context": context, "intervention": intervention},
            agent_id="mcp_gateway",
        )

    @server.tool()
    async def execute_recovery(
        verdict: dict[str, Any],
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute recovery for an AuthorizedVerdict through structural path.

        Refuses unauthorized verdicts, attack-range scenarios, non-sandbox executions,
        and enforces a 10-minute per-event cooldown.
        """
        return await tools.dispatch_tool(
            "execute_recovery",
            {"verdict": verdict, "event_id": event_id},
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

