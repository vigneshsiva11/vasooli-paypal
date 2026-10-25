@'
# What changed since the Razorpay Buildathon version

Baseline: the original Vasooli repository at tag `razorpay-buildathon-submission` (commit 29dd011), a Razorpay-only, INR-only build. This repository starts from that code. Everything below was added or changed afterwards. The Razorpay code path was kept and is protected by regression checks.

## 2026-10-15: PayPal sandbox foundation
- Added `app/gateways/paypal/`: OAuth client with token cache, Invoicing v2 (partial payments, external-payment recording), Orders v2 (card capture, vaulting, server-side vaulted retry).
- Added `POST /webhooks/paypal`: verified through PayPal's verify-webhook-signature API, fails closed, stores each event once (unique index on the event id).
- Added standalone sandbox spike scripts and findings (`docs/spike/`).
- Fixed a double-total bug in invoice creation (a custom breakdown line was added on top of the items) and added a guard that rejects it.
- First complete sandbox recovery: a $100 invoice (INV2-LSEJ-2LLG-D59L-2QNU) was paid as $10 then $90 by a sandbox buyer; both payments were classified as PayPal-collected, status PAID, due 0.00.
- Observed on real payloads: PayPal-collected payments (type PAYPAL) and recorded payments (type EXTERNAL) can be told apart from the invoice itself.

## 2026-10-19 to 2026-10-22: Gateway-agnostic core and PayPal verification
- Additive models: `gateway`, `gateway_ref`, `gateway_url` on events and executions; an `assertion` field (`gateway_verified` or `merchant_asserted`) on verification records, defaulted for old records from their source.
- Currency-keyed rulebook: a USD profile with its own fingerprint; the INR fingerprint is unchanged (`rb1_aba19a5e5ee8124e`); an unknown currency fails closed to manual review.
- Execution port with a Razorpay adapter (behavior identical) and a PayPal adapter (invoice links, vaulted-card retries).
- PayPal reconciler: classifies PayPal-collected versus externally recorded payments, handles partial versus full payment from the cumulative paid amount, never double counts, and is safe against out-of-order delivery. Razorpay verifier checks added so nothing weakened.
- Metrics are scoped per currency (`currency` parameter, `/metrics/currencies`); no endpoint sums across currencies.
- Fixtures taken from real PayPal events are byte-identical and verified by SHA-256; anything edited is labelled "derived".
- Added 104 offline contract checks and 15 integration checks against a real MongoDB (counts at the time of writing).

## 2026-10-23: Live deployment
- Deployed to Render with a permanent webhook URL; PayPal webhooks now reach the hosted service.
- Public-demo guard: with `PUBLIC_DEMO=true`, every write needs an admin token (constant-time compare, fails closed); PayPal webhooks stay protected by signature verification.
- Fixed startup parsing of `CORS_ORIGINS` from the environment (comma-separated or JSON).

## 2026-10-24: Rail 1 (decline to recovery), steps 5a and 5b
- Decline ingestion: a PayPal declined capture becomes a revenue-at-risk event; customer matched through recorded charge attempts; unmatched declines are stored and reprocessed later.
- Vault store for customer payment tokens (last four digits only).
- Diagnosis from real PayPal processor codes (5120, 0500, 00N7); other codes go to Gemini. New root cause `card_verification_failed` (the CVV check failed, not an expired card).
- Idempotent orchestrator (diagnose, decide, policy, execute), with an injectable simulated clock. Runs under simulated time are labelled.
- Retry rules: retries are excluded when there is no vault token or the card network advises not to retry.
- Loop guard: a declined recovery attempt is recorded against the original event and never creates new revenue at risk.
- USD retry cap (3 retries, 24 hours apart) and a fingerprint ledger (`docs/regression/fingerprints.md`) so older verdicts stay re-derivable.
- APIMatic's ContextMatic MCP server was used for PayPal API lookups (`docs/apimatic.md`).

## Honesty notes
- Everything runs against the PayPal sandbox; no real money moves.
- Declines are produced with PayPal's sandbox triggers; the simulated clock is labelled wherever it is used.
- Four Razorpay-dependent gate scripts (s5, s6, s6b, s8) are not run in this repository to protect the Razorpay test account's quota; the Razorpay path is covered by offline characterization checks instead. `s7_verify` reports two expected failures that depend on those gates.
- Some gate scripts were edited to support admin tokens and extra fixtures (`s3`, `s4`, `s7`, `s9`); each edit is recorded in `docs/regression/baseline.md`.
'@ | Set-Content WHAT_CHANGED.md -Encoding utf8
## 2026-10-25: Step 5c (scenario runner)
- Server-side scenario runner (insufficient-funds): onboard with a vaulted card, a sandbox-simulated decline, orchestrated retry on the vaulted card, verified by a signed webhook; every step is labelled with its source.
- Demo reset restricted to an allowlist and the sandbox; declined recovery attempts are typed outcomes, never exceptions.
- Live sandbox probe confirmed custom_id appears in decline and completion webhooks; fixtures saved with hashes.

## 2026-10-26: First live run and production guard
- Hosted run completed on the sandbox: card onboarding vaulted, a simulated decline (5120), an orchestrated retry on the vaulted card, and a signed PAYMENT.CAPTURE.COMPLETED webhook marking the event recovered. Evidence: docs/evidence/live-run-2026-10-25.json.
- Production startup now refuses to run without the demo guard and an admin token; privileged routes check the token independently.
