# kolaylistele / EasyListing — System Design

> **Confidential — Internal Engineering Reference**
> Version 2.0 · June 2025

---

## 1. Overview

**kolaylistele** (Turkish: "list easily") is a SaaS tool that uses AI vision models to analyse product photos and generate fully-optimised marketplace listings in seconds. Sellers upload one or more images, optionally add a short hint, and receive title, description, tags, materials, price suggestion, and an Instagram caption — ready to publish directly to their Etsy shop.

### Products / Domains

| Domain | Market | Currency | Language |
|--------|--------|----------|----------|
| `kolaylistele.com` | Turkey | ₺ TRY | Turkish |
| `easylisting.*` (Railway subdomain + custom) | International | € EUR | English / German |

Both domains run the same Flask application; the active domain is detected at runtime via `request.host` to switch Stripe price IDs and email copy.

### Supported Platforms

The AI pipeline generates platform-specific listings for: **Etsy**, **Shopify**, **Amazon**, **eBay**, **WooCommerce**, **Pinterest**, **Trendyol**, **Hepsiburada**, **n11**.

---

## 2. Architecture Summary

| Layer | Responsibility | Key Components |
|-------|---------------|----------------|
| **Presentation** | User-facing browser UI, CDN, TLS | Browser (vanilla JS + Stripe.js), Cloudflare WAF/CDN, two custom domains |
| **Security & Routing** | Request validation, abuse blocking, auth tokens | Flask-Limiter, probe path blocker, Flask-WTF CSRF, browser fingerprint, CSP nonces, magic-byte image validation |
| **Application** | Business logic, AI orchestration, payment flows | Python 3.12 + Flask 3.1, Gunicorn 2 workers, four logical modules: Auth, Core API, Payments, AI Pipeline |
| **Data & Services** | Persistence, third-party APIs | SQLite on Railway Volume, Etsy REST API v3, Stripe, Google Gemini, NVIDIA NIM, OpenAI, fal.ai, Natro SMTP |

---

## 3. Tech Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| Language | Python | 3.12 | Application runtime |
| Web framework | Flask | 3.1.3 | HTTP routing, session management |
| WSGI server | Gunicorn | 26.0.0 | Production process manager, 2 workers |
| Database | SQLite (WAL mode) | bundled | Per-shop usage, billing state, auth tokens |
| ORM / DB layer | Raw `sqlite3` | stdlib | Direct SQL, no ORM overhead |
| Platform | Railway | — | PaaS deploy, Volume for SQLite persistence |
| CDN / WAF | Cloudflare | — | DDoS protection, edge cache, SSL termination |
| Auth (seller) | Etsy OAuth 2.0 + PKCE | — | Seller shop authorisation |
| Auth (guest) | Magic link + fingerprint | — | Passwordless guest sessions |
| CSRF | Flask-WTF | 1.3.0 | Token-per-request CSRF protection |
| Rate limiting | Flask-Limiter | 4.1.1 | Per-IP, per-endpoint request throttling |
| Session signing | itsdangerous | bundled w/ Flask | Guest-ID cookie integrity |
| AI primary | Google Gemini 2.5 Flash | — | Vision + text generation, free tier |
| AI secondary | NVIDIA NIM — Llama-3.2-90B | — | Vision fallback, free tier |
| AI tertiary | OpenAI GPT-4o | — | Paid opt-in fallback |
| Image generation | FLUX.1 Kontext (fal.ai) | — | Pro photo variants |
| Payments | Stripe | SDK 15.1.0 | Subscriptions, webhooks |
| Email | Natro SMTP | — | Magic link delivery via `info@kolaylistele.com` |
| HTTP client | requests | 2.34.2 | All external API calls |
| Image processing | Pillow | 12.2.0 | Dependency for image handling |
| Env config | python-dotenv | 1.2.2 | `.env` loading |

---

## 4. Data Models

All tables live in a single SQLite file at `$DB_PATH` (default `easylisting.sqlite`, mounted at `/data/easylisting.sqlite` on Railway). WAL journal mode is enabled on every connection. Schema migrations are applied idempotently via `ALTER TABLE … ADD COLUMN` wrapped in try/except.

### 4.1 `shops`

Central per-seller record. Covers both real Etsy shops (numeric `shop_id`) and guest pseudo-shops (`guest_*` prefixed IDs).

