"""The keyless helper must find the workspace's Claude account when run from
cron or supervisord (HOME=/root, no CLAUDE_CONFIG_DIR)."""

from __future__ import annotations

import json

from email_review import claude_p


def _make_account(home, account_id="acct1"):
    accounts = home / ".minds" / "accounts"
    (accounts / account_id).mkdir(parents=True)
    (accounts / "index.json").write_text(
        json.dumps({"accounts": [{"id": account_id, "lane": "anthropic"}], "mru": account_id})
    )
    return str(accounts / account_id)


def test_child_env_points_at_default_account_when_unset(tmp_path, monkeypatch):
    root_home = tmp_path / "root"
    root_home.mkdir()
    user_home = tmp_path / "user"
    expected = _make_account(user_home)
    monkeypatch.setenv("HOME", str(root_home))
    monkeypatch.setattr(claude_p, "WORKSPACE_USER_HOME", str(user_home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert claude_p._child_env()["CLAUDE_CONFIG_DIR"] == expected


def test_child_env_keeps_an_explicit_config_dir(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/explicit")
    assert claude_p._child_env()["CLAUDE_CONFIG_DIR"] == "/explicit"
