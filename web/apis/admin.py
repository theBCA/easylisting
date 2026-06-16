"""Admin endpoints: abuse stats, AI ping, growth stats, plan management, dashboard.

Auth is handled by core.admin_auth.is_admin() — a valid Cloudflare Access JWT
(browser) or an Authorization: Bearer ADMIN_TOKEN (API). Unauthorized requests
get a bare 404 so the endpoints stay hidden.
"""
import os

from flask import Blueprint, request, jsonify, render_template

from extensions import csrf
from core.admin_auth import is_admin
from db import (get_abuse_summary, get_marketing_stats, get_payment_summary,
                get_ai_cost_summary, get_admin_email_list, get_admin_feedback, get_usage_summary)

bp = Blueprint("admin", __name__)


@bp.route("/admin/abuse")
def admin_abuse():
    if not is_admin():
        return "", 404
    days = int(request.args.get("days", 7))
    data = get_abuse_summary(days)
    lines = [f"<h2>Abuse signals — last {days} days</h2>"]
    lines.append("<h3>By event</h3><pre>")
    for event, n in data["by_event"].items():
        lines.append(f"  {event:<25} {n:>6}")
    lines.append("</pre><h3>Top IPs (by unique guest IDs created)</h3><pre>")
    for r in data["top_ips"]:
        lines.append(f"  ip={r['ip_hash']}  guests={r['guests']}  events={r['events']}")
    lines.append("</pre><h3>Top fingerprints (by unique guest IDs)</h3><pre>")
    for r in data["top_fps"]:
        lines.append(f"  fp={r['fp_hash']}  guests={r['guests']}  events={r['events']}")
    lines.append("</pre>")
    return "\n".join(lines), 200, {"Content-Type": "text/html"}

@bp.route("/admin/ping-ai")
def admin_ping_ai():
    if not is_admin():
        return "", 404

    import time
    results = {}
    test_prompt = "Reply with exactly this JSON and nothing else: {\"ok\": true}"

    # Test Gemini
    if os.getenv("GEMINI_API_KEY"):
        try:
            t0 = time.time()
            from google import genai as _ggenai
            client = _ggenai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            resp = client.models.generate_content(model="gemini-2.5-flash", contents=[test_prompt])
            results["gemini"] = {"status": "ok", "ms": int((time.time()-t0)*1000), "preview": (resp.text or "")[:80]}
        except Exception as e:
            results["gemini"] = {"status": "error", "error": str(e)[:200]}
    else:
        results["gemini"] = {"status": "no_key"}

    # Test NVIDIA
    if os.getenv("NVIDIA_API_KEY"):
        try:
            t0 = time.time()
            from openai import OpenAI as _OAI
            client = _OAI(base_url="https://integrate.api.nvidia.com/v1",
                          api_key=os.getenv("NVIDIA_API_KEY"), timeout=30.0)
            resp = client.chat.completions.create(
                model="meta/llama-3.2-90b-vision-instruct",
                messages=[{"role": "user", "content": test_prompt}],
                max_tokens=50,
            )
            content = resp.choices[0].message.content if resp.choices else ""
            results["nvidia"] = {"status": "ok" if content else "empty", "ms": int((time.time()-t0)*1000), "preview": (content or "")[:80]}
        except Exception as e:
            results["nvidia"] = {"status": "error", "error": str(e)[:200]}
    else:
        results["nvidia"] = {"status": "no_key"}

    # Test OpenAI
    if os.getenv("OPENAI_API_KEY"):
        try:
            t0 = time.time()
            from openai import OpenAI as _OAI2
            client = _OAI2(api_key=os.getenv("OPENAI_API_KEY"), timeout=20.0)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": test_prompt}],
                max_tokens=50,
            )
            content = resp.choices[0].message.content if resp.choices else ""
            results["openai"] = {"status": "ok", "ms": int((time.time()-t0)*1000), "preview": (content or "")[:80]}
        except Exception as e:
            results["openai"] = {"status": "error", "error": str(e)[:200]}
    else:
        results["openai"] = {"status": "no_key"}

    return jsonify(results)


