"""Pro photo generation (variants + Etsy photo sets) via Gemini 2.5 Flash Image."""
import base64
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Blueprint, request, jsonify

from extensions import limiter
from core.config import logger, PHOTO_VARIANT_COUNT, GEMINI_IMAGE_MODEL
from core.validators import safe_error
from core.session import is_authorized, is_guest, has_premium_access, shop_id, guest_shop_id
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

def _data_url_to_bytes(data_url: str) -> tuple[bytes, str]:
    """Split a data: URL into (raw bytes, mime type)."""
    raw = data_url.split(",", 1)[1] if "," in data_url else data_url
    mime = "image/jpeg"
    if data_url.startswith("data:image/png"):
        mime = "image/png"
    elif data_url.startswith("data:image/webp"):
        mime = "image/webp"
    return base64.b64decode(raw), mime

def _generate_product_image(image_url: str, prompt: str) -> str:
    """Image-to-image edit via Gemini 2.5 Flash Image. Returns a data: URL.

    Keeps the uploaded product as the visual reference and restyles it per the
    prompt. Same google-genai SDK we use for text generation — no extra vendor.
    """
    from google import genai as ggenai
    from google.genai import types as gtypes

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("Image generation is not configured.")

    src_bytes, mime = _data_url_to_bytes(image_url)
    client = ggenai.Client(api_key=api_key)
    img_part = gtypes.Part.from_bytes(data=src_bytes, mime_type=mime)
    cfg = gtypes.GenerateContentConfig(
        response_modalities=["IMAGE"],
        automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(disable=True),
        http_options=gtypes.HttpOptions(timeout=90_000),
    )
    resp = client.models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=[prompt, img_part],
        config=cfg,
    )
    for cand in (resp.candidates or []):
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                out_mime = inline.mime_type or "image/png"
                data = inline.data
                if isinstance(data, str):  # already base64
                    return f"data:{out_mime};base64,{data}"
                return f"data:{out_mime};base64," + base64.b64encode(data).decode()
    raise RuntimeError("Image model returned no image.")

@bp.route("/api/generate-photos", methods=["POST"])
@limiter.limit("5 per minute")
def api_generate_photos():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    if not has_premium_access():
        return jsonify({"error": "premium_required"}), 403
    if not os.getenv("GEMINI_API_KEY"):
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
    labels = ("White background", "Lifestyle", "Seasonal gift")
    try:
        # Run in parallel — sequential image calls can exceed gunicorn's 120s timeout.
        variants: list[dict] = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(_generate_product_image, image_url, prompt): label
                for label, prompt in zip(labels, prompts)
            }
            for fut in as_completed(futures):
                label = futures[fut]
                variants.append({"label": label, "image": fut.result()})
        order = {label: i for i, label in enumerate(labels)}
        variants.sort(key=lambda v: order[v["label"]])
    except Exception as e:
        logger.exception("Photo variant error: %s", e)
        return jsonify({"error": safe_error(str(e))}), 500

    increment_photo_variant_usage(sid, len(variants))
    return jsonify({"variants": variants, "remaining": max(0, remaining - len(variants))})


# ── Etsy Photo Set (7-shot wizard) ───────────────────────────────────────────

_ETSY_SHOTS = {
    1: ("Worn / In-Use Shot",
        "Worn or in-use scale shot: product being worn, held, or used in a realistic human-scale context that helps Etsy buyers understand size. Cozy, neutral, natural setting appropriate for the product."),
    2: ("Studio Product Shot",
        "Clean studio product shot on a pure white or very light neutral background. Entire product clearly and accurately visible, centered, full product in frame."),
    3: ("Macro Detail Shot",
        "Extreme close-up macro shot showing craftsmanship and material texture: stitching, fibers, buttons, finish, pattern, weave, or handmade construction details."),
    4: ("Back / Construction Shot",
        "Backside and construction shot: reverse side, closure mechanism, button placket, seam quality, lining, attachment detail, or construction quality clearly visible."),
    5: ("Lifestyle Flat Lay",
        "Lifestyle flat lay: product styled on a textured or themed surface with tasteful props connected to its purpose — wedding, baby gift, home decor, fashion, holiday gifting."),
    6: ("Packaging / Gift Shot",
        "Packaging and gift-ready shot: product beautifully packaged with ribbon, kraft box, tissue paper, wrapping, or a handmade thank-you insert card."),
}

