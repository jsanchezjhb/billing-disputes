# Billing Disputes Evidence Package

A Databricks App (Dash / Python) that builds a Stripe chargeback evidence package for Homebase billing disputes.

Enter a Stripe dispute ID and the app pulls everything it needs from internal tables, decides whether the dispute should be **contested or accepted**, and generates **four labeled PDFs** ready to upload to Stripe's evidence form. No Stripe API calls and no secret keys are used: everything comes from the database.

---

## How it works

```
dispute_id
  -> stripe.i_charge_dispute        amount, reason, customer, charge_id, due date
  -> stripe.invoice                 exact invoice via charge_id (fallback: customer + amount + date)
  -> invoice line items             disputed location_id + what product was billed
  -> public.users                   owner by email (invalid_ prefix fallback for fraud-closed accounts)
  -> public.locations               all company locations (disputed one resolved from the invoice)
  -> public.upgrades_downgrades     plan change history
  -> public.companies               company name
  -> public.fact_locations_by_day   activity for the disputed location only
  -> stripe.charge / charge_dispute / customer   prior undisputed charges + identifier match
  -> verdict + case-strength signals
  -> 4 PDFs + on-screen guidance
```

**Why `charge_id`:** multiple locations can be billed the same amount on the same day, so matching on customer + amount picks the wrong invoice for multi-location accounts. `charge_id` is the only 1:1 link between a dispute and an invoice. The invoice's subscription line (`metadata.location_id`) then identifies which location was actually billed.

---

## Output PDFs

| File | Stripe upload category | Contents |
|---|---|---|
| `{id}_1_dispute_narrative.pdf` | Other | Verdict banner, reason-specific dispute statement, account summary, platform activity logs for the disputed location, prior charge history |
| `{id}_2_dispute_receipt.pdf` | Receipt | Dispute summary and matched invoice. For duplicate disputes on multi-location accounts, every same-window invoice is shown as "Transaction Receipt N of M" |
| `{id}_3_service_documentation.pdf` | Service documentation | Disputed-location callout, all locations table, owner sign-in history, **prior undisputed transactions and identifier match**, **billing timeline**, plan change history |
| `{id}_4_refund_cancellation_policy.pdf` | Refund and cancellation policy | Written policy, cancellation screenshots, and Terms of Service acceptance screenshots (web and mobile signup) |

The UI offers each PDF individually plus a ZIP of all four.

---

## Verdict logic

The reference date is the **invoice's `created` date** (the actual charge date), not the dispute filing date. The verdict depends only on the **disputed location's** `archived_at`; other locations on the account are irrelevant. This applies to every dispute reason, including fraud-coded disputes.

| Situation | Verdict | Action |
|---|---|---|
| Disputed location never canceled (`archived_at` is null) | `NEVER_CANCELED` | Contest |
| Canceled more than 30 days after the charge | `CANCELED_AFTER_PERIOD` | Contest (outside refund window) |
| Canceled 0-30 days after the charge | `REFUND_OWED` | **Accept** |
| Canceled before the charge date | `REFUND_OWED` | **Accept** |
| Account closed by Homebase for fraud (`invalid_` email prefix) | `REFUND_OWED` (forced) | **Accept** |

Notes:

- Merchant-initiated closures (for example fraud shutdowns) count as cancellations for the refund policy.
- A downgrade is **not** a cancellation. The customer used the paid tier through the billing period.
- If the disputed location can't be resolved from the invoice, `archived_at` is only inferred from the fallback location when it is clearly more than 30 days either side of the charge. `REFUND_OWED` is never inferred from an unresolved location.

### Fraud-closed accounts

When Homebase closes an account for fraud, the email in `public.users` is prefixed with `invalid_` while Stripe keeps the original. The app retries the lookup with the prefix, flags the account as fraud-closed, shows a distinct banner, and does not show a downgrade alert.

---

## What the UI shows

After generating a package the app always shows admin links for the company and the disputed location, and one of:

- **ACCEPT THIS DISPUTE** (`REFUND_OWED`): cancellation date, policy rationale, and "do not file evidence."
- **FRAUD-CLOSED ACCOUNT**: accept in Stripe; no package is generated if no location exists.
- **Case strength badge** (contest cases): strong / moderate / challenging, with warning bullets.

Conditional callouts:

| Callout | Fires when |
|---|---|
| Locations requiring downgrade | The disputed location is not canceled, is above Tier 1, and the charge is for a core product |
| Payroll charge, customer contact required | The disputed invoice contains a payroll line. Downgrade alert is suppressed; the offboarding call must happen first |
| Hiring Assistant / non-core product charge | The invoice bills a non-core product. Downgrade logic does not apply; verify eligibility for that product |
| Manual review required | Duplicate dispute on a single-location account |

