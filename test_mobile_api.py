"""
Tests for the mobile (iOS) API surface: token auth, mobile OAuth/magic-link
flows, mobile-only endpoints, and regression guards proving the web flow is
untouched when no mobile headers are present.
"""
import os
import json
import time
import tempfile
import pytest
from unittest.mock import patch, MagicMock

os.environ.setdefault("ETSY_API_KEY", "test_key")
os.environ.setdefault("FLASK_SECRET", "test_secret_for_mobile_tests")

import db as db_module


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_mobile.sqlite")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    import app as app_module
    app_module.app.config["TESTING"] = True
    app_module.app.config["WTF_CSRF_ENABLED"] = False
    app_module.limiter.enabled = False
    db_module.init_db()
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def mobile_guest_token(client):
    r = client.post("/auth/mobile/guest")
    assert r.status_code == 200
    return r.get_json()["mobile_token"]


def _etsy_token(shop_id="12345", shop_name="TestShop", **extra):
    return db_module.create_mobile_token(
        shop_id=shop_id, access_token="etsy_access_tok",
        shop_name=shop_name, **extra,
    )


# ── db layer ──────────────────────────────────────────────────────────────────

class TestMobileTokenDB:
    def test_create_and_get(self, client):
        tok = db_module.create_mobile_token(shop_id="111", access_token="abc",
                                            shop_name="Shop", refresh_token="ref",
                                            expires_at=9999999999)
        row = db_module.get_by_mobile_token(tok)
        assert row["shop_id"] == "111"
        assert row["access_token"] == "abc"
        assert row["refresh_token"] == "ref"
        assert row["expires_at"] == 9999999999
        assert not row["is_guest"]

    def test_guest_token(self, client):
        tok = db_module.create_mobile_token(shop_id="g1", is_guest=True, guest_id="g1")
        row = db_module.get_by_mobile_token(tok)
        assert row["is_guest"] == 1
        assert row["guest_id"] == "g1"

    def test_email_token(self, client):
        tok = db_module.create_mobile_token(shop_id="guest_email_x", is_guest=True, is_email=True)
        row = db_module.get_by_mobile_token(tok)
        assert row["is_email"] == 1

    def test_get_missing_returns_none(self, client):
        assert db_module.get_by_mobile_token("nonexistent") is None
        assert db_module.get_by_mobile_token("") is None
        assert db_module.get_by_mobile_token(None) is None

    def test_delete(self, client):
        tok = db_module.create_mobile_token(shop_id="111")
        db_module.delete_mobile_token(tok)
        assert db_module.get_by_mobile_token(tok) is None

    def test_update_access(self, client):
        tok = db_module.create_mobile_token(shop_id="111", access_token="old",
                                            refresh_token="oldref", expires_at=1)
        db_module.update_mobile_token_access(tok, "new", "newref", 12345)
        row = db_module.get_by_mobile_token(tok)
        assert row["access_token"] == "new"
        assert row["refresh_token"] == "newref"
        assert row["expires_at"] == 12345

    def test_update_access_keeps_refresh_when_none(self, client):
        tok = db_module.create_mobile_token(shop_id="111", access_token="old",
                                            refresh_token="keepme", expires_at=1)
        db_module.update_mobile_token_access(tok, "new", None, 99)
        row = db_module.get_by_mobile_token(tok)
        assert row["refresh_token"] == "keepme"

    def test_tokens_are_unique_and_long(self, client):
        toks = {db_module.create_mobile_token(shop_id=str(i)) for i in range(50)}
        assert len(toks) == 50
        assert all(len(t) >= 40 for t in toks)


# ── mobile-only endpoints ─────────────────────────────────────────────────────

class TestMobileGuestEndpoint:
    def test_creates_token_and_shop(self, client):
        r = client.post("/auth/mobile/guest")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        row = db_module.get_by_mobile_token(body["mobile_token"])
        assert row["is_guest"] == 1
        assert row["guest_id"].startswith("mobile_guest_")
        assert db_module.get_shop(row["guest_id"]) is not None

    def test_logs_abuse_signal(self, client):
        client.post("/auth/mobile/guest")
        with db_module._conn() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM abuse_signals WHERE event = 'new_mobile_guest'"
            ).fetchone()
        assert row["n"] == 1


