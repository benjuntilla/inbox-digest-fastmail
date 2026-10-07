"""Draft a reply to a thread and save it in the user's Fastmail Drafts.

Nothing is ever sent: the draft lands in the Drafts mailbox (keyword
``$draft``) as a reply on the same thread, for the user to edit and send from
Fastmail. The reply text is written by Claude from the thread's recent
messages through the workspace's keyless account (email_review.claude_p).
"""

from __future__ import annotations

import re
from typing import Any

from email_review import fastmail
from email_review.account import ACCOUNT_ADDRS, ACCOUNT_NAME
from email_review.claude_p import claude_p_completion

DRAFT_MODEL = "claude-sonnet-5"
# How much of the thread Claude sees: the latest messages, each trimmed.
MAX_MESSAGES = 6
MAX_CHARS_PER_MESSAGE = 3000

DRAFT_SYSTEM = (
    "You write email replies on behalf of {name}. Write only the body of the reply: "
    "no subject line, no quoted thread. Match the tone and length of the thread; default to brief "
    "and friendly. Answer what the latest message asks. Never claim {first} has already "
    "done something (picked times, sent a file, signed up) that the thread does not "
    "show; where the reply needs a fact or a choice only {first} can supply, leave a "
    "short note in [square brackets] for them to fill in. Sign off with just the first "
    "name, {first}."
)

THREAD_PROPERTIES = [
    "id",
    "threadId",
    "mailboxIds",
    "messageId",
    "references",
    "from",
    "to",
    "cc",
    "replyTo",
    "subject",
    "receivedAt",
    "textBody",
    "bodyValues",
]


class NothingToReplyTo(RuntimeError):
    """The thread has no message from someone other than the user."""


def _own(addr: str) -> bool:
    return addr.lower() in {a.lower() for a in ACCOUNT_ADDRS}


def _body_text(email: dict[str, Any]) -> str:
    values = email.get("bodyValues") or {}
    parts = [(values.get(p.get("partId") or "") or {}).get("value", "") for p in email.get("textBody") or []]
    text = "\n".join(parts)
    # Drop the quoted history most clients append; the earlier messages are
    # already in the thread we pass along.
    text = re.split(r"\n(?:On .{0,200}wrote:|-{2,} ?Original Message ?-{2,})", text, maxsplit=1)[0]
    return text.strip()[:MAX_CHARS_PER_MESSAGE]


def load_thread(thread_id: str) -> list[dict[str, Any]]:
    """The thread's emails (drafts excluded), oldest first, with text bodies."""
    ids = fastmail.thread_email_ids([thread_id]).get(thread_id, [])
    emails = fastmail.get_emails(ids, THREAD_PROPERTIES, fetchTextBodyValues=True)
    drafts_id = fastmail.mailbox_id_for_role("drafts")
    emails = [e for e in emails if drafts_id not in (e.get("mailboxIds") or {})]
    return sorted(emails, key=lambda e: e.get("receivedAt") or "")


def latest_inbound(emails: list[dict[str, Any]]) -> dict[str, Any]:
    for e in reversed(emails):
        senders = e.get("from") or []
        if senders and not _own(senders[0].get("email") or ""):
            return e
    raise NothingToReplyTo("there is no message from someone else on this thread to reply to")


def reply_identity(inbound: dict[str, Any]) -> str:
    """The user's address the inbound was sent to, so the reply goes out from
    the same one; the first configured address otherwise."""
    for a in (inbound.get("to") or []) + (inbound.get("cc") or []):
        addr = (a.get("email") or "").lower()
        if _own(addr):
            return addr
    return sorted(ACCOUNT_ADDRS)[0]


def build_prompt(emails: list[dict[str, Any]]) -> str:
    blocks = []
    for e in emails[-MAX_MESSAGES:]:
        blocks.append(
            f"From: {fastmail.format_addresses(e.get('from'))}\n"
            f"To: {fastmail.format_addresses(e.get('to'))}\n"
            f"Date: {e.get('receivedAt', '')}\n"
            f"Subject: {e.get('subject', '')}\n\n{_body_text(e)}"
        )
    return "Here is the email thread, oldest first:\n\n" + "\n\n-----\n\n".join(blocks) + (
        "\n\n-----\n\nWrite the reply to the latest message from someone other than me."
    )


def reply_subject(subject: str) -> str:
    return subject if re.match(r"^\s*re:", subject or "", re.I) else f"Re: {subject or ''}".strip()


def build_draft(emails: list[dict[str, Any]], body: str) -> dict[str, Any]:
    """The JMAP Email object for the reply draft."""
    inbound = latest_inbound(emails)
    recipients = inbound.get("replyTo") or inbound.get("from") or []
    references = list(inbound.get("references") or []) + list(inbound.get("messageId") or [])
    return {
        "mailboxIds": {fastmail.mailbox_id_for_role("drafts"): True},
        "keywords": {"$draft": True, "$seen": True},
        "from": [{"name": ACCOUNT_NAME, "email": reply_identity(inbound)}],
        "to": [{"name": r.get("name"), "email": r.get("email")} for r in recipients],
        "subject": reply_subject(inbound.get("subject") or ""),
        "inReplyTo": inbound.get("messageId") or None,
        "references": references or None,
        "bodyValues": {"body": {"value": body}},
        "textBody": [{"partId": "body", "type": "text/plain"}],
    }


def draft_reply(thread_id: str) -> dict[str, Any]:
    """Write a reply for the thread with Claude and save it to Drafts.
    Returns the new draft's id, its Fastmail link, and the text."""
    emails = load_thread(thread_id)
    draft_shape = build_draft(emails, "")  # fail fast before spending a model call
    first = ACCOUNT_NAME.split()[0]
    body = claude_p_completion(
        build_prompt(emails),
        system=DRAFT_SYSTEM.format(name=ACCOUNT_NAME, first=first),
        model=DRAFT_MODEL,
    ).text.strip()
    draft_shape["bodyValues"] = {"body": {"value": body}}
    res = fastmail.call_one("Email/set", {"create": {"draft": draft_shape}})
    created = (res.get("created") or {}).get("draft")
    if not created:
        raise fastmail.JmapError(f"Fastmail refused the draft: {res.get('notCreated')}")
    return {
        "draft_id": created["id"],
        "thread_id": created.get("threadId", thread_id),
        "url": fastmail.web_url(created["id"], created.get("threadId", thread_id), mailbox="Drafts"),
        "text": body,
    }
