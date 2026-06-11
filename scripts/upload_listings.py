"""
Main script. Reads listings.csv and creates draft listings on Etsy.
Run: .venv/bin/python upload_listings.py
"""

import os
import time
import csv
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = f"{os.getenv('ETSY_API_KEY')}:{os.getenv('ETSY_SHARED_SECRET').strip()}"
ACCESS_TOKEN = os.getenv("ETSY_ACCESS_TOKEN")
SHOP_ID = os.getenv("ETSY_SHOP_ID")

if not all([os.getenv("ETSY_API_KEY"), ACCESS_TOKEN, SHOP_ID]):
    print("Missing credentials in .env. Run auth.py and get_shop_id.py first.")
    exit(1)

HEADERS = {
    "x-api-key": API_KEY,
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}

READINESS_STATE_ID = 1489219211571  # made_to_order, 3-5 days


def create_draft_listing(row):
    tags_raw = row.get("tags", "")
    tags = [t.strip() for t in tags_raw.replace("|", ",").split(",") if t.strip()][:13]

    materials_raw = row.get("materials", "")
    materials = [m.strip() for m in materials_raw.split(",") if m.strip()][:13]

    payload = {
        "quantity": int(row.get("quantity") or 1),
        "title": row["title"],
        "description": row["description"],
        "price": float(row["price"]),
        "who_made": row.get("who_made") or "i_did",
        "when_made": row.get("when_made") or "made_to_order",
        "taxonomy_id": int(row["taxonomy_id"]),
        "is_supply": False,
        "should_auto_renew": True,
        "type": row.get("type") or "physical",
        "state": "draft",
        "readiness_state_id": READINESS_STATE_ID,
    }

    if row.get("shipping_profile_id"):
        payload["shipping_profile_id"] = int(row["shipping_profile_id"])

    if tags:
        payload["tags"] = tags

    if materials:
        payload["materials"] = materials

    return requests.post(
        f"https://openapi.etsy.com/v3/application/shops/{SHOP_ID}/listings",
        headers=HEADERS,
        json=payload,
    )


def upload_image(listing_id, image_path):
    if not image_path or not os.path.exists(image_path):
        print(f"  Image not found: {image_path} — skipping")
        return
    with open(image_path, "rb") as f:
        resp = requests.post(
            f"https://openapi.etsy.com/v3/application/shops/{SHOP_ID}/listings/{listing_id}/images",
            headers={"x-api-key": API_KEY, "Authorization": f"Bearer {ACCESS_TOKEN}"},
            files={"image": f},
            data={"rank": 1},
        )
        if resp.status_code not in (200, 201):
            print(f"  Image upload failed: {resp.text}")
        else:
            print(f"  Image uploaded.")


def main():
    with open("listings.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Found {len(rows)} listing(s) to upload.\n")

    for i, row in enumerate(rows, start=1):
        sku = row.get("sku", f"#{i}")
        print(f"[{i}/{len(rows)}] {sku} — {row['title'][:55]}...")

        resp = create_draft_listing(row)

        if resp.status_code not in (200, 201):
            print(f"  FAILED ({resp.status_code}): {resp.text}\n")
            continue

        listing_id = resp.json()["listing_id"]
        print(f"  Draft created. listing_id={listing_id}")

        upload_image(listing_id, row.get("image_1", ""))
        print(f"  https://www.etsy.com/your/shops/me/tools/listings\n")

        time.sleep(0.3)

    print("All done. Go to Etsy > Listings to review and publish your drafts.")


if __name__ == "__main__":
    main()
