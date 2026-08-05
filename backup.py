#!/usr/bin/env python3
"""Backup accounts.json + api_keys.txt to a dated folder. Keep max 10 backups."""
import os
import shutil
import sys
import subprocess
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKUP_ROOT = SCRIPT_DIR / "backups"
MAX_BACKUPS = 10

FILES_TO_BACKUP = ["accounts.json", "api_keys.txt"]


def main():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = BACKUP_ROOT / timestamp

    if backup_dir.exists():
        print(f"Backup already exists: {backup_dir}")
        return

    backup_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for fname in FILES_TO_BACKUP:
        src = SCRIPT_DIR / fname
        if src.exists():
            shutil.copy2(src, backup_dir / fname)
            copied += 1
        else:
            print(f"  Warning: {fname} not found, skipping")

    if copied == 0:
        print("No files to backup!")
        shutil.rmtree(backup_dir)
        return

    # Prune old backups
    if BACKUP_ROOT.exists():
        all_backups = sorted(
            [d for d in BACKUP_ROOT.iterdir() if d.is_dir()],
            key=lambda d: d.name,
        )
        while len(all_backups) > MAX_BACKUPS:
            oldest = all_backups.pop(0)
            shutil.rmtree(oldest)
            print(f"  Pruned old backup: {oldest.name}")

    print(f"Backup done: {backup_dir}")

    # Git commit + push
    try:
        subprocess.run(
            ["git", "add", "backups/"],
            cwd=str(SCRIPT_DIR),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"backup: {timestamp[:10]}"],
            cwd=str(SCRIPT_DIR),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "push"],
            cwd=str(SCRIPT_DIR),
            check=True,
            capture_output=True,
        )
        print("Git committed and pushed.")
    except FileNotFoundError:
        print("Git not available, skipping commit/push.")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        if "nothing to commit" in stderr or "nothing to commit" in (e.stdout.decode() if e.stdout else ""):
            print("No git changes to commit.")
        else:
            print(f"Git error: {stderr}")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print("backup.py — Backup accounts.json + api_keys.txt")
        print("  Backups are saved to backups/YYYY-MM-DD_HHMMSS/")
        print("  Keeps max 10 backups. Auto-commits to git.")
    else:
        main()
