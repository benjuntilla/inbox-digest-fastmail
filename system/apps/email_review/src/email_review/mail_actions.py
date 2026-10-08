"""Mail action helpers — archive/spam/mute/unsubscribe on Fastmail (JMAP) via
latchkey.

These are sync, called from FastAPI route handlers. They all return the set
of labels removed/added so the undo endpoint can reverse the change. "Labels"
are the mailbox tokens defined in email_review.fastmail (INBOX, ARCHIVE, SPAM,
or a mailbox id).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from email_review import contacts_files, fastmail
from email_review.account import ORG_DOMAINS
from email_review.fastmail import GatewayUnreachable

__all__ = ["GatewayUnreachable"]

# Fastmail has no API-visible "mute", so the app keeps its own top-level
# mailbox (named below) and files muted threads there. The mailbox id is
# resolved (and created on first use) at runtime — see get_muted_label_id().
MUTED_LABEL_NAME = "Muted"


# Domains you are internal to (from account.ORG_DOMAINS). When a
# List-Unsubscribe URL or mailto target lives under one of these, treat the
# link as an internal forwarder / manage-subscription page (e.g. a company
# Google Group) and refuse the unsubscribe — clicking it would remove you from
# the forwarder entirely rather than from the real sender's list.
INTERNAL_FORWARDER_DOMAINS = tuple(ORG_DOMAINS)


# Phishing detection lives in email_review.phishing (shared with classify.py).
# Use the same function in both places so a detection-gap fix lands once.
from email_review.phishing import has_phishing_tells as _has_phishing_tells


def _looks_like_phishing(sender: str, subject: str, snippet: str = "") -> bool:
    """Smart-action's phishing check — delegates to the shared detector.
    Kept as a small wrapper so callers can pass just (sender, subject) when
    they don't have the snippet handy (the shared function accepts both)."""
    return _has_phishing_tells(subject or "", sender or "", snippet or "", None)


def _is_internal_forwarder_unsub(list_unsub_header: str) -> bool:
    """Return True if the List-Unsubscribe header points back at one of your
    own domains (i.e. it's an internal Google Group / forwarder, not a real
    third-party mailing list)."""
    if not list_unsub_header:
        return False
    lower = list_unsub_header.lower()
    return any(d in lower for d in INTERNAL_FORWARDER_DOMAINS)


def _sender_addr(from_header: str) -> str:
    m = re.search(r"<([^>]+)>", from_header or "")
    return (m.group(1) if m else (from_header or "")).strip().lower()


def _sender_name(from_header: str) -> str:
    name = (from_header or "").split("<")[0].strip().strip('"').strip()
    return name or _sender_addr(from_header)


def _has_usable_unsubscribe(list_unsub: str) -> bool:
    """Whether try_unsubscribe could act on this header (a web link). Email-only
    unsubscribes are never sent, so they need no confirmation."""
    return bool(re.search(r"<https?://[^>]+>", list_unsub or ""))


class NotAnEmailAddress(ValueError):
    """add_keep_subscribed was given something that is not an address."""


KEEP_SUBSCRIBED_SECTION = "# ---- never unsubscribe (added from the inbox digest page) ----"


def add_keep_subscribed(addr: str, name: str = "") -> bool:
    """Protect a sender from ever being unsubscribed: add a keep-subscribed row
    to the app's contacts file, in its own section above the imported block.
    Returns False if the address is already protected."""
    addr = (addr or "").strip().lower()
    if not addr or "@" not in addr:
        raise NotAnEmailAddress(f"not an email address: {addr!r}")
    if _is_keep_subscribed(addr):
        return False
    row = "\t".join([addr, (name or addr).replace("\t", " "), "keep-subscribed", "marked from the digest page"])
    path = contacts_files.APP_CONTACTS_PATH
    text = path.read_text() if path.exists() else ""
    if KEEP_SUBSCRIBED_SECTION in text:
        head, _, tail = text.partition(KEEP_SUBSCRIBED_SECTION + "\n")
        text = head + KEEP_SUBSCRIBED_SECTION + "\n" + row + "\n" + tail
    else:
        marker = "# ---- BEGIN Fastmail contacts"
        block = KEEP_SUBSCRIBED_SECTION + "\n" + row + "\n\n"
        text = text.replace(marker, block + marker, 1) if marker in text else text.rstrip("\n") + "\n\n" + block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def _load_keep_subscribed() -> set[str]:
    """The keep-subscribed entries from both contact files. These are senders
    the user wants to stay subscribed to — smart-action archives them but must
    NEVER unsubscribe.
    """
    out: set[str] = set()
    for addrs, _name, category in contacts_files.rows():
        if category == "keep-subscribed":
            out.update(addrs)
    return out


