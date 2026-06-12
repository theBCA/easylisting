# Changelog

All notable changes to EasyListing / kolaylistele are documented here.

Format: [version] — date — summary, then grouped Added / Changed / Fixed / Security / Removed entries.

---

## [Unreleased]

### Added
- **Plan switching** (`POST /billing/change-plan`): existing subscribers can switch between Starter and Pro without creating a new subscription. Upgrades (Starter→Pro) are prorated and charged immediately; downgrades (Pro→Starter) apply a credit on the next invoice. Upgrade page shows "Switch to Pro →" / "Switch to Starter" buttons for active subscribers.

### Changed
- **Lead pipeline (`scripts/lead_pipeline.py`)**: replaced dead `rupom888~etsy-scraper` actor with `crawlerbros~etsy-scraper` which returns `shopName`/`shopUrl` directly; updated `listings_to_shops` and `run_apify_etsy_scrape` to use the new actor's `startUrls`+`maxItems` input format. `APIFY_ETSY_ACTOR` env var overrides the default. `listings_to_shops` now captures `review_count` and `star_seller` per shop. New `enrich-instagram` command guesses Instagram handles from shop names, scrapes bios via `coderx~instagram-profile-scraper-bio-posts`, and extracts `external_url` + bio emails. New `--min-reviews` flag on `apify-scrape` filters to professional sellers (C strategy). New `--min-listing-count` flag on `enrich-instagram`.

### Added
- **Structured request logging**: `core/logging.py` now configures production JSON logs, `LOG_LEVEL` / `LOG_FORMAT`, request IDs, access logs, exception context, and redaction helpers. Responses include `X-Request-ID`.
- **Billing funnel metrics**: Stripe checkout/session/webhook events are now stored in `payment_events`; `/admin/dashboard`, `/admin/stats`, and `/admin/billing-json` show checkout attempts, paid conversions, failures, cancellations, plan split, domain split, and recent billing events.
- **Daily SQLite backup**: `core/backup.py` daemon thread runs hourly, creates a hot `sqlite3.backup()` copy in `<volume>/backups/` once per 23-hour window. Keeps the 7 most-recent dated files. Starts automatically at app startup via `start_daily_backup()`.
- **Admin dashboard** (`/admin/dashboard`): searchable table of all accounts (type badge, plan, usage, created), inline plan selector with Save button wired to `/admin/set-plan`.
- **Cloudflare Access JWT verification**: `core/admin_auth.py` validates `Cf-Access-Jwt-Assertion` via PyJWKClient against the team JWKS; falls back to `Authorization: Bearer` for API use. Controlled by `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `ADMIN_EMAILS` env vars.

### Fixed
- **Pro photo variants**: generate 3 fal.ai variants in parallel so the request finishes within gunicorn's 120s timeout; clearer client error when API fails or returns empty.
- **PostHog `identify` crash** (`t.push is not a function`): defer `identify` to PostHog `loaded` callback instead of calling synchronously after `init`.
- **CSP**: allow `connect-src` to `https://eu-assets.i.posthog.com` (PostHog asset source maps).
- **Stripe webhook secrets for two domains**: `/stripe/webhook` now accepts host-specific/fallback secrets (`STRIPE_WEBHOOK_SECRET_EUR`, `STRIPE_WEBHOOK_SECRET_TRY`, `STRIPE_WEBHOOK_SECRET`) so separate Stripe webhook endpoints for `easylisting.app` and `kolaylistele.com` can both verify correctly.
- **Stripe webhook parsing** (`/stripe/webhook`): only `SignatureVerificationError` is treated as "bad signature". Other parse errors after a valid signature (e.g. Stripe SDK `event.object` check) fall back to `json.loads` so valid events are never silently dropped as 400.
- **E2E test** (`scripts/test_stripe_e2e.py`): `build_webhook` now injects `"id"` and `"object": "event"` top-level fields required by the Stripe SDK; script auto-loads `.env.test` so `STRIPE_WEBHOOK_SECRET` is always consistent between the test and the running app.

### Security
- `CF_ACCESS_TEAM_DOMAIN=aged-term-c87a.cloudflareaccess.com` set on both Railway services — browser JWT path now fully active.

---

## [1.6] — 2026-06-12 — Security hardening, PostHog analytics, App Store review account, durable billing

### Added
- **PostHog EU analytics**: client-side pageview/autocapture/pageleave and server-side `plan_upgraded` / `plan_cancelled` events. Enabled via `POSTHOG_TOKEN` env var. Renders nothing if unset.
- **App Store review account**: `APPSTORE_REVIEW_EMAIL` env var — submitting that email via magic link skips email delivery and grants instant Pro access. Used by App Store reviewers.
- **Durable billing state**: Stripe webhook now persists `plan` field on every `checkout.session.completed` event. `/api/status` returns the current plan name alongside premium flag.

