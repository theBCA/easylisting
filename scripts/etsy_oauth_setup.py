"""
Step 1: Run this once to get your OAuth access token.
It will open a browser for you to authorize your Etsy shop,
then print the access token to paste into .env.
"""

import os
import hashlib
import base64
import secrets
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import requests

load_dotenv()

API_KEY = os.getenv("ETSY_API_KEY")
REDIRECT_URI = "http://localhost:3003/callback"
SCOPES = "listings_r listings_w shops_r"

code_verifier = secrets.token_urlsafe(64)
digest = hashlib.sha256(code_verifier.encode()).digest()
code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

auth_url = (
    "https://www.etsy.com/oauth/connect?"
    + urllib.parse.urlencode({
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "client_id": API_KEY,
        "state": secrets.token_urlsafe(16),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
)

received_code = None

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global received_code
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        received_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Authorization complete. You can close this tab.")

    def log_message(self, *args):
        pass


print("Opening browser for Etsy authorization...")
webbrowser.open(auth_url)

server = HTTPServer(("localhost", 3003), CallbackHandler)
server.handle_request()

if not received_code:
    print("No code received. Authorization failed.")
    exit(1)

resp = requests.post(
    "https://api.etsy.com/v3/public/oauth/token",
    data={
        "grant_type": "authorization_code",
        "client_id": API_KEY,
        "redirect_uri": REDIRECT_URI,
        "code": received_code,
        "code_verifier": code_verifier,
    },
)

token_data = resp.json()

if "access_token" not in token_data:
    print("Failed to get token:", token_data)
    exit(1)

print("\n--- Copy these into your .env file ---")
print(f"ETSY_ACCESS_TOKEN={token_data['access_token']}")
print("--------------------------------------")
print("\nAlso run get_shop_id.py to get your ETSY_SHOP_ID.")