@bp.route("/admin/stats")
def admin_stats():
    if not is_admin():
        return "", 404
    import db as _db
    import hashlib as _hl
    db_path = os.path.abspath(_db.DB_PATH)
    db_exists = os.path.isfile(db_path)
    db_size = os.path.getsize(db_path) if db_exists else 0

    # Build exclusion list from env + hardcoded owner emails
    _owner_emails = ["berkcem123@gmail.com", "berkcemarslan@gmail.com"]
    _extra = [e.strip().lower() for e in os.getenv("ADMIN_EXCLUDE_EMAILS", "").split(",") if e.strip()]
    _excl_hashes = list({_hl.sha256(e.encode()).hexdigest() for e in _owner_emails + _extra})

    s_all  = get_marketing_stats()
    s      = get_marketing_stats(exclude_hashes=_excl_hashes)
    lines = [
        "<h2>EasyListing — Growth Stats</h2>",
        f"<p style='font-family:monospace;font-size:12px;color:#888;'>DB: {db_path} | exists: {db_exists} | size: {db_size:,} bytes</p>",
        "<h3>Email funnel (excluding owner emails)</h3><pre>",
        f"  Magic links sent        {s['magic_links_sent']:>8}  (total incl. yours: {s_all['magic_links_sent']})",
        f"  Magic links verified    {s['magic_links_verified']:>8}  ({s['verify_rate_pct']}%)",
        f"  Unique email hashes     {s['email_hashes_total']:>8}  (total incl. yours: {s_all['email_hashes_total']})",
        "</pre><h3>Marketing list</h3><pre>",
        f"  Subscribed              {s['marketing_subscribed']:>8}",
        f"  Unsubscribed            {s['marketing_unsubscribed']:>8}",
    ]
    if s["by_locale"]:
        lines.append("</pre><h3>By locale</h3><pre>")
        for locale, n in s["by_locale"].items():
            lines.append(f"  {locale:<6} {n:>8}")
    if s["consents_by_day"]:
        lines.append("</pre><h3>New consents (last 30 days)</h3><pre>")
        for row in s["consents_by_day"]:
            lines.append(f"  {row['day']}  {row['n']:>4}")

    payments = get_payment_summary(30)
    lines.append("</pre><h3>Billing funnel — last 30 days</h3><pre>")
    lines.append(f"  Checkout sessions created {payments['attempts']:>8}")
    lines.append(f"  Checkout completed        {payments['completed']:>8}")
    lines.append(f"  Conversion                {payments['conversion_pct']:>7}%")
    for event, n in payments["by_event"].items():
        lines.append(f"  {event:<27} {n:>6}")

    # Raw table row counts — helps diagnose empty-table issues
    lines.append("</pre><h3>DB table counts (raw)</h3><pre>")
    try:
        import sqlite3 as _sq
        con = _sq.connect(_db.DB_PATH)
        con.row_factory = _sq.Row
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        for t in tables:
            try:
                n = con.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
                lines.append(f"  {t:<35} {n:>6} rows")
            except Exception as te:
                lines.append(f"  {t:<35} ERROR: {te}")
        con.close()
    except Exception as e:
        lines.append(f"  ERROR reading tables: {e}")
    lines.append("</pre>")

    # Shops list
    lines.append("<h3>Shops</h3><pre>")
    try:
        import sqlite3 as _sq2
        con2 = _sq2.connect(_db.DB_PATH)
        con2.row_factory = _sq2.Row
        rows = con2.execute(
            "SELECT shop_id, shop_name, plan, has_premium, free_used FROM shops ORDER BY rowid DESC LIMIT 100"
        ).fetchall()
        for r in rows:
            lines.append(f"  {str(r['shop_id']):<20} {str(r['shop_name'] or ''):<25} plan={r['plan'] or 'free':<10} premium={r['has_premium']} used={r['free_used']}")
        con2.close()
    except Exception as e:
        lines.append(f"  ERROR: {e}")
    lines.append("</pre>")
    return "\n".join(lines), 200, {"Content-Type": "text/html"}


@bp.route("/admin/shops-json")
def admin_shops_json():
    if not is_admin():
        return "", 404
    import db as _db
    import sqlite3 as _sq4
    con = _sq4.connect(_db.DB_PATH)
    con.row_factory = _sq4.Row
    rows = con.execute("SELECT shop_id, shop_name, plan, has_premium, free_used FROM shops ORDER BY rowid DESC").fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@bp.route("/admin/billing-json")
