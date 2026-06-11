"""AI generation engine: prompt building, provider dispatch, JSON parsing.

Pure logic — no Flask/session dependencies. Providers (Gemini/OpenAI/NVIDIA) are
imported lazily inside each function so the module loads without their SDKs.
"""
import os
import json
import base64

from core.config import logger, ALLOW_PAID_OPENAI
from db import get_template


def provider_chain(premium=False):
    # Gemini first for both tiers — reliable, fast, free quota
    # NVIDIA as fallback (free but has empty-response issues under load)
    # Premium also gets OpenAI as second option
    if premium:
        return ["gemini", "openai", "nvidia"]
    return ["gemini", "nvidia"]

AI_PROMPT = """You are an expert Etsy listing copywriter for a handmade artisan shop.

Analyze the product image(s) and any hint provided. Generate an optimized Etsy listing.

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "title": "string — max 140 chars, 4-6 keyword phrases separated by commas",
  "description": "string — 180-250 words, warm personal tone, mention handmade to order, customizable, end with CTA to message before ordering",
  "tags": ["exactly 13 tags", "short search phrases", "1-3 words each"],
  "materials": ["list", "of", "materials"],
  "colors": ["visible or likely available colors/variants, e.g. White", "Black", "Custom"],
  "suggested_price_eur": number,
  "taxonomy_query": "most specific Etsy category name, e.g. 'Crocheted Hats' or 'Wall Hangings'",
  "personalization_instructions": "short prompt shown to buyer for custom color/size",
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags for Instagram"
}"""

_LANG_NAMES = {"en": "English", "de": "German", "tr": "Turkish"}
_TURKISH_PLATFORMS = {"trendyol", "hepsiburada", "n11"}

