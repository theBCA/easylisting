#!/usr/bin/env python3
"""
Standalone smoke test for Gemini 2.5 Flash Image generation.

Use this to verify image generation works (and eyeball the quality/cost) BEFORE
deploying the photo-set feature. It calls the exact same code path the app uses
(`apis.photos._generate_product_image`) so a green run here means the live
endpoint will work too.

Usage:
    cd web
    GEMINI_API_KEY=... python ../scripts/test_image_gen.py path/to/product.jpg

    # or test the full 6-shot Etsy set (costs ~6 images ≈ $0.23):
    GEMINI_API_KEY=... python ../scripts/test_image_gen.py product.jpg --full-set

Outputs the generated image(s) to ./image_gen_test_output/ so you can open and
judge them. Each single image costs ~$0.039.
"""
import os
import sys
import base64
import argparse

# Make web/ importable whether run from repo root or web/.
HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "web")
sys.path.insert(0, WEB)


def _load_image_as_data_url(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def _save_data_url(data_url: str, out_path: str):
    raw = data_url.split(",", 1)[1] if "," in data_url else data_url
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(raw))


def main():
    parser = argparse.ArgumentParser(description="Smoke test Gemini image generation.")
    parser.add_argument("image", help="Path to a product photo (jpg/png/webp).")
    parser.add_argument("--full-set", action="store_true",
                        help="Generate all 6 Etsy shots (≈ $0.23) instead of one studio shot.")
    args = parser.parse_args()

    if not os.getenv("GEMINI_API_KEY"):
        print("✗ GEMINI_API_KEY is not set. Export it and retry.")
        sys.exit(1)
    if not os.path.exists(args.image):
        print(f"✗ Image not found: {args.image}")
        sys.exit(1)

    # Imported here so sys.path is set first.
    from apis.photos import (
        _generate_product_image, _describe_product_image,
        _etsy_shot_prompt, _ETSY_SHOTS,
    )
    from core.config import GEMINI_IMAGE_MODEL

    out_dir = os.path.join(os.getcwd(), "image_gen_test_output")
    os.makedirs(out_dir, exist_ok=True)

    print(f"Model: {GEMINI_IMAGE_MODEL}")
    src = _load_image_as_data_url(args.image)

    print("→ Describing product (Gemini vision)…")
    description = _describe_product_image(src)
    print(f"  Product: {description}\n")

    shots = list(_ETSY_SHOTS.keys()) if args.full_set else [2]
    est_cost = len(shots) * 0.039
    print(f"Generating {len(shots)} image(s) — est. cost ≈ ${est_cost:.2f}\n")

    for shot in shots:
        label, _ = _ETSY_SHOTS[shot]
        print(f"→ Shot {shot}: {label} …", end=" ", flush=True)
        try:
            prompt = _etsy_shot_prompt(shot, description)
            result = _generate_product_image(src, prompt)
            out_path = os.path.join(out_dir, f"shot_{shot}.jpg")
            _save_data_url(result, out_path)
            print(f"✓ saved {out_path}")
        except Exception as e:
            print(f"✗ FAILED: {e}")

    print(f"\nDone. Open {out_dir}/ to inspect the results.")


if __name__ == "__main__":
    main()
