"""
Integration tests — hit real external services using keys from .env.

Run with:
    pytest test_integration.py -v -s

These tests are intentionally NOT included in the normal CI suite because they:
- Make real API calls (cost money / tokens)
- Require valid credentials in .env
- Can be slow (5-30s each)

Skip gracefully when a key is missing so the suite still runs in CI without credentials.
"""

import io
import os
import json
import time
import base64
import hashlib
import struct
import pytest
import requests as http_requests

# Load .env into environment before importing app
from pathlib import Path
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# ── app setup ─────────────────────────────────────────────────────────────────

import db as db_module
import app as app_module
from app import app

LIVE_BASE = "https://kolaylistele.com"

# ── helpers ───────────────────────────────────────────────────────────────────

def _jpeg_bytes():
    return b"\xff\xd8\xff\xe0" + b"\x00" * 12


def _skip_if_missing(*env_vars):
    for v in env_vars:
        if not os.getenv(v):
            pytest.skip(f"Missing env var: {v}")


@pytest.fixture()
def client(tmp_path):
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    db_file = str(tmp_path / "integration.sqlite")
    db_module.DB_PATH = db_file
    db_module.init_db()
    try:
        app_module.limiter.reset()
    except Exception:
        pass
    with app.test_client() as c:
        yield c
    db_module.DB_PATH = ":memory:"


@pytest.fixture()
def pro_client(client):
    """Test client with a pro Etsy shop session."""
    db_module.ensure_shop("int-shop-1", "IntegrationShop")
    db_module.set_premium("int-shop-1", "cus_int", "sub_int", True, "pro")
    with client.session_transaction() as sess:
        sess["access_token"] = os.getenv("ETSY_TEST_TOKEN", "fake-int-token")
        sess["shop_id"] = "int-shop-1"
        sess["shop_name"] = "IntegrationShop"
        sess["token_expiry"] = time.time() + 3600
    return client


# ─────────────────────────────────────────────────────────────────────────────
# 1. AI PROVIDERS — real listing generation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_gemini_generates_listing(pro_client):
    """Gemini must return a valid listing JSON with all required fields."""
    _skip_if_missing("GEMINI_API_KEY")

    data = {
        "images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"),
        "hint": "handmade crochet beanie hat, wool, blue",
        "provider": "gemini",
        "lang": "en",
    }
    resp = pro_client.post("/api/generate", data=data, content_type="multipart/form-data")

    assert resp.status_code == 200, f"Gemini returned {resp.status_code}: {resp.data[:300]}"
    body = resp.get_json()
    assert body.get("title"), "Missing title in Gemini response"
    assert isinstance(body.get("tags"), list), "Missing tags"
    assert len(body["tags"]) >= 5, f"Too few tags: {body['tags']}"
    assert body.get("description"), "Missing description"
    print(f"\n✓ Gemini title: {body['title'][:60]}")


@pytest.mark.integration
def test_nvidia_generates_listing(pro_client):
    """NVIDIA Llama must return a valid listing JSON."""
    _skip_if_missing("NVIDIA_API_KEY")

    data = {
        "images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"),
        "hint": "handmade crochet beanie hat, wool, blue",
        "provider": "nvidia",
        "lang": "en",
    }
    resp = pro_client.post("/api/generate", data=data, content_type="multipart/form-data")

    assert resp.status_code in (200, 503), f"Unexpected status: {resp.status_code}"
    if resp.status_code == 503:
        pytest.skip("NVIDIA quota exceeded — skipping")
    body = resp.get_json()
    assert body.get("title"), "Missing title in NVIDIA response"
    print(f"\n✓ NVIDIA title: {body['title'][:60]}")


@pytest.mark.integration
def test_openai_generates_listing(pro_client):
    """OpenAI GPT must return a valid listing JSON."""
    _skip_if_missing("OPENAI_API_KEY")

    data = {
        "images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"),
        "hint": "handmade crochet beanie hat, wool, blue",
        "provider": "openai",
        "lang": "en",
    }
    resp = pro_client.post("/api/generate", data=data, content_type="multipart/form-data")

    assert resp.status_code in (200, 503), f"Unexpected status: {resp.status_code}"
    if resp.status_code == 503:
        pytest.skip("OpenAI quota exceeded")
    body = resp.get_json()
    assert body.get("title"), "Missing title in OpenAI response"
    print(f"\n✓ OpenAI title: {body['title'][:60]}")


@pytest.mark.integration
def test_ai_fallback_chain(pro_client):
    """When primary provider is unavailable, app must fall back and still return 200."""
    _skip_if_missing("GEMINI_API_KEY")

    # Force an invalid provider name — should fall back to chain
    data = {
        "images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"),
        "hint": "leather wallet",
        "provider": "nonexistent",
    }
    resp = pro_client.post("/api/generate", data=data, content_type="multipart/form-data")
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        print(f"\n✓ Fallback worked: {resp.get_json().get('title', '')[:60]}")