def _is_keep_subscribed(addr_or_full_from: str) -> bool:
    """Match either a full email (foo@bar.com) or bare domain (bar.com)."""
    keep = _load_keep_subscribed()  # reload each call so contacts.txt edits take effect immediately
    if not keep or not addr_or_full_from:
        return False
    m = re.search(r"<([^>]+)>", addr_or_full_from)
    addr = (m.group(1) if m else addr_or_full_from).lower()
    if addr in keep:
        return True
    d = addr.split("@", 1)[1] if "@" in addr else ""
    return d in keep


def get_muted_label_id() -> str:
    """Return the id of the app's "Muted" mailbox, creating it on first use."""
    return fastmail.ensure_mailbox(MUTED_LABEL_NAME)


def modify_thread(thread_id: str, add: list[str], remove: list[str]) -> list[str]:
    return fastmail.modify_thread(thread_id, add, remove)


def get_message_headers(message_id: str) -> dict[str, str]:
    """Return a lowercase-keyed dict of the headers smart-action reads."""
    found = fastmail.get_emails([message_id])
    if not found:
        return {}
    e = found[0]
    headers = {
        "from": fastmail.format_addresses(e.get("from")),
        "to": fastmail.format_addresses(e.get("to")),
        "subject": e.get("subject") or "",
    }
    unsub = fastmail.raw_header(e, "List-Unsubscribe")
    if unsub:
        headers["list-unsubscribe"] = unsub
    post = fastmail.raw_header(e, "List-Unsubscribe-Post")
    if post:
        headers["list-unsubscribe-post"] = post
    return headers


def archive(thread_id: str) -> dict[str, Any]:
    """Move a thread's inbox messages to Archive. Returns the label diff for undo."""
    modify_thread(thread_id, add=["ARCHIVE"], remove=["INBOX"])
    return {"action": "archived", "thread_id": thread_id, "added": ["ARCHIVE"], "removed": ["INBOX"]}


def unarchive(thread_id: str) -> dict[str, Any]:
    modify_thread(thread_id, add=["INBOX"], remove=["ARCHIVE"])
    return {"action": "unarchived", "thread_id": thread_id}


def mark_spam(thread_id: str) -> dict[str, Any]:
    modify_thread(thread_id, add=["SPAM"], remove=["INBOX"])
    return {"action": "spam", "thread_id": thread_id, "added": ["SPAM"], "removed": ["INBOX"]}


def unspam(thread_id: str) -> dict[str, Any]:
    modify_thread(thread_id, add=["INBOX"], remove=["SPAM"])
    return {"action": "unspammed", "thread_id": thread_id}


def mute(thread_id: str) -> dict[str, Any]:
    """File the thread's inbox messages in the app's Muted mailbox."""
    label_id = get_muted_label_id()
    modify_thread(thread_id, add=[label_id], remove=["INBOX"])
    return {"action": "muted", "thread_id": thread_id, "added": [label_id], "removed": ["INBOX"]}


def unmute(thread_id: str) -> dict[str, Any]:
    label_id = get_muted_label_id()
    modify_thread(thread_id, add=["INBOX"], remove=[label_id])
    return {"action": "unmuted", "thread_id": thread_id}


