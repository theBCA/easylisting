"""Shared configuration constants and logging setup.

Plain, app-agnostic values read from the environment. Etsy OAuth config lives in
core/etsy.py; input-validation limits live in core/validators.py.
"""
import os
import logging

from dotenv import load_dotenv

# Load .env before reading any environment-derived constant below. In production
# Railway injects real env vars (load_dotenv is a no-op); locally this reads .env.
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("easylisting")

# ── Network ──────────────────────────────────────────────────────────────────
HTTP_TIMEOUT = (5, 30)  # (connect timeout, read timeout) in seconds

# ── Guest / usage ────────────────────────────────────────────────────────────
GUEST_FREE_LIMIT    = 3
GUEST_ID_COOKIE     = "easylisting_guest_id"
GUEST_ID_MAX_AGE    = 60 * 60 * 24 * 180  # 180 days
PHOTO_VARIANT_COUNT = 3

# ── Etsy shipping profile (Readiness state) ──────────────────────────────────
READINESS_ID = 1489219211571

# ── AI / image generation ────────────────────────────────────────────────────
FAL_KEY           = os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
ALLOW_PAID_OPENAI = os.getenv("ALLOW_PAID_OPENAI", "false").lower() == "true"
