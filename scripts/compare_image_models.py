"""
Compare gemini-2.5-flash-image vs gemini-3.1-flash-image vs imagen-4-fast for Etsy shots.

Usage:
    cd /Users/berk.arslan/Desktop/easylisting
    python scripts/compare_image_models.py path/to/product.jpg [shot_number]

    Omit shot_number to run all 6 shots.
    Shot types:
      1 Worn / In-Use
      2 Studio Product Shot
      3 Macro Detail
      4 Back / Construction
      5 Lifestyle Flat Lay
      6 Packaging / Gift Shot

Output: scripts/compare_output/
Models:
  A) gemini-2.5-flash-image   — current production (image-to-image, ~$0.039/shot)
  B) gemini-3.1-flash-image   — newer model (image-to-image, check pricing)
  C) imagen-4-fast            — text-to-image only, no product reference (~$0.02/shot)
"""

import sys, os, base64, pathlib
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "web"))

from dotenv import load_dotenv
load_dotenv(ROOT / "web" / ".env")
for _p in [pathlib.Path(".env"), pathlib.Path("web/.env")]:
    if _p.exists():
        load_dotenv(_p, override=False)

from google import genai as ggenai
from google.genai import types as gtypes

API_KEY = os.getenv("GEMINI_API_KEY", "")
if not API_KEY:
    sys.exit("GEMINI_API_KEY not set")

OUT_DIR = pathlib.Path(__file__).parent / "compare_output"
OUT_DIR.mkdir(exist_ok=True)

SHOTS = {
    1: ("Worn / In-Use",      "Worn or in-use scale shot: product being worn, held, or used in a realistic human-scale context. Cozy, neutral, natural setting."),
    2: ("Studio Shot",        "Clean studio product shot on a pure white or very light neutral background. Entire product clearly visible, centered, full product in frame."),
    3: ("Macro Detail",       "Extreme close-up macro shot showing craftsmanship and material texture: stitching, fibers, finish, pattern, or handmade construction details."),
    4: ("Back/Construction",  "Backside and construction shot: reverse side, closure mechanism, seam quality, lining, or construction quality clearly visible."),
    5: ("Lifestyle Flat Lay", "Lifestyle flat lay: product styled on a textured or themed surface with tasteful props connected to its purpose."),
    6: ("Packaging/Gift",     "Packaging and gift-ready shot: product beautifully packaged with ribbon, kraft box, tissue paper, wrapping, or a handmade thank-you insert."),
}

PROMPT_SUFFIX = (
    " Preserve exact product type, colors, proportions, texture, pattern, and handmade character from the reference image. "
    "Do not change the product design. Improve only photography, lighting, styling, and presentation. "
    "One standalone Etsy listing photo only. No collage, no grid, no labels, no text overlay. "
    "Soft natural lighting, realistic Etsy product photography, clean composition."
)


def load_image(path: str) -> tuple:
    p = pathlib.Path(path)
    data = p.read_bytes()
    mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return data, mime


def describe_product(img_bytes: bytes, mime: str) -> str:
    client = ggenai.Client(api_key=API_KEY)
    cfg = gtypes.GenerateContentConfig(
        thinking_config=gtypes.ThinkingConfig(thinking_budget=0),
        automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(disable=True),
        http_options=gtypes.HttpOptions(timeout=30_000),
    )
    img_part = gtypes.Part.from_bytes(data=img_bytes, mime_type=mime)
    resp = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=["Describe this handmade Etsy product in one sentence for photography prompts. Include: product type, main colors, key materials, texture, and defining visual features. Be concise and specific. No bullet points, no headings.", img_part],
        config=cfg,
    )
    return (resp.text or "handmade product").strip()


def _extract_gemini_image(resp, model: str) -> bytes:
    for cand in (resp.candidates or []):
        for part in (getattr(getattr(cand, "content", None), "parts", None) or []):
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                d = inline.data
                return d if isinstance(d, bytes) else base64.b64decode(d)
    raise RuntimeError(f"{model} returned no image")


