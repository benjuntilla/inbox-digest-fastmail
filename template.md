---
title: "Inbox Digest for Fastmail"
description: "A Fastmail inbox digest and one-click triage app: sorts your inbox into ten groups, lets you archive / mute / spam / unsubscribe in one click, drafts replies into Fastmail Drafts, learns from your corrections, and can run every morning with a heads-up."
thumbnail: "template.svg"
version: v1
format: v2
---

# Inbox Digest for Fastmail

This file is the manifest for the **Inbox Digest for Fastmail** template (slug:
`inbox-digest-fastmail`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a workspace that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

A Fastmail inbox digest and one-click triage app: sorts your inbox into ten groups, lets you archive / mute / spam / unsubscribe in one click, drafts replies into Fastmail Drafts, learns from your corrections, and can run every morning with a heads-up.

An inbox full of mixed mail hides the handful of messages that need you. This
template reads the adopter's Fastmail inbox and sorts every thread into ten
groups -- Reply needed, Decision needed, FYI, TODO, Sent / awaiting reply,
Cold outreach, Marketing / spam / phishing, In-product notifications, Reading,
and Work FYI. A rules pass (mailing-list headers, a who's-who contacts list,
phishing checks) is followed by an AI review pass for the ambiguous cases. The
result is the **Inbox Digest & Review** window: one page with a section per
group, a one-line reason for every thread, and buttons to archive, mute, mark
as spam, unsubscribe, or move a thread to a different group. For threads that
need a reply it can write a draft and save it in Fastmail Drafts for the user
to edit and send -- the app never sends mail itself. Every manual move is
logged and turned into sender rules, so the sorting gets better the more it is
corrected. An optional 7 AM run sorts the inbox before the user wakes and sends
a notification counting what needs them.

This is a Fastmail adaptation of the Gmail-based
[inbox-digest-review](https://github.com/imbue-ai/inbox-digest-review-mind-template)
template: the mail client was rewritten for Fastmail's JMAP API, and reply
drafts, a Fastmail contacts importer, learned sender rules, and the morning run
were added.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original agent onto a clean default-workspace-template base):

- `system/apps/email_review`
- `system/supervisord.conf.d/email-review.conf`
- `.agents/skills/email-digest`
- `system/scripts/review_email_moves.py`
- `uv.lock`

What each one is:

- `system/apps/email_review` -- the app (a Python package, FastAPI + uvicorn).
  `runner.py` serves the digest page and the action endpoints. `fastmail.py`
  is a small JMAP client that reaches Fastmail through `latchkey curl`.
  `mail_actions.py` does archive, mute (files the thread in a "Muted" mailbox),
  spam, and unsubscribe (one-click HTTP links only). `drafts.py` writes reply
  drafts into Fastmail Drafts. `claude_p.py` is the keyless Claude helper.
  `account.py` is the "who am I" identity, and `phishing.py` the impersonation
  checks. Tests sit beside each module.
- `system/supervisord.conf.d/email-review.conf` -- the supervisord program
  `email-review`. It registers the app's URL with `system/scripts/forward_port.py`
  (using `app.toml`, so the window appears as "Inbox Digest & Review"), then
  runs the `email-review` entry point on `127.0.0.1:8091`.
- `.agents/skills/email-digest` -- the skill that does the sorting.
  `SKILL.md` describes the pipeline and `RULES.md` the ten-group rules.
  `contacts.txt` is the who's-who list (example rows only). `scripts/` holds
  the pipeline steps (`propagate_mutes.py`, `classify.py`,
  `synthesize_overrides.py`, `llm_judge.py`), the morning run
  (`daily_digest.py`), and maintenance helpers: `import_fastmail_contacts.py`,
  `contact_audit.py`, `bulk_archive.py` / `bulk_archive_undo.py`, and
  `check_unsub_target.py`.
- `system/scripts/review_email_moves.py` -- a command-line view of the move
  log (every time the user moved a thread to a different group), for checking
  what the app has learned.
- `uv.lock` -- the workspace lockfile with the app's dependencies. Bootstrap
  syncs with `--frozen`, so it must name the `email-review` package.

At runtime: supervisord keeps `email-review` running. The page's "Refresh &
Categorize" button runs the pipeline as subprocesses, in order:
propagate mutes, classify, synthesize sender rules from past moves, then the AI
review pass. The results land in `data/.apps/email-review/` -- the fetched
messages with their classification (`data.json`), the user's manual moves
(`bucket_overrides.json`), and the move log. The page reads them from there. Every one-click action is a
JMAP call through latchkey. `daily_digest.py` runs the same pipeline headless and posts one
notification through the notify-user skill.

## Recipe

This template is version `v1`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting agent must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.

Activation:

- requires_permission: fastmail-api / fastmail-read-all (user-approved; the
  adopting agent initiates this via a latchkey permission request during
  setup -- builds the digest from the adopter's own inbox, and lets the
  contacts importer read their Fastmail address book)
- requires_permission: fastmail-api / fastmail-write-mail (user-approved; the
  adopting agent initiates this via a latchkey permission request during
  setup -- powers archive, mute (creates a "Muted" mailbox on first use),
  mark as spam, and saving reply drafts to Drafts; nothing ever sends mail)
- requires_llm: calls Claude via the keyless subscription path (`claude -p`,
  through the bundled `email_review/claude_p.py`) for the AI review pass
  (`llm_judge.py`) and reply drafts (`drafts.py`); an adopter on the keyed
  litellm path (`ANTHROPIC_API_KEY` set) should switch those calls per the
  `use-ai-integration` skill

Adaptation:

- **Placeholder identity.** `system/apps/email_review/src/email_review/account.py`
  ships with a placeholder name ("Alex Doe"), `yourcompany.example` addresses,
  an org domain, and an accounts-payable forwarder. Before the first real
  run, set these to the adopter's own name, every address they send from (their
  Fastmail sending identities), their organization's domains, and any AP
  forwarder (or none). Otherwise the classifier will not recognize their own
  mail, and the phishing check cannot spot someone impersonating them.
