"""Fastmail (JMAP) client shared by the email-review app and the email-digest
skill scripts. Every request goes through ``latchkey curl``, which injects the
user's Fastmail credential, so nothing here ever handles a token.

JMAP has no labels, only mailboxes, so this module exposes the small set of
"label" tokens the rest of the app speaks:

- ``INBOX`` / ``ARCHIVE`` / ``SPAM`` / ``SENT`` / ``TRASH`` -- the mailbox with
  that role (``inbox``, ``archive``, ``junk``, ``sent``, ``trash``);
- any other string -- a mailbox id (e.g. the app's ``Muted`` mailbox).

An email must always sit in at least one mailbox, which is where JMAP and
Gmail differ: removing ``INBOX`` with nothing added files the email in
``ARCHIVE``, and adding ``INBOX`` with nothing removed takes it back out of
``ARCHIVE``. That keeps "archive" and its undo a plain label diff, exactly as
the Gmail version of this app had it.

Spec: https://jmap.io/spec-mail.html; session doc at
https://api.fastmail.com/jmap/session.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from datetime import datetime
from email.utils import format_datetime
from typing import Any

SESSION_URL = "https://api.fastmail.com/jmap/session"
USING = [
    "urn:ietf:params:jmap:core",
    "urn:ietf:params:jmap:mail",
]

ROLE_TOKENS = {
    "INBOX": "inbox",
    "ARCHIVE": "archive",
    "SPAM": "junk",
    "SENT": "sent",
    "TRASH": "trash",
}

# JMAP servers cap ids per /get and /set call (Fastmail: maxObjectsInGet 4096,
# maxObjectsInSet 4096); stay well below.
BATCH = 500

# Properties classify.py and the app read for a message summary.
SUMMARY_PROPERTIES = [
    "id",
    "threadId",
    "mailboxIds",
    "keywords",
    "from",
    "to",
    "cc",
    "subject",
    "receivedAt",
    "sentAt",
    "preview",
    "header:List-Unsubscribe:asRaw",
    "header:List-Unsubscribe-Post:asRaw",
    "header:List-Id:asText",
]


class GatewayUnreachable(RuntimeError):
    """The latchkey gateway (the Fastmail credential proxy) can't be reached,
    or Fastmail rejected the call. Raised so callers can show a clear message
    instead of a raw traceback or 500."""


class JmapError(RuntimeError):
    """A JMAP method returned an ``error`` response."""


class UnsupportedQuery(ValueError):
    """A search string used an operator parse_query can't translate."""


_lock = threading.Lock()
_session: dict[str, Any] | None = None
_mailboxes: list[dict[str, Any]] | None = None


def _curl(args: list[str], retries: int = 3) -> str:
    last: subprocess.CompletedProcess[str] | None = None
    # Retry transient gateway blips straight away; a real outage fails all tries.
    for _ in range(retries):
        last = subprocess.run(["latchkey", "curl", "-s", "-f", *args], capture_output=True, text=True, check=False)
        if last.returncode == 0:
            return last.stdout
    assert last is not None
    raise GatewayUnreachable(
        f"Fastmail is unreachable via latchkey (curl exit {last.returncode}). Either the "
        "workspace's connection to the credential store is down, or Fastmail refused the "
        f"request. {last.stderr.strip()[:200]}"
    )


def session() -> dict[str, Any]:
    """The JMAP session document (cached for the process lifetime)."""
    global _session
    with _lock:
        if _session is None:
            _session = json.loads(_curl([SESSION_URL]))
        return _session


def account_id() -> str:
    return session()["primaryAccounts"]["urn:ietf:params:jmap:mail"]


def call(method_calls: list[list[Any]]) -> list[list[Any]]:
    """POST one JMAP request and return its methodResponses. Each call's
    arguments get ``accountId`` filled in when absent. Raises JmapError on the
    first method that errored."""
    acct = account_id()
    calls = []
    for name, cargs, tag in method_calls:
        calls.append([name, {"accountId": acct, **cargs}, tag])
    body = json.dumps({"using": USING, "methodCalls": calls})
    out = _curl(["-X", "POST", "-H", "Content-Type: application/json", "--data-binary", body, session()["apiUrl"]])
    responses = json.loads(out)["methodResponses"]
    for name, rargs, tag in responses:
        if name == "error":
            raise JmapError(f"{tag}: {rargs.get('type')} {rargs.get('description', '')}".strip())
    return responses