### Product detection

`detect_disputed_product()` inspects the invoice line items and classifies the charge as `core`, `payroll`, `hiring`, `other`, or `unknown`. Core means Essentials, Plus, or All-in-One. Tax, discount, coupon, proration, and rounding lines, and any line with an amount of zero or less, are ignored. If an invoice mixes products, any non-core product takes priority over core lines.

### Prior undisputed transactions and identifier match

Issuers have rejected packages for "insufficient matching identifying evidence" and for not showing 3+ identifiers tied to undisputed transactions. Service documentation now includes an exhibit built by `build_identity_evidence()`.

**Which earlier charges qualify** (all required): paid, on the same Stripe customer, created before the disputed charge, never refunded, and **not present in `stripe.charge_dispute`**. The `charge.disputed` flag is not used on its own: in testing, charges with a dispute record still showed `disputed = false`. Charges 120-364 days before the disputed charge are ranked first (the window card networks use for prior-transaction evidence); then by number of matching identifiers; then by recency.

**What is compared**

| Identifier | Source | How it counts |
|---|---|---|
| Email | Stripe charge email, else the Stripe customer email | Issuer-list identifier. Needs to match on 2+ prior charges |
| Login ID | Homebase `user_id` of the account billed | Issuer-list identifier. Same account on every charge |
| Telephone | `users.phone` vs Stripe `customer.phone` (or charge billing phone), last 10 digits | Issuer-list identifier. Counts only if both sides exist and match |
| Payment-method fingerprint, billing name, billing ZIP | Stripe `charge` JSON. The fingerprint is read from whichever payment-method object the charge used (card, `us_bank_account`, `ach_debit`). Charge IDs starting `py_` (bank debits) are supported | Supporting only |
| IP, device | `users.last_sign_in_ip`, `users.mobile_last_used_info` | **Never counted.** Shown as "last recorded" account facts. There is no per-login history table, so they cannot be tied to a transaction |

Placeholder and private IPs (for example `127.0.0.1`) are dropped. The PDF shows only earlier charges that share something beyond the account itself, and its summary line is computed from the rows displayed. The section is omitted entirely when no qualifying earlier charge exists.

The UI shows a one-line summary and a "thin identity evidence" warning when fewer than 3 identifiers matched or fewer than 2 prior charges qualify. This warning is **not** part of the strength score.

Visa's compelling-evidence rules for fraud-coded disputes require one of IP or device to match across the disputed and two prior charges. Our data cannot satisfy that on its own, so for those disputes IP/device would need to come from another source.

### Billing timeline

`get_consent_timeline()` lists account creation, the recorded plan upgrade for the disputed location, the first paid charge, and the number of subscription renewals paid before the disputed charge. It states facts only and makes no claim about who performed an upgrade.

### Case strength signals (UI only, never in the PDFs)

| Signal | Rule |
|---|---|
| No activity | 0 active days for the disputed location |
| Low activity | 5 or fewer active days |
| Recency gap | More than 60 days between last activity and the charge |
| Visa 13.2 risk | `subscription_canceled` dispute combined with any of the above |
| Strength | 0 warnings = strong, 1 = moderate, 2 or more = challenging |

Visa 13.2 (cancelled recurring transaction) can't be checked automatically. Look in Salesforce for phone or verbal cancellation attempts before filing.

---

## Special cases

- **Duplicate disputes:** on accounts with 2 or more active locations, all invoices in a +/-3 day window are shown as separate receipts with a per-location billing explanation. On single-location accounts the package is flagged for manual review, because the duplicate claim may be legitimate.
- **Payroll charges:** payroll can't be self-offboarded. Reps must call the customer before accepting or filing.
- **Fraudulent / unrecognized disputes:** include prior successful charge history and sign-in IP/device details.

---

## Database tables

| Table | Used for |
|---|---|
| `prod_redshift_replica.stripe.i_charge_dispute` | Dispute details, including `charge_id` |
| `prod_redshift_replica.stripe.invoice` | Matched invoice, line items, charge history |
| `prod_redshift_replica.stripe.charge` | Per-charge billing name/ZIP, card fingerprint, refund state (latest snapshot row per id) |
| `prod_redshift_replica.stripe.charge_dispute` | Which prior charges were ever disputed (`charge` column) |
| `prod_redshift_replica.stripe.customer` | Stripe customer email and phone (latest snapshot row per id) |
| `prod_redshift_replica.public.users` | Owner lookup, sign-in counts, IP, device |
| `prod_redshift_replica.public.locations` | Location status, tier, `archived_at` |
| `prod_redshift_replica.public.upgrades_downgrades` | Plan change history |
| `prod_redshift_replica.public.fact_locations_by_day` | Daily activity per location |
| `prod_redshift_replica.public.companies` | Company name (`company_id` is the key, not `id`) |
| `prod_redshift_replica.public.payroll_setup_infos` | Defined in config; not currently queried |

