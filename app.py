import os
import io
import json
import time
import base64
import hashlib
import secrets
import logging
import tempfile
import requests
import urllib.parse
from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, g, send_from_directory,
)
from flask_wtf.csrf import generate_csrf
from itsdangerous import BadSignature, URLSafeSerializer
from extensions import limiter, csrf
from core.config import (
    logger, HTTP_TIMEOUT, GUEST_FREE_LIMIT, GUEST_ID_COOKIE, GUEST_ID_MAX_AGE,
    PHOTO_VARIANT_COUNT, READINESS_ID, FAL_KEY, ALLOW_PAID_OPENAI,
)
from core.email import send_email
from core.validators import (
    _is_valid_image_bytes, safe_error, validate_listing_input,
    ALLOWED_IMAGE_TYPES, MAX_TITLE_LEN, MAX_DESC_LEN, MAX_TAG_LEN, MAX_PRICE, MIN_PRICE,
)
from core.ai import (
    provider_chain, _build_prompt, _style_hint_for_shop, _merge_hint_with_style,
    _parse_ai_json, _gemini_generate, _openai_generate, _nvidia_generate,
    _run_provider, _run_text_json,
    AI_PROMPT, PLATFORM_PROMPTS, NVIDIA_MODELS, _LANG_NAMES, _TURKISH_PLATFORMS,
    _PROVIDER_CHAIN_FREE, _PROVIDER_CHAIN_PREMIUM,
)
from core.etsy import (
    ETSY_CLIENT_ID, ETSY_CLIENT_SECRET, ETSY_API_KEY_HEADER, ETSY_SCOPES, REDIRECT_URI,
    _refresh_etsy_token, _refresh_web_etsy_token, _mobile_auth, etsy_headers,
    _find_taxonomy_id, _dynamic_redirect_uri,
)
from core.session import (
    shop_id, is_connected, is_guest, is_authorized, _cookie_guest_id,
    _get_or_create_guest_id, guest_shop_id, _try_restore_email_session,
    is_email_verified, has_premium_access, has_pro_access, usage_shop_id,
    require_connection,
)
from core.domains import _ip_hash, _is_try_domain
from dotenv import load_dotenv
from db import (
    init_db, ensure_shop, can_generate, increment_usage,
    can_improve, increment_improve_usage,
    can_generate_photo_variants, increment_photo_variant_usage,
    save_template, get_template,
    set_premium, get_shop, get_shop_by_stripe_customer,
    log_abuse_signal, get_abuse_summary,
    get_or_create_email_shop, get_email_shop, create_magic_link, use_magic_link, count_recent_magic_links, has_verified_email,
    add_marketing_consent, unsubscribe_by_token, get_marketing_stats,
    save_platform_credentials, get_platform_credentials, delete_platform_credentials,
    save_fp_session, get_fp_session,
    create_mobile_token, get_by_mobile_token, delete_mobile_token,
    update_mobile_token_access,
)

load_dotenv()

# ── App setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
_is_production = os.getenv("ENV") == "production"
_flask_secret   = os.getenv("FLASK_SECRET")
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
)

limiter.init_app(app)
csrf.init_app(app)

init_db()

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
    return {
        "csp_nonce":  getattr(g, "csp_nonce", ""),
        "csrf_token": generate_csrf,
    }

# ── Security headers ──────────────────────────────────────────────────────────

@app.after_request
def set_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"]        = "DENY"
    resp.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"]     = "geolocation=(), microphone=(), camera=()"
    if os.getenv("ENV") == "production":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    nonce = getattr(g, "csp_nonce", "")
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' https://js.stripe.com https://static.cloudflareinsights.com; "
        f"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        f"img-src 'self' data: blob: *.etsystatic.com *.etsy.com; "
        f"connect-src 'self' https://api.stripe.com https://cloudflareinsights.com; "
        f"frame-src https://js.stripe.com https://hooks.stripe.com; "
        f"font-src 'self' https://fonts.gstatic.com; "
        f"frame-ancestors 'none';"
    )
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
    return resp

# ── Helpers ───────────────────────────────────────────────────────────────────

# ── OAuth routes ──────────────────────────────────────────────────────────────

@app.route("/connect")
def connect():
    if is_connected():
        return redirect(url_for("index"))
    return render_template("connect.html")

@app.route("/auth/start")
@limiter.limit("10 per minute")
def auth_start():
    verifier  = secrets.token_urlsafe(64)
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    state     = secrets.token_urlsafe(16)

    redirect_uri = _dynamic_redirect_uri()

    is_mobile_oauth = request.args.get("mobile") == "1"
    pre_oauth_guest = guest_shop_id() if is_guest() else None
    session.clear()  # prevent session fixation
    session["pkce_verifier"]    = verifier
    session["oauth_state"]      = state
    session["_redirect_uri"]    = redirect_uri  # store for callback
    session["_is_mobile_oauth"] = is_mobile_oauth
    if pre_oauth_guest:
        session["_pre_oauth_guest_id"] = pre_oauth_guest

    url = "https://www.etsy.com/oauth/connect?" + urllib.parse.urlencode({
        "response_type":         "code",
        "redirect_uri":          redirect_uri,
        "scope":                 ETSY_SCOPES,
        "client_id":             ETSY_CLIENT_ID,
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    })
    return redirect(url)

@app.route("/auth/callback")
@limiter.limit("10 per minute")
def auth_callback():
    code  = request.args.get("code", "")
    state = request.args.get("state", "")

    if not code or not state or state != session.get("oauth_state"):
        session.clear()
        return render_template("connect.html", error="Authorization failed. Please try again.")

    resp = requests.post(
        "https://api.etsy.com/v3/public/oauth/token",
        data={
            "grant_type":    "authorization_code",
            "client_id":     ETSY_CLIENT_ID,
            "redirect_uri":  session.pop("_redirect_uri", REDIRECT_URI),
            "code":          code,
            "code_verifier": session.pop("pkce_verifier", ""),
        },
        timeout=HTTP_TIMEOUT,
    )
    if not resp.ok:
        session.clear()
        return render_template("connect.html", error="Could not get access token. Please try again.")

    token_json    = resp.json()
    access_token  = token_json.get("access_token")
    refresh_token = token_json.get("refresh_token")
    token_expires = int(time.time()) + int(token_json.get("expires_in", 3600))
    if not access_token:
        session.clear()
        return render_template("connect.html", error="No access token returned.")

    # Capture the mobile flag before the anti-fixation clear wipes it
    is_mobile_oauth = bool(session.get("_is_mobile_oauth", False))
    session.clear()  # prevent session fixation

    h = {"x-api-key": ETSY_API_KEY_HEADER, "Authorization": f"Bearer {access_token}"}
    try:
        me_resp   = requests.get("https://openapi.etsy.com/v3/application/users/me",
                                 headers=h, timeout=HTTP_TIMEOUT)
        if not me_resp.ok:
            logger.error("Etsy /users/me failed: %s %s", me_resp.status_code, me_resp.text[:300])
            return render_template("connect.html", error=f"Etsy API error ({me_resp.status_code}). Check your API key in Railway.")
        me        = me_resp.json()
        shop_id_v = me.get("shop_id")
        if not shop_id_v:
            return render_template("connect.html", error="No Etsy shop found on this account. Make sure you're signing in with a seller account that has an open shop.")
        if not str(shop_id_v).isdigit():
            return render_template("connect.html", error="Could not retrieve your shop. Please try again.")
        shop_resp = requests.get(f"https://openapi.etsy.com/v3/application/shops/{shop_id_v}",
                                 headers=h, timeout=HTTP_TIMEOUT)
        shop = shop_resp.json()
    except Exception as e:
        logger.exception("Etsy auth callback error: %s", e)
        return render_template("connect.html", error="Could not reach Etsy. Please try again.")

    shop_name_v = shop.get("shop_name", "Your Shop")
    ensure_shop(str(shop_id_v), shop_name_v)

    session.permanent        = True
    session["access_token"]  = access_token
    session["shop_id"]       = shop_id_v
    session["shop_name"]     = shop_name_v
    session["refresh_token"] = refresh_token
    session["token_expires"] = token_expires

    if is_mobile_oauth:
        mob_tok = create_mobile_token(
            shop_id=str(shop_id_v),
            access_token=access_token,
            shop_name=shop_name_v,
            refresh_token=refresh_token,
            expires_at=token_expires,
        )
        return redirect(
            "easylisting://auth?mobile_token="
            + urllib.parse.quote(mob_tok)
            + "&shop_name=" + urllib.parse.quote(shop_name_v)
        )

    return redirect(url_for("index"))