| Column | Type | Description |
|--------|------|-------------|
| `shop_id` | TEXT PK | Etsy numeric ID, `guest_{hash}`, `guest_fp_{fp24}`, or `guest_email_{hash20}` |
| `shop_name` | TEXT | Display name ("Guest" for non-Etsy) |
| `free_used` | INTEGER | Number of free AI generations consumed |
| `has_premium` | INTEGER | 0 / 1 — whether an active paid subscription exists |
| `plan` | TEXT | `free`, `starter`, or `pro` |
| `stripe_customer_id` | TEXT | Stripe Customer object ID |
| `stripe_subscription_id` | TEXT | Active Stripe Subscription ID |
| `free_improve_used` | INTEGER | Free listing-improve actions consumed |
| `photo_variant_used` | INTEGER | Photo variants generated this billing period |
| `photo_variant_period` | TEXT | `YYYY-MM` month string for monthly reset |
| `own_api_key` | TEXT | Reserved for BYO-key feature |
| `created_at` | TIMESTAMP | Row creation time |

### 4.2 `templates`

One row per shop storing the shop's reusable style settings as a JSON blob.

| Column | Type | Description |
|--------|------|-------------|
| `shop_id` | TEXT PK | FK → shops.shop_id |
| `data` | TEXT | JSON object: `brand_tone`, `material_phrases`, `production_time`, `shipping_note`, `brand_cta`, `tags`, `materials`, `price`, `shipping_profile_id`, `personalization_instructions` |
| `updated_at` | TIMESTAMP | Last save time |

Style data is appended as a "Saved shop style" block in AI prompts via `_merge_hint_with_style()`.

### 4.3 `magic_links`

One-time, time-limited authentication tokens for guest email sign-in.

| Column | Type | Description |
|--------|------|-------------|
| `token` | TEXT PK | `secrets.token_urlsafe(32)` — 43-char URL-safe token |
| `email_hash` | TEXT | SHA-256 of the normalised email address |
| `shop_id` | TEXT | Resolved guest_email shop ID |
| `created_at` | TIMESTAMP | Issue time; tokens expire 15 minutes after this |
| `used_at` | TIMESTAMP | Set on first use; subsequent uses are rejected |

Index on `email_hash` for rate-limit lookups.

### 4.4 `verified_emails`

Permanent mapping from hashed email to the shop ID created for that email address.

| Column | Type | Description |
|--------|------|-------------|
| `email_hash` | TEXT PK | SHA-256 of normalised email |
| `shop_id` | TEXT | `guest_email_{hash[:20]}` — stable across browser sessions |
| `created_at` | TIMESTAMP | First verification time |

### 4.5 `abuse_signals`

Append-only event log used by the `/admin/abuse` monitoring endpoint.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK AUTOINCREMENT | Row ID |
| `event` | TEXT | Event type: `new_guest`, `limit_hit`, `fp_conflict` |
| `ip_hash` | TEXT | SHA-256[:16] of client IP (hashed for privacy) |
| `guest_id` | TEXT | Resolved guest shop ID at time of event |
| `fp_hash` | TEXT | Browser fingerprint hash slice (24 chars) |
| `detail` | TEXT | Free-text extra context |
| `created_at` | TIMESTAMP | Event time |

Indexes on `ip_hash`, `fp_hash`, and `created_at` support the aggregation queries in `get_abuse_summary()`.

### 4.6 (Implicit) Session Store

Flask server-side sessions stored in signed cookies (HMAC-SHA-1 via `itsdangerous`). Key session keys:

| Key | Type | Set by |
|-----|------|--------|
| `access_token` | str | `/auth/callback` (Etsy OAuth) |
| `shop_id` | int | `/auth/callback` |
| `shop_name` | str | `/auth/callback` |
| `guest` | bool | `/guest` or `/auth/magic` |
| `guest_id` | str | `_get_or_create_guest_id()` |
| `fp_guest_id` | str | `POST /api/fingerprint` |
| `email_verified` | bool | `/auth/magic` |
| `email_shop_id` | str | `/auth/magic` |

### 4.7 `easylisting_guest_id` Cookie

A separate long-lived (180-day) HTTP-only, signed cookie storing the random guest ID. Signed with `URLSafeSerializer(SECRET_KEY, salt="guest-id")`. This persists across browser sessions independently of the server-side session, providing a third layer of identity continuity for guests.

