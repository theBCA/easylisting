"""Pro photo-variant generation via fal.ai."""
import base64

import requests
from flask import Blueprint, request, jsonify

from extensions import limiter
from core.config import logger, HTTP_TIMEOUT, FAL_KEY, PHOTO_VARIANT_COUNT
from core.validators import safe_error
from core.session import is_authorized, is_guest, has_pro_access, shop_id, guest_shop_id
from db import can_generate_photo_variants, increment_photo_variant_usage

bp = Blueprint("photos", __name__)


def _photo_variant_prompts(meta: dict) -> list[str]:
    name = str(meta.get("title") or "handmade product")[:120]
    material = ", ".join(meta.get("materials") or [])[:160] or "visible handmade materials"
    colors = ", ".join(meta.get("colors") or [])[:120] or "visible colors"
    category = str(meta.get("category") or meta.get("taxonomy_path") or "e-commerce")[:120]
    return [
        f"Professional product photography, {name}, {colors}, {material}, 85mm lens, soft diffused studio lighting, pure white background, centered product, sharp commercial e-commerce photo.",
        f"Professional lifestyle product photography, {name}, {material}, warm natural window light, minimalist {category} themed interior, product remains the clear hero, shallow depth of field.",
        f"E-commerce product photography, {name}, seasonal gift-ready scene, soft golden-hour lighting, tasteful background bokeh, product centered and unchanged, high resolution commercial shot.",
    ]

def _fal_generate_variant(image_url: str, prompt: str) -> str:
    resp = requests.post(
        "https://fal.run/fal-ai/flux-pro/kontext",
        headers={"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"},
        json={
            "prompt": prompt,
            "image_url": image_url,
            "num_images": 1,
            "sync_mode": True,
            "output_format": "jpeg",
            "aspect_ratio": "1:1",
            "guidance_scale": 3.5,
            "safety_tolerance": "2",
        },
        timeout=(10, 90),
    )
    if not resp.ok:
        logger.error("fal.ai image error: %s", resp.text[:500])
        raise RuntimeError("Photo generation provider failed.")
    data = resp.json()
    images = data.get("images") or []
    if not images:
        raise RuntimeError("Photo generation returned no images.")
    url = images[0].get("url", "")
    if url.startswith("data:image/"):
        return url
    img_resp = requests.get(url, timeout=HTTP_TIMEOUT)
    if not img_resp.ok:
        raise RuntimeError("Could not fetch generated photo.")
    return "data:image/jpeg;base64," + base64.b64encode(img_resp.content).decode()

@bp.route("/api/generate-photos", methods=["POST"])
@limiter.limit("5 per minute")
def api_generate_photos():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    if not has_pro_access():
        return jsonify({"error": "pro_required"}), 403
    if not FAL_KEY:
        return jsonify({"error": "Photo generation is not configured yet."}), 503

    sid = str(shop_id()) if shop_id() else (guest_shop_id() if is_guest() else None)
    if not sid:
        return jsonify({"error": "Invalid shop"}), 400
    allowed, remaining = can_generate_photo_variants(sid, PHOTO_VARIANT_COUNT)
    if not allowed:
        return jsonify({"error": "photo_limit_reached", "remaining": remaining}), 403

    body = request.get_json(silent=True) or {}
    image_url = str(body.get("image", ""))
    if not image_url.startswith("data:image/"):
        return jsonify({"error": "A generated or uploaded product image is required."}), 400

    prompts = _photo_variant_prompts(body)
    try:
        variants = [
            {"label": label, "image": _fal_generate_variant(image_url, prompt)}
            for label, prompt in zip(("White background", "Lifestyle", "Seasonal gift"), prompts)
        ]
    except Exception as e:
        logger.exception("Photo variant error: %s", e)
        return jsonify({"error": safe_error(str(e))}), 500

    increment_photo_variant_usage(sid, len(variants))
    return jsonify({"variants": variants, "remaining": max(0, remaining - len(variants))})
