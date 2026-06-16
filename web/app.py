"""EasyListing / kolaylistele — Flask app factory.

Thin entry point: configures the app, binds extensions, installs request hooks
and security headers, and registers the API blueprints. All business logic lives
in core/ (shared logic) and apis/ (route blueprints).
"""
import os
import secrets

from flask import Flask, request, redirect, g
from flask.signals import got_request_exception
from flask_wtf.csrf import generate_csrf
from itsdangerous import URLSafeSerializer
from dotenv import load_dotenv

from extensions import limiter, csrf
from core.config import GUEST_ID_COOKIE, GUEST_ID_MAX_AGE
from core.logging import (
    add_request_id_header,
    bind_request_context,
    log_request_exception,
    log_request_finished,
)
from db import init_db

load_dotenv()

# ── App setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
_is_production = os.getenv("ENV") == "production"
_flask_secret  = os.getenv("FLASK_SECRET")
if _is_production and not _flask_secret:
    raise RuntimeError("FLASK_SECRET must be set in production")

app.config.update(
    SECRET_KEY                 = _flask_secret or secrets.token_hex(32),
    MAX_CONTENT_LENGTH         = 30 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY    = True,
    SESSION_COOKIE_SAMESITE    = "Lax",
    SESSION_COOKIE_SECURE      = _is_production,
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 2,  # 48 hours
    WTF_CSRF_TIME_LIMIT        = None,
    POSTHOG_TOKEN              = os.getenv("POSTHOG_TOKEN", ""),
)

limiter.init_app(app)
csrf.init_app(app)

init_db()

from core.backup import start_daily_backup
import db as _db_module
start_daily_backup(_db_module.DB_PATH)

# ── Request hooks ───────────────────────────────────────────────────────────

_BLOCKED_PATH_PREFIXES = (
    "/.git", "/.env", "/root/", "/etc/", "/proc/",
    "/wp-", "//wp-", "//sito/",
    "/backend/", "/API/config", "/api/config",
    "/_next/", "/_react/", "/_layouts/",
    "/attacker/", "/aws-codecommit/",
    "/docker-compose", "/config.ts", "/config.js",
)

_BLOCKED_PATH_SUFFIXES = (
    ".git-credentials", "/.git-credentials",
    "/wlwmanifest.xml", "/xmlrpc.php",
)


@app.before_request
def setup_request_logging():
    bind_request_context()


@app.before_request
def block_probe_paths():
    path = request.path.lower()
    if any(path.startswith(p) for p in _BLOCKED_PATH_PREFIXES):
        return "", 404
    if any(path.endswith(s) for s in _BLOCKED_PATH_SUFFIXES):
        return "", 404


@app.before_request
def setup_request():
    g.csp_nonce = secrets.token_urlsafe(16)
    if _is_production and request.headers.get("X-Forwarded-Proto") == "http":
        return redirect(request.url.replace("http://", "https://"), 301)


@app.context_processor
def inject_security():
    from core.domains import _is_try_domain
    return {
        "csp_nonce":  getattr(g, "csp_nonce", ""),
        "csrf_token": generate_csrf,
        "use_try":    _is_try_domain(),
    }


@app.context_processor
def inject_analytics():
    from flask import session as _s
    from core.session import shop_id as _sid, guest_shop_id as _gsid
    try:
        sid = _sid() or _gsid()
    except Exception:
        return {"ph_identity": None}
    if not sid:
        return {"ph_identity": None}
    return {
        "ph_identity": {
            "id":       str(sid),
            "name":     _s.get("shop_name") or "Guest",
            "is_guest": not bool(_sid()),
        }
    }


# ── Security headers ──────────────────────────────────────────────────────────

@app.after_request
def set_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"]        = "DENY"
    resp.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"]     = "geolocation=(), microphone=(), camera=()"
    if _is_production:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    nonce = getattr(g, "csp_nonce", "")
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' https://js.stripe.com https://static.cloudflareinsights.com https://www.googletagmanager.com; "
        f"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        f"img-src 'self' data: blob: *.etsystatic.com *.etsy.com https://www.googletagmanager.com; "
        f"connect-src 'self' https://api.stripe.com https://cloudflareinsights.com https://www.google-analytics.com https://analytics.google.com https://region1.google-analytics.com; "
        f"frame-src https://js.stripe.com https://hooks.stripe.com; "
        f"font-src 'self' https://fonts.gstatic.com; "
        f"form-action 'self'; "
        f"base-uri 'self'; "
        f"object-src 'none'; "
        f"frame-ancestors 'none';"
    )
    # Prevent browsers / proxies from caching pages that may contain session data
    if "text/html" in (resp.content_type or ""):
        resp.headers["Cache-Control"] = "no-store, private"
    resp.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none"
    resp.headers["Cross-Origin-Opener-Policy"]   = "same-origin-allow-popups"
    guest_id = getattr(g, "set_guest_id_cookie", "")
    if guest_id:
        token = URLSafeSerializer(app.config["SECRET_KEY"], salt="guest-id").dumps(guest_id)
        resp.set_cookie(
            GUEST_ID_COOKIE,
            token,
            max_age=GUEST_ID_MAX_AGE,
            httponly=True,
            secure=_is_production,
            samesite="Lax",
        )
    add_request_id_header(resp)
    log_request_finished(resp)
    return resp


got_request_exception.connect(log_request_exception, app)


# ── Blueprints ────────────────────────────────────────────────────────────────

from apis.pages import bp as pages_bp
from apis.admin import bp as admin_bp
from apis.trendyol import bp as trendyol_bp
from apis.photos import bp as photos_bp
from apis.translate import bp as translate_bp
from apis.payments import bp as payments_bp
from apis.etsy_oauth import bp as etsy_oauth_bp
from apis.auth import bp as auth_bp
from apis.listings import bp as listings_bp

app.register_blueprint(pages_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(trendyol_bp)
app.register_blueprint(photos_bp)
app.register_blueprint(translate_bp)
app.register_blueprint(payments_bp)
app.register_blueprint(etsy_oauth_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(listings_bp)


if __name__ == "__main__":
    os.makedirs("images", exist_ok=True)
    app.run(debug=os.getenv("ENV") != "production", port=5050)
