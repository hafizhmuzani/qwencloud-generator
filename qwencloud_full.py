#!/usr/bin/env python3
"""One-shot Qwen Cloud auto-signup + API key extractor.

Usage:
    python3 qwencloud_full.py --email yudi.luia3@gmail.com --proxy http://user:pass@host:port

Output (final line):
    __RESULT__ {"status": "success", "email": "...", "api_key": "...", "base_url": "..."}
"""
import argparse
import json
import random
import re
import sys
import time
import urllib.parse
import urllib.request
import base64
from pathlib import Path
from typing import Optional

import gmail_auth
from logger import info, ok, warn, error

# fmt: off
COUNTRIES = [
    "Indonesia", "Malaysia", "Singapore", "Thailand", "Philippines",
    "Vietnam", "United States", "United Kingdom", "Germany",
    "Australia", "Canada", "Netherlands", "France", "India", "Brazil",
    "Mexico", "Turkey", "Spain", "Italy", "Sweden",
    "Norway", "Denmark", "Finland", "Poland", "Portugal",
    "Ireland", "Belgium", "Austria", "Switzerland", "Czech Republic",
    "Romania", "Greece", "Croatia", "Argentina", "Chile", "Colombia",
    "Peru", "South Africa", "New Zealand", "United Arab Emirates",
    "Saudi Arabia", "Egypt", "Morocco", "Kenya", "Nigeria",
]
# fmt: on

BASE_OPENAI = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
BASE_ANTHROPIC = "https://dashscope-intl.aliyuncs.com/apps/anthropic"


def _now() -> float:
    return time.time()


def _sleep(t: float):
    time.sleep(t)


def _wait_for(page, selector: str, timeout: float = 30.0, state: str = "visible") -> bool:
    """Poll for element every 1s. Returns True if found, False on timeout.
    state: visible (default), attached, hidden
    """
    deadline = _now() + timeout
    while _now() < deadline:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                _sleep(1)
                continue
            if state == "visible":
                if loc.is_visible():
                    return True
            elif state == "attached":
                return True
            elif state == "hidden":
                return True  # count > 0 but state hidden means element exists but not visible
        except Exception:
            pass
        _sleep(1)
    return False


def _wait_for_text(page, text: str, timeout: float = 30.0) -> bool:
    """Poll for text content every 1s."""
    deadline = _now() + timeout
    while _now() < deadline:
        try:
            if page.get_by_text(text, exact=False).count() > 0:
                return True
        except Exception:
            pass
        _sleep(1)
    return False


def _wait_for_url(page, pattern: str, timeout: float = 30.0) -> bool:
    """Poll for URL pattern every 1s."""
    deadline = _now() + timeout
    while _now() < deadline:
        try:
            if pattern in page.url:
                return True
        except Exception:
            pass
        _sleep(1)
    return False


def _wait_for_page_load(page, timeout: float = 15.0) -> bool:
    """Wait for full page load (networkidle) after navigation."""
    try:
        page.wait_for_load_state("networkidle", timeout=int(timeout * 1000))
        return True
    except Exception:
        return False


def _result(payload: dict):
    line = "__RESULT__ " + json.dumps(payload)
    print(line, flush=True)
    return payload


