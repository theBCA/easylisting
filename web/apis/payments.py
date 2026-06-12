"""Stripe billing: upgrade page, checkout sessions, webhook."""
import os
import urllib.parse

from flask import Blueprint, request, jsonify, render_template, session

from extensions import limiter, csrf
from core.config import logger, GUEST_FREE_LIMIT
from core.domains import _is_try_domain
from core.validators import safe_error
from core.session import (
    require_connection, usage_shop_id, is_guest, is_email_verified, is_authorized,
)
from core.etsy import _mobile_auth
from db import can_generate, get_shop, set_premium, get_shop_by_stripe_customer, ensure_shop

bp = Blueprint("payments", __name__)


@bp.route("/upgrade")
def upgrade():
    redir = require_connection()
    if redir: return redir
    sid = usage_shop_id()
    from db import FREE_LIMIT as _FL
    limit = _FL if (not is_guest() or is_email_verified()) else GUEST_FREE_LIMIT
    _, remaining = can_generate(sid, limit)
    s = get_shop(sid) or {}
    use_try = _is_try_domain()
    return render_template(
        "upgrade.html",
        shop_name=session.get("shop_name"),
        remaining=remaining,
        current_plan=s.get("plan") or "free",
        stripe_key=os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
        has_starter=bool(
            os.getenv("STRIPE_STARTER_PRICE_ID") or os.getenv("STRIPE_STARTER_PRICE_ID_TRY")
        ),
        has_pro=bool(
            os.getenv("STRIPE_PRO_PRICE_ID") or os.getenv("STRIPE_PRO_PRICE_ID_TRY")
            or os.getenv("STRIPE_PRICE_ID")
        ),
        has_starter_annual=bool(
            os.getenv("STRIPE_STARTER_ANNUAL_PRICE_ID") or os.getenv("STRIPE_STARTER_ANNUAL_PRICE_ID_TRY")
        ),
        has_pro_annual=bool(
            os.getenv("STRIPE_PRO_ANNUAL_PRICE_ID") or os.getenv("STRIPE_PRO_ANNUAL_PRICE_ID_TRY")
        ),
        use_try=use_try,
    )

_PLAN_PRICE_IDS = {
    "starter":        "STRIPE_STARTER_PRICE_ID",
    "pro":            "STRIPE_PRO_PRICE_ID",
    "starter_annual": "STRIPE_STARTER_ANNUAL_PRICE_ID",
    "pro_annual":     "STRIPE_PRO_ANNUAL_PRICE_ID",
}
_PLAN_PRICE_IDS_TRY = {
    "starter":        "STRIPE_STARTER_PRICE_ID_TRY",
    "pro":            "STRIPE_PRO_PRICE_ID_TRY",
    "starter_annual": "STRIPE_STARTER_ANNUAL_PRICE_ID_TRY",
    "pro_annual":     "STRIPE_PRO_ANNUAL_PRICE_ID_TRY",
}

@bp.route("/stripe/checkout", methods=["POST"])
@limiter.limit("10 per minute")
def stripe_checkout():
    redir = require_connection()
    if redir: return jsonify({"error": "Not connected"}), 401
    if not os.getenv("STRIPE_SECRET_KEY"):
        return jsonify({"error": "Stripe not configured"}), 503
    body = request.get_json(silent=True) or {}
    plan = body.get("plan", "pro")
    if plan not in _PLAN_PRICE_IDS:
        plan = "pro"
    price_map = _PLAN_PRICE_IDS_TRY if _is_try_domain() else _PLAN_PRICE_IDS
    price_env = price_map.get(plan, "STRIPE_PRO_PRICE_ID")
    price_id  = os.getenv(price_env) or os.getenv("STRIPE_PRICE_ID")
    base_plan = plan.replace("_annual", "")
    if not price_id:
        return jsonify({"error": "Stripe price not configured for this plan"}), 503
    try:
        import stripe as stripe_lib
        stripe_lib.api_key = os.getenv("STRIPE_SECRET_KEY")
        base = request.url_root.rstrip("/")
        checkout = stripe_lib.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=base + f"/upgrade?success=1&plan={plan}",
            cancel_url=base  + "/upgrade?cancelled=1",
            client_reference_id=str(usage_shop_id()),
            metadata={"shop_id": str(usage_shop_id()),
                      "shop_name": session.get("shop_name", ""),
                      "plan": base_plan},
            # Stamp the subscription too, so renewal/update webhooks can recover
            # the shop even if our DB was reset between purchase and renewal.
            subscription_data={"metadata": {"shop_id": str(usage_shop_id()),
                                            "plan": base_plan}},
        )
        return jsonify({"url": checkout.url})
    except Exception as e:
        logger.exception("Stripe checkout error: %s", e)
        return jsonify({"error": safe_error(str(e))}), 500

