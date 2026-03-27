# billing-disputes

Databricks App that generates a complete Stripe chargeback evidence package for Homebase billing disputes.

## What it does

1. Enter a Stripe dispute ID in the UI
2. Looks up the dispute from `prod_redshift_replica.stripe.i_charge_dispute`
3. Resolves the customer email to an internal Homebase account
4. Queries location status, activity logs, and plan history from the DB
5. Generates 5 labeled PDFs ready to upload directly to Stripe's evidence form

No Stripe API calls. No secret keys. Everything comes from the database.

## Output PDFs

| File | Stripe Upload Category |
|---|---|
| `{id}_1_dispute_narrative.pdf` | Other (paste into rebuttal text field) |
| `{id}_2_dispute_receipt.pdf` | Receipt |
| `{id}_3_service_documentation.pdf` | Service documentation |
| `{id}_4_customer_activity_logs.pdf` | Customer communication |
| `{id}_5_refund_cancellation_policy.pdf` | Refund and cancellation policy |

## Database Tables Used

| Table | Purpose |
|---|---|
| `prod_redshift_replica.stripe.i_charge_dispute` | Dispute details (amount, reason, due date, customer email) |
| `prod_redshift_replica.public.users` | Resolve email to user_id |
| `prod_redshift_replica.public.locations` | Location status, archived_at, active_now |
| `prod_redshift_replica.public.upgrades_downgrades` | Subscription/plan change history |
| `prod_redshift_replica.public.fact_locations_by_day` | Daily activity logs |

## Databricks App Setup

1. Connect this GitHub repo in the Databricks App configuration
2. Add your SQL Warehouse as an app resource
3. Set instance size to Medium (2 vCPU, 6 GB) -- no other configuration needed
4. Deploy

The app uses DATABRICKS_TOKEN from the environment automatically -- no manual secrets setup required.

## Files

- `app.py` -- main Dash application
- `requirements.txt` -- Python dependencies (dash, databricks-sql-connector, reportlab)

## Refund & Cancellation Policy

Per Homebase policy (baked into the generated PDFs):

- 30-day full refund window from each charge date (not signup date)
- Applies to both monthly and annual plans
- No prorated refunds under any circumstances
- Cancellation must be completed in-app: Settings > Billing & Plan > Cancel Subscription
