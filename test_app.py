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
    """Each test gets a fresh SQLite file (not :memory: so threads work)."""
    db_file = str(tmp_path / "test.sqlite")
    with patch.dict(os.environ, {"DB_PATH": db_file}):
        db_module.DB_PATH = db_file
        db_module.init_db()
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
    """Client with a guest session."""
    with client.session_transaction() as sess:
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
    assert "Content-Security-Policy" in resp.headers
    assert "frame-ancestors" in resp.headers["Content-Security-Policy"]


def test_csp_has_nonce(client):
    resp = client.get("/")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "nonce-" in csp


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


def test_can_generate_premium_bypasses_limit():
    db_module.ensure_shop("shop3", "Shop Three")
    for _ in range(10):
        db_module.increment_usage("shop3")
    db_module.set_premium("shop3", "cus_fake", "sub_fake", True, "pro")
    allowed, remaining = db_module.can_generate("shop3", limit=3)
    assert allowed is True
    assert remaining == 999


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


def test_can_improve_premium_unlimited():
    db_module.ensure_shop("shop_imppro", "ImpProShop")
    db_module.set_premium("shop_imppro", "cus_ip", "sub_ip", True, "pro")
    for _ in range(10):
        db_module.increment_improve_usage("shop_imppro")
    allowed, remaining = db_module.can_improve("shop_imppro")
    assert allowed is True
    assert remaining == 999


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
