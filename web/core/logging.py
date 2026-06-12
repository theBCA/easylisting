"""Application logging helpers.

Runtime logs go to stdout for Railway. Business events that need querying
belong in DB tables or PostHog; this module is for request/runtime traceability.
"""
import contextvars
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from flask import g, request

_request_id = contextvars.ContextVar("request_id", default="-")
_SENSITIVE_KEYS = (
    "authorization",
    "cookie",
    "csrf",
    "key",
    "password",
    "secret",
    "stripe-signature",
    "token",
)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,80}$")


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = _redact(value)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_logging() -> logging.Logger:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    use_json = os.getenv("LOG_FORMAT", "").lower() == "json" or os.getenv("ENV") == "production"

    handler = logging.StreamHandler(sys.__stdout__)
    handler.addFilter(RequestContextFilter())
    if use_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
        ))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    app_logger = logging.getLogger("easylisting")
    app_logger.setLevel(level)
    if os.getenv("ENV") == "test":
        logging.getLogger("backoff").setLevel(logging.CRITICAL)
        logging.getLogger("posthog").setLevel(logging.CRITICAL)
    return app_logger


logger = configure_logging()


def redact(value: Any) -> Any:
    return _redact(value)


def bind_request_context() -> None:
    rid = request.headers.get("X-Request-ID", "").strip()
    if not _REQUEST_ID_RE.match(rid):
        rid = uuid.uuid4().hex
    _request_id.set(rid)
    g.request_id = rid
    g.request_started_at = time.perf_counter()


def add_request_id_header(resp):
    rid = getattr(g, "request_id", None)
    if rid:
        resp.headers["X-Request-ID"] = rid
    return resp


def log_request_finished(resp) -> None:
    started = getattr(g, "request_started_at", None)
    duration_ms = int((time.perf_counter() - started) * 1000) if started else None
    fields = {
        "method": request.method,
        "path": request.path,
        "status": resp.status_code,
        "duration_ms": duration_ms,
        "host": request.host,
        "ip_hash": _ip_hash_safe(),
        "shop_id": _shop_id_safe(),
    }
    level = logging.WARNING if resp.status_code >= 500 else logging.INFO
    logger.log(level, "request", extra={"extra_fields": fields})


def log_request_exception(sender, exception, **extra) -> None:
    fields = {
        "method": getattr(request, "method", None),
        "path": getattr(request, "path", None),
        "host": getattr(request, "host", None),
        "ip_hash": _ip_hash_safe(),
        "shop_id": _shop_id_safe(),
    }
    logger.error(
        "unhandled_exception",
        exc_info=(type(exception), exception, exception.__traceback__),
        extra={"extra_fields": fields},
    )


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if _is_sensitive_key(str(k)) else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"\b(sk|pk|whsec)_(test|live)_[A-Za-z0-9_]+\b", "[REDACTED]", value)
        value = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/=-]+\b", "Bearer [REDACTED]", value)
    return value


def _is_sensitive_key(key: str) -> bool:
    k = key.lower().replace("_", "-")
    return any(part in k for part in _SENSITIVE_KEYS)


def _ip_hash_safe() -> str | None:
    try:
        from core.domains import _ip_hash
        return _ip_hash()
    except Exception:
        return None


def _shop_id_safe() -> str | None:
    try:
        from core.session import usage_shop_id
        sid = usage_shop_id()
        return str(sid) if sid else None
    except Exception:
        return None
