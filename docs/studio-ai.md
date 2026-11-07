# AG Studio AI Assistant Proxy (`POST /ai/studio`)

## 1. Overview & Architecture

AG Studio embedding in Vasooli features a client-side assistant loop connecting via `AgLlmAdapter`. Rather than exposing the Gemini API key directly to the browser, all assistant requests route through the backend proxy at `POST /ai/studio`.

```
+------------------+         POST /ai/studio          +-------------------------+
| AG Studio Web UI | -------------------------------> | Vasooli Backend Proxy   |
| (AgLlmAdapter)   | <------------------------------- | (FastAPI /ai/studio)    |
+------------------+     Server-Sent Events (SSE)     +-------------------------+
                                                                   |
                                                      1. Check Kill Switch (503)
                                                      2. Validate Body Cap 32KB (413)
                                                      3. Validate Tool Schemas (400)
                                                      4. Enforce Fixed Models Only
                                                      5. Check 4-Tier Limits (429)
                                                      6. Server-Side System Prompt
                                                      7. Response Cache Check
                                                      8. Call Gemini with Fallback
                                                                   |
                                                                   v
                                                      +-------------------------+
                                                      | Google Gemini API       |
                                                      | Primary: gemini-3.6-flash|
                                                      | Lite: gemini-3.5-flash-lite|
                                                      +-------------------------+
```

---

## 2. Controls & Security Guardrails

### A. Kill Switch (`STUDIO_AI_ENABLED`)
- **Default**: `false` (`503 Service Unavailable` returned immediately when disabled).
- Parsed fail-closed like `PUBLIC_DEMO` and `PUBLIC_RUNS`.
- Can be activated without rebuilding via environment variable `STUDIO_AI_ENABLED=true`.

### B. Payload Size Cap
- **Cap**: 32 KB (`32768` bytes).
- Over-sized requests are rejected with `HTTP 413 Payload Too Large`.

### C. Server-Side System Prompt
- The server **completely ignores** any client-supplied `instructions` field.
- Injects a strict read-only system prompt constraining the assistant to dashboard queries and visualizations only.

### D. Strict Tool Schema Allowlist
- Tool schemas supplied in `tools` must match the server's bundled copy of official AG Studio tool schemas **EXACTLY** in name and parameter JSON schema.
- Unrecognized tools, extra tools, or modified parameter schemas are rejected with `HTTP 400 Bad Request`.

### E. Fixed Models Only
- Allowed models are strictly limited to the pre-configured models:
  - Primary: `gemini-3.6-flash`
  - Fallback Lite: `gemini-3.5-flash-lite`
- Any request specifying an unknown or arbitrary model is rejected with `HTTP 400 Bad Request`.

### F. Multi-Tier Rate Limiting & Abuse Prevention
Tracked in MongoDB `studio_ai_counters` with TTL indexing:
1. **Per-Client Limit**: 20 calls / hour per client key.
   - Client key is derived from the **rightmost** `X-Forwarded-For` IP address (added by trusted reverse proxy), falling back to socket address. Spoofed leftmost entries cannot bypass this limit.
2. **Global Hourly Limit**: 100 calls / hour globally.
3. **Global Daily Limit**: 500 calls / day globally.
4. **Per-Turn Guard**: Maximum 6 calls per conversation ID per 10 minutes (prevents runaway autonomous tool loops).
5. Exceeded quotas return `HTTP 429 Too Many Requests` with a compliant `Retry-After` header.

### G. In-Memory Response Cache
- Keyed by `SHA256(model + system_prompt + json(input) + json(tools))`.
- 5-minute TTL (`STUDIO_AI_CACHE_TTL_SECONDS=300`).
- Replays cached streaming events without invoking Gemini, saving cost and quota.

### H. Zero Prompt Text Logging
- Logs never store or print message content, prompts, or user data.
- Only metadata is logged: `hash`, `length`, `model`, `duration_ms`, `status`.
- API keys never appear in any response, error payload, or server log.

---

## 3. Gemini-to-Adapter Event Mapping

AG Studio's adapter expects Server-Sent Events (`text/event-stream`) conforming to `AgAiEvent`:

```
Gemini Part / Stream Fragment                   AG Studio Adapter SSE Event
-----------------------------------------------------------------------------------------
1. First text part in chunk          -->        TEXT_MESSAGE_START (messageId, role='assistant')
2. Text delta in chunk               -->        TEXT_MESSAGE_CONTENT (messageId, delta)
3. End of text message               -->        TEXT_MESSAGE_END (messageId)
4. function_call identified          -->        TOOL_CALL_START (toolCallId, toolCallName)
5. function_call.args JSON string    -->        TOOL_CALL_ARGS (toolCallId, delta)
6. function_call completed           -->        TOOL_CALL_END (toolCallId)
7. Reasoning / thought start         -->        REASONING_MESSAGE_START (messageId, role='reasoning')
8. Reasoning delta                   -->        REASONING_MESSAGE_CONTENT (messageId, delta)
9. Reasoning completed               -->        REASONING_MESSAGE_END (messageId)
10. Stream completion                -->        data: [DONE]
```

---

## 4. Error Handling & Fallback

When a Gemini call experiences transient errors (HTTP 503, 500, 429, or timeout):
1. Retries once with the primary model (`gemini-3.6-flash`).
2. If the retry fails, attempts once with the fallback lite model (`gemini-3.5-flash-lite`).
3. If all attempts fail, returns a clean `HTTP 503 Service Unavailable` with `{"detail": "Studio AI service is temporarily unavailable.", "code": "gemini_unavailable"}` without exposing internal exception details or credentials.

