"""Shared configuration constants and logging setup.

Plain, app-agnostic values read from the environment. Etsy OAuth config lives in
core/etsy.py; input-validation limits live in core/validators.py.
"""
import os

from dotenv import load_dotenv

# Load .env before reading any environment-derived constant below. In production
# Railway injects real env vars (load_dotenv is a no-op); locally this reads .env.
load_dotenv()

from core.logging import logger

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
# Image-to-image model used for photo variants + Etsy photo sets.
# Gemini 3.1 Flash Image — ~$0.039/img (same pricing tier as 2.5), better quality
# in side-by-side testing. Same google-genai SDK, no separate vendor needed.
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")

# ── Per-plan limits (abuse control) ──────────────────────────────────────────
# Every number is env-overridable so limits can be tuned per Railway service
# without a code change. `listings` and `improve` are LIFETIME for free, MONTHLY
# for paid plans. `photo_images` is the monthly cap on generated product images
# (each Etsy photo set = 6 images; each photo-variant batch = 3).
PLAN_LIMITS = {
    "free": {
        "listings":     int(os.getenv("LIMIT_FREE_LISTINGS", "3")),
        "improve":      int(os.getenv("LIMIT_FREE_IMPROVE", "1")),
        "photo_images": int(os.getenv("LIMIT_FREE_PHOTO_IMAGES", "0")),
        "bulk_batch":   int(os.getenv("LIMIT_FREE_BULK_BATCH", "0")),  # no bulk
    },
    "starter": {
        # Matches the advertised "100 generations/month". Pro gets 8× the
        # listings for 2× the price → clear upgrade incentive.
        "listings":     int(os.getenv("LIMIT_STARTER_LISTINGS", "100")),
        "improve":      int(os.getenv("LIMIT_STARTER_IMPROVE", "50")),
        # 6 images = 1 Etsy photo set/mo — a taster so Starter users try the
        # premium photo AI and upgrade to Pro (5 sets) for more.
        "photo_images": int(os.getenv("LIMIT_STARTER_PHOTO_IMAGES", "6")),
        "bulk_batch":   int(os.getenv("LIMIT_STARTER_BULK_BATCH", "10")),
    },
    "pro": {
        # Listings are the advertised feature → keep generous (and cheap at
        # ~$0.003 each). Improve isn't advertised → tight backstop is fine.
        "listings":     int(os.getenv("LIMIT_PRO_LISTINGS", "800")),
        "improve":      int(os.getenv("LIMIT_PRO_IMPROVE", "200")),
        "photo_images": int(os.getenv("LIMIT_PRO_PHOTO_IMAGES", "60")),  # 10 photo sets
        "bulk_batch":   int(os.getenv("LIMIT_PRO_BULK_BATCH", "20")),
    },
}

def plan_limit(plan: str, key: str) -> int:
    """Return a numeric limit for a plan, falling back to the free tier."""
    return PLAN_LIMITS.get(plan or "free", PLAN_LIMITS["free"]).get(key, 0)
