#!/usr/bin/env python3
"""The scheduled morning run: sort the inbox, then tell the user what needs them.

Runs the same pipeline as the app's "Refresh & Categorize" button
(propagate mutes, classify, synthesize, AI review pass), then counts the
threads in the action buckets -- honoring the user's manual moves -- and posts
one notification through the notify-user skill's script.

Usage:
    python .agents/skills/email-digest/scripts/daily_digest.py [--n 200] [--notify-agent AGENT_ID]

--notify-agent names the chat the notification belongs to (clicking it opens
that chat). Scheduled runs have no chat of their own, so the cron entry passes
the chat that set the schedule up. Without it, no notification is sent.

Exits non-zero when a pipeline step fails, so the scheduler retries the run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = REPO_ROOT / ".agents/skills/email-digest/scripts"
DATA_PATH = REPO_ROOT / "data/.apps/email-review/data.json"
OVERRIDES_PATH = REPO_ROOT / "data/.apps/email-review/bucket_overrides.json"
NOTIFY = REPO_ROOT / ".agents/skills/notify-user/scripts/notify_user.py"

# Action buckets the heads-up counts, with the words for one and for several.
ACTION_BUCKETS = (
    ("1", "email needs a reply", "emails need a reply"),
    ("2", "needs a decision", "need a decision"),
    ("4", "to-do", "to-dos"),
)


def run_step(script: str, *args: str, required: bool = True) -> None:
    proc = subprocess.run([sys.executable, str(SCRIPTS / script), *args], cwd=REPO_ROOT, check=False)
    if proc.returncode != 0 and required:
        raise SystemExit(f"{script} failed (exit {proc.returncode})")


def thread_buckets(data: dict, overrides: dict[str, str]) -> dict[str, str]:
    """threadId -> bucket, with the user's manual moves applied on top."""
    out: dict[str, str] = {}
    for m in data.get("messages", []):
        tid = m.get("threadId") or m.get("id")
        out[tid] = m.get("final_bucket", "")
    for tid, bucket in overrides.items():
        if tid in out:
            out[tid] = bucket
    return out


def summary(buckets: dict[str, str]) -> str:
    parts = []
    for bucket, one, many in ACTION_BUCKETS:
        n = sum(1 for b in buckets.values() if b == bucket)
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    if not parts:
        return "Your inbox is sorted. Nothing needs a reply or a decision today."
    return "Your inbox is sorted: " + ", ".join(parts) + ". Open Inbox Digest to go through them."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", default="200")
    ap.add_argument("--notify-agent", default="")
    args = ap.parse_args()

    run_step("propagate_mutes.py", args.n, required=False)
    run_step("classify.py", args.n)
    run_step("synthesize_overrides.py")
    # The AI pass improves the sort but is not essential to it: a failure there
    # still leaves a usable digest, so it does not fail the run.
    run_step("llm_judge.py", required=False)

    data = json.loads(DATA_PATH.read_text())
    overrides = json.loads(OVERRIDES_PATH.read_text()) if OVERRIDES_PATH.exists() else {}
    message = summary(thread_buckets(data, overrides))
    print(message, file=sys.stderr)

    if args.notify_agent:
        env = {**os.environ, "MNGR_AGENT_ID": args.notify_agent}
        proc = subprocess.run(
            [sys.executable, str(NOTIFY), "--title", "Inbox Digest", message], env=env, check=False
        )
        if proc.returncode != 0:
            print("notification did not go out", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
