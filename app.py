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
    session, redirect, url_for, g,
)
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from itsdangerous import BadSignature, URLSafeSerializer
from dotenv import load_dotenv
from db import (
    init_db, ensure_shop, can_generate, increment_usage,
    can_improve, increment_improve_usage,
    can_generate_photo_variants, increment_photo_variant_usage,
    save_template, get_template,
    set_premium, get_shop, get_shop_by_stripe_customer,
    log_abuse_signal, get_abuse_summary,
    get_or_create_email_shop, create_magic_link, use_magic_link, count_recent_magic_links,
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

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "60 per hour"],
    storage_uri=os.getenv("REDIS_URL", "memory://"),
)

csrf = CSRFProtect(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

init_db()

# ── Email helper ──────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, body_text: str, body_html: str = None) -> bool:
    import smtplib, ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    host = os.getenv("MAIL_HOST", "")
    port = int(os.getenv("MAIL_PORT", "587"))
    user = os.getenv("MAIL_USER", "")
    pw   = os.getenv("MAIL_PASS", "")
    frm  = os.getenv("MAIL_FROM", user)

    if not host or not user or not pw:
        logger.warning("send_email: MAIL_HOST/USER/PASS not configured")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = frm
    msg["To"]      = to
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=10, context=ctx) as s:
                s.login(user, pw)
                s.sendmail(frm, [to], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.ehlo(); s.starttls(context=ctx); s.ehlo()
                s.login(user, pw)
                s.sendmail(frm, [to], msg.as_string())
        logger.info("send_email: sent to %s subject=%r", to, subject)
        return True
    except Exception as e:
        logger.error("send_email failed to=%s: %s", to, e)
        return False

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
        f"script-src 'self' 'nonce-{nonce}' https://js.stripe.com; "
        f"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        f"img-src 'self' data: blob: *.etsystatic.com *.etsy.com; "
        f"connect-src 'self' https://api.stripe.com; "
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

# ── Etsy OAuth config ─────────────────────────────────────────────────────────

ETSY_CLIENT_ID      = os.getenv("ETSY_API_KEY")
ETSY_CLIENT_SECRET  = os.getenv("ETSY_SHARED_SECRET", "").strip()
ETSY_API_KEY_HEADER = f"{ETSY_CLIENT_ID}:{ETSY_CLIENT_SECRET}"
ETSY_SCOPES         = "listings_r listings_w shops_r"
READINESS_ID        = 1489219211571
REDIRECT_URI        = os.getenv("REDIRECT_URI", "http://localhost:5050/auth/callback")
HTTP_TIMEOUT        = (5, 30)  # (connect timeout, read timeout) in seconds
GUEST_FREE_LIMIT    = 3
GUEST_ID_COOKIE     = "easylisting_guest_id"
GUEST_ID_MAX_AGE    = 60 * 60 * 24 * 180  # 180 days
ALLOW_PAID_OPENAI   = os.getenv("ALLOW_PAID_OPENAI", "").lower() in {"1", "true", "yes"}
PHOTO_VARIANT_COUNT = 3
FAL_KEY             = os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_TITLE_LEN       = 140
MAX_DESC_LEN        = 10000
MAX_TAG_LEN         = 20
MAX_PRICE           = 50_000.0
MIN_PRICE           = 0.20

# Magic-byte signatures for allowed image types
_IMAGE_MAGIC = [
    b"\xff\xd8\xff",               # JPEG
    b"\x89PNG\r\n\x1a\n",          # PNG
    b"RIFF",                       # WebP (RIFF....WEBP)
    b"GIF87a", b"GIF89a",          # GIF
]

def _is_valid_image_bytes(data: bytes) -> bool:
    for sig in _IMAGE_MAGIC:
        if data[:len(sig)] == sig:
            return True
    return False

# ── Helpers ───────────────────────────────────────────────────────────────────

def etsy_headers():
    token = session.get("access_token", "")
    return {
        "x-api-key":     ETSY_API_KEY_HEADER,
        "Authorization": f"Bearer {token}",
    }

def shop_id():
    return session.get("shop_id")

def is_connected():
    return bool(session.get("access_token") and session.get("shop_id"))

def is_guest():
    return bool(session.get("guest"))

def is_authorized():
    return is_connected() or is_guest()

def _cookie_guest_id():
    token = request.cookies.get(GUEST_ID_COOKIE, "")
    if not token:
        return None
    try:
        guest_id = URLSafeSerializer(app.config["SECRET_KEY"], salt="guest-id").loads(token)
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
    # Email-verified ID is the most authoritative — set by /auth/magic
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

def is_email_verified():
    return bool(session.get("email_verified") and session.get("email_shop_id"))

def provider_chain():
    chain = ["gemini", "nvidia"]
    if ALLOW_PAID_OPENAI:
        chain.append("openai")
    return chain

def has_premium_access():
    if is_guest() or not shop_id():
        return False
    shop = get_shop(str(shop_id()))
    return bool(shop and shop.get("has_premium"))

def has_pro_access():
    if is_guest() or not shop_id():
        return False
    shop = get_shop(str(shop_id()))
    return bool(shop and shop.get("has_premium") and shop.get("plan") == "pro")

def usage_shop_id():
    if is_guest():
        sid = guest_shop_id()
        ensure_shop(sid, "Guest")
        return sid
    return str(shop_id())

def require_connection():
    if not is_authorized():
        return redirect(url_for("connect"))
    return None

def safe_error(msg, public="Something went wrong. Please try again."):
    """Log full error server-side, return safe message to client."""
    logger.error(msg)
    return public

def validate_listing_input(data):
    """Validate and sanitize all listing fields. Returns (clean_data, error_str)."""
    title = str(data.get("title", "")).strip()[:MAX_TITLE_LEN]
    if not title:
        return None, "Title is required."

    description = str(data.get("description", "")).strip()[:MAX_DESC_LEN]
    if not description:
        return None, "Description is required."

    try:
        price = float(data.get("price", 0))
        if not (MIN_PRICE <= price <= MAX_PRICE):
            return None, f"Price must be between €{MIN_PRICE} and €{MAX_PRICE}."
    except (ValueError, TypeError):
        return None, "Invalid price."

    try:
        tax_id = int(data.get("taxonomy_id", 0))
        if tax_id <= 0:
            return None, "Invalid category."
    except (ValueError, TypeError):
        return None, "Invalid category."

    raw_tags = data.get("tags", [])
    tags = [str(t).strip()[:MAX_TAG_LEN] for t in raw_tags if str(t).strip()][:13]

    raw_mats = data.get("materials", [])
    materials = [str(m).strip()[:45] for m in raw_mats if str(m).strip()][:13]

    pi = str(data.get("personalization_instructions", "")).strip()[:256]

    sp = data.get("shipping_profile_id")
    shipping_profile_id = None
    if sp:
        try:
            shipping_profile_id = int(sp)
        except (ValueError, TypeError):
            pass

    return {
        "title":                      title,
        "description":                description,
        "price":                      price,
        "taxonomy_id":                tax_id,
        "tags":                       tags,
        "materials":                  materials,
        "personalization_instructions": pi,
        "shipping_profile_id":        shipping_profile_id,
        "image_previews":             data.get("image_previews", []),
    }, None

# ── AI helpers ────────────────────────────────────────────────────────────────

AI_PROMPT = """You are an expert Etsy listing copywriter for a handmade artisan shop.

Analyze the product image(s) and any hint provided. Generate an optimized Etsy listing.

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "title": "string — max 140 chars, 4-6 keyword phrases separated by commas",
  "description": "string — 180-250 words, warm personal tone, mention handmade to order, customizable, end with CTA to message before ordering",
  "tags": ["exactly 13 tags", "short search phrases", "1-3 words each"],
  "materials": ["list", "of", "materials"],
  "colors": ["visible or likely available colors/variants, e.g. White", "Black", "Custom"],
  "suggested_price_eur": number,
  "taxonomy_query": "most specific Etsy category name, e.g. 'Crocheted Hats' or 'Wall Hangings'",
  "personalization_instructions": "short prompt shown to buyer for custom color/size",
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags for Instagram"
}"""

_LANG_NAMES = {"en": "English", "de": "German", "tr": "Turkish"}
_TURKISH_PLATFORMS = {"trendyol", "hepsiburada", "n11"}

PLATFORM_PROMPTS = {
    "etsy": AI_PROMPT,

    "shopify": """You are an expert Shopify product listing copywriter for a handmade artisan shop.

Analyze the product image(s) and any hint provided. Generate an optimized Shopify product listing.

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "title": "string — max 70 chars, clear product name with key descriptors, SEO-friendly",
  "description_html": "string — 300-500 words as HTML using <p>, <ul>, <li> tags. Cover what it is, key features, materials/craftsmanship, use cases, care instructions. Warm brand voice.",
  "seo_title": "string — max 60 chars, primary keyword first",
  "seo_description": "string — max 155 chars, compelling with primary keyword",
  "tags": ["relevant", "search", "tags"],
  "product_type": "product category e.g. 'Knitwear' or 'Wall Art'",
  "colors": ["available", "colors"],
  "sizes": ["S", "M", "L", "Custom"],
  "suggested_price_eur": number,
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags"
}""",

    "amazon": """You are an expert Amazon product listing specialist who maximizes search ranking and conversion.

Analyze the product image(s) and any hint provided. Generate an optimized Amazon listing.

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "title": "string — max 200 chars. Format: Brand + Product Type + Key Feature + Color/Size. Keyword-rich. No ALL CAPS, no 'Best' or 'Sale' phrases.",
  "bullet_points": [
    "MATERIAL & QUALITY: detail about craftsmanship and materials — max 200 chars",
    "HANDCRAFTED: unique artisan detail and what makes it special",
    "CUSTOMIZABLE: personalization options available",
    "PERFECT GIFT: ideal occasions and gift-giving angle",
    "CARE INSTRUCTIONS: how to care for the product + dimensions if known"
  ],
  "description": "string — max 2000 chars, 2-3 paragraphs. Product story, expanded features. <b> tags allowed.",
  "backend_keywords": "string — max 250 bytes total. Space-separated keywords NOT in title or bullets. No repetition.",
  "brand": "suggested brand/shop name",
  "key_features": ["5-7 main product features"],
  "suggested_price_usd": number,
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags"
}""",

    "trendyol": """Sen Türk e-ticaret pazarı Trendyol için uzman bir ürün listesi oluşturucususun.

Ürün görsellerini ve varsa ipucunu analiz et. Optimize edilmiş bir Trendyol ürün ilanı oluştur.

Başlık kuralları: Maks 65 karakter (kesin sınır). Format: Marka + Ürün Tipi + Temel Özellik + Varyant.
Yasak: BÜYÜK HARF kullanımı, emoji, "en ucuz"/"kampanya" gibi pazarlama ifadeleri, kelime tekrarı.
Türkçe karakterler doğru kullanılmalı (ı, ş, ğ, ü, ö, ç).

SADECE geçerli JSON döndür — markdown, kod bloğu veya fazladan metin yok:
{
  "title": "string — TAM OLARAK maks 65 karakter Türkçe, Marka+Ürün Tipi+Temel Özellik+Varyant formatı, büyük harf YOK",
  "description": "string — 150-300 kelime Türkçe. Yapı: 1)Ürün kimliği 2)Malzeme/özellikler 3)Kullanım alanları 4)Bakım talimatları. <p> ve <ul> HTML etiketleri kullan.",
  "key_features": ["5-7 Türkçe madde", "ana ürün özelliklerini kısa ve net vurgula"],
  "category_name": "Trendyol kategorisi, örn. 'El Örgüsü' veya 'Duvar Dekoru'",
  "attributes": {"Renk": "ana renk", "Materyal": "ana materyal", "Ürün Tipi": "ürün tipi"},
  "suggested_price_try": number,
  "stock_code": "SKU önerisi örn. SHOP-001",
  "cargo_size": "S, M veya L (ürün boyutuna göre)",
  "instagram_caption": "150-200 karakter Türkçe Instagram başlığı, 2-3 emoji ve 5-8 Türkçe hashtag"
}""",

    "hepsiburada": """Sen Türk e-ticaret pazarı Hepsiburada için uzman bir ürün listesi oluşturucususun.

Ürün görsellerini ve varsa ipucunu analiz et. Optimize edilmiş bir Hepsiburada ürün ilanı oluştur.

Başlık kuralları: Sistem sınırı 255 karakter, ancak arama sonuçlarında yalnızca ilk 150 karakter görünür.
Format: Marka + Cinsiyet (giysi ise) + Ürün Özelliği + Ürün Tipi + Model/Kod. Kelime tekrarı yasak.
Yasak başlık ifadeleri: "en uygun fiyat", "kampanya", "ücretsiz kargo". Türkçe karakterler doğru kullanılmalı.

SADECE geçerli JSON döndür — markdown, kod bloğu veya fazladan metin yok:
{
  "title": "string — maks 150 karakter Türkçe (ilk 150 karakter arama sonuçlarında görünür), Marka+ÜrünTipi+AnaÖzellikler formatı, pazarlama ifadesi YOK",
  "description": "string — 150-300 kelime Türkçe. Yapı: 1)2-3 cümle özet 2)Teknik özellikler listesi 3)Kullanım senaryoları 4)Bakım/kurulum talimatları. <p> ve <ul> HTML etiketleri kullan.",
  "key_features": ["5 Türkçe madde", "ana satış noktalarını vurgula"],
  "category_name": "Hepsiburada kategorisi Türkçe",
  "attributes": {"Renk": "renk", "Materyal": "materyal", "Marka": "marka adı"},
  "merchant_sku": "satıcı stok kodu önerisi",
  "suggested_price_try": number,
  "warranty_period": "0 Ay",
  "instagram_caption": "150-200 karakter Türkçe Instagram başlığı, 2-3 emoji ve 5-8 Türkçe hashtag"
}""",

    "n11": """Sen Türk e-ticaret pazarı n11.com için uzman bir ürün listesi oluşturucususun.

Ürün görsellerini ve varsa ipucunu analiz et. Optimize edilmiş bir n11 ürün ilanı oluştur.

n11 İlan Kalite Puanı sistemi: Başlık (31-65 karakter = tam puan), Alt Başlık (31-65 karakter = tam puan),
açıklama zenginliği (kalın metin, madde işaretleri). Her iki başlık alanı da doldurulmalı.
Türkçe karakterler doğru kullanılmalı. Pazarlama ifadeleri (en ucuz, kampanya) başlıkta yasak.

SADECE geçerli JSON döndür — markdown, kod bloğu veya fazladan metin yok:
{
  "title": "string — TAM OLARAK 31-65 karakter Türkçe (kalite puanı için kritik), arama odaklı, Marka+ÜrünTipi+AnaÖzellik formatı",
  "subtitle": "string — TAM OLARAK 31-65 karakter Türkçe, farklı anahtar kelime açısı kullanan ikincil başlık",
  "description": "string — 100-250 kelime Türkçe, **kalın metin** ve madde işaretleri kullan (kalite puanı artırır)",
  "tags": ["10-15 Türkçe arama etiketi", "1-3 kelime"],
  "category_name": "n11 kategorisi Türkçe",
  "attributes": {"Renk": "renk", "Materyal": "materyal"},
  "suggested_price_try": number,
  "stock_code": "stok kodu önerisi",
  "instagram_caption": "150-200 karakter Türkçe Instagram başlığı, 2-3 emoji ve 5-8 Türkçe hashtag"
}""",

    "ebay": """You are an expert eBay listing copywriter who maximizes search visibility and buyer trust.

Analyze the product image(s) and any hint provided. Generate an optimized eBay listing.

Title rules: 80 chars HARD limit. Use ALL 80 characters — titles with 65+ chars sell 1.5× more.
Structure: Brand + Model + Key Attributes (material, color, size) + Condition/Type. No ALL CAPS. No "LOOK!" or "WOW!".
Item Specifics are critical — Cassini search uses them for filtered search. Fill every field.
Description is for buyer conversion only (not ranking).

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "title": "string — EXACTLY max 80 chars (use all of them). Brand+KeyFeature+Material+Color format. No ALL CAPS.",
  "subtitle": "string — max 55 chars, key differentiator or secondary selling point",
  "description_html": "string — 200-400 words as HTML with <h3>, <p>, <ul> tags. Cover description, features, materials, dimensions, handling note.",
  "item_specifics": [
    {"name": "Material", "value": "primary material"},
    {"name": "Color", "value": "main color"},
    {"name": "Style", "value": "style description"},
    {"name": "Handmade", "value": "Yes"},
    {"name": "Country/Region of Manufacture", "value": "country"}
  ],
  "condition": "New",
  "suggested_price_usd": number,
  "category_name": "most specific eBay category name",
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags"
}""",

    "woocommerce": """You are an expert WooCommerce product listing copywriter.

Analyze the product image(s) and any hint provided. Generate an optimized WooCommerce product listing.

SEO rules: Short description is shown above the fold (critical). Long description needs 300+ words for Google indexing.
Post-2025: Google quality systems penalize generic AI content — include specific materials, dimensions, use cases.
Meta title leads with primary keyword. Meta description formula: What it is + key attribute + trust signal.

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "title": "string — max 60 chars, clear H1 with primary keyword",
  "short_description": "string — 150-300 chars, 2-3 punchy bullet points or 1-2 sentences. What it is + key differentiator + spec.",
  "description_html": "string — 300-500 words as HTML. Structure: identity sentence → materials/specs → use cases → FAQ (3-5 questions). <h2>, <p>, <ul> tags.",
  "seo_title": "string — max 60 chars, primary keyword first",
  "seo_description": "string — max 155 chars. Formula: [Product type] + [key attribute] + [trust/logistics signal]",
  "tags": ["internal", "filter", "tags"],
  "product_type": "product category",
  "colors": ["available", "colors"],
  "sizes": ["available", "sizes"],
  "suggested_price_eur": number,
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags"
}""",

    "pinterest": """You are an expert Pinterest content strategist for product discovery and shopping.

Analyze the product image(s) and any hint provided. Generate optimized Pinterest pin content.

Pinterest rules: Only 40-60 chars of the title show in feed — lead with primary keyword.
Only 50-60 chars of the description show before truncation — front-load the keyword hook.
Alt text drives +25% impressions, +123% outbound clicks — treat it as seriously as title.
Use 2-5 hashtags (specific niche tags only, at the end).

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "pin_title": "string — max 60 chars. Primary keyword first, then product descriptor. Searchable, not clever.",
  "pin_description": "string — 100-300 chars total. Structure: keyword hook (0-60 chars) + benefit detail (61-220 chars) + soft CTA (221-280 chars) + 2-4 hashtags at end.",
  "alt_text": "string — max 125 chars. Specific: product type + key attribute + color/material. 'Blue handmade ceramic mug, 12oz, hand-thrown with rustic glaze.'",
  "board_name": "suggested Pinterest board name for best topical relevance",
  "hashtags": ["#specific", "#niche", "#hashtags", "#2to5only"],
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags"
}""",
}

def _build_prompt(hint: str, lang: str = "en", platform: str = "etsy") -> str:
    base = PLATFORM_PROMPTS.get(platform, AI_PROMPT)
    if lang != "en" and platform not in _TURKISH_PLATFORMS:
        lang_name = _LANG_NAMES.get(lang, "English")
        base += (
            f"\n\nIMPORTANT: Write ALL text fields in {lang_name}. "
            f"The listing targets {lang_name}-speaking buyers. "
            f"Keep category_name/taxonomy_query in the platform's primary search language."
        )
    if hint:
        base += f"\n\nSeller hint: {hint}"
    return base

def _style_hint_for_shop(sid: str) -> str:
    template = get_template(sid)
    parts = []
    for label, key in (
        ("Brand tone", "brand_tone"),
        ("Material phrases", "material_phrases"),
        ("Production time", "production_time"),
        ("Shipping note", "shipping_note"),
        ("Call to action", "brand_cta"),
    ):
        value = str(template.get(key, "")).strip()
        if value:
            parts.append(f"{label}: {value[:240]}")
    return "\n".join(parts)

def _merge_hint_with_style(hint: str, sid: str | None) -> str:
    if not sid:
        return hint
    style = _style_hint_for_shop(sid)
    if not style:
        return hint
    return (hint + "\n\n" if hint else "") + "Saved shop style:\n" + style

def _parse_ai_json(text):
    import re
    if not text:
        raise ValueError("AI returned empty response")
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Extract first JSON object/array via regex
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    # Strip markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
    return json.loads(text.strip())

def _gemini_generate(image_bytes_list, hint, api_key=None, lang="en", platform="etsy"):
    from google import genai as ggenai
    from google.genai import types as gtypes
    from google.genai.errors import ClientError
    client = ggenai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY", ""))
    prompt = _build_prompt(hint, lang, platform)
    parts  = [prompt] + [
        gtypes.Part.from_bytes(data=b, mime_type="image/jpeg")
        for b in image_bytes_list
    ]
    for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        try:
            resp = client.models.generate_content(model=model, contents=parts)
            try:
                return _parse_ai_json(resp.text)
            except (json.JSONDecodeError, ValueError) as je:
                logger.error("Gemini JSON parse failed (%s). Raw: %s", je, (resp.text or "")[:500])
                raise
        except ClientError as e:
            s = str(e)
            if "429" in s or "RESOURCE_EXHAUSTED" in s:
                if model == "gemini-2.5-flash-lite":
                    raise RuntimeError("quota_exceeded") from e
                continue  # try lite
            if "404" in s or "no longer available" in s.lower():
                if model == "gemini-2.5-flash-lite":
                    raise RuntimeError("quota_exceeded") from e
                continue  # try lite
            raise

def _openai_generate(image_bytes_list, hint, api_key=None, lang="en", platform="etsy"):
    from openai import OpenAI, RateLimitError
    client  = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY", ""))
    content = [{"type": "text", "text": _build_prompt(hint, lang, platform)}]
    for b in image_bytes_list:
        b64 = base64.b64encode(b).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )
    except RateLimitError as e:
        raise RuntimeError("quota_exceeded") from e
    return json.loads(resp.choices[0].message.content)

