"""Etsy API client: OAuth token refresh, request headers, taxonomy lookup.

Reads session/request/g directly so core/session can build on it without a cycle.
"""
import os
import time

import requests
from flask import session, request, g

from core.config import logger, HTTP_TIMEOUT
from db import update_mobile_token_access, get_by_mobile_token


ETSY_CLIENT_ID      = os.getenv("ETSY_API_KEY")
ETSY_CLIENT_SECRET  = os.getenv("ETSY_SHARED_SECRET", "").strip()
ETSY_API_KEY_HEADER = f"{ETSY_CLIENT_ID}:{ETSY_CLIENT_SECRET}" if ETSY_CLIENT_SECRET else (ETSY_CLIENT_ID or "")
ETSY_SCOPES         = "listings_r listings_w shops_r"
REDIRECT_URI        = os.getenv("REDIRECT_URI", "http://localhost:5050/auth/callback")


def _refresh_etsy_token(mob: dict) -> dict:
    """Refresh an expired Etsy access token for a mobile session. Returns updated row."""
    try:
        resp = requests.post(
            "https://api.etsy.com/v3/public/oauth/token",
            data={
                "grant_type":    "refresh_token",
                "client_id":     ETSY_CLIENT_ID,
                "refresh_token": mob["refresh_token"],
            },
            timeout=HTTP_TIMEOUT,
        )
        if resp.ok:
            j = resp.json()
            new_access  = j.get("access_token")
            new_refresh = j.get("refresh_token")
            expires_at  = int(time.time()) + int(j.get("expires_in", 3600))
            if new_access:
                update_mobile_token_access(mob["token"], new_access, new_refresh, expires_at)
                mob = dict(mob, access_token=new_access,
                           refresh_token=new_refresh or mob.get("refresh_token"),
                           expires_at=expires_at)
        else:
            logger.warning("Etsy token refresh failed (%s) for mobile session", resp.status_code)
    except Exception as e:
        logger.warning("Etsy token refresh error: %s", e)
    return mob

def _refresh_web_etsy_token() -> None:
    """Refresh an expired Etsy access token stored in the web session (in-place)."""
    rt = session.get("refresh_token")
    if not rt:
        return
    try:
        resp = requests.post(
            "https://api.etsy.com/v3/public/oauth/token",
            data={
                "grant_type":    "refresh_token",
                "client_id":     ETSY_CLIENT_ID,
                "refresh_token": rt,
            },
            timeout=HTTP_TIMEOUT,
        )
        if resp.ok:
            j = resp.json()
            new_access  = j.get("access_token")
            new_refresh = j.get("refresh_token")
            expires_at  = int(time.time()) + int(j.get("expires_in", 3600))
            if new_access:
                session["access_token"]  = new_access
                session["refresh_token"] = new_refresh or rt
                session["token_expires"] = expires_at
                logger.info("Web Etsy token refreshed successfully")
        else:
            logger.warning("Web Etsy token refresh failed (%s)", resp.status_code)
    except Exception as e:
        logger.warning("Web Etsy token refresh error: %s", e)

def _mobile_auth() -> dict | None:
    """Return mobile token row for the current request, cached on g. None for web requests."""
    if not hasattr(g, "_mob"):
        tok = request.headers.get("X-Mobile-Token", "")
        mob = get_by_mobile_token(tok) if tok else None
        if (mob and mob.get("access_token") and mob.get("refresh_token")
                and mob.get("expires_at") and time.time() > mob["expires_at"] - 120):
            mob = _refresh_etsy_token(mob)
        g._mob = mob
    return g._mob

def etsy_headers():
    # Auto-refresh web session token if close to expiry (within 2 minutes)
    if session.get("access_token") and session.get("token_expires"):
        if time.time() > session["token_expires"] - 120:
            _refresh_web_etsy_token()
    tok = session.get("access_token") or ((_mobile_auth() or {}).get("access_token") or "")
    return {
        "x-api-key":     ETSY_API_KEY_HEADER,
        "Authorization": f"Bearer {tok}",
    }


def _find_taxonomy_id(query):
    r = requests.get(
        "https://openapi.etsy.com/v3/application/seller-taxonomy/nodes",
        headers=etsy_headers(), timeout=HTTP_TIMEOUT,
    )
    if not r.ok:
        return None, None

    def flatten(nodes, path=""):
        for n in nodes:
            full = f"{path} > {n['name']}" if path else n["name"]
            yield n["id"], full
            if n.get("children"):
                yield from flatten(n["children"], full)

    words = [w for w in query.lower().split() if len(w) > 2]
    best_id, best_path, best_score = None, None, 0

    for nid, path in flatten(r.json().get("results", [])):
        p = path.lower()
        # exact substring match wins immediately
        if query.lower() in p:
            return nid, path
        # score by how many query words appear in the path
        score = sum(1 for w in words if w in p)
        if score > best_score:
            best_score, best_id, best_path = score, nid, path

    return (best_id, best_path) if best_score > 0 else (None, None)


def _dynamic_redirect_uri() -> str:
    """Use the current request's host so the OAuth callback stays on the same domain."""
    host = request.host or ""
    _ALLOWED = {"kolaylistele.com", "easylisting.app"}
    for allowed in _ALLOWED:
        if host == allowed or host.endswith("." + allowed):
            return f"https://{allowed}/auth/callback"
    return REDIRECT_URI  # fallback to env var (local dev)
