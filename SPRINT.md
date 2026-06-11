# Sprint: Modularize the Monolith

Goal: split `app.py` (2787 lines) into a thin entry point + `core/` (shared logic) + `apis/` (route modules), keeping all 198 tests green at every step.

---

## Target structure

```
etsy/
├── app.py                 # ENTRY POINT: create app, init extensions, register apis, hooks (~120 lines)
├── extensions.py          # limiter + csrf singletons (no app binding)
├── db.py                  # unchanged — data layer
│
├── core/                  # shared logic, NO route handlers
│   ├── __init__.py
│   ├── config.py          # env constants + app.config values, logger
│   ├── email.py           # send_email
│   ├── validators.py      # _is_valid_image_bytes, validate_listing_input, safe_error
│   ├── etsy.py            # ETSY_* consts, _refresh_etsy_token, _refresh_web_etsy_token,
│   │                      #   _mobile_auth, etsy_headers, _dynamic_redirect_uri
│   ├── session.py         # shop_id, is_connected, is_guest, is_authorized, guest helpers,
│   │                      #   is_email_verified, has_premium_access, has_pro_access,
│   │                      #   usage_shop_id, require_connection, provider_chain
│   ├── domains.py         # _is_try_domain, _ip_hash
│   └── ai.py              # _build_prompt, _style_hint_for_shop, _merge_hint_with_style,
│                          #   _parse_ai_json, _gemini/_openai/_nvidia_generate, _run_provider,
│                          #   _run_text_json, _find_taxonomy_id, prompt/model constants
│
└── apis/                  # route modules (Flask blueprints)
    ├── __init__.py
    ├── etsy_oauth.py      # /connect /auth/start /auth/callback /disconnect
    ├── auth.py            # /guest /api/csrf-token /auth/mobile/* /api/fingerprint
    │                      #   /api/magic-link /auth/magic /api/email-verified
    ├── listings.py        # / /listings /api/listings /api/status /api/generate /api/taxonomy
    │                      #   /api/publish /api/template /api/improve-listing
    │                      #   /api/listing-variants /bulk /api/bulk-generate
    ├── photos.py          # /api/generate-photos
    ├── translate.py       # /api/translate
    ├── trendyol.py        # /uploads/<path> + /api/trendyol/*
    ├── payments.py        # /upgrade /stripe/checkout /api/stripe/checkout
    │                      #   /upgrade/mobile/return /stripe/webhook
    ├── admin.py           # /admin/*
    └── pages.py           # /unsubscribe /privacy /terms /health
```

`app.py` keeps: app factory, `app.config`, `limiter.init_app`/`csrf.init_app`, `init_db()`,
the `before_request`/`after_request`/`context_processor` hooks, blueprint registration, `__main__`.

---

## Critical constraints (read before any ticket)

1. **Tests patch `app.*` internals.** `test_app.py`, `test_integration.py`, `test_mobile_api.py`
   do `from app import app` and `patch("app.FAL_KEY", ...)`, `patch("app._gemini_generate", ...)`,
   `patch("app.shop_id", ...)`, etc. When a symbol moves to a module, the **call site** must
   reference it through the new module (e.g. `from core import ai` then `ai._gemini_generate(...)`),
   and the test patch target must change to `core.ai._gemini_generate`. Update tests in the SAME
   ticket that moves the symbol. Never leave a ticket with red tests.
2. **`from app import app` must keep working** — the WSGI entry (`Procfile`/Railway) imports it.
3. **One ticket = one module = one commit.** Run `pytest -q` at the end of every ticket. If red, fix
   before moving on.
4. **No behavior changes.** Pure move + re-wire. No renames of routes, no logic edits. Cleanups are
   separate follow-ups.
5. **Circular imports:** `core/*` may import `db`, `extensions`, other `core/*`, and flask — never
   `apis/*` and never `app`. `apis/*` may import `core/*`, `db`, `extensions` — never `app`.

---

## Ticket order & dependencies

```
T0  scaffold + baseline ......... (none)
T1  extensions.py ............... T0
T2  core/config + core/email .... T1
T3  core/validators ............. T2
T4  core/ai ..................... T2
T5  core/etsy ................... T2
T6  core/session + domains ...... T5
T7  apis/pages .................. T6
T8  apis/admin .................. T6
T9  apis/trendyol ............... T6
T10 apis/translate + photos ..... T4, T6
T11 apis/payments ............... T6
T12 apis/etsy_oauth ............. T5, T6
T13 apis/auth .................. T6
T14 apis/listings .............. T4, T5, T6
T15 finalize app.py + cleanup ... all
```

