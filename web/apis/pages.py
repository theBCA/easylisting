"""Static / informational pages: unsubscribe, legal, health check, feedback."""
import threading

from flask import Blueprint, request, render_template, jsonify, session

from extensions import limiter, csrf
from db import unsubscribe_by_token, save_feedback
from core.config import logger
from core.email import send_email

bp = Blueprint("pages", __name__)

_FEEDBACK_TO = "berkcemarslan@gmail.com"


@bp.route("/api/feedback", methods=["POST"])
@limiter.limit("5 per minute; 20 per day")
@csrf.exempt
def api_feedback():
    body     = request.get_json(silent=True) or {}
    message  = str(body.get("message", "")).strip()
    reply_to = str(body.get("reply_to", "")).strip()
    page     = str(body.get("page", "")).strip()
    if not message or len(message) < 3:
        return jsonify({"error": "Message too short"}), 400

    shop = session.get("shop_name") or session.get("shop_id") or "anonymous"
    sid  = session.get("shop_id") or "anon"

    def _store_and_notify():
        try:
            save_feedback(sid, message, reply_to or None, page or None)
        except Exception as e:
            logger.warning("save_feedback failed: %s", e)
        try:
            reply_line = f"\nReply to: {reply_to}" if reply_to else ""
            send_email(
                to=_FEEDBACK_TO,
                subject=f"[EasyListing Feedback] {shop}",
                body_text=(
                    f"Shop: {shop}\nPage: {page or '—'}{reply_line}\n\n"
                    f"---\n{message}\n---"
                ),
            )
        except Exception as e:
            logger.warning("feedback email failed: %s", e)

    threading.Thread(target=_store_and_notify, daemon=True).start()
    return jsonify({"success": True})


@bp.route("/unsubscribe")
def unsubscribe():
    token = request.args.get("token", "")
    if not token:
        return "Invalid unsubscribe link.", 400
    changed = unsubscribe_by_token(token)
    if changed:
        msg = (
            "Abonelikten çıkıldı. Artık pazarlama e-postası almayacaksınız."
            if "kolaylistele" in request.host
            else "You have been unsubscribed. You will no longer receive marketing emails."
        )
    else:
        msg = (
            "Bu bağlantı daha önce kullanılmış veya geçersiz."
            if "kolaylistele" in request.host
            else "This unsubscribe link has already been used or is invalid."
        )
    return f"<p style='font-family:sans-serif;padding:40px;font-size:16px;'>{msg}</p>", 200


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@bp.route("/terms")
def terms():
    return render_template("terms.html")


@bp.route("/health")
def health():
    return jsonify({"status": "ok"}), 200
