"""
Helper: search Etsy taxonomy (category) IDs by keyword.
Run: python get_taxonomy.py <keyword>
"""

import sys
import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = f"{os.getenv('ETSY_API_KEY')}:{os.getenv('ETSY_SHARED_SECRET').strip()}"

resp = requests.get(
    "https://openapi.etsy.com/v3/application/seller-taxonomy/nodes",
    headers={"x-api-key": api_key},
)
nodes = resp.json().get("results", [])


def flatten(nodes, path=""):
    for node in nodes:
        full_path = f"{path} > {node['name']}" if path else node["name"]
        yield node["id"], full_path
        if node.get("children"):
            yield from flatten(node["children"], full_path)


keyword = " ".join(sys.argv[1:]).lower() if len(sys.argv) > 1 else ""

print(f"\nSearching taxonomy for: '{keyword}'\n" if keyword else "\nAll taxonomy nodes:\n")

for node_id, path in flatten(nodes):
    if not keyword or keyword in path.lower():
        print(f"  {node_id:>6}  {path}")