def _describe_product_image(image_b64: str) -> str:
    """Call Gemini vision to get a one-sentence product description."""
    try:
        from google import genai as ggenai
        from google.genai import types as gtypes
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            return "handmade product"
        raw = image_b64
        if "," in raw:
            raw = raw.split(",", 1)[1]
        image_bytes = base64.b64decode(raw)
        mime = "image/jpeg"
        if image_b64.startswith("data:image/png"):
            mime = "image/png"
        elif image_b64.startswith("data:image/webp"):
            mime = "image/webp"
        client = ggenai.Client(api_key=api_key)
        cfg = gtypes.GenerateContentConfig(
            thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
            automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(disable=True),
            http_options=gtypes.HttpOptions(timeout=30_000),
        )
        img_part = gtypes.Part.from_bytes(data=image_bytes, mime_type=mime)
        prompt = (
            "Describe this handmade Etsy product in one sentence for photography prompts. "
            "Include: product type, main colors, key materials, texture, and defining visual features. "
            "Be concise and specific. No bullet points, no headings."
        )
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=[prompt, img_part],
            config=cfg,
        )
        return (resp.text or "handmade product").strip()
    except Exception as e:
        logger.warning("Product description failed: %s", e)
        return "handmade product"

def _etsy_shot_prompt(shot: int, description: str) -> str:
    _, concept = _ETSY_SHOTS[shot]
    return (
        f"{concept}. "
        f"Product reference: {description}. "
        "Preserve exact product type, colors, proportions, texture, pattern, and handmade character from the reference image. "
        "Do not change the product design. Improve only photography, lighting, styling, and presentation. "
        "One standalone Etsy listing photo only. No collage, no grid, no multi-panel layout, no labels, no captions, no text overlay. "
        "Soft natural lighting, realistic Etsy product photography, shallow depth of field where appropriate, clean composition."
    )

@bp.route("/api/etsy-photo-set", methods=["POST"])
@limiter.limit("10 per minute")
def api_etsy_photo_set():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    if not has_premium_access():
        return jsonify({"error": "premium_required"}), 403
    if not os.getenv("GEMINI_API_KEY"):
        return jsonify({"error": "Photo generation is not configured yet."}), 503

    sid = str(shop_id()) if shop_id() else (guest_shop_id() if is_guest() else None)
    if not sid:
        return jsonify({"error": "Invalid shop"}), 400

    # Each photo-set request generates exactly one shot → costs one image credit.
    allowed, remaining = can_generate_photo_variants(sid, 1)
    if not allowed:
        return jsonify({"error": "photo_limit_reached", "remaining": remaining}), 403

    body = request.get_json(silent=True) or {}
    image_url = str(body.get("image", ""))
    shot = int(body.get("shot", 1))
    description = str(body.get("description", "")).strip()

    if not image_url.startswith("data:image/"):
        return jsonify({"error": "A product image is required."}), 400
    if shot not in _ETSY_SHOTS:
        return jsonify({"error": "Invalid shot number (1–6)."}), 400

    if not description:
        description = _describe_product_image(image_url)

    prompt = _etsy_shot_prompt(shot, description)
    try:
        generated = _generate_product_image(image_url, prompt)
    except Exception as e:
        logger.exception("Etsy photo set error (shot=%s): %s", shot, e)
        return jsonify({"error": safe_error(str(e))}), 500

    increment_photo_variant_usage(sid, 1)
    label, _ = _ETSY_SHOTS[shot]
    return jsonify({
        "image": generated,
        "description": description,
        "shot": shot,
        "label": label,
        "remaining": max(0, remaining - 1),
    })