class TestMobileLogout:
    def test_deletes_token(self, client, mobile_guest_token):
        r = client.post("/auth/mobile/logout",
                        headers={"X-Mobile-Token": mobile_guest_token})
        assert r.status_code == 200
        assert db_module.get_by_mobile_token(mobile_guest_token) is None

    def test_no_token_is_noop(self, client):
        r = client.post("/auth/mobile/logout")
        assert r.status_code == 200


class TestCSRFTokenEndpoint:
    def test_returns_token(self, client):
        r = client.get("/api/csrf-token")
        assert r.status_code == 200
        assert len(r.get_json()["csrf_token"]) > 10


# ── token-based auth on existing endpoints ────────────────────────────────────

class TestMobileTokenAuth:
    def test_status_with_guest_token(self, client, mobile_guest_token):
        r = client.get("/api/status", headers={"X-Mobile-Token": mobile_guest_token})
        assert r.status_code == 200
        body = r.get_json()
        assert body["is_guest"] is True
        assert body["remaining"] == 3

    def test_status_with_etsy_token(self, client):
        tok = _etsy_token()
        db_module.ensure_shop("12345", "TestShop")
        r = client.get("/api/status", headers={"X-Mobile-Token": tok})
        assert r.status_code == 200
        assert r.get_json()["is_guest"] is False

    def test_status_without_token_unauthorized(self, client):
        r = client.get("/api/status")
        assert r.status_code == 401

    def test_invalid_token_unauthorized(self, client):
        r = client.get("/api/status", headers={"X-Mobile-Token": "garbage"})
        assert r.status_code == 401

    def test_guest_usage_tracked_per_token(self, client, mobile_guest_token):
        row = db_module.get_by_mobile_token(mobile_guest_token)
        db_module.increment_usage(row["guest_id"])
        r = client.get("/api/status", headers={"X-Mobile-Token": mobile_guest_token})
        assert r.get_json()["remaining"] == 2

    def test_generate_requires_email_verification_for_plain_guest(self, client, mobile_guest_token):
        r = client.post("/api/generate",
                        headers={"X-Mobile-Token": mobile_guest_token},
                        data={})
        assert r.status_code == 403
        assert r.get_json()["error"] == "email_verification_required"

    def test_email_verified_guest_passes_verification_gate(self, client):
        tok = db_module.create_mobile_token(
            shop_id="guest_email_abc", is_guest=True, is_email=True)
        db_module.ensure_shop("guest_email_abc", "Guest")
        r = client.post("/api/generate",
                        headers={"X-Mobile-Token": tok}, data={})
        # passes the verification gate, fails on missing images
        assert r.status_code == 400
        assert "image" in r.get_json()["error"].lower()

    def test_listings_endpoint_requires_connection(self, client, mobile_guest_token):
        r = client.get("/api/listings", headers={"X-Mobile-Token": mobile_guest_token})
        assert r.status_code == 401

    @patch("app.requests.get")
    def test_listings_endpoint_with_etsy_token(self, mock_get, client):
        tok = _etsy_token()
        mock_get.return_value = MagicMock(
            ok=True, json=lambda: {"results": [{"listing_id": 1, "title": "x"}]})
        r = client.get("/api/listings?state=active", headers={"X-Mobile-Token": tok})
        assert r.status_code == 200
        assert r.get_json()["results"][0]["listing_id"] == 1
        # Bearer header must carry the mobile session's Etsy token
        sent_headers = mock_get.call_args.kwargs["headers"]
        assert sent_headers["Authorization"] == "Bearer etsy_access_tok"

    def test_listings_rejects_invalid_state(self, client):
        tok = _etsy_token()
        with patch("app.requests.get") as mock_get:
            mock_get.return_value = MagicMock(ok=True, json=lambda: {"results": []})
            client.get("/api/listings?state=<script>", headers={"X-Mobile-Token": tok})
            assert "state=active" in mock_get.call_args.args[0]


# ── Etsy token refresh ────────────────────────────────────────────────────────

