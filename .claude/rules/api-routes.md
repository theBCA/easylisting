---
description: Rules that apply when editing Flask API routes
globs:
  - "web/apis/*.py"
---

## API route rules

- All routes needing auth call `require_connection()` (web) or `is_authorized()` (API/mobile) — never skip auth on a non-public endpoint
- POST/PUT/DELETE routes that are NOT called by mobile use CSRF protection via Flask-WTF (no `@csrf.exempt`). Mobile API routes use `@csrf.exempt` + `_mobile_auth()` check instead.
- Rate-limit sensitive endpoints: `/auth/*`, `/api/generate`, `/api/stripe/*`, `/api/improve-listing` all need `@limiter.limit()`
- `/stripe/webhook` is the only route that should have both `@csrf.exempt` AND `@limiter.exempt`
- Never import from `app.py` or `extensions.py` via the app factory — import the singletons directly: `from extensions import limiter, csrf`
- `safe_error(str(e))` is for client error messages only — log the real error separately with `logger.exception("...", e)`
- Blueprint URL names are qualified: `url_for("payments.upgrade")`, `url_for("listings.index")` — never use bare `url_for("upgrade")`
