"""Admin authentication.

Two accepted credentials, checked in this order:

1. **Cloudflare Access JWT** — for the browser dashboard. Cloudflare injects a
   signed `Cf-Access-Jwt-Assertion` header (and a `CF_Authorization` cookie) on
   every request it proxies to `/admin/*`. We verify the signature against the
   team's public keys and check the audience + email allowlist. We do NOT trust
   the plaintext `Cf-Access-Authenticated-User-Email` header on its own, because
   the Railway origin is reachable directly (bypassing Cloudflare) and that
   header could be forged — only a signature-verified JWT is trusted.

2. **Bearer token** — for API / scripts / diagnostics. `Authorization: Bearer
   <ADMIN_TOKEN>`, compared in constant time.

Config (env):
  ADMIN_TOKEN            shared secret for the Bearer path
  CF_ACCESS_TEAM_DOMAIN  e.g. "yourteam.cloudflareaccess.com"
  CF_ACCESS_AUD          the Access application AUD tag (per service)
  ADMIN_EMAILS           extra comma-separated allowlisted emails
"""
import os
import secrets

from flask import request

_OWNER_EMAILS = ["berkcem123@gmail.com", "berkcemarslan@gmail.com"]


def _admin_emails():
    extra = [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
    return {e.lower() for e in _OWNER_EMAILS} | set(extra)


_jwks_client = None


def _get_jwks_client():
    """Lazily build a cached PyJWKClient for the team's signing keys."""
    global _jwks_client
    team = os.getenv("CF_ACCESS_TEAM_DOMAIN", "").strip()
    if not team:
        return None
    if _jwks_client is None:
        from jwt import PyJWKClient
        _jwks_client = PyJWKClient(f"https://{team}/cdn-cgi/access/certs")
    return _jwks_client


def _cf_access_email():
    """Return the verified admin email from a Cloudflare Access JWT, or None."""
    token = (request.headers.get("Cf-Access-Jwt-Assertion", "")
             or request.cookies.get("CF_Authorization", ""))
    aud    = os.getenv("CF_ACCESS_AUD", "").strip()
    client = _get_jwks_client()
    if not token or not aud or client is None:
        return None
    try:
        import jwt
        signing_key = client.get_signing_key_from_jwt(token)
        data = jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=aud)
    except Exception:
        return None
    email = (data.get("email") or "").lower()
    return email if email in _admin_emails() else None


def _bearer_ok():
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected:
        return False
    tok = request.headers.get("Authorization", "").removeprefix("Bearer ")
    return bool(tok) and secrets.compare_digest(tok, expected)


def is_admin():
    """True if the request carries a valid admin credential (CF Access or Bearer)."""
    return bool(_cf_access_email()) or _bearer_ok()
