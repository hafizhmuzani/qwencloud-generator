#!/usr/bin/env python3
"""Check quota status for all QwenCloud accounts. Supports parallel testing."""
import argparse
import json
import os
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
ACCOUNTS_JSON = SCRIPT_DIR / "accounts.json"
QUOTA_RESULTS = SCRIPT_DIR / "quota-check.json"
API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"


def test_key(email, api_key):
    """Test a single key with a minimal request. Returns (email, status)."""
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

        # Parse error response
        if "Unpurchased" in body or "AccessDenied" in body:
            return (email, "exhausted")
        if status_code == 401:
            return (email, "invalid")

        # Other errors — try to be smart
        try:
            err = json.loads(body)
            err_msg = err.get("error", {}).get("message", "")
            if "Unpurchased" in err_msg or "exhausted" in err_msg.lower():
                return (email, "exhausted")
        except (json.JSONDecodeError, AttributeError):
            pass

        return (email, "unknown")

    except Exception as e:
        return (email, f"error:{e}")


def main():
    parser = argparse.ArgumentParser(description="Check quota status for all QwenCloud accounts")
    parser.add_argument("--parallel", type=int, default=10, help="Number of concurrent requests (default: 10)")
    parser.add_argument("--dry-run", action="store_true", help="Show accounts without testing")
    args = parser.parse_args()

    if not ACCOUNTS_JSON.exists():
        print(f"Error: {ACCOUNTS_JSON} not found")
        sys.exit(1)

    with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)

    accounts = {k: v for k, v in raw.items() if not k.startswith("_")}
    total = len(accounts)

    if args.dry_run:
        print(f"Would test {total} accounts with {args.parallel} workers")
        return

    print(f"Testing {total} accounts with {args.parallel} workers...")

    ok_count = 0
    exhausted_count = 0
    invalid_count = 0
    other_count = 0
    results = {}

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {
            executor.submit(test_key, email, info.get("api_key", info.get("key", ""))): email
            for email, info in accounts.items()
        }

        for i, future in enumerate(as_completed(futures), 1):
            email, status = future.result()
            results[email] = {"status": status}

            if status == "ok":
                ok_count += 1
                marker = "✓"
            elif status == "exhausted":
                exhausted_count += 1
                marker = "✗"
            elif status == "invalid":
                invalid_count += 1
                marker = "!"
            else:
                other_count += 1
                marker = "?"

            # Print progress
            print(f"  [{i}/{total}] {marker} {email[:40]:40s} → {status}")

    # Summary
    print()
    print(f"OK: {ok_count} | Exhausted: {exhausted_count} | Invalid: {invalid_count}", end="")
    if other_count:
        print(f" | Other: {other_count}")
    else:
        print()

    # Save results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "ok": ok_count,
            "exhausted": exhausted_count,
            "invalid": invalid_count,
            "other": other_count,
        },
        "results": results,
    }

    with open(QUOTA_RESULTS, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Results saved to {QUOTA_RESULTS}")


if __name__ == "__main__":
    main()