---

## 5. Authentication Flows

### 5.1 Etsy OAuth 2.0 with PKCE

```
Browser                Flask               Etsy
   |                     |                   |
   |--- GET /auth/start ->|                   |
   |                     | generate verifier, challenge, state
   |                     | session[pkce_verifier, oauth_state]
   |<-- 302 → etsy.com/oauth/connect --------|
   |                                         |
   |-- User authorises app ----------------->|
   |<-- 302 → /auth/callback?code=...&state= |
   |                     |                   |
   |--- GET /auth/callback?code&state ------>|
   |                     | validate state == session[oauth_state]
   |                     | POST oauth/token with code + verifier
   |                     |<--- access_token --|
   |                     | GET /users/me → shop_id
   |                     | GET /shops/{id}   → shop_name
   |                     | ensure_shop() in SQLite
   |                     | session[access_token, shop_id, shop_name]
   |<-- 302 → / ---------|
```

Scopes requested: `listings_r listings_w shops_r`. Session is cleared before and after the exchange to prevent session fixation. PKCE uses S256 challenge method with a 64-byte random verifier.

### 5.2 Guest Magic Link

```
Browser                Flask               Natro SMTP
   |                     |                   |
   |-- POST /api/magic-link {email} -------->|
   |                     | validate email format
   |                     | hash = SHA-256(email)
   |                     | check count_recent_magic_links ≤ 5/hr
   |                     | get_or_create_email_shop(hash)
   |                     | migrate usage from current session ID
   |                     | token = secrets.token_urlsafe(32)
   |                     | create_magic_link(token, hash, shop_id)
   |                     | send_email(to, link) ------------>|
   |<-- {ok: true} ------|                   |
   |                     |                   |
   |-- GET /auth/magic?token=... ----------->|
   |                     | use_magic_link(token)
   |                     | checks: not used, < 15min old
   |                     | session[guest=True, email_verified=True,
   |                     |          email_shop_id=...]
   |<-- 302 → / ---------|
```

Email addresses are never stored in plaintext — only their SHA-256 hash. The `email_shop_id` is the most authoritative guest identity and takes precedence over fingerprint and cookie IDs in `guest_shop_id()`.

### 5.3 Browser Fingerprint Fallback

On `/guest` entry, a random `guest_id` is created and stored in both the server-side session and the `easylisting_guest_id` cookie (180-day). The frontend sends a derived browser fingerprint to `POST /api/fingerprint`:

1. Client computes a fingerprint hash (canvas, fonts, UA, etc.) and sends `{fp: "<hex>"}`.
2. Server creates `fp_guest_id = f"guest_fp_{fp[:24]}"` and stores it in the session.
3. If the current random ID has usage > 0 but the FP ID is new (free_used = 0), usage is migrated to the FP ID — prevents free limit reset via incognito mode.
4. If the FP ID already has usage but arrives with a fresh random ID (`fp_conflict` pattern), the event is logged to `abuse_signals`.

Priority order in `guest_shop_id()`:
1. `email_shop_id` (most authoritative — verified email)
2. `fp_guest_id` (survives incognito tab resets)
3. `guest_id` in session cookie (random, ephemeral)
4. `easylisting_guest_id` cookie (random, 180-day persistent)

---

## 6. AI Pipeline

### 6.1 Provider Chain

Three AI providers are configured in a priority order. The application tries each in sequence if the previous returns a `quota_exceeded` runtime error (HTTP 429 / RESOURCE_EXHAUSTED):

```
① Google Gemini 2.5 Flash  (primary — free)
        ↓ quota_exceeded
② NVIDIA NIM — Llama-3.2-90B-Vision  (secondary — free)
        ↓ quota_exceeded
③ OpenAI GPT-4o  (tertiary — paid, opt-in via ALLOW_PAID_OPENAI=true)
```

Within Gemini, there is an inner fallback from `gemini-2.5-flash` to `gemini-2.5-flash-lite` on quota errors before escalating to NVIDIA.

The provider to try first can be specified by the frontend (`provider` form field). The chain starts at the requested provider and wraps around, skipping providers with no API key configured.

### 6.2 Prompt Structure

Each call to `_build_prompt(hint, lang, platform)` assembles:

1. **Base platform prompt** — one of 8 hardcoded expert copywriter prompts from `PLATFORM_PROMPTS`, each tailored to the target marketplace's rules (title length limits, keyword strategies, field schemas, language).
2. **Language injection** — if `lang != "en"` and the platform is not a Turkish marketplace (Trendyol/Hepsiburada/n11 have Turkish baked in), a translation directive is appended.
3. **Seller hint** — the user-supplied free-text hint (max 200 chars) appended as `"Seller hint: ..."`.
4. **Shop style** — if the seller has saved brand settings (via `/api/template`), these are merged as a `"Saved shop style:"` block with fields: brand tone, material phrases, production time, shipping note, call to action.

All prompts instruct the model to return **only valid JSON** with a strict schema. Responses are parsed by `_parse_ai_json()` which tries direct `json.loads`, then regex-extracts the first `{...}` block, then strips markdown fences.

### 6.3 Image Handling

- Up to 5 images per request; each validated for MIME type (`image/jpeg`, `image/png`, `image/webp`, `image/gif`) and magic bytes (JPEG `\xFF\xD8\xFF`, PNG signature, RIFF/WEBP, GIF87a/89a).
- Max upload size: 30 MB (`MAX_CONTENT_LENGTH`).
- For Gemini: images are passed as `Part.from_bytes(data=bytes, mime_type="image/jpeg")`.
- For NVIDIA/OpenAI: the first image is base64-encoded and sent as a `data:image/jpeg;base64,...` URL in the chat messages array.
- After generation, images are re-attached to the response as base64 data URLs in `image_previews`.

### 6.4 Post-Generation Enrichment (Etsy)

After a successful Etsy listing generation:
1. `_find_taxonomy_id(taxonomy_query)` calls the Etsy taxonomy API and scores category paths by word overlap. The best `taxonomy_id` and full path are injected into the response.
2. The shop's shipping profiles are fetched and returned in `shipping_profiles` for the frontend to offer a dropdown.

### 6.5 Photo Variant Generation (Pro — FLUX.1 Kontext)

Pro subscribers can generate 3 professional photo variants per product image:

1. **White background** — studio lighting, pure white BG, sharp commercial shot.
2. **Lifestyle** — warm natural window light, minimalist interior.
3. **Seasonal gift** — soft golden-hour, gift-ready scene.

Each variant is produced by `POST https://fal.run/fal-ai/flux-pro/kontext` with the original image URL + a crafted style prompt. Monthly limit: 30 variants (`PHOTO_VARIANT_MONTHLY_LIMIT`), reset per calendar month. Usage is tracked in `shops.photo_variant_used` + `photo_variant_period`.

---

## 7. Payment System

### 7.1 Subscription Plans

| Plan | EUR/mo | EUR/yr | TRY/mo | TRY/yr | Features |
|------|--------|--------|--------|--------|----------|
| Free | — | — | — | — | 3 AI generations, 1 listing improve |
| Starter | €4.99 | €49.99 | ₺249 | ₺2,490 | Unlimited generations + improvements, bulk generate, listing variants, translation |
| Pro | €9.99 | €99.00 | ₺499 | ₺4,990 | Everything in Starter + FLUX photo variants (30/mo) |

Plans are stored as Stripe Price IDs in environment variables. The correct price is selected based on the active domain (`_is_try_domain()` checks `request.host` for "kolaylistele").

### 7.2 Checkout Flow

```
Frontend                Flask                 Stripe
   |                     |                     |
   |-- POST /stripe/checkout {plan} -------->  |
   |                     | validate plan name
   |                     | resolve price_id from env
   |                     | stripe.checkout.Session.create(
   |                     |   mode="subscription",
   |                     |   client_reference_id=shop_id,
   |                     |   metadata={shop_id, plan}
   |                     | )                   |
   |<-- {url: checkout_url} -----------------  |
   |-- redirect to checkout_url ------------->  |
   |                     |                     |
   |<-- redirect to /upgrade?success=1 ------  |
```

### 7.3 Webhook Events

`POST /stripe/webhook` validates the Stripe-Signature header using `stripe.Webhook.construct_event()` with `STRIPE_WEBHOOK_SECRET`.

| Event | Action |
|-------|--------|
| `checkout.session.completed` | `set_premium(shop_id, customer_id, subscription_id, active=True, plan=plan)` |
| `customer.subscription.deleted` | `set_premium(shop_id, customer_id, subscription_id, active=False)` — revokes premium |
| `customer.subscription.paused` | Same as deleted — access suspended |