def call_one(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return call([[name, args, "0"]])[0][1]


# ---- Mailboxes ----


def mailboxes(refresh: bool = False) -> list[dict[str, Any]]:
    global _mailboxes
    if _mailboxes is None or refresh:
        _mailboxes = call_one("Mailbox/get", {"properties": ["id", "name", "role", "parentId"]})["list"]
    return _mailboxes


def mailbox_id_for_role(role: str) -> str:
    for mb in mailboxes():
        if mb.get("role") == role:
            return mb["id"]
    raise JmapError(f"no mailbox with role {role!r} on this Fastmail account")


def resolve(token: str) -> str:
    """Label token -> mailbox id (see module docstring)."""
    role = ROLE_TOKENS.get(token)
    return mailbox_id_for_role(role) if role else token


def labels_for(mailbox_ids: dict[str, bool]) -> list[str]:
    """Mailbox ids -> label tokens: role mailboxes by their token, others by id."""
    by_id = {mb["id"]: mb for mb in mailboxes()}
    role_to_token = {v: k for k, v in ROLE_TOKENS.items()}
    out = []
    for mid in mailbox_ids:
        role = (by_id.get(mid) or {}).get("role")
        out.append(role_to_token.get(role, mid) if role else mid)
    return sorted(out)


def ensure_mailbox(name: str) -> str:
    """Return the id of the top-level mailbox called ``name``, creating it
    on first use."""
    for mb in mailboxes():
        if mb.get("name") == name and not mb.get("parentId"):
            return mb["id"]
    res = call_one("Mailbox/set", {"create": {"new": {"name": name, "parentId": None}}})
    created = (res.get("created") or {}).get("new")
    if not created:
        raise JmapError(f"could not create the {name!r} mailbox: {res.get('notCreated')}")
    mailboxes(refresh=True)
    return created["id"]


# ---- Emails ----


def query_ids(filter_: dict[str, Any], limit: int, position: int = 0) -> list[str]:
    """Email ids matching ``filter_``, newest first."""
    ids: list[str] = []
    while len(ids) < limit:
        res = call_one(
            "Email/query",
            {
                "filter": filter_,
                "sort": [{"property": "receivedAt", "isAscending": False}],
                "position": position + len(ids),
                "limit": min(BATCH, limit - len(ids)),
            },
        )
        page = res.get("ids", [])
        ids.extend(page)
        if not page:
            break
    return ids[:limit]


def any_match(filter_: dict[str, Any]) -> bool:
    return bool(call_one("Email/query", {"filter": filter_, "limit": 1}).get("ids"))


def get_emails(ids: list[str], properties: list[str] | None = None, **extra: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), BATCH):
        res = call_one("Email/get", {"ids": ids[i : i + BATCH], "properties": properties or SUMMARY_PROPERTIES, **extra})
        out.extend(res.get("list", []))
    return out


def thread_email_ids(thread_ids: list[str]) -> dict[str, list[str]]:
    """threadId -> its email ids, oldest first."""
    out: dict[str, list[str]] = {}
    for i in range(0, len(thread_ids), BATCH):
        res = call_one("Thread/get", {"ids": thread_ids[i : i + BATCH]})
        for t in res.get("list", []):
            out[t["id"]] = t.get("emailIds", [])
    return out


def modify_emails(emails: list[dict[str, Any]], add: list[str], remove: list[str]) -> list[str]:
    """Apply a label diff to the given emails (each needs ``id`` and
    ``mailboxIds``). Only emails sitting in one of the removed mailboxes are
    touched -- or, when nothing is removed, those in ARCHIVE (see the module
    docstring). Returns the ids that changed."""
    add_ids = [resolve(t) for t in add]
    remove_ids = [resolve(t) for t in remove]
    if not remove_ids and "INBOX" in add:
        remove_ids = [resolve("ARCHIVE")]
    if not add_ids and "INBOX" in remove:
        add_ids = [resolve("ARCHIVE")]
    spam_id = resolve("SPAM")

    update: dict[str, dict[str, Any]] = {}
    for e in emails:
        boxes = e.get("mailboxIds") or {}
        if not any(r in boxes for r in remove_ids):
            continue
        patch: dict[str, Any] = {f"mailboxIds/{r}": None for r in remove_ids if r in boxes}
        patch.update({f"mailboxIds/{a}": True for a in add_ids})
        # Tell Fastmail's spam filter what we decided.
        if spam_id in add_ids:
            patch["keywords/$junk"] = True
            patch["keywords/$notjunk"] = None
        elif spam_id in remove_ids:
            patch["keywords/$junk"] = None
            patch["keywords/$notjunk"] = True
        update[e["id"]] = patch

    changed: list[str] = []
    items = list(update.items())
    for i in range(0, len(items), BATCH):
        res = call_one("Email/set", {"update": dict(items[i : i + BATCH])})
        if res.get("notUpdated"):
            raise JmapError(f"Fastmail refused to update some messages: {res['notUpdated']}")
        changed.extend((res.get("updated") or {}).keys())
    return changed


