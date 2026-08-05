#!/usr/bin/env python3
"""Auto-recover exhausted/invalid QwenCloud accounts by registering new ones."""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.request
    import urllib.error

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = Path.home() / "AppData" / "Roaming" / "9router" / "db" / "data.sqlite"
ACCOUNTS_JSON = SCRIPT_DIR / "accounts.json"
API_KEYS_TXT = SCRIPT_DIR / "api_keys.txt"
API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"


def test_key(email, api_key):
    """Test a single key. Returns (email, status)."""
    payload = json.dumps({
        "messages": [{"role": "user", "content": "1"}],
        "max_tokens": 1,
        "model": "qwen-flash-character",
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        if HAS_REQUESTS:
            resp = requests.post(API_URL, headers=headers, data=payload, timeout=30)
            status_code = resp.status_code
            body = resp.text
        else:
            req = urllib.request.Request(API_URL, data=payload, headers=headers, method="POST")
            try:
                resp = urllib.request.urlopen(req, timeout=30)
                status_code = resp.status
                body = resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                status_code = e.code
                body = e.read().decode("utf-8", errors="replace")

        if status_code == 200:
            return (email, "ok")

        if "Unpurchased" in body or "AccessDenied" in body:
            return (email, "exhausted")
        if status_code == 401:
            return (email, "invalid")

        try:
            err = json.loads(body)
            err_msg = err.get("error", {}).get("message", "")
            if "Unpurchased" in err_msg:
                return (email, "exhausted")
        except (json.JSONDecodeError, AttributeError):
            pass

        return (email, "unknown")

    except Exception as e:
        return (email, f"error:{str(e)[:80]}")


def get_slot_for_email(email):
    """Find the 9Router slot name for a given email."""
    if not DB_PATH.exists():
        return None

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, data FROM providerConnections WHERE name LIKE 'Qwen%'")
        for name, data_raw in cursor.fetchall():
            if not data_raw:
                continue
            try:
                data = json.loads(data_raw)
                if data.get("email") == email:
                    return name
            except (json.JSONDecodeError, TypeError):
                continue
    finally:
        conn.close()
    return None


def update_9router_slot(slot_name, new_email, new_api_key):
    """Update the 9Router DB slot with new credentials."""
    if not DB_PATH.exists() or not slot_name:
        return False

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT data FROM providerConnections WHERE name = ?", (slot_name,))
        row = cursor.fetchone()
        if not row:
            return False

        data = json.loads(row[0])
        data["email"] = new_email
        data["apiKey"] = new_api_key
        new_data = json.dumps(data)

        cursor.execute(
            "UPDATE providerConnections SET data = ?, email = ? WHERE name = ?",
            (new_data, new_email, slot_name),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"    DB update error for {slot_name}: {e}")
        return False
    finally:
        conn.close()


def register_new_account():
    """Register a new account via run.py. Returns (email, api_key) or None."""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "run.py"), "1", "--self", "--tempmail", "--log"],
            cwd=str(SCRIPT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )

        output = result.stdout + result.stderr

        # Look for new account in accounts.json after registration
        if ACCOUNTS_JSON.exists():
            with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
                raw = json.load(f)
            accounts = {k: v for k, v in raw.items() if not k.startswith("_")}
            # Find the newest account by updated_at
            newest_email = None
            newest_time = ""
            for email, info in accounts.items():
                ts = info.get("updated_at", "")
                if ts > newest_time:
                    newest_time = ts
                    newest_email = email

            if newest_email:
                api_key = accounts[newest_email].get("api_key", "")
                return (newest_email, api_key)

        print(f"    Registration output (last 300 chars): {output[-300:]}")
        return None

    except subprocess.TimeoutExpired:
        print("    Registration timed out (120s)")
        return None
    except Exception as e:
        print(f"    Registration error: {e}")
        return None


