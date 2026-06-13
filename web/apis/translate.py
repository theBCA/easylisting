"""AI translation of listing fields."""
import os
import json

from flask import Blueprint, request, jsonify

from extensions import limiter
from core.config import logger, ALLOW_PAID_OPENAI
from core.ai import _parse_ai_json
from core.validators import safe_error
from core.session import (
    require_connection, has_premium_access, _consume_improve_allowance,
)
from db import increment_improve_usage

bp = Blueprint("translate", __name__)


def _translate_ai(fields: dict, target_lang: str) -> dict:
    lang_names = {"de": "German", "tr": "Turkish", "en": "English"}
    prompt = (
        f"Translate this Etsy listing to {lang_names[target_lang]}. "
        "Keep tags 1-3 words each. Keep title under 140 chars. "
        "Return ONLY valid JSON with the same keys:\n" + json.dumps(fields)
    )
    # Try Gemini, then optionally fall back to paid OpenAI if explicitly enabled.
    if os.getenv("GEMINI_API_KEY"):
        try:
            from google import genai as ggenai
            from google.genai.errors import ClientError
            client = ggenai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            resp = client.models.generate_content(model="gemini-2.5-flash", contents=[prompt])
            return _parse_ai_json(resp.text)
        except ClientError as e:
            if "429" not in str(e) and "RESOURCE_EXHAUSTED" not in str(e):
                raise
    if ALLOW_PAID_OPENAI and os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    raise RuntimeError("No AI provider available for translation")

@bp.route("/api/translate", methods=["POST"])
@limiter.limit("30 per minute")
def api_translate():
    redir = require_connection()
    if redir: return jsonify({"error": "Not connected"}), 401
    sid, err_resp, err_code = _consume_improve_allowance()
    if err_resp is not None:
        return err_resp, err_code
    body = request.get_json(silent=True) or {}
    lang = body.get("lang", "de")
    if lang not in ("de", "tr", "en"):
        return jsonify({"error": "Invalid language"}), 400
    fields = {
        "title":       str(body.get("title",       ""))[:140],
        "description": str(body.get("description", ""))[:3000],
        "tags":        [str(t)[:20] for t in body.get("tags", [])[:13]],
    }
    try:
        data = _translate_ai(fields, lang)
        if not has_premium_access():
            increment_improve_usage(sid, request.headers.get("CF-IPCountry"))
        return jsonify(data)
    except Exception as e:
        logger.exception("Translate error: %s", e)
        return jsonify({"error": safe_error(str(e))}), 500
