# kolaylistele / EasyListing — System Design

> **Confidential — Internal Engineering Reference**
> Version 3.0 · June 2026

---

## 1. Overview

**kolaylistele** (Turkish: "list easily") is a SaaS tool that uses AI vision models to analyse product photos and generate fully-optimised marketplace listings in seconds. Sellers upload one or more images, optionally add a short hint, and receive title, description, tags, materials, price suggestion, and an Instagram caption — ready to publish directly to their Etsy shop or other supported platforms.

### Products / Domains

| Domain | Market | Currency | Language |
|--------|--------|----------|----------|
| `kolaylistele.com` | Turkey | ₺ TRY | Turkish |
| `easylisting.app` | International | € EUR | English / German |

Both domains run the same Flask application; the active domain is detected at runtime via `request.host` to switch Stripe price IDs and email copy.

### Supported Platforms

The AI pipeline generates platform-specific listings for: **Etsy**, **Shopify**, **Amazon**, **eBay**, **WooCommerce**, **Pinterest**, **Trendyol**, **Hepsiburada**, **n11**.

---

## 2. Architecture Summary

| Layer | Responsibility | Key Components |
|-------|---------------|----------------|
| **Presentation** | User-facing browser UI + iOS native app | Browser (vanilla JS + Stripe.js), iOS Swift app |
| **CDN / WAF** | Edge cache, DDoS mitigation, TLS | Cloudflare |
| **Security & Routing** | Request validation, abuse blocking, auth tokens | Flask-Limiter, probe-path blocker, Flask-WTF CSRF, browser fingerprint, CSP nonces, magic-byte image validation |
| **Application** | Business logic, AI orchestration, payment flows | Python 3.12 + Flask 3.1, Gunicorn 2 workers, blueprint-per-feature architecture |
| **Data & Services** | Persistence, third-party APIs | SQLite on Railway Volume, Etsy REST API v3, Stripe, Google Gemini, NVIDIA NIM, OpenAI, fal.ai, Resend |
| **Analytics** | Product telemetry | PostHog EU cloud |

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
| Email | Resend | — | Transactional email (magic links) via REST API |
| Analytics | PostHog | — | EU cloud — pageviews, autocapture, server-side events |
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
| `own_api_key` | TEXT | Reserved for BYO-key feature (unused) |
| `created_at` | TIMESTAMP | Row creation time |

### 4.2 `templates`

One row per shop storing the shop's reusable style settings as a JSON blob.

| Column | Type | Description |
|--------|------|-------------|
| `shop_id` | TEXT PK | FK → shops.shop_id |
| `data` | TEXT | JSON: `brand_tone`, `material_phrases`, `production_time`, `shipping_note`, `brand_cta`, `tags`, `materials`, `price`, `shipping_profile_id`, `personalization_instructions` |
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

### 4.5 `mobile_tokens`

Auth tokens for the iOS app. One row per active device/session.

| Column | Type | Description |
|--------|------|-------------|
| `token` | TEXT PK | `secrets.token_urlsafe(32)` — sent as `X-Mobile-Token` header |
| `shop_id` | TEXT | Associated shop ID |
| `access_token` | TEXT | Etsy OAuth access token (null for guests) |
| `refresh_token` | TEXT | Etsy OAuth refresh token (null for guests) |
| `expires_at` | INTEGER | Unix timestamp — Etsy token expiry |
| `shop_name` | TEXT | Etsy shop name or "Guest" |
| `is_guest` | INTEGER | 1 = unauthenticated guest, 0 = Etsy-connected |
| `is_email` | INTEGER | 1 = email-verified guest |
| `guest_id` | TEXT | Random guest ID reference for quota tracking |
| `created_at` | TIMESTAMP | Token creation time |

Auto-refresh: when `expires_at - now < 120s`, the token endpoint refreshes the Etsy access token transparently.

### 4.6 `fp_sessions`

Maps browser fingerprint IDs to email-verified shop IDs. Used to restore session state after browser cache clears.