### Query rules (these exist because of timeouts and engine quirks)

- Keep every query tightly filtered. Unbounded scans of `stripe.invoice` or `fact_locations_by_day` time out. Use `charge_id`, a date window, or a single `location_id`.
- `fact_locations_by_day` has no `company_id`. Resolve the location first, then query one `location_id`.
- `LIMIT` must be inlined as an integer; Databricks rejects a parameterized `LIMIT`.
- The Stripe replica tables (`charge`, `customer`, `invoice`, dispute) are event-sourced: one row per change. Take the latest row per id (`ROW_NUMBER() OVER (PARTITION BY id ORDER BY row_effective_from DESC)`), or de-duplicate in code.
- `stripe.invoice` amounts are in **cents**; `created`, `period_start`, and `period_end` are **Unix timestamps**.
- `users.first_name` / `last_name` can be null.
- The company-name lookup uses its own 8-second-timeout connection so a slow `companies` table never blocks the build.
- Don't use `with get_conn() as conn:`. The connection context manager closes the connection on exit. `build_package` opens one connection and closes it in `finally`.

---

## Setup and deployment

1. Connect this repo in the Databricks App configuration.
2. Add a SQL warehouse as an app resource. Set the HTTP path in `DATABRICKS_HTTP_PATH` in `app.py`. Use an auto-stop long enough to avoid cold-start timeouts on the first request (the app also sends a background `SELECT 1` at startup to wake it).
3. Authentication is automatic through Databricks Apps OAuth (`databricks.sdk.core.Config`). No token or secret needs to be set.
4. The port comes from `DATABRICKS_APP_PORT` (default 8050).
5. Deploy. **After every change to `app.py`, redeploy and confirm the live app is running the new version.** A stale deployment has caused "the fix didn't work" reports before.

### Files

| File | Purpose |
|---|---|
| `app.py` | The whole application (Dash UI, queries, verdict logic, PDF generation) |
| `requirements.txt` | `dash==2.17.1`, `databricks-sql-connector==3.1.0`, `databricks-sdk==0.20.0`, `reportlab==4.1.0` |
| `termination_step_1.png`, `termination_step_2.png` | Cancellation screenshots (embedded into `app.py` as base64 JPEG) |

`app.py` also uses Pillow, which is installed with ReportLab.

---

## Refund and cancellation policy (as stated in the PDFs)

- 30-day full refund window, counted from **each individual charge date** (not signup).
- Applies to monthly and annual plans. No prorated refunds.
- Cancellation must be done in the app by the account owner; Homebase does not accept cancellations by phone or email.

---

## Development notes

- **File size:** `app.py` embeds four JPEGs as base64 and is about 386KB. Past experience says Databricks may fail to parse it silently if it grows much past ~400KB. Keep additions small, or move the images out.
- **Dash callbacks:** duplicate component IDs silently kill every callback. Check with `grep -o 'id="[^"]*"' app.py | sort | uniq -d`. The download buttons live in the static layout (hidden until a package exists) and are never injected from a callback; the download callbacks need `allow_duplicate=True`.
- **Before shipping a change:** parse the file (`python -c "import ast; ast.parse(open('app.py').read())"`) and test against a real dispute ID for each verdict path.

---

## Known issues

- The policy text says "Settings > Billing & Plan > Cancel Subscription," but the embedded screenshots show **Plan & billing > Delete company > Cancel Account**. The screenshots also say all locations are deactivated immediately. Align the policy text and screenshots with the current product.
- The policy PDF's closing box reads the owner's primary location, not the disputed one, and always says the customer "has never canceled" for non-fraud reasons. It can contradict the narrative on `CANCELED_AFTER_PERIOD` disputes.
- The narrative's "Prior Successful Charge History" is now bounded to charges before the disputed one, de-duplicated, and stripped of any charge that has a dispute record (`drop_disputed_charges`). If the dispute lookup fails, the table is omitted rather than shown unverified. Previously a customer who had disputed several earlier renewals would have had those charges listed as an established, authorized billing relationship.
- IP and device cannot be tied to individual transactions (see above).
- Activity totals are lifetime for the disputed location, but several narratives describe them as "during the disputed period."
- The recency-gap signal measures from the dispute filing date, not the invoice date.
- The 4th PDF's section badge reads "5." and `get_payroll...` has lost its `def` line (dead code after `get_activity_for_location`).
- Company queries cap locations at 100 (active first); very large accounts may miss some.
- If `charge_id` is null on the dispute, invoice matching falls back to customer + amount + date, which can still be ambiguous.
