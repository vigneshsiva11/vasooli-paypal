# PayPal Live Sandbox Probe — Part 1 Findings

**Date**: 2026-10-25  
**Environment**: PayPal Sandbox (`api-m.sandbox.paypal.com`)  
**API Call Budget**: 4 calls made out of 8 maximum allowed. Zero secrets printed or stored.  
**Live Demo Database**: `vasooli_demo` (read-only polling of `paypal_events`).  
**Awake Health Check**: Live service health URL not configured in environment; skipped.

---

## 1. Executive Summary & Verification Matrix

| Order | Purpose | Card / Auth | Custom ID | HTTP Result | Order Status | Capture Status | Key Output / IDs |
|---|---|---|---|---|---|---|---|
| **Order A** | Standard payment + Vault on success | Test Visa (`4111...1111`) | `cust:probe1` | `201 Created` | `COMPLETED` | `COMPLETED` | Order `8YF00147EB635281R`<br>Capture `28C544276S4714412`<br>Vault Token `3w783067vb799012h` |
| **Order B** | Decline simulation trigger | `CCREJECT-IF` (not vaulted) | `cust:probe1` | `201 Created` | `COMPLETED` | `DECLINED` | Order `2CG62440G8279605F`<br>Capture `5LG46948JJ571544V`<br>Code: `5120` (`INSUFFICIENT_FUNDS`) |
| **Order C** | Vaulted retry charge (no card details) | Token `3w783067vb799012h` | `ev:probe_evt` | `201 Created` | `COMPLETED` | `COMPLETED` | Order `29935697HU833963D`<br>Capture `64P52986GA335712Y` |

---

## 2. Answers to Specific Spike Questions

### Q1: Does `custom_id` appear in the DECLINED and COMPLETED webhook payloads?
**YES.**
In all capture webhooks, `custom_id` appears directly at the root of the `resource` object:
* In `PAYMENT.CAPTURE.DECLINED` (Order B): `resource.custom_id = "cust:probe1"`.
* In `PAYMENT.CAPTURE.COMPLETED` (Order A): `resource.custom_id = "cust:probe1"`.
* In `PAYMENT.CAPTURE.COMPLETED` (Order C): `resource.custom_id = "ev:probe_evt"`.

This confirms Vasooli's reverse-lookup strategy:
1. `ev:<event_id>` directly identifies the recovery event for an automated or manual retry.
2. `cust:<customer_ref>` correlates customer onboarding / original payment attempts to customer accounts.

### Q2: Where do Order ID, Customer ID, and Payment Advice Code appear?
* **Order ID**:
  * In `PAYMENT.CAPTURE.COMPLETED` and `PAYMENT.CAPTURE.DECLINED`:
    1. Primary location: `resource.supplementary_data.related_ids.order_id`.
    2. Up-link: `resource.links` entry with `"rel": "up"`, containing `https://api.sandbox.paypal.com/v2/checkout/orders/<order_id>`.
  * In `VAULT.PAYMENT-TOKEN.CREATED`:
    * Appears under `resource.metadata.order_id`.
* **Customer ID**:
  * In `VAULT.PAYMENT-TOKEN.CREATED`:
    * Appears under `resource.customer.id` (e.g. `"bUPhnSIexi"`).
  * In `PAYMENT.CAPTURE.*`:
    * The capture resource itself **does not** contain a direct `customer_id` field; customer correlation occurs via `resource.custom_id` (`cust:<customer_ref>`) or by dereferencing `supplementary_data.related_ids.order_id` in Vasooli's charge attempts store.
* **Payment Advice Code (`payment_advice_code`)**:
  * When present in card network responses, it is mapped under `resource.processor_response.payment_advice_code`.
  * In sandbox declines (tested with trigger `CCREJECT-IF`), `resource.processor_response` returns:
    ```json
    {
      "avs_code": "G",
      "cvv_code": "P",
      "response_code": "5120"
    }
    ```
    `payment_advice_code` is **ABSENT** in standard sandbox declines for `5120`.

### Q3: Does the vault event carry the Order ID?
**YES.**
The webhook event `VAULT.PAYMENT-TOKEN.CREATED` contains the order ID in `resource.metadata.order_id`:
```json
"resource": {
  "id": "3w783067vb799012h",
  "customer": {
    "id": "bUPhnSIexi"
  },
  "metadata": {
    "order_id": "8YF00147EB635281R"
  },
  ...
}
```
This enables deterministic correlation between vault tokens created on-success and the original onboarding order.

### Q4: What does the Orders API return on a decline?
When creating and capturing an order inline via `POST /v2/checkout/orders` with `intent: "CAPTURE"` and a declining payment source (such as `name: "CCREJECT-IF"`):
* **HTTP Status Code**: `201 Created` (not a 4xx error).
* **Overall Order Status**: `"COMPLETED"`.
* **Inline Capture Details**:
  Inside `purchase_units[0].payments.captures[0]`:
  * `status`: `"DECLINED"`
  * `processor_response`:
    ```json
    {
      "avs_code": "G",
      "cvv_code": "P",
      "response_code": "5120"
    }
    ```
  * `seller_protection.status`: `"NOT_ELIGIBLE"`
The transaction is recorded as an order, but its internal capture is marked `DECLINED`. This mirrors the webhook lifecycle where `PAYMENT.CAPTURE.DECLINED` is emitted.

---

## 3. Exported Live Fixtures & SHA-256 Provenance

All 4 events received by `vasooli_demo.paypal_events` were exported as byte-identical fixtures under `tests/fixtures/paypal/live/`:

| Fixture Name | Event Type | PayPal Event ID | SHA-256 Checksum | Provenance |
|---|---|---|---|---|
| `vault_payment_token_created.json` | `VAULT.PAYMENT-TOKEN.CREATED` | `WH-1GS16093L62323701-3L2850250W433933F` | `08ebbf52a9adbf560c48f335f226467f6e548e8fe34e0ab0a0054415fd97ed96` | captured |
| `payment_capture_declined_order_b.json` | `PAYMENT.CAPTURE.DECLINED` | `WH-4AF58932CR119514D-1KM26931PG851734B` | `5c1b3b0e8a96236829bad8e6d1fba8b90d59f4c792ec1b39116c2966c5cf7ebd` | captured |
| `payment_capture_completed_order_a.json` | `PAYMENT.CAPTURE.COMPLETED` | `WH-6BJ84679FR1049458-6RT28794KN431740B` | `520dea1e1a820b96b356377acb9c4e1ad5aba602a55a4abf8af21a74a058e03d` | captured |
| `payment_capture_completed_order_c.json` | `PAYMENT.CAPTURE.COMPLETED` | `WH-7LY56442MB8918050-91623769MG2801454` | `6c0f2f2f24590e95569db7841f6b2972ed1d9319fc3a1b71d4f452afc886288c` | captured |
