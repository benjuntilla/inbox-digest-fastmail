"""Tests for daily_digest.py's heads-up wording and manual-move handling."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[5] / ".agents/skills/email-digest/scripts/daily_digest.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("daily_digest_under_test", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules["daily_digest_under_test"] = m
    spec.loader.exec_module(m)
    return m


def test_counts_threads_not_messages_and_applies_moves(mod):
    data = {"messages": [
        {"threadId": "a", "final_bucket": "1"},
        {"threadId": "a", "final_bucket": "1"},
        {"threadId": "b", "final_bucket": "1"},
        {"threadId": "c", "final_bucket": "7"},
    ]}
    buckets = mod.thread_buckets(data, {"b": "3", "gone": "1"})
    assert buckets == {"a": "1", "b": "3", "c": "7"}
    assert mod.summary(buckets) == "Your inbox is sorted: 1 email needs a reply. Open Inbox Digest to go through them."


def test_plural_and_mixed(mod):
    buckets = {"a": "1", "b": "1", "c": "2", "d": "4", "e": "4"}
    assert mod.summary(buckets) == (
        "Your inbox is sorted: 2 emails need a reply, 1 needs a decision, 2 to-dos. "
        "Open Inbox Digest to go through them."
    )


def test_nothing_to_do(mod):
    assert "Nothing needs a reply" in mod.summary({"a": "9"})
