# Vasooli Agent Gateway & Model Context Protocol (MCP) Server

The Vasooli Agent Gateway provides a secure, audited boundary for AI agents to inspect revenue risk and propose recovery actions under active policy rulebooks.

Mounted at `/mcp` over Streamable HTTP, the gateway exposes strictly gated tools and rejects direct, unvalidated state manipulation or financial execution.

---

## 1. Connection Details

- **MCP Transport**: Streamable HTTP (Official MCP Python SDK `mcp >= 2.3.0`)
- **Connection URL**: `http://127.0.0.1:8123/mcp`
- **Authentication**: Bearer token via HTTP Header:
  ```http
  Authorization: Bearer <AGENT_TOKEN>
  ```
  - In production (`ENVIRONMENT=production` or `RENDER=true`), returns HTTP `503 Service Unavailable` if `AGENT_TOKEN` is unset.
  - Missing or invalid tokens return HTTP `401 Unauthorized` with `WWW-Authenticate: Bearer`.
  - Token comparison is constant-time (`hmac.compare_digest`). Token strings are never written to logs.
- **Rate Limit**: 60 requests per minute per token hash, tracked atomically in MongoDB collection `agent_counters`. Returns HTTP `429 Too Many Requests` when exceeded.

---

## 2. Exposed Tools

The MCP server exposes **EXACTLY** the 6 tools from the Gateway Registry (`app.gateway.tools.ALLOWED_TOOLS`) and nothing else:

| Tool Name | Description | State Impact |
|:---|:---|:---|
| `list_at_risk_events(limit: int = 50)` | Lists at-risk revenue events, sanitized of raw customer text. | Read-Only (0 writes) |
| `get_event_audit(event_id: str)` | Retrieves complete audit history and timeline for an event. | Read-Only (0 writes) |
| `get_active_rules(currency: str = "USD")` | Returns active policy rulebook thresholds and parameters. | Read-Only (0 writes) |
| `propose_recovery(event: dict, context: dict)` | Dry-run evaluates recovery intervention using pure policy evaluation. | Dry-Run (0 business writes) |
| `propose_contact(event: dict, context: dict, intervention: str)` | Dry-run evaluates customer outreach against cooldowns and opt-outs. | Dry-Run (0 business writes) |
| `execute_recovery(verdict: dict, event_id: str)` | Submits an authorized verdict for sandbox execution through structural path. | Strictly Gated Execution |

Every tool invocation is logged into the `agent_audit` collection with arguments hash, client identity hash, rulebook fingerprint, and outcome (`allowed`, `blocked`, `refused`, `manual_review`, `error`).

---

## 3. Threat Model & Guardrails

The Gateway enforces defense-in-depth between autonomous agents and core financial state:

| Threat / Vector | Mechanism / Guardrail | Behavior |
|:---|:---|:---|
| **Prompt Injection via Customer Text** | Field sanitization via `app.gateway.tools.sanitize_customer_facing_data` | Customer text and contact fields are redacted (`[REDACTED]`) before being returned to agents. |
| **Direct State Mutation via Admin Tools** | Forbidden tool allowlist check | Tools like `apply_rules`, `revert`, `reset`, `advance_clock`, `tick`, `run_scenario`, and direct PayPal APIs are NOT registered in the MCP tool registry. Attempting to call unlisted tools yields refusal `"not offered"` and triggers audit logging. |
| **Unauthorized Financial Execution** | Structural Verdict Validation | `execute_recovery` requires a structurally complete, authorized verdict signed with an active rulebook fingerprint. Unapproved amounts (> $1000) or manual review flags fail closed. |
| **Execution on Attack-Range Scenarios** | Scenario Range Check | Event IDs in the attack scenario test range (`scen_atk_*`) are rejected immediately without execution. |
| **Rapid Double-Execution / Race Condition** | In-memory & DB Cooldown Guard | `execute_recovery` enforces a mandatory 10-minute cooldown per `event_id`. Consecutive calls within 10 minutes are refused. |
| **Replay / Tampered Rulebooks** | Cryptographic Fingerprint Verification | Recovery proposals verify that the rulebook fingerprint matches the currently active rulebook. |
| **Exhaustion / DoS** | Atomic Sliding-Window Limiters | Enforces 60 requests/minute per agent token and 100 requests/hour globally. |

---

## 4. Connecting to the MCP Server

### A. Using MCP Inspector

To test and inspect tools interactively:

```bash
npx @modelcontextprotocol/inspector
```

In the Inspector UI:
1. Set **Transport Type** to `Streamable HTTP`.
2. Set **URL** to:
   ```
   http://127.0.0.1:8123/mcp
   ```
3. Add Custom Header:
   - Name: `Authorization`
   - Value: `Bearer <YOUR_AGENT_TOKEN>`
4. Click **Connect**. You will see the 6 registered tools available to invoke.

### B. Using Python MCP Client

```python
import asyncio
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    headers = {"Authorization": "Bearer test-agent-token"}
    async with streamable_http_client(
        "http://127.0.0.1:8123/mcp",
        headers=headers,
    ) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            
            # List available tools
            tools = await session.list_tools()
            print("Available tools:", [t.name for t in tools.tools])
            
            # Call a tool
            result = await session.call_tool("get_active_rules", arguments={"currency": "USD"})
            print("Active rules:", result)

asyncio.run(main())
```

### C. In-Memory Testing Client

For test suites, use `mcp.client.mcpserver.Client(mcp_server)` to directly test tools without starting a network server:

```python
from mcp.client.mcpserver import Client
from app.gateway.mcp_server import mcp_server

async with Client(mcp_server) as client:
    tools_result = await client.list_tools()
    tool_names = [t.name for t in tools_result.tools]
    assert set(tool_names) == {"list_at_risk_events", "get_event_audit", "get_active_rules", "propose_recovery", "propose_contact", "execute_recovery"}
```