class TestEtsyTokenRefresh:
    @patch("app.requests.post")
    def test_expired_token_triggers_refresh(self, mock_post, client):
        tok = _etsy_token(refresh_token="ref_tok", expires_at=int(time.time()) - 10)
        mock_post.return_value = MagicMock(ok=True, json=lambda: {
            "access_token": "fresh_tok", "refresh_token": "new_ref", "expires_in": 3600})
        db_module.ensure_shop("12345", "TestShop")
        r = client.get("/api/status", headers={"X-Mobile-Token": tok})
        assert r.status_code == 200
        row = db_module.get_by_mobile_token(tok)
        assert row["access_token"] == "fresh_tok"
        assert row["refresh_token"] == "new_ref"
        assert row["expires_at"] > time.time()

    @patch("app.requests.post")
    def test_valid_token_skips_refresh(self, mock_post, client):
        tok = _etsy_token(refresh_token="ref", expires_at=int(time.time()) + 3000)
        db_module.ensure_shop("12345", "TestShop")
        client.get("/api/status", headers={"X-Mobile-Token": tok})
        mock_post.assert_not_called()

    @patch("app.requests.post")
    def test_failed_refresh_keeps_old_token(self, mock_post, client):
        tok = _etsy_token(refresh_token="ref", expires_at=int(time.time()) - 10)
        mock_post.return_value = MagicMock(ok=False, status_code=400)
        db_module.ensure_shop("12345", "TestShop")
        r = client.get("/api/status", headers={"X-Mobile-Token": tok})
        assert r.status_code == 200  # request still succeeds with stale token
        assert db_module.get_by_mobile_token(tok)["access_token"] == "etsy_access_tok"


# ── mobile OAuth flow ─────────────────────────────────────────────────────────

class TestMobileOAuth:
    def test_auth_start_mobile_flag_stored(self, client):
        r = client.get("/auth/start?mobile=1")
        assert r.status_code == 302
        assert "etsy.com/oauth/connect" in r.headers["Location"]
        with client.session_transaction() as sess:
            assert sess["_is_mobile_oauth"] is True

    def test_auth_start_web_unaffected(self, client):
        r = client.get("/auth/start")
        assert r.status_code == 302
        with client.session_transaction() as sess:
            assert sess["_is_mobile_oauth"] is False

    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_mobile_callback_redirects_to_app_scheme(self, mock_post, mock_get, client):
        with client.session_transaction() as sess:
            sess["oauth_state"]      = "state123"
            sess["pkce_verifier"]    = "ver"
            sess["_redirect_uri"]    = "https://easylisting.app/auth/callback"
            sess["_is_mobile_oauth"] = True
        mock_post.return_value = MagicMock(ok=True, json=lambda: {
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600})
        me   = MagicMock(ok=True, json=lambda: {"shop_id": 777})
        shop = MagicMock(ok=True, json=lambda: {"shop_name": "MyShop"})
        mock_get.side_effect = [me, shop]

        r = client.get("/auth/callback?code=abc&state=state123")
        assert r.status_code == 302
        loc = r.headers["Location"]
        assert loc.startswith("easylisting://auth?mobile_token=")
        assert "shop_name=MyShop" in loc
        # token row persisted with refresh data
        mob_tok = loc.split("mobile_token=")[1].split("&")[0]
        from urllib.parse import unquote
        row = db_module.get_by_mobile_token(unquote(mob_tok))
        assert row["shop_id"] == "777"
        assert row["refresh_token"] == "rt"

    @patch("app.requests.get")
    @patch("app.requests.post")
    def test_web_callback_still_redirects_to_index(self, mock_post, mock_get, client):
        with client.session_transaction() as sess:
            sess["oauth_state"]   = "state123"
            sess["pkce_verifier"] = "ver"
            sess["_redirect_uri"] = "http://localhost:5050/auth/callback"
        mock_post.return_value = MagicMock(ok=True, json=lambda: {
            "access_token": "at", "expires_in": 3600})
        me   = MagicMock(ok=True, json=lambda: {"shop_id": 888})
        shop = MagicMock(ok=True, json=lambda: {"shop_name": "WebShop"})
        mock_get.side_effect = [me, shop]

        r = client.get("/auth/callback?code=abc&state=state123")
        assert r.status_code == 302
        assert r.headers["Location"] == "/"