- **Contacts list ships with examples only.** `.agents/skills/email-digest/contacts.txt`
  has fake example rows. Run
  `uv run python .agents/skills/email-digest/scripts/import_fastmail_contacts.py --write`
  to fill a managed block from the adopter's Fastmail address book. Then add
  hand-written rows for anything special: newsletters to keep, vendors,
  contractors, and a bare-domain row for their school or employer domain.
  Without this, the first digest treats almost everyone as cold outreach.
- **Opinionated ten-group taxonomy.** `.agents/skills/email-digest/RULES.md`
  encodes one specific way to triage: the group list, the header pre-filter,
  and the cold-outreach and phishing heuristics. Walk the adopter through the
  ten groups and rename, merge, or retune any that do not match how they work.
  `RULES.md` is the place to change them, along with the group table in
  `runner.py` and `classify.py`'s sender lists (`READING_SENDERS` ships with
  example entries only).
- **"Muted" mailbox name.** The mute action files threads in a Fastmail
  mailbox literally named `Muted`, created on first use (`MUTED_LABEL_NAME` in
  `mail_actions.py`). If the adopter already has a mailbox by that name for
  something else, rename the constant before the first mute.
- **Morning run is off.** The 7 AM run is not scheduled in a fresh workspace.
  If the adopter wants it, add a cron entry through
  `system/libs/automations/run_job.sh` that runs
  `.agents/skills/email-digest/scripts/daily_digest.py --notify-agent <chat agent id>`
  with the id of the chat that should own the heads-up. The exact line is in
  the skill's `SKILL.md` under "Morning run"; the `manage-scheduled-tasks`
  skill explains how to install it.
- **Email-only unsubscribes are not sent, by design.** Unsubscribe follows only
  one-click HTTP links. When a sender offers only a `mailto:` unsubscribe, the
  app reports it and does nothing, because acting on it would mean sending mail
  from the adopter's account. Confirm the adopter is fine with that.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting agent
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that agent's environment rather than frozen to
whatever this publisher happened to have.

Nothing extra -- runs on the stock workspace environment. The app's Python
dependencies (FastAPI, uvicorn, Jinja2, nh3) come from its `pyproject.toml` and
the included `uv.lock`, and the scripts use only the standard library and the
app's package. The two programs it calls, `latchkey` and `claude`, are part of
every stock workspace.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new agent. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `latchkey` skill; the request
   opens the approval/login flow in the Imbue Studio app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-10-07) -- first publish: Fastmail adaptation of inbox-digest-review

## Adaptation history

Each agent that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