### Security
- Admin endpoints hardened following OWASP ZAP audit: `/admin/*` now requires `Authorization: Bearer` header, returns 404 (not 401) on mismatch.
- Added `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp` response headers.
- Removed `Access-Control-Allow-Origin: *` default that was leaking on some API responses.

### Fixed
- CSP nonce now correctly applied to PostHog inline init script.
- Mobile magic-link handoff page now renders a proper HTTPS page (instead of redirecting directly) so email clients can follow the link before the app deeplink fires.

---

## [1.5] — 2026-06-11 — Native iOS app, backend mobile API, project reorganisation, fastlane

### Added
- **Native iOS app** (Swift/Xcode) under `mobile/ios/`. Full listing generation, Etsy connection, upgrade flow.
- **Mobile backend API surface**:
  - `POST /auth/mobile/guest` — create guest mobile token (rate: 5/hr, 10/day)
  - `POST /auth/mobile/logout` — invalidate token
  - `GET /api/csrf-token` — returns CSRF token for mobile POST requests
  - `GET /api/listings` — JSON listing list
  - `POST /api/stripe/checkout` — mobile-aware checkout session
  - `GET /upgrade/mobile/return` — deeplink handler after Stripe (`easylisting://upgrade/success?plan=…`)
- **Mobile token auth**: `X-Mobile-Token` header on all app requests; Etsy tokens auto-refresh when within 2 minutes of expiry.
- **Etsy OAuth mobile path**: `GET /auth/start?mobile=1` → after callback creates mobile_token and deeplinks `easylisting://auth?mobile_token=…`
- **Magic link mobile path**: `GET /auth/magic?token=…&m=1` renders HTTPS handoff page before deeplink.
- **`mobile_tokens` DB table**: stores token, shop_id, Etsy access/refresh tokens, expiry, guest/email flags.
- **fastlane**: automated App Store screenshots and metadata upload via `fastlane snapshot`.
- **Image upload background thread**: Etsy image uploads and personalization now run in a background thread so `/api/publish` returns immediately and avoids Railway's 120s timeout.

### Changed
- Project reorganised into `web/`, `mobile/`, `scripts/` subdirectories. `Procfile` and `railway.json` updated to `--chdir web`.
- Professional UI overhaul for iOS app (colours, typography, spacing).

### Fixed
- Removed hardcoded `readiness_state_id` from Etsy listing creation payload (caused publish failures for some shops).
- fastlane lanes split into `upload_screenshots` + `upload_metadata` to avoid partial failures.

---

## [1.4] — 2026-06-10 — Test suite expansion, upgrade flow fixes, navigation audit

### Added
- **47 new tests**: Stripe checkout, email flow, admin endpoints, photo generation, end-to-end guest → upgrade cycles. Total: 216 tests.
- **Integration tests** (`-m integration`): live Gemini, FAL, Resend, and Stripe smoke tests.
- `/health` endpoint now returns Stripe config and domain info for Railway diagnostics.

### Fixed
- Missing CSRF token on Stripe checkout `fetch()` in `upgrade.html`.
- Upgrade page "Back" button now uses `history.back()` instead of hardcoded `/`.
- Upgrade buttons now visible when any Stripe price ID is configured (EUR or TRY), not requiring both.
- `GUEST_ID` typo in magic link usage migration — usage was not being transferred correctly.
- All navigation and crash bugs found in full UI audit resolved.

---

## [1.3] — 2026-06-08 — AI reliability, auth UX, admin stats fixes

### Added
- **Auto-login returning email users**: if an email is already in `verified_emails`, magic link request auto-logs them in without sending a new email.

### Changed
- AI provider order changed to Gemini-first (was NVIDIA-first). NVIDIA kept as free fallback.
- Provider fallback now triggers on **any** error (not just quota errors) — prevents 500 responses when a provider returns malformed JSON or times out.

### Fixed
- NVIDIA timeout and empty-response handling — gracefully falls through to next provider.
- SQLite `database is locked` error on multi-worker Gunicorn startup (WAL mode init race condition).
- Email modal: already-verified users are sent directly to the app; users who hit the free limit see the paywall instead of the email form.
- Usage correctly migrated to email shop on both auto-login and magic link click paths.
- Admin stats exclude owner test emails from funnel metrics; raw table row counts added for diagnostics.
- CSP and session reinit issues from previously unmerged branches resolved.

---

## [1.2] — 2026-06-07 — Full Trendyol integration, GDPR marketing opt-in