PLATFORM_PROMPTS = {
    "etsy": AI_PROMPT,

    "shopify": """You are an expert Shopify product listing copywriter for a handmade artisan shop.

Analyze the product image(s) and any hint provided. Generate an optimized Shopify product listing.

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "title": "string — max 70 chars, clear product name with key descriptors, SEO-friendly",
  "description_html": "string — 300-500 words as HTML using <p>, <ul>, <li> tags. Cover what it is, key features, materials/craftsmanship, use cases, care instructions. Warm brand voice.",
  "seo_title": "string — max 60 chars, primary keyword first",
  "seo_description": "string — max 155 chars, compelling with primary keyword",
  "tags": ["relevant", "search", "tags"],
  "product_type": "product category e.g. 'Knitwear' or 'Wall Art'",
  "colors": ["available", "colors"],
  "sizes": ["S", "M", "L", "Custom"],
  "suggested_price_eur": number,
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags"
}""",

    "amazon": """You are an expert Amazon product listing specialist who maximizes search ranking and conversion.

Analyze the product image(s) and any hint provided. Generate an optimized Amazon listing.

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "title": "string — max 200 chars. Format: Brand + Product Type + Key Feature + Color/Size. Keyword-rich. No ALL CAPS, no 'Best' or 'Sale' phrases.",
  "bullet_points": [
    "MATERIAL & QUALITY: detail about craftsmanship and materials — max 200 chars",
    "HANDCRAFTED: unique artisan detail and what makes it special",
    "CUSTOMIZABLE: personalization options available",
    "PERFECT GIFT: ideal occasions and gift-giving angle",
    "CARE INSTRUCTIONS: how to care for the product + dimensions if known"
  ],
  "description": "string — max 2000 chars, 2-3 paragraphs. Product story, expanded features. <b> tags allowed.",
  "backend_keywords": "string — max 250 bytes total. Space-separated keywords NOT in title or bullets. No repetition.",
  "brand": "suggested brand/shop name",
  "key_features": ["5-7 main product features"],
  "suggested_price_usd": number,
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags"
}""",

    "trendyol": """Sen Türk e-ticaret pazarı Trendyol için uzman bir ürün listesi oluşturucususun.

Ürün görsellerini ve varsa ipucunu analiz et. Optimize edilmiş bir Trendyol ürün ilanı oluştur.

Başlık kuralları: Maks 65 karakter (kesin sınır). Format: Marka + Ürün Tipi + Temel Özellik + Varyant.
Yasak: BÜYÜK HARF kullanımı, emoji, "en ucuz"/"kampanya" gibi pazarlama ifadeleri, kelime tekrarı.
Türkçe karakterler doğru kullanılmalı (ı, ş, ğ, ü, ö, ç).

SADECE geçerli JSON döndür — markdown, kod bloğu veya fazladan metin yok:
{
  "title": "string — TAM OLARAK maks 65 karakter Türkçe, Marka+Ürün Tipi+Temel Özellik+Varyant formatı, büyük harf YOK",
  "description": "string — 150-300 kelime Türkçe. Yapı: 1)Ürün kimliği 2)Malzeme/özellikler 3)Kullanım alanları 4)Bakım talimatları. <p> ve <ul> HTML etiketleri kullan.",
  "key_features": ["5-7 Türkçe madde", "ana ürün özelliklerini kısa ve net vurgula"],
  "category_name": "Trendyol kategorisi, örn. 'El Örgüsü' veya 'Duvar Dekoru'",
  "attributes": {"Renk": "ana renk", "Materyal": "ana materyal", "Ürün Tipi": "ürün tipi"},
  "suggested_price_try": number,
  "stock_code": "SKU önerisi örn. SHOP-001",
  "cargo_size": "S, M veya L (ürün boyutuna göre)",
  "instagram_caption": "150-200 karakter Türkçe Instagram başlığı, 2-3 emoji ve 5-8 Türkçe hashtag"
}""",

    "hepsiburada": """Sen Türk e-ticaret pazarı Hepsiburada için uzman bir ürün listesi oluşturucususun.

Ürün görsellerini ve varsa ipucunu analiz et. Optimize edilmiş bir Hepsiburada ürün ilanı oluştur.

Başlık kuralları: Sistem sınırı 255 karakter, ancak arama sonuçlarında yalnızca ilk 150 karakter görünür.
Format: Marka + Cinsiyet (giysi ise) + Ürün Özelliği + Ürün Tipi + Model/Kod. Kelime tekrarı yasak.
Yasak başlık ifadeleri: "en uygun fiyat", "kampanya", "ücretsiz kargo". Türkçe karakterler doğru kullanılmalı.

SADECE geçerli JSON döndür — markdown, kod bloğu veya fazladan metin yok:
{
  "title": "string — maks 150 karakter Türkçe (ilk 150 karakter arama sonuçlarında görünür), Marka+ÜrünTipi+AnaÖzellikler formatı, pazarlama ifadesi YOK",
  "description": "string — 150-300 kelime Türkçe. Yapı: 1)2-3 cümle özet 2)Teknik özellikler listesi 3)Kullanım senaryoları 4)Bakım/kurulum talimatları. <p> ve <ul> HTML etiketleri kullan.",
  "key_features": ["5 Türkçe madde", "ana satış noktalarını vurgula"],
  "category_name": "Hepsiburada kategorisi Türkçe",
  "attributes": {"Renk": "renk", "Materyal": "materyal", "Marka": "marka adı"},
  "merchant_sku": "satıcı stok kodu önerisi",
  "suggested_price_try": number,
  "warranty_period": "0 Ay",
  "instagram_caption": "150-200 karakter Türkçe Instagram başlığı, 2-3 emoji ve 5-8 Türkçe hashtag"
}""",

    "n11": """Sen Türk e-ticaret pazarı n11.com için uzman bir ürün listesi oluşturucususun.

Ürün görsellerini ve varsa ipucunu analiz et. Optimize edilmiş bir n11 ürün ilanı oluştur.

n11 İlan Kalite Puanı sistemi: Başlık (31-65 karakter = tam puan), Alt Başlık (31-65 karakter = tam puan),
açıklama zenginliği (kalın metin, madde işaretleri). Her iki başlık alanı da doldurulmalı.
Türkçe karakterler doğru kullanılmalı. Pazarlama ifadeleri (en ucuz, kampanya) başlıkta yasak.

SADECE geçerli JSON döndür — markdown, kod bloğu veya fazladan metin yok:
{
  "title": "string — TAM OLARAK 31-65 karakter Türkçe (kalite puanı için kritik), arama odaklı, Marka+ÜrünTipi+AnaÖzellik formatı",
  "subtitle": "string — TAM OLARAK 31-65 karakter Türkçe, farklı anahtar kelime açısı kullanan ikincil başlık",
  "description": "string — 100-250 kelime Türkçe, **kalın metin** ve madde işaretleri kullan (kalite puanı artırır)",
  "tags": ["10-15 Türkçe arama etiketi", "1-3 kelime"],
  "category_name": "n11 kategorisi Türkçe",
  "attributes": {"Renk": "renk", "Materyal": "materyal"},
  "suggested_price_try": number,
  "stock_code": "stok kodu önerisi",
  "instagram_caption": "150-200 karakter Türkçe Instagram başlığı, 2-3 emoji ve 5-8 Türkçe hashtag"
}""",

    "ebay": """You are an expert eBay listing copywriter who maximizes search visibility and buyer trust.

Analyze the product image(s) and any hint provided. Generate an optimized eBay listing.

Title rules: 80 chars HARD limit. Use ALL 80 characters — titles with 65+ chars sell 1.5× more.
Structure: Brand + Model + Key Attributes (material, color, size) + Condition/Type. No ALL CAPS. No "LOOK!" or "WOW!".
Item Specifics are critical — Cassini search uses them for filtered search. Fill every field.
Description is for buyer conversion only (not ranking).

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "title": "string — EXACTLY max 80 chars (use all of them). Brand+KeyFeature+Material+Color format. No ALL CAPS.",
  "subtitle": "string — max 55 chars, key differentiator or secondary selling point",
  "description_html": "string — 200-400 words as HTML with <h3>, <p>, <ul> tags. Cover description, features, materials, dimensions, handling note.",
  "item_specifics": [
    {"name": "Material", "value": "primary material"},
    {"name": "Color", "value": "main color"},
    {"name": "Style", "value": "style description"},
    {"name": "Handmade", "value": "Yes"},
    {"name": "Country/Region of Manufacture", "value": "country"}
  ],
  "condition": "New",
  "suggested_price_usd": number,
  "category_name": "most specific eBay category name",
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags"
}""",

    "woocommerce": """You are an expert WooCommerce product listing copywriter.

Analyze the product image(s) and any hint provided. Generate an optimized WooCommerce product listing.

SEO rules: Short description is shown above the fold (critical). Long description needs 300+ words for Google indexing.
Post-2025: Google quality systems penalize generic AI content — include specific materials, dimensions, use cases.
Meta title leads with primary keyword. Meta description formula: What it is + key attribute + trust signal.

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "title": "string — max 60 chars, clear H1 with primary keyword",
  "short_description": "string — 150-300 chars, 2-3 punchy bullet points or 1-2 sentences. What it is + key differentiator + spec.",
  "description_html": "string — 300-500 words as HTML. Structure: identity sentence → materials/specs → use cases → FAQ (3-5 questions). <h2>, <p>, <ul> tags.",
  "seo_title": "string — max 60 chars, primary keyword first",
  "seo_description": "string — max 155 chars. Formula: [Product type] + [key attribute] + [trust/logistics signal]",
  "tags": ["internal", "filter", "tags"],
  "product_type": "product category",
  "colors": ["available", "colors"],
  "sizes": ["available", "sizes"],
  "suggested_price_eur": number,
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags"
}""",

    "pinterest": """You are an expert Pinterest content strategist for product discovery and shopping.

Analyze the product image(s) and any hint provided. Generate optimized Pinterest pin content.

Pinterest rules: Only 40-60 chars of the title show in feed — lead with primary keyword.
Only 50-60 chars of the description show before truncation — front-load the keyword hook.
Alt text drives +25% impressions, +123% outbound clicks — treat it as seriously as title.
Use 2-5 hashtags (specific niche tags only, at the end).

Return ONLY valid JSON — no markdown, no code fences, no extra text:
{
  "pin_title": "string — max 60 chars. Primary keyword first, then product descriptor. Searchable, not clever.",
  "pin_description": "string — 100-300 chars total. Structure: keyword hook (0-60 chars) + benefit detail (61-220 chars) + soft CTA (221-280 chars) + 2-4 hashtags at end.",
  "alt_text": "string — max 125 chars. Specific: product type + key attribute + color/material. 'Blue handmade ceramic mug, 12oz, hand-thrown with rustic glaze.'",
  "board_name": "suggested Pinterest board name for best topical relevance",
  "hashtags": ["#specific", "#niche", "#hashtags", "#2to5only"],
  "instagram_caption": "150-200 char caption with 2-3 emojis and 5-8 hashtags"
}""",
}