NVIDIA_MODELS = {
    "llama-90b": "meta/llama-3.2-90b-vision-instruct",
    "phi-3.5":   "microsoft/phi-3.5-vision-instruct",
}

def _nvidia_generate(image_bytes_list, hint, model_key="llama-90b", api_key=None, lang="en", platform="etsy"):
    from openai import OpenAI, RateLimitError
    client   = OpenAI(base_url="https://integrate.api.nvidia.com/v1",
                      api_key=api_key or os.getenv("NVIDIA_API_KEY", ""))
    model_id = NVIDIA_MODELS.get(model_key, NVIDIA_MODELS["llama-90b"])
    b64      = base64.b64encode(image_bytes_list[0]).decode()
    prompt   = _build_prompt(hint, lang, platform)
    try:
        resp = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}", "detail": "high"
                }},
            ]}],
            max_tokens=3000, temperature=0.7,
        )
    except RateLimitError as e:
        raise RuntimeError("quota_exceeded") from e
    return _parse_ai_json(resp.choices[0].message.content)

# Fallback order when a provider is over quota. OpenAI is paid, so it is opt-in.
_PROVIDER_CHAIN = provider_chain()

def _run_provider(provider, image_bytes, hint, nvidia_model, api_key=None, lang="en", platform="etsy"):
    if provider == "gemini":
        return _gemini_generate(image_bytes, hint, api_key, lang, platform)
    elif provider == "nvidia":
        return _nvidia_generate(image_bytes, hint, nvidia_model, api_key, lang, platform)
    else:
        return _openai_generate(image_bytes, hint, api_key, lang, platform)