### Added
- **Trendyol integration** (full):
  - `POST /api/trendyol/connect` — validate and store supplier credentials
  - `POST /api/trendyol/disconnect`
  - `GET /api/trendyol/categories` — full category tree (cached 24h per supplier)
  - `GET /api/trendyol/categories/<id>/attributes`
  - `GET /api/trendyol/brands`
  - `GET /api/trendyol/addresses`
  - `POST /api/trendyol/publish` — create product on Trendyol
  - `GET /uploads/<filename>` — static image hosting for Trendyol CDN ingestion
  - `platform_credentials` DB table for storing Trendyol API keys
- **GDPR marketing opt-in**: `marketing_consents` table; consent captured at magic link time with locale, unsubscribe token, and `GET /unsubscribe` handler.
- **Admin stats dashboard** (`GET /admin/stats`): email funnel (sent/verified/rate), marketing list by locale, raw table counts, shops list with plan info.
- DB path and size shown on admin stats for Railway diagnostics.

---

## [1.1] — 2026-06-06 — Email-verified guest subscriptions, legal, CI

### Added
- Email-verified guest users can now subscribe to paid plans (Stripe checkout works without an Etsy connection).
- `fp_sessions` DB table: maps fingerprint IDs to email shop IDs for session restoration after cache clears.

### Changed
- OAuth redirect URI is now derived dynamically from `request.host` — no hardcoded domain needed.

### Fixed
- Domain-specific language preference stored correctly (was shared across domains).
- Etsy API header corrected: only keystring sent in `x-api-key` (not full `key:secret`).

### Legal
- Required Etsy API trademark notices and Terms of Service disclaimer added to UI and privacy policy per Etsy developer requirements.

---

## [1.0] — 2026-06-04 (late) — Security audit, AI provider chain, test suite

### Added
- **AI provider fallback chain**: Gemini 2.5 Flash → Gemini 2.5 Flash Lite → NVIDIA NIM → OpenAI GPT-4o. Skips providers with no API key.
- **Comprehensive security audit fixes**: input sanitisation, session handling, header hardening, CSRF exemption corrections.
- **97 tests** passing; CI pipeline configured.

### Changed
- Gemini set as primary AI provider; NVIDIA as free fallback; OpenAI gated behind `ALLOW_PAID_OPENAI=true`.

---

## [0.8] — 2026-06-04 — Magic link polish, Resend email, tab auto-continue

### Added
- **Resend API** replaces Natro SMTP for transactional email. Env var: `RESEND_API_KEY`.
- Magic link flow re-enabled after Resend migration.
- After clicking a magic link, the original browser tab automatically continues to the app (no manual navigation needed).
- Professional bilingual (TR/EN) HTML email template for magic link: preheader, branded logo, CTA button, fallback plain-text link, 15-minute expiry notice.

### Fixed
- Email verification loop bug — users were being asked to verify again on refresh.
- Already-verified guard prevents duplicate sessions.
- Magic link now opens the verified page on first click, then auto-loads the template.

---

## [0.7] — 2026-06-04 — Magic link email authentication

### Added
- **Magic link auth** (`POST /api/magic-link`, `GET /auth/magic`): guest users can verify their email address to get a persistent identity across browsers and devices.
- `magic_links` DB table: one-time tokens, 15-minute expiry, single-use enforcement.
- `verified_emails` DB table: permanent email hash → shop ID mapping.
- Email addresses stored as SHA-256 hashes only — no plaintext.
- Initial SMTP email helper (`send_email`), Natro `mail.kurumsaleposta.com:465` configuration.

---

## [0.6] — 2026-06-04 — Abuse tracking, TRY pricing, incognito-resistant guest limits

### Added
- **Abuse tracking**: `abuse_signals` DB table; events: `new_guest`, `limit_hit`, `fp_conflict`.
- `GET /admin/abuse` endpoint: HTML dashboard showing event counts, top IPs, top fingerprints.
- **TRY pricing**: separate Stripe price IDs for `kolaylistele.com`; domain detected at runtime via `request.host`.
- **Incognito-resistant guest limits**: browser fingerprint ID (`guest_fp_*`) persists usage across incognito tabs within the same browser.

### Fixed
- Race condition in fingerprint registration that could reset usage counter to 0.
- Fingerprint conflict detection: logs `fp_conflict` when a known FP arrives with a fresh random ID.

---

## [0.5] — 2026-06-04 — Internationalisation, security hardening, test suite

### Added
- **i18n**: all sidebar tool sections translated to Turkish (TR) and German (DE).
- **Probe path blocker**: `block_probe_paths()` before_request hook returns 404 for common scanner paths (`.git`, `.env`, `/wp-admin`, etc.).
- Initial test suite added.