def _build_prompt(hint: str, lang: str = "en", platform: str = "etsy") -> str:
    base = PLATFORM_PROMPTS.get(platform, AI_PROMPT)
    if lang != "en" and platform not in _TURKISH_PLATFORMS:
        lang_name = _LANG_NAMES.get(lang, "English")
        base += (
            f"\n\nIMPORTANT: Write ALL text fields in {lang_name}. "
            f"The listing targets {lang_name}-speaking buyers. "
            f"Keep category_name/taxonomy_query in the platform's primary search language."
        )
    if hint:
        base += f"\n\nSeller hint: {hint}"
    return base

def _style_hint_for_shop(sid: str) -> str:
    template = get_template(sid)
    parts = []
    for label, key in (
        ("Brand tone", "brand_tone"),
        ("Material phrases", "material_phrases"),
        ("Production time", "production_time"),
        ("Shipping note", "shipping_note"),
        ("Call to action", "brand_cta"),
    ):
        value = str(template.get(key, "")).strip()
        if value:
            parts.append(f"{label}: {value[:240]}")
    return "\n".join(parts)

def _merge_hint_with_style(hint: str, sid: str | None) -> str:
    if not sid:
        return hint
    style = _style_hint_for_shop(sid)
    if not style:
        return hint
    return (hint + "\n\n" if hint else "") + "Saved shop style:\n" + style

