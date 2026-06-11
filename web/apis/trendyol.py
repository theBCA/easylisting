"""Trendyol marketplace integration + public image uploads."""
import os
import base64
import time

import requests
from flask import Blueprint, request, jsonify, send_from_directory

from extensions import csrf, limiter
from core.config import logger, HTTP_TIMEOUT
from core.validators import _is_valid_image_bytes, safe_error
from core.session import is_authorized, shop_id, guest_shop_id
from db import (
    get_platform_credentials, save_platform_credentials, delete_platform_credentials,
)

bp = Blueprint("trendyol", __name__)


# ── Static image uploads (public URLs for Trendyol etc.) ─────────────────────

# Project root is the parent of apis/ — keep uploads under <root>/static/uploads
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(_PROJECT_ROOT, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@bp.route("/uploads/<path:filename>")
def serve_upload(filename):
    safe = os.path.basename(filename)
    if safe != filename or not safe:
        return "", 404
    return send_from_directory(UPLOAD_DIR, safe)


def _save_image_for_upload(b64_data: str) -> str | None:
    import uuid as _uuid
    try:
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]
        img_bytes = base64.b64decode(b64_data)
        if len(img_bytes) > 10 * 1024 * 1024:
            return None
        if not _is_valid_image_bytes(img_bytes):
            return None
        fname = f"{_uuid.uuid4().hex}.jpg"
        fpath = os.path.join(UPLOAD_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(img_bytes)
        return fname
    except Exception:
        return None


# ── Trendyol integration ──────────────────────────────────────────────────────

TR_BASE = "https://apigw.trendyol.com"
_TR_CATEGORY_CACHE: dict = {}   # {supplier_id: {"data": [...], "ts": float}}


def _tr_headers(creds: dict) -> dict:
    import base64 as _b64
    raw   = f"{creds['api_key']}:{creds['api_secret']}"
    token = _b64.b64encode(raw.encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        "User-Agent":    f"{creds['supplier_id']} - EasyListing",
        "Content-Type":  "application/json",
    }


def _tr_get(path: str, creds: dict, params: dict = None):
    return requests.get(f"{TR_BASE}{path}", headers=_tr_headers(creds),
                        params=params, timeout=HTTP_TIMEOUT)


def _tr_post(path: str, creds: dict, payload: dict):
    return requests.post(f"{TR_BASE}{path}", headers=_tr_headers(creds),
                         json=payload, timeout=HTTP_TIMEOUT)


def _tr_creds() -> dict | None:
    return get_platform_credentials(str(shop_id() or guest_shop_id()), "trendyol")


@bp.route("/api/trendyol/connect", methods=["POST"])
@csrf.exempt
@limiter.limit("10 per minute")
def api_trendyol_connect():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    body        = request.get_json(silent=True) or {}
    supplier_id = str(body.get("supplier_id", "")).strip()
    api_key     = str(body.get("api_key", "")).strip()
    api_secret  = str(body.get("api_secret", "")).strip()
    if not all([supplier_id, api_key, api_secret]):
        return jsonify({"error": "Tüm alanlar zorunludur."}), 400

    creds = {"supplier_id": supplier_id, "api_key": api_key, "api_secret": api_secret}
    try:
        r = _tr_get(f"/integration/product/sellers/{supplier_id}/addresses", creds)
    except Exception as e:
        logger.error("Trendyol connect error: %s", e)
        return jsonify({"error": "Trendyol'a ulaşılamadı. Tekrar deneyin."}), 502
    if r.status_code in (401, 403):
        return jsonify({"error": "Geçersiz API bilgileri. Lütfen kontrol edin."}), 400
    if not r.ok and r.status_code not in (404,):
        return jsonify({"error": "Trendyol'a bağlanılamadı. Tekrar deneyin."}), 502

    sid = str(shop_id() or guest_shop_id())
    save_platform_credentials(sid, "trendyol", creds)
    return jsonify({"ok": True})


@bp.route("/api/trendyol/disconnect", methods=["POST"])
@csrf.exempt
@limiter.limit("10 per minute")
def api_trendyol_disconnect():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    sid = str(shop_id() or guest_shop_id())
    delete_platform_credentials(sid, "trendyol")
    return jsonify({"ok": True})


@bp.route("/api/trendyol/addresses")
@limiter.limit("30 per minute")
def api_trendyol_addresses():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    creds = _tr_creds()
    if not creds:
        return jsonify({"error": "not_connected"}), 400
    try:
        r = _tr_get(f"/integration/product/sellers/{creds['supplier_id']}/addresses", creds)
    except Exception as e:
        logger.error("Trendyol addresses error: %s", e)
        return jsonify({"error": "Adresler alınamadı."}), 502
    if not r.ok:
        return jsonify({"error": "Adresler alınamadı."}), 502
    data      = r.json()
    addresses = [
        {"id": a.get("id"), "label": a.get("fullAddress", str(a.get("id", "")))}
        for a in data.get("supplierAddresses", [])
    ]
    return jsonify({"addresses": addresses})


@bp.route("/api/trendyol/categories")
@limiter.limit("30 per minute")
def api_trendyol_categories():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    creds = _tr_creds()
    if not creds:
        return jsonify({"error": "not_connected"}), 400
    sid    = creds["supplier_id"]
    cached = _TR_CATEGORY_CACHE.get(sid)
    if cached and time.time() - cached["ts"] < 86400:
        return jsonify({"categories": cached["data"]})
    try:
        r = _tr_get("/integration/product-categories", creds)
    except Exception as e:
        logger.error("Trendyol categories error: %s", e)
        return jsonify({"error": "Kategoriler alınamadı."}), 502
    if not r.ok:
        return jsonify({"error": "Kategoriler alınamadı."}), 502
    cats = r.json().get("categories", [])
    _TR_CATEGORY_CACHE[sid] = {"data": cats, "ts": time.time()}
    return jsonify({"categories": cats})


@bp.route("/api/trendyol/categories/<int:cat_id>/attributes")
@limiter.limit("60 per minute")
def api_trendyol_cat_attributes(cat_id):
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    creds = _tr_creds()
    if not creds:
        return jsonify({"error": "not_connected"}), 400
    try:
        r = _tr_get(f"/integration/product-categories/{cat_id}/attributes", creds)
    except Exception as e:
        logger.error("Trendyol attributes error: %s", e)
        return jsonify({"error": "Özellikler alınamadı."}), 502
    if not r.ok:
        return jsonify({"error": "Özellikler alınamadı."}), 502
    return jsonify(r.json())


@bp.route("/api/trendyol/brands")
@limiter.limit("60 per minute")
def api_trendyol_brands():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    creds = _tr_creds()
    if not creds:
        return jsonify({"error": "not_connected"}), 400
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"brands": []})
    try:
        r = _tr_get("/integration/product/brands/by-name", creds,
                    params={"name": q, "page": 0, "size": 10})
    except Exception as e:
        logger.error("Trendyol brands error: %s", e)
        return jsonify({"brands": []})
    if not r.ok:
        return jsonify({"brands": []})
    brands = [{"id": b.get("id"), "name": b.get("name")} for b in r.json()]
    return jsonify({"brands": brands})