@app.route("/disconnect")
def disconnect():
    session.clear()
    return redirect(url_for("connect"))

@app.route("/guest")
@limiter.limit("20 per minute")
def start_guest():
    session.clear()
    session["guest"] = True
    log_abuse_signal("new_guest", ip_hash=_ip_hash())
    return redirect(url_for("index"))

# ── Mobile-specific endpoints ──────────────────────────────────────────────────

@app.route("/api/csrf-token")
@limiter.limit("60 per minute")
def api_csrf_token():
    """Return a CSRF token for the current session. iOS app calls this once, then
    includes the returned token as X-CSRFToken on every subsequent POST request."""
    return jsonify({"csrf_token": generate_csrf()})


@app.route("/auth/mobile/guest", methods=["POST"])
@csrf.exempt
@limiter.limit("5 per hour; 10 per day")
def auth_mobile_guest():
    """Create a guest mobile token for anonymous iOS users.
    Tight rate limit: each call mints a fresh guest with free quota, so this is
    the main abuse vector for free-limit resets."""
    guest_id = f"mobile_guest_{secrets.token_urlsafe(12)}"
    ensure_shop(guest_id, "Guest")
    log_abuse_signal("new_mobile_guest", ip_hash=_ip_hash(), guest_id=guest_id)
    mob_tok = create_mobile_token(
        shop_id=guest_id, is_guest=True, guest_id=guest_id,
    )
    return jsonify({"ok": True, "mobile_token": mob_tok})


@app.route("/auth/mobile/logout", methods=["POST"])
@csrf.exempt
@limiter.limit("30 per minute")
def auth_mobile_logout():
    """Invalidate a mobile token (logout from iOS app)."""
    tok = request.headers.get("X-Mobile-Token", "")
    if tok:
        delete_mobile_token(tok)
    return jsonify({"ok": True})

@app.route("/api/fingerprint", methods=["POST"])
@csrf.exempt
@limiter.limit("30 per minute")
def api_fingerprint():
    if not is_guest():
        return jsonify({"ok": False}), 400
    body = request.get_json(silent=True) or {}
    fp = body.get("fp", "").strip().lower()
    # Must be 16–64 lowercase hex chars (SHA-256 slice from client)
    if not fp or not (16 <= len(fp) <= 64) or not all(c in "0123456789abcdef" for c in fp):
        return jsonify({"ok": False}), 400
    fp_guest_id = f"guest_fp_{fp[:24]}"

    # Capture the current random-based ID BEFORE switching, so we can
    # migrate its usage to the fp ID (prevents fp ID starting at 0/3).
    current_sid = guest_shop_id()

    session["fp_guest_id"] = fp_guest_id
    session.permanent = True
    # Do NOT overwrite GUEST_ID_COOKIE — the random 180-day cookie is the
    # stable fallback and must not be replaced with a fresh fp ID.

    # Ensure fp shop exists, then migrate usage from the random shop if
    # the fp shop is brand-new (free_used=0) but the random shop has usage.
    ensure_shop(fp_guest_id, "Guest")
    if current_sid != fp_guest_id:
        fp_shop      = get_shop(fp_guest_id)
        current_shop = get_shop(current_sid)
        if fp_shop and current_shop:
            if fp_shop.get("free_used", 0) == 0 and current_shop.get("free_used", 0) > 0:
                # Migrate usage from random ID to fp ID
                from db import _conn as _db_conn
                with _db_conn() as con:
                    con.execute(
                        "UPDATE shops SET free_used = ? WHERE shop_id = ?",
                        (int(current_shop["free_used"]), fp_guest_id),
                    )
            elif fp_shop.get("free_used", 0) > 0 and current_shop.get("free_used", 0) == 0:
                # fp ID already has usage but came in with a fresh random ID —
                # this is the incognito/cleared-cookie pattern; log it.
                log_abuse_signal("fp_conflict", ip_hash=_ip_hash(),
                                 guest_id=current_sid, fp_hash=fp[:24],
                                 detail=f"fp_used={fp_shop['free_used']}")
                logger.info("ABUSE fp_conflict ip=%s fp=%s new_guest=%s",
                            _ip_hash(), fp[:24], current_sid)

    return jsonify({"ok": True})

# ── Magic link routes ────────────────────────────────────────────────────────