def _parse_ai_json(text):
    import re
    if not text:
        raise ValueError("AI returned empty response")
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Extract first JSON object/array via regex
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    # Strip markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
    return json.loads(text.strip())

def _gemini_generate(image_bytes_list, hint, api_key=None, lang="en", platform="etsy"):
    from google import genai as ggenai
    from google.genai import types as gtypes
    from google.genai.errors import ClientError
    client = ggenai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY", ""))
    prompt = _build_prompt(hint, lang, platform)
    parts  = [prompt] + [
        gtypes.Part.from_bytes(data=b, mime_type="image/jpeg")
        for b in image_bytes_list
    ]
    last_exc = None
    for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        try:
            resp = client.models.generate_content(model=model, contents=parts)
            try:
                return _parse_ai_json(resp.text)
            except (json.JSONDecodeError, ValueError) as je:
                logger.warning("Gemini JSON parse failed (model=%s): %s. Raw: %s", model, je, (resp.text or "")[:500])
                last_exc = je
                continue  # try the lite model before giving up
        except ClientError as e:
            s = str(e)
            if "429" in s or "RESOURCE_EXHAUSTED" in s:
                if model == "gemini-2.5-flash-lite":
                    raise RuntimeError("quota_exceeded") from e
                last_exc = e
                continue  # try lite
            if "404" in s or "no longer available" in s.lower():
                if model == "gemini-2.5-flash-lite":
                    raise RuntimeError("quota_exceeded") from e
                last_exc = e
                continue  # try lite
            raise
        except Exception as e:
            logger.warning("Gemini API error (model=%s): %s", model, e)
            last_exc = e
            continue
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("quota_exceeded")

def _openai_generate(image_bytes_list, hint, api_key=None, lang="en", platform="etsy"):
    from openai import OpenAI, RateLimitError
    client  = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY", ""), timeout=60.0)
    content = [{"type": "text", "text": _build_prompt(hint, lang, platform)}]
    for b in image_bytes_list:
        b64 = base64.b64encode(b).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
        )
    except RateLimitError as e:
        raise RuntimeError("quota_exceeded") from e
    return json.loads(resp.choices[0].message.content)

NVIDIA_MODELS = {
    "llama-90b": "meta/llama-3.2-90b-vision-instruct",
    "phi-3.5":   "microsoft/phi-3.5-vision-instruct",
}

def _nvidia_generate(image_bytes_list, hint, model_key="llama-90b", api_key=None, lang="en", platform="etsy"):
    from openai import OpenAI, RateLimitError, APITimeoutError
    import httpx
    client   = OpenAI(base_url="https://integrate.api.nvidia.com/v1",
                      api_key=api_key or os.getenv("NVIDIA_API_KEY", ""),
                      timeout=60.0)
    model_id = NVIDIA_MODELS.get(model_key, NVIDIA_MODELS["llama-90b"])
    b64      = base64.b64encode(image_bytes_list[0]).decode()
    prompt   = _build_prompt(hint, lang, platform)
    try:
        resp = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}", "detail": "high"
                }},
            ]}],
            max_tokens=3000, temperature=0.7,
        )
    except RateLimitError as e:
        raise RuntimeError("quota_exceeded") from e
    except (APITimeoutError, httpx.TimeoutException) as e:
        logger.warning("NVIDIA timeout, falling back: %s", e)
        raise RuntimeError("quota_exceeded") from e
    content = resp.choices[0].message.content if resp.choices else ""
    if not content or not content.strip():
        logger.warning("NVIDIA returned empty content, falling back")
        raise RuntimeError("quota_exceeded")
    return _parse_ai_json(content)

# Fallback order when a provider is over quota. OpenAI is paid, so it is opt-in.
_PROVIDER_CHAIN_FREE    = provider_chain(premium=False)
_PROVIDER_CHAIN_PREMIUM = provider_chain(premium=True)

def _run_provider(provider, image_bytes, hint, nvidia_model, api_key=None, lang="en", platform="etsy"):
    if provider == "gemini":
        return _gemini_generate(image_bytes, hint, api_key, lang, platform)
    elif provider == "nvidia":
        return _nvidia_generate(image_bytes, hint, nvidia_model, api_key, lang, platform)
    else:
        return _openai_generate(image_bytes, hint, api_key, lang, platform)

def _run_text_json(prompt: str):
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
    raise RuntimeError("no_text_ai_provider")