@bp.route("/api/stripe/checkout", methods=["POST"])
@limiter.limit("10 per minute")
def api_stripe_checkout():
    """Mobile-friendly Stripe checkout. Returns {"url": "..."} for the app to open in Safari."""
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    if is_guest() and not is_email_verified():
        return jsonify({"error": "Please connect with Etsy or verify your email to upgrade."}), 403
    if not os.getenv("STRIPE_SECRET_KEY"):
        return jsonify({"error": "Stripe not configured"}), 503
    body = request.get_json(silent=True) or {}
    plan = body.get("plan", "pro")
    if plan not in _PLAN_PRICE_IDS:
        plan = "pro"
    price_map = _PLAN_PRICE_IDS_TRY if _is_try_domain() else _PLAN_PRICE_IDS
    price_env = price_map.get(plan, "STRIPE_PRO_PRICE_ID")
    price_id  = os.getenv(price_env) or os.getenv("STRIPE_PRICE_ID")
    if not price_id:
        return jsonify({"error": "Stripe price not configured for this plan"}), 503
    base_plan = plan.replace("_annual", "")
    sid = str(usage_shop_id())
    mob = _mobile_auth()
    shop_name_val = (mob or {}).get("shop_name", "") or session.get("shop_name", "")
    base = request.url_root.rstrip("/")
    try:
        import stripe as stripe_lib
        stripe_lib.api_key = os.getenv("STRIPE_SECRET_KEY")
        checkout = stripe_lib.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=base + f"/upgrade/mobile/return?status=success&plan={plan}",
            cancel_url=base  + "/upgrade/mobile/return?status=cancel",
            client_reference_id=sid,
            metadata={"shop_id": sid, "shop_name": shop_name_val, "plan": base_plan},
            subscription_data={"metadata": {"shop_id": sid, "plan": base_plan}},
        )
        return jsonify({"url": checkout.url})
    except Exception as e:
        logger.exception("Stripe checkout error (mobile): %s", e)
        return jsonify({"error": safe_error(str(e))}), 500

@bp.route("/upgrade/mobile/return")
def upgrade_mobile_return():
    """Redirect page shown after Stripe checkout on mobile. Deeplinks back to the app."""
    status = request.args.get("status", "cancel")
    plan   = request.args.get("plan", "pro")
    if status == "success":
        deeplink = f"easylisting://upgrade/success?plan={urllib.parse.quote(plan)}"
        msg      = "Payment complete! Returning to EasyListing…"
    else:
        deeplink = "easylisting://upgrade/cancel"
        msg      = "Returning to EasyListing…"
    html = f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="1;url={deeplink}">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EasyListing</title>
<style>body{{font-family:-apple-system,sans-serif;display:flex;align-items:center;
justify-content:center;height:100vh;margin:0;background:#F6F7FB;color:#111827}}</style>
</head><body>
<p>{msg}</p>
<script>setTimeout(()=>window.location='{deeplink}',800)</script>
</body></html>"""
    return html, 200

@bp.route("/stripe/webhook", methods=["POST"])
@csrf.exempt
def stripe_webhook():
    if not os.getenv("STRIPE_SECRET_KEY"):
        return jsonify({"error": "not configured"}), 503
    import stripe as stripe_lib
    stripe_lib.api_key = os.getenv("STRIPE_SECRET_KEY")
    payload = request.get_data()
    sig     = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe_lib.Webhook.construct_event(
            payload, sig, os.getenv("STRIPE_WEBHOOK_SECRET", "")
        )
    except Exception as e:
        logger.error("Stripe webhook invalid: %s", e)
        return jsonify({"error": "Invalid signature"}), 400

    obj   = event["data"]["object"]
    etype = event["type"]

    if etype == "checkout.session.completed":
        if obj.get("payment_status") != "paid":
            return jsonify({"received": True})  # wait for invoice.payment_succeeded
        sid  = obj.get("client_reference_id") or obj.get("metadata", {}).get("shop_id")
        plan = obj.get("metadata", {}).get("plan", "pro")
        if sid:
            ensure_shop(sid, obj.get("metadata", {}).get("shop_name") or "Shop")
            set_premium(sid, obj.get("customer"), obj.get("subscription"), True, plan)
            logger.info("Premium activated for shop %s (plan=%s)", sid, plan)
            from core.analytics import capture as _ph_capture
            _ph_capture(sid, "plan_upgraded", {"plan": plan, "source": "stripe"})

    elif etype == "invoice.payment_succeeded":
        # Renewal — and self-heal if our DB was reset since purchase. The shop_id
        # rides on the subscription metadata we stamp at checkout.
        sid, plan = _shop_and_plan_from_invoice(obj)
        if sid:
            ensure_shop(sid, "Shop")
            set_premium(sid, obj.get("customer"), obj.get("subscription"), True, plan)
            logger.info("Premium reaffirmed for shop %s (plan=%s, renewal)", sid, plan)

    elif etype == "customer.subscription.updated":
        # Plan change / cancel-at-period-end toggle / reactivation.
        sub    = obj
        status = sub.get("status")
        active = status in ("active", "trialing")
        plan   = (sub.get("metadata") or {}).get("plan", "pro")
        sid    = (sub.get("metadata") or {}).get("shop_id")
        if not sid:
            s   = get_shop_by_stripe_customer(sub.get("customer"))
            sid = s["shop_id"] if s else None
        if sid:
            ensure_shop(sid, "Shop")
            set_premium(sid, sub.get("customer"), sub.get("id"), active, plan)
            logger.info("Subscription updated for shop %s (status=%s, active=%s)", sid, status, active)

    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        sid = (obj.get("metadata") or {}).get("shop_id")
        if not sid:
            s   = get_shop_by_stripe_customer(obj.get("customer"))
            sid = s["shop_id"] if s else None
        if sid:
            set_premium(sid, obj.get("customer"), obj.get("id"), False)
            logger.info("Premium deactivated for shop %s", sid)
            from core.analytics import capture as _ph_capture
            _ph_capture(sid, "plan_cancelled", {"event_type": etype})

    return jsonify({"received": True})


def _shop_and_plan_from_invoice(invoice: dict):
    """Best-effort (shop_id, plan) from an invoice. Prefers the subscription
    metadata we stamp at checkout, then falls back to our customer mapping."""
    sd   = (invoice.get("subscription_details") or {}).get("metadata") or {}
    sid  = sd.get("shop_id")
    plan = sd.get("plan", "pro")
    if not sid:
        s = get_shop_by_stripe_customer(invoice.get("customer"))
        if s:
            sid  = s["shop_id"]
            plan = s.get("plan") or "pro"
    return sid, plan