| Column | Type | Description |
|--------|------|-------------|
| `fp_id` | TEXT PK | Browser fingerprint ID (`guest_fp_{fp24}`) |
| `email_shop_id` | TEXT | The verified email shop to restore on next visit |
| `created_at` | TIMESTAMP | — |

### 4.7 `marketing_consents`

Records email addresses that opted in to marketing (collected at magic-link time).

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK AUTOINCREMENT | — |
| `email` | TEXT | Plaintext email (for sending) |
| `email_hash` | TEXT UNIQUE | SHA-256 for dedup |
| `locale` | TEXT | `en` or `tr` |
| `source` | TEXT | `magic_link` |
| `consented_at` | TIMESTAMP | Opt-in time |
| `unsubscribe_token` | TEXT UNIQUE | Token in unsubscribe links |
| `unsubscribed_at` | TIMESTAMP | Set when user clicks unsubscribe |

Index on `email_hash` and `unsubscribe_token`.

### 4.8 `platform_credentials`

Stores third-party marketplace credentials (currently Trendyol).

| Column | Type | Description |
|--------|------|-------------|
| `shop_id` | TEXT | FK → shops.shop_id |
| `platform` | TEXT | `trendyol` (extensible) |
| `credentials` | TEXT | JSON: `{supplier_id, api_key, api_secret}` |
| `connected_at` | TIMESTAMP | — |

Primary key: `(shop_id, platform)`.

### 4.9 `abuse_signals`

Append-only event log used by the `/admin/abuse` monitoring endpoint.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK AUTOINCREMENT | Row ID |
| `event` | TEXT | `new_guest`, `limit_hit`, `fp_conflict` |
| `ip_hash` | TEXT | SHA-256[:16] of client IP (hashed for privacy) |
| `guest_id` | TEXT | Resolved guest shop ID at time of event |
| `fp_hash` | TEXT | Browser fingerprint hash slice (24 chars) |
| `detail` | TEXT | Free-text extra context |
| `created_at` | TIMESTAMP | Event time |

### 4.10 Session Store

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

### 4.11 `easylisting_guest_id` Cookie

A separate long-lived (180-day) HTTP-only, signed cookie storing the random guest ID. Signed with `URLSafeSerializer(SECRET_KEY, salt="guest-id")`. Persists across browser sessions independently of the server-side session.

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

**Mobile variant:** `GET /auth/start?mobile=1` → after callback, creates a `mobile_token`, redirects to `easylisting://auth?mobile_token=...&shop_name=...`.

### 5.2 Guest Magic Link

```
Browser                Flask               Resend
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

Email addresses are never stored in plaintext — only their SHA-256 hash. The `email_shop_id` is the most authoritative guest identity.

**Mobile variant:** `POST /api/magic-link` with `X-Mobile-Request` header returns `{already_verified, mobile_token}` if email is already verified. `GET /auth/magic?token=...&m=1` renders a handoff page that deeplinks the token to the app.

**App Store review shortcut:** If `APPSTORE_REVIEW_EMAIL` is set and matches the submitted email, magic-link consumption immediately grants a Pro session without email sending.

### 5.3 Mobile Guest Session

```
iOS App              Flask
   |                   |
   |-- POST /auth/mobile/guest -->|
   |                   | create mobile_token(is_guest=1)
   |<-- {token: "..."} |
   |                   |
   | (all subsequent calls include X-Mobile-Token: {token})
   |                   |
   |-- POST /auth/mobile/logout -->|
   |                   | delete mobile_token from DB
   |<-- {ok: true} ----|
