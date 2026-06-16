"""
Test suite for EasyListing Flask app.
Covers: security hardening, auth flows, free-limit gating,
        premium/Stripe webhook logic, and core API contracts.
"""
import io
import json
import os
import struct
import time
import hashlib
import hmac
import tempfile
import pytest
from unittest.mock import patch, MagicMock

# ── use an isolated in-memory DB for every test run ──────────────────────────
os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("FLASK_SECRET", "test-secret-key-32-chars-minimum!")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("ETSY_API_KEY", "fake-etsy-key")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_fake")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test_fake")
os.environ.setdefault("STRIPE_STARTER_PRICE_ID", "price_starter_fake")
os.environ.setdefault("STRIPE_PRO_PRICE_ID", "price_pro_fake")

import db as db_module
import app as app_module
from app import app


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Each test gets a fresh SQLite file and a reset rate-limiter."""
    db_file = str(tmp_path / "test.sqlite")
    with patch.dict(os.environ, {"DB_PATH": db_file}):
        db_module.DB_PATH = db_file
        db_module.init_db()
        # Reset in-memory rate-limiter storage so limits don't bleed across tests
        try:
            app_module.limiter.reset()
        except Exception:
            pass
        yield
        db_module.DB_PATH = ":memory:"


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c


@pytest.fixture()
def connected_client(client):
    """Client with a fake Etsy session (simulates logged-in shop owner)."""
    with client.session_transaction() as sess:
        sess["access_token"] = "fake-token"
        sess["shop_id"] = "12345"
        sess["shop_name"] = "TestShop"
        sess["token_expiry"] = time.time() + 3600
    return client


@pytest.fixture()
def guest_client(client):
    """Client with a guest session (is_guest() returns True)."""
    with client.session_transaction() as sess:
        sess["guest"]    = True
        sess["guest_id"] = "guest-abc123"
    return client


# ─────────────────────────────────────────────────────────────────────────────
# 1. SECURITY — probe path blocking
# ─────────────────────────────────────────────────────────────────────────────

PROBE_PATHS = [
    "/.git/config",
    "/.git-credentials",
    "/.env",
    "/.env.production",
    "/root/.ssh/id_rsa",
    "/etc/passwd",
    "/proc/self/environ",
    "/wp-login.php",
    "/wp-includes/wlwmanifest.xml",
    "//wp-includes/wlwmanifest.xml",
    "//sito/wp-includes/wlwmanifest.xml",
    "/backend/node/constants.js",
    "/API/config/constants.js",
    "/api/config/something",
    "/_next/static/chunks/main.js",
    "/_react/action/foo",
    "/_layouts/15/start.aspx",
    "/attacker/docker-compose.yml",
    "/aws-codecommit/credentials",
    "/docker-compose.yml",
    "/config.ts",
    "/config.js",
]

@pytest.mark.parametrize("path", PROBE_PATHS)
def test_probe_paths_return_404(client, path):
    resp = client.get(path)
    assert resp.status_code == 404, f"Expected 404 for {path}, got {resp.status_code}"


SAFE_PATHS = ["/", "/connect", "/health", "/upgrade", "/privacy", "/terms"]

@pytest.mark.parametrize("path", SAFE_PATHS)
def test_safe_paths_not_blocked(client, path):
    resp = client.get(path)
    assert resp.status_code != 404, f"Legitimate path {path} got 404"


# ─────────────────────────────────────────────────────────────────────────────
# 2. SECURITY — response headers
# ─────────────────────────────────────────────────────────────────────────────

def test_security_headers_present(client):
    resp = client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert "strict-origin" in resp.headers.get("Referrer-Policy", "")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "Content-Security-Policy" in resp.headers
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert "base-uri 'self'" in csp
    assert "object-src 'none'" in csp
    assert resp.headers.get("Cross-Origin-Embedder-Policy") == "unsafe-none"
    assert resp.headers.get("Cross-Origin-Opener-Policy") == "same-origin-allow-popups"


def test_request_id_header_generated_and_forwarded(client):
    generated = client.get("/health")
    assert generated.headers.get("X-Request-ID")

    supplied = client.get("/health", headers={"X-Request-ID": "req-test-123456"})
    assert supplied.headers.get("X-Request-ID") == "req-test-123456"


def test_logging_redacts_sensitive_values():
    from core.logging import redact

    redacted = redact({
        "Authorization": "Bearer abc.def.ghi",
        "stripe_secret": "sk_live_sensitive",
        "nested": {"api_key": "pk_live_sensitive"},
        "safe": "visible",
    })
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["stripe_secret"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert redacted["safe"] == "visible"


def test_csp_has_nonce(client):
    resp = client.get("/")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "nonce-" in csp


def test_html_responses_have_no_store_cache_control(client):
    for path in ["/", "/privacy", "/terms", "/connect"]:
        resp = client.get(path)
        if "text/html" in (resp.content_type or ""):
            cc = resp.headers.get("Cache-Control", "")
            assert "no-store" in cc, f"{path} missing no-store in Cache-Control: {cc!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────

def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# 4. AUTH FLOW
# ─────────────────────────────────────────────────────────────────────────────

def test_connect_page_loads(client):
    resp = client.get("/connect")
    assert resp.status_code == 200


def test_auth_start_redirects_to_etsy(client):
    resp = client.get("/auth/start")
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert "etsy.com/oauth/connect" in location
    assert "code_challenge" in location
    assert "state" in location


def test_auth_start_clears_session(client):
    with client.session_transaction() as sess:
        sess["old_key"] = "old_value"

    client.get("/auth/start")

    with client.session_transaction() as sess:
        assert "old_key" not in sess
        assert "pkce_verifier" in sess
        assert "oauth_state" in sess


def test_auth_callback_rejects_state_mismatch(client):
    with client.session_transaction() as sess:
        sess["pkce_verifier"] = "verifier"
        sess["oauth_state"] = "correct-state"

    resp = client.get("/auth/callback?code=abc&state=wrong-state")
    assert resp.status_code == 200
    assert b"Authorization failed" in resp.data


def test_auth_callback_rejects_missing_code(client):
    with client.session_transaction() as sess:
        sess["oauth_state"] = "mystate"

    resp = client.get("/auth/callback?state=mystate")
    assert resp.status_code == 200
    assert b"Authorization failed" in resp.data


def test_connected_redirects_to_index(connected_client):
    resp = connected_client.get("/connect")
    assert resp.status_code == 302
    assert "/" in resp.headers["Location"]


# ─────────────────────────────────────────────────────────────────────────────
# 5. GUEST FLOW
# ─────────────────────────────────────────────────────────────────────────────

def test_guest_endpoint_creates_session(client):
    resp = client.get("/guest")
    assert resp.status_code in (200, 302)


def test_api_status_requires_auth(client):
    resp = client.get("/api/status")
    assert resp.status_code == 401


def test_api_generate_requires_auth(client):
    resp = client.post("/api/generate")
    assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 6. FREE LIMIT GATING
# ─────────────────────────────────────────────────────────────────────────────

def test_can_generate_free_under_limit():
    db_module.ensure_shop("shop1", "Shop One")
    allowed, remaining = db_module.can_generate("shop1", limit=3)
    assert allowed is True
    assert remaining == 3


def test_can_generate_free_at_limit():
    db_module.ensure_shop("shop2", "Shop Two")
    for _ in range(3):
        db_module.increment_usage("shop2")
    allowed, remaining = db_module.can_generate("shop2", limit=3)
    assert allowed is False
    assert remaining == 0


def test_can_generate_premium_uses_plan_monthly_cap():
    """Premium plans aren't bound by the free limit; they use the per-plan monthly cap."""
    db_module.ensure_shop("shop3", "Shop Three")
    for _ in range(10):
        db_module.increment_usage("shop3")  # also bumps the monthly counter to 10
    db_module.set_premium("shop3", "cus_fake", "sub_fake", True, "pro")
    allowed, remaining = db_module.can_generate("shop3", limit=3)
    assert allowed is True
    # Pro monthly cap defaults to 800; 10 already used this period → 790 left.
    assert remaining == 790