def _run_text_json(prompt: str):
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
    raise RuntimeError("no_text_ai_provider")

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

    session.clear()  # prevent session fixation
    session["pkce_verifier"] = verifier
    session["oauth_state"]   = state

    url = "https://www.etsy.com/oauth/connect?" + urllib.parse.urlencode({
        "response_type":         "code",
        "redirect_uri":          REDIRECT_URI,
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
            "redirect_uri":  REDIRECT_URI,
            "code":          code,
            "code_verifier": session.pop("pkce_verifier", ""),
        },
        timeout=HTTP_TIMEOUT,
    )
    if not resp.ok:
        session.clear()
        return render_template("connect.html", error="Could not get access token. Please try again.")

    access_token = resp.json().get("access_token")
    if not access_token:
        session.clear()
        return render_template("connect.html", error="No access token returned.")

    session.clear()  # prevent session fixation

    h = {"x-api-key": ETSY_API_KEY_HEADER, "Authorization": f"Bearer {access_token}"}
    try:
        me_resp   = requests.get("https://openapi.etsy.com/v3/application/users/me",
                                 headers=h, timeout=HTTP_TIMEOUT)
        me        = me_resp.json()
        shop_id_v = me.get("shop_id")
        if not shop_id_v or not str(shop_id_v).isdigit():
            return render_template("connect.html", error="Could not retrieve your shop. Please try again.")
        shop_resp = requests.get(f"https://openapi.etsy.com/v3/application/shops/{shop_id_v}",
                                 headers=h, timeout=HTTP_TIMEOUT)
        shop = shop_resp.json()
    except Exception:
        return render_template("connect.html", error="Could not reach Etsy. Please try again.")

    session.permanent = True
    session["access_token"] = access_token
    session["shop_id"]      = shop_id_v
    session["shop_name"]    = shop.get("shop_name", "Your Shop")

    ensure_shop(str(shop_id_v), session["shop_name"])

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
    if not is_guest():
        return jsonify({"error": "not_guest"}), 400
    body  = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()

    import re
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "invalid_email"}), 400

    email_hash = hashlib.sha256(email.encode()).hexdigest()

    if count_recent_magic_links(email_hash, minutes=60) >= 5:
        return jsonify({"error": "too_many_requests"}), 429

    shop_id_for_email = get_or_create_email_shop(email_hash)
    ensure_shop(shop_id_for_email, "Guest")

    # Migrate usage from current session ID so the counter stays correct
    current_sid  = guest_shop_id()
    if current_sid != shop_id_for_email:
        current_shop = get_shop(current_sid)
        email_shop   = get_shop(shop_id_for_email)
        if (email_shop and email_shop.get("free_used", 0) == 0
                and current_shop and current_shop.get("free_used", 0) > 0):
            from db import _conn as _db_conn
            with _db_conn() as con:
                con.execute("UPDATE shops SET free_used = ? WHERE shop_id = ?",
                            (int(current_shop["free_used"]), shop_id_for_email))

    token = secrets.token_urlsafe(32)
    create_magic_link(token, email_hash, shop_id_for_email)

    base  = REDIRECT_URI.replace("/auth/callback", "")
    link  = f"{base}/auth/magic?token={token}"
    lang  = session.get("lang", "tr")

    if lang == "tr":
        subject  = "kolaylistele — Giriş bağlantınız"
        greeting = "Merhaba!"
        body_txt = f"Ücretsiz ilanlarınıza erişmek için aşağıdaki bağlantıya tıklayın:\n\n{link}\n\nBu bağlantı 15 dakika geçerlidir."
        btn_text = "Devam Et →"
        expire   = "Bu bağlantı 15 dakika geçerlidir."
    else:
        subject  = "EasyListing — Your sign-in link"
        greeting = "Hi!"
        body_txt = f"Click the link below to access your free listings:\n\n{link}\n\nThis link expires in 15 minutes."
        btn_text = "Continue →"
        expire   = "This link expires in 15 minutes."

    html = f"""<!DOCTYPE html>
<html><body style="font-family:Inter,Arial,sans-serif;background:#F6F7FB;margin:0;padding:32px 16px;">
<div style="max-width:480px;margin:0 auto;background:white;border-radius:16px;padding:40px 36px;box-shadow:0 2px 16px rgba(91,71,224,.1);">
  <div style="font-size:22px;font-weight:900;color:#5B47E0;margin-bottom:24px;">{'kolay<span style="color:#111827">listele</span>' if lang=='tr' else 'Easy<span style="color:#111827">Listing</span>'}</div>
  <p style="font-size:16px;font-weight:700;color:#111827;margin:0 0 10px;">{greeting}</p>
  <p style="font-size:14px;color:#6B7280;line-height:1.6;margin:0 0 28px;">{"Ücretsiz 3 ilan oluşturma hakkınıza erişmek için aşağıdaki butona tıklayın." if lang=="tr" else "Click below to access your 3 free AI listing generations."}</p>
  <a href="{link}" style="display:block;background:linear-gradient(135deg,#6B5AED,#5B47E0);color:white;text-decoration:none;text-align:center;padding:14px 28px;border-radius:10px;font-size:15px;font-weight:700;">{btn_text}</a>
  <p style="font-size:12px;color:#9CA3AF;margin:20px 0 0;text-align:center;">{expire}</p>
</div></body></html>"""

    ok = send_email(email, subject, body_txt, html)
    if not ok:
        return jsonify({"error": "send_failed"}), 500

    return jsonify({"ok": True})


