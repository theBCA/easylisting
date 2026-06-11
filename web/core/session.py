"""Session / identity helpers: connection state, guest IDs, plan access.

Builds on core/etsy for mobile-token auth. Reads flask session/request/g directly.
"""
import hashlib
import secrets

from flask import session, request, g, redirect, url_for, current_app, jsonify
from itsdangerous import BadSignature, URLSafeSerializer

from core.config import GUEST_ID_COOKIE
from core.etsy import _mobile_auth
from db import get_shop, ensure_shop, get_fp_session, can_improve


def shop_id():
    if session.get("shop_id"):
        return session["shop_id"]
    mob = _mobile_auth()
    if mob and not mob.get("is_guest"):
        return mob["shop_id"]
    return None

def is_connected():
    if session.get("access_token") and session.get("shop_id"):
        return True
    mob = _mobile_auth()
    return bool(mob and not mob.get("is_guest") and mob.get("access_token"))

def is_guest():
    if session.get("guest"):
        return True
    mob = _mobile_auth()
    return bool(mob and mob.get("is_guest"))

def is_authorized():
    return is_connected() or is_guest()

def _cookie_guest_id():
    token = request.cookies.get(GUEST_ID_COOKIE, "")
    if not token:
        return None
    try:
        guest_id = URLSafeSerializer(current_app.config["SECRET_KEY"], salt="guest-id").loads(token)
    except BadSignature:
        return None
    if not isinstance(guest_id, str) or len(guest_id) < 16:
        return None
    return guest_id

def _get_or_create_guest_id():
    cookie_guest_id = _cookie_guest_id()
    guest_id = session.get("guest_id") or cookie_guest_id
    if not isinstance(guest_id, str) or len(guest_id) < 16:
        guest_id = secrets.token_urlsafe(18)
    if session.get("guest_id") != guest_id:
        session.permanent = True
        session["guest_id"] = guest_id
    if cookie_guest_id != guest_id:
        g.set_guest_id_cookie = guest_id
    return guest_id

def guest_shop_id():
    # Mobile: email-verified guest or plain guest
    mob = _mobile_auth()
    if mob and mob.get("is_guest"):
        if mob.get("is_email") and mob.get("shop_id"):
            return mob["shop_id"]
        if mob.get("guest_id"):
            return mob["guest_id"]
    # Web: Email-verified ID is the most authoritative — set by /auth/magic
    email_id = session.get("email_shop_id")
    if email_id and isinstance(email_id, str) and email_id.startswith("guest_email_"):
        return email_id
    # Fingerprint-based ID survives incognito resets (set by /api/fingerprint)
    fp_id = session.get("fp_guest_id")
    if fp_id and isinstance(fp_id, str) and fp_id.startswith("guest_fp_"):
        return fp_id
    guest_id = _get_or_create_guest_id()
    guest_hash = hashlib.sha256(f"guest:{guest_id}".encode()).hexdigest()[:16]
    return f"guest_{guest_hash}"

def _try_restore_email_session():
    """Restore email-verified session from DB if session was cleared (e.g. server restart)."""
    if session.get("email_verified") and session.get("email_shop_id"):
        return  # already set
    fp_id = session.get("fp_guest_id")
    if fp_id:
        email_shop = get_fp_session(fp_id)
        if email_shop:
            session["email_verified"] = True
            session["email_shop_id"]  = email_shop
            session["guest"]          = True
            session.permanent         = True

def is_email_verified():
    mob = _mobile_auth()
    if mob and mob.get("is_email"):
        return True
    _try_restore_email_session()
    return bool(session.get("email_verified") and session.get("email_shop_id"))

def has_premium_access():
    if is_connected() and shop_id():
        shop = get_shop(str(shop_id()))
        return bool(shop and shop.get("has_premium"))
    if is_guest() and is_email_verified():
        shop = get_shop(guest_shop_id())
        return bool(shop and shop.get("has_premium"))
    return False

def has_pro_access():
    if is_connected() and shop_id():
        shop = get_shop(str(shop_id()))
        plan = (shop.get("plan") or "free") if shop else "free"
        return bool(shop and shop.get("has_premium") and plan == "pro")
    if is_guest() and is_email_verified():
        shop = get_shop(guest_shop_id())
        plan = (shop.get("plan") or "free") if shop else "free"
        return bool(shop and shop.get("has_premium") and plan == "pro")
    return False

def usage_shop_id():
    if is_guest():
        sid = guest_shop_id()
        ensure_shop(sid, "Guest")
        return sid
    sid = shop_id()
    return str(sid) if sid else ""

def require_connection():
    if not is_authorized():
        return redirect(url_for("etsy_oauth.connect"))
    return None


def _consume_improve_allowance():
    sid = usage_shop_id()
    allowed, remaining = can_improve(sid)
    if not allowed:
        return sid, jsonify({"error": "premium_required", "remaining": 0}), 403
    return sid, None, None
