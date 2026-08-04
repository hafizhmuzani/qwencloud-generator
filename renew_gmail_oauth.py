#!/usr/bin/env python3
"""Simple script to obtain fresh Gmail OAuth refresh token."""

import json
import urllib.parse
import urllib.request
from pathlib import Path

# Read client_secret.json
client_file = Path("client_secret.json")
if not client_file.exists():
    print("❌ client_secret.json not found!")
    print("\nYou need to:")
    print("1. Go to https://console.cloud.google.com/")
    print("2. Create a new project")
    print("3. Enable Gmail API")
    print("4. Create OAuth credentials (Desktop app)")
    print("5. Download and save as client_secret.json")
    print("   in this folder: D:\\qwencloud-generator\\")
    exit(1)

client_data = json.loads(client_file.read_text())
client_id = client_data["installed"]["client_id"]
client_secret = client_data["installed"]["client_secret"]

# Step 1: Build OAuth URL
scopes = "https://www.googleapis.com/auth/gmail.readonly"
redirect_uri = "http://localhost:8085/callback"

params = {
    "client_id": client_id,
    "redirect_uri": redirect_uri,
    "scope": scopes,
    "response_type": "code",
    "access_type": "offline",
    "prompt": "consent",  # Force re-authorization
}

auth_url = f"https://accounts.google.com/o/oauth2/auth?{urllib.parse.urlencode(params)}"

print("=" * 60)
print("🔐 QwenCloud Generator - Gmail OAuth Token Renewal")
print("=" * 60)
print()
print(f"📧 Your registered Gmail: earlyright22@gmail.com")
print(f"⚠️ Current token is EXPIRED")
print()
print("Instructions:")
print("1. Open this URL in your browser:")
print()
print(auth_url)
print()
print("2. Sign in with Gmail account: earlyright22@gmail.com")
print("3. Click 'Allow' / 'Continue' on consent screen")
print("4. You'll be redirected to localhost:8085/callback")
print("5. Copy the full URL from address bar (it will be long)")
print()
print("Once you have the code from URL, paste it below:")
print("=" * 60)
print()

# Get the authorization code from user
auth_code = input("Paste the authorization URL or code here: ").strip()

# Extract code if full URL was pasted
if "code=" in auth_code:
    code = urllib.parse.parse_qs(urllib.parse.urlparse(auth_code).query)["code"][0]
else:
    code = auth_code

print(f"\n🔄 Exchanging code for tokens...")
print()

# Step 2: Exchange code for tokens
token_params = {
    "code": code,
    "client_id": client_id,
    "client_secret": client_secret,
    "redirect_uri": redirect_uri,
    "grant_type": "authorization_code",
}

body = urllib.parse.urlencode(token_params).encode()
req = urllib.request.Request(
    "https://oauth2.googleapis.com/token",
    data=body,
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    method="POST",
)

try:
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read().decode("utf-8"))
    
    # Save the new token
    gmail_tokens_file = Path("gmail_tokens.json")
    old_data = json.loads(gmail_tokens_file.read_text()) if gmail_tokens_file.exists() else {"accounts": {}, "default_client": client_data["installed"]}
    
    normalized_email = "earlyright22@gmail.com"  # Gmail dot-variant normalizes to this
    old_data["accounts"][normalized_email] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": result["refresh_token"],
        "access_token": result["access_token"],
        "expires_in": result.get("expires_in", 3600),
        "expires_at": int(__import__("time").time()) + result.get("expires_in", 3600),
        "token_type": result.get("token_type", "Bearer"),
    }
    
    tmp = gmail_tokens_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(old_data, indent=2))
    tmp.replace(gmail_tokens_file)
    
    print("✅ SUCCESS! New Gmail OAuth tokens saved!")
    print()
    print(f"📧 Email: {normalized_email}")
    print(f"🕒 Expires at: {result.get('expires_in', 'N/A')} seconds from now")
    print(f"🔑 Refresh Token (first 30 chars): {result['refresh_token'][:30]}...")
    print()
    print("Now you can run the signup process again!")
    print("=" * 60)
    
except urllib.error.HTTPError as e:
    error_data = json.loads(e.read().decode("utf-8"))
    print(f"❌ OAuth Error {e.code}: {error_data.get('error', 'Unknown error')}")
    print(f"Details: {error_data.get('error_description', 'N/A')}")
    print()
    print("Possible causes:")
    print("- Invalid or expired authorization code")
    print("- Code already used")
    print("- Client ID/Secret mismatch")
    exit(1)
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
