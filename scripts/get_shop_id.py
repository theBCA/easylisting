"""
Step 2: Run this after auth.py to find your shop ID and paste it into .env.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = f"{os.getenv('ETSY_API_KEY')}:{os.getenv('ETSY_SHARED_SECRET').strip()}"
ACCESS_TOKEN = os.getenv("ETSY_ACCESS_TOKEN")

headers = {
    "x-api-key": API_KEY,
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}

resp = requests.get("https://openapi.etsy.com/v3/application/users/me", headers=headers)
user = resp.json()

print("User ID:", user.get("user_id"))
print("Email:", user.get("primary_email"))

resp2 = requests.get(
    f"https://openapi.etsy.com/v3/application/users/{user['user_id']}/shops",
    headers=headers,
)
shop = resp2.json()
print("\n--- Copy this into your .env file ---")
print(f"ETSY_SHOP_ID={shop.get('shop_id')}")
print("Shop name:", shop.get("shop_name"))
print("-------------------------------------")
