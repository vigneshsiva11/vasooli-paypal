# PayPal Webhook Events & Payload Field Mapping

This document provides a field-by-field payload mapping across all captured PayPal sandbox webhook fixtures in `tests/fixtures/paypal/`, official PayPal processor response code definitions, and the convention for tracking customer and event references.

---

## 1. Conventions for `custom_id` Tracking

PayPal Orders v2 supports setting `purchase_units[0].custom_id` (up to 127 characters). Vasooli uses the following conventions:

1. **`cust:<customer_ref>`**: Set on initial and recurring charges to tie the charge attempt to a specific customer reference.
2. **`ev:<event_id>`**: Set on recovery orders or invoices created to resolve a specific revenue-at-risk event.

---

## 2. Payload Field Paths by Fixture

### A. `payment_capture_declined.json` (`PAYMENT.CAPTURE.DECLINED`)
* **order id**: `resource.supplementary_data.related_ids.order_id` (`"6PW329308U6154049"`)
* **capture id**: `resource.id` (`"59185726VV0844043"`)
* **custom_id**: **DOES NOT EXIST** (`resource.custom_id` is missing).
  * *Fallback*: Resolve `customer_ref` from the `charge_attempts` collection by `order_id`. If unresolved at webhook arrival time, store the event as `unmatched` in `paypal_events` and return HTTP 200; `reprocess_unmatched_declines()` retries resolution once the charge attempt is recorded.
* **invoice_id**: **DOES NOT EXIST**
* **PayPal customer id or merchant customer id**: **DOES NOT EXIST** (`resource.payee.merchant_id` is the merchant account, not the customer).
  * *Fallback*: Resolved via `charge_attempts` by `order_id`.
* **processor_response**:
  * `response_code`: `resource.processor_response.response_code` (`"5120"`)
  * `payment_advice_code`: **DOES NOT EXIST** in this payload.
  * `avs_code`: `resource.processor_response.avs_code` (`"G"`)
  * `cvv_code`: `resource.processor_response.cvv_code` (`"P"`)
* **status_details.reason**: **DOES NOT EXIST** (`resource.status_details` is not present; top-level status is `resource.status = "DECLINED"`).
* **amount**: `resource.amount.value` (`"10.00"`)
* **currency**: `resource.amount.currency_code` (`"USD"`)
* **vault id**: **DOES NOT EXIST**
* **card brand and last digits**: `resource.network_transaction_reference.network` (`"VISA"`). Last digits **DO NOT EXIST** in capture decline.

---

### B. `payment_capture_completed.json` (`PAYMENT.CAPTURE.COMPLETED`)
* **order id**: `resource.supplementary_data.related_ids.order_id` (`"46969136VC639191U"`)
* **capture id**: `resource.id` (`"54J97366HE2238715"`)
* **custom_id**: `resource.custom_id` (`"ev:evt_paypal_order_capture_01"`)
* **invoice_id**: **DOES NOT EXIST**
* **PayPal customer id or merchant customer id**: **DOES NOT EXIST**
* **processor_response**:
  * `response_code`: `resource.processor_response.response_code` (`"0000"`)
  * `payment_advice_code`: **DOES NOT EXIST**
  * `avs_code`: `resource.processor_response.avs_code` (`"A"`)
  * `cvv_code`: `resource.processor_response.cvv_code` (`"M"`)
* **status_details.reason**: **DOES NOT EXIST**
* **amount**: `resource.amount.value` (`"25.00"`)
* **currency**: `resource.amount.currency_code` (`"USD"`)
* **vault id**: **DOES NOT EXIST**
* **card brand and last digits**: `resource.network_transaction_reference.network` (`"VISA"`). Last digits **DO NOT EXIST**.

---

### C. `vault_payment_token_created.json` (`VAULT.PAYMENT-TOKEN.CREATED`)
* **order id**: `resource.metadata.order_id` (`"46969136VC639191U"`)
* **capture id**: **DOES NOT EXIST**
* **custom_id**: **DOES NOT EXIST**
* **invoice_id**: **DOES NOT EXIST**
* **PayPal customer id or merchant customer id**: `resource.customer.id` (`"nuwAZXaKZY"`)
  * *Resolution*: Best identifier is merchant-supplied customer id in `resource.customer.id` if set; otherwise lookup `charge_attempts` by `resource.metadata.order_id`.