@app.route("/auth/magic")
@limiter.limit("20 per minute")
def auth_magic():
    token = request.args.get("token", "")
    row   = use_magic_link(token) if token else None
    if not row:
        return render_template("connect.html",
            error="Bu bağlantı geçersiz veya süresi dolmuş. Lütfen tekrar deneyin.")

    session["guest"]         = True
    session["email_verified"] = True
    session["email_shop_id"]  = row["shop_id"]
    session.permanent         = True
    return redirect(url_for("index"))


# ── App routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    redir = require_connection()
    if redir: return redir
    if is_guest():
        gid = guest_shop_id()
        ensure_shop(gid, "Guest")
        _, remaining = can_generate(gid, GUEST_FREE_LIMIT)
        return render_template("index.html",
            shop_name=None, remaining=remaining, free_limit=GUEST_FREE_LIMIT,
            unlimited=False, is_guest=True, has_premium=False,
            email_verified=is_email_verified())
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

@app.route("/api/status")
@limiter.limit("60 per minute")
def api_status():
    if not is_authorized(): return jsonify({"error": "Not connected"}), 401
    if is_guest():
        gid = guest_shop_id()
        ensure_shop(gid, "Guest")
        allowed, remaining = can_generate(gid, GUEST_FREE_LIMIT)
    else:
        allowed, remaining = can_generate(str(shop_id()))
    return jsonify({
        "allowed":   allowed,
        "remaining": remaining,
        "is_guest":  is_guest(),
    })