@app.route("/api/magic-link", methods=["POST"])
@csrf.exempt
@limiter.limit("5 per minute; 10 per hour")
def api_magic_link():
    is_mobile = bool(request.headers.get("X-Mobile-Request"))

    if not is_mobile:
        if not is_guest():
            if is_connected():
                return jsonify({"error": "not_guest"}), 400
            session["guest"] = True
            session.permanent = True
        if is_email_verified():
            from db import FREE_LIMIT as _FL
            sid  = guest_shop_id()
            shop = get_shop(sid)
            if shop and not shop.get("has_premium") and int(shop.get("free_used", 0)) >= _FL:
                return jsonify({"error": "limit_reached"}), 403
            if sid and sid.startswith("guest_email_"):
                ensure_shop(sid, "Guest")
            return jsonify({"ok": True, "already_verified": True})

    body             = request.get_json(silent=True) or {}
    email            = (body.get("email") or "").strip().lower()
    marketing_opt_in = bool(body.get("marketing_consent", False))

    import re
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "invalid_email"}), 400

    email_hash = hashlib.sha256(email.encode()).hexdigest()

    # Check BEFORE creating the DB record — if the email has been seen before,
    # auto-login without sending another email. Brand-new emails get the full flow.
    existing_shop_id = get_email_shop(email_hash)

    shop_id_for_email = get_or_create_email_shop(email_hash)
    ensure_shop(shop_id_for_email, "Guest")

    from db import FREE_LIMIT as _FL2
    email_shop = get_shop(shop_id_for_email)

    if existing_shop_id is not None:
        # Returning user — migrate usage first, then log in immediately
        current_sid = guest_shop_id()
        if current_sid != shop_id_for_email:
            current_shop = get_shop(current_sid)
            if current_shop and email_shop:
                merged = max(int(current_shop.get("free_used", 0)),
                             int(email_shop.get("free_used", 0)))
                if merged > int(email_shop.get("free_used", 0)):
                    from db import _conn as _db_conn
                    with _db_conn() as con:
                        con.execute("UPDATE shops SET free_used = ? WHERE shop_id = ?",
                                    (merged, shop_id_for_email))
                    email_shop = get_shop(shop_id_for_email)  # refresh after update

        if email_shop and not email_shop.get("has_premium") and int(email_shop.get("free_used", 0)) >= _FL2:
            return jsonify({"error": "limit_reached"}), 403
        if is_mobile:
            mob_tok = create_mobile_token(
                shop_id=shop_id_for_email, is_guest=True, is_email=True,
            )
            return jsonify({"ok": True, "already_verified": True, "mobile_token": mob_tok})
        session["guest"]          = True
        session["email_verified"] = True
        session["email_shop_id"]  = shop_id_for_email
        session.permanent         = True
        fp_id = session.get("fp_guest_id")
        if fp_id:
            save_fp_session(fp_id, shop_id_for_email)
        return jsonify({"ok": True, "already_verified": True})

    # Brand-new email — rate-limit and send the magic link
    if count_recent_magic_links(email_hash, minutes=60) >= 5:
        return jsonify({"error": "too_many_requests"}), 429

    if email_shop and not email_shop.get("has_premium") and int(email_shop.get("free_used", 0)) >= _FL2:
        return jsonify({"error": "limit_reached"}), 403

    # Migrate usage: take the MAX so neither session loses progress
    current_sid  = guest_shop_id()
    if current_sid != shop_id_for_email:
        current_shop = get_shop(current_sid)
        email_shop   = get_shop(shop_id_for_email)
        if current_shop and email_shop:
            merged = max(int(current_shop.get("free_used", 0)),
                         int(email_shop.get("free_used", 0)))
            if merged > int(email_shop.get("free_used", 0)):
                from db import _conn as _db_conn
                with _db_conn() as con:
                    con.execute("UPDATE shops SET free_used = ? WHERE shop_id = ?",
                                (merged, shop_id_for_email))

    token = secrets.token_urlsafe(32)
    create_magic_link(token, email_hash, shop_id_for_email)

    base  = REDIRECT_URI.replace("/auth/callback", "")
    is_tr = "kolaylistele" in base or request.host and "kolaylistele" in request.host
    if is_mobile:
        link = f"easylisting://auth/magic?token={token}"
    else:
        link = f"{base}/auth/magic?token={token}"

    if marketing_opt_in:
        locale = "tr" if is_tr else "en"
        add_marketing_consent(email, email_hash, locale=locale, source="magic_link")

    if is_tr:
        subject   = "kolaylistele — Giriş bağlantınız"
        pre_head  = "Ücretsiz ilanlarınıza erişmek için tıklayın"
        headline  = "Merhaba! 👋"
        body1     = "kolaylistele'de <strong>3 ücretsiz ilan</strong> oluşturma hakkınız için giriş bağlantınız hazır."
        body2     = "Aşağıdaki butona tıklayarak hemen başlayabilirsiniz."
        btn_text  = "Devam Et →"
        expire    = "Bu bağlantı <strong>15 dakika</strong> geçerlidir ve yalnızca bir kez kullanılabilir."
        footer1   = "Bu e-postayı siz talep etmediyseniz güvenle yok sayabilirsiniz."
        footer2   = "© kolaylistele · info@kolaylistele.com"
        plain     = f"Merhaba,\n\nkolaylistele ücretsiz ilan hakkınız için giriş bağlantınız:\n\n{link}\n\nBu bağlantı 15 dakika geçerlidir.\n\n—\nkolaylistele ekibi"
    else:
        subject   = "EasyListing — Your sign-in link"
        pre_head  = "Click to access your free AI listings"
        headline  = "Hi there! 👋"
        body1     = "Your sign-in link for <strong>3 free AI listings</strong> on EasyListing is ready."
        body2     = "Click the button below to get started instantly."
        btn_text  = "Continue to EasyListing →"
        expire    = "This link is valid for <strong>15 minutes</strong> and can only be used once."
        footer1   = "If you didn't request this, you can safely ignore this email."
        footer2   = "© EasyListing · info@kolaylistele.com"
        plain     = f"Hi,\n\nYour EasyListing sign-in link:\n\n{link}\n\nExpires in 15 minutes.\n\n— EasyListing team"

    brand_html = '<span style="color:#5B47E0;font-weight:900;">kolay</span><span style="color:#111827;font-weight:900;">listele</span>' if is_tr else '<span style="color:#5B47E0;font-weight:900;">Easy</span><span style="color:#111827;font-weight:900;">Listing</span>'

    html = f"""<!DOCTYPE html>
<html lang="{'tr' if is_tr else 'en'}">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{subject}</title>
<!--[if mso]><style>td{{font-family:Arial,sans-serif!important}}</style><![endif]-->
</head>
<body style="margin:0;padding:0;background:#F6F7FB;font-family:'Helvetica Neue',Arial,sans-serif;">
<span style="display:none;max-height:0;overflow:hidden;">{pre_head}&nbsp;&#847;&nbsp;</span>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F6F7FB;padding:40px 16px;">
  <tr><td align="center">
    <table width="100%" style="max-width:520px;" cellpadding="0" cellspacing="0">

      <!-- Logo bar -->
      <tr><td style="padding-bottom:24px;text-align:center;">
        <div style="font-size:24px;letter-spacing:-.5px;">{brand_html}</div>
      </td></tr>

      <!-- Card -->
      <tr><td style="background:#ffffff;border-radius:16px;padding:40px 40px 32px;box-shadow:0 2px 20px rgba(91,71,224,.08);">

        <p style="font-size:20px;font-weight:700;color:#111827;margin:0 0 14px;">{headline}</p>
        <p style="font-size:15px;color:#374151;line-height:1.65;margin:0 0 8px;">{body1}</p>
        <p style="font-size:14px;color:#6B7280;line-height:1.6;margin:0 0 28px;">{body2}</p>

        <!-- CTA button -->
        <table cellpadding="0" cellspacing="0" width="100%">
          <tr><td align="center">
            <a href="{link}"
               style="display:inline-block;background:linear-gradient(135deg,#6B5AED,#5B47E0);
                      color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;
                      padding:14px 32px;border-radius:10px;letter-spacing:.1px;">
              {btn_text}
            </a>
          </td></tr>
        </table>

        <!-- Fallback link -->
        <p style="font-size:12px;color:#9CA3AF;margin:20px 0 0;text-align:center;line-height:1.5;">
          Buton çalışmıyorsa bu bağlantıyı kopyalayın:<br>
          <a href="{link}" style="color:#5B47E0;word-break:break-all;">{link}</a>
        </p>

        <!-- Expiry notice -->
        <div style="margin-top:24px;padding:12px 16px;background:#F0EEFF;border-radius:8px;
                    font-size:12px;color:#5B47E0;line-height:1.5;text-align:center;">
          ⏱ {expire}
        </div>
      </td></tr>

      <!-- Footer -->
      <tr><td style="padding:20px 0 0;text-align:center;">
        <p style="font-size:11px;color:#9CA3AF;line-height:1.6;margin:0;">{footer1}<br>{footer2}</p>
      </td></tr>

    </table>
  </td></tr>
</table>
</body></html>"""

    plain = plain

    ok, err_reason = send_email(email, subject, plain, html)
    if not ok:
        logger.error("api_magic_link: send_email failed for %s: %s", email_hash[:8], err_reason)
        return jsonify({"error": "send_failed"}), 500

    return jsonify({"ok": True})