def modify_thread(thread_id: str, add: list[str], remove: list[str]) -> list[str]:
    ids = thread_email_ids([thread_id]).get(thread_id, [])
    emails = get_emails(ids, ["id", "mailboxIds"])
    return modify_emails(emails, add, remove)


def modify_message_ids(ids: list[str], add: list[str], remove: list[str]) -> list[str]:
    return modify_emails(get_emails(ids, ["id", "mailboxIds"]), add, remove)


# ---- Formatting helpers (JMAP address objects -> RFC-822-ish strings) ----


def format_address(a: dict[str, Any]) -> str:
    name = (a.get("name") or "").strip()
    email = (a.get("email") or "").strip()
    if name and email:
        return f'"{name}" <{email}>' if re.search(r'[,;"]', name) else f"{name} <{email}>"
    return email or name


def format_addresses(lst: list[dict[str, Any]] | None) -> str:
    return ", ".join(format_address(a) for a in (lst or []))


def web_url(email_id: str, thread_id: str = "", mailbox: str = "Inbox") -> str:
    """Link to the message in Fastmail's web app (format confirmed by the
    user: /mail/<Mailbox>/<threadId>.<emailId>)."""
    if thread_id:
        return f"https://app.fastmail.com/mail/{mailbox}/{thread_id}.{email_id}"
    return f"https://app.fastmail.com/mail/search:msgid:{email_id}/"


# ---- Gmail-style search strings -> JMAP filters ----

_IN_TOKENS = {"inbox": "INBOX", "sent": "SENT", "spam": "SPAM", "junk": "SPAM", "archive": "ARCHIVE", "trash": "TRASH"}


def parse_query(q: str) -> dict[str, Any]:
    """Translate the small Gmail search dialect the skill's tools use
    (``from:``, ``to:``, ``cc:``, ``subject:``, ``in:``, ``newer_than:Nd``, and
    free text) into a JMAP FilterOperator AND. Unsupported operators raise
    UnsupportedQuery rather than silently matching more mail than asked for."""
    conds: list[dict[str, Any]] = []
    for tok in re.findall(r'(\w+:"[^"]*"|\w+:\S+|"[^"]*"|\S+)', q):
        key, _, val = tok.partition(":") if re.match(r"^\w+:", tok) else ("", "", tok)
        val = val.strip('"')
        if key == "from":
            conds.append({"from": val})
        elif key == "to":
            conds.append({"to": val})
        elif key == "cc":
            conds.append({"cc": val})
        elif key == "subject":
            conds.append({"subject": val})
        elif key == "in":
            if val.lower() not in _IN_TOKENS:
                raise UnsupportedQuery(f"unsupported in:{val}")
            conds.append({"inMailbox": resolve(_IN_TOKENS[val.lower()])})
        elif key == "newer_than":
            m = re.fullmatch(r"(\d+)d", val)
            if not m:
                raise UnsupportedQuery(f"unsupported newer_than:{val} (use Nd)")
            after = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - int(m.group(1)) * 86400))
            conds.append({"after": after})
        elif key:
            raise UnsupportedQuery(f"unsupported search operator {key}:")
        else:
            conds.append({"text": val})
    if len(conds) == 1:
        return conds[0]
    return {"operator": "AND", "conditions": conds}


def raw_header(email: dict[str, Any], name: str) -> str:
    """An ``asRaw`` header value from an Email/get result, unfolded and
    stripped ("" when absent)."""
    raw = email.get(f"header:{name}:asRaw") or ""
    return re.sub(r"\r?\n[ \t]+", " ", raw).strip()


def rfc2822_date(iso: str) -> str:
    """JMAP UTCDate (``2026-10-07T20:13:34Z``) -> an RFC 2822 Date string, the
    shape the app's date parsing and display already expect."""
    if not iso:
        return ""
    return format_datetime(datetime.fromisoformat(iso.replace("Z", "+00:00")))


# ---- Contacts (RFC 9610 JMAP for Contacts) ----

CONTACTS_CAPABILITY = "urn:ietf:params:jmap:contacts"


def contact_cards() -> list[dict[str, Any]]:
    """Every ContactCard in every address book (individuals and groups)."""
    acct = session()["primaryAccounts"][CONTACTS_CAPABILITY]
    body = json.dumps({
        "using": [*USING, CONTACTS_CAPABILITY],
        "methodCalls": [["ContactCard/get", {"accountId": acct}, "0"]],
    })
    out = _curl(["-X", "POST", "-H", "Content-Type: application/json", "--data-binary", body, session()["apiUrl"]])
    name, args, _ = json.loads(out)["methodResponses"][0]
    if name == "error":
        raise JmapError(f"ContactCard/get: {args.get('type')} {args.get('description', '')}".strip())
    return args.get("list", [])
