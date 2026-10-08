"""Add an event from an email to the user's Fastmail calendar.

Two steps, so nothing lands on the calendar without the user seeing it first:
``extract_event`` has Claude read the thread and return the event's details
(or say there is none); ``add_event`` writes exactly those details as a JMAP
CalendarEvent (JSCalendar, RFC 8984) into the default calendar -- or the
calendar named "School" for a School-group thread, when one exists.
``remove_event`` is the undo.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from email_review import fastmail
from email_review.claude_p import claude_p_completion

CALENDARS_CAPABILITY = "urn:ietf:params:jmap:calendars"
EXTRACT_MODEL = "claude-sonnet-5"
MAX_BODY_CHARS = 8000
SCHOOL_CALENDAR_NAME = "School"

EXTRACT_SYSTEM = (
    "You extract one calendar event from an email. Reply with a single JSON object "
    "and nothing else. If the email does not describe a specific event with a date, "
    'reply {"found": false, "reason": "<short reason>"}. Otherwise reply '
    '{"found": true, "title": str, "start": "YYYY-MM-DDTHH:MM:SS" (local wall-clock '
    'time, no offset), "time_zone": IANA name, "duration": ISO 8601 duration like '
    '"PT1H", "all_day": bool, "location": str or null, "url": str or null (a join '
    'or event page link), "description": one or two sentences}. Use the time zone '
    "the email states; if it gives several (e.g. 9 AM PT / 12 PM ET), pick the "
    "first. If it states none, use {default_tz}. If no end time or length is given, "
    "use PT1H. For all-day events use T00:00:00 and duration P1D."
)


class NoEventFound(RuntimeError):
    """The email does not describe a specific event."""


class BadEventDetails(RuntimeError):
    """The extracted or submitted details are not a valid event."""


def _call(method_calls: list[list[Any]]) -> list[list[Any]]:
    acct = fastmail.session()["primaryAccounts"][CALENDARS_CAPABILITY]
    calls = [[name, {"accountId": acct, **args}, tag] for name, args, tag in method_calls]
    body = json.dumps({"using": [*fastmail.USING, CALENDARS_CAPABILITY], "methodCalls": calls})
    out = fastmail._curl(
        ["-X", "POST", "-H", "Content-Type: application/json", "--data-binary", body, fastmail.session()["apiUrl"]]
    )
    responses = json.loads(out)["methodResponses"]
    for name, args, tag in responses:
        if name == "error":
            raise fastmail.JmapError(f"{tag}: {args.get('type')} {args.get('description', '')}".strip())
    return responses


def calendars() -> list[dict[str, Any]]:
    return _call([["Calendar/get", {}, "0"]])[0][1]["list"]


def pick_calendar(cals: list[dict[str, Any]], prefer_school: bool) -> dict[str, Any]:
    """The calendar new events go to: "School" for school threads when present,
    else the default one, else the first the user can write to."""
    writable = [c for c in cals if (c.get("myRights") or {}).get("mayWriteOwn") and not c.get("syncedFrom")]
    if not writable:
        raise BadEventDetails("no calendar on this Fastmail account accepts new events")
    if prefer_school:
        for c in writable:
            if (c.get("name") or "").strip().lower() == SCHOOL_CALENDAR_NAME.lower():
                return c
    return next((c for c in writable if c.get("isDefault")), writable[0])


def _thread_text(thread_id: str) -> tuple[str, str]:
    """(latest message rendered for the prompt, its receivedAt)."""
    ids = fastmail.thread_email_ids([thread_id]).get(thread_id, [])
    emails = fastmail.get_emails(
        ids,
        ["id", "from", "subject", "receivedAt", "textBody", "htmlBody", "bodyValues"],
        fetchTextBodyValues=True,
        fetchHTMLBodyValues=True,
    )
    if not emails:
        raise NoEventFound("the thread has no messages")
    e = max(emails, key=lambda m: m.get("receivedAt") or "")
    values = e.get("bodyValues") or {}
    text = "\n".join((values.get(p.get("partId") or "") or {}).get("value", "") for p in e.get("textBody") or [])
    if not text.strip():
        html_text = "\n".join((values.get(p.get("partId") or "") or {}).get("value", "") for p in e.get("htmlBody") or [])
        text = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html_text))
    rendered = (
        f"From: {fastmail.format_addresses(e.get('from'))}\nSubject: {e.get('subject', '')}\n"
        f"Received: {e.get('receivedAt', '')}\n\n{text.strip()[:MAX_BODY_CHARS]}"
    )
    return rendered, e.get("receivedAt") or ""


def parse_extraction(reply: str) -> dict[str, Any]:
    """The model's JSON reply -> a validated event dict (raises on anything off)."""
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        raise BadEventDetails("the event reader did not return JSON")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise BadEventDetails(f"the event reader returned malformed JSON: {e}") from e
    if not data.get("found"):
        raise NoEventFound(data.get("reason") or "no specific event with a date in this email")
    return validate_event(data)