* **processor_response**:
  * `response_code`: `resource.payment_source.card.verification.processor_response.response_code` (`"0000"`)
  * `payment_advice_code`: **DOES NOT EXIST**
  * `avs_code`: `resource.payment_source.card.verification.processor_response.avs_code` (`"A"`)
  * `cvv_code`: `resource.payment_source.card.verification.processor_response.cvv_code` (`"M"`)
* **status_details.reason**: **DOES NOT EXIST** (`resource.payment_source.card.verification_status` is `"VERIFIED"`).
* **amount**: `resource.payment_source.card.verification.amount.value` (`"0.00"`)
* **currency**: `resource.payment_source.card.verification.amount.currency_code` (`"USD"`)
* **vault id**: `resource.id` (`"4mr90404j6010301k"`)
* **card brand and last digits**:
  * brand: `resource.payment_source.card.brand` (`"VISA"`)
  * last digits: `resource.payment_source.card.last_digits` (`"1111"`) (full card numbers are never returned and never stored).

---

### D. `invoicing_invoice_created.json` (`INVOICING.INVOICE.CREATED`) & `invoicing_invoice_sent.json` (`INVOICING.INVOICE.SENT`)
* **order id**: **DOES NOT EXIST**
* **capture id**: **DOES NOT EXIST**
* **custom_id**: **DOES NOT EXIST**
* **invoice_id**: `resource.invoice.id` (`"INV2-LSEJ-2LLG-D59L-2QNU"`)
* **PayPal customer id or merchant customer id**: Recipient email is at `resource.invoice.primary_recipients[0].billing_info.email_address` (`"sb-38n4q53381803@personal.example.com"`). No customer id is provided.
* **processor_response**: **DOES NOT EXIST**
* **status_details.reason**: **DOES NOT EXIST**
* **amount**: `resource.invoice.amount.value` (`"100.00"`)
* **currency**: `resource.invoice.amount.currency_code` (`"USD"`)
* **vault id**: **DOES NOT EXIST**
* **card brand and last digits**: **DOES NOT EXIST**

---

### E. `invoicing_invoice_paid_full.json`, `invoicing_invoice_paid_partial.json`, `invoicing_invoice_paid_external.json` (`INVOICING.INVOICE.PAID`)
* **order id**: **DOES NOT EXIST**
* **capture id**: **DOES NOT EXIST**
* **custom_id**: **DOES NOT EXIST**
* **invoice_id**: `resource.invoice.id` (`"INV2-LSEJ-2LLG-D59L-2QNU"`)
* **PayPal customer id or merchant customer id**: **DOES NOT EXIST** (recipient email only).
* **processor_response**: **DOES NOT EXIST**
* **status_details.reason**: **DOES NOT EXIST**
* **amount**: Total invoice amount at `resource.invoice.amount.value` (`"100.00"`). Paid amount at `resource.invoice.payments.paid_amount.value` (`"100.00"` in full, `"10.00"` in partial).
* **currency**: `resource.invoice.amount.currency_code` (`"USD"`)
* **vault id**: **DOES NOT EXIST**
* **card brand and last digits**: **DOES NOT EXIST**

---

### 3. Official PayPal Processor Response Codes & Documentation Evidence

### Sources Actually Fetched & Confirmed

All mappings in this section cite **ONLY** pages and tool calls actually fetched and confirmed in this environment:

1. **`https://developer.paypal.com/api/rest/sandbox/card-testing/` & `https://developer.paypal.com/tools/sandbox/card-testing`**
   * *Fetched Content*: Official PayPal card testing response simulator table. Defines test triggers and processor response codes:
     * `CCREJECT-REFUSED` -> `0500` (`DO_NOT_HONOR`)
     * `CCREJECT-IF` -> `5120` (`INSUFFICIENT_FUNDS`)
     * `CCREJECT-CVV_F` -> `00N7` (`CVV2_FAILURE_POSSIBLE_RETRY_WITH_CVV`)
     * `CCREJECT-SF` -> `9500` (`SUSPECTED_FRAUD. Try using another card. Do not retry the same card.`)
     * `CCREJECT-IRC` -> `5180` (`INVALID_OR_RESTRICTED_CARD. Try using another card. Do not retry the same card.`)
     * `CCREJECT-LS` -> `9520` (`LOST_OR_STOLEN. Try using another card. Do not retry the same card.`)