The CSRF exemption is applied to this endpoint since Stripe cannot include a CSRF token.

---

## 8. Security Layers

### Layer 1 — Cloudflare WAF + CDN

All traffic enters via Cloudflare. Provides: DDoS mitigation, bot score filtering, global CDN cache, and SSL/TLS termination. The Flask app receives requests with `X-Forwarded-Proto` and `X-Forwarded-For` headers.

### Layer 2 — Probe Path Blocking

`@app.before_request` hook `block_probe_paths()` returns 404 for requests matching a curated list of scanner/probe patterns:

- Prefixes: `/.git`, `/.env`, `/root/`, `/etc/`, `/proc/`, `/wp-`, `/_next/`, `/_react/`, `/backend/`, `/api/config`, `/docker-compose`, `/attacker/`, `/aws-codecommit/`
- Suffixes: `.git-credentials`, `/wlwmanifest.xml`, `/xmlrpc.php`

### Layer 3 — CSRF Protection

Flask-WTF `CSRFProtect` validates a CSRF token on all state-changing requests. Tokens have no time limit (`WTF_CSRF_TIME_LIMIT = None`). Endpoints explicitly exempt: `/api/fingerprint`, `/api/magic-link`, `/stripe/webhook` (each has its own auth mechanism).

### Layer 4 — Rate Limiting

Flask-Limiter throttles at multiple granularities:

| Endpoint | Limit |
|----------|-------|
| Global default | 200/day, 60/hour |
| `POST /api/generate` | 10/min, 50/day |
| `POST /api/bulk-generate` | 5/min, 30/day |
| `POST /api/magic-link` | 5/min, 10/hour |
| `GET /auth/start`, `/auth/callback` | 10/min |
| `GET /auth/magic` | 20/min |
| `POST /stripe/checkout` | 10/min |

Storage: in-memory by default; set `REDIS_URL` to use Redis for multi-worker consistency.

### Layer 5 — Magic Bytes Image Validation

Uploaded images are validated at two levels:
1. `Content-Type` header must be one of: `image/jpeg`, `image/png`, `image/webp`, `image/gif`.
2. Raw bytes are checked against known file signatures (JPEG `\xFF\xD8\xFF`, PNG `\x89PNG\r\n\x1a\n`, RIFF/WEBP, GIF87a/89a) by `_is_valid_image_bytes()`.

Both checks are applied at upload (`/api/generate`) and at publish (`/api/publish` when processing `image_previews`).

### Layer 6 — Security Headers

Set by `@app.after_request set_security_headers()`:

| Header | Value |
|--------|-------|
| `Content-Security-Policy` | Per-request nonce for scripts; restricts `connect-src`, `frame-src`, `img-src` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` (production only) |

Session cookies: `HttpOnly=True`, `SameSite=Lax`, `Secure=True` (production), 48-hour lifetime.

---

## 9. Guest Abuse Prevention

The guest system allows up to 3 free AI generations without an Etsy account. The abuse prevention stack layers multiple signals:

### Identity Hierarchy (highest authority first)

1. **Email-verified ID** (`guest_email_{hash20}`) — set by `/auth/magic` after clicking a magic link. Persists to `verified_emails` table. Cannot be reset by clearing cookies.
2. **Browser fingerprint ID** (`guest_fp_{fp24}`) — derived from a client-side browser fingerprint hash. Survives incognito tabs within the same browser/device. Set by `POST /api/fingerprint`.
3. **Session cookie ID** (`guest_{sha256[:16]}`) — random ID in the Flask session. Persists for the session duration.
4. **Long-lived cookie** (`easylisting_guest_id`) — signed 180-day HTTP-only cookie with a random ID. Survives session expiry.

### Abuse Detection Logic

When a `POST /api/fingerprint` arrives:

- **Usage migration**: if the FP shop is new (free_used=0) but the random shop has usage, migrate the count to prevent bypass via incognito.
- **`fp_conflict` detection**: if the FP shop already has usage but arrives with a fresh random ID (pattern: incognito tab opened after clearing cookies), the event is logged with `log_abuse_signal("fp_conflict", ...)`.

When a limit-hit event occurs (`/api/generate` returns 403), `log_abuse_signal("limit_hit", ...)` is called with IP hash, guest ID, and FP hash.

### Admin Monitoring

`GET /admin/abuse?token=<ADMIN_TOKEN>&days=7` (token compared with `secrets.compare_digest`) returns an HTML dashboard showing:

- Event counts grouped by type.
- Top 20 IP hashes by unique guest IDs created.
- Top 20 fingerprint hashes by unique guest IDs.

---

## 10. Email

### SMTP Configuration

| Setting | Value |
|---------|-------|
| Provider | Natro hosted email (`mail.kurumsaleposta.com`) |
| From address | `info@kolaylistele.com` |
| Port | 465 (SSL) — STARTTLS (587) has a certificate mismatch on Natro |
| Env vars | `MAIL_HOST`, `MAIL_PORT`, `MAIL_USER`, `MAIL_PASS`, `MAIL_FROM` |

The `send_email(to, subject, body_text, body_html)` helper in `app.py`:
- Sends multipart/alternative with plain text + HTML.
- Uses `smtplib.SMTP_SSL` on port 465, `SMTP + STARTTLS` on other ports.
- Returns `True` on success, `False` on failure (never raises — failure is logged only).

### Magic Link Email Template

Bilingual (TR/EN) HTML email selected based on `request.host`:
- **TR** (kolaylistele): Subject "kolaylistele — Giriş bağlantınız", button "Devam Et →"
- **EN** (easylisting): Subject "EasyListing — Your sign-in link", button "Continue to EasyListing →"

Template features: `display:none` preheader text, branded logo bar, card with CTA button, fallback plain-text link, 15-minute expiry notice box, purple `#F0EEFF` accent, anti-spam structure.

