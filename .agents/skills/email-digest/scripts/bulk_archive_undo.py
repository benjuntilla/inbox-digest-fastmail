#!/usr/bin/env python3
"""Undo a previous bulk_archive run by moving every archived id back to the Inbox.

Usage:
    uv run python .agents/skills/email-digest/scripts/bulk_archive_undo.py last_run_20260517-203000.json

Pass the filename of a record from runtime/bulk_archive/. The script moves
every id listed in `archived_ids` from Archive back to Inbox. Safe to re-run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from email_review import fastmail

REPO_ROOT = Path(__file__).resolve().parents[4]
WORK_DIR = REPO_ROOT / "runtime/bulk_archive"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    name = sys.argv[1]
    path = Path(name) if Path(name).is_absolute() else WORK_DIR / name
    if not path.exists():
        print(f"Run record not found: {path}", file=sys.stderr)
        return 1
    record = json.loads(path.read_text())
    ids = record.get("archived_ids", [])
    print(f"Restoring {len(ids)} messages to INBOX (from query: {record.get('query')!r})…",
          file=sys.stderr)
    for i in range(0, len(ids), fastmail.BATCH):
        batch = ids[i : i + fastmail.BATCH]
        fastmail.modify_message_ids(batch, add=["INBOX"], remove=["ARCHIVE"])
        print(f"  restored {i + len(batch)}/{len(ids)}", file=sys.stderr)
    print("Done.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
