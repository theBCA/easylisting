"""Static / informational pages: unsubscribe, legal, health check."""
from flask import Blueprint, request, render_template, jsonify

from db import unsubscribe_by_token

bp = Blueprint("pages", __name__)


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
