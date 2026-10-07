#!/usr/bin/env python3
"""Propagate the app's mute across thread follow-ups.

Background: when you (or the email-review smart-action) mute a thread, the
app files its messages in the `Muted` mailbox. Fastmail does NOT carry that
to *future* messages on the same thread — when a cold-outreach sender follows
up days later, the new message lands in the Inbox and shows up in the digest
again.

The fix: before each digest refresh, look at threads represented in the
current inbox (bounded by the digest's N-message window). For each, check
whether any message in the thread is in the Muted mailbox. If yes, move the
thread's inbox messages to Archive so they never reach the digest.

This bounds the work to ~N messages (default 100) instead of every muted
thread in the mailbox — which would be too slow on a long-lived account.

Usage:
    uv run python .agents/skills/email-digest/scripts/propagate_mutes.py [N]

Idempotent. Safe to run before every refresh.
"""

from __future__ import annotations

import sys
from typing import Any

from email_review import fastmail
from email_review.mail_actions import get_muted_label_id


def check_thread(
    thread_emails: list[dict[str, Any]], muted_id: str, inbox_id: str
) -> tuple[bool, list[str]]:
    """Return (has_muted_sibling, inbox_email_ids_to_archive) for one thread's
    emails (each with ``id`` and ``mailboxIds``)."""
    has_muted = any(muted_id in (e.get("mailboxIds") or {}) for e in thread_emails)
    if not has_muted:
        return False, []
    return True, [e["id"] for e in thread_emails if inbox_id in (e.get("mailboxIds") or {})]


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100

    muted_id = get_muted_label_id()
    inbox_id = fastmail.resolve("INBOX")

    print(f"Scanning {n} most recent inbox messages for muted-thread follow-ups…", file=sys.stderr)
    inbox_ids = fastmail.query_ids({"inMailbox": inbox_id}, n)
    print(f"  pulled {len(inbox_ids)} inbox messages", file=sys.stderr)

    thread_ids = sorted({e["threadId"] for e in fastmail.get_emails(inbox_ids, ["id", "threadId"])})
    print(f"  → {len(thread_ids)} unique threads to check", file=sys.stderr)

    by_thread = fastmail.thread_email_ids(thread_ids)
    all_ids = [i for ids in by_thread.values() for i in ids]
    emails = {e["id"]: e for e in fastmail.get_emails(all_ids, ["id", "mailboxIds"])}

    to_archive: list[dict[str, Any]] = []
    affected_threads = 0
    for ids in by_thread.values():
        is_muted, inbox_msgs = check_thread([emails[i] for i in ids if i in emails], muted_id, inbox_id)
        if is_muted and inbox_msgs:
            to_archive.extend(emails[i] for i in inbox_msgs)
            affected_threads += 1

    if to_archive:
        fastmail.modify_emails(to_archive, add=["ARCHIVE"], remove=["INBOX"])
    print(
        f"Propagated mute to {len(to_archive)} follow-up message(s) "
        f"across {affected_threads} thread(s).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
