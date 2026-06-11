"""Input validation, image sniffing, and error sanitization."""
from core.config import logger

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_TITLE_LEN = 140
MAX_DESC_LEN  = 10000
MAX_TAG_LEN   = 20
MAX_PRICE     = 50_000.0
MIN_PRICE     = 0.20

# Magic-byte signatures for allowed image types
_IMAGE_MAGIC = [
    b"\xff\xd8\xff",               # JPEG
    b"\x89PNG\r\n\x1a\n",          # PNG
    b"RIFF",                       # WebP (RIFF....WEBP)
    b"GIF87a", b"GIF89a",          # GIF
]


def _is_valid_image_bytes(data: bytes) -> bool:
    for sig in _IMAGE_MAGIC:
        if data[:len(sig)] == sig:
            return True
    return False


def safe_error(msg, public="Something went wrong. Please try again."):
    """Log full error server-side, return safe message to client."""
    logger.error(msg)
    return public


def validate_listing_input(data):
    """Validate and sanitize all listing fields. Returns (clean_data, error_str)."""
    title = str(data.get("title", "")).strip()[:MAX_TITLE_LEN]
    if not title:
        return None, "Title is required."

    description = str(data.get("description", "")).strip()[:MAX_DESC_LEN]
    if not description:
        return None, "Description is required."

    try:
        price = float(data.get("price", 0))
        if not (MIN_PRICE <= price <= MAX_PRICE):
            return None, f"Price must be between €{MIN_PRICE} and €{MAX_PRICE}."
    except (ValueError, TypeError):
        return None, "Invalid price."

    try:
        tax_id = int(data.get("taxonomy_id", 0))
        if tax_id <= 0:
            return None, "Invalid category."
    except (ValueError, TypeError):
        return None, "Invalid category."

    raw_tags = data.get("tags", [])
    tags = [str(t).strip()[:MAX_TAG_LEN] for t in raw_tags if str(t).strip()][:13]

    raw_mats = data.get("materials", [])
    materials = [str(m).strip()[:45] for m in raw_mats if str(m).strip()][:13]

    pi = str(data.get("personalization_instructions", "")).strip()[:256]

    sp = data.get("shipping_profile_id")
    shipping_profile_id = None
    if sp:
        try:
            shipping_profile_id = int(sp)
        except (ValueError, TypeError):
            pass

    return {
        "title":                      title,
        "description":                description,
        "price":                      price,
        "taxonomy_id":                tax_id,
        "tags":                       tags,
        "materials":                  materials,
        "personalization_instructions": pi,
        "shipping_profile_id":        shipping_profile_id,
        "image_previews":             data.get("image_previews", []),
    }, None