---

## 11. Deployment

### Railway Configuration (`railway.json`)

```json
{
  "build":  { "builder": "NIXPACKS" },
  "deploy": {
    "startCommand": "gunicorn app:app --workers 2 --timeout 120",
    "healthcheckPath": "/health",
    "restartPolicyType": "ON_FAILURE"
  }
}
```

`Procfile`: `web: gunicorn app:app --workers 2 --timeout 120 --bind 0.0.0.0:$PORT`

The `--timeout 120` is necessary because Gemini vision calls can take up to ~30 seconds and FLUX photo generation can take up to ~90 seconds.

### SQLite Persistence

A Railway Volume is mounted at `/data/`. Set `DB_PATH=/data/easylisting.sqlite` to persist data across deploys.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FLASK_SECRET` | Yes (prod) | 32+ byte hex string for session signing |
| `ENV` | Yes (prod) | Set to `production` to enable HTTPS redirect, HSTS, secure cookies |
| `DB_PATH` | Recommended | Path to SQLite file; default `easylisting.sqlite` |
| `ETSY_API_KEY` | Yes | Etsy app keystring |
| `ETSY_SHARED_SECRET` | Yes | Etsy app shared secret |
| `REDIRECT_URI` | Yes | Full OAuth callback URL, e.g. `https://app.railway.app/auth/callback` |
| `GEMINI_API_KEY` | Yes (primary AI) | Google AI Studio key |
| `NVIDIA_API_KEY` | Recommended | NVIDIA NIM key (free) |
| `OPENAI_API_KEY` | Optional | OpenAI key — only used if `ALLOW_PAID_OPENAI=true` |
| `ALLOW_PAID_OPENAI` | No | `true`/`1` to enable GPT-4o as tertiary fallback |
| `FAL_KEY` | Pro feature | fal.ai key for FLUX photo variants |
| `PHOTO_VARIANT_MONTHLY_LIMIT` | No | Default `30` — monthly photo generation cap per Pro shop |
| `STRIPE_SECRET_KEY` | Yes (payments) | Stripe secret key |
| `STRIPE_PUBLISHABLE_KEY` | Yes (payments) | Stripe publishable key (sent to frontend) |
| `STRIPE_STARTER_PRICE_ID` | Yes | Stripe price ID for Starter monthly (€4.99) |
| `STRIPE_PRO_PRICE_ID` | Yes | Stripe price ID for Pro monthly (€9.99) |
| `STRIPE_STARTER_ANNUAL_PRICE_ID` | Optional | Starter annual (€49.99) |
| `STRIPE_PRO_ANNUAL_PRICE_ID` | Optional | Pro annual (€99.00) |
| `STRIPE_STARTER_PRICE_ID_TRY` | Yes (TR) | Starter monthly (₺249) |
| `STRIPE_PRO_PRICE_ID_TRY` | Yes (TR) | Pro monthly (₺499) |
| `STRIPE_STARTER_ANNUAL_PRICE_ID_TRY` | Optional | Starter annual (₺2,490) |
| `STRIPE_PRO_ANNUAL_PRICE_ID_TRY` | Optional | Pro annual (₺4,990) |
| `STRIPE_WEBHOOK_SECRET` | Yes (payments) | Stripe webhook signing secret |
| `MAIL_HOST` | Yes (email) | `mail.kurumsaleposta.com` |
| `MAIL_PORT` | Yes (email) | `465` |
| `MAIL_USER` | Yes (email) | `info@kolaylistele.com` |
| `MAIL_PASS` | Yes (email) | Email account password |
| `MAIL_FROM` | Yes (email) | `info@kolaylistele.com` |
| `ADMIN_TOKEN` | Yes (admin) | Random secret for `/admin/abuse` access |
| `REDIS_URL` | Optional | Redis URI for distributed rate limiting |