def admin_billing_json():
    if not is_admin():
        return "", 404
    days = int(request.args.get("days", 30))
    return jsonify(get_payment_summary(days))


@bp.route("/admin/set-plan", methods=["POST"])
@csrf.exempt
def admin_set_plan():
    if not is_admin():
        return "", 404
    body = request.get_json(silent=True) or {}
    shop_id  = str(body.get("shop_id", "")).strip()
    shop_name = str(body.get("shop_name", "")).strip()
    plan = body.get("plan", "pro")
    if plan not in ("free", "starter", "pro"):
        return jsonify({"error": "invalid plan"}), 400
    import db as _db
    import sqlite3 as _sq3
    # Look up by shop_name if shop_id not provided
    if not shop_id and shop_name:
        con3 = _sq3.connect(_db.DB_PATH)
        con3.row_factory = _sq3.Row
        rows = con3.execute(
            "SELECT shop_id, shop_name FROM shops WHERE LOWER(shop_name) = LOWER(?) LIMIT 5",
            (shop_name,)
        ).fetchall()
        con3.close()
        if not rows:
            return jsonify({"error": f"no shop found with name '{shop_name}'"}), 404
        if len(rows) > 1:
            return jsonify({"error": "multiple shops match", "shops": [dict(r) for r in rows]}), 400
        shop_id = rows[0]["shop_id"]
    if not shop_id:
        return jsonify({"error": "shop_id or shop_name required"}), 400
    shop = _db.get_shop(shop_id)
    if not shop:
        # Create the shop record if it doesn't exist (handles fresh DB after deploy)
        _db.ensure_shop(shop_id, shop_name or "Shop")
        shop = _db.get_shop(shop_id) or {}
    existing_customer = shop.get("stripe_customer_id") or ""
    # Preserve real Stripe customer IDs; pass None when none exists so we
    # don't pollute the field with a fake value that breaks billing portal.
    customer_arg = existing_customer if existing_customer.startswith("cus_") else None
    _db.set_premium(shop_id, customer_arg, "admin", plan != "free", plan)
    return jsonify({"ok": True, "shop_id": shop_id, "shop_name": shop.get("shop_name") or shop_name, "plan": plan})


def _classify_shop(shop_id: str) -> str:
    sid = str(shop_id)
    if sid.isdigit():
        return "etsy"
    if sid.startswith("guest_email_") or sid.startswith("review_"):
        return "email"
    if sid.startswith(("mobile_guest_", "guest_")):
        return "guest"
    return "other"


@bp.route("/admin/dashboard")
def admin_dashboard():
    if not is_admin():
        return "", 404
    # Date-range toggle: ?days=7|30|90 (default 30); clamp to the allowed set.
    try:
        days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    if days not in (7, 30, 90):
        days = 30
    import db as _db
    import sqlite3 as _sq
    con = _sq.connect(_db.DB_PATH)
    con.row_factory = _sq.Row
    rows = con.execute(
        "SELECT shop_id, shop_name, plan, has_premium, free_used, created_at, "
        "stripe_customer_id, stripe_subscription_id, listing_used, improve_used, "
        "photo_variant_used, last_seen, country "
        "FROM shops ORDER BY rowid DESC"
    ).fetchall()
    con.close()

    shops = []
    for r in rows:
        d = dict(r)
        d["kind"] = _classify_shop(d["shop_id"])
        shops.append(d)

    counts = {
        "total":    len(shops),
        "etsy":     sum(1 for s in shops if s["kind"] == "etsy"),
        "email":    sum(1 for s in shops if s["kind"] == "email"),
        "guest":    sum(1 for s in shops if s["kind"] == "guest"),
        "premium":  sum(1 for s in shops if s.get("has_premium")),
        "pro":      sum(1 for s in shops if s.get("plan") == "pro"),
        "starter":  sum(1 for s in shops if s.get("plan") == "starter"),
    }
    payments  = get_payment_summary(days)
    ai_costs  = get_ai_cost_summary(days)
    emails    = get_admin_email_list(200)
    feedback  = get_admin_feedback(50)
    usage     = get_usage_summary()
    abuse     = get_abuse_summary(days)
    return render_template("admin_dashboard.html",
                           shops=shops, counts=counts, payments=payments,
                           ai_costs=ai_costs, emails=emails,
                           feedback=feedback, usage=usage, abuse=abuse, days=days)
