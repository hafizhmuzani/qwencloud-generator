#!/usr/bin/env python3
"""Health checker + auto-recovery for 9Router QwenCloud connections.

Usage:
    python auto-recovery.py            # Run health checks + auto-recover failed slots
    python auto-recovery.py --dry-run  # Check health only, no DB changes

For each active Qwen connection in 9Router:
  1. Test API key against qwen-flash-character model
  2. If FAILED: mark connection as inactive (isActive=0, testStatus='failed')
  3. If spare accounts exist in accounts.json, auto-create replacement slots

Exit code 0 always (informational script).
"""
import argparse
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / "AppData/Roaming/9router/db/data.sqlite"
ACCOUNTS_JSON = Path(__file__).parent / "accounts.json"
PROVIDER_ID = "openai-compatible-chat-099c2f33-e34d-43e7-afcb-724d9c0c9d17"
TEST_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
TEST_MODEL = "qwen-flash-character"


def test_api_key(api_key, timeout=20):
    """Test a chat-completions call. Returns True if 200 with choices."""
    payload = json.dumps({
        "model": TEST_MODEL,
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 10,
    }).encode()
    req = urllib.request.Request(
        TEST_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = json.loads(resp.read().decode())
        return bool(body.get("choices"))
    except Exception:
        return False


def load_accounts():
    """Load accounts.json, skipping keys starting with _."""
    if not ACCOUNTS_JSON.exists():
        return {}
    data = json.loads(ACCOUNTS_JSON.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_9router_connections():
    """Load all Qwen connections from 9Router DB."""
    if not DB_PATH.exists():
        print(f"[ERROR] 9Router DB not found at {DB_PATH}")
        return []

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, email, isActive, data, priority FROM providerConnections WHERE provider=?",
        (PROVIDER_ID,),
    )
    rows = cur.fetchall()
    conn.close()

    connections = []
    for row_id, name, email, is_active, data_json, priority in rows:
        if not name or not re.match(r"^Qwen\d+$", name):
            continue
        d = json.loads(data_json) if data_json else {}
        connections.append({
            "id": row_id,
            "name": name,
            "email": email,
            "isActive": is_active,
            "priority": priority,
            "apiKey": d.get("apiKey", ""),
            "testStatus": d.get("testStatus", ""),
            "data": d,
        })
    return connections


def mark_connection_failed(conn_id, data):
    """Mark a connection as inactive with testStatus='failed'."""
    if not DB_PATH.exists():
        return

    data["testStatus"] = "failed"
    data["lastError"] = "auto-recovery health check failed"
    data["lastErrorAt"] = datetime.now().isoformat()

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "UPDATE providerConnections SET isActive=0, data=?, updatedAt=CURRENT_TIMESTAMP WHERE id=?",
        (json.dumps(data), conn_id),
    )
    conn.commit()
    conn.close()


def create_replacement_slot(email, api_key, max_slot):
    """Create a new 9Router connection slot for a replacement account."""
    import uuid

    next_slot = max_slot + 1
    slot_name = f"Qwen{next_slot}"
    new_id = str(uuid.uuid4())

    data = {
        "apiKey": api_key,
        "email": email,
        "name": email,
        "defaultModel": TEST_MODEL,
        "testStatus": "active",
        "providerSpecificData": {
            "prefix": "Qwen",
            "apiType": "chat",
            "baseUrl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "nodeName": "QwenCloud",
            "connectionProxyEnabled": False,
            "connectionProxyUrl": "",
            "connectionNoProxy": "",
        },
        "lastError": None,
        "errorCode": None,
        "lastErrorAt": None,
    }

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (new_id, PROVIDER_ID, "apikey", slot_name, email, next_slot, 1, json.dumps(data)),
    )
    conn.commit()
    conn.close()

    # Append to api_keys.txt
    keys_file = Path(__file__).parent / "api_keys.txt"
    with open(keys_file, "a", encoding="utf-8") as f:
        f.write(f"{email}|{api_key}\n")

    # Update accounts.json
    accounts = {}
    if ACCOUNTS_JSON.exists():
        accounts = json.loads(ACCOUNTS_JSON.read_text(encoding="utf-8"))
    accounts[email] = {
        "email": email,
        "status": "success",
        "api_key": api_key,
        "base_url_openai": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "country": "",
        "updated_at": datetime.now().isoformat(),
    }
    ACCOUNTS_JSON.write_text(json.dumps(accounts, indent=2, ensure_ascii=False), encoding="utf-8")

    return slot_name


def main():
    parser = argparse.ArgumentParser(description="9Router QwenCloud health checker + auto-recovery")
    parser.add_argument("--dry-run", action="store_true", help="Check health only, no DB changes")
    args = parser.parse_args()

    print(f"[{datetime.now():%H:%M:%S}] Starting health check{' (DRY RUN)' if args.dry_run else ''}...")
    if not DB_PATH.exists():
        print(f"[ERROR] 9Router DB not found: {DB_PATH}")
        sys.exit(1)

    # 1. Load connections
    connections = load_9router_connections()
    qwen_conns = [c for c in connections if c["name"].startswith("Qwen")]
    active_conns = [c for c in qwen_conns if c["isActive"]]

    print(f"  Total Qwen slots: {len(qwen_conns)}")
    print(f"  Active: {len(active_conns)}")

    # 2. Health check each active connection
    tested = 0
    passed = 0
    failed = 0
    failed_emails = []

    for conn_info in active_conns:
        tested += 1
        name = conn_info["name"]
        api_key = conn_info["apiKey"]

        if not api_key:
            print(f"  [FAIL] {name} — no API key")
            failed += 1
            failed_emails.append(conn_info["email"])
            if not args.dry_run:
                mark_connection_failed(conn_info["id"], conn_info["data"])
            continue

        ok = test_api_key(api_key)
        if ok:
            passed += 1
            print(f"  [OK]   {name}")
        else:
            failed += 1
            failed_emails.append(conn_info["email"])
            print(f"  [FAIL] {name} — API test failed")
            if not args.dry_run:
                mark_connection_failed(conn_info["id"], conn_info["data"])

    # 3. Auto-replace failed slots with spare accounts
    replaced = 0
    if failed_emails and not args.dry_run:
        accounts = load_accounts()
        emails_in_9router = {c["email"] for c in qwen_conns if c["email"]}
        spare_accounts = {
            email: info for email, info in accounts.items()
            if info.get("status") == "success"
            and email not in emails_in_9router
            and email not in set(failed_emails)
        }

        # Find max slot number
        max_slot = 0
        for c in qwen_conns:
            m = re.match(r"Qwen(\d+)", c["name"])
            if m:
                max_slot = max(max_slot, int(m.group(1)))

        # Replace up to failed count
        for email, info in list(spare_accounts.items()):
            if replaced >= failed:
                break
            slot_name = create_replacement_slot(email, info["api_key"], max_slot + replaced)
            replaced += 1
            print(f"  [REPLACED] {slot_name} ← {email}")

    # 4. Summary
    print(f"\n[{datetime.now():%H:%M:%S}] Health check complete:")
    print(f"  Tested: {tested}")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    if replaced:
        print(f"  Replaced: {replaced}")
    if args.dry_run and failed:
        print(f"\n  [DRY RUN] Would mark {failed} connections as failed")
        if failed_emails:
            print(f"  Affected: {', '.join(failed_emails[:5])}{'...' if len(failed_emails) > 5 else ''}")


if __name__ == "__main__":
    main()
