"""Weekly reading summary: one page summarizing the newsletters in the Reading
group, then archiving the ones the user never opened.

Each run writes ``data/.apps/email-review/reading_summaries/<date>.json`` with
the summary (markdown), every source email it was built from (sender, subject,
date, Fastmail link, whether it had been opened), and the threads it archived,
so the page can show the sources and undo the archive.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from email_review import fastmail
from email_review.claude_p import claude_p_completion

DATA_DIR = Path(os.environ.get("EMAIL_REVIEW_DATA_DIR", "data/.apps/email-review"))
SUMMARY_DIR = DATA_DIR / "reading_summaries"
READING_BUCKET = "9"
SUMMARY_MODEL = "claude-sonnet-5"
MAX_CHARS_PER_SOURCE = 6000
MAX_SOURCES = 60

SUMMARY_SYSTEM = (
    "You summarize a week of newsletters for a busy reader. Write markdown: a "
    "one-paragraph 'This week' overview, then sections by theme (### headings). "
    "Each point is one or two plain sentences and ends with the source number(s) "
    "in square brackets, like [3] or [2, 7]. Skip ads, sponsor blurbs, and "
    "housekeeping. Do not invent anything the newsletters do not say."
)

SOURCE_PROPERTIES = [
    "id",
    "threadId",
    "mailboxIds",
    "keywords",
    "from",
    "subject",
    "receivedAt",
    "textBody",
    "htmlBody",
    "bodyValues",
]


class NothingToSummarize(RuntimeError):
    """The Reading group has no mail from the window."""


def _strip_html(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def source_text(email: dict[str, Any]) -> str:
    values = email.get("bodyValues") or {}

    def parts(key: str) -> list[str]:
        return [(values.get(p.get("partId") or "") or {}).get("value", "") for p in email.get(key) or []]

    text = "\n".join(t for t in parts("textBody") if t.strip())
    if not text.strip():
        text = _strip_html("\n".join(parts("htmlBody")))
    return text.strip()[:MAX_CHARS_PER_SOURCE]


def reading_thread_ids(data: dict[str, Any], overrides: dict[str, str]) -> list[str]:
    """Thread ids currently in the Reading group, the user's manual moves applied."""
    buckets: dict[str, str] = {}
    for m in data.get("messages", []):
        buckets[m.get("threadId") or m.get("id")] = m.get("final_bucket", "")
    buckets.update({tid: b for tid, b in overrides.items() if tid in buckets})
    return [tid for tid, b in buckets.items() if b == READING_BUCKET]


def collect_sources(thread_ids: list[str], since: datetime) -> list[dict[str, Any]]:
    """The inbox emails of those threads received since ``since``, oldest first."""
    inbox = fastmail.resolve("INBOX")
    by_thread = fastmail.thread_email_ids(thread_ids)
    ids = [i for t in thread_ids for i in by_thread.get(t, [])]
    emails = fastmail.get_emails(ids, SOURCE_PROPERTIES, fetchTextBodyValues=True, fetchHTMLBodyValues=True)
    cutoff = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    emails = [e for e in emails if inbox in (e.get("mailboxIds") or {}) and (e.get("receivedAt") or "") >= cutoff]
    emails.sort(key=lambda e: e.get("receivedAt") or "")
    return emails[-MAX_SOURCES:]


def build_prompt(sources: list[dict[str, Any]]) -> str:
    blocks = [
        f"[{n}] From: {fastmail.format_addresses(e.get('from'))}\n"
        f"Subject: {e.get('subject', '')}\nDate: {e.get('receivedAt', '')}\n\n{source_text(e)}"
        for n, e in enumerate(sources, 1)
    ]
    return "Here are this week's newsletters, numbered:\n\n" + "\n\n=====\n\n".join(blocks)


def source_record(n: int, e: dict[str, Any]) -> dict[str, Any]:
    return {
        "n": n,
        "id": e["id"],
        "thread_id": e.get("threadId", ""),
        "from": fastmail.format_addresses(e.get("from")),
        "subject": e.get("subject") or "",
        "received_at": e.get("receivedAt") or "",
        "url": fastmail.web_url(e["id"], e.get("threadId", "")),
        "opened": bool((e.get("keywords") or {}).get("$seen")),
    }


def unopened_threads(sources: list[dict[str, Any]]) -> list[str]:
    """Threads none of whose summarized emails were opened."""
    opened: dict[str, bool] = {}
    for e in sources:
        tid = e.get("threadId", "")
        opened[tid] = opened.get(tid, False) or bool((e.get("keywords") or {}).get("$seen"))
    return [tid for tid, was_opened in opened.items() if not was_opened]


def run(days: int = 7, archive_unopened: bool = True, now: datetime | None = None) -> dict[str, Any]:
    """Summarize the Reading group's last ``days`` days, save it, and archive the
    threads never opened. Returns the saved record."""
    now = now or datetime.now(timezone.utc)
    data = json.loads((DATA_DIR / "data.json").read_text())
    overrides_path = DATA_DIR / "bucket_overrides.json"
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    sources = collect_sources(reading_thread_ids(data, overrides), now - timedelta(days=days))
    if not sources:
        raise NothingToSummarize(f"no Reading mail in the inbox from the last {days} days")

    summary = claude_p_completion(build_prompt(sources), system=SUMMARY_SYSTEM, model=SUMMARY_MODEL).text.strip()

    archived: list[str] = []
    if archive_unopened:
        for tid in unopened_threads(sources):
            fastmail.modify_thread(tid, add=["ARCHIVE"], remove=["INBOX"])
            archived.append(tid)

    record = {
        "created_at": now.isoformat(),
        "days": days,
        "summary_md": summary,
        "sources": [source_record(n, e) for n, e in enumerate(sources, 1)],
        "archived_thread_ids": archived,
        "archive_undone": False,
    }
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    (SUMMARY_DIR / f"{now.strftime('%Y-%m-%d')}.json").write_text(json.dumps(record, indent=2))
    return record


def latest() -> tuple[str, dict[str, Any]] | None:
    """(file name, record) of the newest saved summary, or None."""
    files = sorted(SUMMARY_DIR.glob("*.json")) if SUMMARY_DIR.is_dir() else []
    if not files:
        return None
    return files[-1].name, json.loads(files[-1].read_text())


def undo_archive(name: str) -> int:
    """Move the threads a summary archived back to the Inbox. Returns how many."""
    path = SUMMARY_DIR / Path(name).name
    record = json.loads(path.read_text())
    if record.get("archive_undone"):
        return 0
    for tid in record.get("archived_thread_ids", []):
        fastmail.modify_thread(tid, add=["INBOX"], remove=["ARCHIVE"])
    record["archive_undone"] = True
    path.write_text(json.dumps(record, indent=2))
    return len(record.get("archived_thread_ids", []))
