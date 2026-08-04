#!/usr/bin/env python3
"""Tempmail Cloudflare client — poll inbox for Qoder verification codes.

Uses the user's Cloudflare Worker tempmail service:
    https://tempmail.hafizhmuzani.my.id

API (from tempmail-worker/src/index.js):
    POST /api/session                          -> {"sessionId": "..."}
    POST /api/inboxes  (x-session-id)          -> {"address": "..."}
    GET  /api/inboxes/:address/messages        -> [ {sender, subject, body_text, body_html, ...} ]
"""
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

BASE_URL = "https://tempmail.hafizhmuzani.my.id"
INBOX_DOMAIN = "hafizhmuzani.my.id"  # main domain with Cloudflare Email Routing (MX records verified)
STATE_FILE = Path(__file__).parent / "tempmail_state.json"  # {"sessionId": "...", "address": "..."}


def _req(method: str, path: str, body: Optional[dict] = None, session_id: Optional[str] = None, timeout: int = 15) -> dict:
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if session_id:
        headers["x-session-id"] = session_id
    headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def get_or_create_inbox() -> dict:
    """Create a fresh session + inbox (or reuse persisted one). Returns {"sessionId","address"}."""
    if STATE_FILE.exists():
        try:
            st = json.loads(STATE_FILE.read_text())
            if st.get("sessionId") and st.get("address"):
                return st
        except Exception:
            pass
    session = _req("POST", "/api/session")
    sid = session["sessionId"]
    inbox = _req("POST", "/api/inboxes", body={"domain": INBOX_DOMAIN}, session_id=sid)
    st = {"sessionId": sid, "address": inbox["address"]}
    STATE_FILE.write_text(json.dumps(st, indent=2))
    return st


def list_messages(session_id: str, address: str, timeout: int = 15) -> List[dict]:
    addr_q = urllib.parse.quote(address, safe="")
    try:
        return _req("GET", f"/api/inboxes/{addr_q}/messages", session_id=session_id, timeout=timeout)
    except Exception:
        return []


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def extract_code(text: str) -> Optional[str]:
    """Find a 6-digit verification code in email text/html."""
    if not text:
        return None
    # common patterns: "code is 123456", "verification code: 123456", standalone 6 digits
    m = re.search(r"(?:code|verification|otp)\D{0,30}?(\d{6})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    codes = re.findall(r"\b\d{6}\b", text)
    return codes[0] if codes else None


def wait_for_code(session_id: str, address: str, since_ms: int = 0, timeout: int = 120, poll_interval: float = 3.0) -> Optional[str]:
    """Poll tempmail until a verification code arrives (or timeout)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = list_messages(session_id, address)
        for m in msgs:
            if m.get("created_at", 0) < since_ms:
                continue
            body = m.get("body_text") or ""
            if not body and m.get("body_html"):
                body = _strip_html(m["body_html"])
            subject = m.get("subject") or ""
            code = extract_code(subject + " " + body)
            if code:
                return code
        time.sleep(poll_interval)
    return None


def reset():
    if STATE_FILE.exists():
        STATE_FILE.unlink()


if __name__ == "__main__":
    st = get_or_create_inbox()
    print(json.dumps(st))
    print("messages:", json.dumps(list_messages(st["sessionId"], st["address"]), indent=2)[:1000])
