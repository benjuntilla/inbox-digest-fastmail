#!/usr/bin/env python3
"""Fill contacts.txt from the Fastmail address book.

Everyone in your Fastmail contacts with an email address becomes a
`trusted-warm` row, so the classifier treats them as people you know rather
than cold outreach. Fastmail's own "Autosaved" group (addresses you have
written to) and any other group a person is in are noted in the notes column.

Skipped: your own addresses (account.ACCOUNT_ADDRS), automated senders
(classify.AUTOMATED_PATTERNS, e.g. noreply@), one-off relay addresses
(a long hex hash before the @, e.g. Craigslist replies), and any address that already
has a hand-written row in contacts.txt -- a hand-written category always wins.

The imported rows live in one managed block, between the BEGIN/END markers
below, in the app's own contacts file under data/ (email_review.contacts_files)
-- never in the skill's hand-written contacts.txt, so the weekly scheduled run
does not edit a file saved alongside the code. A re-run replaces that block
and touches nothing else (e.g. the "never unsubscribe" rows the page adds).
The raw cards are saved to data/.apps/email-review/fastmail_contacts.json.

Usage:
    uv run python .agents/skills/email-digest/scripts/import_fastmail_contacts.py          # dry run
    uv run python .agents/skills/email-digest/scripts/import_fastmail_contacts.py --write  # update the app's contacts file
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

from email_review import contacts_files, fastmail
from email_review.account import ACCOUNT_ADDRS

SCRIPTS_DIR = Path(__file__).resolve().parent
RAW_PATH = Path("data/.apps/email-review/fastmail_contacts.json")
BEGIN = "# ---- BEGIN Fastmail contacts (managed by import_fastmail_contacts.py; re-run to refresh) ----"
END = "# ---- END Fastmail contacts ----"

# One-off relay addresses (Craigslist replies and the like): a long hex hash
# as the local part. They never write again, so they are not contacts.
RELAY_LOCAL_PART = re.compile(r"^[0-9a-f]{24,}$")


def _automated_patterns() -> list[str]:
    spec = importlib.util.spec_from_file_location("classify_for_import", SCRIPTS_DIR / "classify.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.AUTOMATED_PATTERNS)


def split_managed(text: str) -> tuple[str, str]:
    """(contacts.txt without the managed block, the managed block's body)."""
    if BEGIN not in text:
        return text, ""
    head, rest = text.split(BEGIN, 1)
    body, _, tail = rest.partition(END)
    return head.rstrip("\n") + "\n" + tail.lstrip("\n"), body


def hand_written_addrs(text: str) -> set[str]:
    out: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.update(a.strip().lower() for a in line.split("\t")[0].split(",") if a.strip())
    return out


def build_rows(cards: list[dict[str, Any]], skip: set[str], automated: list[str]) -> list[str]:
    """One TAB-separated trusted-warm row per person with a usable address."""
    groups_of: dict[str, list[str]] = {}
    for c in cards:
        if c.get("kind") == "group":
            gname = ((c.get("name") or {}).get("full") or "").strip()
            for uid in c.get("members") or {}:
                if gname:
                    groups_of.setdefault(uid, []).append(gname)

    rows: list[str] = []
    seen: set[str] = set()
    for c in cards:
        if c.get("kind") == "group":
            continue
        addrs = []
        for e in (c.get("emails") or {}).values():
            a = (e.get("address") or "").strip().lower()
            if not a or "@" not in a or a in skip or a in seen:
                continue
            if any(p.lower() in a for p in automated):
                continue
            if RELAY_LOCAL_PART.match(a.split("@", 1)[0]):
                continue
            addrs.append(a)
            seen.add(a)
        if not addrs:
            continue
        name = " ".join(((c.get("name") or {}).get("full") or "").split()) or addrs[0]
        notes = "Fastmail contact"
        groups = sorted(set(groups_of.get(c.get("uid", ""), [])))
        if groups:
            notes += " (" + ", ".join(groups) + ")"
        rows.append("\t".join([",".join(addrs), name.replace("\t", " "), "trusted-warm", notes]))
    return sorted(rows, key=str.lower)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="update contacts.txt (default: dry run)")
    args = ap.parse_args()

    cards = fastmail.contact_cards()
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(cards, indent=2))

    hand = contacts_files.HAND_CONTACTS_PATH
    app_file = contacts_files.APP_CONTACTS_PATH
    text = app_file.read_text() if app_file.exists() else ""
    outside, _ = split_managed(text)
    hand_text = hand.read_text() if hand.exists() else ""
    skip = hand_written_addrs(hand_text) | hand_written_addrs(outside) | {a.lower() for a in ACCOUNT_ADDRS}
    rows = build_rows(cards, skip, _automated_patterns())

    print(f"{len(cards)} Fastmail cards -> {len(rows)} trusted-warm rows", file=sys.stderr)
    for r in rows[:10]:
        print("  " + r.replace("\t", " | "), file=sys.stderr)
    if not args.write:
        print(f"Dry run. Re-run with --write to update {app_file}.", file=sys.stderr)
        return 0
    block = "\n".join([BEGIN, *rows, END]) + "\n"
    app_file.parent.mkdir(parents=True, exist_ok=True)
    app_file.write_text((outside.rstrip("\n") + "\n\n" if outside.strip() else "") + block)
    print(f"Wrote {len(rows)} rows to {app_file}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
