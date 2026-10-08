"""Tests for calendar_events.py: validating what the event reader returns,
choosing the calendar, and the JSCalendar object written."""

from __future__ import annotations

import json

import pytest

from email_review import calendar_events as ce

GOOD = {
    "found": True, "title": "Office Hours", "start": "2026-10-07T09:00:00",
    "time_zone": "America/Los_Angeles", "duration": "PT1H", "all_day": False,
    "location": "Zoom", "url": "https://zoom.example/j/1", "description": "1:1s",
}


def test_parse_extraction_accepts_json_with_surrounding_text():
    ev = ce.parse_extraction("Here you go:\n" + json.dumps(GOOD))
    assert ev["title"] == "Office Hours" and ev["start"] == "2026-10-07T09:00:00"


def test_parse_extraction_no_event():
    with pytest.raises(ce.NoEventFound, match="propose times"):
        ce.parse_extraction('{"found": false, "reason": "asks you to propose times"}')


@pytest.mark.parametrize(
    "bad",
    [{"title": ""}, {"start": "next tuesday"}, {"time_zone": "Mars/Base"}, {"duration": "1 hour"}, {"duration": "PT"}],
)
def test_validate_event_rejects_bad_details(bad):
    with pytest.raises(ce.BadEventDetails):
        ce.validate_event({**GOOD, **bad})


def _cal(cid, name, default=False, writable=True, synced=False):
    return {"id": cid, "name": name, "isDefault": default, "myRights": {"mayWriteOwn": writable},
            "syncedFrom": {"url": "x"} if synced else None}


def test_pick_calendar_prefers_school_then_default_and_skips_subscriptions():
    cals = [_cal("a", "Canvas", synced=True, writable=False), _cal("b", "Personal", default=True), _cal("c", "School")]
    assert ce.pick_calendar(cals, prefer_school=True)["id"] == "c"
    assert ce.pick_calendar(cals, prefer_school=False)["id"] == "b"
    with pytest.raises(ce.BadEventDetails):
        ce.pick_calendar([_cal("a", "Canvas", synced=True)], prefer_school=False)


def test_jscalendar_object():
    obj = ce.to_jscalendar(ce.validate_event(GOOD), "b")
    assert obj["calendarIds"] == {"b": True}
    assert obj["timeZone"] == "America/Los_Angeles" and obj["showWithoutTime"] is False
    assert obj["locations"]["1"]["name"] == "Zoom"
    assert obj["links"]["1"]["href"] == "https://zoom.example/j/1"
    all_day = ce.to_jscalendar(ce.validate_event({**GOOD, "all_day": True, "duration": "P1D"}), "b")
    assert all_day["timeZone"] is None and all_day["showWithoutTime"] is True