def validate_event(data: dict[str, Any]) -> dict[str, Any]:
    title = (data.get("title") or "").strip()
    if not title:
        raise BadEventDetails("the event has no title")
    try:
        start = datetime.fromisoformat(str(data.get("start")))
    except ValueError as e:
        raise BadEventDetails(f"bad start time {data.get('start')!r}") from e
    tz = str(data.get("time_zone") or "")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise BadEventDetails(f"unknown time zone {tz!r}") from e
    duration = str(data.get("duration") or "PT1H")
    if not re.fullmatch(r"P(?:\d+D)?(?:T(?:\d+H)?(?:\d+M)?)?", duration) or duration in ("P", "PT"):
        raise BadEventDetails(f"bad duration {duration!r}")
    return {
        "title": title[:200],
        "start": start.replace(tzinfo=None).isoformat(timespec="seconds"),
        "time_zone": tz,
        "duration": duration,
        "all_day": bool(data.get("all_day")),
        "location": (data.get("location") or None),
        "url": (data.get("url") or None),
        "description": (data.get("description") or "").strip()[:1000],
    }


def extract_event(thread_id: str, default_tz: str) -> dict[str, Any]:
    text, _ = _thread_text(thread_id)
    reply = claude_p_completion(
        text, system=EXTRACT_SYSTEM.replace("{default_tz}", default_tz), model=EXTRACT_MODEL
    ).text
    return parse_extraction(reply)


def to_jscalendar(event: dict[str, Any], calendar_id: str) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "@type": "Event",
        # Fastmail requires the JSCalendar version (RFC 8984 is 1.0; Fastmail's events say 2.0).
        "version": "2.0",
        "calendarIds": {calendar_id: True},
        "title": event["title"],
        "start": event["start"],
        "timeZone": None if event["all_day"] else event["time_zone"],
        "duration": event["duration"],
        "showWithoutTime": event["all_day"],
        "description": event.get("description") or "",
    }
    if event.get("location"):
        obj["locations"] = {"1": {"@type": "Location", "name": event["location"]}}
    if event.get("url"):
        obj["links"] = {"1": {"@type": "Link", "href": event["url"], "rel": "describedby"}}
    return obj


def add_event(event: dict[str, Any], prefer_school: bool = False) -> dict[str, Any]:
    event = validate_event(event)
    cal = pick_calendar(calendars(), prefer_school)
    res = _call([["CalendarEvent/set", {"create": {"ev": to_jscalendar(event, cal["id"])}}, "0"]])[0][1]
    created = (res.get("created") or {}).get("ev")
    if not created:
        raise fastmail.JmapError(f"Fastmail refused the event: {res.get('notCreated')}")
    return {"event_id": created["id"], "calendar": cal.get("name") or "", "event": event}


def remove_event(event_id: str) -> None:
    res = _call([["CalendarEvent/set", {"destroy": [event_id]}, "0"]])[0][1]
    if event_id not in (res.get("destroyed") or []):
        raise fastmail.JmapError(f"Fastmail did not remove the event: {res.get('notDestroyed')}")