def rewrite_accounts_json(accounts):
    """Update accounts.json with given accounts dict."""
    data = dict(accounts)
    # Preserve metadata keys
    if ACCOUNTS_JSON.exists():
        with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            if k.startswith("_"):
                data[k] = v

    with open(ACCOUNTS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def rewrite_api_keys_txt(accounts):
    """Regenerate api_keys.txt from accounts dict."""
    lines = []
    for email, info in accounts.items():
        key = info.get("api_key", info.get("key", ""))
        if key:
            lines.append(f"{email}|{key}")

    with open(API_KEYS_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Auto-recover exhausted/invalid QwenCloud accounts")
    parser.add_argument("--parallel", type=int, default=10, help="Concurrent key test workers (default: 10)")
    parser.add_argument("--dry-run", action="store_true", help="Detect broken accounts without recovering")
    parser.add_argument("--skip-test", action="store_true", help="Skip testing, just register N new accounts")
    parser.add_argument("--count", type=int, default=1, help="Number of accounts to register (with --skip-test)")
    args = parser.parse_args()

    if not ACCOUNTS_JSON.exists():
        print(f"Error: {ACCOUNTS_JSON} not found")
        sys.exit(1)

    with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)

    accounts = {k: v for k, v in raw.items() if not k.startswith("_")}

    # --skip-test mode: just register N new accounts
    if args.skip_test:
        print(f"Registering {args.count} new accounts...")
        recovered = 0
        failed = 0
        for i in range(args.count):
            print(f"  [{i+1}/{args.count}] Registering...")
            result = register_new_account()
            if result:
                recovered += 1
                print(f"    ✓ New account: {result[0]}")
            else:
                failed += 1
                print(f"    ✗ Registration failed")

        # Reload and update files
        with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
            raw = json.load(f)
        accounts = {k: v for k, v in raw.items() if not k.startswith("_")}
        rewrite_accounts_json(accounts)
        rewrite_api_keys_txt(accounts)
        print(f"\nRecovered: {recovered} | Failed: {failed} | Total accounts: {len(accounts)}")
        return

    # Phase 1: Test all keys
    total = len(accounts)
    print(f"Phase 1: Testing {total} keys with {args.parallel} workers...")

    broken = {}  # email -> slot_name
    ok_count = 0

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {
            executor.submit(test_key, email, info.get("api_key", info.get("key", ""))): email
            for email, info in accounts.items()
        }

        for i, future in enumerate(as_completed(futures), 1):
            email, status = future.result()
            if status == "ok":
                ok_count += 1
                print(f"  [{i}/{total}] ✓ {email[:45]:45s} → ok")
            else:
                slot = get_slot_for_email(email)
                broken[email] = slot
                print(f"  [{i}/{total}] ✗ {email[:45]:45s} → {status} (slot: {slot or 'none'})")

    if not broken:
        print(f"\nAll {ok_count} keys are healthy. Nothing to recover.")
        return

    print(f"\nFound {len(broken)} broken keys. OK: {ok_count}")

    if args.dry_run:
        print("Dry run — not recovering.")
        for email, slot in broken.items():
            print(f"  {email} → slot {slot or 'unassigned'}")
        return

    # Phase 2: Recover broken accounts
    print(f"\nPhase 2: Recovering {len(broken)} accounts...")
    recovered = 0
    failed = 0

    for i, (old_email, slot) in enumerate(broken.items(), 1):
        print(f"  [{i}/{len(broken)}] Recovering {old_email} (slot: {slot or 'unassigned'})...")

        result = register_new_account()
        if not result:
            failed += 1
            print(f"    ✗ Registration failed")
            continue

        new_email, new_key = result
        print(f"    ✓ Registered: {new_email}")

        # Update 9Router slot
        if slot:
            if update_9router_slot(slot, new_email, new_key):
                print(f"    ✓ Updated 9Router slot {slot}")
            else:
                print(f"    ⚠ Could not update 9Router slot {slot}")

        # Update in-memory accounts
        del accounts[old_email]
        accounts[new_email] = {
            "email": new_email,
            "status": "success",
            "api_key": new_key,
            "country": "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        recovered += 1

    # Phase 3: Save updated data
    print("\nPhase 3: Saving updated data...")
    rewrite_accounts_json(accounts)
    rewrite_api_keys_txt(accounts)
    print(f"  ✓ accounts.json updated ({len(accounts)} accounts)")
    print(f"  ✓ api_keys.txt updated")

    # Git commit
    try:
        subprocess.run(
            ["git", "add", "accounts.json", "api_keys.txt"],
            cwd=str(SCRIPT_DIR), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"recovery: replaced {recovered} broken accounts"],
            cwd=str(SCRIPT_DIR), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push"], cwd=str(SCRIPT_DIR), check=True, capture_output=True,
        )
        print("  ✓ Git committed and pushed")
    except Exception as e:
        print(f"  ⚠ Git commit/push skipped: {e}")

    print(f"\nRecovered: {recovered} | Failed: {failed} | Total accounts: {len(accounts)}")


if __name__ == "__main__":
    main()
