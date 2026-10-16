# What changed since the Razorpay Buildathon version

## 2026-10-15: PayPal sandbox foundation
- Added `app/gateways/paypal/`: OAuth client with token cache, Invoicing v2 (partial payments, external-payment recording), Orders v2 (card capture, vaulting, server-side vaulted retry).
- Added `POST /webhooks/paypal`: verified through PayPal's verify-webhook-signature API, fails closed, stores each event once.
- Added standalone sandbox spike scripts and findings (`docs/spike/`).
- Razorpay code left untouched.



Add-Content WHAT_CHANGED.md @"

- Fixed a double-total bug in invoice creation (a custom breakdown line was added on top of the items) and added a guard that rejects it.
- Verified on the sandbox: signed INVOICING.INVOICE.PAID webhooks delivered to the receiver through a public tunnel; events stored under a unique index on the event id.
- Observed that PayPal-collected payments (type PAYPAL) and recorded payments (type EXTERNAL) can be told apart from the invoice payload.
"@