def try_unsubscribe(message_id: str, thread_id: str) -> tuple[bool, str]:
    """Best-effort unsubscribe using the message's List-Unsubscribe header.

    Returns (succeeded, method_description). When the header is absent or
    the request fails, returns (False, reason) and the caller should fall
    back to mute or archive.
    """
    headers = get_message_headers(message_id)
    list_unsub = headers.get("list-unsubscribe", "")
    if not list_unsub:
        return False, "no List-Unsubscribe header"

    if _is_internal_forwarder_unsub(list_unsub):
        return False, (
            f"List-Unsubscribe targets your own domain "
            f"({list_unsub[:80]}) — refusing (internal forwarder)"
        )

    # Try HTTPS URL first. RFC8058 (List-Unsubscribe-Post) means we POST
    # `List-Unsubscribe=One-Click` to the URL. Otherwise GET it.
    http_match = re.search(r"<(https?://[^>]+)>", list_unsub)
    if http_match:
        url = http_match.group(1)
        try:
            if "list-unsubscribe-post" in headers:
                data = urllib.parse.urlencode({"List-Unsubscribe": "One-Click"}).encode()
                req = urllib.request.Request(url, data=data, method="POST")
            else:
                req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                ok = 200 <= resp.status < 400
            return ok, f"HTTP unsubscribe to {url}"
        except (urllib.error.URLError, TimeoutError) as e:
            return False, f"HTTP unsubscribe failed: {e}"

    # Mailto-only unsubscribe would mean sending mail from your account, which
    # this app is deliberately not allowed to do. Report it so the caller falls
    # back to mute.
    mailto_match = re.search(r"<mailto:([^>]+)>", list_unsub)
    if mailto_match:
        return False, f"Unsubscribe is by email only ({mailto_match.group(1)[:60]}) — not sent"

    return False, "List-Unsubscribe header had no usable URL or mailto"