def run_gemini_image(model: str, img_bytes: bytes, mime: str, prompt: str) -> bytes:
    client = ggenai.Client(api_key=API_KEY)
    img_part = gtypes.Part.from_bytes(data=img_bytes, mime_type=mime)
    cfg = gtypes.GenerateContentConfig(
        response_modalities=["IMAGE"],
        automatic_function_calling=gtypes.AutomaticFunctionCallingConfig(disable=True),
        http_options=gtypes.HttpOptions(timeout=90_000),
    )
    resp = client.models.generate_content(
        model=model,
        contents=[prompt, img_part],
        config=cfg,
    )
    return _extract_gemini_image(resp, model)


def run_imagen_fast(prompt: str) -> bytes:
    client = ggenai.Client(api_key=API_KEY)
    resp = client.models.generate_images(
        model="imagen-4.0-fast-generate-001",
        prompt=prompt,
        config=gtypes.GenerateImagesConfig(number_of_images=1, aspect_ratio="1:1"),
    )
    for img in (resp.generated_images or []):
        raw = getattr(img, "image", None)
        if raw:
            data = getattr(raw, "image_bytes", None) or getattr(raw, "data", None)
            if data:
                return data if isinstance(data, bytes) else base64.b64decode(data)
    raise RuntimeError("imagen-4-fast returned no image")


def save_image(data: bytes, stem: str) -> pathlib.Path:
    ext = ".png" if data[:4] == b'\x89PNG' else ".jpg"
    p = OUT_DIR / (stem + ext)
    p.write_bytes(data)
    return p


def run_shot(shot: int, img_bytes: bytes, mime: str, description: str):
    shot_name, shot_concept = SHOTS[shot]
    gemini_prompt = shot_concept + PROMPT_SUFFIX + f" Product: {description}."
    imagen_prompt = (
        f"Professional Etsy product photography. {shot_concept}. "
        f"Product: {description}. "
        "Realistic handmade product photo, soft natural lighting, clean composition, "
        "no text overlay, no collage, single product shot."
    )

    results = {}

    def task_gemini25(p=gemini_prompt):
        return ("A_gemini-2.5", run_gemini_image("gemini-2.5-flash-image", img_bytes, mime, p))

    def task_gemini31(p=gemini_prompt):
        return ("B_gemini-3.1", run_gemini_image("gemini-3.1-flash-image", img_bytes, mime, p))

    def task_imagen(p=imagen_prompt):
        return ("C_imagen4-fast", run_imagen_fast(p))

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(task_gemini25), pool.submit(task_gemini31), pool.submit(task_imagen)]
        for fut in as_completed(futures):
            try:
                label, data = fut.result()
                path = save_image(data, f"shot{shot}_{label}")
                results[label] = path
                print(f"  [{shot}] {label} → {path.name}")
            except Exception as e:
                print(f"  [{shot}] FAILED: {e}")

    return shot_name, results


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python compare_image_models.py path/to/product.jpg [shot 1-6]")

    img_path = sys.argv[1]
    shots_to_run = [int(sys.argv[2])] if len(sys.argv) > 2 else list(range(1, 7))

    print(f"\nInput: {img_path}")
    print(f"Shots: {shots_to_run}")
    print(f"Output: {OUT_DIR}/\n")

    img_bytes, mime = load_image(img_path)

    print("Describing product...")
    description = describe_product(img_bytes, mime)
    print(f"Description: {description}\n")

    print("Running all models in parallel per shot...\n")
    all_results = {}
    for shot in shots_to_run:
        shot_name = SHOTS[shot][0]
        print(f"Shot {shot}: {shot_name}")
        name, results = run_shot(shot, img_bytes, mime, description)
        all_results[shot] = (name, results)

    print(f"\n{'='*60}")
    print(f"Done. Open {OUT_DIR}/ to compare.")
    print(f"\nFiles saved:")
    for shot, (name, results) in sorted(all_results.items()):
        print(f"\n  Shot {shot}: {name}")
        for label, path in sorted(results.items()):
            print(f"    {label:25s} → {path.name}")

    n = len(shots_to_run)
    print(f"\nEstimated cost: ~${0.039*n:.3f} (gemini-2.5) + ~$?? (gemini-3.1) + ~${0.02*n:.3f} (imagen-4-fast)")
    print("Open the compare_output/ folder and look at:")
    print("  A_ = gemini-2.5-flash-image (current production)")
    print("  B_ = gemini-3.1-flash-image (newer, potentially better)")
    print("  C_ = imagen-4-fast (cheapest, but no product reference — judge fidelity)")


if __name__ == "__main__":
    main()
