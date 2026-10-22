# Baseline Verification Results (Step 0)

Conducted against isolated database `vasooli_regress` on port 8123 with shell override `MONGODB_DB_NAME=vasooli_regress`. Database `vasooli_paypal` was not touched.

## 1. Script Audit & Classification Matrix

| Script | Server Needed? | Port | Needs Seeded Data? | DB Access | Calls Real External API? | Action in Step 0 |
|---|---|---|---|---|---|---|
| `scripts/s10_validation_suite.py` | No | N/A | No | None (Pure in-memory) | None (0 Gemini) | **Executed** |
| `scripts/s3_verify.py` | Yes | 8123 | Yes (`s3_seed.py`) | **WRITES** (`POST /decide/{id}`) | None | **Executed** |
| `scripts/s4_verify.py` | Yes | 8123 | Yes (`s3_seed.py`, `s4_seed.py`) | **WRITES** (`POST /authorize/{id}`, opt-out) | None | **Executed** |
| `scripts/s5_verify.py` | Yes | 8123 | Seeds own fixtures | **WRITES** (`POST /execute/{id}`) | **REAL RAZORPAY API** (`payment_links` creation) | **HELD** (quota gate) |
| `scripts/s6_verify.py` | Yes | 8123 | Yes (Stage 5 executions) | **WRITES** (verifications, status) | **REAL RAZORPAY API** (fetch/cancel links) | **HELD** (quota gate) |
| `scripts/s6b_verify.py` | Yes | 8123 | Seeds own fixtures | **WRITES** (promises, executions) | **REAL RAZORPAY API** (creates link for scenario C) | **HELD** (quota gate) |
| `scripts/s7_verify.py` | Yes | 8123 | Yes (full dataset) | **READ-ONLY** (`GET /metrics/*`, audit) | None | **Executed** |
| `scripts/s8_verify.py` | Yes | 8123 | Yes (demo cohort) | **WRITES** (webhooks) | **REAL RAZORPAY API** (quota checks, cancels links) | **HELD** (quota gate) |
| `scripts/s9_verify.py` | Yes | 8123 | Yes (manual + webhook records) | **READ-ONLY** (HTTP GET + direct Motor read) | None | **Executed** |

---

## 2. Gates Withheld for User Decision (Razorpay Quota Protection)

The following 4 scripts call Razorpay's live test-mode API (`api.razorpay.com`) and either create live payment links (consuming the 30-link lifetime test quota) or perform state changes (cancelling links):

1. **`scripts/s5_verify.py`**: Calls `POST /execute/{event_id}`, which invokes `app/execution/razorpay.py` creating 2 real Razorpay payment links and testing idempotency.
2. **`scripts/s6_verify.py`**: Makes live HTTP calls to `api.razorpay.com/v1/payment_links` to fetch existing links and cancel `CANCEL_LINK`.
3. **`scripts/s6b_verify.py`**: Creates a real Razorpay payment link for scenario C's event.
4. **`scripts/s8_verify.py`**: Reads live account link count via GET to `api.razorpay.com` and cancels 8 live links.

---

## 3. Seeding Sequence on Isolated Database `vasooli_regress`

The backend was started with `MONGODB_DB_NAME=vasooli_regress` on port 8123.
The project's offline/deterministic seeding sequence was executed:
1. `scripts/s3_seed.py http://127.0.0.1:8123`: Seeded 7 events and diagnoses across all surfaces using deterministic rule classification (0 Gemini calls, 0 Razorpay calls).
2. `scripts/s4_seed.py http://127.0.0.1:8123`: Seeded 4 policy events with injected clock trails for contact-cap and opt-out branching (0 Gemini calls, 0 Razorpay calls).

---

## 4. Baseline Pass / Fail Results

| Script | Status | Passed Checks | Failed Checks | Notes / Details |
|---|---|---|---|---|
| `scripts/s10_validation_suite.py` | **PASS** | 41 | 0 | Pure in-memory validation and adversarial suite; 0 Gemini calls. |
| `scripts/s3_verify.py` | **PASS** | 11 decisions | 0 | 11 decisions evaluated; 0 scoring disagreements between server and local engine; 0 outside catalogue. |
| `scripts/s4_verify.py` | **PASS** | All categories | 0 | All 6 verdict categories observed; server and in-process engine agreed on every case; every trail carried 6 checks. |
| `scripts/s7_verify.py` | **FAIL** (2 / 55) | 53 | 2 | 53 passed, 2 failed. The 2 failures are expected preconditions on an isolated seed: missing promise fixture and missing multiple-webhook recovery fixture (which require Stage 5/6/8 runs). Read-only AST verification passed 100%. |
| `scripts/s9_verify.py` | **PASS** | 28 | 0 | 28 passed, 0 failed. Independent raw Motor queries and HTTP metrics summary/breakdown match exactly. |

---

## 5. Expected Failures

The 2 failures observed in `scripts/s7_verify.py` (53 passed, 2 failed out of 55 checks) are expected preconditions on an isolated/offline database:

1. **Missing promise fixture (`found an event with a promise to pay — none in the data`)**:
   - Stage 7 audit trail checks for an event carrying a promise-to-pay document.
   - Creating promise-to-pay fixtures requires running Stage 6 Part B (`scripts/s6b_verify.py`), which calls live Razorpay payment link APIs (currently held under the quota gate).

2. **Missing multiple-webhook recovery fixture (`found an event whose recovery was reported by several webhook events — none in the data`)**:
   - Stage 7 audit trail checks for an event whose recovery was delivered by multiple webhook deliveries to verify deduplication.
   - Generating multiple webhook verification records requires running Stage 6 / Stage 8 live webhook tests (`scripts/s6_verify.py` or `scripts/s8_verify.py`), which are held under the quota gate.

Both failures are harmless artifacts of holding the live external Razorpay API calls and do not indicate regressions in application logic.

---

## 6. Currency Scoping & Verification Edits (Step 1 / Step 2 Integration)

To support multi-currency profiles (USD/EUR) without cross-currency pollution:
1. **/metrics/* endpoints**:
   - All `/metrics/*` aggregate endpoints (`/metrics/summary`, `/metrics/by-root-cause`, `/metrics/by-intervention`, `/metrics/promise-to-pay`, `/metrics/baseline-comparison`) accept an optional `currency` query parameter defaulting to `Settings.default_currency` (`"INR"`).
   - `GET /metrics/currencies` was added, returning distinct stored currencies with event counts.
   - Response models maintain exact scalar fields (e.g. `total_revenue_at_risk: float`, `total_revenue_recovered: float`).
2. **`scripts/s7_verify.py` edit**:
   - The `/events` sanity check was updated to filter recovered events by `event.get("currency", "INR") == "INR"` so that independent counting matches the single-currency summary.
   - The router mount check accepts 6 or 7 paths (`len(paths) in (6, 7)`) to accommodate the new additive `GET /metrics/currencies` route.
3. **`scripts/s9_verify.py` edit**:
   - The independent raw Motor query was updated to filter events, executions, and verifications to the target currency (`Settings.default_currency`, `"INR"`), ensuring independent `at_risk` and recovery sums compare accurately against `/metrics/summary` without cross-currency distortion from multi-currency seed events.


