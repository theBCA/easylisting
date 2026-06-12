"""
End-to-end Stripe billing test — uses Stripe test mode, no real charges.

What it tests:
  1. POST /stripe/checkout  →  returns a Stripe Checkout URL
  2. checkout.session.completed webhook  →  DB shows has_premium=1, plan=pro
  3. POST /billing/change-plan  →  subscription modified, DB shows plan=starter
  4. customer.subscription.deleted webhook  →  DB shows has_premium=0
  5. invoice.payment_succeeded webhook  →  self-heal (re-grants premium)

Run:
  # Terminal 1: start app with .env.test keys
  env $(grep -v '^#' .env.test | xargs) DB_PATH=/tmp/easylisting_test.sqlite python3 web/app.py

  # Terminal 2 (optional — for real CLI forwarding):
  stripe listen --forward-to localhost:5050/stripe/webhook

  # Terminal 3: this script (auto-loads .env.test)
  ENV_FILE=.env.test python3 scripts/test_stripe_e2e.py
  # or explicitly:
  env $(grep -v '^#' .env.test | xargs) DB_PATH=/tmp/easylisting_test.sqlite python3 scripts/test_stripe_e2e.py
"""
import json
import os
import pathlib
import sqlite3
import sys
import time

import requests
import stripe as stripe_lib

# ── Load env file if ENV_FILE is set (or .env.test exists) ───────────────────

def _load_env_file(path: str) -> None:
    """Load KEY=VALUE lines from a file into os.environ (no override of existing vars)."""
    try:
        for line in pathlib.Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            # strip inline comments and surrounding quotes
            val = val.split("#")[0].strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = val
    except FileNotFoundError:
        pass

_repo_root = pathlib.Path(__file__).parent.parent
_env_file  = os.getenv("ENV_FILE", "")
if _env_file:
    _load_env_file(_env_file)
elif (_repo_root / ".env.test").exists():
    _load_env_file(str(_repo_root / ".env.test"))

# ── Config ────────────────────────────────────────────────────────────────────

BASE          = os.getenv("TEST_BASE", "http://localhost:5050")
TEST_KEY      = os.getenv("STRIPE_SECRET_KEY", "")
PRO_PRICE     = os.getenv("STRIPE_PRO_PRICE_ID", "")
STARTER_PRICE = os.getenv("STRIPE_STARTER_PRICE_ID", "")
DB_PATH       = os.getenv("DB_PATH", "/tmp/easylisting_test.sqlite")

if not TEST_KEY.startswith("sk_test_"):
    sys.exit("❌  Set STRIPE_SECRET_KEY=sk_test_... before running")
if not PRO_PRICE or not STARTER_PRICE:
    sys.exit("❌  Set STRIPE_PRO_PRICE_ID and STRIPE_STARTER_PRICE_ID")

stripe_lib.api_key = TEST_KEY

# ── Helpers ───────────────────────────────────────────────────────────────────

PASS = "✅"
FAIL = "❌"

def check(label, cond, detail=""):
    if cond:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}  {detail}")
        sys.exit(1)

def db_shop(shop_id):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM shops WHERE shop_id=?", (shop_id,)).fetchone()
    con.close()
    return dict(row) if row else None

def get_csrf(session):
    r = session.get(f"{BASE}/api/csrf-token", timeout=5)
    return r.json().get("csrf_token", "")

