"""Core listing flow: home, listings, generate, publish, templates, tools, bulk."""
import os
import json
import time
import base64
import tempfile
import threading

import requests
from flask import (
    Blueprint, request, jsonify, session, redirect, url_for, render_template,
)

from extensions import limiter
from core.config import logger, HTTP_TIMEOUT, GUEST_FREE_LIMIT
from core.validators import (
    validate_listing_input, safe_error, _is_valid_image_bytes, ALLOWED_IMAGE_TYPES,
)
from core.ai import (
    _run_provider, _run_text_json, _merge_hint_with_style,
    _LANG_NAMES, PLATFORM_PROMPTS, NVIDIA_MODELS,
    _PROVIDER_CHAIN_FREE, _PROVIDER_CHAIN_PREMIUM,
)
from core.etsy import etsy_headers, _find_taxonomy_id, _mobile_auth, ETSY_API_KEY_HEADER
from core.domains import _ip_hash
from core.session import (
    require_connection, is_guest, is_connected, is_authorized, shop_id, guest_shop_id,
    is_email_verified, has_premium_access, _consume_improve_allowance,
)
from db import (
    can_generate, increment_usage, increment_improve_usage,
    save_template, get_template, ensure_shop, log_abuse_signal,
    get_platform_credentials,
)

bp = Blueprint("listings", __name__)


@bp.route("/")
def index():
    redir = require_connection()
    if redir: return redir
    if is_guest():
        gid = guest_shop_id()
        ensure_shop(gid, "Guest")
        email_verified = is_email_verified()
        premium = has_premium_access()
        from db import FREE_LIMIT as _FL
        limit = _FL if email_verified else GUEST_FREE_LIMIT
        _, remaining = can_generate(gid, limit)
        return render_template("index.html",
            shop_name=None, remaining=remaining, free_limit=limit,
            unlimited=premium, is_guest=True, has_premium=premium,
            email_verified=email_verified)
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

@bp.route("/listings")
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

@bp.route("/api/listings")
@limiter.limit("30 per minute")
def api_listings():
    """JSON version of /listings for the mobile app."""
    if not is_connected():
        return jsonify({"error": "Not connected"}), 401
    state = request.args.get("state", "active")
    if state not in ("draft", "active"):
        state = "active"
    r = requests.get(
        f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/listings"
        f"?state={state}&limit=100&includes[]=images",
        headers=etsy_headers(), timeout=HTTP_TIMEOUT,
    )
    items = r.json().get("results", []) if r.ok else []
    return jsonify({"results": items})

@bp.route("/api/status")
@limiter.limit("60 per minute")
def api_status():
    if not is_authorized(): return jsonify({"error": "Not connected"}), 401
    if is_guest():
        gid = guest_shop_id()
        ensure_shop(gid, "Guest")
        from db import FREE_LIMIT as _FL
        limit = _FL if is_email_verified() else GUEST_FREE_LIMIT
        allowed, remaining = can_generate(gid, limit)
    else:
        allowed, remaining = can_generate(str(shop_id()))
    sid = str(shop_id() or guest_shop_id())
    return jsonify({
        "allowed":            allowed,
        "remaining":          remaining,
        "is_guest":           is_guest(),
        "is_email":           is_email_verified(),
        "trendyol_connected": bool(get_platform_credentials(sid, "trendyol")),
    })

@bp.route("/api/generate", methods=["POST"])
@limiter.limit("10 per minute; 50 per day")
def api_generate():
    if not is_authorized(): return jsonify({"error": "Not connected"}), 401
    if is_guest() and not is_email_verified():
        return jsonify({"error": "email_verification_required"}), 403

    if is_guest():
        sid = guest_shop_id()
        ensure_shop(sid, "Guest")
        from db import FREE_LIMIT as _FL
        limit = _FL if is_email_verified() else GUEST_FREE_LIMIT
        allowed, remaining = can_generate(sid, limit)
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
    provider     = request.form.get("provider", "nvidia")
    nvidia_model = request.form.get("nvidia_model", "llama-90b")
    lang         = request.form.get("lang", "en")
    platform     = request.form.get("platform", "etsy")

    premium = has_premium_access()
    chain   = _PROVIDER_CHAIN_PREMIUM if premium else _PROVIDER_CHAIN_FREE

    if provider not in chain:
        provider = chain[0]
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
    # Try chosen provider first, then fall through the chain.
    start   = chain.index(provider) if provider in chain else 0
    ordered = chain[start:] + chain[:start]
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
            logger.warning("AI RuntimeError (provider=%s), trying next: %s", p, e)
            continue
        except json.JSONDecodeError as e:
            logger.warning("AI JSON decode error (provider=%s), trying next: %s", p, e)
            continue
        except Exception as e:
            logger.exception("AI error (provider=%s): %s", p, e)
            continue
    if data is None:
        return jsonify({"error": "All AI providers are over quota right now. Please try again later."}), 503

    increment_usage(sid)
    from core.analytics import capture as _ph_capture
    _ph_capture(sid, "listing_generated", {
        "provider": p, "lang": lang, "platform": platform,
        "image_count": len(image_bytes), "is_guest": is_guest(),
    })

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

@bp.route("/api/taxonomy")
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

def _bg_post_listing(safe_lid, data, access_token):
    """Upload images + personalization after listing creation, in a daemon thread.

    shop_id() and etsy_headers() read from Flask session, which only exists in
    a request context. Snapshot them here (still in-context) and pass as plain
    values so the thread runs without a request context.
    """
    sid          = str(shop_id())
    auth_headers = etsy_headers()

    def _run():
        _upload_images(safe_lid, sid, data["image_previews"], access_token)
        _post_personalization(safe_lid, sid, auth_headers, data["personalization_instructions"])

    threading.Thread(target=_run, daemon=True).start()


