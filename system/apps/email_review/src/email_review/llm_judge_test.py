"""Tests for llm_judge.py's verdict parsing and which threads it judges."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[5] / ".agents/skills/email-digest/scripts/llm_judge.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("llm_judge_under_test", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules["llm_judge_under_test"] = m
    spec.loader.exec_module(m)
    return m


def test_cold_outreach_is_judged(mod):
    assert "6" in mod.JUDGED_BUCKETS


def test_parses_moves_to_any_bucket_and_keeps(mod):
    out = (
        "[0] MOVE_TO_8 — automated credit alert\n"
        "[1] KEEP — real ask\n"
        "[2] MOVE_TO_9 — newsletter\n"
        "[3] MOVE_TO_11 — nonsense\n"
        "noise line\n"
    )
    verdicts = mod.parse_batch_verdicts(out, [0, 1, 2, 3])
    assert verdicts[0] == ("8", "automated credit alert")
    assert verdicts[1][0] is None
    assert verdicts[2][0] == "9"
    assert verdicts[3][0] is None  # unknown bucket is refused, thread kept


def test_rules_come_from_rules_md(mod):
    assert "10-bucket" in mod.load_rules() or "Reply needed" in mod.load_rules()


def test_learned_rule_threads_are_not_judged(mod, tmp_path, monkeypatch):
    data = {"messages": [
        {"threadId": "t1", "final_bucket": "6", "learned_rule": True, "subject": "s"},
    ]}
    path = tmp_path / "data.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(mod, "DATA_PATH", path)
    monkeypatch.setattr(mod, "call_claude", lambda prompt: (_ for _ in ()).throw(AssertionError("judged")))
    assert mod.main() == 0
    assert json.loads(path.read_text())["stats"]["llm_judge_moves"] == 0
