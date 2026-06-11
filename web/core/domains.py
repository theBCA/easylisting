"""Request-derived helpers: client IP hashing and domain detection."""
import hashlib

from flask import request


def _ip_hash() -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _is_try_domain() -> bool:
    host = request.host or ""
    return host == "kolaylistele.com" or host.endswith(".kolaylistele.com")