@app.route("/api/generate", methods=["POST"])
@limiter.limit("10 per minute; 50 per day")
def api_generate():
    if not is_authorized(): return jsonify({"error": "Not connected"}), 401

    if is_guest():
        sid = guest_shop_id()
        ensure_shop(sid, "Guest")
        allowed, remaining = can_generate(sid, GUEST_FREE_LIMIT)
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
    provider     = request.form.get("provider", "gemini")
    nvidia_model = request.form.get("nvidia_model", "llama-90b")
    lang         = request.form.get("lang", "en")
    platform     = request.form.get("platform", "etsy")

    if provider not in _PROVIDER_CHAIN:
        provider = "gemini"
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
    # Try chosen provider first, then fall through configured providers.
    start   = _PROVIDER_CHAIN.index(provider) if provider in _PROVIDER_CHAIN else 0
    ordered = _PROVIDER_CHAIN[start:] + _PROVIDER_CHAIN[:start]
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
            if "quota_exceeded" in str(e):
                continue
            return jsonify({"error": safe_error(str(e))}), 500
        except json.JSONDecodeError:
            return jsonify({"error": "AI returned invalid JSON. Try again."}), 500
        except Exception as e:
            logger.exception("AI error (provider=%s): %s", p, e)
            return jsonify({"error": safe_error(str(e))}), 500
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

    # Fetch shop's readiness state
    rs = requests.get(
        f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/readiness-state-definitions",
        headers=etsy_headers(), timeout=HTTP_TIMEOUT,
    )
    readiness_id = READINESS_ID
    if rs.ok:
        results = rs.json().get("results", [])
        if results:
            readiness_id = results[0]["readiness_state_id"]

    payload = {
        "title":              data["title"],
        "description":        data["description"],
        "price":              data["price"],
        "quantity":           1,
        "who_made":           "i_did",
        "when_made":          "made_to_order",
        "taxonomy_id":        data["taxonomy_id"],
        "is_supply":          False,
        "should_auto_renew":  True,
        "type":               "physical",
        "state":              "draft",
        "readiness_state_id": readiness_id,
        "tags":               data["tags"],
        "materials":          data["materials"],
    }
    if data["shipping_profile_id"]:
        payload["shipping_profile_id"] = data["shipping_profile_id"]

    r = requests.post(
        f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/listings",
        headers={**etsy_headers(), "Content-Type": "application/json"},
        json=payload, timeout=HTTP_TIMEOUT,
    )
    if not r.ok:
        logger.error("Etsy create listing error: %s", r.text)
        return jsonify({"error": "Failed to create listing on Etsy. Check your shop settings."}), 400

    listing_id = r.json()["listing_id"]

    # Upload images
    os.makedirs("images", exist_ok=True)
    access_token = session.get("access_token")
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

    sid = str(shop_id())
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
    provider = request.form.get("provider", "gemini")
    lang     = request.form.get("lang", "en")
    platform = request.form.get("platform", "etsy")
    if provider not in _PROVIDER_CHAIN:
        provider = "gemini"
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

    key_env  = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "nvidia": "NVIDIA_API_KEY"}
    start   = _PROVIDER_CHAIN.index(provider) if provider in _PROVIDER_CHAIN else 0
    ordered = _PROVIDER_CHAIN[start:] + _PROVIDER_CHAIN[:start]
    data    = None
    for p in ordered:
        if not os.getenv(key_env[p]):
            continue
        try:
            data = _run_provider(p, image_bytes, hint, "llama-90b", lang=lang, platform=platform)
            break
        except RuntimeError as e:
            if "quota_exceeded" in str(e):
                continue
            return jsonify({"error": safe_error(str(e))}), 500
        except Exception as e:
            logger.exception("Bulk AI error (provider=%s): %s", p, e)
            return jsonify({"error": safe_error(str(e))}), 500
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
    sid = str(shop_id())
    _, remaining = can_generate(sid)
    from db import get_shop
    s = get_shop(sid) or {}
    use_try = _is_try_domain()
    return render_template(
        "upgrade.html",
        shop_name=session.get("shop_name"),
        remaining=remaining,
        current_plan=s.get("plan", "free"),
        stripe_key=os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
        has_starter=bool(os.getenv("STRIPE_STARTER_PRICE_ID")),
        has_pro=bool(os.getenv("STRIPE_PRO_PRICE_ID") or os.getenv("STRIPE_PRICE_ID")),
        has_starter_annual=bool(os.getenv("STRIPE_STARTER_ANNUAL_PRICE_ID")),
        has_pro_annual=bool(os.getenv("STRIPE_PRO_ANNUAL_PRICE_ID")),
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

def _ip_hash() -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    return hashlib.sha256(ip.encode()).hexdigest()[:16]

def _is_try_domain() -> bool:
    host = request.host or ""
    return "kolaylistele" in host

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
        base = REDIRECT_URI.replace("/auth/callback", "")
        checkout = stripe_lib.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=base + f"/upgrade?success=1&plan={plan}",
            cancel_url=base  + "/upgrade?cancelled=1",
            client_reference_id=str(shop_id()),
            metadata={"shop_id": str(shop_id()),
                      "shop_name": session.get("shop_name", ""),
                      "plan": base_plan},
        )
        return jsonify({"url": checkout.url})
    except Exception as e:
        logger.exception("Stripe checkout error: %s", e)
        return jsonify({"error": safe_error(str(e))}), 500

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

