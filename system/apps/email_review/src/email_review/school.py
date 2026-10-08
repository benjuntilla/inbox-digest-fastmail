"""The School group (bucket 11): which senders are school senders, and which
buckets their threads are moved out of. Shared by classify.py and the AI
review pass so both apply the same rule."""

from __future__ import annotations

from typing import Any

from email_review import account

SCHOOL_BUCKET = "11"
# FYI, cold outreach (campus offices you never wrote to), notifications, reading.
# Reply needed, decisions, TODOs, and marketing keep their bucket.
SCHOOL_SOURCE_BUCKETS = {"3", "6", "8", "9"}


def is_school(addr: str) -> bool:
    """A sender on one of account.SCHOOL_DOMAINS or their subdomains."""
    d = addr.split("@", 1)[1].lower() if addr and "@" in addr else ""
    return any(d == s or d.endswith("." + s) for s in account.SCHOOL_DOMAINS)


def apply_school_group(threads: list[list[dict[str, Any]]]) -> int:
    """Move qualifying threads to the School bucket. Returns how many moved."""
    moved = 0
    for msgs in threads:
        if not msgs or msgs[0].get("final_bucket") not in SCHOOL_SOURCE_BUCKETS:
            continue
        if any(is_school(m.get("from_addr", "")) for m in msgs):
            for m in msgs:
                m["final_why"] = f"School sender (was {m['final_bucket']}: {m['final_why']})"
                m["final_bucket"] = SCHOOL_BUCKET
            moved += 1
    return moved
