#!/usr/bin/env python3
"""Refresh qwencloud-monitor.html with latest account data from accounts.json + 9Router DB."""
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = Path.home() / "AppData" / "Roaming" / "9router" / "db" / "data.sqlite"
ACCOUNTS_JSON = SCRIPT_DIR / "accounts.json"
DASHBOARD_HTML = SCRIPT_DIR / "qwencloud-monitor.html"


def get_slot_map():
    """Read 9Router DB to get email → slot (QwenN) mapping."""
    slot_map = {}  # email -> slot_name
    if not DB_PATH.exists():
        print(f"  Warning: 9Router DB not found at {DB_PATH}")
        return slot_map

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, data FROM providerConnections WHERE name LIKE 'Qwen%'")
        for name, data_raw in cursor.fetchall():
            if not data_raw:
                continue
            try:
                data = json.loads(data_raw)
                email = data.get("email", "")
                if email:
                    slot_map[email] = name
            except (json.JSONDecodeError, TypeError):
                continue
    finally:
        conn.close()

    return slot_map


def get_slot_number(slot_name):
    """Extract number from 'QwenN' for sorting."""
    m = re.match(r"Qwen(\d+)", slot_name or "")
    return int(m.group(1)) if m else 9999


def main():
    # Load accounts.json
    if not ACCOUNTS_JSON.exists():
        print(f"Error: {ACCOUNTS_JSON} not found")
        sys.exit(1)

    with open(ACCOUNTS_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Filter out metadata keys
    accounts = {k: v for k, v in raw.items() if not k.startswith("_")}

    # Get slot mapping from 9Router
    slot_map = get_slot_map()

    # Build dashboard entries
    entries = []
    for email, info in accounts.items():
        slot = slot_map.get(email, "")
        entries.append({
            "slot": slot,
            "email": email,
            "key": info.get("api_key", info.get("key", "")),
            "country": info.get("country", ""),
            "registered": info.get("updated_at", ""),
            "status": "ok",
        })

    # Sort by slot number
    entries.sort(key=lambda e: get_slot_number(e["slot"]))

    # Read HTML
    if not DASHBOARD_HTML.exists():
        print(f"Error: {DASHBOARD_HTML} not found")
        sys.exit(1)

    with open(DASHBOARD_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    # Replace const ACCOUNTS = [...];
    # Match from "const ACCOUNTS = [" to the closing "];"
    new_accounts_json = json.dumps(entries, indent=2, ensure_ascii=False)
    new_block = "const ACCOUNTS = " + new_accounts_json + ";"

    pattern = r"const ACCOUNTS\s*=\s*\[.*?\];"
    new_html, count = re.subn(pattern, new_block, html, flags=re.DOTALL)
    if count == 0:
        print("Error: Could not find 'const ACCOUNTS = [...];' in HTML")
        sys.exit(1)

    # Update title header: "QwenCloud Monitor — N Accounts"
    n = len(entries)
    new_html = re.sub(
        r"QwenCloud Monitor — \d+ Accounts",
        f"QwenCloud Monitor — {n} Accounts",
        new_html,
    )

    with open(DASHBOARD_HTML, "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"Dashboard refreshed: {n} accounts")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print("refresh-dashboard.py — Update qwencloud-monitor.html with latest data")
        print("  Reads accounts.json + 9Router DB for slot mapping")
        print("  Replaces ACCOUNTS array in the HTML dashboard")
    else:
        main()
