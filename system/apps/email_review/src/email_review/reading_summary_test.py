"""Tests for reading_summary.py's selection, archiving choice, and undo."""

from __future__ import annotations

import json

from email_review import reading_summary


def test_reading_threads_apply_manual_moves():
    data = {"messages": [
        {"threadId": "a", "final_bucket": "9"},
        {"threadId": "b", "final_bucket": "9"},
        {"threadId": "c", "final_bucket": "7"},
    ]}
    assert reading_summary.reading_thread_ids(data, {"b": "3", "c": "9", "gone": "9"}) == ["a", "c"]


def test_only_threads_never_opened_are_archived():
    sources = [
        {"threadId": "t1", "keywords": {"$seen": True}},
        {"threadId": "t2", "keywords": {}},
        {"threadId": "t3"},
        {"threadId": "t3", "keywords": {"$seen": True}},  # one opened message keeps the thread
    ]
    assert reading_summary.unopened_threads(sources) == ["t2"]


def test_source_text_falls_back_to_html_and_drops_scripts():
    email = {
        "textBody": [],
        "htmlBody": [{"partId": "1"}],
        "bodyValues": {"1": {"value": "<style>x{}</style><p>Hello <b>world</b></p><script>bad()</script>"}},
    }
    assert reading_summary.source_text(email) == "Hello world"


def test_undo_archive_restores_once(tmp_path, monkeypatch):
    monkeypatch.setattr(reading_summary, "SUMMARY_DIR", tmp_path)
    moved = []
    monkeypatch.setattr(reading_summary.fastmail, "modify_thread", lambda tid, add, remove: moved.append((tid, add, remove)))
    (tmp_path / "2026-10-11.json").write_text(json.dumps({"archived_thread_ids": ["t1", "t2"], "archive_undone": False}))
    assert reading_summary.undo_archive("2026-10-11.json") == 2
    assert moved == [("t1", ["INBOX"], ["ARCHIVE"]), ("t2", ["INBOX"], ["ARCHIVE"])]
    assert reading_summary.undo_archive("2026-10-11.json") == 0
    assert reading_summary.latest()[0] == "2026-10-11.json"
