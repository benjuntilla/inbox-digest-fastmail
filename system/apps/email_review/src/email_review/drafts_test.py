"""Tests for drafts.py: who a reply goes to, from which address, and on which thread."""

from __future__ import annotations

import pytest

from email_review import drafts

OWN = "alex@yourcompany.example"  # pinned by conftest.py


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(drafts.fastmail, "mailbox_id_for_role", lambda role: f"mb-{role}")


def _email(sender, to, *, msg_id, received, subject="Plans", reply_to=None, refs=None):
    e = {
        "from": [{"name": "Sender", "email": sender}],
        "to": [{"name": None, "email": a} for a in to],
        "cc": [],
        "subject": subject,
        "messageId": [msg_id],
        "references": refs,
        "receivedAt": received,
    }
    if reply_to:
        e["replyTo"] = [{"name": "Desk", "email": reply_to}]
    return e


def test_replies_to_latest_inbound_not_own_message():
    thread = [
        _email("pat@x.example", ["alex.doe@gmail.example"], msg_id="a@x", received="1"),
        _email(OWN, ["pat@x.example"], msg_id="b@x", received="2", refs=["a@x"]),
        _email("pat@x.example", ["alex.doe@gmail.example"], msg_id="c@x", received="3", refs=["a@x", "b@x"]),
    ]
    d = drafts.build_draft(thread, "hello")
    assert d["to"] == [{"name": "Sender", "email": "pat@x.example"}]
    # Sent from the address the mail was addressed to.
    assert d["from"][0]["email"] == "alex.doe@gmail.example"
    assert d["inReplyTo"] == ["c@x"]
    assert d["references"] == ["a@x", "b@x", "c@x"]
    assert d["subject"] == "Re: Plans"
    assert d["mailboxIds"] == {"mb-drafts": True}
    assert d["keywords"]["$draft"] is True
    assert d["bodyValues"]["body"]["value"] == "hello"


def test_reply_to_header_wins_and_subject_not_doubled():
    thread = [_email("noreply@x.example", [OWN], msg_id="a@x", received="1", subject="Re: Q", reply_to="desk@x.example")]
    d = drafts.build_draft(thread, "")
    assert d["to"][0]["email"] == "desk@x.example"
    assert d["subject"] == "Re: Q"


def test_only_own_messages_is_refused():
    with pytest.raises(drafts.NothingToReplyTo):
        drafts.build_draft([_email(OWN, ["pat@x.example"], msg_id="a@x", received="1")], "")


def test_quoted_history_is_trimmed_from_prompt():
    e = _email("pat@x.example", [OWN], msg_id="a@x", received="1")
    e["textBody"] = [{"partId": "1", "type": "text/plain"}]
    e["bodyValues"] = {"1": {"value": "New question here\nOn Mon, Pat wrote:\n> old stuff"}}
    prompt = drafts.build_prompt([e])
    assert "New question here" in prompt
    assert "old stuff" not in prompt
