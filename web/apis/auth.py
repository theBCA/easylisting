"""Auth: guest sessions, mobile tokens, fingerprint, magic-link email login."""
import os
import secrets
import hashlib

from flask import (
    Blueprint, request, jsonify, session, redirect, url_for, render_template,
)
from flask_wtf.csrf import generate_csrf

from extensions import csrf, limiter
from core.config import logger
from core.domains import _ip_hash
from core.etsy import REDIRECT_URI
from core.email import send_email
from core.session import (
    is_connected, is_guest, guest_shop_id, is_email_verified, is_authorized,
)
from db import (
    ensure_shop, create_mobile_token, delete_mobile_token, log_abuse_signal,
    get_shop, get_or_create_email_shop, get_email_shop, create_magic_link,
    use_magic_link, count_recent_magic_links, add_marketing_consent, save_fp_session,
    set_premium,
)

bp = Blueprint("auth", __name__)


@bp.route("/guest")
@limiter.limit("20 per minute")
def start_guest():
    session.clear()
    session["guest"] = True
    log_abuse_signal("new_guest", ip_hash=_ip_hash())
    return redirect(url_for("listings.index"))

# ── Mobile-specific endpoints ──────────────────────────────────────────────────

@bp.route("/api/csrf-token")
@limiter.limit("60 per minute")
def api_csrf_token():
    """Return a CSRF token for the current session. iOS app calls this once, then
    includes the returned token as X-CSRFToken on every subsequent POST request."""
    return jsonify({"csrf_token": generate_csrf()})


@bp.route("/auth/mobile/guest", methods=["POST"])
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


@bp.route("/auth/mobile/logout", methods=["POST"])
@csrf.exempt
@limiter.limit("30 per minute")
def auth_mobile_logout():
    """Invalidate a mobile token (logout from iOS app)."""
    tok = request.headers.get("X-Mobile-Token", "")
    if tok:
        delete_mobile_token(tok)
    return jsonify({"ok": True})

@bp.route("/api/fingerprint", methods=["POST"])
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

@bp.route("/api/magic-link", methods=["POST"])
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

    # App Store / web review account: instant pro login with no email round-trip
    # (reviewers can't access our inbox). Gated to one env-configured email and
    # backed by a dedicated demo shop, recreated on demand so it survives the
    # ephemeral SQLite on each deploy.
    review_email = os.getenv("APPSTORE_REVIEW_EMAIL", "").strip().lower()
    if review_email and email == review_email:
        review_sid = "review_appstore"
        ensure_shop(review_sid, "App Review")
        set_premium(review_sid, "review", "review", True, "pro")
        logger.info("App review account login (mobile=%s)", is_mobile)
        if is_mobile:
            mob_tok = create_mobile_token(
                shop_id=review_sid, is_guest=True, is_email=True,
            )
            return jsonify({"ok": True, "already_verified": True, "mobile_token": mob_tok})
        session["guest"]          = True
        session["email_verified"] = True
        session["email_shop_id"]  = review_sid
        session.permanent         = True
        return jsonify({"ok": True, "already_verified": True})

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
        # Email clients strip custom-scheme (easylisting://) links, so the button
        # would be dead on mobile. Send an https link that lands on a browser
        # handoff page (?m=1), which then hands off to the app via the custom
        # scheme. The token is consumed by the app, not the browser landing.
        link = f"{base}/auth/magic?token={token}&m=1"
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


@bp.route("/auth/magic")
@limiter.limit("20 per minute")
def auth_magic():
    token     = request.args.get("token", "")
    is_mobile = bool(request.headers.get("X-Mobile-Request"))

    # Mobile-originated link (?m=1) opened in a browser → hand off to the iOS app
    # via the custom URL scheme. Do NOT consume the token here; the app consumes
    # it through this same endpoint with the X-Mobile-Request header.
    if request.args.get("m") == "1" and not is_mobile and token:
        is_tr = bool(request.host and "kolaylistele" in request.host)
        return render_template("magic_handoff.html", token=token, is_tr=is_tr)

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


@bp.route("/api/email-verified")
@limiter.limit("30 per minute")
def api_email_verified():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    return jsonify({"verified": is_email_verified()})
