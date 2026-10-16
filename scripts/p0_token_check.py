"""Checks that PayPal sandbox credentials in .env work. Prints no secrets."""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_env(path: Path) -> dict:
    values = {}
    if not path.exists():
        sys.exit(f"FAIL: {path} not found. Run this from the repo root.")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.split(" #")[0].strip().strip('"').strip("'")
    return values


env = load_env(Path(".env"))
client_id = env.get("PAYPAL_CLIENT_ID") or os.environ.get("PAYPAL_CLIENT_ID", "")
secret = env.get("PAYPAL_CLIENT_SECRET") or os.environ.get("PAYPAL_CLIENT_SECRET", "")
mode = (env.get("PAYPAL_ENV") or "sandbox").lower()

if mode != "sandbox":
    sys.exit("FAIL: PAYPAL_ENV must be 'sandbox' for this project.")
if not client_id or not secret:
    sys.exit("FAIL: PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET is empty in .env")

auth = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
req = urllib.request.Request(
    "https://api-m.sandbox.paypal.com/v1/oauth2/token",
    data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
    headers={
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    },
    method="POST",
)

try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
except urllib.error.HTTPError as e:
    body = e.read().decode(errors="replace")
    print(f"FAIL: HTTP {e.code}")
    print(body[:500])
    sys.exit(1)
except Exception as e:  # network problems etc.
    sys.exit(f"FAIL: could not reach PayPal: {e}")

print("SUCCESS: PayPal sandbox token received.")
print(f"  Token type : {data.get('token_type')}")
print(f"  Expires in : {data.get('expires_in')} seconds")
print(f"  App ID     : {data.get('app_id')}")
scopes = (data.get("scope") or "").split()
print(f"  Scopes     : {len(scopes)} granted")
for key in ("invoic", "vault", "payment-tokens", "payments"):
    hits = [s for s in scopes if key in s]
    print(f"  - contains '{key}': {'yes' if hits else 'NO'}")