---

## 12. API Reference

| Endpoint | Method | Auth Required | Description | Rate Limit |
|----------|--------|---------------|-------------|------------|
| `/` | GET | Any (Etsy or Guest) | Main app UI | — |
| `/connect` | GET | None | Etsy connect / guest entry page | — |
| `/guest` | GET | None | Start a guest session | 20/min |
| `/disconnect` | GET | Any | Clear session | — |
| `/listings` | GET | Etsy | Browse shop draft/active listings | — |
| `/bulk` | GET | Etsy + Premium | Bulk generate UI | — |
| `/upgrade` | GET | Etsy | Upgrade/pricing page | — |
| `/privacy` | GET | None | Privacy policy | — |
| `/terms` | GET | None | Terms of service | — |
| `/health` | GET | None | Railway health check → `{status: "ok"}` | — |
| `/auth/start` | GET | None | Begin Etsy OAuth PKCE flow | 10/min |
| `/auth/callback` | GET | None | Etsy OAuth callback, exchanges code for token | 10/min |
| `/auth/magic` | GET | None | Consume magic link token, establish guest session | 20/min |
| `/api/fingerprint` | POST | Guest | Submit browser fingerprint hash for persistent guest ID | 30/min |
| `/api/magic-link` | POST | Guest | Send magic link email to provided address | 5/min, 10/hr |
| `/api/status` | GET | Any | Returns `{allowed, remaining, is_guest}` for the current shop | 60/min |
| `/api/generate` | POST | Any | Generate listing from uploaded images + hint. Form fields: `images[]`, `hint`, `provider`, `lang`, `platform` | 10/min, 50/day |
| `/api/publish` | POST | Etsy | Publish listing + images to Etsy as draft | 20/min |
| `/api/template` | GET | Etsy | Get shop's saved style template | 60/min |
| `/api/template` | POST | Etsy | Save shop's style template | 60/min |
| `/api/taxonomy` | GET | Etsy | Search Etsy taxonomy nodes. Query param: `q` | 30/min |
| `/api/improve-listing` | POST | Any | Improve an existing listing field. Body: `{action, title, description, tags, lang}`. Actions: `title_seo`, `description_warm`, `tags`, `shorten_etsy` | 20/min |
| `/api/listing-variants` | POST | Any + Premium | Generate 3 listing variants (SEO, Emotional, Gift-focused) | 10/min |
| `/api/translate` | POST | Etsy | Translate listing to `de`/`tr`/`en`. Body: `{lang, title, description, tags}` | 30/min |
| `/api/bulk-generate` | POST | Etsy + Premium | Same as /api/generate but for bulk flow | 5/min, 30/day |
| `/api/generate-photos` | POST | Etsy + Pro | Generate 3 FLUX photo variants. Body: `{image (data URL), title, materials, colors, ...}` | 5/min |
| `/stripe/checkout` | POST | Etsy | Create Stripe Checkout Session. Body: `{plan}` | 10/min |
| `/stripe/webhook` | POST | None (Stripe-Signature) | Receive Stripe webhook events | — |
| `/admin/abuse` | GET | Query token | Abuse monitoring dashboard. Query: `?token=ADMIN_TOKEN&days=7` | — |

---

*Last updated: June 2025 · Architecture v2.0*
