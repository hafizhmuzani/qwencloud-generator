#!/usr/bin/env python3
"""Backup critical project files to a local backups/ folder.

Usage:
    python backup-accounts.py

Backs up: accounts.json, api_keys.txt, gmail_tokens.json, client_secret.json
Filename format: accounts_2026-08-05_1430.json
Also ensures backups/ is in .gitignore.
"""
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
BACKUP_DIR = PROJECT_DIR / "backups"
GITIGNORE = PROJECT_DIR / ".gitignore"

BACKUP_FILES = [
    "accounts.json",
    "api_keys.txt",
    "gmail_tokens.json",
    "client_secret.json",
]


def ensure_gitignore():
    """Ensure backups/ is in .gitignore."""
    if not GITIGNORE.exists():
        GITIGNORE.write_text("backups/\n", encoding="utf-8")
        print("  Created .gitignore with backups/")
        return

    content = GITIGNORE.read_text(encoding="utf-8")
    if "backups/" not in content:
        content = content.rstrip() + "\n\n# Local backups\nbackups/\n"
        GITIGNORE.write_text(content, encoding="utf-8")
        print("  Added backups/ to .gitignore")
    else:
        print("  backups/ already in .gitignore")


def backup_files():
    """Copy each backup file to backups/ with timestamped filename."""
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")

    copied = []
    skipped = []

    for fname in BACKUP_FILES:
        src = PROJECT_DIR / fname
        if not src.exists():
            skipped.append(fname)
            continue

        # accounts.json -> accounts_2026-08-05_1430.json
        stem = src.stem
        suffix = src.suffix
        dest = BACKUP_DIR / f"{stem}_{ts}{suffix}"

        shutil.copy2(str(src), str(dest))
        copied.append(dest.name)

    return copied, skipped


def main():
    print(f"[{datetime.now():%H:%M:%S}] Starting backup...")

    # 1. Ensure .gitignore has backups/
    ensure_gitignore()

    # 2. Copy files
    copied, skipped = backup_files()

    for f in copied:
        print(f"  [OK] {f}")
    for f in skipped:
        print(f"  [SKIP] {f} (not found)")

    # 3. Summary
    total_size = sum((BACKUP_DIR / f).stat().st_size for f in copied if (BACKUP_DIR / f).exists())
    print(f"\n[{datetime.now():%H:%M:%S}] Backup complete: {len(copied)} files, {total_size / 1024:.1f} KB")
    print(f"  Location: {BACKUP_DIR}")


if __name__ == "__main__":
    main()