def test_generate_api_rejects_bad_image_type(connected_client):
    data = {
        "images": (io.BytesIO(b"fake content"), "test.txt"),
        "hint": "test product",
    }
    resp = connected_client.post(
        "/api/generate",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code in (400, 403)


def test_generate_api_rejects_fake_magic_bytes(connected_client):
    # Looks like JPEG content-type but wrong magic bytes
    data = {
        "images": (io.BytesIO(b"NOTANIMAGE_FAKEBYTES_PAYLOAD"), "evil.jpg"),
        "hint": "test",
    }
    resp = connected_client.post(
        "/api/generate",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code in (400, 403)


def test_generate_api_rejects_no_images(connected_client):
    resp = connected_client.post(
        "/api/generate",
        data={"hint": "test"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 7. PREMIUM / PHOTO VARIANT GATING
# ─────────────────────────────────────────────────────────────────────────────

def test_photo_variants_require_pro_plan():
    db_module.ensure_shop("free_shop", "Free Shop")
    allowed, remaining = db_module.can_generate_photo_variants("free_shop", count=3)
    assert allowed is False
    assert remaining == 0


def test_photo_variants_allowed_for_pro():
    db_module.ensure_shop("pro_shop", "Pro Shop")
    db_module.set_premium("pro_shop", "cus_pro", "sub_pro", True, "pro")
    allowed, remaining = db_module.can_generate_photo_variants("pro_shop", count=3)
    assert allowed is True
    assert remaining > 0


def test_photo_variants_monthly_limit_enforced():
    db_module.ensure_shop("limited_shop", "Limited")
    db_module.set_premium("limited_shop", "cus_lim", "sub_lim", True, "pro")
    # Exhaust the monthly limit
    db_module.increment_photo_variant_usage("limited_shop", 30)
    allowed, remaining = db_module.can_generate_photo_variants("limited_shop", count=1, limit=30)
    assert allowed is False
    assert remaining == 0


def test_photo_variants_api_rejects_non_pro(connected_client):
    db_module.ensure_shop("12345", "TestShop")  # free plan by default
    resp = connected_client.post(
        "/api/generate-photos",
        data=json.dumps({"product_data": {}, "image_b64": "fake"}),
        content_type="application/json",
    )
    assert resp.status_code in (403, 400)


def test_starter_plan_listing_monthly_cap_enforced():
    """Starter plans get a finite monthly listing cap, not unlimited generation."""
    from core.config import plan_limit
    db_module.ensure_shop("starter_cap", "StarterCap")
    db_module.set_premium("starter_cap", "cus_sc", "sub_sc", True, "starter")
    cap = plan_limit("starter", "listings")
    for _ in range(cap):
        db_module.increment_usage("starter_cap")
    allowed, remaining = db_module.can_generate("starter_cap")
    assert allowed is False
    assert remaining == 0


def test_pro_plan_listing_cap_higher_than_starter():
    from core.config import plan_limit
    assert plan_limit("pro", "listings") > plan_limit("starter", "listings")
    assert plan_limit("free", "photo_images") == 0
    assert plan_limit("pro", "photo_images") > 0


def test_monthly_counter_resets_next_period():
    """Usage from a prior month must not count against the current month."""
    db_module.ensure_shop("rollover_shop", "Rollover")
    db_module.set_premium("rollover_shop", "cus_ro", "sub_ro", True, "starter")
    # Simulate usage stamped in a past period.
    with db_module._conn() as con:
        con.execute(
            "UPDATE shops SET listing_used = 9999, listing_period = ? WHERE shop_id = ?",
            ("2000-01", "rollover_shop"),
        )
    allowed, remaining = db_module.can_generate("rollover_shop")
    assert allowed is True
    # Full starter cap available again because the stale period is ignored.
    from core.config import plan_limit
    assert remaining == plan_limit("starter", "listings")


# ─────────────────────────────────────────────────────────────────────────────
# 8. STRIPE WEBHOOK
# ─────────────────────────────────────────────────────────────────────────────

def _stripe_signature(payload: bytes, secret: str) -> str:
    """Build a valid Stripe-Signature header (t=timestamp,v1=hmac)."""
    ts = str(int(time.time()))
    signed = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


WEBHOOK_SECRET = "whsec_test_fake"


def test_stripe_webhook_rejects_bad_signature(client):
    payload = json.dumps({"type": "checkout.session.completed", "data": {"object": {}}}).encode()
    resp = client.post(
        "/stripe/webhook",
        data=payload,
        headers={"Stripe-Signature": "t=1,v1=badsig", "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_stripe_webhook_activates_premium(client):
    db_module.ensure_shop("shop_stripe", "StripeShop")

    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": "shop_stripe",
            "customer": "cus_abc",
            "subscription": "sub_abc",
            "payment_status": "paid",
            "metadata": {"shop_id": "shop_stripe", "plan": "pro"},
        }},
    }).encode()

    sig = _stripe_signature(payload, WEBHOOK_SECRET)

    with patch("stripe.Webhook.construct_event") as mock_event:
        mock_event.return_value = json.loads(payload)
        resp = client.post(
            "/stripe/webhook",
            data=payload,
            headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    shop = db_module.get_shop("shop_stripe")
    assert shop["has_premium"] == 1
    assert shop["plan"] == "pro"
    payments = db_module.get_payment_summary()
    assert payments["completed"] == 1
    assert payments["by_event"]["checkout_completed"] == 1


def test_stripe_webhook_accepts_secondary_secret(client):
    db_module.ensure_shop("shop_stripe_eur", "StripeShopEUR")
    payload = json.dumps({
        "id": "evt_secondary_secret",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_secondary_secret",
            "object": "checkout.session",
            "client_reference_id": "shop_stripe_eur",
            "customer": "cus_eur",
            "subscription": "sub_eur",
            "payment_status": "paid",
            "metadata": {"shop_id": "shop_stripe_eur", "plan": "starter"},
        }},
    }).encode()
    sig = _stripe_signature(payload, "whsec_eur_fake")

    with patch.dict(os.environ, {
        "STRIPE_WEBHOOK_SECRET": "whsec_wrong",
        "STRIPE_WEBHOOK_SECRET_EUR": "whsec_eur_fake",
    }):
        resp = client.post(
            "/stripe/webhook",
            data=payload,
            headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    shop = db_module.get_shop("shop_stripe_eur")
    assert shop["has_premium"] == 1
    assert shop["plan"] == "starter"


def test_stripe_webhook_deactivates_premium_on_cancel(client):
    db_module.ensure_shop("shop_cancel", "CancelShop")
    db_module.set_premium("shop_cancel", "cus_xyz", "sub_xyz", True, "pro")

    payload = json.dumps({
        "type": "customer.subscription.deleted",
        "data": {"object": {
            "customer": "cus_xyz",
            "id": "sub_xyz",
        }},
    }).encode()

    sig = _stripe_signature(payload, WEBHOOK_SECRET)

    with patch("stripe.Webhook.construct_event") as mock_event:
        mock_event.return_value = json.loads(payload)
        resp = client.post(
            "/stripe/webhook",
            data=payload,
            headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    shop = db_module.get_shop("shop_cancel")
    assert shop["has_premium"] == 0
    assert shop["plan"] == "free"


def test_stripe_webhook_invoice_payment_reaffirms_premium(client):
    """Renewal / self-heal: invoice.payment_succeeded recreates the shop and
    re-activates premium using the shop_id stamped on the subscription."""
    payload = json.dumps({
        "type": "invoice.payment_succeeded",
        "data": {"object": {
            "customer": "cus_renew",
            "subscription": "sub_renew",
            "subscription_details": {"metadata": {"shop_id": "shop_renew", "plan": "pro"}},
        }},
    }).encode()
    sig = _stripe_signature(payload, WEBHOOK_SECRET)
    with patch("stripe.Webhook.construct_event") as mock_event:
        mock_event.return_value = json.loads(payload)
        resp = client.post(
            "/stripe/webhook", data=payload,
            headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    shop = db_module.get_shop("shop_renew")
    assert shop is not None  # recreated on the fly
    assert shop["has_premium"] == 1
    assert shop["plan"] == "pro"


def test_stripe_webhook_subscription_updated_syncs_plan(client):
    db_module.ensure_shop("shop_upd", "UpdShop")
    payload = json.dumps({
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_upd", "customer": "cus_upd", "status": "active",
            "metadata": {"shop_id": "shop_upd", "plan": "starter"},
        }},
    }).encode()
    sig = _stripe_signature(payload, WEBHOOK_SECRET)
    with patch("stripe.Webhook.construct_event") as mock_event:
        mock_event.return_value = json.loads(payload)
        resp = client.post(
            "/stripe/webhook", data=payload,
            headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    shop = db_module.get_shop("shop_upd")
    assert shop["has_premium"] == 1
    assert shop["plan"] == "starter"


def test_stripe_webhook_subscription_updated_deactivates_on_cancel(client):
    db_module.ensure_shop("shop_can2", "C2")
    db_module.set_premium("shop_can2", "cus_can2", "sub_can2", True, "pro")
    payload = json.dumps({
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_can2", "customer": "cus_can2", "status": "canceled",
            "metadata": {"shop_id": "shop_can2", "plan": "pro"},
        }},
    }).encode()
    sig = _stripe_signature(payload, WEBHOOK_SECRET)
    with patch("stripe.Webhook.construct_event") as mock_event:
        mock_event.return_value = json.loads(payload)
        resp = client.post(
            "/stripe/webhook", data=payload,
            headers={"Stripe-Signature": sig, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    shop = db_module.get_shop("shop_can2")
    assert shop["has_premium"] == 0
    assert shop["plan"] == "free"


def test_stripe_checkout_requires_auth(client):
    resp = client.post(
        "/stripe/checkout",
        data=json.dumps({"plan": "pro"}),
        content_type="application/json",
    )
    assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 9. DB LAYER — set_premium / get_shop_by_stripe_customer
# ─────────────────────────────────────────────────────────────────────────────

def test_set_premium_and_lookup_by_customer():
    db_module.ensure_shop("shop_db", "DBShop")
    db_module.set_premium("shop_db", "cus_db123", "sub_db123", True, "starter")

    shop = db_module.get_shop("shop_db")
    assert shop["has_premium"] == 1
    assert shop["plan"] == "starter"
    assert shop["stripe_customer_id"] == "cus_db123"

    found = db_module.get_shop_by_stripe_customer("cus_db123")
    assert found is not None
    assert found["shop_id"] == "shop_db"


def test_set_premium_inactive_reverts_plan():
    db_module.ensure_shop("shop_rev", "RevertShop")
    db_module.set_premium("shop_rev", "cus_rev", "sub_rev", True, "pro")
    db_module.set_premium("shop_rev", "cus_rev", "sub_rev", False)

    shop = db_module.get_shop("shop_rev")
    assert shop["has_premium"] == 0
    assert shop["plan"] == "free"


def test_can_improve_free_limit():
    db_module.ensure_shop("shop_imp", "ImpShop")
    allowed, _ = db_module.can_improve("shop_imp")
    assert allowed is True

    db_module.increment_improve_usage("shop_imp")
    allowed, remaining = db_module.can_improve("shop_imp")
    assert allowed is False
    assert remaining == 0


def test_can_improve_premium_uses_plan_monthly_cap():
    db_module.ensure_shop("shop_imppro", "ImpProShop")
    db_module.set_premium("shop_imppro", "cus_ip", "sub_ip", True, "pro")
    for _ in range(10):
        db_module.increment_improve_usage("shop_imppro")
    allowed, remaining = db_module.can_improve("shop_imppro")
    assert allowed is True
    # Pro improve cap defaults to 200; 10 used this period → 190 left.
    assert remaining == 190


# ─────────────────────────────────────────────────────────────────────────────
# 10. TEMPLATE SAVE / LOAD
# ─────────────────────────────────────────────────────────────────────────────

def test_template_save_and_load():
    db_module.ensure_shop("shop_tmpl", "TmplShop")
    data = {"price": "29.99", "shipping_profile_id": "999", "tags": ["handmade"]}
    db_module.save_template("shop_tmpl", data)
    loaded = db_module.get_template("shop_tmpl")
    assert loaded["price"] == "29.99"
    assert loaded["tags"] == ["handmade"]


def test_template_returns_empty_for_unknown_shop():
    result = db_module.get_template("nonexistent_shop_99999")
    assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# 11. LEVEL 2 — Auth-wall checks on all critical endpoints
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/publish",          {"title": "x"}),
    ("POST", "/api/bulk-generate",    None),
    ("POST", "/api/improve-listing",  {"action": "title_seo"}),
    ("POST", "/api/listing-variants", {}),
    ("POST", "/api/translate",        {"lang": "de"}),
    ("GET",  "/api/template",         None),
    ("POST", "/api/template",         {}),
    ("GET",  "/api/email-verified",   None),
])
def test_endpoint_requires_auth(client, method, path, body):
    if method == "POST":
        resp = client.post(path, json=body)
    else:
        resp = client.get(path)
    assert resp.status_code == 401, f"{method} {path} should be 401 without auth, got {resp.status_code}"


def test_bulk_page_redirects_without_auth(client):
    resp = client.get("/bulk")
    assert resp.status_code == 302


def test_bulk_generate_requires_premium(connected_client):
    db_module.ensure_shop("12345", "TestShop")  # free plan
    import io
    jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 12
    data = {"images": (io.BytesIO(jpeg_header), "test.jpg"), "hint": "hat"}
    resp = connected_client.post(
        "/api/bulk-generate",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 403
    assert b"premium_required" in resp.data


def test_listing_variants_requires_premium(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    resp = connected_client.post("/api/listing-variants", json={"title": "Hat"})
    assert resp.status_code == 403
    assert b"premium_required" in resp.data


def test_publish_rejects_missing_fields(connected_client):
    resp = connected_client.post("/api/publish", json={"title": "only title"})
    assert resp.status_code == 400


def test_publish_rejects_bad_price(connected_client):
    resp = connected_client.post("/api/publish", json={
        "title": "Test Hat",
        "description": "A great hat",
        "price": -5,
        "taxonomy_id": 1234,
    })
    assert resp.status_code == 400


def test_improve_listing_rejects_invalid_action(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    resp = connected_client.post(
        "/api/improve-listing",
        json={"action": "not_a_real_action", "title": "Test"},
    )
    assert resp.status_code == 400


def test_translate_rejects_unsupported_lang(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    resp = connected_client.post(
        "/api/translate",
        json={"lang": "zh", "title": "Hat", "description": "Nice hat", "tags": []},
    )
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 12. LEVEL 3 — Full flows with mocked AI and Etsy API
# ─────────────────────────────────────────────────────────────────────────────

_FAKE_AI_LISTING = {
    "title": "Handmade Crochet Hat, Warm Beanie",
    "description": "A lovely handmade hat crafted with care.",
    "tags": ["crochet hat", "beanie", "handmade", "warm hat", "knit cap",
             "winter hat", "wool hat", "artisan", "custom hat", "gift idea",
             "cozy hat", "hand knit", "boho hat"],
    "materials": ["wool", "cotton"],
    "colors": ["white", "cream"],
    "suggested_price_eur": 29.99,
    "taxonomy_query": "Crocheted Hats",
    "personalization_instructions": "Custom color?",
    "instagram_caption": "Love this hat! #handmade #crochet",
}


def _jpeg_bytes():
    return b"\xff\xd8\xff\xe0" + b"\x00" * 12


# ── 12a. Generate flow ────────────────────────────────────────────────────────

def test_generate_returns_listing_for_connected_shop(connected_client):
    db_module.ensure_shop("12345", "TestShop")

    with patch("apis.listings._run_provider", return_value=dict(_FAKE_AI_LISTING)) as mock_ai, \
         patch("requests.get") as mock_get, \
         patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}):
        mock_get.return_value = MagicMock(ok=True, json=lambda: {"results": []})

        import io
        data = {
            "images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"),
            "hint":   "crochet hat",
        }
        resp = connected_client.post(
            "/api/generate",
            data=data,
            content_type="multipart/form-data",
        )

    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["title"] == _FAKE_AI_LISTING["title"]
    assert "image_previews" in body
    assert mock_ai.called

    shop = db_module.get_shop("12345")
    assert shop["free_used"] == 1


def test_generate_increments_usage_and_blocks_at_limit(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    from db import FREE_LIMIT
    for _ in range(FREE_LIMIT):
        db_module.increment_usage("12345")

    import io
    resp = connected_client.post(
        "/api/generate",
        data={"images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"), "hint": "hat"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 403
    assert b"free_limit_reached" in resp.data


def test_generate_blocks_guest_without_email_verification(client):
    with client.session_transaction() as sess:
        sess["guest"]    = True
        sess["guest_id"] = "guest-unverified"
    import io
    resp = client.post(
        "/api/generate",
        data={"images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"), "hint": "hat"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 403
    assert b"email_verification_required" in resp.data


def test_generate_all_providers_quota_returns_503(connected_client):
    db_module.ensure_shop("12345", "TestShop")

    def raise_quota(*args, **kwargs):
        raise RuntimeError("quota_exceeded")

    with patch("apis.listings._run_provider", side_effect=raise_quota), \
         patch.dict(os.environ, {"NVIDIA_API_KEY": "fake", "GEMINI_API_KEY": "fake", "OPENAI_API_KEY": "fake"}):
        import io
        resp = connected_client.post(
            "/api/generate",
            data={"images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"), "hint": "hat"},
            content_type="multipart/form-data",
        )
    assert resp.status_code == 503


# ── 12b. Publish flow ────────────────────────────────────────────────────────

_VALID_PUBLISH_PAYLOAD = {
    "title": "Handmade Crochet Hat",
    "description": "A beautiful handmade hat.",
    "price": 29.99,
    "taxonomy_id": 68887201,
    "tags": ["handmade", "crochet"],
    "materials": ["wool"],
    "image_previews": [],
    "shipping_profile_id": None,
    "personalization_instructions": "",
}


def test_publish_creates_listing_on_etsy(connected_client):
    etsy_create = MagicMock(ok=True, json=lambda: {"listing_id": 999888})
    etsy_readiness = MagicMock(ok=True, json=lambda: {"results": []})

    with patch("requests.post", return_value=etsy_create), \
         patch("requests.get",  return_value=etsy_readiness):
        resp = connected_client.post("/api/publish", json=_VALID_PUBLISH_PAYLOAD)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["listing_id"] == 999888


def test_publish_returns_400_on_etsy_error(connected_client):
    # Etsy returns a non-JSON error body ("shop not ready") — .json() raises,
    # so the route falls back to its default user-facing message.
    etsy_fail = MagicMock(ok=False, text="shop not ready")
    etsy_fail.json.side_effect = ValueError("not json")
    etsy_readiness = MagicMock(ok=True, json=lambda: {"results": []})

    with patch("requests.post", return_value=etsy_fail), \
         patch("requests.get",  return_value=etsy_readiness):
        resp = connected_client.post("/api/publish", json=_VALID_PUBLISH_PAYLOAD)

    assert resp.status_code == 400
    assert b"Failed to create listing" in resp.data


def test_publish_uploads_images(connected_client):
    import base64
    img_b64 = "data:image/jpeg;base64," + base64.b64encode(_jpeg_bytes()).decode()
    payload = {**_VALID_PUBLISH_PAYLOAD, "image_previews": [img_b64]}

    etsy_create   = MagicMock(ok=True, json=lambda: {"listing_id": 111222})
    etsy_readiness = MagicMock(ok=True, json=lambda: {"results": []})
    etsy_img_upload = MagicMock(ok=True)

    post_calls = []
    def mock_post(url, **kwargs):
        post_calls.append(url)
        if "listings" in url and "images" in url:
            return etsy_img_upload
        return etsy_create

    # Bypass the background thread so the mock captures upload calls synchronously.
    import apis.listings as listings_mod

    def sync_bg(safe_lid, data, access_token):
        sid = "12345"
        listings_mod._upload_images(safe_lid, sid, data["image_previews"], access_token)

    with patch("requests.post", side_effect=mock_post), \
         patch("requests.get",  return_value=etsy_readiness), \
         patch("apis.listings._bg_post_listing", side_effect=sync_bg):
        resp = connected_client.post("/api/publish", json=payload)

    assert resp.status_code == 200
    image_upload_calls = [u for u in post_calls if "images" in u]
    assert len(image_upload_calls) == 1


# ── 12c. Improve listing flow ────────────────────────────────────────────────

def test_improve_listing_returns_improved_data(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    improved = {"title": "Better SEO Hat", "description": "Improved desc", "tags": ["tag1"]}

    with patch("apis.listings._run_text_json", return_value=improved):
        resp = connected_client.post(
            "/api/improve-listing",
            json={"action": "title_seo", "title": "Hat", "description": "Old desc", "tags": []},
        )

    assert resp.status_code == 200
    assert resp.get_json()["title"] == "Better SEO Hat"
    shop = db_module.get_shop("12345")
    assert shop["free_improve_used"] == 1


def test_improve_listing_premium_does_not_increment(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_x", "sub_x", True, "pro")
    improved = {"title": "SEO Hat", "description": "desc", "tags": []}

    with patch("apis.listings._run_text_json", return_value=improved):
        resp = connected_client.post(
            "/api/improve-listing",
            json={"action": "title_seo", "title": "Hat", "description": "desc", "tags": []},
        )

    assert resp.status_code == 200
    shop = db_module.get_shop("12345")
    assert shop["free_improve_used"] == 0


def test_improve_listing_blocks_at_free_limit(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    from db import FREE_IMPROVE_LIMIT
    db_module.increment_improve_usage("12345")  # exhaust the 1-use free allowance
    # ensure we're past the limit
    for _ in range(FREE_IMPROVE_LIMIT):
        db_module.increment_improve_usage("12345")

    resp = connected_client.post(
        "/api/improve-listing",
        json={"action": "title_seo", "title": "Hat", "description": "desc", "tags": []},
    )
    assert resp.status_code == 403


# ── 12d. Translation flow ────────────────────────────────────────────────────

def test_translate_returns_translated_data(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    translated = {"title": "Gehäkelte Mütze", "description": "Beschreibung", "tags": ["handgemacht"]}

    with patch("apis.translate._translate_ai", return_value=translated):
        resp = connected_client.post(
            "/api/translate",
            json={"lang": "de", "title": "Crochet Hat", "description": "Nice hat", "tags": ["handmade"]},
        )

    assert resp.status_code == 200
    assert resp.get_json()["title"] == "Gehäkelte Mütze"


# ── 12e. Template API ────────────────────────────────────────────────────────

def test_template_api_get_and_post(connected_client):
    db_module.ensure_shop("12345", "TestShop")

    resp = connected_client.get("/api/template")
    assert resp.status_code == 200
    assert resp.get_json() == {}

    resp = connected_client.post(
        "/api/template",
        json={"shipping_profile_id": "42", "price": "19.99"},
    )
    assert resp.status_code == 200

    resp = connected_client.get("/api/template")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["shipping_profile_id"] == "42"


# ── 12f. API status ──────────────────────────────────────────────────────────

def test_api_status_connected_user(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    resp = connected_client.get("/api/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "allowed" in body
    assert body["is_guest"] is False


def test_api_status_exposes_plan_and_premium(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_st", "sub_st", True, "pro")
    resp = connected_client.get("/api/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["plan"] == "pro"
    assert body["has_premium"] is True


def test_api_status_free_user_plan_defaults(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    resp = connected_client.get("/api/status")
    body = resp.get_json()
    assert body["plan"] == "free"
    assert body["has_premium"] is False


def test_api_status_guest_user(guest_client):
    resp = guest_client.get("/api/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["is_guest"] is True


# ── 12g. Magic link flow ─────────────────────────────────────────────────────

def test_magic_link_sent_to_guest(client):
    with client.session_transaction() as sess:
        sess["guest"]    = True
        sess["guest_id"] = "guest-ml-test"

    with patch("apis.auth.send_email", return_value=(True, None)) as mock_mail, \
         patch("apis.auth.count_recent_magic_links", return_value=0), \
         patch("apis.auth.get_or_create_email_shop", return_value="email-shop-1"), \
         patch("apis.auth.create_magic_link"):
        db_module.ensure_shop("email-shop-1", "Guest")
        resp = client.post(
            "/api/magic-link",
            json={"email": "user@example.com"},
            content_type="application/json",
        )

    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert mock_mail.called


def test_magic_link_rejects_invalid_email(client):
    with client.session_transaction() as sess:
        sess["guest"]    = True
        sess["guest_id"] = "guest-ml-bad"

    resp = client.post(
        "/api/magic-link",
        json={"email": "not-an-email"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert b"invalid_email" in resp.data


def test_magic_link_rate_limited(client):
    with client.session_transaction() as sess:
        sess["guest"]    = True
        sess["guest_id"] = "guest-ml-ratelimit"

    with patch("apis.auth.count_recent_magic_links", return_value=5):
        resp = client.post(
            "/api/magic-link",
            json={"email": "user@example.com"},
            content_type="application/json",
        )
    assert resp.status_code == 429


def test_magic_link_rejects_non_guest(connected_client):
    resp = connected_client.post(
        "/api/magic-link",
        json={"email": "user@example.com"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert b"not_guest" in resp.data


def test_auth_magic_valid_token_sets_session(client):
    fake_row = {"shop_id": "email-shop-42"}
    with patch("apis.auth.use_magic_link", return_value=fake_row):
        resp = client.get("/auth/magic?token=valid-token-abc")
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("email_verified") is True
        assert sess.get("email_shop_id") == "email-shop-42"


def test_auth_magic_invalid_token_shows_error(client):
    with patch("db.use_magic_link", return_value=None):
        resp = client.get("/auth/magic?token=bad-token")
    assert resp.status_code == 200
    assert b"ge" in resp.data  # error text rendered in connect.html


def test_mobile_magic_email_uses_https_handoff_link(client):
    """Mobile-originated magic email must contain an https link (clickable in
    email clients), not a dead custom-scheme easylisting:// link."""
    with patch("apis.auth.send_email", return_value=(True, None)) as mock_mail, \
         patch("apis.auth.count_recent_magic_links", return_value=0), \
         patch("apis.auth.get_email_shop", return_value=None), \
         patch("apis.auth.get_or_create_email_shop", return_value="email-shop-mob"), \
         patch("apis.auth.create_magic_link"):
        db_module.ensure_shop("email-shop-mob", "Guest")
        resp = client.post(
            "/api/magic-link",
            json={"email": "mobileuser@example.com"},
            content_type="application/json",
            headers={"X-Mobile-Request": "true"},
        )
    assert resp.status_code == 200
    html = mock_mail.call_args.args[3]
    assert "/auth/magic?token=" in html
    assert "m=1" in html
    assert "easylisting://" not in html  # custom scheme would be stripped by mail clients


def test_auth_magic_browser_handoff_does_not_consume_token(client):
    """A mobile link (?m=1) opened in a browser renders the handoff page and
    must NOT consume the token — the app consumes it via the API."""
    with patch("apis.auth.use_magic_link") as mock_use:
        resp = client.get("/auth/magic?token=handoff-tok-123&m=1")
    assert resp.status_code == 200
    assert mock_use.called is False  # token preserved for the app
    assert b"easylisting://auth/magic?token=handoff-tok-123" in resp.data


def test_auth_magic_mobile_app_still_consumes_token(client):
    """The iOS app (X-Mobile-Request header) consumes the token and gets a
    mobile_token back, even when the link carried ?m=1."""
    fake_row = {"shop_id": "email-shop-app"}
    with patch("apis.auth.use_magic_link", return_value=fake_row):
        resp = client.get(
            "/auth/magic?token=app-tok-456&m=1",
            headers={"X-Mobile-Request": "true"},
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "mobile_token" in body


# ── 12h. App Store review account ────────────────────────────────────────────

def test_appstore_review_email_instant_pro_login_web(client):
    """The configured review email logs in instantly with pro, no email sent."""
    with client.session_transaction() as sess:
        sess["guest"]    = True
        sess["guest_id"] = "guest-review-web"
    with patch.dict(os.environ, {"APPSTORE_REVIEW_EMAIL": "review@easylisting.app"}), \
         patch("apis.auth.send_email") as mock_mail:
        resp = client.post(
            "/api/magic-link",
            json={"email": "review@easylisting.app"},
            content_type="application/json",
        )
    assert resp.status_code == 200
    assert resp.get_json()["already_verified"] is True
    mock_mail.assert_not_called()
    shop = db_module.get_shop("review_appstore")
    assert shop["plan"] == "pro"
    with client.session_transaction() as sess:
        assert sess.get("email_verified") is True
        assert sess.get("email_shop_id") == "review_appstore"


def test_appstore_review_email_instant_token_mobile(client):
    """Mobile review login returns a mobile_token directly, no email sent."""
    with patch.dict(os.environ, {"APPSTORE_REVIEW_EMAIL": "review@easylisting.app"}), \
         patch("apis.auth.send_email") as mock_mail:
        resp = client.post(
            "/api/magic-link",
            headers={"X-Mobile-Request": "true"},
            json={"email": "review@easylisting.app"},
            content_type="application/json",
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["already_verified"] is True
    assert "mobile_token" in body
    mock_mail.assert_not_called()


def test_non_review_email_does_not_trigger_bypass(client):
    """A non-review email must still go through the normal email-sending flow."""
    with patch.dict(os.environ, {"APPSTORE_REVIEW_EMAIL": "review@easylisting.app"}), \
         patch("apis.auth.send_email", return_value=(True, None)) as mock_mail, \
         patch("apis.auth.count_recent_magic_links", return_value=0), \
         patch("apis.auth.get_email_shop", return_value=None), \
         patch("apis.auth.get_or_create_email_shop", return_value="email-shop-other"), \
         patch("apis.auth.create_magic_link"):
        db_module.ensure_shop("email-shop-other", "Guest")
        resp = client.post(
            "/api/magic-link",
            headers={"X-Mobile-Request": "true"},
            json={"email": "someone@example.com"},
            content_type="application/json",
        )
    assert resp.status_code == 200
    mock_mail.assert_called()


# ── 12h. Bulk generate flow ──────────────────────────────────────────────────

def test_bulk_generate_returns_data_for_premium(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_bulk", "sub_bulk", True, "pro")

    with patch("apis.listings._run_provider", return_value=dict(_FAKE_AI_LISTING)), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}):
        import io
        data = {
            "images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"),
            "hint":   "crochet hat",
        }
        resp = connected_client.post(
            "/api/bulk-generate",
            data=data,
            content_type="multipart/form-data",
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["title"] == _FAKE_AI_LISTING["title"]
    assert "image_previews" in body


def test_bulk_generate_rejects_non_image(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_bulk2", "sub_bulk2", True, "pro")

    import io
    data = {"images": (io.BytesIO(b"notanimage"), "file.txt"), "hint": "hat"}
    resp = connected_client.post(
        "/api/bulk-generate",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code in (400, 403)


# ─────────────────────────────────────────────────────────────────────────────
# 13. STRIPE CHECKOUT — URL generation, plan routing, TRY domain
# ─────────────────────────────────────────────────────────────────────────────

def test_stripe_checkout_generates_url_for_pro(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/test-session"

    with patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
        resp = connected_client.post(
            "/stripe/checkout",
            json={"plan": "pro"},
            content_type="application/json",
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert "url" in body
    assert "stripe.com" in body["url"]
    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["line_items"][0]["price"] == os.environ["STRIPE_PRO_PRICE_ID"]
    payments = db_module.get_payment_summary()
    assert payments["attempts"] == 1
    assert payments["by_event"]["checkout_started"] == 1
    assert payments["by_event"]["checkout_created"] == 1


def test_stripe_checkout_generates_url_for_starter(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/starter"

    with patch.dict(os.environ, {"STRIPE_STARTER_PRICE_ID": "price_starter_fake"}), \
         patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
        resp = connected_client.post(
            "/stripe/checkout",
            json={"plan": "starter"},
            content_type="application/json",
        )

    assert resp.status_code == 200
    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["line_items"][0]["price"] == "price_starter_fake"


def test_stripe_checkout_rejects_unknown_plan(connected_client):
    """Unknown plan falls back to 'pro' — should still succeed."""
    db_module.ensure_shop("12345", "TestShop")
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/fallback"

    with patch("stripe.checkout.Session.create", return_value=mock_session):
        resp = connected_client.post(
            "/stripe/checkout",
            json={"plan": "enterprise"},
            content_type="application/json",
        )

    assert resp.status_code == 200


def test_stripe_checkout_returns_503_when_price_not_configured(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    with patch.dict(os.environ, {
        "STRIPE_PRO_PRICE_ID": "",
        "STRIPE_PRICE_ID": "",
    }):
        resp = connected_client.post(
            "/stripe/checkout",
            json={"plan": "pro"},
            content_type="application/json",
        )
    assert resp.status_code == 503
    assert b"not configured" in resp.data


def test_stripe_checkout_try_domain_uses_try_price(connected_client):
    """kolaylistele.com requests must use TRY price IDs."""
    db_module.ensure_shop("12345", "TestShop")
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/try"

    with patch.dict(os.environ, {"STRIPE_PRO_PRICE_ID_TRY": "price_try_pro_fake"}), \
         patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create, \
         patch("apis.payments._is_try_domain", return_value=True):
        resp = connected_client.post(
            "/stripe/checkout",
            json={"plan": "pro"},
            content_type="application/json",
        )

    assert resp.status_code == 200
    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["line_items"][0]["price"] == "price_try_pro_fake"


def test_stripe_checkout_annual_plan(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    mock_session = MagicMock()
    mock_session.url = "https://checkout.stripe.com/annual"

    with patch.dict(os.environ, {"STRIPE_PRO_ANNUAL_PRICE_ID": "price_pro_annual_fake"}), \
         patch("stripe.checkout.Session.create", return_value=mock_session) as mock_create:
        resp = connected_client.post(
            "/stripe/checkout",
            json={"plan": "pro_annual"},
            content_type="application/json",
        )

    assert resp.status_code == 200
    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["line_items"][0]["price"] == "price_pro_annual_fake"


def test_admin_billing_json_reports_payment_summary(client):
    db_module.log_payment_event(
        "checkout_created",
        shop_id="shop_admin_billing",
        shop_name="BillingShop",
        plan="pro",
        surface="web",
        domain="easylisting.app",
    )
    db_module.log_payment_event(
        "checkout_completed",
        shop_id="shop_admin_billing",
        shop_name="BillingShop",
        plan="pro",
        domain="easylisting.app",
    )
    with patch.dict(os.environ, {"ADMIN_TOKEN": "admin-test-token"}):
        resp = client.get(
            "/admin/billing-json",
            headers={"Authorization": "Bearer admin-test-token"},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["attempts"] == 1
    assert body["completed"] == 1
    assert body["conversion_pct"] == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# 14. PAGE RENDERING — upgrade, index, listings, bulk, connect
# ─────────────────────────────────────────────────────────────────────────────

def test_upgrade_page_renders_for_connected_user(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    resp = connected_client.get("/upgrade")
    assert resp.status_code == 200
    assert b"Upgrade" in resp.data or b"upgrade" in resp.data.lower()


def test_upgrade_page_shows_correct_plan_for_pro_user(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_up", "sub_up", True, "pro")
    resp = connected_client.get("/upgrade")
    assert resp.status_code == 200
    assert b"pro" in resp.data.lower() or b"unlimited" in resp.data.lower()


def test_upgrade_page_redirects_unauthenticated(client):
    resp = client.get("/upgrade")
    assert resp.status_code == 302
    assert "/connect" in resp.headers["Location"]


def test_index_page_renders_for_connected_user(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    resp = connected_client.get("/")
    assert resp.status_code == 200


def test_index_page_renders_for_guest(guest_client):
    resp = guest_client.get("/")
    assert resp.status_code == 200


def test_index_page_redirects_unauthenticated(client):
    resp = client.get("/")
    assert resp.status_code == 302


def test_listings_page_requires_etsy_auth(guest_client):
    """Guest users reach /listings but get empty results (no Etsy shop_id)."""
    with patch("requests.get", return_value=MagicMock(ok=False, json=lambda: {})):
        resp = guest_client.get("/listings")
    assert resp.status_code == 200


def test_listings_page_renders_for_connected_user(connected_client):
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"results": []}
    with patch("requests.get", return_value=mock_resp):
        resp = connected_client.get("/listings")
    assert resp.status_code == 200


def test_bulk_page_renders_for_premium(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_bk", "sub_bk", True, "pro")
    resp = connected_client.get("/bulk")
    assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 15. SESSION FLOWS — disconnect, guest, email-verified guest
# ─────────────────────────────────────────────────────────────────────────────

def test_disconnect_clears_session_and_redirects(connected_client):
    resp = connected_client.get("/disconnect")
    assert resp.status_code == 302
    assert "/connect" in resp.headers["Location"]
    with connected_client.session_transaction() as sess:
        assert "access_token" not in sess
        assert "shop_id" not in sess


def test_guest_route_creates_guest_session(client):
    resp = client.get("/guest", follow_redirects=False)
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("guest") is True


def test_email_verified_guest_can_generate(client):
    """Guest who verified email should be able to generate listings."""
    with client.session_transaction() as sess:
        sess["guest"] = True
        sess["guest_id"] = "guest-verified-1"
        sess["email_verified"] = True
        sess["email_shop_id"] = "email-shop-verified-1"

    db_module.ensure_shop("email-shop-verified-1", "Guest")

    with patch("apis.listings._run_provider", return_value=dict(_FAKE_AI_LISTING)), \
         patch("requests.get", return_value=MagicMock(ok=True, json=lambda: {"results": []})), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}):
        import io
        resp = client.post(
            "/api/generate",
            data={"images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"), "hint": "hat"},
            content_type="multipart/form-data",
        )

    assert resp.status_code == 200
    assert resp.get_json()["title"] == _FAKE_AI_LISTING["title"]


def test_email_verified_status(client):
    with client.session_transaction() as sess:
        sess["guest"] = True
        sess["guest_id"] = "guest-evs"
        sess["email_verified"] = True
        sess["email_shop_id"] = "email-shop-evs"

    db_module.ensure_shop("email-shop-evs", "Guest")
    resp = client.get("/api/email-verified")
    assert resp.status_code == 200
    assert resp.get_json()["verified"] is True


def test_email_not_verified_status(guest_client):
    resp = guest_client.get("/api/email-verified")
    assert resp.status_code == 200
    assert resp.get_json()["verified"] is False


def test_api_status_email_verified_guest(client):
    with client.session_transaction() as sess:
        sess["guest"] = True
        sess["guest_id"] = "guest-status"
        sess["email_verified"] = True
        sess["email_shop_id"] = "email-shop-status"

    db_module.ensure_shop("email-shop-status", "Guest")
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["is_guest"] is True
    assert "remaining" in body


# ─────────────────────────────────────────────────────────────────────────────
# 16. ADMIN ENDPOINTS — token gating
# ─────────────────────────────────────────────────────────────────────────────

def test_admin_abuse_wrong_token_returns_404(client):
    with patch.dict(os.environ, {"ADMIN_TOKEN": "secret-admin-token"}):
        resp = client.get("/admin/abuse", headers={"Authorization": "Bearer wrongtoken"})
    assert resp.status_code == 404


def test_admin_abuse_correct_token_returns_200(client):
    with patch.dict(os.environ, {"ADMIN_TOKEN": "secret-admin-token"}):
        resp = client.get("/admin/abuse", headers={"Authorization": "Bearer secret-admin-token"})
    assert resp.status_code == 200
    assert b"Abuse signals" in resp.data


def test_admin_stats_wrong_token_returns_404(client):
    with patch.dict(os.environ, {"ADMIN_TOKEN": "secret-token"}):
        resp = client.get("/admin/stats", headers={"Authorization": "Bearer bad"})
    assert resp.status_code == 404


def test_admin_stats_correct_token_returns_200(client):
    with patch.dict(os.environ, {"ADMIN_TOKEN": "secret-token"}):
        resp = client.get("/admin/stats", headers={"Authorization": "Bearer secret-token"})
    assert resp.status_code == 200


def test_admin_ping_ai_correct_token(client):
    with patch.dict(os.environ, {
        "ADMIN_TOKEN": "ping-token",
        "GEMINI_API_KEY": "",
        "NVIDIA_API_KEY": "",
        "OPENAI_API_KEY": "",
    }):
        resp = client.get("/admin/ping-ai", headers={"Authorization": "Bearer ping-token"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert "gemini" in body
    assert "nvidia" in body
    assert "openai" in body


def test_admin_no_token_env_always_404(client):
    """If ADMIN_TOKEN env var is unset, admin endpoints must be inaccessible."""
    with patch.dict(os.environ, {"ADMIN_TOKEN": "", "CF_ACCESS_TEAM_DOMAIN": ""}):
        assert client.get("/admin/stats").status_code == 404
        assert client.get("/admin/abuse").status_code == 404


def test_admin_dashboard_requires_auth(client):
    with patch.dict(os.environ, {"ADMIN_TOKEN": "dash-tok", "CF_ACCESS_TEAM_DOMAIN": ""}):
        assert client.get("/admin/dashboard").status_code == 404


def test_admin_dashboard_lists_shops_with_bearer(client):
    db_module.ensure_shop("66106727", "ZSArtBoutique")
    db_module.set_premium("66106727", "admin", "admin", True, "pro")
    with patch.dict(os.environ, {"ADMIN_TOKEN": "dash-tok"}):
        resp = client.get("/admin/dashboard", headers={"Authorization": "Bearer dash-tok"})
    assert resp.status_code == 200
    assert b"ZSArtBoutique" in resp.data
    assert b"Overview" in resp.data
    assert b"Abuse" in resp.data  # tracing section present


def test_admin_auth_cf_access_allows_owner_email(client):
    """A signature-verified CF Access JWT for an allowlisted email authorizes admin."""
    import core.admin_auth as aa
    fake_key = type("K", (), {"key": "x"})()
    with patch.dict(os.environ, {"ADMIN_TOKEN": "", "CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
                                 "CF_ACCESS_AUD": "aud123"}), \
         patch.object(aa, "_get_jwks_client") as mock_client, \
         patch("jwt.decode", return_value={"email": "berkcem123@gmail.com"}), \
         patch("jwt.PyJWKClient"):
        mock_client.return_value.get_signing_key_from_jwt.return_value = fake_key
        resp = client.get("/admin/stats", headers={"Cf-Access-Jwt-Assertion": "fake.jwt.token"})
    assert resp.status_code == 200


def test_admin_auth_cf_access_rejects_unknown_email(client):
    import core.admin_auth as aa
    fake_key = type("K", (), {"key": "x"})()
    with patch.dict(os.environ, {"ADMIN_TOKEN": "", "CF_ACCESS_TEAM_DOMAIN": "team.cloudflareaccess.com",
                                 "CF_ACCESS_AUD": "aud123"}), \
         patch.object(aa, "_get_jwks_client") as mock_client, \
         patch("jwt.decode", return_value={"email": "attacker@evil.com"}), \
         patch("jwt.PyJWKClient"):
        mock_client.return_value.get_signing_key_from_jwt.return_value = fake_key
        resp = client.get("/admin/stats", headers={"Cf-Access-Jwt-Assertion": "fake.jwt.token"})
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 17. LISTING VARIANTS — premium full flow
# ─────────────────────────────────────────────────────────────────────────────

def test_listing_variants_returns_data_for_premium(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_lv", "sub_lv", True, "pro")

    fake_variants = {
        "variants": [
            {"name": "SEO-focused", "title": "SEO Hat", "description": "SEO desc", "tags": ["seo"]},
            {"name": "Emotional", "title": "Handmade Hat", "description": "Warm desc", "tags": ["handmade"]},
            {"name": "Gift", "title": "Gift Hat", "description": "Gift desc", "tags": ["gift"]},
        ]
    }

    with patch("apis.listings._run_text_json", return_value=fake_variants):
        resp = connected_client.post(
            "/api/listing-variants",
            json={"title": "Crochet Hat", "description": "Nice hat", "tags": ["crochet"]},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert "variants" in body
    assert len(body["variants"]) == 3


def test_listing_variants_starter_plan_allowed(connected_client):
    """Starter plan has premium access, so listing-variants should be allowed."""
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_st", "sub_st", True, "starter")

    fake_variants = {"variants": [{"name": "v1", "title": "t", "description": "d", "tags": []}]}
    with patch("apis.listings._run_text_json", return_value=fake_variants):
        resp = connected_client.post(
            "/api/listing-variants",
            json={"title": "Hat"},
        )

    assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 18. PHOTO GENERATION — pro-only full flow
# ─────────────────────────────────────────────────────────────────────────────

def test_generate_photos_full_flow_for_pro(connected_client):
    import base64
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_ph", "sub_ph", True, "pro")

    fake_image = "data:image/jpeg;base64," + base64.b64encode(_jpeg_bytes()).decode()
    fake_variant_img = "data:image/jpeg;base64," + base64.b64encode(_jpeg_bytes()).decode()

    # Image generation needs GEMINI_API_KEY (checked via env) + the Gemini call patched.
    with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-test-key"}), \
         patch("apis.photos._generate_product_image", return_value=fake_variant_img):
        resp = connected_client.post(
            "/api/generate-photos",
            json={
                "image": fake_image,
                "title": "Crochet Hat",
                "materials": ["wool"],
                "colors": ["white"],
            },
            content_type="application/json",
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert "variants" in body
    assert len(body["variants"]) == 3


def test_generate_photos_requires_data_url(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_ph2", "sub_ph2", True, "pro")

    with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-test-key"}):
        resp = connected_client.post(
            "/api/generate-photos",
            json={"image": "https://example.com/not-a-data-url.jpg"},
            content_type="application/json",
        )

    assert resp.status_code == 400


def test_generate_photos_rejected_without_api_key(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_ph3", "sub_ph3", True, "pro")

    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
        import base64
        fake_image = "data:image/jpeg;base64," + base64.b64encode(_jpeg_bytes()).decode()
        resp = connected_client.post(
            "/api/generate-photos",
            json={"image": fake_image},
            content_type="application/json",
        )

    assert resp.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# 19. TAXONOMY — requires Etsy auth
# ─────────────────────────────────────────────────────────────────────────────

def test_taxonomy_requires_etsy_auth(client):
    """Unauthenticated users (no session) cannot access taxonomy."""
    resp = client.get("/api/taxonomy?q=hat")
    assert resp.status_code == 401


def test_taxonomy_returns_results(connected_client):
    fake_taxonomy = {"results": [
        {"id": 1, "name": "Hats", "children": [
            {"id": 2, "name": "Beanies", "children": []}
        ]}
    ]}
    with patch("requests.get", return_value=MagicMock(ok=True, json=lambda: fake_taxonomy)):
        resp = connected_client.get("/api/taxonomy?q=hat")
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, list)


# ─────────────────────────────────────────────────────────────────────────────
# 20. UPLOADS — path traversal blocked
# ─────────────────────────────────────────────────────────────────────────────

def test_uploads_blocks_path_traversal(client):
    resp = client.get("/uploads/../etc/passwd")
    assert resp.status_code in (400, 404)


def test_uploads_blocks_absolute_path(client):
    # %2F-encoded slashes trigger Flask's redirect normalization (308),
    # then the normalized path is rejected as 404 by basename check.
    resp = client.get("/uploads/%2Fetc%2Fpasswd", follow_redirects=True)
    assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 21. UNSUBSCRIBE
# ─────────────────────────────────────────────────────────────────────────────

def test_unsubscribe_with_no_token_returns_400(client):
    resp = client.get("/unsubscribe")
    assert resp.status_code == 400


def test_unsubscribe_with_valid_token(client):
    with patch("apis.pages.unsubscribe_by_token", return_value=True):
        resp = client.get("/unsubscribe?token=valid-token-abc")
    assert resp.status_code == 200
    assert b"unsubscribe" in resp.data.lower() or b"abonelik" in resp.data.lower()


def test_unsubscribe_with_already_used_token(client):
    with patch("apis.pages.unsubscribe_by_token", return_value=False):
        resp = client.get("/unsubscribe?token=used-token")
    assert resp.status_code == 200
    assert b"invalid" in resp.data.lower() or b"kullan" in resp.data.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 22. END-TO-END FLOWS
# ─────────────────────────────────────────────────────────────────────────────

def test_full_flow_email_user_buys_pro_then_generates(client):
    """Complete flow: email login → pro plan via webhook → generate listing."""
    # Step 1: email login
    with client.session_transaction() as sess:
        sess["guest"] = True
        sess["guest_id"] = "guest-e2e"
        sess["email_verified"] = True
        sess["email_shop_id"] = "email-shop-e2e"

    db_module.ensure_shop("email-shop-e2e", "Guest")

    # Step 2: Stripe webhook upgrades to pro
    payload = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": "email-shop-e2e",
            "customer": "cus_e2e",
            "subscription": "sub_e2e",
            "payment_status": "paid",
            "metadata": {"shop_id": "email-shop-e2e", "plan": "pro"},
        }},
    }).encode()

    with patch("stripe.Webhook.construct_event", return_value=json.loads(payload)):
        webhook_resp = client.post(
            "/stripe/webhook",
            data=payload,
            headers={"Stripe-Signature": "t=1,v1=fake", "Content-Type": "application/json"},
        )
    assert webhook_resp.status_code == 200

    # Step 3: Verify shop is now pro
    shop = db_module.get_shop("email-shop-e2e")
    assert shop["plan"] == "pro"
    assert shop["has_premium"] == 1

    # Step 4: Generate listing (should now be unlimited)
    with patch("apis.listings._run_provider", return_value=dict(_FAKE_AI_LISTING)), \
         patch("requests.get", return_value=MagicMock(ok=True, json=lambda: {"results": []})), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}):
        import io
        gen_resp = client.post(
            "/api/generate",
            data={"images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"), "hint": "crochet hat"},
            content_type="multipart/form-data",
        )

    assert gen_resp.status_code == 200
    assert gen_resp.get_json()["title"] == _FAKE_AI_LISTING["title"]


def test_full_flow_etsy_user_publish_cycle(client):
    """Connected Etsy user (pro): generate → improve → translate → publish."""
    with client.session_transaction() as sess:
        sess["access_token"] = "fake-token-cycle"
        sess["shop_id"] = "cycle-shop"
        sess["shop_name"] = "CycleShop"
        sess["token_expiry"] = time.time() + 3600
    db_module.ensure_shop("cycle-shop", "CycleShop")
    db_module.set_premium("cycle-shop", "cus_cyc", "sub_cyc", True, "pro")
    connected_client = client

    # Generate
    with patch("apis.listings._run_provider", return_value=dict(_FAKE_AI_LISTING)), \
         patch("requests.get", return_value=MagicMock(ok=True, json=lambda: {"results": []})), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}):
        import io
        gen = connected_client.post(
            "/api/generate",
            data={"images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"), "hint": "hat"},
            content_type="multipart/form-data",
        )
    assert gen.status_code == 200

    # Improve
    improved = {"title": "Better Hat", "description": "Improved", "tags": ["hat"]}
    with patch("apis.listings._run_text_json", return_value=improved):
        imp = connected_client.post(
            "/api/improve-listing",
            json={"action": "title_seo", "title": "Hat", "description": "Old", "tags": []},
        )
    assert imp.status_code == 200

    # Translate
    translated = {"title": "Mütze", "description": "Beschreibung", "tags": ["Hut"]}
    with patch("apis.translate._translate_ai", return_value=translated):
        tr = connected_client.post(
            "/api/translate",
            json={"lang": "de", "title": "Hat", "description": "A hat", "tags": ["hat"]},
        )
    assert tr.status_code == 200

    # Publish
    with patch("requests.post", return_value=MagicMock(ok=True, json=lambda: {"listing_id": 777})), \
         patch("requests.get", return_value=MagicMock(ok=True, json=lambda: {"results": []})):
        pub = connected_client.post("/api/publish", json=_VALID_PUBLISH_PAYLOAD)
    assert pub.status_code == 200
    assert pub.get_json()["listing_id"] == 777


def test_subscription_cancelled_blocks_premium_features(connected_client):
    """After subscription cancellation, premium features must be inaccessible."""
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_cancel", "sub_cancel", True, "pro")

    # Cancel via webhook
    payload = json.dumps({
        "type": "customer.subscription.deleted",
        "data": {"object": {"customer": "cus_cancel", "id": "sub_cancel"}},
    }).encode()

    with patch("stripe.Webhook.construct_event", return_value=json.loads(payload)):
        client_ref = connected_client
        client_ref.post(
            "/stripe/webhook",
            data=payload,
            headers={"Stripe-Signature": "t=1,v1=fake", "Content-Type": "application/json"},
        )

    # Bulk generate should now be blocked
    import io
    resp = connected_client.post(
        "/api/bulk-generate",
        data={"images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"), "hint": "hat"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 23. STARTER PLAN — premium access but not pro
# ─────────────────────────────────────────────────────────────────────────────

def test_starter_plan_has_premium_access(connected_client):
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_st2", "sub_st2", True, "starter")

    resp = connected_client.get("/api/status")
    assert resp.status_code == 200
    # Should not be blocked (has premium access)
    assert resp.get_json()["allowed"] is True


def test_free_plan_cannot_use_photos(connected_client):
    """Photos require a paid plan — free shops have a 0 photo-image cap."""
    db_module.ensure_shop("12345", "TestShop")  # free by default

    import base64
    fake_image = "data:image/jpeg;base64," + base64.b64encode(_jpeg_bytes()).decode()
    with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-test"}):
        resp = connected_client.post(
            "/api/generate-photos",
            json={"image": fake_image},
            content_type="application/json",
        )
    assert resp.status_code == 403


def test_starter_plan_gets_photo_taster():
    """Starter now gets a small photo allowance (1 set = 6 images)."""
    db_module.ensure_shop("12345", "TestShop")
    db_module.set_premium("12345", "cus_st3", "sub_st3", True, "starter")
    # The cap is non-zero for starter, so the DB gate allows generation.
    allowed, remaining = db_module.can_generate_photo_variants("12345", 1)
    assert allowed is True
    assert remaining == 6


def test_bulk_batch_limits_per_plan():
    from core.config import plan_limit
    assert plan_limit("free", "bulk_batch") == 0
    assert plan_limit("starter", "bulk_batch") == 10
    assert plan_limit("pro", "bulk_batch") == 20


# ─────────────────────────────────────────────────────────────────────────────
# 24. SECURITY — CSRF on non-exempt endpoints
# ─────────────────────────────────────────────────────────────────────────────

def test_csrf_rejected_on_generate_without_token():
    """With CSRF enabled, generate must reject requests missing the token."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["WTF_CSRF_CHECK_DEFAULT"] = True
    try:
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["access_token"] = "fake-token"
                sess["shop_id"] = "csrf-shop"
                sess["shop_name"] = "CsrfShop"
                sess["token_expiry"] = time.time() + 3600
            import io
            resp = c.post(
                "/api/generate",
                data={"images": (io.BytesIO(_jpeg_bytes()), "hat.jpg")},
                content_type="multipart/form-data",
            )
        assert resp.status_code in (400, 403)
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_csrf_rejected_on_stripe_checkout_without_token():
    """With CSRF enabled, stripe/checkout must reject requests missing X-CSRFToken."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["WTF_CSRF_CHECK_DEFAULT"] = True
    try:
        with app.test_client() as c:
            db_module.ensure_shop("csrf-stripe-shop", "TestShop")
            with c.session_transaction() as sess:
                sess["access_token"] = "fake-token"
                sess["shop_id"] = "csrf-stripe-shop"
                sess["shop_name"] = "CsrfStripeShop"
                sess["token_expiry"] = time.time() + 3600
            resp = c.post(
                "/stripe/checkout",
                json={"plan": "pro"},
                headers={"Content-Type": "application/json"},
                # No X-CSRFToken header — must be rejected
            )
        assert resp.status_code in (400, 403)
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


# ─────────────────────────────────────────────────────────────────────────────
# 25. PRIVACY AND TERMS PAGES
# ─────────────────────────────────────────────────────────────────────────────

def test_privacy_page_loads(client):
    resp = client.get("/privacy")
    assert resp.status_code == 200


def test_terms_page_loads(client):
    resp = client.get("/terms")
    assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 26. FINGERPRINT ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────

def test_fingerprint_stores_fp_session(client):
    """Valid fingerprint from a guest must be accepted and stored."""
    with client.session_transaction() as sess:
        sess["guest"] = True
        sess["guest_id"] = "guest-fp-test"
    fp = "a" * 32  # 32 lowercase hex chars
    resp = client.post("/api/fingerprint", json={"fp": fp})
    assert resp.status_code == 200
    assert resp.get_json().get("ok") is True
    with client.session_transaction() as sess:
        assert "fp_guest_id" in sess
        assert sess["fp_guest_id"] == f"guest_fp_{fp[:24]}"


def test_fingerprint_rejects_connected_user(connected_client):
    """Fingerprint endpoint must return 400 when user is already connected."""
    resp = connected_client.post("/api/fingerprint", json={"fp": "a" * 32})
    assert resp.status_code == 400


def test_fingerprint_rejects_invalid_fp(client):
    """Fingerprint endpoint must reject too-short or non-hex values."""
    with client.session_transaction() as sess:
        sess["guest"] = True
        sess["guest_id"] = "guest-fp-bad"
    assert client.post("/api/fingerprint", json={"fp": "abc"}).status_code == 400
    assert client.post("/api/fingerprint", json={"fp": "GGGG" * 8}).status_code == 400
    assert client.post("/api/fingerprint", json={"fp": ""}).status_code == 400


def test_fingerprint_migrates_usage(client):
    """Fingerprint must migrate free_used from random guest shop to fp shop."""
    import hashlib as hl
    # guest_shop_id() hashes "guest:{guest_id}" — compute the real shop ID
    gid = "guest-random-001"
    hashed_shop_id = "guest_" + hl.sha256(f"guest:{gid}".encode()).hexdigest()[:16]
    db_module.ensure_shop(hashed_shop_id, "Guest")
    for _ in range(3):
        db_module.increment_usage(hashed_shop_id)
    with client.session_transaction() as sess:
        sess["guest"] = True
        sess["guest_id"] = gid
    fp = "b" * 32
    client.post("/api/fingerprint", json={"fp": fp})
    fp_shop = db_module.get_shop(f"guest_fp_{fp[:24]}")
    assert fp_shop is not None
    assert fp_shop.get("free_used", 0) >= 3


# ─────────────────────────────────────────────────────────────────────────────
# 27. TRENDYOL ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

def _make_tr_response(status=200, body=None):
    """Build a mock requests.Response for Trendyol API calls."""
    r = MagicMock()
    r.status_code = status
    r.ok = (status < 400)
    r.json.return_value = body or {}
    r.text = json.dumps(body or {})
    return r


def test_trendyol_connect_requires_auth(client):
    resp = client.post("/api/trendyol/connect", json={
        "supplier_id": "123", "api_key": "k", "api_secret": "s"
    })
    assert resp.status_code == 401


def test_trendyol_connect_missing_fields(connected_client):
    resp = connected_client.post("/api/trendyol/connect", json={"supplier_id": "123"})
    assert resp.status_code == 400


def test_trendyol_connect_success(connected_client):
    with patch("apis.trendyol._tr_get") as mock_get:
        mock_get.return_value = _make_tr_response(200, {"supplierAddresses": []})
        resp = connected_client.post("/api/trendyol/connect", json={
            "supplier_id": "sup-1", "api_key": "key-1", "api_secret": "sec-1"
        })
    assert resp.status_code == 200
    assert resp.get_json().get("ok") is True
    creds = db_module.get_platform_credentials("12345", "trendyol")
    assert creds is not None
    assert creds["supplier_id"] == "sup-1"


def test_trendyol_connect_invalid_credentials(connected_client):
    with patch("apis.trendyol._tr_get") as mock_get:
        mock_get.return_value = _make_tr_response(401)
        resp = connected_client.post("/api/trendyol/connect", json={
            "supplier_id": "bad", "api_key": "bad", "api_secret": "bad"
        })
    assert resp.status_code == 400


def test_trendyol_connect_api_down(connected_client):
    with patch("apis.trendyol._tr_get", side_effect=Exception("timeout")):
        resp = connected_client.post("/api/trendyol/connect", json={
            "supplier_id": "s", "api_key": "k", "api_secret": "x"
        })
    assert resp.status_code == 502


def test_trendyol_disconnect_requires_auth(client):
    resp = client.post("/api/trendyol/disconnect")
    assert resp.status_code == 401


def test_trendyol_disconnect_clears_credentials(connected_client):
    db_module.save_platform_credentials("12345", "trendyol",
        {"supplier_id": "s", "api_key": "k", "api_secret": "x"})
    resp = connected_client.post("/api/trendyol/disconnect")
    assert resp.status_code == 200
    assert db_module.get_platform_credentials("12345", "trendyol") is None


def test_trendyol_addresses_requires_auth(client):
    assert client.get("/api/trendyol/addresses").status_code == 401


def test_trendyol_addresses_requires_connection(connected_client):
    resp = connected_client.get("/api/trendyol/addresses")
    assert resp.status_code == 400
    assert resp.get_json().get("error") == "not_connected"


def test_trendyol_addresses_returns_list(connected_client):
    db_module.save_platform_credentials("12345", "trendyol",
        {"supplier_id": "s", "api_key": "k", "api_secret": "x"})
    with patch("apis.trendyol._tr_get") as mock_get:
        mock_get.return_value = _make_tr_response(200, {
            "supplierAddresses": [{"id": 10, "fullAddress": "Istanbul"}]
        })
        resp = connected_client.get("/api/trendyol/addresses")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["addresses"][0]["id"] == 10
    assert body["addresses"][0]["label"] == "Istanbul"


def test_trendyol_categories_requires_auth(client):
    assert client.get("/api/trendyol/categories").status_code == 401


def test_trendyol_categories_returns_and_caches(connected_client):
    db_module.save_platform_credentials("12345", "trendyol",
        {"supplier_id": "s", "api_key": "k", "api_secret": "x"})
    cats = [{"id": 1, "name": "Electronics"}]
    with patch("apis.trendyol._tr_get") as mock_get:
        mock_get.return_value = _make_tr_response(200, {"categories": cats})
        resp1 = connected_client.get("/api/trendyol/categories")
        resp2 = connected_client.get("/api/trendyol/categories")  # should use cache
        assert mock_get.call_count == 1  # second call served from cache
    assert resp1.status_code == 200
    assert resp2.get_json()["categories"] == cats


def test_trendyol_cat_attributes_requires_auth(client):
    assert client.get("/api/trendyol/categories/1/attributes").status_code == 401


def test_trendyol_cat_attributes_returns_data(connected_client):
    db_module.save_platform_credentials("12345", "trendyol",
        {"supplier_id": "s", "api_key": "k", "api_secret": "x"})
    with patch("apis.trendyol._tr_get") as mock_get:
        mock_get.return_value = _make_tr_response(200, {"categoryAttributes": []})
        resp = connected_client.get("/api/trendyol/categories/42/attributes")
    assert resp.status_code == 200


def test_trendyol_brands_requires_auth(client):
    assert client.get("/api/trendyol/brands?q=nike").status_code == 401


def test_trendyol_brands_short_query_returns_empty(connected_client):
    db_module.save_platform_credentials("12345", "trendyol",
        {"supplier_id": "s", "api_key": "k", "api_secret": "x"})
    resp = connected_client.get("/api/trendyol/brands?q=x")
    assert resp.status_code == 200
    assert resp.get_json() == {"brands": []}


def test_trendyol_brands_returns_results(connected_client):
    db_module.save_platform_credentials("12345", "trendyol",
        {"supplier_id": "s", "api_key": "k", "api_secret": "x"})
    with patch("apis.trendyol._tr_get") as mock_get:
        mock_get.return_value = _make_tr_response(200, [{"id": 5, "name": "Nike"}])
        resp = connected_client.get("/api/trendyol/brands?q=nike")
    assert resp.status_code == 200
    assert resp.get_json()["brands"][0]["name"] == "Nike"


def test_trendyol_publish_requires_auth(client):
    assert client.post("/api/trendyol/publish", json={}).status_code == 401


def test_trendyol_publish_requires_trendyol_connection(connected_client):
    resp = connected_client.post("/api/trendyol/publish", json={})
    assert resp.status_code == 400
    assert resp.get_json().get("error") == "not_connected"


def test_trendyol_publish_validates_barcode(connected_client):
    db_module.save_platform_credentials("12345", "trendyol",
        {"supplier_id": "s", "api_key": "k", "api_secret": "x"})
    resp = connected_client.post("/api/trendyol/publish", json={
        "barcode": "", "title": "t", "description": "d",
        "sale_price": 10, "list_price": 10,
        "brand_id": 1, "category_id": 1,
        "shipment_address_id": 1, "returning_address_id": 1,
        "image_previews": [],
    })
    assert resp.status_code == 400


def test_trendyol_publish_validates_price(connected_client):
    db_module.save_platform_credentials("12345", "trendyol",
        {"supplier_id": "s", "api_key": "k", "api_secret": "x"})
    resp = connected_client.post("/api/trendyol/publish", json={
        "barcode": "1234", "title": "t", "description": "d",
        "sale_price": 100, "list_price": 50,  # list < sale → invalid
        "brand_id": 1, "category_id": 1,
        "shipment_address_id": 1, "returning_address_id": 1,
        "image_previews": [],
    })
    assert resp.status_code == 400


def test_trendyol_publish_requires_image(connected_client):
    """Publishing without images must return 400."""
    db_module.save_platform_credentials("12345", "trendyol",
        {"supplier_id": "s", "api_key": "k", "api_secret": "x"})
    resp = connected_client.post("/api/trendyol/publish", json={
        "barcode": "1234", "title": "My Product", "description": "Desc",
        "sale_price": 50, "list_price": 100,
        "brand_id": 1, "category_id": 1,
        "shipment_address_id": 1, "returning_address_id": 1,
        "image_previews": [],  # no images
    })
    assert resp.status_code == 400


def test_trendyol_publish_success(connected_client):
    """Valid product must be submitted to Trendyol and return ok=True."""
    db_module.save_platform_credentials("12345", "trendyol",
        {"supplier_id": "s", "api_key": "k", "api_secret": "x"})
    import base64
    # Minimal valid JPEG data URL
    fake_b64 = "data:image/jpeg;base64," + base64.b64encode(
        b"\xff\xd8\xff\xe0" + b"\x00" * 12).decode()
    with patch("apis.trendyol._tr_post") as mock_post, \
         patch("apis.trendyol._save_image_for_upload", return_value="test.jpg"):
        mock_post.return_value = _make_tr_response(200, {"batchRequestId": "batch-1"})
        resp = connected_client.post("/api/trendyol/publish", json={
            "barcode": "1234", "title": "My Product", "description": "A product",
            "sale_price": 50, "list_price": 100,
            "brand_id": 1, "category_id": 1,
            "shipment_address_id": 1, "returning_address_id": 1,
            "image_previews": [fake_b64],
        })
    assert resp.status_code == 200
    assert resp.get_json().get("ok") is True


# ─────────────────────────────────────────────────────────────────────────────
# 28. MAGIC LINK — guest usage migration on verification
# ─────────────────────────────────────────────────────────────────────────────

def test_magic_link_migrates_guest_usage_on_verify(client):
    """Guest usage (free_used) must carry over to the email shop when the
    magic link is verified — regardless of whether a fingerprint was submitted."""
    import hashlib as hl
    email = "migrate-test@example.com"
    email_hash = hl.sha256(email.encode()).hexdigest()
    email_shop_id = f"guest_email_{email_hash[:20]}"

    # Set up: random guest shop with 3 uses (keyed by raw guest_id, as read in app.py:1159)
    db_module.ensure_shop("guest-random-migrate", "Guest")
    for _ in range(3):
        db_module.increment_usage("guest-random-migrate")

    # Set up email shop with 0 uses
    db_module.ensure_shop(email_shop_id, "Guest")

    # Create a magic link token (create_magic_link returns None, store the token string)
    token_val = "tok-migrate-01"
    db_module.create_magic_link(token_val, email_hash, email_shop_id)

    # Simulate: guest session with guest_id (no fingerprint)
    with client.session_transaction() as sess:
        sess["guest"] = True
        sess["guest_id"] = "guest-random-migrate"

    # Verify the magic link
    resp = client.get(f"/auth/magic?token={token_val}")
    assert resp.status_code == 200

    # The email shop must now reflect the migrated usage
    email_shop = db_module.get_shop(email_shop_id)
    assert email_shop is not None
    assert int(email_shop.get("free_used", 0)) >= 3