@app.route("/auth/magic")
@limiter.limit("20 per minute")
def auth_magic():
    token     = request.args.get("token", "")
    is_mobile = bool(request.headers.get("X-Mobile-Request"))
    row       = use_magic_link(token) if token else None
    if not row:
        if is_mobile:
            return jsonify({"error": "invalid_or_expired"}), 400
        return render_template("connect.html",
            error="Bu bağlantı geçersiz veya süresi dolmuş. Lütfen tekrar deneyin.")

    email_shop_id = row["shop_id"]
    ensure_shop(email_shop_id, "Guest")

    if is_mobile:
        mob_tok = create_mobile_token(
            shop_id=email_shop_id,
            is_guest=True,
            is_email=True,
        )
        return jsonify({"ok": True, "mobile_token": mob_tok, "shop_id": email_shop_id})

    # Web path: migrate usage from current guest shop → email shop before switching
    current_sid = session.get("fp_guest_id") or session.get("guest_id")
    if current_sid and current_sid != email_shop_id:
        current_shop = get_shop(current_sid)
        email_shop   = get_shop(email_shop_id)
        if current_shop and email_shop:
            merged = max(int(current_shop.get("free_used", 0)),
                         int(email_shop.get("free_used", 0)))
            if merged > int(email_shop.get("free_used", 0)):
                from db import _conn as _db_conn
                with _db_conn() as con:
                    con.execute("UPDATE shops SET free_used = ? WHERE shop_id = ?",
                                (merged, email_shop_id))

    session["guest"]          = True
    session["email_verified"] = True
    session["email_shop_id"]  = email_shop_id
    session.permanent         = True
    fp_id = session.get("fp_guest_id")
    if fp_id:
        save_fp_session(fp_id, email_shop_id)
    return render_template("magic_verified.html")


# ── App routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    redir = require_connection()
    if redir: return redir
    if is_guest():
        gid = guest_shop_id()
        ensure_shop(gid, "Guest")
        email_verified = is_email_verified()
        premium = has_premium_access()
        from db import FREE_LIMIT as _FL
        limit = _FL if email_verified else GUEST_FREE_LIMIT
        _, remaining = can_generate(gid, limit)
        return render_template("index.html",
            shop_name=None, remaining=remaining, free_limit=limit,
            unlimited=premium, is_guest=True, has_premium=premium,
            email_verified=email_verified)
    sid = str(shop_id())
    _, remaining = can_generate(sid)
    from db import FREE_LIMIT
    premium = has_premium_access()
    return render_template(
        "index.html",
        shop_name=session.get("shop_name"),
        remaining=remaining,
        free_limit=FREE_LIMIT,
        unlimited=remaining >= 999,
        has_premium=premium,
        is_guest=False,
    )

@app.route("/api/email-verified")
@limiter.limit("30 per minute")
def api_email_verified():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    return jsonify({"verified": is_email_verified()})


@app.route("/listings")
def listings():
    redir = require_connection()
    if redir: return redir
    state = request.args.get("state", "draft")
    if state not in ("draft", "active"):
        state = "draft"
    r = requests.get(
        f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/listings"
        f"?state={state}&limit=100&includes[]=images",
        headers=etsy_headers(), timeout=HTTP_TIMEOUT,
    )
    items = r.json().get("results", []) if r.ok else []
    return render_template("listings.html", items=items, state=state,
                           shop_name=session.get("shop_name"))

@app.route("/api/listings")
@limiter.limit("30 per minute")
def api_listings():
    """JSON version of /listings for the mobile app."""
    if not is_connected():
        return jsonify({"error": "Not connected"}), 401
    state = request.args.get("state", "active")
    if state not in ("draft", "active"):
        state = "active"
    r = requests.get(
        f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/listings"
        f"?state={state}&limit=100&includes[]=images",
        headers=etsy_headers(), timeout=HTTP_TIMEOUT,
    )
    items = r.json().get("results", []) if r.ok else []
    return jsonify({"results": items})

@app.route("/api/status")
@limiter.limit("60 per minute")
def api_status():
    if not is_authorized(): return jsonify({"error": "Not connected"}), 401
    if is_guest():
        gid = guest_shop_id()
        ensure_shop(gid, "Guest")
        from db import FREE_LIMIT as _FL
        limit = _FL if is_email_verified() else GUEST_FREE_LIMIT
        allowed, remaining = can_generate(gid, limit)
    else:
        allowed, remaining = can_generate(str(shop_id()))
    sid = str(shop_id() or guest_shop_id())
    return jsonify({
        "allowed":            allowed,
        "remaining":          remaining,
        "is_guest":           is_guest(),
        "is_email":           is_email_verified(),
        "trendyol_connected": bool(get_platform_credentials(sid, "trendyol")),
    })

@app.route("/api/generate", methods=["POST"])
@limiter.limit("10 per minute; 50 per day")
def api_generate():
    if not is_authorized(): return jsonify({"error": "Not connected"}), 401
    if is_guest() and not is_email_verified():
        return jsonify({"error": "email_verification_required"}), 403

    if is_guest():
        sid = guest_shop_id()
        ensure_shop(sid, "Guest")
        from db import FREE_LIMIT as _FL
        limit = _FL if is_email_verified() else GUEST_FREE_LIMIT
        allowed, remaining = can_generate(sid, limit)
    else:
        sid = str(shop_id())
        allowed, remaining = can_generate(sid)
    if not allowed:
        if is_guest():
            log_abuse_signal("limit_hit", ip_hash=_ip_hash(), guest_id=sid,
                             fp_hash=session.get("fp_guest_id", "")[-24:] or None)
            logger.info("ABUSE limit_hit ip=%s guest=%s", _ip_hash(), sid)
        return jsonify({"error": "free_limit_reached", "remaining": 0}), 403

    images       = request.files.getlist("images")
    hint         = str(request.form.get("hint", "")).strip()[:200]
    hint         = _merge_hint_with_style(hint, None if is_guest() else sid)
    provider     = request.form.get("provider", "nvidia")
    nvidia_model = request.form.get("nvidia_model", "llama-90b")
    lang         = request.form.get("lang", "en")
    platform     = request.form.get("platform", "etsy")

    premium = has_premium_access()
    chain   = _PROVIDER_CHAIN_PREMIUM if premium else _PROVIDER_CHAIN_FREE

    if provider not in chain:
        provider = chain[0]
    if nvidia_model not in NVIDIA_MODELS:
        nvidia_model = "llama-90b"
    if lang not in _LANG_NAMES:
        lang = "en"
    if platform not in PLATFORM_PROMPTS:
        platform = "etsy"

    if not images or not images[0].filename:
        return jsonify({"error": "No images provided"}), 400

    # Validate image MIME types and magic bytes
    image_bytes = []
    for img in images[:5]:
        if img.content_type not in ALLOWED_IMAGE_TYPES:
            return jsonify({"error": f"File type not allowed: {img.content_type}"}), 400
        data_b = img.read()
        if not _is_valid_image_bytes(data_b):
            return jsonify({"error": "File content does not match an allowed image format."}), 400
        image_bytes.append(data_b)

    key_env = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "nvidia": "NVIDIA_API_KEY"}
    # Try chosen provider first, then fall through the chain.
    start   = chain.index(provider) if provider in chain else 0
    ordered = chain[start:] + chain[:start]
    data    = None
    for p in ordered:
        if not os.getenv(key_env[p]):
            continue
        try:
            data = _run_provider(p, image_bytes, hint, nvidia_model, lang=lang, platform=platform)
            if p != provider:
                logger.info("Fell back from %s to %s (quota)", provider, p)
            break
        except RuntimeError as e:
            logger.warning("AI RuntimeError (provider=%s), trying next: %s", p, e)
            continue
        except json.JSONDecodeError as e:
            logger.warning("AI JSON decode error (provider=%s), trying next: %s", p, e)
            continue
        except Exception as e:
            logger.exception("AI error (provider=%s): %s", p, e)
            continue
    if data is None:
        return jsonify({"error": "All AI providers are over quota right now. Please try again later."}), 503

    increment_usage(sid)

    try:
        if not is_guest() and platform == "etsy":
            tq = data.get("taxonomy_query", "")
            tax_id, tax_path = _find_taxonomy_id(tq)
            if not tax_id and tq:
                for word in tq.split():
                    if len(word) > 3:
                        tax_id, tax_path = _find_taxonomy_id(word)
                        if tax_id:
                            break
            data["taxonomy_id"]   = tax_id
            data["taxonomy_path"] = tax_path
            sp_resp = requests.get(
                f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/shipping-profiles",
                headers=etsy_headers(), timeout=HTTP_TIMEOUT,
            )
            data["shipping_profiles"] = sp_resp.json().get("results", []) if sp_resp.ok else []
        else:
            data["taxonomy_id"]       = None
            data["taxonomy_path"]     = None
            data["shipping_profiles"] = []
    except Exception as e:
        logger.exception("Post-generation enrichment error: %s", e)
        data["taxonomy_id"]       = None
        data["taxonomy_path"]     = None
        data["shipping_profiles"] = []

    data["image_previews"] = [
        "data:image/jpeg;base64," + base64.b64encode(b).decode()
        for b in image_bytes
    ]
    return jsonify(data)