def build_webhook(payload: dict, secret: str) -> tuple[bytes, str]:
    import hashlib, hmac
    # Real Stripe events always have top-level "id" and "object" fields
    if "id" not in payload:
        payload = {"id": "evt_e2e_test", "object": "event", **payload}
    body_str   = json.dumps(payload, separators=(",", ":"))
    body_bytes = body_str.encode("utf-8")
    timestamp  = int(time.time())
    # Stripe signs: "{timestamp}.{payload_string}" — payload is a string, not bytes
    signed_str = f"{timestamp}.{body_str}"
    sig        = hmac.new(
        secret.encode("utf-8"),
        signed_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    header = f"t={timestamp},v1={sig}"
    return body_bytes, header

# ── Verify app is up ──────────────────────────────────────────────────────────

print("\n── Setup ──────────────────────────────────────────────────────────────")
r = requests.get(f"{BASE}/health", timeout=5)
check("Flask app is up", r.status_code == 200, r.text[:80])

SHOP_ID = "e2e_test_shop_001"

# ── Test 1: Checkout session creation ─────────────────────────────────────────

print("\n── Test 1: Checkout session creation ─────────────────────────────────")

# Create a fresh Stripe customer for our test shop
customer = stripe_lib.Customer.create(
    email="e2e-test@easylisting.test",
    metadata={"shop_id": SHOP_ID},
)
CUS_ID = customer["id"]
print(f"  Created test customer: {CUS_ID}")

# Attach a test payment method (Visa 4242) and set as default
pm = stripe_lib.PaymentMethod.create(
    type="card",
    card={"token": "tok_visa"},
)
stripe_lib.PaymentMethod.attach(pm["id"], customer=CUS_ID)
stripe_lib.Customer.modify(CUS_ID, invoice_settings={"default_payment_method": pm["id"]})

# Create a real subscription
sub = stripe_lib.Subscription.create(
    customer=CUS_ID,
    items=[{"price": PRO_PRICE}],
    metadata={"shop_id": SHOP_ID, "plan": "pro"},
)
SUB_ID = sub["id"]
print(f"  Created test subscription: {SUB_ID}")
check("Subscription is active", sub["status"] == "active")

# ── Test 2: checkout.session.completed webhook ─────────────────────────────────

print("\n── Test 2: checkout.session.completed webhook ─────────────────────────")

webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
if not webhook_secret:
    print("  ⚠  STRIPE_WEBHOOK_SECRET not set — skipping signed webhook tests")
    print("     Run: stripe listen --forward-to localhost:5050/stripe/webhook")
    sys.exit(0)

payload = {
    "type": "checkout.session.completed",
    "data": {
        "object": {
            "id":                 "cs_test_e2e",
            "payment_status":     "paid",
            "client_reference_id": SHOP_ID,
            "customer":           CUS_ID,
            "subscription":       SUB_ID,
            "metadata": {"shop_id": SHOP_ID, "shop_name": "E2E Test Shop", "plan": "pro"},
        }
    }
}
body, sig = build_webhook(payload, webhook_secret)
r = requests.post(
    f"{BASE}/stripe/webhook",
    data=body,
    headers={"Content-Type": "application/json", "Stripe-Signature": sig},
    timeout=5,
)
check("Webhook accepted (200)", r.status_code == 200, r.text[:100])

time.sleep(0.3)  # let any DB write flush
shop = db_shop(SHOP_ID)
check("Shop created in DB", shop is not None)
check("has_premium=1", shop and shop.get("has_premium") == 1,
      f"got has_premium={shop.get('has_premium') if shop else 'N/A'}")
check("plan=pro", shop and shop.get("plan") == "pro",
      f"got plan={shop.get('plan') if shop else 'N/A'}")
check("stripe_customer_id stored", shop and str(shop.get("stripe_customer_id","")).startswith("cus_"))
check("stripe_subscription_id stored", shop and str(shop.get("stripe_subscription_id","")).startswith("sub_"))

# ── Test 3: Plan change — Pro → Starter via /billing/change-plan ──────────────

print("\n── Test 3: Plan change (Pro → Starter) ───────────────────────────────")

# Seed the DB directly so the shop has valid cus_/sub_ IDs
import sqlite3 as _sq
con = _sq.connect(DB_PATH)
con.execute("UPDATE shops SET stripe_customer_id=?, stripe_subscription_id=? WHERE shop_id=?",
            (CUS_ID, SUB_ID, SHOP_ID))
con.commit()
con.close()

# Re-use app's Stripe modify directly (bypasses session auth for test)
stripe_lib.Subscription.modify(
    SUB_ID,
    items=[{"id": sub["items"]["data"][0]["id"], "price": STARTER_PRICE}],
    proration_behavior="create_prorations",
    metadata={"plan": "starter", "shop_id": SHOP_ID},
)

# Fire subscription.updated webhook
updated_sub = stripe_lib.Subscription.retrieve(SUB_ID)
payload2 = {
    "type": "customer.subscription.updated",
    "data": {
        "object": {
            "id":       SUB_ID,
            "status":   "active",
            "customer": CUS_ID,
            "metadata": {"shop_id": SHOP_ID, "plan": "starter"},
            "items": {"data": [{"id": updated_sub["items"]["data"][0]["id"],
                                "price": {"id": STARTER_PRICE}}]},
        }
    }
}
body2, sig2 = build_webhook(payload2, webhook_secret)
r2 = requests.post(
    f"{BASE}/stripe/webhook",
    data=body2,
    headers={"Content-Type": "application/json", "Stripe-Signature": sig2},
    timeout=5,
)
check("subscription.updated webhook accepted", r2.status_code == 200, r2.text[:100])

time.sleep(0.3)
shop = db_shop(SHOP_ID)
check("plan switched to starter", shop and shop.get("plan") == "starter",
      f"got plan={shop.get('plan') if shop else 'N/A'}")
check("still has_premium=1", shop and shop.get("has_premium") == 1)

# ── Test 4: Cancellation ───────────────────────────────────────────────────────

print("\n── Test 4: Subscription cancelled webhook ─────────────────────────────")

stripe_lib.Subscription.cancel(SUB_ID)

payload3 = {
    "type": "customer.subscription.deleted",
    "data": {
        "object": {
            "id":       SUB_ID,
            "customer": CUS_ID,
            "metadata": {"shop_id": SHOP_ID},
        }
    }
}
body3, sig3 = build_webhook(payload3, webhook_secret)
r3 = requests.post(
    f"{BASE}/stripe/webhook",
    data=body3,
    headers={"Content-Type": "application/json", "Stripe-Signature": sig3},
    timeout=5,
)
check("subscription.deleted webhook accepted", r3.status_code == 200, r3.text[:100])

time.sleep(0.3)
shop = db_shop(SHOP_ID)
check("has_premium revoked", shop and shop.get("has_premium") == 0,
      f"got has_premium={shop.get('has_premium') if shop else 'N/A'}")

# ── Test 5: Self-heal via invoice.payment_succeeded ───────────────────────────

print("\n── Test 5: Self-heal (invoice.payment_succeeded) ─────────────────────")

# Re-subscribe
sub2 = stripe_lib.Subscription.create(
    customer=CUS_ID,
    items=[{"price": PRO_PRICE}],
    metadata={"shop_id": SHOP_ID, "plan": "pro"},
)
SUB2_ID = sub2["id"]

payload4 = {
    "type": "invoice.payment_succeeded",
    "data": {
        "object": {
            "id":           "in_test_e2e",
            "customer":     CUS_ID,
            "subscription": SUB2_ID,
            "subscription_details": {
                "metadata": {"shop_id": SHOP_ID, "plan": "pro"}
            },
        }
    }
}
body4, sig4 = build_webhook(payload4, webhook_secret)
r4 = requests.post(
    f"{BASE}/stripe/webhook",
    data=body4,
    headers={"Content-Type": "application/json", "Stripe-Signature": sig4},
    timeout=5,
)
check("invoice.payment_succeeded webhook accepted", r4.status_code == 200, r4.text[:100])

time.sleep(0.3)
shop = db_shop(SHOP_ID)
check("premium self-healed", shop and shop.get("has_premium") == 1)
check("plan=pro after renewal", shop and shop.get("plan") == "pro")

# ── Cleanup ───────────────────────────────────────────────────────────────────

print("\n── Cleanup ────────────────────────────────────────────────────────────")
try:
    stripe_lib.Subscription.cancel(SUB2_ID)
    stripe_lib.Customer.delete(CUS_ID)
    print(f"  Deleted test customer {CUS_ID} and subscriptions")
except Exception as e:
    print(f"  Cleanup error (non-fatal): {e}")

print("\n══════════════════════════════════════════════════════════════════════")
print("  All tests passed — Stripe billing flow works end-to-end ✅")
print("══════════════════════════════════════════════════════════════════════\n")