def _upload_images(safe_lid, sid, image_previews, access_token):
    os.makedirs("images", exist_ok=True)
    for i, b64 in enumerate(image_previews[:10], 1):
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
                    f"https://openapi.etsy.com/v3/application/shops/{sid}/listings/{safe_lid}/images",
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


def _post_personalization(safe_lid, sid, auth_headers, pi):
    if not pi:
        return
    try:
        requests.post(
            f"https://openapi.etsy.com/v3/application/shops/{sid}/listings/{safe_lid}/personalization",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"personalization_questions": [{
                "question_text": "Personalization",
                "instructions":  pi,
                "question_type": "text_input",
                "required":      False,
                "max_allowed_characters": 256,
            }]},
            timeout=HTTP_TIMEOUT,
        )
    except Exception as e:
        logger.error("Personalization error for listing %s: %s", safe_lid, e)


@bp.route("/api/publish", methods=["POST"])
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

    # Fetch shop's readiness state — only include if the API returns one
    readiness_id = None
    try:
        rs = requests.get(
            f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/readiness-state-definitions",
            headers=etsy_headers(), timeout=HTTP_TIMEOUT,
        )
        if rs.ok:
            results = rs.json().get("results", [])
            if results:
                readiness_id = results[0]["readiness_state_id"]
    except Exception:
        pass

    payload = {
        "title":             data["title"],
        "description":       data["description"],
        "price":             data["price"],
        "quantity":          1,
        "who_made":          "i_did",
        "when_made":         "made_to_order",
        "taxonomy_id":       data["taxonomy_id"],
        "is_supply":         False,
        "should_auto_renew": True,
        "type":              "physical",
        "state":             "draft",
        "tags":              data["tags"],
        "materials":         data["materials"],
    }
    if readiness_id:
        payload["readiness_state_id"] = readiness_id
    if data["shipping_profile_id"]:
        payload["shipping_profile_id"] = data["shipping_profile_id"]

    r = requests.post(
        f"https://openapi.etsy.com/v3/application/shops/{shop_id()}/listings",
        headers={**etsy_headers(), "Content-Type": "application/json"},
        json=payload, timeout=HTTP_TIMEOUT,
    )
    if not r.ok:
        logger.error("Etsy create listing error: %s", r.text)
        try:
            etsy_msg = r.json().get("error", "") or r.json().get("error_description", "")
        except Exception:
            etsy_msg = ""
        user_msg = etsy_msg if etsy_msg else "Failed to create listing on Etsy. Check your shop settings."
        return jsonify({"error": user_msg}), 400

    listing_id = r.json()["listing_id"]
    safe_lid   = str(int(listing_id))  # ensure numeric, no path traversal

    # Upload images + personalization in a background thread so the response
    # returns immediately (image uploads can take 60–120s for 5+ images,
    # easily exceeding the gunicorn 120s worker timeout).
    access_token = session.get("access_token") or ((_mobile_auth() or {}).get("access_token"))
    _bg_post_listing(safe_lid, data, access_token)

    from core.analytics import capture as _ph_capture
    _ph_capture(str(shop_id()), "listing_published", {"listing_id": listing_id})
    return jsonify({"success": True, "listing_id": listing_id})

# ── Template ──────────────────────────────────────────────────────────────────

@bp.route("/api/template", methods=["GET", "POST"])
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

def _listing_fields_from_body(body: dict) -> dict:
    return {
        "title":       str(body.get("title", ""))[:140],
        "description": str(body.get("description", ""))[:5000],
        "tags":        [str(t)[:20] for t in body.get("tags", [])[:13]],
        "materials":   [str(m)[:45] for m in body.get("materials", [])[:13]],
        "price":       str(body.get("price", ""))[:30],
        "category":    str(body.get("category", ""))[:160],
    }

@bp.route("/api/improve-listing", methods=["POST"])
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

@bp.route("/api/listing-variants", methods=["POST"])
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

# ── Translation ───────────────────────────────────────────────────────────────

# ── Bulk generate ─────────────────────────────────────────────────────────────

@bp.route("/bulk")
def bulk():
    redir = require_connection()
    if redir: return redir
    if not has_premium_access():
        return redirect(url_for("payments.upgrade", bulk_required=1))
    return render_template("bulk.html", shop_name=session.get("shop_name"))

@bp.route("/api/bulk-generate", methods=["POST"])
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
    provider = request.form.get("provider", "nvidia")
    lang     = request.form.get("lang", "en")
    platform = request.form.get("platform", "etsy")
    chain    = _PROVIDER_CHAIN_PREMIUM if has_premium_access() else _PROVIDER_CHAIN_FREE
    if provider not in chain:
        provider = chain[0]
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

    key_env = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "nvidia": "NVIDIA_API_KEY"}
    start   = chain.index(provider) if provider in chain else 0
    ordered = chain[start:] + chain[:start]
    data    = None
    for p in ordered:
        if not os.getenv(key_env[p]):
            continue
        try:
            data = _run_provider(p, image_bytes, hint, "llama-90b", lang=lang, platform=platform)
            break
        except RuntimeError as e:
            logger.warning("Bulk AI RuntimeError (provider=%s), trying next: %s", p, e)
            continue
        except Exception as e:
            logger.exception("Bulk AI error (provider=%s): %s", p, e)
            continue
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