@app.route("/api/taxonomy")
@limiter.limit("30 per minute")
def api_taxonomy():
    redir = require_connection()
    if redir: return jsonify([]), 401
    q = str(request.args.get("q", "")).strip().lower()[:100]
    r = requests.get(
        "https://openapi.etsy.com/v3/application/seller-taxonomy/nodes",
        headers=etsy_headers(), timeout=HTTP_TIMEOUT,
    )
    if not r.ok:
        return jsonify([])
    def flatten(nodes, path=""):
        for n in nodes:
            full = f"{path} > {n['name']}" if path else n["name"]
            if not q or q in full.lower():
                yield {"id": n["id"], "path": full}
            if n.get("children"):
                yield from flatten(n["children"], full)
    return jsonify(list(flatten(r.json().get("results", [])))[:40])

@app.route("/api/publish", methods=["POST"])
@limiter.limit("20 per minute")
def api_publish():
    redir = require_connection()
    if redir: return jsonify({"error": "Not connected"}), 401

    raw  = request.get_json(silent=True)
    if not raw:
        return jsonify({"error": "Invalid request"}), 400

    data, err = validate_listing_input(raw)
    if err:
        return jsonify({"error": err}), 400

    # Fetch shop's readiness state — only include if the API returns one
    readiness_id = None
    try:
        rs = requests.get(
            f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/readiness-state-definitions",
            headers=etsy_headers(), timeout=HTTP_TIMEOUT,
        )
        if rs.ok:
            results = rs.json().get("results", [])
            if results:
                readiness_id = results[0]["readiness_state_id"]
    except Exception:
        pass

    payload = {
        "title":             data["title"],
        "description":       data["description"],
        "price":             data["price"],
        "quantity":          1,
        "who_made":          "i_did",
        "when_made":         "made_to_order",
        "taxonomy_id":       data["taxonomy_id"],
        "is_supply":         False,
        "should_auto_renew": True,
        "type":              "physical",
        "state":             "draft",
        "tags":              data["tags"],
        "materials":         data["materials"],
    }
    if readiness_id:
        payload["readiness_state_id"] = readiness_id
    if data["shipping_profile_id"]:
        payload["shipping_profile_id"] = data["shipping_profile_id"]

    r = requests.post(
        f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/listings",
        headers={**etsy_headers(), "Content-Type": "application/json"},
        json=payload, timeout=HTTP_TIMEOUT,
    )
    if not r.ok:
        logger.error("Etsy create listing error: %s", r.text)
        try:
            etsy_msg = r.json().get("error", "") or r.json().get("error_description", "")
        except Exception:
            etsy_msg = ""
        user_msg = etsy_msg if etsy_msg else "Failed to create listing on Etsy. Check your shop settings."
        return jsonify({"error": user_msg}), 400

    listing_id = r.json()["listing_id"]

    # Upload images
    os.makedirs("images", exist_ok=True)
    access_token = session.get("access_token") or ((_mobile_auth() or {}).get("access_token"))
    safe_lid = str(int(listing_id))  # ensure numeric, no path traversal
    for i, b64 in enumerate(data["image_previews"][:10], 1):
        path = None
        try:
            if "," in b64:
                b64 = b64.split(",", 1)[1]
            img_bytes = base64.b64decode(b64)
            if len(img_bytes) > 10 * 1024 * 1024:
                continue
            if not _is_valid_image_bytes(img_bytes):
                logger.warning("Skipping invalid image bytes for listing %s", safe_lid)
                continue
            with tempfile.NamedTemporaryFile(suffix=".jpg", dir="images", delete=False) as tmp:
                tmp.write(img_bytes)
                path = tmp.name
            with open(path, "rb") as f:
                requests.post(
                    f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/listings/{safe_lid}/images",
                    headers={"x-api-key": ETSY_API_KEY_HEADER,
                             "Authorization": f"Bearer {access_token}"},
                    files={"image": f},
                    data={"rank": i},
                    timeout=HTTP_TIMEOUT,
                )
            time.sleep(0.2)
        except Exception as e:
            logger.error("Image upload error for listing %s: %s", safe_lid, e)
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    # Personalization
    pi = data["personalization_instructions"]
    if pi:
        requests.post(
            f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/listings/{safe_lid}/personalization",
            headers={**etsy_headers(), "Content-Type": "application/json"},
            json={"personalization_questions": [{
                "question_text": "Personalization",
                "instructions":  pi,
                "question_type": "text_input",
                "required":      False,
                "max_allowed_characters": 256,
            }]},
            timeout=HTTP_TIMEOUT,
        )

    return jsonify({"success": True, "listing_id": listing_id})

# ── Template ──────────────────────────────────────────────────────────────────