def smart_action(
    message_id: str,
    thread_id: str,
    bucket: str,
    snippet: str = "",
    sender: str = "",
    subject: str = "",
    confirm_unsubscribe: bool = False,
) -> dict[str, Any]:
    """Apply the smart-action ruleset:

    - **If a usable unsubscribe option exists, unsubscribe** -- for ANY bucket,
      including cold outreach (bucket 6). Uses the message's List-Unsubscribe
      header (one-click POST / GET / mailto).
    - When there is no usable List-Unsubscribe header, fall back to **mute**
      for the subscription-type and cold-outreach buckets (6/7/8/9) — muting
      applies the app's Muted label and suppresses follow-ups without
      confirming a live address — or **archive** otherwise.
    - Mark as spam if it looks like spam (suspicious sender / phishing tells).

    Three protective exceptions override "always unsubscribe" -- each would harm
    the user rather than just leave a mailing list:
      - keep-subscribed senders (contacts.txt) -> archive only;
      - internal forwarders (List-Unsubscribe on one of your own domains, e.g. a
        company Google Group) -> archive only (unsubscribing drops you from the
        group);
      - phishing (bucket 7 tells) -> mark spam (never click a phisher's link).

    **Every path archives** (takes the thread out of INBOX). Mute, spam, and
    unsubscribe additionally apply their own label/flag, but the thread
    always leaves the inbox so the digest doesn't show it again. Encoded
    as an invariant — each `modify_thread` call below MUST include
    `remove=["INBOX"]` (or be followed by an archive call) regardless of
    the action.

    Returns a dict describing what happened so the toast can show it and
    the undo handler can reverse it.
    """
    # 0. Keep-subscribed override: if the sender is on the keep-subscribed
    # list (contacts.txt), never unsubscribe — just archive. The user
    # explicitly wants to stay on these mailing lists.
    if _is_keep_subscribed(sender):
        modify_thread(thread_id, add=["ARCHIVE"], remove=["INBOX"])
        return {
            "action": "archived",
            "thread_id": thread_id,
            "detail": "Sender is keep-subscribed (per contacts.txt) — archived only",
            "undo_add": ["INBOX"],
            "undo_remove": ["ARCHIVE"],
        }

    # 0.5: Phishing/impersonation in bucket 7 → mark as spam (NOT unsubscribe).
    # The unsubscribe flow trusts the sender's list-unsubscribe header, which
    # is exactly what phishers exploit; marking as spam is the right action
    # because it both removes from inbox AND teaches Fastmail's spam filter. Detect
    # via the same heuristics as classify.py:
    #   - Display name impersonates you but the From address isn't yours.
    #   - Subject matches a known phishing signature (INV/PO with random
    #     alphanumeric suffix, gift-card scams, signature-request spoofing).
    if bucket == "7" and _looks_like_phishing(sender, subject, snippet):
        modify_thread(thread_id, add=["SPAM"], remove=["INBOX"])
        return {
            "action": "spam",
            "thread_id": thread_id,
            "detail": "Phishing tells (display-name impersonation or signature-spam subject) — marked as spam",
            "undo_add": ["INBOX"],
            "undo_remove": ["SPAM"],
        }

    # 1. If a usable unsubscribe option exists, unsubscribe -- for ANY bucket
    # (including cold outreach, bucket 6). The protective exceptions are the
    # keep-subscribed check above (step 0), the phishing check above (step
    # 0.5), and the internal-forwarder check below.
    headers = get_message_headers(message_id)
    list_unsub = headers.get("list-unsubscribe", "")

    # Internal forwarder (List-Unsubscribe on one of your own domains, e.g. a
    # company Google Group): unsubscribing would drop you from the group.
    # Archive only -- never unsubscribe.
    if list_unsub and _is_internal_forwarder_unsub(list_unsub):
        modify_thread(thread_id, add=["ARCHIVE"], remove=["INBOX"])
        return {
            "action": "archived",
            "thread_id": thread_id,
            "detail": "List-Unsubscribe link is on your own domain — internal forwarder, archived only",
            "undo_add": ["INBOX"],
            "undo_remove": ["ARCHIVE"],
        }

    # A real List-Unsubscribe option is present -> try to unsubscribe, but
    # only once the user has said yes: an unsubscribe cannot be undone from
    # here, so the first call changes nothing and asks instead.
    if list_unsub and not confirm_unsubscribe and _has_usable_unsubscribe(list_unsub):
        return {
            "action": "needs_confirm",
            "thread_id": thread_id,
            "sender": sender,
            "sender_addr": _sender_addr(sender),
            "sender_name": _sender_name(sender),
            "detail": "Unsubscribing can't be undone; waiting for your confirmation",
        }
    if list_unsub:
        unsub_ok, unsub_reason = try_unsubscribe(message_id, thread_id)
        if unsub_ok:
            modify_thread(thread_id, add=["ARCHIVE"], remove=["INBOX"])
            return {
                "action": "unsubscribed",
                "thread_id": thread_id,
                "detail": unsub_reason,
                "undo_add": ["INBOX"],
                "undo_remove": ["ARCHIVE"],
            }
        # Header present but the direct call failed -> fall through to the
        # mute/archive fallback below.

    # 2. No usable unsubscribe. For the subscription-type buckets (7 marketing,
    # 8 in-product, 9 reading) and cold outreach (bucket 6), mute the thread:
    # apply the Muted label and remove INBOX so follow-ups on the thread stop
    # reaching the digest, without confirming a live address to the sender.
    if bucket in {"6", "7", "8", "9"}:
        label_id = get_muted_label_id()
        modify_thread(thread_id, add=[label_id], remove=["INBOX"])
        return {
            "action": "muted",
            "thread_id": thread_id,
            "detail": "No usable unsubscribe option — muted (Muted label applied, removed from inbox)",
            "undo_add": ["INBOX"],
            "undo_remove": [label_id],
        }

    # 3. Spammy heuristic: phishing tells in snippet, or sender domain suspicious
    snip_low = (snippet or "").lower()
    spam_tells = ["unsubscribe.*ach", "process the attached invoice via ach", "wire transfer"]
    if any(re.search(p, snip_low) for p in spam_tells):
        modify_thread(thread_id, add=["SPAM"], remove=["INBOX"])
        return {
            "action": "spam",
            "thread_id": thread_id,
            "detail": "Phishing tells in snippet",
            "undo_add": ["INBOX"],
            "undo_remove": ["SPAM"],
        }

    # 4. Default: archive (no unsubscribe option found).
    modify_thread(thread_id, add=["ARCHIVE"], remove=["INBOX"])
    return {
        "action": "archived",
        "thread_id": thread_id,
        "detail": "No unsubscribe option; archived",
        "undo_add": ["INBOX"],
        "undo_remove": ["ARCHIVE"],
    }


def undo_action(thread_id: str, undo_add: list[str], undo_remove: list[str]) -> dict[str, Any]:
    """Reverse a prior action by re-adding/removing the recorded labels."""
    modify_thread(thread_id, add=undo_add, remove=undo_remove)
    return {"action": "undone", "thread_id": thread_id}
