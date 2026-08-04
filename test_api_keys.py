#!/usr/bin/env python3
"""Simple API Key Test Script"""
import json
from pathlib import Path

# Load the accounts database
db_path = Path("accounts.json")
if not db_path.exists():
    print("❌ accounts.json not found")
    exit(1)

# Load and display last created account
accounts = json.loads(db_path.read_text())
print(f"✅ Found {len([k for k in accounts.keys() if k != '_endpoints'])} accounts\n")

# Get the most recent successful account
successful_accounts = [
    (k, v) for k, v in sorted(accounts.items(), key=lambda x: x[1].get('updated_at', ''))
    if k != '_endpoints' and v.get('status') == 'success'
]

if not successful_accounts:
    print("⚠️ No successful accounts found")
    exit(1)

# Show latest
email, data = successful_accounts[-1]
api_key = data.get('api_key', '')
base_url_openai = data.get('base_url_openai', 'N/A')
country = data.get('country', 'N/A')

print("=" * 60)
print(f"📧 Latest Account:")
print(f"   Email: {email}")
print(f"   Country: {country}")
print(f"   Status: {data.get('status')}")
print(f"   Updated: {data.get('updated_at')}")
print(f"\n🔑 API Key:")
print(f"   {api_key[:50]}...{api_key[-4:]}" if len(api_key) > 50 else f"   {api_key}")
print(f"\n🌐 Base URL (OpenAI compatible):")
print(f"   {base_url_openai}")
print("=" * 60)

# Test the API key
print("\n🧪 Testing API Key with simple model request...\n")

import urllib.request
import ssl

# Create a basic OpenAI-compatible request
test_payload = {
    "model": "qwen-turbo",
    "messages": [{"role": "user", "content": "Hello, this is a test. Please respond briefly."}],
    "temperature": 0.7,
    "max_tokens": 100
}

try:
    req = urllib.request.Request(
        url=f"{base_url_openai}/chat/completions",
        data=json.dumps(test_payload).encode('utf-8'),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        method='POST'
    )
    
    # Ignore SSL verification (for testing only)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    
    response = urllib.request.urlopen(req, timeout=30, context=context)
    result = json.loads(response.read().decode('utf-8'))
    
    print("✅ API Test PASSED!")
    print(f"\n📝 Response:")
    print(f"   Model: {result.get('model', 'N/A')}")
    print(f"   Content: {result['choices'][0]['message']['content']}")
    print(f"   Usage: {result.get('usage', {})}")
    
except urllib.error.HTTPError as e:
    print(f"❌ API Test FAILED with HTTP Error {e.code}")
    try:
        error_detail = json.loads(e.read().decode('utf-8'))
        print(f"   Error: {error_detail.get('error', {}).get('message', 'Unknown error')}")
    except:
        print(f"   Details: {e.read().decode()}")
except Exception as e:
    print(f"❌ API Test FAILED: {e}")

print("\n" + "=" * 60)