@app.route("/api/template", methods=["GET", "POST"])
@limiter.limit("60 per minute")
def api_template():
    redir = require_connection()
    if redir: return jsonify({"error": "Not connected"}), 401
    sid = str(shop_id())
    if request.method == "GET":
        return jsonify(get_template(sid))
    body = request.get_json(silent=True) or {}
    save_template(sid, {
        "shipping_profile_id": body.get("shipping_profile_id"),
        "price":               body.get("price"),
        "materials":           [str(m)[:45]  for m in body.get("materials",  [])[:13]],
        "tags":                [str(t)[:20]  for t in body.get("tags",       [])[:13]],
        "personalization_instructions": str(body.get("personalization_instructions", ""))[:256],
        "brand_tone":          str(body.get("brand_tone", ""))[:80],
        "material_phrases":    str(body.get("material_phrases", ""))[:500],
        "production_time":     str(body.get("production_time", ""))[:120],
        "shipping_note":       str(body.get("shipping_note", ""))[:240],
        "brand_cta":           str(body.get("brand_cta", ""))[:160],
    })
    return jsonify({"success": True})

# ── Sellability AI tools ──────────────────────────────────────────────────────

def _consume_improve_allowance():
    sid = usage_shop_id()
    allowed, remaining = can_improve(sid)
    if not allowed:
        return sid, jsonify({"error": "premium_required", "remaining": 0}), 403
    return sid, None, None

def _listing_fields_from_body(body: dict) -> dict:
    return {
        "title":       str(body.get("title", ""))[:140],
        "description": str(body.get("description", ""))[:5000],
        "tags":        [str(t)[:20] for t in body.get("tags", [])[:13]],
        "materials":   [str(m)[:45] for m in body.get("materials", [])[:13]],
        "price":       str(body.get("price", ""))[:30],
        "category":    str(body.get("category", ""))[:160],
    }

@app.route("/api/improve-listing", methods=["POST"])
@limiter.limit("20 per minute")
def api_improve_listing():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    sid, err_resp, err_code = _consume_improve_allowance()
    if err_resp is not None:
        return err_resp, err_code

    body = request.get_json(silent=True) or {}
    action = body.get("action", "title_seo")
    lang = body.get("lang", "en")
    if lang not in _LANG_NAMES:
        lang = "en"
    fields = _listing_fields_from_body(body)

    actions = {
        "title_seo": "Rewrite only the title to be more searchable for Etsy while staying under 140 characters.",
        "description_warm": "Rewrite only the description with a warmer handmade seller tone. Keep it factual and buyer-friendly.",
        "tags": "Generate exactly 13 Etsy tags, each 20 characters or fewer, focused on buyer search phrases.",
        "shorten_etsy": "Shorten title and description for Etsy clarity while preserving important keywords.",
    }
    if action not in actions:
        return jsonify({"error": "Invalid action"}), 400

    prompt = (
        "You are improving an Etsy listing. "
        f"Write output in {_LANG_NAMES[lang]}. "
        f"Task: {actions[action]}\n\n"
        "Return ONLY valid JSON with keys title, description, tags. "
        "Keep unchanged fields identical where the task does not affect them.\n\n"
        + json.dumps(fields, ensure_ascii=False)
    )
    try:
        data = _run_text_json(prompt)
    except RuntimeError as e:
        if "no_text_ai_provider" in str(e):
            return jsonify({"error": "No AI provider configured for improvements."}), 503
        raise
    except Exception as e:
        logger.exception("Improve error: %s", e)
        return jsonify({"error": safe_error(str(e))}), 500

    if not has_premium_access():
        increment_improve_usage(sid)
    return jsonify(data)

@app.route("/api/listing-variants", methods=["POST"])
@limiter.limit("10 per minute")
def api_listing_variants():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    if not has_premium_access():
        return jsonify({"error": "premium_required"}), 403

    body = request.get_json(silent=True) or {}
    lang = body.get("lang", "en")
    if lang not in _LANG_NAMES:
        lang = "en"
    fields = _listing_fields_from_body(body)
    prompt = (
        "Create three distinct Etsy listing variants from the current listing. "
        f"Write output in {_LANG_NAMES[lang]}. "
        "Return ONLY valid JSON in this shape: "
        "{\"variants\":[{\"name\":\"SEO-focused\",\"title\":\"...\",\"description\":\"...\",\"tags\":[\"13 tags\"]},"
        "{\"name\":\"Emotional handmade\",\"title\":\"...\",\"description\":\"...\",\"tags\":[\"13 tags\"]},"
        "{\"name\":\"Gift-focused\",\"title\":\"...\",\"description\":\"...\",\"tags\":[\"13 tags\"]}]}.\n\n"
        + json.dumps(fields, ensure_ascii=False)
    )
    try:
        return jsonify(_run_text_json(prompt))
    except RuntimeError as e:
        if "no_text_ai_provider" in str(e):
            return jsonify({"error": "No AI provider configured for variants."}), 503
        raise
    except Exception as e:
        logger.exception("Variant error: %s", e)
        return jsonify({"error": safe_error(str(e))}), 500

def _photo_variant_prompts(meta: dict) -> list[str]:
    name = str(meta.get("title") or "handmade product")[:120]
    material = ", ".join(meta.get("materials") or [])[:160] or "visible handmade materials"
    colors = ", ".join(meta.get("colors") or [])[:120] or "visible colors"
    category = str(meta.get("category") or meta.get("taxonomy_path") or "e-commerce")[:120]
    return [
        f"Professional product photography, {name}, {colors}, {material}, 85mm lens, soft diffused studio lighting, pure white background, centered product, sharp commercial e-commerce photo.",
        f"Professional lifestyle product photography, {name}, {material}, warm natural window light, minimalist {category} themed interior, product remains the clear hero, shallow depth of field.",
        f"E-commerce product photography, {name}, seasonal gift-ready scene, soft golden-hour lighting, tasteful background bokeh, product centered and unchanged, high resolution commercial shot.",
    ]

def _fal_generate_variant(image_url: str, prompt: str) -> str:
    resp = requests.post(
        "https://fal.run/fal-ai/flux-pro/kontext",
        headers={"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"},
        json={
            "prompt": prompt,
            "image_url": image_url,
            "num_images": 1,
            "sync_mode": True,
            "output_format": "jpeg",
            "aspect_ratio": "1:1",
            "guidance_scale": 3.5,
            "safety_tolerance": "2",
        },
        timeout=(10, 90),
    )
    if not resp.ok:
        logger.error("fal.ai image error: %s", resp.text[:500])
        raise RuntimeError("Photo generation provider failed.")
    data = resp.json()
    images = data.get("images") or []
    if not images:
        raise RuntimeError("Photo generation returned no images.")
    url = images[0].get("url", "")
    if url.startswith("data:image/"):
        return url
    img_resp = requests.get(url, timeout=HTTP_TIMEOUT)
    if not img_resp.ok:
        raise RuntimeError("Could not fetch generated photo.")
    return "data:image/jpeg;base64," + base64.b64encode(img_resp.content).decode()

@app.route("/api/generate-photos", methods=["POST"])
@limiter.limit("5 per minute")
def api_generate_photos():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    if not has_pro_access():
        return jsonify({"error": "pro_required"}), 403
    if not FAL_KEY:
        return jsonify({"error": "Photo generation is not configured yet."}), 503

    sid = str(shop_id()) if shop_id() else (guest_shop_id() if is_guest() else None)
    if not sid:
        return jsonify({"error": "Invalid shop"}), 400
    allowed, remaining = can_generate_photo_variants(sid, PHOTO_VARIANT_COUNT)
    if not allowed:
        return jsonify({"error": "photo_limit_reached", "remaining": remaining}), 403

    body = request.get_json(silent=True) or {}
    image_url = str(body.get("image", ""))
    if not image_url.startswith("data:image/"):
        return jsonify({"error": "A generated or uploaded product image is required."}), 400

    prompts = _photo_variant_prompts(body)
    try:
        variants = [
            {"label": label, "image": _fal_generate_variant(image_url, prompt)}
            for label, prompt in zip(("White background", "Lifestyle", "Seasonal gift"), prompts)
        ]
    except Exception as e:
        logger.exception("Photo variant error: %s", e)
        return jsonify({"error": safe_error(str(e))}), 500

    increment_photo_variant_usage(sid, len(variants))
    return jsonify({"variants": variants, "remaining": max(0, remaining - len(variants))})