Phase 1 (T0–T6): foundations, no routes move. Phase 2 (T7–T14): routes move to blueprints.
T15: app.py becomes the thin factory.

---

## Per-ticket prompts

Each prompt below is self-contained. Run them one at a time as separate Claude Code tasks.
Every prompt assumes: working dir `/Users/berk.arslan/Desktop/etsy`, read `SPRINT.md` first,
end by running `pytest -q` and reporting pass/fail.

---

### T0 — Scaffold + baseline

```
Read SPRINT.md. Establish the refactor scaffold WITHOUT moving any code yet.
1. Run `pytest -q` and record the baseline pass count — this number must never drop.
2. Create empty packages: core/__init__.py and apis/__init__.py.
3. Rename the dead standalone script auth.py -> scripts/etsy_oauth_setup.py (it is NOT imported
   by the app — verify with `grep -rn "import auth" --include=*.py .` returning nothing relevant).
   Create scripts/ if needed.
4. Add a smoke test test_imports.py that asserts `from app import app` works and `app` has a
   test_client. Run pytest again — same pass count + the new test.
Report: baseline count, new count, confirmation nothing else changed.
```

### T1 — extensions.py

```
Read SPRINT.md. Extract Flask extension singletons to break future circular imports.
1. Create extensions.py with `limiter = Limiter(get_remote_address, default_limits=[...],
   storage_uri=...)` and `csrf = CSRFProtect()` — NO app argument.
2. In app.py, replace the inline `limiter = Limiter(..., app=app, ...)` and `csrf = CSRFProtect(app)`
   with `from extensions import limiter, csrf` then `limiter.init_app(app)` and `csrf.init_app(app)`
   AFTER `app.config.update(...)`.
3. Keep `limiter` and `csrf` importable from app (tests/decorators use `@limiter.limit` and
   `@csrf.exempt` as `app`-level names) — `from extensions import limiter, csrf` at top of app.py
   satisfies this.
Run pytest -q. Must equal T0 count. Report.
```

### T2 — core/config.py + core/email.py

```
Read SPRINT.md. Move configuration constants and the email helper.
1. core/config.py: move env-derived module constants and the logger setup. Include _is_production,
   _flask_secret handling note (the SECRET_KEY check stays where app.config is built — only move the
   plain constants like HTTP_TIMEOUT, FAL_KEY, ALLOW_PAID_OPENAI, ETSY-unrelated env reads, plus
   `logger = logging.getLogger(__name__)` and basicConfig). Do NOT move ETSY_* (that's T5).
2. core/email.py: move send_email (line ~72). It imports resend lazily and uses logger — import
   logger from core.config.
3. In app.py, replace definitions with imports. IMPORTANT: tests patch `app.FAL_KEY` and may call
   `app.send_email` — keep them importable from app via `from core.config import FAL_KEY, ...` and
   `from core.email import send_email`, and update any test that patches a moved symbol to its new
   home (e.g. core.config.FAL_KEY) ONLY where the call site now reads it from the new module.
Run pytest -q, fix any red, report.
```

### T3 — core/validators.py

