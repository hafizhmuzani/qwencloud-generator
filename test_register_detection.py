#!/usr/bin/env python3
"""TEST: Is 'already-registered' detection a false positive?
Fills a FRESH email in QwenCloud register form, clicks Next, dumps result."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

EMAIL = "earlyri.ght.22@gmail.com"  # never got OTP per Gmail scan

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()

    print("1. Opening home.qwencloud.com...")
    page.goto("https://home.qwencloud.com/", wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)
    print(f"   URL: {page.url}")
    print(f"   Title: {page.title()}")

    # Click Get Started
    try:
        gs = page.get_by_role("link", name="Get Started")
        print(f"   Get Started count: {gs.count()}")
        if gs.count() > 0:
            gs.first.click()
            time.sleep(3)
            print(f"   After click URL: {page.url[:120]}")
    except Exception as e:
        print(f"   Get Started error: {e}")

    # Look for Sign Up link
    try:
        su = page.get_by_role("link", name="Sign Up")
        print(f"   Sign Up link count: {su.count()}")
        if su.count() > 0:
            su.first.click()
            time.sleep(3)
            print(f"   After Sign Up URL: {page.url[:120]}")
    except Exception as e:
        print(f"   Sign Up error: {e}")

    # Now on register page - dump body text BEFORE filling
    body_before = page.evaluate("() => document.body.innerText")
    print("\n2. Register page body text (BEFORE fill):")
    print("   " + body_before[:600].replace("\n", " | "))

    # Fill email
    email_input = page.locator('input[placeholder="Email"], input[name="email"], input[type="email"]').first
    print(f"\n3. Email input found: {email_input.count() > 0}")
    email_input.fill(EMAIL)
    time.sleep(0.5)

    # Click Next
    next_btn = page.locator('button:has-text("Next"), button[type="submit"]').first
    print(f"   Next button found: {next_btn.count() > 0}")
    next_btn.click()
    time.sleep(3)

    # Dump body text AFTER
    body_after = page.evaluate("() => document.body.innerText")
    print("\n4. Page body text (AFTER Next):")
    print("   " + body_after[:800].replace("\n", " | "))

    # Check for key markers
    low = body_after.lower()
    print("\n5. MARKERS:")
    print(f"   'already' found: {'already' in low}")
    print(f"   'registered' found: {'registered' in low}")
    print(f"   'verification code' found: {'verification code' in low}")
    print(f"   'already have an account' found: {'already have an account' in low}")
    otp_inputs = page.locator('input[placeholder*="code"], input[placeholder*="Code"], input[maxlength="6"]')
    print("   OTP input exists: " + str(otp_inputs.count() > 0))

    # Screenshot for evidence
    shot = str(Path.home() / "Desktop" / "qwencloud_register_test.png")
    page.screenshot(path=shot, full_page=True)
    print("6. Screenshot saved: " + shot)

    browser.close()
    print("\nDONE")
