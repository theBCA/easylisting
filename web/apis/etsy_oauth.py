"""Etsy OAuth flow: connect page, PKCE start, callback, disconnect."""
import time
import base64
import hashlib
import secrets
import urllib.parse

import requests
from flask import (
    Blueprint, request, redirect, url_for, session, render_template,
)

from extensions import limiter
from core.config import logger, HTTP_TIMEOUT
from core.etsy import (
    ETSY_CLIENT_ID, ETSY_SCOPES, ETSY_API_KEY_HEADER, REDIRECT_URI,
    _dynamic_redirect_uri,
)
from core.session import is_connected, is_guest, guest_shop_id
from db import ensure_shop, create_mobile_token

bp = Blueprint("etsy_oauth", __name__)


@bp.route("/connect")
def connect():
    if is_connected():
        return redirect(url_for("listings.index"))
    return render_template("connect.html")

@bp.route("/auth/start")
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

@bp.route("/auth/callback")
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

    return redirect(url_for("listings.index"))

@bp.route("/disconnect")
def disconnect():
    session.clear()
    return redirect(url_for("etsy_oauth.connect"))
