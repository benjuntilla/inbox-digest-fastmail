"""Where the who's-who rows live, and reading them.

Two files, same TAB-separated format (email-or-domain, name, category, notes):

- the skill's ``contacts.txt`` -- rows you write by hand (or ask the agent to);
- ``data/.apps/email-review/contacts.txt`` -- rows the app writes itself: the
  weekly Fastmail address-book import (a managed block it replaces each run)
  and senders marked "Keep me subscribed" on the digest page.

Keeping app-written rows under data/ means the scheduled import never edits a
file that is saved alongside the code. Readers use both; a hand-written row
wins over an imported one for the same address (the importer skips it).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

# Anchored on the repo root (system/apps/email_review/src/email_review/contacts_files.py -> parents[5]),
# so the paths hold whatever the caller's working directory is.
REPO_ROOT = Path(__file__).resolve().parents[5]
HAND_CONTACTS_PATH = REPO_ROOT / ".agents/skills/email-digest/contacts.txt"
APP_CONTACTS_PATH = (
    Path(os.environ.get("EMAIL_REVIEW_DATA_DIR") or REPO_ROOT / "data/.apps/email-review") / "contacts.txt"
)


def rows(paths: tuple[Path, ...] | None = None) -> Iterator[tuple[set[str], str, str]]:
    """(addresses, name, category) for every row in the contact files."""
    for path in paths or (HAND_CONTACTS_PATH, APP_CONTACTS_PATH):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            addrs = {a.strip() for a in parts[0].strip().lower().split(",") if a.strip()}
            yield addrs, parts[1].strip(), parts[2].strip()
