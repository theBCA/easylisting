---
description: Rules that apply when editing Stripe billing code
globs:
  - "web/apis/payments.py"
  - "web/core/config.py"
  - "web/tests/test_app.py"
---

## Stripe billing rules

- `/billing/change-plan` must check `has_premium=1` AND `cus_/sub_` format before calling Stripe — never modify a cancelled subscription
- `customer.subscription.updated` webhook: when `plan` key is missing from metadata (portal change / system event), fall back to the shop's existing DB plan — never default to "pro"
- Only catch `stripe_lib.error.SignatureVerificationError` as a bad signature (→ 400). Other exceptions after `construct_event` are parse errors — fall back to `json.loads(payload)`
- `set_premium(active=False)` intentionally keeps `cus_/sub_` IDs — the billing portal needs the customer ID. Do not null them on cancellation.
- `has_active_sub` only checks ID format, not that the subscription is live. Always also check `has_premium=1` before calling Stripe's modify/cancel APIs.
- Always use `_PLAN_PRICE_IDS_TRY` on kolaylistele.com and `_PLAN_PRICE_IDS` on easylisting.app — never mix them
- Two webhook secrets exist: `STRIPE_WEBHOOK_SECRET` (TRY) and `STRIPE_WEBHOOK_SECRET_EUR` — `_webhook_secret_candidates()` handles routing, don't hardcode either
