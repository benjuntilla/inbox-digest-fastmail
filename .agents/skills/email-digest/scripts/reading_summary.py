#!/usr/bin/env python3
"""The weekly reading summary run: summarize the Reading group's last week of
newsletters into one page (shown at the app's /reading-summary), archive the
ones never opened, and send one heads-up.

Usage:
    python .agents/skills/email-digest/scripts/reading_summary.py [--days 7] [--no-archive] [--notify-agent AGENT_ID]

Run it right after a fresh sort (the schedule runs daily_digest first is not
required: it reads the current digest's Reading group). Exits non-zero on a
failure so the scheduler retries; "nothing to summarize" is not a failure.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from email_review import reading_summary

REPO_ROOT = Path(__file__).resolve().parents[4]
NOTIFY = REPO_ROOT / ".agents/skills/notify-user/scripts/notify_user.py"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--no-archive", action="store_true")
    ap.add_argument("--notify-agent", default="")
    args = ap.parse_args()

    try:
        record = reading_summary.run(days=args.days, archive_unopened=not args.no_archive)
    except reading_summary.NothingToSummarize as e:
        print(str(e), file=sys.stderr)
        return 0
    n_sources = len(record["sources"])
    n_archived = len(record["archived_thread_ids"])
    message = f"Your weekly reading summary is ready: {n_sources} newsletters in one page."
    if n_archived:
        message += f" {n_archived} you never opened were archived (one click on the page puts them back)."
    print(message, file=sys.stderr)
    if args.notify_agent:
        env = {**os.environ, "MNGR_AGENT_ID": args.notify_agent}
        subprocess.run([sys.executable, str(NOTIFY), "--title", "Reading summary", message], env=env, check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