# ── Translation ───────────────────────────────────────────────────────────────

def _translate_ai(fields: dict, target_lang: str) -> dict:
    lang_names = {"de": "German", "tr": "Turkish", "en": "English"}
    prompt = (
        f"Translate this Etsy listing to {lang_names[target_lang]}. "
        "Keep tags 1-3 words each. Keep title under 140 chars. "
        "Return ONLY valid JSON with the same keys:\n" + json.dumps(fields)
    )
    # Try Gemini, then optionally fall back to paid OpenAI if explicitly enabled.
    if os.getenv("GEMINI_API_KEY"):
        try:
            from google import genai as ggenai
            from google.genai.errors import ClientError
            client = ggenai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            resp = client.models.generate_content(model="gemini-2.5-flash", contents=[prompt])
            return _parse_ai_json(resp.text)
        except ClientError as e:
            if "429" not in str(e) and "RESOURCE_EXHAUSTED" not in str(e):
                raise
    if ALLOW_PAID_OPENAI and os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    raise RuntimeError("No AI provider available for translation")

@app.route("/api/translate", methods=["POST"])
@limiter.limit("30 per minute")
def api_translate():
    redir = require_connection()
    if redir: return jsonify({"error": "Not connected"}), 401
    sid, err_resp, err_code = _consume_improve_allowance()
    if err_resp is not None:
        return err_resp, err_code
    body = request.get_json(silent=True) or {}
    lang = body.get("lang", "de")
    if lang not in ("de", "tr", "en"):
        return jsonify({"error": "Invalid language"}), 400
    fields = {
        "title":       str(body.get("title",       ""))[:140],
        "description": str(body.get("description", ""))[:3000],
        "tags":        [str(t)[:20] for t in body.get("tags", [])[:13]],
    }
    try:
        data = _translate_ai(fields, lang)
        if not has_premium_access():
            increment_improve_usage(sid)
        return jsonify(data)
    except Exception as e:
        logger.exception("Translate error: %s", e)
        return jsonify({"error": safe_error(str(e))}), 500

# ── Bulk generate ─────────────────────────────────────────────────────────────

@app.route("/bulk")
def bulk():
    redir = require_connection()
    if redir: return redir
    if not has_premium_access():
        return redirect(url_for("upgrade", bulk_required=1))
    return render_template("bulk.html", shop_name=session.get("shop_name"))

@app.route("/api/bulk-generate", methods=["POST"])
@limiter.limit("5 per minute; 30 per day")
def api_bulk_generate():
    redir = require_connection()
    if redir: return jsonify({"error": "Not connected"}), 401
    if not has_premium_access():
        return jsonify({"error": "premium_required"}), 403
    sid = str(shop_id())
    allowed, _ = can_generate(sid)
    if not allowed:
        return jsonify({"error": "free_limit_reached"}), 403

    images   = request.files.getlist("images")
    hint     = str(request.form.get("hint", "")).strip()[:200]
    hint     = _merge_hint_with_style(hint, sid)
    provider = request.form.get("provider", "nvidia")
    lang     = request.form.get("lang", "en")
    platform = request.form.get("platform", "etsy")
    chain    = _PROVIDER_CHAIN_PREMIUM if has_premium_access() else _PROVIDER_CHAIN_FREE
    if provider not in chain:
        provider = chain[0]
    if lang not in _LANG_NAMES:
        lang = "en"
    if platform not in PLATFORM_PROMPTS:
        platform = "etsy"

    if not images or not images[0].filename:
        return jsonify({"error": "No image provided"}), 400

    image_bytes = []
    for img in images[:5]:
        if img.content_type not in ALLOWED_IMAGE_TYPES:
            return jsonify({"error": f"File type not allowed: {img.content_type}"}), 400
        data_b = img.read()
        if not _is_valid_image_bytes(data_b):
            return jsonify({"error": "File content does not match an allowed image format."}), 400
        image_bytes.append(data_b)

    key_env = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "nvidia": "NVIDIA_API_KEY"}
    start   = chain.index(provider) if provider in chain else 0
    ordered = chain[start:] + chain[:start]
    data    = None
    for p in ordered:
        if not os.getenv(key_env[p]):
            continue
        try:
            data = _run_provider(p, image_bytes, hint, "llama-90b", lang=lang, platform=platform)
            break
        except RuntimeError as e:
            logger.warning("Bulk AI RuntimeError (provider=%s), trying next: %s", p, e)
            continue
        except Exception as e:
            logger.exception("Bulk AI error (provider=%s): %s", p, e)
            continue
    if data is None:
        return jsonify({"error": "All AI providers are over quota."}), 503

    increment_usage(sid)

    try:
        if platform == "etsy":
            tq = data.get("taxonomy_query", "")
            tax_id, tax_path = _find_taxonomy_id(tq)
            if not tax_id and tq:
                for word in tq.split():
                    if len(word) > 3:
                        tax_id, tax_path = _find_taxonomy_id(word)
                        if tax_id:
                            break
            data["taxonomy_id"]   = tax_id
            data["taxonomy_path"] = tax_path
        else:
            data["taxonomy_id"]   = None
            data["taxonomy_path"] = None
    except Exception as e:
        logger.exception("Bulk post-generation enrichment error: %s", e)
        data["taxonomy_id"]   = None
        data["taxonomy_path"] = None

    data["image_previews"] = [
        "data:image/jpeg;base64," + base64.b64encode(b).decode()
        for b in image_bytes
    ]
    return jsonify(data)

# ── Upgrade / Stripe ──────────────────────────────────────────────────────────

@app.route("/upgrade")
def upgrade():
    redir = require_connection()
    if redir: return redir
    sid = usage_shop_id()
    from db import FREE_LIMIT as _FL
    limit = _FL if (not is_guest() or is_email_verified()) else GUEST_FREE_LIMIT
    _, remaining = can_generate(sid, limit)
    from db import get_shop
    s = get_shop(sid) or {}
    use_try = _is_try_domain()
    return render_template(
        "upgrade.html",
        shop_name=session.get("shop_name"),
        remaining=remaining,
        current_plan=s.get("plan") or "free",
        stripe_key=os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
        has_starter=bool(
            os.getenv("STRIPE_STARTER_PRICE_ID") or os.getenv("STRIPE_STARTER_PRICE_ID_TRY")
        ),
        has_pro=bool(
            os.getenv("STRIPE_PRO_PRICE_ID") or os.getenv("STRIPE_PRO_PRICE_ID_TRY")
            or os.getenv("STRIPE_PRICE_ID")
        ),
        has_starter_annual=bool(
            os.getenv("STRIPE_STARTER_ANNUAL_PRICE_ID") or os.getenv("STRIPE_STARTER_ANNUAL_PRICE_ID_TRY")
        ),
        has_pro_annual=bool(
            os.getenv("STRIPE_PRO_ANNUAL_PRICE_ID") or os.getenv("STRIPE_PRO_ANNUAL_PRICE_ID_TRY")
        ),
        use_try=use_try,
    )