### Fixed
- `datetime.utcnow()` deprecation replaced with timezone-aware calls.

---

## [0.4] — 2026-06-04 — Monetisation, guest mode, mobile responsiveness

### Added
- **Guest mode** (`GET /guest`): use the app without an Etsy account; random ID stored in session + 180-day signed cookie.
- **Browser fingerprint** (`POST /api/fingerprint`): derived guest ID survives incognito mode.
- **Monetisation**: Starter and Pro plans; free tier capped at 3 generations/month and 1 improve/month.
- **Bulk generate** (`GET /bulk`, `POST /api/bulk-generate`): premium feature for processing multiple products at once.
- **Premium image generation** (`POST /api/generate-photos`): FLUX.1 Kontext via fal.ai, 3 variants per image, 30/month cap for Pro.
- Free limit and bulk access protection middleware.
- Full mobile responsiveness across all pages.
- Manual Etsy field hints (seller can override AI-suggested taxonomy, shipping profile, etc.).

### Changed
- Monetisation: plan gating applied to bulk generate and improve-listing endpoints.

---

## [0.3] — 2026-06-04 — UI fixes, legal pages, favicon

### Added
- Privacy policy (`GET /privacy`) and Terms of Service (`GET /terms`) pages.
- Favicon and Apple touch icon.

### Fixed
- CSP violations from inline event handlers.
- Listing URL format, language selector, publish toast notification.
- Form early-return bug that prevented submission in some browsers.
- Coming-soon platform placeholders cleaned up; AI provider selector removed from UI.

---

## [0.2] — 2026-06-01 — Deployment & CDN

### Added
- Cloudflare Workers configuration (later removed in favour of Cloudflare proxy only).
- Gunicorn bound to Railway `$PORT` environment variable.

### Removed
- Wrangler config removed; Cloudflare Workers approach abandoned in favour of standard Cloudflare proxy.

---

## [0.1] — 2026-06-01 — Initial release

### Added
- Flask application with Etsy OAuth 2.0 + PKCE authentication.
- AI listing generation from uploaded product images: title, description, tags, materials, price suggestion.
- Multi-provider AI: Google Gemini, NVIDIA NIM, OpenAI GPT-4o.
- Platform support: Etsy, Shopify, Amazon, eBay, WooCommerce, Pinterest, Trendyol, Hepsiburada, n11.
- Etsy listing publish (`POST /api/publish`) — creates draft with images.
- Shop style templates (`GET|POST /api/template`): save brand tone, materials, shipping defaults.
- Listing improve (`POST /api/improve-listing`): title SEO, description rewrite, tag generation, shorten.
- Listing variants (`POST /api/listing-variants`): 3 SEO/emotional/gift-focused variants.
- Translation (`POST /api/translate`): DE / TR / EN.
- Etsy taxonomy search (`GET /api/taxonomy`).
- Stripe subscription payments: Starter + Pro plans, monthly + annual, EUR pricing.
- Stripe webhook handler: `checkout.session.completed`, `subscription.deleted`, `subscription.paused`.
- Flask-Limiter rate limiting, Flask-WTF CSRF protection.
- Security headers: CSP with per-request nonce, HSTS, X-Frame-Options, Permissions-Policy.
- SQLite database with WAL mode; Railway Volume persistence.
- Two-domain setup: `kolaylistele.com` (TRY) and `easylisting.app` (EUR).
- `/health` endpoint for Railway health checks.
- `GET /admin/abuse` abuse monitoring endpoint (Bearer token auth).

---

<!-- AGENTS: When you implement a feature, fix a bug, or make any notable change, add an entry under the appropriate version below this line. If no unreleased section exists yet, create one. Follow the format above exactly: version header, date, one-line summary, then grouped Added / Changed / Fixed / Security / Removed bullets. Be specific — name the endpoint, table, env var, or component affected. -->

## [Unreleased]

### Fixed
- Gemini API calls in `_gemini_generate` and `_run_text_json` now set `thinking_budget=0`, `AutomaticFunctionCallingConfig(disable=True)`, and `http_options=HttpOptions(timeout=90_000)` (90 000 ms = 90 s). Prevents thinking mode from silently adding cost with no quality benefit for JSON generation, and ensures all calls time out before Railway's 120 s worker limit.
- Recreated `.venv` with Python 3.11 (was pointing to a non-existent `etsy/` path after project rename). Both `web/core/config.py` and `web/app.py` now search for `.env` up the directory tree, so `flask run` from `web/` picks up the repo-root `.env` automatically.