```

Rate limit: 5/hour, 10/day per IP to prevent abuse.

### 5.4 Browser Fingerprint Fallback

On `/guest` entry, a random `guest_id` is created and stored in both the server-side session and the `easylisting_guest_id` cookie (180-day). The frontend sends a derived browser fingerprint to `POST /api/fingerprint`:

1. Client computes a fingerprint hash (canvas, fonts, UA, etc.) and sends `{fp: "<hex>"}`.
2. Server creates `fp_guest_id = f"guest_fp_{fp[:24]}"` and stores it in the session.
3. If the current random ID has usage > 0 but the FP ID is new, usage is migrated to the FP ID.
4. If the FP ID already has usage but arrives with a fresh random ID (`fp_conflict`), the event is logged to `abuse_signals`.

Priority order in `guest_shop_id()`:
1. `email_shop_id` — verified email (most authoritative)
2. `fp_guest_id` — survives incognito tab resets
3. `guest_id` in session cookie
4. `easylisting_guest_id` cookie (180-day)

---

## 6. AI Pipeline

### 6.1 Provider Chain

Three AI providers are configured in a priority order. The application tries each in sequence if the previous returns a `quota_exceeded` error (HTTP 429 / RESOURCE_EXHAUSTED):

```
① Google Gemini 2.5 Flash  (primary — free)
        ↓ quota_exceeded
   Google Gemini 2.5 Flash Lite  (inner fallback)
        ↓ quota_exceeded
② NVIDIA NIM — Llama-3.2-90B-Vision  (secondary — free, single image)
        ↓ quota_exceeded
③ OpenAI GPT-4o  (tertiary — paid, opt-in via ALLOW_PAID_OPENAI=true)
```

The provider to try first can be specified by the frontend (`provider` form field).

### 6.2 Prompt Structure

Each call to `_build_prompt(hint, lang, platform)` assembles:

1. **Base platform prompt** — one of 8 hardcoded expert copywriter prompts from `PLATFORM_PROMPTS`, each tailored to the target marketplace's rules (title length limits, keyword strategies, field schemas, language).
2. **Language injection** — if `lang != "en"` and the platform is not a Turkish marketplace.
3. **Seller hint** — the user-supplied free-text hint (max 200 chars).
4. **Shop style** — if the seller has saved brand settings via `/api/template`, these are merged as a "Saved shop style:" block.

All prompts instruct the model to return **only valid JSON** with a strict schema. Responses are parsed by `_parse_ai_json()` which tries direct `json.loads`, regex-extracts the first `{...}` block, then strips markdown fences.

### 6.3 Image Handling

- Up to 5 images per request; each validated for MIME type and magic bytes (JPEG `\xFF\xD8\xFF`, PNG signature, RIFF/WEBP, GIF87a/89a).
- Max upload size: 30 MB (`MAX_CONTENT_LENGTH`).
- For Gemini: images passed as `Part.from_bytes(data=bytes, mime_type="image/jpeg")`.
- For NVIDIA/OpenAI: first image base64-encoded as `data:image/jpeg;base64,...` URL.
- After generation, images re-attached to the response as base64 data URLs in `image_previews`.

### 6.4 Post-Generation Enrichment (Etsy)

After a successful Etsy listing generation:
1. `_find_taxonomy_id(taxonomy_query)` calls the Etsy taxonomy API and scores category paths by word overlap. Best `taxonomy_id` and full path are injected into the response.
2. The shop's shipping profiles are fetched and returned in `shipping_profiles`.

### 6.5 Photo Variant Generation (Pro — FLUX.1 Kontext)

Pro subscribers can generate 3 professional photo variants per product image:

1. **White background** — studio lighting, pure white BG, sharp commercial shot.
2. **Lifestyle** — warm natural window light, minimalist interior.
3. **Seasonal gift** — soft golden-hour, gift-ready scene.

Each variant is produced by `POST https://fal.run/fal-ai/flux-pro/kontext`. Monthly limit: 30 variants (`PHOTO_VARIANT_MONTHLY_LIMIT`), reset per calendar month. Usage tracked in `shops.photo_variant_used` + `photo_variant_period`.

### 6.6 Improve & Translate

- **Improve** (`POST /api/improve-listing`): 4 actions — `title_seo`, `description_warm`, `tags`, `shorten_etsy`. Free users: 1/month; premium: unlimited.
- **Translate** (`POST /api/translate`): Translates title, description, tags to DE/TR/EN. Consumes 1 improve credit (unless premium).
- **Variants** (`POST /api/listing-variants`, premium): Generates 3 variants — SEO-focused, Emotional/handmade, Gift-focused.

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
   |  (or /api/stripe/checkout for mobile)     |
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