2. **`https://www.rubydoc.info/gems/paypal-server-sdk/PaypalServerSdk/ProcessorResponseCode`**
   * *Fetched Content*: Complete constant enumeration of `PaypalServerSdk::ProcessorResponseCode` (`RESPONSE_0000 = '0000'`, `RESPONSE_00N7 = '00N7'`, `RESPONSE_0500 = '0500'`, `RESPONSE_5120 = '5120'`, `RESPONSE_5180 = '5180'`, `RESPONSE_9500 = '9500'`, etc.).
3. **`context-matic` MCP Tool Calls**
   * Full details and session attribution documented in [docs/apimatic.md](file:///d:/vasooli-paypal/docs/apimatic.md).

---

### Verified Processor Response Codes (Verbatim Quoted Text)

1. **`5120` — `INSUFFICIENT_FUNDS`**
   * **Source**: `https://developer.paypal.com/tools/sandbox/card-testing`
   * **Trigger**: `CCREJECT-IF`
   * **Quoted Code Description**: `"INSUFFICIENT_FUNDS"`
   * **Vasooli Root Cause Mapping**: `insufficient_funds`
   * **Action**: Prompt customer to fund account or choose another payment method.

2. **`0500` — `DO_NOT_HONOR`**
   * **Source**: `https://developer.paypal.com/tools/sandbox/card-testing`
   * **Trigger**: `CCREJECT-REFUSED`
   * **Quoted Code Description**: `"DO_NOT_HONOR"`
   * **Vasooli Root Cause Mapping**: `issuer_declined`
   * **Action**: Advise customer to contact their card-issuing bank or use a different card.

3. **`00N7` — `CVV2_FAILURE_POSSIBLE_RETRY_WITH_CVV`**
   * **Source**: `https://developer.paypal.com/tools/sandbox/card-testing`
   * **Trigger**: `CCREJECT-CVV_F`
   * **Quoted Code Description**: `"CVV2_FAILURE_POSSIBLE_RETRY_WITH_CVV"`
   * **Vasooli Root Cause Mapping**: `card_verification_failed` (additive member of `PaymentRootCause`, mapped to `payment_method_update_link`).
   * **Action**: Prompt customer to update card details and provide valid CVV.

4. **Do-Not-Retry Response Codes: `9500` & `5180`**
   * **Source**: `https://developer.paypal.com/tools/sandbox/card-testing`
   * **`9500` (`CCREJECT-SF`)**: `"SUSPECTED_FRAUD. Try using another card. Do not retry the same card."`
   * **`5180` (`CCREJECT-IRC`)**: `"INVALID_OR_RESTRICTED_CARD. Try using another card. Do not retry the same card."`
   * **Vasooli Action**: Both codes explicitly instruct "Do not retry the same card." Ratified as `DO_NOT_RETRY_RESPONSE_CODES = frozenset({"9500", "5180"})`. Enforced as a retry exclusion in decision and defence-in-depth policy check (`retry_not_advised`) for USD profile.

5. **All other response codes**
   * Anything without quoted text from a fetched source is marked **`UNVERIFIED`** and treated as unmapped (`raw_failure_reason="paypal_<code|unknown>"`), falling through to fallback/Gemini classification.

---

### Payment Advice Codes (Status: UNVERIFIED)

* **Source**: Mastercard / Visa Merchant Advice Codes via PayPal `processor_response.payment_advice_code`.
* **Verification Status**: Direct page text for individual advice codes was **not** fetched as raw markdown in this environment. Therefore, all individual payment advice code meaning strings (`01`, `02`, `03`, `21`, `24`-`30`, `40`, `41`, `43`) are formally designated **`UNVERIFIED`**.
* **Policy Handling**:
  * Advice code `03` remains in `DO_NOT_RETRY_ADVICE_CODES = frozenset({"03"})` for backward compatibility.
  * Primary verified do-not-retry exclusion is anchored in `DO_NOT_RETRY_RESPONSE_CODES = frozenset({"9500", "5180"})` from the verified card testing documentation.


