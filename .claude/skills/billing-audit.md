---
name: billing-audit
description: Audit the Stripe billing flow for gaps, unhandled edge cases, and DB/Stripe state mismatches. Use before shipping billing changes.
---

## Stripe billing audit

**Current state snapshot:**

Webhook events handled:
!`grep "elif etype ==" web/apis/payments.py`

Rate limiting / auth guards on billing routes:
!`grep -A2 "@bp.route.*billing\|@bp.route.*stripe\|@bp.route.*upgrade" web/apis/payments.py | grep -E "@limiter|@csrf|def "`

Shops with premium but no cus_ ID (DB inconsistency):
!`python3 -c "import sqlite3,os; db=os.getenv('DB_PATH','web/easylisting.sqlite'); c=sqlite3.connect(db); rows=c.execute(\"SELECT shop_id,plan,stripe_customer_id FROM shops WHERE has_premium=1 AND (stripe_customer_id IS NULL OR stripe_customer_id NOT LIKE 'cus_%')\").fetchall(); print(rows or 'None — all good');" 2>/dev/null || echo "(run with DB_PATH set to check live data)"`

Audit checklist:
1. Every webhook event type Stripe can send — are all handled or explicitly ignored?
2. Every Stripe API call — does it check `has_premium` before calling?
3. Both domains (EUR + TRY) — are price IDs and webhook secrets correctly routed?
4. After cancellation — can the user still access billing portal to resubscribe?
5. Plan switch buttons — do they only show for `has_premium=1` users?

Report any gaps with: event type, what's missing, one-line fix.