**Mobile variant:** `POST /api/stripe/checkout` → app opens URL in Safari → Stripe redirects to `/upgrade/mobile/return?status=success|cancel` → renders handoff page → meta-refresh to `easylisting://upgrade/success?plan=...` or `easylisting://upgrade/cancel`.

### 7.3 Webhook Events

`POST /stripe/webhook` validates the Stripe-Signature header using `stripe.Webhook.construct_event()` with `STRIPE_WEBHOOK_SECRET`. CSRF exempt.

| Event | Action |
|-------|--------|
| `checkout.session.completed` | `set_premium(shop_id, customer_id, subscription_id, active=True, plan=plan)` + PostHog `plan_upgraded` event |
| `customer.subscription.deleted` | `set_premium(..., active=False)` — revokes premium |
| `customer.subscription.paused` | Same as deleted — access suspended |

---

## 8. Trendyol Integration

Trendyol is a Turkish marketplace. Unlike Etsy (OAuth), Trendyol uses a credentials-based integration.

### 8.1 Connection

1. User enters Supplier ID + API Key + API Secret in settings.
2. `POST /api/trendyol/connect` validates by fetching the supplier's address list from Trendyol API.
3. Credentials stored in `platform_credentials` table as encrypted JSON.
4. `POST /api/trendyol/disconnect` removes the row.

### 8.2 Metadata Fetching

| Endpoint | Purpose | Cache |
|----------|---------|-------|
| `GET /api/trendyol/categories` | Full category tree | 24h per supplier |
| `GET /api/trendyol/categories/<id>/attributes` | Attribute schema for a category | None |
| `GET /api/trendyol/brands?q=<name>` | Brand search | None |
| `GET /api/trendyol/addresses` | Supplier shipping/return addresses | None |

### 8.3 Publishing

1. User generates listing (AI or manual) with platform=trendyol.
2. `POST /api/trendyol/publish`:
   - Validates: barcode (required), pricing (sale ≤ list price), required fields.
   - Saves images locally → generates public URLs at `/uploads/{uuid}.jpg`.
   - Creates Trendyol product payload (barcode, title max 65 chars, HTML description, brand_id, category_id, attributes, images, pricing, VAT, shipping/returning addresses).
   - POSTs to `/integration/product/sellers/{supplier_id}/v2/products`.
3. Returns confirmation or Turkish error message.

### 8.4 Static Image Serving

`GET /uploads/<filename>` serves images from `static/uploads/` for Trendyol's image ingestion. These are public, unguarded routes.

---

## 9. Security Layers

### Layer 1 — Cloudflare WAF + CDN

All traffic enters via Cloudflare. Provides: DDoS mitigation, bot score filtering, global CDN cache, and SSL/TLS termination.

### Layer 2 — Probe Path Blocking

`@app.before_request` hook `block_probe_paths()` returns 404 for common scanner patterns:
- Prefixes: `/.git`, `/.env`, `/root/`, `/etc/`, `/proc/`, `/wp-`, `/_next/`, `/_react/`, `/backend/`, `/api/config`, `/docker-compose`, `/attacker/`, `/aws-codecommit/`
- Suffixes: `.git-credentials`, `/wlwmanifest.xml`, `/xmlrpc.php`

### Layer 3 — CSRF Protection

Flask-WTF `CSRFProtect` validates tokens on all state-changing requests. `WTF_CSRF_TIME_LIMIT = None`. Explicitly exempt: `/api/fingerprint`, `/api/magic-link`, `/stripe/webhook`, all mobile endpoints (`/auth/mobile/*`, `/api/trendyol/*`).

### Layer 4 — Rate Limiting

Flask-Limiter throttles at multiple granularities:

| Endpoint | Limit |
|----------|-------|
| Global default | 200/day, 60/hour |
| `POST /api/generate` | 10/min, 50/day |
| `POST /api/bulk-generate` | 5/min, 30/day |
| `POST /api/magic-link` | 5/min, 10/hour |
| `POST /auth/mobile/guest` | 5/hour, 10/day |
| `GET /auth/start`, `/auth/callback` | 10/min |
| `GET /auth/magic` | 20/min |
| `POST /stripe/checkout` | 10/min |
| `POST /api/generate-photos` | 5/min |

Storage: in-memory by default; set `REDIS_URL` to use Redis for multi-worker consistency.

### Layer 5 — Magic Bytes Image Validation

Uploaded images are validated at two levels:
1. `Content-Type` header must be one of: `image/jpeg`, `image/png`, `image/webp`, `image/gif`.
2. Raw bytes checked against known file signatures by `_is_valid_image_bytes()`.

Both checks applied at upload and at publish.

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
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Embedder-Policy` | `require-corp` |

Session cookies: `HttpOnly=True`, `SameSite=Lax`, `Secure=True` (production), 48-hour lifetime.

---

## 10. Analytics — PostHog

PostHog EU cloud (`eu.i.posthog.com`) is embedded via `_posthog.html` partial included in the base template.

- **Client-side**: pageview capture, autocapture, pageleave. Identity set to `shop_id` + `{name, is_guest}` when available.
- **Server-side events**: `plan_upgraded` (on Stripe webhook `checkout.session.completed`), `plan_cancelled` (on subscription deleted/paused).
- Enabled only when `POSTHOG_TOKEN` env var is set; renders nothing otherwise.
- CSP nonce applied to the inline init script.

---

## 11. Email

Transactional email is sent via the **Resend** API (`resend` Python SDK).

| Setting | Value |
|---------|-------|
| Provider | Resend |
| From address | `info@kolaylistele.com` (configurable via `MAIL_FROM`) |
| Env vars | `RESEND_API_KEY`, `MAIL_FROM` |

The `send_email(to, subject, body_text, body_html)` helper in `core/email.py`:
- Returns `(True, None)` on success, `(False, reason_str)` on failure (never raises).

### Magic Link Email Template

Bilingual (TR/EN) HTML email selected based on `request.host`:
- **TR** (kolaylistele): Subject "kolaylistele — Giriş bağlantınız", button "Devam Et →"
- **EN** (easylisting): Subject "EasyListing — Your sign-in link", button "Continue to EasyListing →"

Template features: `display:none` preheader text, branded logo bar, card with CTA button, fallback plain-text link, 15-minute expiry notice box, purple `#F0EEFF` accent, anti-spam structure.

---

## 12. Guest Abuse Prevention

The guest system allows up to 3 free AI generations without an Etsy account. The abuse prevention stack layers multiple signals:

### Identity Hierarchy (highest authority first)

1. **Email-verified ID** (`guest_email_{hash20}`) — set by `/auth/magic`. Persists to `verified_emails` table. Cannot be reset by clearing cookies.
2. **Browser fingerprint ID** (`guest_fp_{fp24}`) — derived from client-side fingerprint hash. Survives incognito tabs within the same browser/device.
3. **Session cookie ID** (`guest_{sha256[:16]}`) — random ID in the Flask session.
4. **Long-lived cookie** (`easylisting_guest_id`) — signed 180-day HTTP-only cookie.

### Abuse Detection Logic

When a `POST /api/fingerprint` arrives:
- **Usage migration**: if the FP shop is new but the random shop has usage, migrate the count.
- **`fp_conflict` detection**: if the FP shop already has usage but arrives with a fresh random ID, log to `abuse_signals`.

When a limit-hit event occurs, `log_abuse_signal("limit_hit", ...)` is called.

---

## 13. Deployment

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

The `--timeout 120` is necessary because Gemini vision calls can take ~30s and FLUX photo generation ~90s.

Two separate Railway services (one per domain) with their own env vars + ephemeral SQLite. Both deploy from this repo root. The backend lives in `web/`, so `Procfile` and `railway.json` start it with `gunicorn --chdir web app:app`.

### SQLite Persistence

