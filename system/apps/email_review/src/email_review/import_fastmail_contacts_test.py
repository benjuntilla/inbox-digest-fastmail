"""Tests for import_fastmail_contacts.py -- which Fastmail cards become
contacts.txt rows, and that a re-run replaces only its own block."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[5]
    / ".agents/skills/email-digest/scripts/import_fastmail_contacts.py"
)


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("import_contacts_under_test", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules["import_contacts_under_test"] = m
    spec.loader.exec_module(m)
    return m


def _card(uid, name, *addrs, kind="individual", members=None):
    card = {"uid": uid, "kind": kind, "name": {"full": name}}
    card["emails"] = {str(i): {"address": a} for i, a in enumerate(addrs)}
    if members:
        card["members"] = {m: True for m in members}
    return card


class TestBuildRows:
    def test_person_becomes_trusted_warm_with_groups(self, mod):
        cards = [
            _card("u1", "Jane  Doe", "Jane@Example.com", "jane@home.example"),
            _card("g1", "VIPs", kind="group", members=["u1"]),
        ]
        rows = mod.build_rows(cards, skip=set(), automated=[])
        assert rows == ["jane@example.com,jane@home.example\tJane Doe\ttrusted-warm\tFastmail contact (VIPs)"]

    def test_skips_own_automated_relay_and_handwritten(self, mod):
        cards = [
            _card("u1", "Me", "me@self.example"),
            _card("u2", "Bot", "noreply@service.example"),
            _card("u3", "", "0123456789abcdef0123456789abcdef@hous.craigslist.org"),
            _card("u4", "Known", "known@friend.example"),
            _card("u5", "", "new@friend.example"),
        ]
        rows = mod.build_rows(
            cards, skip={"me@self.example", "known@friend.example"}, automated=["noreply@"]
        )
        # A nameless card falls back to its address as the name.
        assert rows == ["new@friend.example\tnew@friend.example\ttrusted-warm\tFastmail contact"]


class TestManagedBlock:
    def test_rerun_replaces_only_the_block(self, mod):
        text = (
            "# header\nhand@written.example\tHand\tvendor\tnote\n\n"
            f"{mod.BEGIN}\nold@row.example\tOld\ttrusted-warm\tFastmail contact\n{mod.END}\n"
        )
        outside, body = mod.split_managed(text)
        assert "old@row.example" in body
        assert "old@row.example" not in outside
        assert "hand@written.example" in outside
        assert mod.hand_written_addrs(outside) == {"hand@written.example"}