```
Read SPRINT.md. Move input validation + error sanitization.
Move from app.py: _is_valid_image_bytes (~196), validate_listing_input (~417), safe_error (~412)
and their constants (MAX_*/MIN_* limits) into core/validators.py.
Re-import them in app.py. Update test patch targets if any test patches these (search
`patch("app.validate_listing_input"` etc.). Run pytest -q. Must equal baseline. Report.
```

### T4 — core/ai.py

```
Read SPRINT.md. Move the AI engine — highest-value extraction.
Move from app.py into core/ai.py: _build_prompt, _style_hint_for_shop, _merge_hint_with_style,
_parse_ai_json, _gemini_generate, _openai_generate, _nvidia_generate, _run_provider,
_run_text_json, _find_taxonomy_id, and all related constants (PLATFORM_PROMPTS, _LANG_NAMES,
NVIDIA_MODELS, _PROVIDER_CHAIN_*, _TURKISH_PLATFORMS, any GEMINI/OPENAI/NVIDIA key reads).
- _style_hint_for_shop / _merge_hint_with_style read templates from db — import db in core/ai.
- Route handlers in app.py still call these; re-import in app.py as `from core import ai` AND keep
  module-level aliases (`_gemini_generate = ai._gemini_generate`) so existing `app._gemini_generate`
  patch targets keep working, OR (preferred) change call sites to `ai._gemini_generate(...)` and
  update tests to patch `core.ai._gemini_generate`. Pick the alias approach if many tests patch these
  — grep first: `grep -n "patch(\"app\\._\\(gemini\\|openai\\|nvidia\\|run_provider\\|build_prompt\\)" test_*.py`.
Run pytest -q. Fix reds. Report which patch targets changed.
```

### T5 — core/etsy.py

```
Read SPRINT.md. Move the Etsy API client layer.
Move into core/etsy.py: ETSY_* constants (CLIENT_ID/SECRET/SCOPES/REDIRECT_URI/API key header),
HTTP_TIMEOUT if Etsy-specific, _refresh_etsy_token (~204), _refresh_web_etsy_token (~232),
_mobile_auth (~262), etsy_headers (~273), _dynamic_redirect_uri (~891).
- These use flask `session`/`request` and `db` (mobile tokens) — import them.
- _mobile_auth and etsy_headers are used by session.py (T6) and many routes — keep clean.
Re-import in app.py. Update test patches (`patch("app.etsy_headers"`, `patch("app._mobile_auth"`,
`patch("app._refresh_web_etsy_token"`) to core.etsy targets where call sites moved.
Run pytest -q. Fix reds. Report.
```

### T6 — core/session.py + core/domains.py

```
Read SPRINT.md. Move session/auth/identity helpers — the shared spine for all blueprints.
core/domains.py: _is_try_domain (~2104), _ip_hash (~2100).
core/session.py: shop_id, is_connected, is_guest, is_authorized, _cookie_guest_id,
_get_or_create_guest_id, guest_shop_id, _try_restore_email_session, is_email_verified,
provider_chain, has_premium_access, has_pro_access, usage_shop_id, require_connection.
- session.py imports: flask session/request, db, core.etsy (_mobile_auth, etsy_headers).
- Many tests patch `app.shop_id`, `app.is_connected`, `app.has_pro_access`, `app.is_guest`,
  `app.guest_shop_id`, `app.usage_shop_id`. Grep them ALL first. Because routes still live in app.py
  this phase, prefer module-level aliases in app.py (`shop_id = session_mod.shop_id`) so existing
  patch targets keep working — routes call the local name. Document that in T14 these become
  `from core import session` references inside apis/listings.py and tests repoint to core.session.
Run pytest -q. Fix reds. Report every patch target touched.
```

### T7 — apis/pages.py

```
Read SPRINT.md. First blueprint — trivial routes, proves the pattern.
Create apis/pages.py with `bp = Blueprint("pages", __name__)`. Move routes: /unsubscribe (~2748),
/privacy (~2771), /terms (~2775), /health (~2781). Change `@app.route` -> `@bp.route`.
In app.py: `from apis.pages import bp as pages_bp` and `app.register_blueprint(pages_bp)`; delete the
moved route bodies. url_for references like 'privacy' become 'pages.privacy' — grep templates and code
for `url_for('privacy'|'terms'|'health'|'unsubscribe')` and update.
Run pytest -q. Fix reds. Report.
```

### T8 — apis/admin.py

```
Read SPRINT.md. Move admin routes into apis/admin.py (Blueprint "admin").
Routes: /admin/abuse (~2531), /admin/ping-ai (~2552), /admin/stats (~2616), /admin/shops-json (~2693),
/admin/set-plan (~2708, has @csrf.exempt — import csrf from extensions). They import db helpers and
core.ai (ping-ai). Register in app.py, delete originals, update any url_for. Run pytest -q. Report.
```

### T9 — apis/trendyol.py

```
Read SPRINT.md. Move Trendyol integration into apis/trendyol.py (Blueprint "trendyol").
Move: serve_upload /uploads/<path> (~2251), _save_image_for_upload (~2259), _tr_headers/_tr_get/
_tr_post/_tr_creds and all /api/trendyol/* routes (~2309–2530), plus _TR_CATEGORY_CACHE and any
@csrf.exempt (import csrf from extensions). Uses core.session (shop_id) + db.platform_credentials.
Register, delete originals, fix url_for('serve_upload') -> 'trendyol.serve_upload' in templates/code.
Run pytest -q. Fix reds. Report.
```

### T10 — apis/translate.py + apis/photos.py

```
Read SPRINT.md. Two single-route blueprints.
apis/translate.py (Blueprint "translate"): _translate_ai (~1902) + /api/translate (~1933). Uses core.ai.
apis/photos.py (Blueprint "photos"): _photo_variant_prompts (~1823), _fal_generate_variant (~1834),
/api/generate-photos (~1867). Uses core.session (has_pro_access, shop_id), db photo-variant helpers,
FAL_KEY from core.config. Tests patch `app.FAL_KEY` and `app._fal_generate_variant` — repoint to the
new modules. Register both, delete originals. Run pytest -q. Fix reds. Report.
```

### T11 — apis/payments.py

```
Read SPRINT.md. Move billing — test the Stripe webhook carefully.
apis/payments.py (Blueprint "payments"): upgrade (~2055), stripe_checkout (~2110),
api_stripe_checkout (~2147), upgrade_mobile_return (~2187), stripe_webhook (~2212, @csrf.exempt),
plus _PLAN_PRICE_IDS / _PLAN_PRICE_IDS_TRY constants. Uses core.session, core.domains (_is_try_domain),
db (set_premium, get_shop_by_stripe_customer), stripe lib. Register, delete originals, update url_for
('upgrade' -> 'payments.upgrade') across templates (upgrade.html, index.html) and code.
Run pytest -q AND the webhook tests specifically. Fix reds. Report.
```

### T12 — apis/etsy_oauth.py

```
Read SPRINT.md. Move the Etsy OAuth flow.
apis/etsy_oauth.py (Blueprint "etsy_oauth"): connect (~886), auth_start (~902, @limiter),
auth_callback (~933, @limiter), disconnect (~1015). Uses core.etsy (_dynamic_redirect_uri, token
refresh), db.ensure_shop, core.session. Register, delete originals. Update url_for('connect'|
'auth_start'|'auth_callback'|'disconnect') across templates + code to 'etsy_oauth.*'.
Manually trace the connect -> callback -> session set path. Run pytest -q. Fix reds. Report.
```

### T13 — apis/auth.py

```
Read SPRINT.md. Move email/magic-link/mobile/fingerprint auth.
apis/auth.py (Blueprint "auth"): start_guest /guest (~1021), api_csrf_token (~1031),
auth_mobile_guest (~1040), auth_mobile_logout (~1056), api_fingerprint (~1066), api_magic_link (~1116),
auth_magic (~1317), api_email_verified (~1396). Several are @csrf.exempt (import csrf). Uses
core.email (send_email), core.session, core.domains, db magic/fp/mobile helpers.
NOTE: name the blueprint "auth" — there is no longer a root auth.py (renamed in T0). Register, delete
originals, update url_for. test_mobile_api.py heavily exercises these — run it specifically.
Run pytest -q. Fix reds. Report.
```

### T14 — apis/listings.py

```
Read SPRINT.md. The big one — move the core listing flow last.
apis/listings.py (Blueprint "listings"): index / (~1365), listings /listings (~1403),
api_listings (~1420), api_status (~1437), api_generate (~1458), api_taxonomy (~1574),
api_publish (~1595), api_template (~1704), _consume_improve_allowance (~1727),
_listing_fields_from_body (~1734), api_improve_listing (~1746), api_listing_variants (~1793),
bulk (~1960), api_bulk_generate (~1969).
Uses core.ai (generation + taxonomy), core.session (all gates), core.validators, core.etsy
(publish via etsy_headers), db usage helpers. This blueprint references the most patched symbols —
update ALL remaining test patch targets from app.* to their core.* homes now; after this ticket
app.py contains NO route handlers and NO business logic.
Register, delete originals. index is the root route — confirm url_for('index') -> 'listings.index'
everywhere (templates + redirects). Run the FULL suite. Fix reds. Report.
```

### T15 — Finalize app.py + cleanup

```
Read SPRINT.md. Make app.py the thin factory and clean up.
1. app.py should now contain only: imports, app creation, app.config, limiter/csrf init_app, init_db,
   before_request (block_probe_paths, setup_request), after_request (set_security_headers),
   context_processor (inject_security), the _BLOCKED_PATH_* constants (or move to core/middleware.py),
   all register_blueprint calls, and __main__. Target < 130 lines.
2. Remove any now-unused module-level aliases left from T2–T6.
3. Optional: wrap creation in `def create_app()` returning app, with `app = create_app()` at module
   bottom so `from app import app` still works.
4. Update CLAUDE.md (or create one) documenting the new layout.
Run the full suite + a manual smoke (`flask run` or import check). Report final app.py line count and
total test pass count (must equal baseline + test_imports).
```

---

## Definition of done (whole sprint)

- `app.py` < 130 lines, zero route handlers, zero business logic.
- All 198 tests + `test_imports` green.
- `core/` has no imports from `apis/` or `app`; `apis/` has no imports from `app`.
- `grep -rn "url_for(" templates/` all resolve to blueprint-qualified endpoints.
- Manual smoke: connect → generate → publish, magic-link, Stripe checkout, both domains.
