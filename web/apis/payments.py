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
from db import (
    can_generate, get_shop, set_premium, get_shop_by_stripe_customer, ensure_shop,
    log_payment_event,
)

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
        log_payment_event(
            "checkout_failed",
            shop_id=usage_shop_id(),
            shop_name=session.get("shop_name", ""),
            plan=base_plan,
            surface="web",
            domain=request.host,
            detail={"reason": "missing_price", "price_env": price_env},
        )
        return jsonify({"error": "Stripe price not configured for this plan"}), 503
    try:
        import stripe as stripe_lib
        stripe_lib.api_key = os.getenv("STRIPE_SECRET_KEY")
        base = request.url_root.rstrip("/")
        sid = str(usage_shop_id())
        shop_name_val = session.get("shop_name", "")
        log_payment_event(
            "checkout_started",
            shop_id=sid,
            shop_name=shop_name_val,
            plan=base_plan,
            surface="web",
            domain=request.host,
            detail={"requested_plan": plan, "price_env": price_env},
        )
        checkout = stripe_lib.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=base + f"/upgrade?success=1&plan={plan}",
            cancel_url=base  + "/upgrade?cancelled=1",
            client_reference_id=sid,
            metadata={"shop_id": sid,
                      "shop_name": shop_name_val,
                      "plan": base_plan},
            # Stamp the subscription too, so renewal/update webhooks can recover
            # the shop even if our DB was reset between purchase and renewal.
            subscription_data={"metadata": {"shop_id": sid,
                                            "plan": base_plan}},
        )
        stripe_session_id = getattr(checkout, "id", None)
        if not isinstance(stripe_session_id, str):
            stripe_session_id = None
        log_payment_event(
            "checkout_created",
            shop_id=sid,
            shop_name=shop_name_val,
            plan=base_plan,
            surface="web",
            domain=request.host,
            stripe_session_id=stripe_session_id,
            detail={"requested_plan": plan, "price_env": price_env},
        )
        return jsonify({"url": checkout.url})
    except Exception as e:
        log_payment_event(
            "checkout_failed",
            shop_id=usage_shop_id(),
            shop_name=session.get("shop_name", ""),
            plan=base_plan,
            surface="web",
            domain=request.host,
            detail={"reason": safe_error(str(e)), "price_env": price_env},
        )
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
        log_payment_event(
            "checkout_failed",
            shop_id=usage_shop_id(),
            plan=plan.replace("_annual", ""),
            surface="mobile",
            domain=request.host,
            detail={"reason": "missing_price", "price_env": price_env},
        )
        return jsonify({"error": "Stripe price not configured for this plan"}), 503
    base_plan = plan.replace("_annual", "")
    sid = str(usage_shop_id())
    mob = _mobile_auth()
    shop_name_val = (mob or {}).get("shop_name", "") or session.get("shop_name", "")
    base = request.url_root.rstrip("/")
    try:
        import stripe as stripe_lib
        stripe_lib.api_key = os.getenv("STRIPE_SECRET_KEY")
        log_payment_event(
            "checkout_started",
            shop_id=sid,
            shop_name=shop_name_val,
            plan=base_plan,
            surface="mobile",
            domain=request.host,
            detail={"requested_plan": plan, "price_env": price_env},
        )
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
        stripe_session_id = getattr(checkout, "id", None)
        if not isinstance(stripe_session_id, str):
            stripe_session_id = None
        log_payment_event(
            "checkout_created",
            shop_id=sid,
            shop_name=shop_name_val,
            plan=base_plan,
            surface="mobile",
            domain=request.host,
            stripe_session_id=stripe_session_id,
            detail={"requested_plan": plan, "price_env": price_env},
        )
        return jsonify({"url": checkout.url})
    except Exception as e:
        log_payment_event(
            "checkout_failed",
            shop_id=sid,
            shop_name=shop_name_val,
            plan=base_plan,
            surface="mobile",
            domain=request.host,
            detail={"reason": safe_error(str(e)), "price_env": price_env},
        )
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

