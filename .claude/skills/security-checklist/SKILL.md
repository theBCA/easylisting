---
name: security-checklist
description: Project-specific security checklist for EasyListing (Flask backend + iOS app) — auth, CSRF, SQL injection, rate limiting, Stripe webhooks, token storage. Use when reviewing changes to web/apis, web/core, or mobile/ios for security issues. For a general diff-wide review, use the bundled /security-review skill instead.
---

## EasyListing security checklist

**Flask backend (`web/apis/`, `web/db.py`):**
- Authentication: routes that need auth call `require_connection()` (web) or `is_authorized()` / `_mobile_auth()` (API/mobile)
- CSRF: POST/PUT/DELETE routes either use `@csrf.exempt` (mobile API endpoints only, paired with `_mobile_auth()`) or rely on Flask-WTF CSRF validation
- SQL injection: all DB queries use parameterised `?` placeholders, never f-strings or `.format()`
- Secrets: no hardcoded API keys, tokens, or passwords — all read from `os.environ`
- Rate limiting: `/auth/*`, `/api/generate`, `/api/stripe/*`, `/api/improve-listing` have `@limiter.limit()`
- Input validation: user-supplied strings are bounded in length before storage (`core/validators.py`)
- Token storage: Etsy `access_token`/`refresh_token` in `mobile_tokens` table are never logged

**iOS app (`mobile/ios/EasyListing/`):**
- Keychain: mobile token stored via `KeychainHelper`, never in `UserDefaults`
- Network: all API calls go through `APIClient`, which sends the `X-Mobile-Token` header
- Deeplinks: `easylisting://` handler validates the path before acting
- No sensitive data logged with `print()` or `Logger` at non-debug levels

**Stripe:**
- Webhook signature verified with `stripe_lib.Webhook.construct_event`; only `SignatureVerificationError` is treated as a bad signature (400)
- `success_url` uses the backend `/upgrade/mobile/return` intermediate page, not a direct deeplink

## Diagnostics

**Current auth patterns:**
!`grep -n "require_connection\|is_authorized\|_mobile_auth" web/apis/*.py | head -20`

**Hardcoded secrets scan:**
!`grep -rn "sk_live\|sk_test\|whsec_\|password\s*=\s*['\"]" web/ --include="*.py" | grep -v "test_\|\.pyc" || echo "Clean"`

**Routes without rate limiting:**
!`grep -B5 "def " web/apis/*.py | grep -B3 "def " | grep "@bp.route" | grep -v "limiter" | head -10 || echo "All routes checked manually"`