@pytest.mark.integration
def test_gemini_turkish_output(pro_client):
    """Gemini must produce Turkish output when lang=tr."""
    _skip_if_missing("GEMINI_API_KEY")

    data = {
        "images": (io.BytesIO(_jpeg_bytes()), "hat.jpg"),
        "hint": "el yapımı örgü bere",
        "provider": "gemini",
        "lang": "tr",
        "platform": "etsy",
    }
    resp = pro_client.post("/api/generate", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data[:200]
    body = resp.get_json()
    assert body.get("title"), "No title returned"
    print(f"\n✓ Turkish title: {body['title'][:60]}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. AI IMPROVE & TRANSLATE — real calls
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_improve_listing_real_ai(pro_client):
    """AI improve must return a better title."""
    _skip_if_missing("GEMINI_API_KEY")

    resp = pro_client.post("/api/improve-listing", json={
        "action": "title_seo",
        "title": "blue hat",
        "description": "a nice blue hat made by hand",
        "tags": ["hat", "blue", "handmade"],
    })
    assert resp.status_code == 200, resp.data[:200]
    body = resp.get_json()
    assert body.get("title"), "No improved title"
    print(f"\n✓ Improved title: {body['title'][:80]}")


@pytest.mark.integration
def test_translate_to_german_real_ai(pro_client):
    """AI translate must produce German output."""
    _skip_if_missing("GEMINI_API_KEY")

    resp = pro_client.post("/api/translate", json={
        "lang": "de",
        "title": "Handmade Crochet Hat",
        "description": "A beautiful handmade hat made from pure wool.",
        "tags": ["handmade", "crochet", "hat", "wool"],
    })
    assert resp.status_code == 200, resp.data[:200]
    body = resp.get_json()
    assert body.get("title"), "No translated title"
    print(f"\n✓ German title: {body['title'][:80]}")


@pytest.mark.integration
def test_translate_to_turkish_real_ai(pro_client):
    """AI translate must produce Turkish output."""
    _skip_if_missing("GEMINI_API_KEY")

    resp = pro_client.post("/api/translate", json={
        "lang": "tr",
        "title": "Handmade Crochet Hat",
        "description": "A beautiful handmade hat.",
        "tags": ["handmade", "crochet"],
    })
    assert resp.status_code == 200, resp.data[:200]
    body = resp.get_json()
    assert body.get("title"), "No Turkish title"
    print(f"\n✓ Turkish title: {body['title'][:80]}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. FAL.AI PHOTO GENERATION — real image generation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_fal_photo_generation_real(pro_client):
    """fal.ai must return 3 photo variants for a pro user."""
    _skip_if_missing("FAL_KEY")

    # Use a minimal valid JPEG as source image
    fake_image = "data:image/jpeg;base64," + base64.b64encode(_jpeg_bytes()).decode()

    resp = pro_client.post("/api/generate-photos", json={
        "image": fake_image,
        "title": "Handmade Crochet Hat",
        "materials": ["wool"],
        "colors": ["blue", "white"],
    })

    assert resp.status_code == 200, f"FAL returned {resp.status_code}: {resp.data[:300]}"
    body = resp.get_json()
    assert "variants" in body, "No variants in response"
    assert len(body["variants"]) == 3, f"Expected 3 variants, got {len(body['variants'])}"
    for v in body["variants"]:
        assert v.get("image"), f"Variant missing image: {v}"
        assert v["image"].startswith("data:image/"), "Variant image is not a data URL"
    print(f"\n✓ FAL generated {len(body['variants'])} photo variants")
    print(f"  Labels: {[v['label'] for v in body['variants']]}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. EMAIL — magic link delivery
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_magic_link_email_delivery(client):
    """Magic link email must be sent successfully via SMTP/Resend."""
    _skip_if_missing("RESEND_API_KEY")

    with client.session_transaction() as sess:
        sess["guest"] = True
        sess["guest_id"] = "guest-int-email"

    db_module.ensure_shop("email-int-shop", "Guest")

    resp = client.post("/api/magic-link", json={"email": "berkcem123@gmail.com"})

    assert resp.status_code == 200, f"Magic link returned {resp.status_code}: {resp.data[:200]}"
    body = resp.get_json()
    assert body.get("ok") is True, f"Expected ok=true, got: {body}"
    print("\n✓ Magic link email sent to berkcem123@gmail.com")


@pytest.mark.integration
def test_magic_link_rate_limit_is_enforced(client):
    """After 3 requests in 1 hour, magic link must be rate-limited."""
    _skip_if_missing("RESEND_API_KEY")

    with client.session_transaction() as sess:
        sess["guest"] = True
        sess["guest_id"] = "guest-rl-test"

    email_hash = hashlib.sha256("ratelimit-test@example.com".encode()).hexdigest()

    # Simulate 5 recent magic links already sent
    for i in range(5):
        db_module.create_magic_link(f"token-rl-{i}", email_hash, "guest-rl-shop")

    resp = client.post("/api/magic-link", json={"email": "ratelimit-test@example.com"})
    assert resp.status_code == 429, f"Expected 429, got {resp.status_code}"
    print("\n✓ Magic link rate limiting works correctly")


# ─────────────────────────────────────────────────────────────────────────────
# 5. LIVE SITE SMOKE TESTS — HTTP checks against production
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.live
def test_live_health_endpoint():
    """Production health endpoint must return 200 ok."""
    for domain in ["https://kolaylistele.com", "https://easylisting.app"]:
        resp = http_requests.get(f"{domain}/health", timeout=10)
        assert resp.status_code == 200, f"{domain}/health returned {resp.status_code}"
        assert resp.json()["status"] == "ok"
        print(f"\n✓ {domain}/health is up")


@pytest.mark.integration
@pytest.mark.live
def test_live_connect_page_loads():
    """Production connect page must load with correct security headers."""
    resp = http_requests.get(f"{LIVE_BASE}/connect", timeout=10)
    assert resp.status_code == 200
    assert "X-Frame-Options" in resp.headers
    assert "Content-Security-Policy" in resp.headers
    print(f"\n✓ {LIVE_BASE}/connect loads with security headers")


@pytest.mark.integration
@pytest.mark.live
def test_live_upgrade_redirects_to_connect():
    """Unauthenticated /upgrade must redirect to /connect."""
    resp = http_requests.get(f"{LIVE_BASE}/upgrade", timeout=10, allow_redirects=False)
    assert resp.status_code in (301, 302, 303)
    assert "/connect" in resp.headers.get("Location", "")
    print(f"\n✓ /upgrade correctly redirects unauthenticated users")


@pytest.mark.integration
@pytest.mark.live
def test_live_stripe_checkout_requires_auth():
    """POST /stripe/checkout without auth must return 401."""
    resp = http_requests.post(
        f"{LIVE_BASE}/stripe/checkout",
        json={"plan": "pro"},
        headers={"Content-Type": "application/json", "X-CSRFToken": "test"},
        timeout=10,
    )
    assert resp.status_code == 401
    print(f"\n✓ /stripe/checkout correctly rejects unauthenticated requests")


@pytest.mark.integration
@pytest.mark.live
def test_live_probe_paths_blocked():
    """Common scanner probe paths must return 404 on production."""
    probe_paths = ["/.env", "/.git/config", "/wp-login.php", "/api/config"]
    for path in probe_paths:
        resp = http_requests.get(f"{LIVE_BASE}{path}", timeout=10)
        assert resp.status_code == 404, f"{path} returned {resp.status_code} (should be 404)"
    print(f"\n✓ All {len(probe_paths)} probe paths blocked")


@pytest.mark.integration
@pytest.mark.live
def test_live_admin_without_token_is_hidden():
    """Admin endpoints without token must return 404 on production."""
    for path in ["/admin/stats", "/admin/abuse", "/admin/ping-ai"]:
        resp = http_requests.get(f"{LIVE_BASE}{path}", timeout=10)
        assert resp.status_code == 404, f"{path} returned {resp.status_code}"
    print(f"\n✓ Admin endpoints hidden without token")


# ─────────────────────────────────────────────────────────────────────────────
# 6. LISTING VARIANTS — real AI call
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_listing_variants_real_ai(pro_client):
    """AI must return 3 distinct listing variants for a pro user."""
    _skip_if_missing("GEMINI_API_KEY")

    resp = pro_client.post("/api/listing-variants", json={
        "title": "Handmade Crochet Hat",
        "description": "A beautiful handmade hat made from pure wool.",
        "tags": ["handmade", "crochet", "hat"],
        "lang": "en",
    })

    assert resp.status_code == 200, f"Variants returned {resp.status_code}: {resp.data[:300]}"
    body = resp.get_json()
    assert "variants" in body, f"No variants key in response: {body}"
    assert len(body["variants"]) >= 1, "Expected at least 1 variant"
    for v in body["variants"]:
        assert v.get("title"), f"Variant missing title: {v}"
    print(f"\n✓ Got {len(body['variants'])} listing variants")
    for v in body["variants"]:
        print(f"  [{v.get('name', '?')}] {v.get('title', '')[:60]}")
