"""
Helper: prints your existing Etsy shipping profiles and their IDs.
Run this after auth.py and get_shop_id.py.
Paste the correct shipping_profile_id into listings.csv.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = f"{os.getenv('ETSY_API_KEY')}:{os.getenv('ETSY_SHARED_SECRET').strip()}"
ACCESS_TOKEN = os.getenv("ETSY_ACCESS_TOKEN")
SHOP_ID = os.getenv("ETSY_SHOP_ID")

headers = {
    "x-api-key": API_KEY,
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}

resp = requests.get(
    f"https://openapi.etsy.com/v3/application/shops/{SHOP_ID}/shipping-profiles",
    headers=headers,
)

profiles = resp.json().get("results", [])

if not profiles:
    print("No shipping profiles found.")
    print("Go to: Etsy > Shop Manager > Settings > Shipping Settings > Add a profile")
    print("Create one for Turkey → Europe, then run this script again.")
else:
    print("Your shipping profiles:\n")
    for p in profiles:
        print(f"  ID: {p['shipping_profile_id']}  |  Name: {p['title']}")
    print("\nCopy the correct ID into the shipping_profile_id column in listings.csv")
