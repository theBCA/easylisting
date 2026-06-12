# EasyListing / kolaylistele — Architecture

Flask app on Railway serving two domains: **easylisting.app** (EUR) and
**kolaylistele.com** (TRY). AI-powered Etsy/marketplace listing generator.

## Layout

```
web/                       # The Flask backend (Railway deploys this — see Deploy below)
├── app.py                 # Thin entry point: config, extensions, hooks, blueprint registration
├── extensions.py          # limiter + csrf singletons (bound via init_app in app.py)
├── db.py                  # SQLite data layer
├── conftest.py            # puts web/ on sys.path for pytest
├── core/                  # Shared logic — NO route handlers, NO imports from apis/ or app
│   ├── config.py          # env constants + logging (loads .env)
│   ├── email.py           # send_email (Resend)
│   ├── validators.py      # validate_listing_input, safe_error, image sniffing + limits
│   ├── etsy.py            # Etsy OAuth token refresh, headers, taxonomy lookup, ETSY_* consts
│   ├── session.py         # connection state, guest IDs, plan access, _consume_improve_allowance
│   ├── domains.py         # _is_try_domain, _ip_hash
│   └── ai.py              # prompt building, Gemini/OpenAI/NVIDIA dispatch, JSON parsing
├── apis/                  # Route blueprints — import from core/, db, extensions (never app)
│   ├── pages.py           # /unsubscribe /privacy /terms /health
│   ├── admin.py           # /admin/* (token-gated)
│   ├── trendyol.py        # /uploads/<f> + /api/trendyol/*
│   ├── photos.py          # /api/generate-photos (fal.ai, pro-only)
│   ├── translate.py       # /api/translate
│   ├── payments.py        # /upgrade /stripe/checkout /api/stripe/checkout /stripe/webhook
│   ├── etsy_oauth.py      # /connect /auth/start /auth/callback /disconnect
│   ├── auth.py            # /guest /api/csrf-token /auth/mobile/* /api/fingerprint /api/magic-link /auth/magic
│   └── listings.py        # / /listings /api/generate /api/publish /api/template /api/improve-listing /bulk ...
├── templates/             # Jinja templates
├── static/                # static assets (+ static/uploads for Trendyol image URLs)
└── tests/                 # test_app.py, test_mobile_api.py, test_integration.py, test_imports.py

mobile/
└── ios/                   # native iOS app (Swift / Xcode)

scripts/                   # standalone one-off utilities (NOT imported by the app)
├── etsy_oauth_setup.py    # one-time OAuth token helper (was root auth.py)
├── get_shop_id.py  get_taxonomy.py  get_shipping_profiles.py
├── upload_listings.py     # bulk-create drafts from listings.csv
└── listings.csv

docs/                      # SYSTEM_DESIGN.md, architecture.svg
Procfile  railway.json  requirements.txt   # deploy config — stay at repo root
```

## Import rules (enforced to avoid cycles)
- `core/*` may import: flask, db, extensions, other `core/*`. Never `apis/*` or `app`.
- `apis/*` may import: flask, db, extensions, `core/*`. Never `app`.
- `extensions.py` holds `limiter`/`csrf` with no app binding so blueprints can import them freely.

## Endpoint naming
Routes are blueprint-qualified: `url_for("listings.index")`, `url_for("payments.upgrade")`,
`url_for("etsy_oauth.connect")`. Templates only use `url_for('static', ...)`.

## Tests
- Run from `web/`: `cd web && pytest -m "not integration"` (or `pytest web/tests` from root).
- 213 unit tests + 3 smoke (`tests/test_imports.py`) = 216, all green.
- Integration tests (`-m integration`) hit live Gemini/FAL/Stripe and need real API keys.
- Tests patch helpers at their module path, e.g. `patch("apis.listings._run_provider")`,
  `patch("apis.photos.FAL_KEY")`, `patch("apis.auth.send_email")`. When moving a symbol,
  repoint its patch target in the same change.

## Deploy
Two separate Railway services (one per domain) with their own env vars + ephemeral SQLite.
Both deploy from this repo root. The backend lives in `web/`, so `Procfile` and `railway.json`
start it with `gunicorn --chdir web app:app`. `requirements.txt` stays at the repo root for
Nixpacks to install. No Railway dashboard change is needed.
TRY Stripe price IDs must be set on the kolaylistele.com service.

## Changelog rule (MANDATORY for all agents)
After **every** implementation — feature, fix, security change, or refactor — update
`CHANGELOG.md` at the repo root before considering the task done.

Rules:
- If there is an `[Unreleased]` section, add your entry there.
- If there is no `[Unreleased]` section yet, create one at the top (above the latest version).
- When a version is released/tagged, rename `[Unreleased]` to `[x.y] — YYYY-MM-DD — one-line summary`.
- Group entries under: **Added**, **Changed**, **Fixed**, **Security**, or **Removed**.
- Be specific: name the endpoint, table, env var, file, or component affected.
- One bullet per logical change. Don't batch unrelated changes into one bullet.
