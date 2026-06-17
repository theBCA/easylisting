# Changelog

All notable changes to EasyListing / kolaylistele are documented here.

Format: [version] — date — summary, then grouped Added / Changed / Fixed / Security / Removed entries.

---

## [Unreleased]

### Fixed
- **CSP blocking all inline JS on upgrade page** (`web/app.py`): Cloudflare was injecting a second nonce into the `Content-Security-Policy` header alongside `'strict-dynamic'`, which caused it to also rewrite `nonce=` attributes in the HTML — so our scripts had a mismatched nonce and were blocked entirely (setLang never ran, page stayed in English). Replaced `'strict-dynamic'` with `'unsafe-inline'` (ignored by nonce-supporting browsers, but prevents Cloudflare's nonce rewriter from triggering). Also added PostHog (`eu.i.posthog.com`, `eu-assets.i.posthog.com`) and `stats.g.doubleclick.net` to `connect-src` to stop console noise from those blocked fetches.
- **Branding/language flash on kolaylistele.com across all pages** (`templates/index.html`, `bulk.html`, `connect.html`, `listings.html`, `photo_set.html`, `privacy.html`, `terms.html`, `upgrade.html`): `<html lang>`, `<title>`, nav logo, and active lang button were hardcoded in English/easylisting on every template and only corrected by JS after load — visible flash on first render. Now all set server-side via `use_try` Jinja variable (global context processor). `privacy.html` and `terms.html` nav logos were hardcoded "KolayListele" (wrong on easylisting.app); now conditional on both.

### Added
- **GA4 Consent Mode v2 + cookie banner** (`templates/_gtag.html`, `templates/_consent_banner.html`, all public templates): `_gtag.html` now sets `analytics_storage: denied` by default and grants it only if the user previously accepted. A cookie consent banner (EN/DE/TR, matches app style) is included before `</body>` on all 8 public pages, updates consent via `gtag('consent','update',...)` on accept/decline, and stores the choice in `localStorage`. Required for EEA/GDPR compliance with GA4.
- **Google Analytics (GA4) tag** (`templates/_gtag.html`, all public templates): added `G-W32V01W3L2` gtag.js snippet as a shared partial included immediately after `<head>` in all 11 public-facing templates. Admin dashboard excluded.

### Fixed
- **Admin dashboard sticky table header overlap** (`templates/admin_dashboard.html`): the sticky `th` had no `z-index`, so it created no stacking context and the later-in-DOM `<tbody>` rows painted on top of it while scrolling (visible overlap in "Shops & Plans"). Added `z-index:10` — above the body rows, still below the `.admin-bar` (`z-index:50`).
- **Untranslated value-prop stats on `/upgrade`** (`templates/upgrade.html`): the three stat labels (`t-stat1/2/3`) and value description (`t-value-desc`) were hardcoded in Turkish and never updated by `setLang()`, so they stayed Turkish under EN/DE. Added `stat1/stat2/stat3/valueDesc` keys to the EN/DE/TR `T` dictionaries, wired them into `setLang()`, and changed the static HTML defaults to English.
- **Wrong favicon on easylisting.app** (`static/favicon-e.svg`, `app.py`, all templates): JS-based favicon swapping was unreliable — browsers cache and lock the icon before JS fires. Moved to server-side rendering: added `use_try` to the global `inject_security` context processor in `app.py`, then replaced every template's static `<link rel="icon">` with a Jinja conditional (`{% if use_try %}favicon.svg{% else %}favicon-e.svg{% endif %}`). Removed the JS swap from `security.js`.
- **AI Photo Studio nav logo showed "Tekil İlan" on TR** (`templates/photo_set.html`): the brand wordmark and the "Single Listing" nav link were both `<a href="/">`, so `applyLang()`'s `querySelector('a[href="/"]')` matched the logo first and overwrote it with the translated nav-single label (while the real Single Listing link stayed untranslated). Gave the link its own `id="navSingle"` and target it by id.
- **AI Photo Studio `<h1>` never translated** (`templates/photo_set.html`): the `page_title` string existed for EN/DE/TR but `applyLang()` never applied it to the heading, so it stayed "AI Photo Studio" under DE/TR. Wrapped the heading text in `<span id="pageTitle">` and set it (plus `document.title`) in `applyLang()`.
- **Nav logo wordmark now domain-based, not language-based** (`templates/index.html`, `bulk.html`, `connect.html`, `listings.html`, `photo_set.html`, `upgrade.html`): the logo was set from `BRAND[lang]`, so switching language to e.g. EN on kolaylistele.com flipped the wordmark to "easylisting". The logo (and connect.html's "EL"/"KL" logo-mark, and photo_set's tab-title brand suffix) now picks the wordmark by hostname — always "kolaylistele" on kolaylistele.com and "easylisting" on easylisting.app, on every page, regardless of the selected content language.

### Changed
- **Browser tab titles lowercased to wordmark** (`templates/index.html`, `bulk.html`, `connect.html`, `listings.html`, `photo_set.html`, `upgrade.html`, `privacy.html`, `terms.html`): `<title>` tags and the JS/i18n title strings now use the lowercase wordmark ("easylisting", "kolaylistele") to match the nav logo. Footers and legal prose keep the proper-noun "EasyListing".
- **Image model upgrade** (`core/config.py`, `apis/photos.py`): switched default `GEMINI_IMAGE_MODEL` from `gemini-2.5-flash-image` to `gemini-3.1-flash-image` — same pricing (~$0.039/image), better quality in side-by-side testing. Cost log estimate updated to `$0.04/image` as a conservative rounding. Overridable via `GEMINI_IMAGE_MODEL` env var.
- **Etsy Photo Set Shot 4 replaced** (`apis/photos.py`, `templates/photo_set.html`): "Back / Construction Shot" replaced with "Size Reference Shot" — product held in hand or beside a common object to communicate real-world dimensions. More universally useful across product categories (e.g. ceramic plates, jewellery, textiles) than a back/construction view.
- **UI naming refresh (Phase 1 of premium UI pass)**: renamed two features across nav links, page titles, `<h1>`s, and EN/DE/TR i18n dictionaries in `templates/index.html`, `photo_set.html`, `bulk.html`, and `upgrade.html`:
  - "Photo Set" → **AI Photo Studio** (DE: KI-Fotostudio, TR: Yapay Zeka Foto Stüdyosu). The `photo_set.html` `<h1>` previously read "Etsy Photo Set Generator", contradicting the nav — now consistent. Routes (`/photo-set`) unchanged; "photo set" retained where it describes the actual 6-shot output.
  - "Bulk Upload" → **Bulk Listings** (DE: Massen-Listings, TR: Toplu İlanlar). Route (`/bulk`) unchanged.
- **Shared base stylesheet** (`static/app.css`, premium UI pass Phase 2): single source of truth for design tokens (`:root`), the `body`/`*` reset, and the base font, linked from all 8 templates. Replaces the `:root` block that was duplicated — and had drifted — across every template (shadows `.08` vs `.09`; `photo_set` referenced an undefined `--red`). Unified to one soft `--shadow: 0 2px 16px rgba(91,71,224,.08)` and defines all status-color aliases (`--green-bg`/`--green-light`, `--red`/`--red-light`, `--amber`/`--amber-light`) so no template breaks.
- **Font swap to Plus Jakarta Sans** (Phase 3, all templates): replaced Inter with Plus Jakarta Sans (Google Fonts, weights 400–800). Chosen over Geist because Geist on Google Fonts ships no `latin-ext` subset — it would break Turkish glyphs (ş ğ ı İ) on kolaylistele.com. Plus Jakarta Sans's served `unicode-range` blocks cover Turkish (U+0100–017F, U+0131) and German (ä ö ü ß), verified before switching.
- **Modernized primary CTA buttons** (Phase 5: `btn-generate`/`btn-publish` in `index.html`, `btn-plan.cta`/`.cta-green` in `upgrade.html`, `btn-connect` in `connect.html`): replaced the dated `linear-gradient` + heavy `rgba(...,.35)` glow with a flat solid fill + subtle lift-on-hover (`translateY(-1px)` + soft shadow), darkening on hover.
- **AI Photo Studio header icon** (Phase 5, `templates/photo_set.html`): replaced the `📸` emoji in the page `<h1>` with an inline Lucide camera SVG in the brand color.
- **Nav wordmark lowercased** (all templates, static + JS `BRAND` map): "EasyListing" → "easylisting" in the top-left logo only, matching the lowercase "kolaylistele" treatment. Page titles, footers, and legal prose keep proper-noun "EasyListing". The Phase-4 SVG logo mark was reverted per request (favicon restored to the original "K" tile, `_logo.html` removed).
- **Turkish "YZ" → "yapay zeka"** (`index.html`, `connect.html`, `upgrade.html`): spelled out the AI abbreviation in all Turkish copy (14 strings) for clarity/trust; English "AI" and German "KI" unchanged.
- **Admin dashboard UI overhaul** (`templates/admin_dashboard.html`): adopted `app.css` + Plus Jakarta Sans (was system font), added a sticky top bar with section jump-nav (Overview/Billing/AI Spend/Usage/Abuse/Shops/Emails/Feedback), accent stat cards, hover rows, sticky table headers, and soft shadows.

### Added
- **Admin dashboard: Abuse &amp; Tracing section** (`apis/admin.py` + `templates/admin_dashboard.html`): surfaces `db.get_abuse_summary(7)` — signal-event counts, top IPs by distinct guest count, and top fingerprints by distinct guests — for multi-account/abuse tracing. Data was already collected but not shown on the dashboard.
- **Live generation progress** (`templates/index.html`): the listing-generation loading screen now shows the user's uploaded photo with an animated scan line and an honest step checklist. The old `animateSteps()` marked all 4 steps "done" on a 3.6s timer regardless of the real request (which often runs 10–30s); now steps advance on a realistic cadence but the final step stays "in progress" (spinner) until the `/api/generate` response actually arrives (`finishSteps()`), and timers are cleared on every error path. Frontend-only — no backend change.
- **Recent generations history** (`templates/index.html`): client-side localStorage history (`el_recent_generations`, max 6, quota-safe trimming) of generated listings, shown as a thumbnail grid on the upload screen. Clicking a card restores the full result via `populateReview()`/`populatePlatformReview()` so closing the tab no longer loses work. EN/DE/TR. Guest-friendly, no backend/DB change.
- **Inline error state with retry** (`templates/index.html`): generation failures now show a dedicated error screen (Lucide alert icon, the actual error detail, "Try again" + "Back to upload") instead of a toast that bounces the user back to upload. "Try again" re-runs the last generation since `selectedFiles` is still in memory. EN/DE/TR.
- **Admin dashboard: date-range toggle** (`apis/admin.py` + `templates/admin_dashboard.html`): `?days=7|30|90` (default 30, clamped) drives `get_payment_summary`, `get_ai_cost_summary`, and `get_abuse_summary`; a 7d/30d/90d segmented control in the sticky bar switches the range and section labels reflect it.

### Changed
- **Mobile nav no longer hides paid features** (`templates/index.html`): the `@media (max-width:600px)` rules had `display:none` on the Bulk and AI Photo Studio nav links, making two paid features unreachable on phones. They now render as compact pills (the nav already wraps to a second row on mobile). Conversion-bug fix.
- **Emoji → Lucide SVG icons** (`index.html`, `photo_set.html`, `bulk.html`, `listings.html`): replaced 9 emoji-as-UI-icons in static markup with inline Lucide SVGs (nav shop icon → shopping-bag; platform cards Etsy/Shopify/Amazon/WooCommerce → shopping-bag/cart/package/globe; upload-zone → image/package; export → download; colors → palette; primary-color → tag). Country flags and pricing-list ✓/✗ kept intentionally. Emoji that are injected at runtime from JS/i18n dictionaries were left as-is (a static swap there would be overwritten on load).
- **Listings image fade-in** (`templates/listings.html`): listing card images fade in on load (`.card-img img` opacity transition + `.loaded` class via a nonce'd listener, with `complete`/`error` fallbacks) instead of popping in. (The page is server-rendered, so true skeleton loaders didn't apply — this addresses the same pop-in jank.)

### Fixed
- **Admin dashboard crash** (`db.get_ai_cost_summary`): `by_model` query aliased cost column as `cost` but template expected `cost_usd` — renamed alias to match.
- **Photo set page nav logo** (`templates/photo_set.html`): nav logo was hardcoded to "EasyListing"; now uses the same BRAND map as other pages — kolaylistele.com shows `kolaylistele`, EN/DE show `EasyListing`.

### Added
- **AI cost tracking** (`db.ai_cost_log`, `db.log_ai_cost`): new table logs every AI generation call with provider, model, token counts, estimated cost in USD, and endpoint name. `core/ai._gemini_generate` captures `usage_metadata` from the Gemini response; `_openai_generate` captures `resp.usage`; NVIDIA logs zero cost (free tier). Photo generation in `apis/photos.py` logs fixed per-image pricing ($0.039/img via Gemini image model). All logging is fire-and-forget in a background thread — no latency added to requests.
- **Admin dashboard: AI spend section** (`/admin/dashboard`): shows total cost and call count for last 30 days, plus per-model and per-endpoint breakdowns, sourced from `ai_cost_log`.
- **Admin dashboard: monthly usage totals**: listing-generated, improvements, and photos-generated counts aggregated across all shops for the current billing period via `db.get_usage_summary()`.
- **Admin dashboard: email list** (`db.get_admin_email_list`): real subscriber emails from `marketing_consents` with locale, source, and subscription status.
- **Admin dashboard: feedback inbox** (`db.get_admin_feedback`): last 50 messages from the feedback widget, with shop ID, reply-to email, page URL, and timestamp.
- **Contact/feedback widget** (`templates/_feedback_widget.html`): floating 💬 button on every page, CSP-compliant (nonce on script block, no inline handlers), EN/DE/TR i18n. POSTs to `POST /api/feedback` (`apis/pages.py`), stores in `db.feedback` table, and emails `berkcemarslan@gmail.com` in a background thread.
- **Photo limit alert emails** (`apis/photos.py` `_alert_limit_hit`): fire-and-forget email to owner when any shop exhausts their monthly photo quota — fires on both `/api/generate-photos` and `/api/etsy-photo-set`.
- **Pro photo limit raised** (`core/config.py` `PLAN_LIMITS`): Pro `photo_images` increased from 30 → 60 (10 full sets/month at ~$2.34 AI cost vs €9.99 revenue).
- **Per-image download buttons** (`templates/photo_set.html`): each generated shot now has its own download button in addition to the collage download.
- **"Upload to Etsy Listing" feature** (`POST /api/upload-photos-to-listing`, `GET /api/shop-listings`): after generating shots, users can pick an existing Etsy listing and upload selected photos directly to it. Upload panel with listing picker and per-shot checkboxes.
- **"Create Listing →" button** (`templates/photo_set.html`): appears after first shot is generated, links to the listing generator.

### Changed
- **Admin dashboard shop table**: added `listing_used`, `improve_used`, `photo_variant_used` columns so usage is visible per shop without drilling into the DB.
- **Provider names hidden from consumers** (`templates/upgrade.html`, `bulk.html`, `index.html`, `privacy.html`): all mentions of "Gemini", "NVIDIA", "OpenAI", "fal.ai" removed from user-facing UI and replaced with "AI-powered" / "KI-gestützt" / "YZ destekli". Provider dropdown removed from bulk upload.
- **Photo-set page i18n and CSP** (`templates/photo_set.html`): all hardcoded English strings translated to DE/TR via `PAGE_STRINGS`; inline `onclick=""` handlers replaced with `addEventListener` (CSP compliance); PostHog include removed.

### Fixed
- **CSP blocking photo-set buttons**: inline `onclick=""` attributes on Generate/Next/Download buttons were blocked by `script-src` nonce policy — converted all to `addEventListener` in a nonce-guarded `<script>` block.
- **Language not preserved on photo-set page**: page now reads the same `localStorage` key as `index.html` and applies translations on init.

### Added
- **Etsy Photo Set Generator** (`/photo-set`, premium): upload one product photo → AI generates 6 professional Etsy listing shots one at a time (worn/in-use, studio, macro detail, back/construction, lifestyle flat lay, packaging). Step wizard UI with sidebar progress, inline thumbnails, per-image download, a client-side 3×2 collage download, and a live "photo credits left this month" pill with an upgrade nudge when a Starter user runs out. Backend: `POST /api/etsy-photo-set` in `apis/photos.py` — Gemini vision auto-describes the product on shot 1, then Gemini 2.5 Flash Image generates each shot with Etsy-specific prompts. Linked from the main nav for all paid plans. Uses the per-plan photo-image monthly quota (1 credit per shot).
- **Per-plan limits config** (`core/config.py` `PLAN_LIMITS` + `plan_limit()`): central, env-overridable monthly caps for `listings`, `improve`, `photo_images`, and `bulk_batch` per plan (free / starter / pro). Defaults: Starter 100 listings + 50 improves + 1 photo set (6 images) + 10 bulk/batch; Pro 800 listings + 200 improves + 5 photo sets (30 images) + 20 bulk/batch. Starter listings match the advertised "100/month"; Pro gives 8× the listings for 2× the price and 5× the photo sets. The Starter photo set is a taster to drive Pro upgrades. Sized so worst-case usage stays profitable (~64% margin on Pro, ~88% on Starter). Every value overridable via `LIMIT_*` env vars per Railway service.
- **Standalone image-gen smoke test** (`scripts/test_image_gen.py`): verify Gemini image generation (single shot or full 6-shot set) against a local photo before deploying, with cost estimate and saved output files.
- **`flag-turkish` step in `scripts/lead_pipeline.py`**: heuristically flags scraped Etsy shops likely run from Turkey (for kolaylistele.com outreach) by checking `.tr` website/email domains, Turkish-specific characters, `+90` phone numbers, and Turkish keywords in the Instagram bio. `enrich-instagram` now also stores the Instagram bio text (`instagram_bio` column) so this step needs no extra API calls. Adds `instagram_bio` and `turkish_signal` to the leads CSV schema.
- **`flag-turkish` heuristic widened**: now also scans `shop_url`, checks a new `shop_location` column against Turkey-equivalent values, detects the 🇹🇷 flag emoji, and matches a much larger keyword/city list (Türkiye/Turkey city names, "made in turkey", common Turkish shipping/order/handmade phrases). `shop_location` is added to the leads CSV schema (not yet populated by any scrape step — reserved for future enrichment).
- **Lead pipeline expanded to 1098 shops**: ran `apify-scrape` for 5 more Turkish-leaning niches (evil eye bracelet, turkish ceramic plate, leather sandals handmade, gold filled jewelry, copper handmade gift) via `crawlerbros/etsy-scraper`, raw dumps saved as `scripts/data/raw_etsy_*.json`. Guessed Instagram handles for the 366 new shops with neither a handle nor email, in 37 chunks of 10 with a 90s per-chunk timeout and incremental CSV writes (see Fixed below) — found 5 more emails. Re-ran `flag-turkish` (10/1098 flagged, up from 6) and `export` (21 unique emails, up from 18). Rebuilt `leads_kolaylistele_tr.csv` (7 contactable Turkish-flagged shops) and `leads_easylisting_en.csv` (547 contactable shops).

### Added
- **`shops.last_seen` and `shops.country` columns** (`db.py` `init_db`): `increment_usage`/`increment_improve_usage` now take an optional `country` arg and update `last_seen` (always) and `country` (from the `CF-IPCountry` header, set automatically by Cloudflare) on every listing generation/improve/bulk/translate call. Wired from `apis/listings.py` and `apis/translate.py`.

### Changed
- **Admin dashboard (`/admin/dashboard`)**: accounts table now shows per-shop monthly `listing_used`/`improve_used` counts, the Stripe `stripe_subscription_id` (or `—` if none), `country` (from `CF-IPCountry`), and `last_seen` date — for quicker support lookups and seeing where accounts are active from.
- **Image generation moved from fal.ai → Gemini 2.5 Flash Image** (`gemini-2.5-flash-image`, env `GEMINI_IMAGE_MODEL`): photo variants and the new photo set now use the google-genai SDK we already use for text — drops the fal.ai vendor/`FAL_KEY` dependency and consolidates billing. Comparable cost (~$0.039/img) on a key we already manage.
- **Listing-generation fallback model `gpt-4o` → `gpt-4o-mini`** (`core/ai._openai_generate`, env `OPENAI_VISION_MODEL`): ~30× cheaper for the paid last-resort vision path; Gemini/NVIDIA remain the primary free providers.
- **Paid plans now have monthly generation caps** (`db.can_generate` / `can_improve`): previously returned `999` (effectively unlimited) for any premium user — a runaway script could rack up unbounded API cost. Now enforced against `PLAN_LIMITS`. New `shops` columns `listing_used`/`listing_period` and `improve_used`/`improve_period` track monthly usage with automatic period rollover. The main page still shows "Unlimited" to premium users (the cap is an abuse backstop, not a UX-facing quota).
- **`can_generate_photo_variants`** now derives its monthly cap from the shop's plan via `PLAN_LIMITS` (any premium plan with a non-zero `photo_images` allowance) instead of hardcoding pro + 30.
- **Photo features opened to all paid plans** (`apis/photos.py`, `/photo-set` route): `/api/generate-photos`, `/api/etsy-photo-set`, and the photo-set page now gate on `has_premium_access()` instead of `has_pro_access()`. The `photo_images` cap does the real gating (free = 0 → blocked; Starter = 6; Pro = 30), so Starter users get a 1-set taster.
- **Upgrade page overhaul** (`templates/upgrade.html`): plan cards now show the real differences — AI photo sets as the headline Pro differentiator (Free ✗ / Starter 1 / Pro 5), plan-accurate bulk batch sizes (Starter 10 / Pro 20), and corrected the Free listing count (3, was mislabeled 5). Fixed a latent bug where feature bullets were never localized — DE/TR now show translated bullets.
- **Bulk upload per-batch cap** (`templates/bulk.html`, `/bulk` route): photos per bulk run are now limited by plan (`bulk_batch`: Starter 10, Pro 20) instead of a hardcoded 20; the drop-zone copy and limit toast reflect the plan's number. Bulk listings still count against the monthly `listings` cap.
- **Claude Code workflow config**: `.mcp.json` adds GitHub, Stripe (restricted key), and SQLite MCPs alongside existing context7. `.claude/settings.json` pre-approves safe Bash patterns (pytest, git, grep, ls) and blocks destructive commands (rm -rf, force-push, sudo). `.claude/settings.local.json` (gitignored) holds per-developer MCP secrets.
- **Path-scoped rules** (`.claude/rules/`): `stripe-payments.md`, `api-routes.md`, `database.md` — load only when Claude reads matching files, keeping main context lean.
- **Custom skills**: `/billing-audit` (Stripe event coverage + DB consistency check with live snapshots), `/pre-deploy` (tests + changelog + import cycle + config checks before pushing).
- **CLAUDE.md gotchas section**: 10 project-specific mistakes with explanations — import cycles, `safe_error()` misuse, `set_premium` cancellation behaviour, webhook signature logic, Railway timeout constraints, two-DB architecture.
- **Plan switching** (`POST /billing/change-plan`): existing subscribers can switch between Starter and Pro without creating a new subscription. Upgrades (Starter→Pro) are prorated and charged immediately; downgrades (Pro→Starter) apply a credit on the next invoice. Upgrade page shows "Switch to Pro →" / "Switch to Starter" buttons for active subscribers.

### Changed
- **Lead pipeline data expanded to 757 shops**: ran `apify-scrape` for 5 more niches (custom tumbler, custom t-shirt, evil eye jewelry, mosaic lamp, turkish towel) using a second Apify account (`APIFY_TOKEN2`) after the first account hit its monthly usage hard limit; ran `enrich-instagram --no-guess` and `enrich-website` on the existing 188 handles/websites, then `flag-turkish` + `export`. `scripts/data/leads.csv` now has 757 rows, 188 contactable (18 with email, 5 Turkish-flagged), rebuilt into `leads_kolaylistele_tr.csv` (2 rows) and `leads_easylisting_en.csv` (186 rows).

### Fixed
- **`scripts/lead_pipeline.py` `enrich-instagram --guess` can hang for hours with no output on large batches**: running `--guess` against ~570 shops with no existing Instagram handle (so the script guesses a handle from `shop_name` for each) ran for 2.5+ hours with the `apify/instagram-profile-scraper` actor and never returned or errored; killed manually with no data written (output is only flushed at the very end of the command). Until `_apify_run`'s per-batch timeout/retry behavior is investigated, avoid `--guess` on large (500+) row sets — use `--no-guess` to enrich only rows that already have a real `instagram_handle`.
- **Worked around the `--guess` hang for 366 new-niche shops**: instead of `enrich-instagram --guess`, used a small one-off chunked script (10 handles/chunk, 90s timeout, CSV saved after every chunk) so a hang on any single chunk wouldn't lose progress — completed all 37 chunks successfully with no hang. If `--guess` is needed again at scale, consider porting this chunk size/timeout/incremental-save pattern into `cmd_enrich_instagram` itself.

### Changed
- **Lead pipeline (`scripts/lead_pipeline.py`)**: replaced dead `rupom888~etsy-scraper` actor with `crawlerbros~etsy-scraper` which returns `shopName`/`shopUrl` directly; updated `listings_to_shops` and `run_apify_etsy_scrape` to use the new actor's `startUrls`+`maxItems` input format. `APIFY_ETSY_ACTOR` env var overrides the default. `listings_to_shops` now captures `review_count` and `star_seller` per shop. New `enrich-instagram` command guesses Instagram handles from shop names, scrapes bios via `coderx~instagram-profile-scraper-bio-posts`, and extracts `external_url` + bio emails. New `--min-reviews` flag on `apify-scrape` filters to professional sellers (C strategy). New `--min-listing-count` flag on `enrich-instagram`.
- **Lead pipeline rebuilt as a single CSV-driven pipeline (`scripts/lead_pipeline.py`)**: 4 subcommands now share one `scripts/data/leads.csv` — `apify-scrape` (Etsy shops via `crawlerbros/etsy-scraper`, `--min-reviews` filter, `--country` must be one of US/DE/GB/FR/AU/CA/IT/ES/NL/JP per the actor's input schema, dumps raw Apify response to `scripts/data/raw_etsy_<ts>.json` for schema inspection since actor output fields vary), `enrich-instagram` (guesses IG handles from shop names, scrapes via `apify/instagram-profile-scraper`, pulls `publicEmail`/`businessEmail`/bio-email + non-Etsy `externalUrl`), new `enrich-website` (no Apify — plain `requests` crawl of the shop's/Instagram's linked website + common `/contact`/`/about` paths for `mailto:`/inline emails, filtering placeholder/asset/CDN false-positives like `*.etsystatic.com` and `image@2x.png`), and `export` (dedupes by email, writes `leads_export.csv`). Config via gitignored `scripts/leads.env` (template: `scripts/leads.example.env`), `APIFY_API_TOKEN` (or existing `APIFY_TOKEN`)/`APIFY_ETSY_ACTOR`/`APIFY_INSTAGRAM_ACTOR`/`APIFY_BATCH_SIZE`. Verified end-to-end against live Apify actors.

### Added
- **Structured request logging**: `core/logging.py` now configures production JSON logs, `LOG_LEVEL` / `LOG_FORMAT`, request IDs, access logs, exception context, and redaction helpers. Responses include `X-Request-ID`.
- **Billing funnel metrics**: Stripe checkout/session/webhook events are now stored in `payment_events`; `/admin/dashboard`, `/admin/stats`, and `/admin/billing-json` show checkout attempts, paid conversions, failures, cancellations, plan split, domain split, and recent billing events.
- **Daily SQLite backup**: `core/backup.py` daemon thread runs hourly, creates a hot `sqlite3.backup()` copy in `<volume>/backups/` once per 23-hour window. Keeps the 7 most-recent dated files. Starts automatically at app startup via `start_daily_backup()`.
- **Admin dashboard** (`/admin/dashboard`): searchable table of all accounts (type badge, plan, usage, created), inline plan selector with Save button wired to `/admin/set-plan`.
- **Cloudflare Access JWT verification**: `core/admin_auth.py` validates `Cf-Access-Jwt-Assertion` via PyJWKClient against the team JWKS; falls back to `Authorization: Bearer` for API use. Controlled by `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `ADMIN_EMAILS` env vars.

### Fixed
- **Generate page (`/`, `index.html`) language dropdown defaulted to Turkish on easylisting.app**: `#langSelect` listed `<option value="tr">` first, so the dropdown visually showed "Türkçe" on page load even though `currentLang` was correctly `en` on easylisting.app — the AI-generated listing language could silently end up Turkish for EN/DE users who didn't touch the dropdown. Reordered options (English first) and `setLang()` now syncs `#langSelect.value` to `currentLang` on load and on language switch.
- **`scripts/lead_pipeline.py` `apify-scrape` data-loss bug**: re-running `apify-scrape` with a different `--out`-merging query (e.g. the same niche with a different `--min-reviews`) re-matched shops already in `leads.csv` and overwrote their row entirely with blank `email`/`website`/`followers`/`instagram_bio`/`turkish_signal`, silently discarding prior `enrich-instagram`/`enrich-website`/`flag-turkish` results (lost 4 of 18 collected emails in one run). The merge now preserves existing enrichment fields and only refreshes scrape-derived fields (`shop_name`, `review_count`, `star_seller`, etc.) on a re-scrape.
- **Upgrade page (`/upgrade`) untranslated/wrong-currency elements on kolaylistele.com**: the Free plan price was hardcoded to `€0` regardless of `use_try`, the "Manage billing & cancel" button had a duplicate `id` attribute (so its translation target was unreachable and it never localized), and the bottom legal footer (Etsy trademark line + Privacy/Terms links) was hardcoded English. Free plan now shows `₺0` on kolaylistele.com, the button is translated (and its error-fallback text uses the active language via a new `currentLang` tracker — `setBilling()` previously referenced this same undeclared variable, which would throw on the Aylık/Yıllık toggle), and the footer is now translated via new `manageBilling`/`footerTrademark`/`footerPrivacy`/`footerTerms` keys in `T`.
- **Same legal footer (trademark + Privacy/Terms) on `index.html` and `connect.html`**: was hardcoded English on both pages (and on `connect.html` sat *after* the `<script>` that runs `setLang()`, so even a JS-side translation would silently no-op on first load). Moved both footers before their `<script>` tags and added `footer_trademark`/`footer_privacy`/`footer_terms` (`index.html`, `data-i18n`) and `footerTrademark`/`footerPrivacy`/`footerTerms` (`connect.html`, `T` object) translations for en/de/tr.
- **Feedback widget (`_feedback_widget.html`) language**: defaulted to English when no language was saved yet (kolaylistele.com first-time visitors saw English), and never updated after the initial page load — switching the page language via the lang switcher left the widget's title, placeholders, and buttons in the old language. Now defaults per-domain (`tr` on kolaylistele.com) and re-applies translations live on `.lang-btn` clicks.
- **Connect page (`/connect`) partial translation**: `setLang()` in `connect.html` called `getElementById('t-platform-label')` on an element that only exists inside the commented-out platform-picker markup, throwing `TypeError: Cannot set properties of null` and halting the rest of the translation function — so the step list and "Connect Shop" button stayed in English on the TR/DE pages. Now guarded with a null check.
- **Bulk upload shipping profile**: Added `GET /api/shipping-profiles` endpoint that loads the shop's shipping profiles at page init (not tied to generate response). `bulk.html` shows a publish-settings bar with a shipping profile dropdown above the result grid; `publishOne` passes the selected `shipping_profile_id` to `/api/publish`, fixing the "shipping_profile_id is required for physical listings" Etsy error. Full UI redesign: numbered cards, cleaner image/field layout, published state highlight, better error cards.
- **Pro photo variants**: generate 3 fal.ai variants in parallel so the request finishes within gunicorn's 120s timeout; clearer client error when API fails or returns empty.
- **PostHog `identify` crash** (`t.push is not a function`): defer `identify` to PostHog `loaded` callback instead of calling synchronously after `init`.
- **CSP**: allow `connect-src` to `https://eu-assets.i.posthog.com` (PostHog asset source maps).
- **Stripe webhook secrets for two domains**: `/stripe/webhook` now accepts host-specific/fallback secrets (`STRIPE_WEBHOOK_SECRET_EUR`, `STRIPE_WEBHOOK_SECRET_TRY`, `STRIPE_WEBHOOK_SECRET`) so separate Stripe webhook endpoints for `easylisting.app` and `kolaylistele.com` can both verify correctly.
- **Stripe webhook parsing** (`/stripe/webhook`): only `SignatureVerificationError` is treated as "bad signature". Other parse errors after a valid signature (e.g. Stripe SDK `event.object` check) fall back to `json.loads` so valid events are never silently dropped as 400.
- **`customer.subscription.updated` plan preservation**: if the webhook arrives without a `plan` key in subscription metadata (e.g. Stripe portal-initiated change), the handler now keeps the shop's existing DB plan instead of defaulting to `"pro"` — prevents a Starter subscriber being incorrectly promoted to Pro.
- **`/billing/change-plan` cancelled-subscription guard**: endpoint now checks `has_premium=1` in addition to `cus_/sub_` ID presence; returns a clear 404 instead of attempting to modify a cancelled subscription and returning "Something went wrong".
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
