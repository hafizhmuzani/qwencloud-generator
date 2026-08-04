
#!/usr/bin/env python3
"""Quick scan: which emails from email_list.txt are already registered on QwenCloud."""
import time, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

emails = [l.strip() for l in Path("email_list.txt").read_text().splitlines() if l.strip()]
print(f"Scanning {len(emails)} emails on QwenCloud...\n")

REGISTER_URL = "https://account.alibabacloud.com/sso/register?response_type=code&client_id=qwencloud&scope=openid&redirect_uri=https%3A%2F%2Faccount.qwencloud.com%2Fsso%2FssoLogin&return_url=https%3A%2F%2Fhome.qwencloud.com%2F"

registered = []
unused = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()
    
    for i, email in enumerate(emails):
        try:
            page.goto(REGISTER_URL, wait_until="domcontentloaded", timeout=20000)
            time.sleep(1)
            
            # Fill email
            email_input = page.locator("input[name='email'], input[placeholder*='email'], input[type='email']").first
            email_input.fill(email)
            time.sleep(0.3)
            
            # Try to click send code or similar to trigger validation
            try:
                send_btn = page.locator("button:has-text('Send'), button:has-text('Get Code'), button:has-text('Verify')").first
                send_btn.click()
                time.sleep(2)
            except:
                pass
            
            # Check for "already registered" message
            body_text = page.inner_text("body").lower()
            if "already" in body_text or "registered" in body_text or "already registered" in body_text:
                status = "REGISTERED"
                registered.append(email)
            elif "verification code" in body_text or "code sent" in body_text or "captcha" in body_text:
                status = "UNUSED (OTP sent)"
                unused.append(email)
            else:
                status = "UNKNOWN"
                unused.append(email)
            
            print(f"[{i+1}/{len(emails)}] {email} -> {status}")
            
        except Exception as e:
            print(f"[{i+1}/{len(emails)}] {email} -> ERROR: {str(e)[:60]}")
            unused.append(email)
    
    browser.close()

print(f"\n{'='*50}")
print(f"REGISTERED (sudah dipakai): {len(registered)}")
for e in registered:
    print(f"  ✗ {e}")
print(f"\nUNUSED (belum dipakai): {len(unused)}")
for e in unused[:10]:
    print(f"  ✓ {e}")
if len(unused) > 10:
    print(f"  ... dan {len(unused)-10} lainnya")
