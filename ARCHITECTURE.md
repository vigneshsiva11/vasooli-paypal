# Vasooli architecture

Diagrams render on GitHub. For the long-form design notes carried over from the first version, see [../ARCHITECTURE.md](../ARCHITECTURE.md).

## 1. The recovery pipeline

```mermaid
flowchart LR
  PP["PayPal sandbox<br/>Orders v2 · Vault · Invoicing"] -- "signed webhooks" --> WH["POST /webhooks/paypal<br/>verify-webhook-signature<br/>stored once per event id"]
  WH --> ING["Ingestion<br/>decline becomes revenue at risk"]
  ING --> DIA["Diagnosis<br/>rules first, Gemini only for unmapped codes"]
  DIA --> DEC["Decision<br/>expected recovery value<br/>excludes unsafe interventions"]
  DEC --> POL{"Policy<br/>active rulebook<br/>six checks"}
  POL -- "authorized" --> EXE["Execution port<br/>PayPal adapter"]
  POL -- "blocked / manual review" --> LED
  EXE -- "vaulted retry" --> PP
  PP -- "PAYMENT.CAPTURE.COMPLETED" --> REC["Reconciler<br/>PAYPAL vs EXTERNAL<br/>partial vs full"]
  REC --> LED[("Ledger and metrics<br/>verified vs asserted")]
  ORC["Orchestrator<br/>+ simulated clock"] -. drives the stages .-> DIA
```

**Loop guard:** a `PAYMENT.CAPTURE.DECLINED` whose `custom_id` starts with `ev:` is a failed recovery attempt. It is attached to the original event and never creates new revenue at risk.

## 2. Trust boundaries

```mermaid
flowchart TB
  subgraph Outside["Outside the system"]
    V["Visitors and judges"]
    A["External AI agents"]
  end
  subgraph Static["Render static site"]
    UI["React app<br/>Mission Control · Rules · Ledger · Studio · Try to break it"]
  end
  subgraph API["Render web service (FastAPI)"]
    MW["Guard middleware<br/>exact-path allowlist · admin token · rate limits"]
    GW["Agent gateway<br/>8 allowlisted tools · audit log"]
    RUL["Rules compiler<br/>Gemini proposes · validator decides"]
    POL["Policy layer<br/>deterministic · hard caps"]
  end
  DB[("MongoDB Atlas")]
  PPAL["PayPal sandbox"]
  GEM["Gemini API (key server-side only)"]
  V --> UI --> MW
  A -- "bearer token, MCP" --> GW
  MW --> POL
  GW --> POL
  RUL --> POL
  POL --> DB
  POL -- "authorized actions only" --> PPAL
  RUL -. "proposal only" .-> GEM
```

## 3. Plain-English rules: who decides what

```mermaid
sequenceDiagram
  participant M as Merchant
  participant P as /rules/preview
  participant G as Gemini
  participant V as Validator
  participant A as /rules/apply (admin)
  M->>P: rules in plain English (500 chars max)
  P->>G: structured-JSON request, text fenced as untrusted data
  G-->>P: proposed values and source phrases
  P->>V: caps, invariants, value must match its source phrase
  V-->>M: diff and new fingerprint, or a rejection with reasons
  M->>A: preview id and parent fingerprint, with the admin token
  A-->>M: new rulebook version applied (409 if the parent changed)
```

The model never sees the policy engine and never applies anything. Values that break a cap are rejected with an explanation, never silently clamped.

## 4. Hard caps on merchant rules (USD profile)

| Parameter | Allowed range |
|---|---|
| Contacts per event | 1 to 5 |
| Cooldown between contacts | 6 to 168 hours |
| Auto-authorize below | $0 to $500 |
| Never auto at or above | $50 to $5,000 (must exceed the auto limit) |
| Minimum expected recovery value | $0 to $50 |
| Retries per event | 1 to 5 |
| Retry cooldown | 12 to 168 hours |

Immutable: the opt-out check, the list of policy checks, the reason precedence, the INR profile.

## 5. Verified versus asserted money

| | `gateway_verified` | `merchant_asserted` |
|---|---|---|
| Evidence | A signed PayPal webhook whose invoice or capture shows a PayPal-collected payment | A payment the merchant recorded outside PayPal |
| Counted as "Verified by PayPal webhook" | Yes | No |
| Mixed in one total | Never | Never |

Cumulative payments never double count: a partial then a full payment reports the invoice's cumulative paid amount, and out-of-order webhooks never move an event backwards.

## 6. The agent gateway

One shared tool core serves two adapters: a public REST page (`POST /agent/attempt`) and an MCP server (`/mcp`, bearer token).

| Tool | Type | Behavior |
|---|---|---|
| `list_at_risk_events`, `get_event_audit`, `get_active_rules` | read | Sanitized data only |
| `propose_recovery`, `propose_contact` | dry run | Real diagnosis, decision, and policy; nothing is written |
| `execute_recovery` | action | Needs an authorized verdict; sandbox only; refuses attack-range events; one per event per 10 minutes |
| `list_attack_scenarios`, `run_attack_scenario` | demo | Seven scripted attacks evaluated in memory; only audit rows are written |

Not offered: applying or reverting rules, reset, clock control, the orchestrator, raw PayPal calls. Unknown tools are refused and audited.

## 7. Collections

`events`, `diagnoses`, `decisions`, `policy_verdicts`, `executions`, `verifications`, `charge_attempts`, `customer_vault`, `orchestrator_runs`, `demo_runs`, `paypal_events`, `rulebook_versions`, `agent_audit`, plus rate-limit counters with TTL indexes. Stored documents and their read models are tested for symmetry, so a field written by the code can never be rejected on read.