def _read_verification_code(email: str, timeout: int = 60, min_internal_date_ms: int = 0, skip_time_filter: bool = False) -> Optional[str]:
    """Poll Gmail API for the latest Qwen Cloud verification code."""
    base = gmail_auth.normalize_gmail(email)
    info(f"[{email}] polling Gmail for verification code (base={base})")
    access_token = gmail_auth.get_access_token(email)
    q = urllib.parse.quote('from:system_sg@notice.qwencloud.com')
    list_url = f"https://www.googleapis.com/gmail/v1/users/me/messages?q={q}&maxResults=10"
    deadline = _now() + timeout

    def _extract_part(payload: dict, mime: str) -> str:
        if payload.get("mimeType") == mime and "data" in payload.get("body", {}):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode(errors="ignore")
        for p in payload.get("parts", []):
            t = _extract_part(p, mime)
            if t:
                return t
        return ""

    while _now() < deadline:
        req = urllib.request.Request(list_url, headers={"Authorization": f"Bearer {access_token}"})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                warn(f"[{email}] Gmail rate limit (429), backing off 5s")
                _sleep(5)
                continue
            if e.code == 503:
                warn(f"[{email}] Gmail unavailable (503), backing off 3s")
                _sleep(3)
                continue
            warn(f"[{email}] Gmail HTTP error {e.code}: {e}")
            _sleep(2)
            continue
        except Exception as e:
            warn(f"[{email}] Gmail list error: {e}")
            _sleep(2)
            continue
        
        # Gmail API returns messages in reverse chronological order (newest first)
        # Just take the FIRST message — it's the most recent one
        messages = data.get("messages", [])[:1]
        
        for m in messages:
            r = urllib.request.Request(
                f"https://www.googleapis.com/gmail/v1/users/me/messages/{m['id']}?format=full",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            try:
                d = json.loads(urllib.request.urlopen(r, timeout=30).read().decode())
            except Exception:
                continue
            # Ignore old emails ONLY for signup mode (not login) - allows existing OTPs in inbox
            internal_ms = int(d.get("internalDate", 0))
            if not skip_time_filter and min_internal_date_ms and internal_ms < min_internal_date_ms:
                continue
            
            headers = {h["name"]: h["value"] for h in d.get("payload", {}).get("headers", [])}
            
            # More lenient match - just look for "qwen" anywhere
            subject = headers.get("Subject", "")
            from_addr = headers.get("From", "")
            combined_subj_from = (subject + from_addr).lower()
            
            # Skip emails that clearly are NOT QwenCloud
            if not any(w in combined_subj_from for w in ['qwen', 'alibabacloud', 'notice.qwen']):
                continue
            
            # Skip Welcome emails
            if "welcome" in subject.lower():
                info(f"[{email}] skipping Welcome email: {subject}")
                continue
            
            text = _extract_part(d.get("payload", {}), "text/plain")
            html = _extract_part(d.get("payload", {}), "text/html")
            combined = text + " " + html
            # The verification code is tied to the specific email variant used at signup.
            # To header may contain angle brackets: <email@gmail.com>
            to_header = headers.get("To", "").replace("<", "").replace(">", "")
            # For login mode (skip_time_filter), be more lenient with email matching
            if not skip_time_filter and email not in (to_header + combined):
                continue
            # Qwen Cloud email contains a large 36px <div> with the 6-digit code.
            # Some emails say "Your verification code...", others omit "Your".
            m = re.search(
                r'(?:Your )?verification code for Qwen Cloud is:.*?<div[^>]*>\s*(\d{6})\s*</div>',
                combined,
                re.IGNORECASE | re.DOTALL,
            )
            if m:
                info(f"[{email}] verification code found: {m.group(1)}")
                return m.group(1)
            # Robust fallback: the code is the only 6-digit inside the 36px styled div.
            m2 = re.search(
                r'<div[^>]*font-size:\s*36px[^>]*>\s*(\d{6})\s*</div>',
                combined,
                re.IGNORECASE | re.DOTALL,
            )
            if m2:
                info(f"[{email}] verification code found (style fallback): {m2.group(1)}")
                return m2.group(1)
            # Smart fallback: look for "verification code" pattern in text (handles multi-code emails)
            m3 = re.search(
                r'verification code[^0-9]*(\d{6})',
                text + " " + re.sub(r'<[^>]+>', ' ', html),  # strip HTML tags for cleaner match
                re.IGNORECASE,
            )
            if m3:
                info(f"[{email}] verification code found (text pattern): {m3.group(1)}")
                return m3.group(1)
            # Last resort: any 6-digit in the message (may be a color code).
            codes = re.findall(r'\b\d{6}\b', combined)
            if codes:
                info(f"[{email}] verification code found (last resort): {codes[0]}")
                return codes[0]
        _sleep(0.5)
    return None


def _safe_count(page, selector: str, limit: int = 10) -> int:
    for _ in range(limit):
        try:
            return page.locator(selector).count()
        except Exception:
            _sleep(0.3)
    return 0


def _is_access_denied(page) -> bool:
    try:
        url = page.url.lower()
        body = page.content().lower()
    except Exception:
        return False
    return any(k in url or k in body for k in ["access denied", "access_denied", "error?error"])


def _random_country() -> str:
    return random.choice(COUNTRIES)


def _detect_page(page) -> str:
    """Identify current page state. Returns: login|signup|otp|country|dashboard|api_keys|api_key_dialog|unknown"""
    try:
        url = page.url.lower()
        title = page.title().lower()
    except Exception:
        return "unknown"

    # Check URL patterns (most specific first)
    if "home.qwencloud.com" in url and "/api-keys" in url:
        return "api_keys"
    if "home.qwencloud.com" in url:
        if page.locator('dialog:has-text("Copy your API Key")').count() > 0:
            return "api_key_dialog"
        return "dashboard"
    # SSO pages
    if "sso/register" in url:
        # Could be signup, OTP, or country page — check content
        if page.locator('text=Please select your country').count() > 0:
            return "country"
        if page.locator('text=Enter Verification Code').count() > 0:
            return "otp"
        return "signup"
    if "sso/login" in url:
        return "login"
    # Fallback: check page content
    if page.locator('dialog:has-text("Copy your API Key")').count() > 0:
        return "api_key_dialog"
    if page.locator('button:has-text("Create API key")').count() > 0:
        return "api_keys"
    return "unknown"


def _split_proxy(proxy: str) -> dict:
    """Parse proxy string into Playwright proxy dict.

    Accepts:
      - user:pass@host:port
      - http://user:pass@host:port
      - host:port:user:pass
      - host:port
    """
    p = proxy.strip()
    scheme = ""
    if "://" in p:
        scheme, p = p.split("://", 1)
    if "@" in p:
        auth, server = p.rsplit("@", 1)
        user, pw = auth.split(":", 1)
    else:
        parts = p.split(":")
        if len(parts) == 4:
            host, port, user, pw = parts
            server = f"{host}:{port}"
        else:
            server = p
            user = pw = ""
    server_url = f"{scheme}://{server}" if scheme else server
    return {"server": server_url, "username": user, "password": pw}


def _select_country(page, country: str) -> bool:
    info(f"selecting country: {country}")
    input_sel = 'input[placeholder="Select your country/region"]'
    try:
        page.locator(input_sel).click()
        _sleep(0.5)
        page.locator(input_sel).fill(country)
        _sleep(0.5)
    except Exception as e:
        warn(f"country input open failed: {e}")
        return False

    opt_sel = f'[role="option"]:has-text("{country}")'
    try:
        page.locator(opt_sel).wait_for(state="visible", timeout=10000)
        page.locator(opt_sel).click(timeout=10000)
    except Exception as e:
        warn(f"country option click failed: {e}")
        # Fallback: use JS to select the option by innerText.
        try:
            selected = page.evaluate(
                f"() => {{"
                f"  const opt = Array.from(document.querySelectorAll('[role=option]'))"
                f"    .find(el => el.innerText.includes('{country}'));"
                f"  if (opt) {{ opt.click(); return true; }}"
                f"  return false;"
                f"}}"
            )
            if not selected:
                return False
        except Exception:
            return False
    _sleep(0.5)
    # verify button enabled
    try:
        disabled = page.locator('button:has-text("Continue")').is_disabled()
        return not disabled
    except Exception:
        return False


def _do_login(page, email: str) -> dict:
    """Login via email + OTP. Reaches dashboard without country selection."""
    info(f"starting login (resume) for {email}")
    request_code_at_ms = int(time.time() * 1000) - 60000  # 60s clock skew tolerance
    try:
        page.get_by_role("textbox", name="Email").fill(email)
        _sleep(0.5)
        # Click Send Code to request OTP
        send_btn = page.get_by_role("button", name="Send Code")
        if send_btn.count() > 0:
            send_btn.click()
            info(f"[{email}] Send Code clicked")
        else:
            # Login page might have Next button instead
            next_btn = page.locator('button:has-text("Next")')
            if next_btn.count() > 0:
                next_btn.click()
                info(f"[{email}] Next clicked for OTP")
                _wait_for_page_load(page, timeout=10)
                _sleep(0.5)
                # Now look for Send Code or Verification Code input
                send_btn2 = page.get_by_role("button", name="Send Code")
                if send_btn2.count() > 0:
                    send_btn2.click()
                    info(f"[{email}] Send Code clicked")
    except Exception as e:
        return {"status": "error", "email": email, "reason": f"login-fill-failed: {e}"}

    # Poll for verification code (skip time filter for login to allow existing OTP)
    code = _read_verification_code(email, timeout=90, min_internal_date_ms=request_code_at_ms, skip_time_filter=True)
    if not code:
        return {"status": "error", "email": email, "reason": "login-verification-code-not-found"}

    # Fill verification code into the login page's verification code input
    try:
        vc_input = page.get_by_role("textbox", name="Verification Code")
        vc_input.wait_for(state="visible", timeout=10000)
        vc_input.fill(code)
        _sleep(0.5)
    except Exception:
        # Fallback: fill into second textbox
        try:
            inputs = page.locator('input[type="text"]')
            inputs.nth(1).fill(code)
        except Exception as e:
            return {"status": "error", "email": email, "reason": f"login-code-fill-failed: {e}"}

    # Click Next to submit login
    try:
        next_btn = page.get_by_role("button", name="Next")
        next_btn.click()
    except Exception as e:
        return {"status": "error", "email": email, "reason": f"login-next-failed: {e}"}

    # Wait for full page load after Next click
    _wait_for_page_load(page, timeout=10)
    _sleep(0.5)

    # Wait for dashboard via polling AND wait for SSO cookie to be set
    dashboard_ok = False
    deadline = _now() + 30
    while _now() < deadline:
        _sleep(0.5)
        try:
            url_lower = page.url.lower()
            # Must be on home.qwencloud.com and NOT on SSO/login pages
            if "home.qwencloud.com" in url_lower and "sso/login" not in url_lower:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
                time.sleep(1)  # Extra wait for SPA hydration
                
                # Check if user is authenticated by looking for dashboard content
                body_text = page.evaluate("() => document.body.innerText.toLowerCase()")
                has_user_menu = page.evaluate("""() => {
                    // Look for user avatar/icon or logout button (indicates logged in)
                    const selectors = ['[class*="avatar"]', '[class*="user"]', '[data-testid*="user"]', '[class*="profile"]'];
                    for (const sel of selectors) {
                        if (document.querySelector(sel)) return true;
                    }
                    // Check for sidebar nav items that appear when logged in
                    const navItems = document.querySelectorAll('[class*="sidebar"] a, nav a');
                    for (const item of navItems) {
                        if (item.textContent.includes('Settings') || item.textContent.includes('API Keys')) return true;
                    }
                    return false;
                }""")
                
                info(f"[{email}] dashboard check: has_user_menu={has_user_menu}, body_has_login={'log in' in body_text}")
                
                # If we can find user menu elements OR the body doesn't have "Log in to QwenCloud" prompt
                if has_user_menu or ("log in" not in body_text[:500] and "access all services" not in body_text):
                    dashboard_ok = True
                    info(f"[{email}] authenticated session established")
                    break
        except Exception as e:
            info(f"[{email}] dashboard check exception: {e}")
            continue
    
    if not dashboard_ok:
        info(f"[{email}] dashboard check failed - will retry on /api-keys access")
    
    info(f"[{email}] login dashboard reached, returning status={dashboard_ok}")
    return {"status": "login-ok" if dashboard_ok else "logged-in-session-started", "email": email, "needs_auth_check": not dashboard_ok}


def _do_signup(page, email: str, country: str) -> dict:
    info(f"starting signup for {email}")

    # --- signup email page ---
    request_code_at_ms = int(time.time() * 1000) - 60000  # 60s clock skew tolerance
    try:
        page.locator('input[placeholder="Email"]').fill(email)
        page.locator('button:has-text("Next")').click()
    except Exception as e:
        return {"status": "error", "email": email, "reason": f"signup-fill-failed: {e}"}

    # Wait for full page load after Next click (signup → OTP or already page)
    _wait_for_page_load(page, timeout=10)
    _sleep(0.5)

    # --- detect "already registered" immediately after Next ---
    deadline = _now() + 10
    while _now() < deadline:
        try:
            body = page.evaluate("() => document.body.innerText").lower()
            if "already" in body or "registered" in body:
                return {"status": "already-registered", "email": email}
            if "enter verification code" in body:
                break
        except Exception:
            pass
        _sleep(0.5)
    else:
        return {"status": "error", "email": email, "reason": "verification-code-page-not-found"}

    # --- OTP page detected! Now click Resend to get a FRESH code ---
    info(f"[{email}] OTP page detected")
    
    # First, wait for any auto-sent OTP to arrive (~5-15 seconds)
    initial_wait = 20
    info(f"[{email}] waiting {initial_wait}s for first OTP to arrive...")
    _sleep(initial_wait)
    
    # Record timestamp after this wait
    fresh_otp_timestamp = int(time.time() * 1000) - 120000  # 2 min buffer BEFORE now
    
    # Poll for FIRST OTP that should have arrived
    code = _read_verification_code(email, timeout=30, min_internal_date_ms=fresh_otp_timestamp, skip_time_filter=False)
    
    if not code:
        # If no OTP yet, try clicking Resend (if enabled)
        info(f"[{email}] No OTP found yet, trying Resend...")
        try:
            # Find Resend button - handle different text variations
            resend_btn = page.get_by_role("button", name=re.compile(r"(resend|send.*code)", re.I))
            for btn in resend_btn.all():
                try:
                    is_enabled = btn.is_enabled()
                    if is_enabled:
                        info(f"[{email}] Resend button enabled, clicking...")
                        btn.click()
                        time.sleep(5)
                        fresh_otp_timestamp = int(time.time() * 1000) - 120000  # Same buffer
                        break
                except:
                    continue
            
            # Poll for RESNED OTP
            code = _read_verification_code(email, timeout=40, min_internal_date_ms=fresh_otp_timestamp, skip_time_filter=False)
            
        except Exception as e:
            info(f"[{email}] Resend failed: {e}")
        
        if not code:
            return {"status": "error", "email": email, "reason": "verification-code-not-found"}

    # Type the whole code into the first OTP input with real key events.
    # The UI auto-advances between the 6 boxes and submits once complete.
    otp_first = page.locator('input[type="text"]').first
    otp_first.wait_for(state="visible", timeout=10000)
    otp_first.click()
    _sleep(0.3)
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    _sleep(0.3)
    otp_first.press_sequentially(code, delay=50)
    _wait_for_text(page, "Please select your country/region", timeout=10)
    # debug: log current OTP values
    values = page.evaluate("() => Array.from(document.querySelectorAll('input[type=\\\"text\\\"]')).map(i => i.value)")
    info(f"[{email}] OTP values after typing: {values}")

    # Some sessions auto-advance, others require clicking Validate.
    try:
        validate_btn = page.locator('button:has-text("Validate")')
        if validate_btn.count() > 0 and not validate_btn.is_disabled():
            info(f"[{email}] clicking Validate button")
            validate_btn.click()
            _wait_for_text(page, "Please select your country/region", timeout=10)
        else:
            info(f"[{email}] Validate button disabled; trying JS click fallback")
            page.evaluate("() => { const b = document.querySelector('button'); if (b) b.click(); }")
            _wait_for_text(page, "Please select your country/region", timeout=10)
    except Exception as e:
        info(f"[{email}] Validate button not clicked: {e}")

    # --- country/agreement page ---
    try:
        page.wait_for_selector('text=Please select your country/region', timeout=20000)
    except Exception:
        warn(f"[{email}] country page not found. url={page.url} title={page.title()}")
        try:
            inner = page.evaluate("() => document.body.innerText.slice(0, 600)")
            warn(f"[{email}] body innerText: {inner}")
        except Exception:
            pass
        return {"status": "error", "email": email, "reason": "country-page-not-found"}

    if not _select_country(page, country):
        return {"status": "error", "email": email, "reason": "country-selection-failed"}

    # check agreement checkbox via role-based selector + JS click
    try:
        cb = page.get_by_role("checkbox")
        cb.wait_for(state="attached", timeout=10000)
        checked = page.evaluate(
            "() => { const cb = document.querySelector('input[type=\"checkbox\"]');"
            "  if (!cb) return null;"
            "  cb.click();"
            "  return cb.checked;"
            "}"
        )
        if checked is None:
            return {"status": "error", "email": email, "reason": "agreement-checkbox-not-found"}
        info(f"[{email}] agreement checkbox checked: {checked}")
        _sleep(0.5)
    except Exception as e:
        return {"status": "error", "email": email, "reason": f"agreement-click-failed: {e}"}

    continue_btn = page.locator('button:has-text("Continue")')
    try:
        continue_btn.wait_for(state="visible", timeout=10000)
        if continue_btn.is_disabled():
            warn(f"[{email}] Continue button disabled before click")
            # try clicking the visible button via JS as fallback
            page.evaluate("() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.innerText.includes('Continue')); if (b) b.click(); }")
        else:
            continue_btn.click()
    except Exception as e:
        return {"status": "error", "email": email, "reason": f"continue-click-failed: {e}"}

    # --- wait for dashboard via polling (avoids execution-context-destroyed race) ---
    dashboard_ok = False
    deadline = _now() + 30
    while _now() < deadline:
        _sleep(0.5)
        try:
            url = page.url.lower()
            if "home.qwencloud.com" in url:
                # Wait for page to FULLY load
                _wait_for_page_load(page, timeout=10)
                # Confirm we're really on dashboard (not SSO redirect)
                if "sso/" not in page.url.lower():
                    dashboard_ok = True
                    break
        except Exception:
            continue

    if not dashboard_ok:
        try:
            current_url = page.url
        except Exception:
            current_url = "navigating"
        warn(f"[{email}] dashboard redirect timeout. url={current_url}")
        return {"status": "error", "email": email, "reason": "dashboard-redirect-timeout"}
    info(f"[{email}] dashboard reached")
    # Navigate directly to /api-keys (same as login flow)
    try:
        page.goto("https://home.qwencloud.com/api-keys", wait_until="commit", timeout=15000)
        _wait_for_page_load(page, timeout=10)
    except Exception:
        pass
    return {"status": "signup-ok", "email": email}


def _dismiss_overlays(page):
    """Dismiss mobile notification overlay via JS click (fast, no Playwright timeout)."""
    try:
        page.evaluate(
            "() => {"
            "  const btns = Array.from(document.querySelectorAll('button'));"
            "  const close = btns.find(b => b.innerText.trim() === 'Close' && b.getBoundingClientRect().width > 0);"
            "  if (close) { close.click(); return true; }"
            "  return false;"
            "}"
        )
    except Exception:
        pass
    return False


def _create_api_key(page, description: str = "default") -> Optional[str]:
    info("navigating to API keys page")
    _dismiss_overlays(page)
    
    # Navigate to /api-keys via direct URL (might trigger SSO redirect)
    try:
        page.goto("https://home.qwencloud.com/api-keys", wait_until="domcontentloaded", timeout=15000)
        time.sleep(2)
    except Exception as e:
        warn(f"Initial navigation to /api-keys failed: {e}")
        pass
    
    # Check if we were redirected to login or landing page
    url_lower = page.url.lower()
    if "sso/login" in url_lower or ("home.qwencloud.com" in url_lower and "/api-keys" not in url_lower):
        info(f"API Keys page triggered SSO redirect. Current URL: {page.url}")
        
        # Try clicking "Get Started" if on landing page
        if "sso/login" not in url_lower:
            try:
                get_started = page.get_by_role("link", name="Get Started")
                if get_started.count() > 0:
                    info("Clicking Get Started to navigate to login...")
                    get_started.click()
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    time.sleep(2)
            except Exception as e:
                info(f"Failed to click Get Started: {e}")
        
        # After waiting, check if we're now on API keys page with fresh session
        url_lower = page.url.lower()
        if "/api-keys" not in url_lower:
            warn(f"Still not on API keys page: {page.url}")
            return None
    
    # Wait for Create API key button or sidebar link
    if not _wait_for(page, 'button:has-text("Create API key")', timeout=30):
        info("Create API key button not found, trying sidebar navigation...")
        _dismiss_overlays(page)
        try:
            api_keys_link = page.get_by_role("link", name="API Keys")
            if api_keys_link.count() > 0:
                api_keys_link.click()
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                time.sleep(2)
        except Exception as e:
            warn(f"Sidebar API Keys link click failed: {e}")
        
        if not _wait_for(page, 'button:has-text("Create API key")', timeout=15):
            warn("Create API key button still not found")
            return None
    
    # Click Create API key
    try:
        create_btn = page.locator('button:has-text("Create API key")').first
        create_btn.wait_for(state="visible", timeout=5000)
        create_btn.click()
        _sleep(1)
    except Exception as e:
        warn(f"Create API key button click failed: {e}")
        return None
    
    # Wait for dialog
    if not _wait_for_text(page, "Create API Key", timeout=15):
        info("Looking for alternative dialogs...")
        # Maybe it's already generated? Or different text?
        page_body = page.evaluate("() => document.body.innerText.substring(0, 1000)")
        warn(f"Body preview: {page_body[:500]}")
        return None
    
    # Fill description
    try:
        desc_input = page.locator('input[placeholder*="Production API key"]').first
        if desc_input.is_enabled():
            desc_input.fill(description)
            _sleep(1)
    except Exception as e:
        warn(f"Description fill failed: {e}")
        # Continue without description
    
    # Wait for Generate button to become enabled
    deadline = _now() + 10
    while _now() < deadline:
        try:
            gen_btn = page.locator('button:has-text("Generate Key")')
            if gen_btn.is_enabled():
                break
        except:
            pass
        _sleep(0.5)
    
    # Click Generate Key
    try:
        gen_btn = page.locator('button:has-text("Generate Key")')
        gen_btn.wait_for(state="visible", timeout=10000)
        gen_btn.click()
        _sleep(2)
    except Exception as e:
        warn(f"Generate Key click failed: {e}")
        return None
    
    # Extract API key from the response dialog/page
    key = None
    deadline = _now() + 15
    while _now() < deadline:
        try:
            # Try multiple selectors
            key_elem = page.query_selector('input[value^="sk-"]')
            if key_elem:
                key = key_elem.input_value()
                if key and key.startswith("sk-"):
                    info(f"API key extracted: {key[:20]}...")
                    return key
            
            # Also try evaluating via JS
            key = page.evaluate("""() => {
                const inputs = Array.from(document.querySelectorAll('input'));
                const visible = inputs.filter(i => { 
                    const r = i.getBoundingClientRect(); 
                    return r.width > 0 && r.height > 0; 
                });
                const keyInput = visible.find(i => i.value && i.value.startsWith('sk-'));
                return keyInput ? keyInput.value : null;
            }""")
            if key:
                info(f"API key extracted via JS: {key[:20]}...")
                return key
                
        except Exception as e:
            warn(f"Key extraction attempt failed: {e}")
        _sleep(1)
    
    warn("Failed to extract API key - might be a UI change")
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--proxy", default="")
    parser.add_argument("--country", default="")
    parser.add_argument("--api-key-desc", default="default")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--api-timeout", type=int, default=120)
    parser.add_argument("--resume", action="store_true", help="Try login for already-registered accounts to harvest API key")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    country = args.country or _random_country()
    info(f"QwenCloud bot | email={args.email} | country={country}")

    with sync_playwright() as p:
        browser_args = ["--disable-blink-features=AutomationControlled"]
        if args.proxy:
            browser = p.chromium.launch(
                headless=args.headless,
                args=browser_args,
                proxy=_split_proxy(args.proxy),
            )
        else:
            browser = p.chromium.launch(headless=args.headless, args=browser_args)

        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.set_default_timeout(30000)  # 30s — fail fast, let orchestrator retry

        # NOTE: resource blocking disabled — caused dashboard redirect timeouts.
        # Qwen Cloud SPA likely needs font/image resources for post-Continue navigation.

        try:
            page.goto("https://home.qwencloud.com/", wait_until="commit", timeout=30000)
            
            if _is_access_denied(page):
                browser.close()
                return _result({"status": "access-denied", "email": args.email})
            
            url = page.url
            title = page.title().lower()
            info(f"landing: {url} | {title}")
            
            # NEW UI: Landing page has "Get Started" link in the banner
            # Go directly to signup via Get Started or Sign Up link
            signup_attempts = 0
            while signup_attempts < 3:
                signup_attempts += 1
                
                # Wait for page to fully load
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except:
                    pass
                time.sleep(1)
                
                url = page.url.lower()
                title = page.title().lower()
                info(f"attempt {signup_attempts}: url={url} | title={title}")
                
                # Check if we're already on register page
                if "sso/register" in url:
                    info("Already on register page!")
                    break
                
                # Try Get Started link first (new UI landing page)
                try:
                    get_started = page.get_by_role("link", name="Get Started")
                    if get_started.count() > 0:
                        info(f"Found 'Get Started' link, clicking...")
                        get_started.click()
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=15000)
                        except:
                            pass
                        time.sleep(2)
                        info(f"After Get Started: url={page.url}")
                        continue
                except Exception as e:
                    info(f"Get Started click failed: {e}")
                
                # Try Sign Up link (on SSO login page)
                if "sso/login" in url or "login" in url:
                    try:
                        signup_link = page.get_by_role("link", name="Sign Up")
                        if signup_link.count() > 0:
                            info(f"Found 'Sign Up' link on login page, clicking...")
                            signup_link.click()
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=15000)
                            except:
                                pass
                            time.sleep(2)
                            info(f"After Sign Up: url={page.url}")
                            continue
                    except Exception as e:
                        info(f"Sign Up link click failed: {e}")
                
                # Try "Create account" or other signup buttons
                if "login" in url:
                    try:
                        create_btn = page.get_by_role("button", name=re.compile(r"(Create|Sign\s*up)", re.I))
                        if create_btn.count() > 0:
                            info(f"Found create/signup button, clicking...")
                            create_btn.first.click()
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=15000)
                            except:
                                pass
                            time.sleep(2)
                            continue
                    except Exception as e:
                        pass
                
                # If not on any known page, check what we see
                if signup_attempts >= 3:
                    try:
                        body_text = page.evaluate("() => document.body.innerText.substring(0, 500)")
                        info(f"Page body text: {body_text[:200]}")
                    except:
                        pass
                    return _result({"status": "error", "email": args.email, "reason": f"signup-page-not-found:url={url}:title={title}"})
            
            # Continue with signup flow from here
            res = _do_signup(page, args.email, country)
            if res["status"] == "already-registered":
                info(f"[{args.email}] already-registered, trying login to harvest API key")
                # On the SAME register page, look for "Log In" link instead of navigating to home
                try:
                    info("Looking for 'Log In' link on the current page...")
                    
                    # Check if we're still on register page
                    url_lower = page.url.lower()
                    if "/register" not in url_lower:
                        warn(f"Not on register page anymore: {page.url}")
                    
                    # Try multiple strategies to get to login page
                    success = False
                    
                    # Strategy 1: Direct URL change (simplest)
                    info("Strategy 1: Direct navigation to sso/login...")
                    try:
                        page.goto("https://account.alibabacloud.com/sso/login.htm?response_type=code&client_id=qwencloud", wait_until="domcontentloaded", timeout=15000)
                        time.sleep(2)
                        if "sso/login" in page.url.lower():
                            info("✅ Direct login nav successful!")
                            success = True
                    except Exception as e:
                        info(f"Direct nav failed: {e}")
                    
                    # Strategy 2: Find "Log In" or "Sign In" link on current page
                    if not success:
                        info("Strategy 2: Looking for 'Log In' link...")
                        try:
                            log_in_links = page.get_by_role("link", name=re.compile(r"(log|signin)", re.I))
                            if log_in_links.count() > 0:
                                log_in_links.first.click()
                                page.wait_for_load_state("domcontentloaded", timeout=15000)
                                time.sleep(2)
                                if "sso/login" in page.url.lower():
                                    info("✅ Found 'Log In' link!")
                                    success = True
                        except Exception as e:
                            info(f"Login link click failed: {e}")
                    
                    # Strategy 3: Click "Get Started" which redirects to login
                    if not success:
                        info("Strategy 3: Using Get Started → Login fallback...")
                        try:
                            get_started = page.get_by_role("link", name="Get Started")
                            if get_started.count() > 0:
                                get_started.click()
                                page.wait_for_load_state("domcontentloaded", timeout=15000)
                                time.sleep(2)
                                if "sso/login" in page.url.lower():
                                    info("✅ Get Started → Login successful!")
                                    success = True
                        except Exception as e:
                            info(f"Get Started fallback failed: {e}")
                    
                    if not success:
                        warn("All strategies failed! Current page:")
                        body_preview = page.evaluate("() => document.body.innerText.substring(0, 500)")
                        info(body_preview[:300])
                
                    login_res = _do_login(page, args.email)
                    if login_res["status"] != "login-ok":
                        browser.close()
                        return _result({**login_res, "email": args.email})
                        
                except Exception as e:
                    warn(f"Failed to transition to login: {e}")
                    browser.close()
                    return _result({"status": "error", "email": args.email, "reason": str(e)})
            elif res["status"] != "signup-ok":
                browser.close()
                return _result({**res, "email": args.email})

            # Create API key (with SSO recovery: if redirected to login, re-login)
            api_key = _create_api_key(page, args.api_key_desc)
            if not api_key:
                pg = _detect_page(page)
                if pg in ("login", "signup", "otp", "login_again"):
                    info(f"[{args.email}] SSO session lost (page={pg}), re-logging in")
                    try:
                        page.goto("https://home.qwencloud.com/", wait_until="commit", timeout=15000)
                        # Navigate to login page (new UI: Get Started → Login)
                        if "sso/login" not in page.url.lower():
                            try:
                                gs = page.get_by_role("link", name="Get Started")
                                if gs.count() > 0:
                                    gs.click()
                                    time.sleep(2)
                            except:
                                pass
                        login_res = _do_login(page, args.email)
                        if login_res["status"] == "login-ok":
                            api_key = _create_api_key(page, args.api_key_desc)
                    except Exception as e:
                        warn(f"re-login failed: {e}")
                if not api_key:
                    browser.close()
                    return _result({"status": "success-no-key", "email": args.email})
            browser.close()
            return _result({
                "status": "success",
                "email": args.email,
                "api_key": api_key,
                "base_url_openai": BASE_OPENAI,
                "base_url_anthropic": BASE_ANTHROPIC,
                "country": country,
            })
        except Exception as e:
            try:
                browser.close()
            except Exception:
                pass
            error(f"[{args.email}] unhandled: {e}")
            return _result({"status": "error", "email": args.email, "reason": str(e)})


if __name__ == "__main__":
    main()