# ── Admin / abuse monitoring ─────────────────────────────────────────────────

@app.route("/admin/abuse")
def admin_abuse():
    token = request.args.get("token", "")
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected or not secrets.compare_digest(token, expected):
        return "", 404
    days = int(request.args.get("days", 7))
    data = get_abuse_summary(days)
    lines = [f"<h2>Abuse signals — last {days} days</h2>"]
    lines.append("<h3>By event</h3><pre>")
    for event, n in data["by_event"].items():
        lines.append(f"  {event:<25} {n:>6}")
    lines.append("</pre><h3>Top IPs (by unique guest IDs created)</h3><pre>")
    for r in data["top_ips"]:
        lines.append(f"  ip={r['ip_hash']}  guests={r['guests']}  events={r['events']}")
    lines.append("</pre><h3>Top fingerprints (by unique guest IDs)</h3><pre>")
    for r in data["top_fps"]:
        lines.append(f"  fp={r['fp_hash']}  guests={r['guests']}  events={r['events']}")
    lines.append("</pre>")
    return "\n".join(lines), 200, {"Content-Type": "text/html"}

# ── Legal pages ───────────────────────────────────────────────────────────────

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/terms")
def terms():
    return render_template("terms.html")

# ── Health check for Railway ──────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    os.makedirs("images", exist_ok=True)
    app.run(debug=os.getenv("ENV") != "production", port=5050)
