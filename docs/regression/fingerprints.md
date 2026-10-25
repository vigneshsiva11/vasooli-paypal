# Policy Rulebook Fingerprint Ledger

This document maintains the append-only ledger of ratified policy rulebook fingerprints across supported currency profiles.

## Ledger Rules
1. **Append-only**: Every release or ratified change to a currency profile appends a new row to this ledger with the release date, currency, fingerprint, and reason.
2. **Re-derivation guarantee**: Previous rulebooks must remain resolvable in `app.policy.rules.rulebook_registry()` so historical verdicts can always be re-derived against the exact parameters that authorized them.
3. **Contract enforcement**: Contract tests verify that the ledger's INR entry equals `rb1_aba19a5e5ee8124e` and the last USD row equals `current_fingerprint("USD")`.

## Released Fingerprints

| Currency | Fingerprint | Release Date | Status | Reason / Description |
|---|---|---|---|---|
| INR | `rb1_aba19a5e5ee8124e` | 2026-10-24 | Current | Initial ratified INR profile in force (fixed parameter baseline, 6 canonical checks). |
| USD | `rb1_c7c49bbd323ceed4` | 2026-10-24 | Superseded | Initial USD profile release (fixed 1 USD = 83.00 INR conversion, minimum ERV $1.00, auto-auth below $250, never-auto at/above $1000). |
| USD | `rb1_f1e6f9c8957920de` | 2026-10-24 | Superseded | USD profile with retry cap: max 3 retry-type executions per event, >=24h cooldown between retries, and additive PolicyReason `retry_cap_exceeded`. |
| USD | `rb1_8221b1b9069ffc80` | 2026-10-25 | Current | USD profile with retry cap and do-not-retry response codes ("9500", "5180"). |

## Registry Resolution
All historical and active rulebooks in this ledger are registered in `rulebook_registry()`:
- `rb1_aba19a5e5ee8124e`: Resolved via `current_inr_rulebook()`
- `rb1_c7c49bbd323ceed4`: Resolved via `superseded_usd_v1_rulebook()`
- `rb1_f1e6f9c8957920de`: Resolved via `superseded_usd_v2_rulebook()`
- `rb1_8221b1b9069ffc80`: Resolved via `current_usd_rulebook()`
- Additional superseded Stage 4 INR variants (`rb1_5c8af5310956c94e`, `rb1_f5a054a86ae4ee08`, `rb1_3ecc9dde2839f090`) and unsupported currency fallback (`rb1_039f96212f6d818b`) remain resolvable.