A Railway Volume is mounted at `/data/`. Set `DB_PATH=/data/easylisting.sqlite` to persist data across deploys.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FLASK_SECRET` | Yes | 32+ byte hex string for session signing |
| `ENV` | Yes (prod) | Set to `production` to enable HTTPS redirect, HSTS, secure cookies |
| `DB_PATH` | Recommended | Path to SQLite file; default `easylisting.sqlite` |
| `LOG_LEVEL` | No | Python logging threshold; default `INFO` |
| `LOG_FORMAT` | No | Set `json` for structured logs; production defaults to JSON |
| `ETSY_API_KEY` | Yes | Etsy app keystring |
| `ETSY_SHARED_SECRET` | Yes | Etsy app shared secret |
| `REDIRECT_URI` | Yes | Full OAuth callback URL |
| `GEMINI_API_KEY` | Yes | Google AI Studio key (primary AI) |
| `NVIDIA_API_KEY` | Recommended | NVIDIA NIM key (free fallback) |
| `OPENAI_API_KEY` | Optional | OpenAI key — used only if `ALLOW_PAID_OPENAI=true` |
| `ALLOW_PAID_OPENAI` | No | `true`/`1` to enable GPT-4o as tertiary fallback |
| `FAL_KEY` | Pro feature | fal.ai key for FLUX photo variants |
| `PHOTO_VARIANT_MONTHLY_LIMIT` | No | Default `30` — monthly photo generation cap per Pro shop |
| `STRIPE_SECRET_KEY` | Yes | Stripe secret key |
| `STRIPE_PUBLISHABLE_KEY` | Yes | Stripe publishable key (sent to frontend) |
| `STRIPE_STARTER_PRICE_ID` | Yes | Starter monthly (€4.99) |
| `STRIPE_PRO_PRICE_ID` | Yes | Pro monthly (€9.99) |
| `STRIPE_STARTER_ANNUAL_PRICE_ID` | Optional | Starter annual (€49.99) |
| `STRIPE_PRO_ANNUAL_PRICE_ID` | Optional | Pro annual (€99.00) |
| `STRIPE_STARTER_PRICE_ID_TRY` | Yes (TR) | Starter monthly (₺249) |
| `STRIPE_PRO_PRICE_ID_TRY` | Yes (TR) | Pro monthly (₺499) |
| `STRIPE_STARTER_ANNUAL_PRICE_ID_TRY` | Optional | Starter annual (₺2,490) |
| `STRIPE_PRO_ANNUAL_PRICE_ID_TRY` | Optional | Pro annual (₺4,990) |
| `STRIPE_WEBHOOK_SECRET` | Yes | Stripe webhook signing secret |
| `RESEND_API_KEY` | Yes (email) | Resend API key for transactional email |
| `MAIL_FROM` | Yes (email) | Sender address, e.g. `info@kolaylistele.com` |
| `POSTHOG_TOKEN` | Optional | PostHog EU project API key (enables analytics) |
| `APPSTORE_REVIEW_EMAIL` | Optional | Email that bypasses magic-link send and gets instant Pro access (App Store review) |
| `ADMIN_TOKEN` | Yes (admin) | Random secret for `/admin/*` endpoints |
| `REDIS_URL` | Optional | Redis URI for distributed rate limiting across workers |

---

## 14. API Reference

### Web + Shared Endpoints

| Endpoint | Method | Auth | Description | Rate Limit |
|----------|--------|------|-------------|------------|
| `/` | GET | Any | Main app UI | — |
| `/connect` | GET | None | Etsy connect / guest entry page | — |
| `/guest` | GET | None | Start a guest session | 20/min |
| `/disconnect` | GET | Any | Clear session | — |
| `/listings` | GET | Etsy | Browse shop draft/active listings | — |
| `/bulk` | GET | Etsy + Premium | Bulk generate UI | — |
| `/upgrade` | GET | Any | Upgrade/pricing page | — |
| `/privacy` | GET | None | Privacy policy | — |
| `/terms` | GET | None | Terms of service | — |
| `/health` | GET | None | Railway health check → `{status: "ok"}` | — |
| `/auth/start` | GET | None | Begin Etsy OAuth PKCE flow | 10/min |
| `/auth/callback` | GET | None | Etsy OAuth callback | 10/min |
| `/auth/magic` | GET | None | Consume magic link token | 20/min |
| `/api/csrf-token` | GET | None | Returns `{csrf_token}` for mobile | 60/min |
| `/api/fingerprint` | POST | Guest | Submit browser fingerprint | 30/min |
| `/api/magic-link` | POST | Guest | Send magic link email | 5/min, 10/hr |
| `/api/email-verified` | GET | Any | Returns current email verification status | — |
| `/api/status` | GET | Any | Returns `{allowed, remaining, is_guest, plan, …}` | 60/min |
| `/api/generate` | POST | Any | Generate listing from images | 10/min, 50/day |
| `/api/publish` | POST | Etsy | Create Etsy draft listing | 20/min |
| `/api/template` | GET/POST | Etsy | Get/save shop style template | 60/min |
| `/api/taxonomy` | GET | Etsy | Search Etsy taxonomy. Query: `?q=` | 30/min |
| `/api/improve-listing` | POST | Any | AI-improve a listing field | 20/min |
| `/api/listing-variants` | POST | Any + Premium | Generate 3 listing variants | 10/min |
| `/api/translate` | POST | Any | Translate listing to DE/TR/EN | 30/min |
| `/api/bulk-generate` | POST | Etsy + Premium | Bulk AI generation | 5/min, 30/day |
| `/api/generate-photos` | POST | Etsy + Pro | Generate 3 FLUX photo variants | 5/min |
| `/api/listings` | GET | Any | JSON listing list (mobile) | 30/min |
| `/stripe/checkout` | POST | Any | Create Stripe Checkout Session (web) | 10/min |
| `/api/stripe/checkout` | POST | Any | Create Stripe Checkout Session (mobile) | 10/min |
| `/stripe/webhook` | POST | Stripe-Signature | Receive Stripe webhook events | — |
| `/upgrade/mobile/return` | GET | None | Deeplink handler after Stripe mobile checkout | — |
| `/unsubscribe` | GET | None | Process email unsubscribe token | — |

### Mobile-Only Endpoints

| Endpoint | Method | Auth | Description | Rate Limit |
|----------|--------|------|-------------|------------|
| `/auth/mobile/guest` | POST | None | Create guest mobile token | 5/hr, 10/day |
| `/auth/mobile/logout` | POST | Mobile token | Invalidate mobile token | 30/min |

### Trendyol Endpoints

| Endpoint | Method | Auth | Description | Rate Limit |
|----------|--------|------|-------------|------------|
| `/api/trendyol/connect` | POST | Any | Store + validate Trendyol credentials | 10/min |
| `/api/trendyol/disconnect` | POST | Any | Delete Trendyol credentials | 10/min |
| `/api/trendyol/categories` | GET | Any | Full category tree (cached 24h) | 30/min |
| `/api/trendyol/categories/<id>/attributes` | GET | Any | Category attribute schema | 60/min |
| `/api/trendyol/brands` | GET | Any | Brand search by name | 60/min |
| `/api/trendyol/addresses` | GET | Any | Supplier shipping/return addresses | 30/min |
| `/api/trendyol/publish` | POST | Any | Create Trendyol product | 10/min |
| `/uploads/<filename>` | GET | None | Serve product images for Trendyol CDN ingestion | — |

### Admin Endpoints

All protected by `Authorization: Bearer {ADMIN_TOKEN}` (returns 404 on mismatch).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/abuse` | GET | HTML abuse signal summary. Query: `?days=7` |
| `/admin/ping-ai` | GET | Test Gemini, NVIDIA, OpenAI latency + health |
| `/admin/stats` | GET | HTML growth dashboard (email funnel, marketing list, shops) |
| `/admin/shops-json` | GET | All shops as JSON array |
| `/admin/set-plan` | POST | Change shop plan by `shop_id` or `shop_name` |

---

*Last updated: June 2026 · Architecture v3.0*
