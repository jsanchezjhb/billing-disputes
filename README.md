# billing-disputes

Databricks App that generates a complete Stripe chargeback evidence package for Homebase billing disputes.

## What it does

1. Takes a Stripe dispute ID as input
2. Fetches the dispute from the Stripe API (server-side — no CORS issues)
3. Resolves the charge → invoice → customer email → internal Homebase account
4. Queries the Homebase database for location status, activity logs, and plan history
5. Generates 5 labeled PDFs ready to upload directly to Stripe's evidence form

## Output PDFs

| File | Stripe Upload Category |
|---|---|
| `{id}_1_dispute_narrative.pdf` | Other (paste into rebuttal text field) |
| `{id}_2_dispute_receipt.pdf` | Receipt |
| `{id}_3_service_documentation.pdf` | Service documentation |
| `{id}_4_customer_activity_logs.pdf` | Customer communication |
| `{id}_5_refund_cancellation_policy.pdf` | Refund and cancellation policy |

## Setup

### 1. Databricks Secrets

Store your Stripe secret key in Databricks Secrets before deploying:

```python
# Run in a Databricks notebook
dbutils.secrets.createScope("billing-disputes")
dbutils.secrets.put(
    scope="billing-disputes",
    key="stripe-secret-key",
    string_value="sk_live_..."  # use a restricted key with disputes:read + charges:read
)
```

### 2. Databricks App Configuration

- **App name:** billing-disputes-package
- **Git repo:** this repository
- **Instance size:** Medium (2 vCPU, 6 GB)
- **Resources:** Add your SQL Warehouse

### 3. Stripe Restricted Key

Create a restricted key in Stripe Dashboard → Developers → API keys with:
- `Disputes` → Read
- `Charges` → Read

## Files

- `app.py` — main Dash application
- `requirements.txt` — Python dependencies

## Refund & Cancellation Policy

- 30-day full refund window from **each charge date** (not signup date)
- Applies to both monthly and annual plans
- No prorated refunds under any circumstances
- Cancellation must be completed in-app: Settings → Billing & Plan → Cancel Subscription
