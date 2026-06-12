# Security Review

Review code in this project for security issues.

## What to check

**Flask backend (`app.py`, `db.py`):**
- Authentication: all routes that need auth call `is_authorized()` or `_mobile_auth()`
- CSRF: POST/PUT/DELETE routes either use `@csrf.exempt` (for mobile API endpoints) or the CSRF token is validated via Flask-WTF
- SQL injection: all DB queries use parameterised `?` placeholders, never f-strings or `.format()`
- Secrets: no hardcoded API keys, tokens, or passwords — all read from `os.environ`
- Rate limiting: sensitive endpoints (`/auth/*`, `/api/generate`, `/api/stripe/*`) have `@limiter.limit()`
- Input validation: user-supplied strings are bounded in length before storage
- Token storage: Etsy `access_token` and `refresh_token` in `mobile_tokens` table are not logged

**iOS app (`ios/EasyListing/`):**
- Keychain: `mobileToken` stored via `KeychainHelper`, never in `UserDefaults`
- Network: all API calls go through `APIClient` which sends `X-Mobile-Token` header
- Deeplinks: `easylisting://` handler in `EasyListingApp.swift` validates path before acting
- No sensitive data logged with `print()` or `Logger` at non-debug levels

**Stripe:**
- Webhook signature verified with `stripe_lib.Webhook.construct_event`
- `success_url` uses backend `/upgrade/mobile/return` intermediate page (not direct deeplink)

## Usage

Run: `/security-review [file or area to focus on]`

**Current auth patterns:**
!`grep -n "require_connection\|is_authorized\|_mobile_auth" web/apis/*.py | head -20`

**Hardcoded secrets scan:**
!`grep -rn "sk_live\|sk_test\|whsec_\|password\s*=\s*['\"]" web/ --include="*.py" | grep -v "test_\|\.pyc" || echo "Clean"`

**Routes without rate limiting:**
!`grep -B5 "def " web/apis/*.py | grep -B3 "def " | grep "@bp.route" | grep -v "limiter" | head -10 || echo "All routes checked manually"`
