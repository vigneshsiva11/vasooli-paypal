# Context-Matic / APIMatic Tool Call Evidence

This document records the exact `context-matic` tool calls made in relation to PayPal SDK models and endpoints, written honestly without claiming more than was actually executed.

---

## 1. Earlier Sessions Audit

* **Sessions Inspected**:
  * `f9c86a65-4b39-4ac3-8051-21004b0c773f` (PayPal Integration and Decline Handling)
  * `715ad1c6-e9e6-4ef2-b232-019ed9749252` (PayPal Invoicing API Payment Parameters)
* **Findings**:
  * **Zero (0) `context-matic` tool calls were executed in those two sessions.**
  * Those sessions performed web searches via `search_web` and inspected workspace files, but did not call `context-matic` MCP tools.

---

## 2. Tool Calls Executed in Session `ed23671a-99fd-412d-afba-8f31b57adb61`

The following tool calls to the `context-matic` MCP server were executed in session `ed23671a-99fd-412d-afba-8f31b57adb61` (PayPal Events Mapping and Regression Plan):

### A. `fetch_api`
* **Tool Name**: `context-matic.fetch_api`
* **Arguments**: `{"key": "paypal", "language": "python"}`
* **Returned**:
  ```json
  [{"key":"paypal","name":"PayPal Server SDK","description":"The PayPal Server SDK provides access to PayPal REST APIs across controllers including Orders, Payments, US-only Vault, Transaction Search, and Subscriptions."}]
  ```
* **Informed**: Confirmed that PayPal Server SDK context is registered in ContextMatic and resolved the key `"paypal"` for subsequent queries. Informed `docs/plan/paypal-events-map.md`.

---

### B. `model_search` (`ProcessorResponse`)
* **Tool Name**: `context-matic.model_search`
* **Arguments**: `{"key": "paypal", "language": "python", "query": "ProcessorResponse"}`
* **Returned**:
  * Exact match for `ProcessorResponse`:
    * `avs_code (AvsCode)`: Address verification code for Visa, Discover, Mastercard, or American Express transactions.
    * `cvv_code (CvvCode)`: Card verification value code for Visa, Discover, Mastercard, or American Express.
    * `response_code (ProcessorResponseCode)`: Processor response code for the non-PayPal payment processor errors.
    * `payment_advice_code (PaymentAdviceCode)`: Declined payment transactions might have payment advice codes returned by card networks like Visa and Mastercard.
  * Similar models: `PaymentsCapture`, `CardVerificationProcessorResponse`, `OrdersCapture`.
* **Informed**: Verified payload structure and field names (`response_code`, `payment_advice_code`, `avs_code`, `cvv_code`) for `docs/plan/paypal-events-map.md`, `app/webhooks/paypal_reconciler.py`, and `app/policy/engine.py`.

---

### C. `model_search` (`ProcessorResponseCode`)
* **Tool Name**: `context-matic.model_search`
* **Arguments**: `{"key": "paypal", "language": "python", "query": "ProcessorResponseCode"}`
* **Returned**:
  * Enum definition for `ProcessorResponseCode` with 85+ values including `Response_0000`, `Response_00N7`, `Response_0500`, `Response_5120`, `Response_9520`, etc.
* **Informed**: Verified that `00N7`, `0500`, `5120` are valid PayPal Server SDK processor response codes. Informed `docs/plan/paypal-events-map.md` and `app/diagnosis/rules.py`.

---

### D. `model_search` (`PaymentAdviceCode`)
* **Tool Name**: `context-matic.model_search`
* **Arguments**: `{"key": "paypal", "language": "python", "query": "PaymentAdviceCode"}`
* **Returned**:
  * Enum definition for `PaymentAdviceCode` with values:
    * `PaymentAdvice_01`
    * `PaymentAdvice_02`
    * `PaymentAdvice_03`
    * `PaymentAdvice_04`
    * `PaymentAdvice_21`
    * `PaymentAdvice_22`
    * `PaymentAdvice_24`
    * `PaymentAdvice_25`
    * `PaymentAdvice_26`
    * `PaymentAdvice_27`
    * `PaymentAdvice_28`
    * `PaymentAdvice_29`
    * `PaymentAdvice_30`
    * `PaymentAdvice_40`
    * `PaymentAdvice_43`
* **Informed**: Verified that `PaymentAdviceCode` in the PayPal Server SDK contains exact matching values for Mastercard/Visa Merchant Advice Codes `01`-`43`. Informed `docs/plan/paypal-events-map.md` and `app/policy/rules.py`.

---

### E. `ask` (ProcessorResponse details)
* **Tool Name**: `context-matic.ask`
* **Arguments**: `{"key": "paypal", "language": "python", "query": "What are the descriptions and meanings of response_code 5120, 0500, 00N7 and PaymentAdviceCode in ProcessorResponse?"}`
* **Returned**:
  * Confirmed that `Response_5120`, `Response_0500`, and `Response_00N7` are valid enum values, but noted that the SDK models do not provide meaning descriptions for individual values within their docstrings.
* **Informed**: Highlighted that exact meaning descriptions must be sourced and quoted directly from fetched official documentation pages (`developer.paypal.com` and Braintree documentation).

---

### F. `ask` (TypeScript Enum Docstrings)
* **Tool Name**: `context-matic.ask`
* **Arguments**: `{"key": "paypal", "language": "typescript", "query": "What are the enum values and docstrings for ProcessorResponseCode?"}`
* **Returned**:
  * Docstring: `"Processor response code for the non-PayPal payment processor errors."`
  * Complete listing of TypeScript enum members.
* **Informed**: `docs/plan/paypal-events-map.md`.

---

### G. `endpoint_search` (`capture`)
* **Tool Name**: `context-matic.endpoint_search`
* **Arguments**: `{"key": "paypal", "language": "python", "query": "capture"}`
* **Returned**:
  * Endpoints for `OrdersCaptureController` (`/v2/checkout/orders/{id}/capture`) and `PaymentsController` (`/v2/payments/captures/{capture_id}`).
* **Informed**: Verified capture endpoint paths for `docs/plan/paypal-events-map.md`.

---

## 3. Tool Calls Executed in Current Session (`268e7741-44b9-43f3-8c35-52af48709926`)

The following tool calls were executed in the current session:

### A. `fetch_api`
* **Tool Name**: `context-matic.fetch_api`
* **Arguments**: `{"key": "paypal", "language": "python"}`
* **Returned**: Verified active registration of PayPal Server SDK metadata.

### B. `endpoint_search` (`capture`)
* **Tool Name**: `context-matic.endpoint_search`
* **Arguments**: `{"key": "paypal", "language": "python", "query": "capture"}`
* **Returned**: Detailed SDK code sample and parameters for `CaptureOrder` via `orders_controller.capture_order({'id': '...', 'prefer': 'return=minimal'})`.
* **Informed**: Live probe structure for Orders v2 card capture in Part 1.