# ── mobile magic link flow ────────────────────────────────────────────────────

class TestMobileMagicLink:
    @patch("app.send_email", return_value=(True, None))
    def test_new_email_sends_app_scheme_link(self, mock_send, client):
        r = client.post("/api/magic-link",
                        headers={"X-Mobile-Request": "true"},
                        json={"email": "new@example.com"})
        assert r.status_code == 200
        assert r.get_json()["ok"] is True
        link_text = mock_send.call_args.args[2]
        assert "easylisting://auth/magic?token=" in link_text

    @patch("app.send_email", return_value=(True, None))
    def test_returning_email_gets_instant_token(self, mock_send, client):
        import hashlib
        email_hash = hashlib.sha256(b"ret@example.com").hexdigest()
        db_module.get_or_create_email_shop(email_hash)

        r = client.post("/api/magic-link",
                        headers={"X-Mobile-Request": "true"},
                        json={"email": "ret@example.com"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["already_verified"] is True
        row = db_module.get_by_mobile_token(body["mobile_token"])
        assert row["is_email"] == 1
        mock_send.assert_not_called()

    def test_magic_verify_mobile_returns_json_token(self, client):
        import hashlib
        email_hash = hashlib.sha256(b"v@example.com").hexdigest()
        shop_id = db_module.get_or_create_email_shop(email_hash)
        db_module.create_magic_link("tok123", email_hash, shop_id)

        r = client.get("/auth/magic?token=tok123",
                       headers={"X-Mobile-Request": "true"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert db_module.get_by_mobile_token(body["mobile_token"])["is_email"] == 1

    def test_magic_verify_mobile_invalid_token_is_json_400(self, client):
        r = client.get("/auth/magic?token=bogus",
                       headers={"X-Mobile-Request": "true"})
        assert r.status_code == 400
        assert r.get_json()["error"] == "invalid_or_expired"

    def test_magic_verify_web_returns_html(self, client):
        import hashlib
        email_hash = hashlib.sha256(b"w@example.com").hexdigest()
        shop_id = db_module.get_or_create_email_shop(email_hash)
        db_module.create_magic_link("tokweb", email_hash, shop_id)

        r = client.get("/auth/magic?token=tokweb")
        assert r.status_code == 200
        assert b"<!" in r.data or b"<html" in r.data.lower()

    def test_magic_token_single_use(self, client):
        import hashlib
        email_hash = hashlib.sha256(b"s@example.com").hexdigest()
        shop_id = db_module.get_or_create_email_shop(email_hash)
        db_module.create_magic_link("tokonce", email_hash, shop_id)

        first  = client.get("/auth/magic?token=tokonce", headers={"X-Mobile-Request": "true"})
        second = client.get("/auth/magic?token=tokonce", headers={"X-Mobile-Request": "true"})
        assert first.status_code == 200
        assert second.status_code == 400


# ── web regression guards ─────────────────────────────────────────────────────

class TestWebFlowUnchanged:
    """No mobile headers → behavior identical to the pre-mobile web app."""

    def test_web_root_redirects_to_connect(self, client):
        r = client.get("/")
        assert r.status_code == 302
        assert "/connect" in r.headers["Location"]

    def test_web_guest_session_flow(self, client):
        client.get("/guest")
        r = client.get("/api/status")
        assert r.status_code == 200
        assert r.get_json()["is_guest"] is True

    def test_web_magic_link_requires_email(self, client):
        client.get("/guest")
        r = client.post("/api/magic-link", json={"email": "not-an-email"})
        assert r.status_code == 400
        assert r.get_json()["error"] == "invalid_email"

    def test_mobile_token_header_ignored_when_invalid_for_web_session(self, client):
        # A web session with a stray invalid mobile header must not break
        client.get("/guest")
        r = client.get("/api/status", headers={"X-Mobile-Token": "junk"})
        assert r.status_code == 200
        assert r.get_json()["is_guest"] is True