_PLAN_PRICE_IDS = {
    "starter":        "STRIPE_STARTER_PRICE_ID",
    "pro":            "STRIPE_PRO_PRICE_ID",
    "starter_annual": "STRIPE_STARTER_ANNUAL_PRICE_ID",
    "pro_annual":     "STRIPE_PRO_ANNUAL_PRICE_ID",
}
_PLAN_PRICE_IDS_TRY = {
    "starter":        "STRIPE_STARTER_PRICE_ID_TRY",
    "pro":            "STRIPE_PRO_PRICE_ID_TRY",
    "starter_annual": "STRIPE_STARTER_ANNUAL_PRICE_ID_TRY",
    "pro_annual":     "STRIPE_PRO_ANNUAL_PRICE_ID_TRY",
}

@app.route("/stripe/checkout", methods=["POST"])
@limiter.limit("10 per minute")
def stripe_checkout():
    redir = require_connection()
    if redir: return jsonify({"error": "Not connected"}), 401
    if not os.getenv("STRIPE_SECRET_KEY"):
        return jsonify({"error": "Stripe not configured"}), 503
    body = request.get_json(silent=True) or {}
    plan = body.get("plan", "pro")
    if plan not in _PLAN_PRICE_IDS:
        plan = "pro"
    price_map = _PLAN_PRICE_IDS_TRY if _is_try_domain() else _PLAN_PRICE_IDS
    price_env = price_map.get(plan, "STRIPE_PRO_PRICE_ID")
    price_id  = os.getenv(price_env) or os.getenv("STRIPE_PRICE_ID")
    base_plan = plan.replace("_annual", "")
    if not price_id:
        return jsonify({"error": "Stripe price not configured for this plan"}), 503
    try:
        import stripe as stripe_lib
        stripe_lib.api_key = os.getenv("STRIPE_SECRET_KEY")
        base = request.url_root.rstrip("/")
        checkout = stripe_lib.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=base + f"/upgrade?success=1&plan={plan}",
            cancel_url=base  + "/upgrade?cancelled=1",
            client_reference_id=str(usage_shop_id()),
            metadata={"shop_id": str(usage_shop_id()),
                      "shop_name": session.get("shop_name", ""),
                      "plan": base_plan},
        )
        return jsonify({"url": checkout.url})
    except Exception as e:
        logger.exception("Stripe checkout error: %s", e)
        return jsonify({"error": safe_error(str(e))}), 500

@app.route("/api/stripe/checkout", methods=["POST"])
@limiter.limit("10 per minute")
def api_stripe_checkout():
    """Mobile-friendly Stripe checkout. Returns {"url": "..."} for the app to open in Safari."""
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    if is_guest() and not is_email_verified():
        return jsonify({"error": "Please connect with Etsy or verify your email to upgrade."}), 403
    if not os.getenv("STRIPE_SECRET_KEY"):
        return jsonify({"error": "Stripe not configured"}), 503
    body = request.get_json(silent=True) or {}
    plan = body.get("plan", "pro")
    if plan not in _PLAN_PRICE_IDS:
        plan = "pro"
    price_map = _PLAN_PRICE_IDS_TRY if _is_try_domain() else _PLAN_PRICE_IDS
    price_env = price_map.get(plan, "STRIPE_PRO_PRICE_ID")
    price_id  = os.getenv(price_env) or os.getenv("STRIPE_PRICE_ID")
    if not price_id:
        return jsonify({"error": "Stripe price not configured for this plan"}), 503
    base_plan = plan.replace("_annual", "")
    sid = str(usage_shop_id())
    mob = _mobile_auth()
    shop_name_val = (mob or {}).get("shop_name", "") or session.get("shop_name", "")
    base = request.url_root.rstrip("/")
    try:
        import stripe as stripe_lib
        stripe_lib.api_key = os.getenv("STRIPE_SECRET_KEY")
        checkout = stripe_lib.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=base + f"/upgrade/mobile/return?status=success&plan={plan}",
            cancel_url=base  + "/upgrade/mobile/return?status=cancel",
            client_reference_id=sid,
            metadata={"shop_id": sid, "shop_name": shop_name_val, "plan": base_plan},
        )
        return jsonify({"url": checkout.url})
    except Exception as e:
        logger.exception("Stripe checkout error (mobile): %s", e)
        return jsonify({"error": safe_error(str(e))}), 500

@app.route("/upgrade/mobile/return")
def upgrade_mobile_return():
    """Redirect page shown after Stripe checkout on mobile. Deeplinks back to the app."""
    status = request.args.get("status", "cancel")
    plan   = request.args.get("plan", "pro")
    if status == "success":
        deeplink = f"easylisting://upgrade/success?plan={urllib.parse.quote(plan)}"
        msg      = "Payment complete! Returning to EasyListing…"
    else:
        deeplink = "easylisting://upgrade/cancel"
        msg      = "Returning to EasyListing…"
    html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="1;url={deeplink}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EasyListing</title>
<style>body{{font-family:-apple-system,sans-serif;display:flex;align-items:center;
justify-content:center;height:100vh;margin:0;background:#F6F7FB;color:#111827}}</style>
</head><body>
<p>{msg}</p>
<script>setTimeout(()=>window.location='{deeplink}',800)</script>
</body></html>"""
    return html, 200

@app.route("/stripe/webhook", methods=["POST"])
@csrf.exempt
def stripe_webhook():
    if not os.getenv("STRIPE_SECRET_KEY"):
        return jsonify({"error": "not configured"}), 503
    import stripe as stripe_lib
    stripe_lib.api_key = os.getenv("STRIPE_SECRET_KEY")
    payload = request.get_data()
    sig     = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe_lib.Webhook.construct_event(
            payload, sig, os.getenv("STRIPE_WEBHOOK_SECRET", "")
        )
    except Exception as e:
        logger.error("Stripe webhook invalid: %s", e)
        return jsonify({"error": "Invalid signature"}), 400

    obj = event["data"]["object"]
    if event["type"] == "checkout.session.completed":
        if obj.get("payment_status") != "paid":
            return jsonify({"received": True})  # wait for invoice.payment_succeeded
        sid  = obj.get("client_reference_id") or obj.get("metadata", {}).get("shop_id")
        plan = obj.get("metadata", {}).get("plan", "pro")
        if sid:
            set_premium(sid, obj.get("customer"), obj.get("subscription"), True, plan)
            logger.info("Premium activated for shop %s (plan=%s)", sid, plan)
    elif event["type"] in ("customer.subscription.deleted",
                           "customer.subscription.paused"):
        s = get_shop_by_stripe_customer(obj.get("customer"))
        if s:
            set_premium(s["shop_id"], obj["customer"], obj["id"], False)
            logger.info("Premium deactivated for shop %s", s["shop_id"])

    return jsonify({"received": True})

# ── Blueprints ────────────────────────────────────────────────────────────────
from apis.pages import bp as pages_bp
from apis.admin import bp as admin_bp
from apis.trendyol import bp as trendyol_bp
app.register_blueprint(pages_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(trendyol_bp)


if __name__ == "__main__":
    os.makedirs("images", exist_ok=True)
    app.run(debug=os.getenv("ENV") != "production", port=5050)