@bp.route("/billing/portal", methods=["POST"])
@limiter.limit("10 per minute")
def billing_portal():
    """Create a Stripe customer portal session so users can manage / cancel."""
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    if not os.getenv("STRIPE_SECRET_KEY"):
        return jsonify({"error": "Stripe not configured"}), 503
    sid = usage_shop_id()
    shop = get_shop(sid) or {}
    customer_id = shop.get("stripe_customer_id")
    if not customer_id:
        return jsonify({"error": "No billing account found"}), 404
    try:
        import stripe as stripe_lib
        stripe_lib.api_key = os.getenv("STRIPE_SECRET_KEY")
        base = request.url_root.rstrip("/")
        portal = stripe_lib.billing_portal.Session.create(
            customer=customer_id,
            return_url=base + "/upgrade",
        )
        return jsonify({"url": portal.url})
    except Exception as e:
        logger.exception("Stripe portal error: %s", e)
        return jsonify({"error": safe_error(str(e))}), 500


@bp.route("/stripe/webhook", methods=["POST"])
@csrf.exempt
def stripe_webhook():
    if not os.getenv("STRIPE_SECRET_KEY"):
        return jsonify({"error": "not configured"}), 503
    import stripe as stripe_lib
    stripe_lib.api_key = os.getenv("STRIPE_SECRET_KEY")
    payload = request.get_data()
    sig     = request.headers.get("Stripe-Signature", "")
    event = None
    last_error = None
    for secret in _webhook_secret_candidates():
        try:
            event = stripe_lib.Webhook.construct_event(payload, sig, secret)
            event = _stripe_to_plain(event)
            break
        except Exception as e:
            last_error = e
    if event is None:
        logger.error("Stripe webhook invalid: %s", last_error)
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
            log_payment_event(
                "checkout_completed",
                shop_id=sid,
                shop_name=obj.get("metadata", {}).get("shop_name"),
                plan=plan,
                stripe_session_id=obj.get("id"),
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=obj.get("subscription"),
                detail={"payment_status": obj.get("payment_status")},
            )
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
            log_payment_event(
                "invoice_payment_succeeded",
                shop_id=sid,
                plan=plan,
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=obj.get("subscription"),
            )
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
            log_payment_event(
                "subscription_updated",
                shop_id=sid,
                plan=plan,
                stripe_customer_id=sub.get("customer"),
                stripe_subscription_id=sub.get("id"),
                detail={"status": status, "active": active},
            )
            logger.info("Subscription updated for shop %s (status=%s, active=%s)", sid, status, active)

    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        sid = (obj.get("metadata") or {}).get("shop_id")
        if not sid:
            s   = get_shop_by_stripe_customer(obj.get("customer"))
            sid = s["shop_id"] if s else None
        if sid:
            set_premium(sid, obj.get("customer"), obj.get("id"), False)
            log_payment_event(
                "subscription_cancelled",
                shop_id=sid,
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=obj.get("id"),
                detail={"event_type": etype},
            )
            logger.info("Premium deactivated for shop %s", sid)
            from core.analytics import capture as _ph_capture
            _ph_capture(sid, "plan_cancelled", {"event_type": etype})

    elif etype == "invoice.payment_failed":
        # Renewal failed — revoke premium so the user sees the paywall
        sid, plan = _shop_and_plan_from_invoice(obj)
        if sid:
            set_premium(sid, obj.get("customer"), obj.get("subscription"), False)
            log_payment_event(
                "invoice_payment_failed",
                shop_id=sid,
                plan=plan,
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=obj.get("subscription"),
            )
            logger.warning("Payment failed, premium revoked for shop %s", sid)
            from core.analytics import capture as _ph_capture
            _ph_capture(sid, "payment_failed", {"plan": plan})

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


def _stripe_to_plain(value):
    if isinstance(value, dict):
        return {k: _stripe_to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stripe_to_plain(v) for v in value]
    data = getattr(value, "_data", None)
    if isinstance(data, dict):
        return {k: _stripe_to_plain(v) for k, v in data.items()}
    return value


def _webhook_secret_candidates() -> list[str]:
    host = request.host or ""
    preferred = []
    if "easylisting" in host:
        preferred.append("STRIPE_WEBHOOK_SECRET_EUR")
    if "kolaylistele" in host:
        preferred.append("STRIPE_WEBHOOK_SECRET_TRY")
    preferred.extend([
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_WEBHOOK_SECRET_EUR",
        "STRIPE_WEBHOOK_SECRET_TRY",
    ])

    secrets = []
    for key in preferred:
        val = os.getenv(key, "")
        if val and val not in secrets:
            secrets.append(val)
    return secrets or [""]