@bp.route("/api/trendyol/publish", methods=["POST"])
@csrf.exempt
@limiter.limit("10 per minute")
def api_trendyol_publish():
    if not is_authorized():
        return jsonify({"error": "Not connected"}), 401
    creds = _tr_creds()
    if not creds:
        return jsonify({"error": "not_connected"}), 400

    body    = request.get_json(silent=True) or {}
    barcode = str(body.get("barcode", "")).strip()
    try:
        list_price = float(body.get("list_price", 0))
        sale_price = float(body.get("sale_price", 0))
        vat_rate   = int(body.get("vat_rate", 18))
        quantity   = int(body.get("quantity", 1))
        brand_id   = int(body.get("brand_id", 0))
        cat_id     = int(body.get("category_id", 0))
        ship_addr  = int(body.get("shipment_address_id", 0))
        ret_addr   = int(body.get("returning_address_id", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Geçersiz fiyat veya ID değerleri."}), 400

    if not barcode:
        return jsonify({"error": "Barkod zorunludur."}), 400
    if sale_price <= 0 or list_price < sale_price:
        return jsonify({"error": "Liste fiyatı satış fiyatından küçük olamaz."}), 400
    if not all([brand_id, cat_id, ship_addr, ret_addr]):
        return jsonify({"error": "Marka, kategori ve adres seçimi zorunludur."}), 400

    stock_code  = str(body.get("stock_code", barcode)).strip() or barcode
    title       = str(body.get("title", "")).strip()[:65]
    description = str(body.get("description", "")).strip()
    attributes  = body.get("attributes", [])

    if not title or not description:
        return jsonify({"error": "Başlık ve açıklama zorunludur."}), 400

    # Save images to disk → generate public URLs
    previews   = body.get("image_previews", [])
    base_url   = request.host_url.rstrip("/")
    image_urls = []
    for b64 in previews[:5]:
        fname = _save_image_for_upload(b64)
        if fname:
            image_urls.append({"url": f"{base_url}/uploads/{fname}"})
    if not image_urls:
        return jsonify({"error": "En az bir geçerli görsel gerekli."}), 400

    payload = {
        "items": [{
            "barcode":            barcode,
            "title":              title,
            "productMainId":      stock_code,
            "brandId":            brand_id,
            "categoryId":         cat_id,
            "quantity":           quantity,
            "stockCode":          stock_code,
            "dimensionalWeight":  1,
            "description":        description,
            "images":             image_urls,
            "vatRate":            vat_rate,
            "listPrice":          list_price,
            "salePrice":          sale_price,
            "attributes":         attributes,
            "shipmentAddressId":  ship_addr,
            "returningAddressId": ret_addr,
        }]
    }

    sid = creds["supplier_id"]
    try:
        r = _tr_post(f"/integration/product/sellers/{sid}/v2/products", creds, payload)
    except Exception as e:
        logger.error("Trendyol publish error: %s", e)
        return jsonify({"error": "Trendyol'a bağlanılamadı."}), 502
    if not r.ok:
        logger.error("Trendyol publish rejected: %s", r.text[:500])
        try:
            msg = r.json().get("message") or "Trendyol'da ürün oluşturulamadı."
        except Exception:
            msg = "Trendyol'da ürün oluşturulamadı."
        return jsonify({"error": safe_error(msg)}), 400

    return jsonify({
        "ok":      True,
        "barcode": barcode,
        "message": "Ürün Trendyol'a gönderildi. Onay sürecindedir.",
    })
