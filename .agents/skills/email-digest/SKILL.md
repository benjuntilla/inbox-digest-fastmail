---
name: email-digest
description: Build an inbox digest for the user. Reads the Fastmail inbox, classifies every message into the 11-bucket taxonomy via mailing-list header hints + content-aware rules, and surfaces what needs a reply within 48hrs, what decisions are pending, what's truly cold outreach, what you're waiting on, and what's pure noise. Use when the user says "show me my digest", "what's in my inbox", "what do I need to reply to", "what am I waiting on", or asks to triage / process their email.
---

# Email digest skill

## Always start here

Before doing anything, read `.agents/skills/email-digest/RULES.md`. It is the
canonical, user-editable source of truth for the 10-bucket taxonomy, the
label-based pre-filter, the content-aware rules per bucket, the cold-outreach
detection methodology, and the maintained lists of known automation patterns
and vendor domains. When this file and RULES.md disagree, RULES.md wins.

Set your identity in `system/apps/email_review/src/email_review/account.py` (your
name, addresses, org domains, AP forwarder) and fill in
`.agents/skills/email-digest/contacts.txt` (your who's-who allowlist) before
the first run — the classifier reads both.

## Pipeline (every digest run)

1. **Pull.** Fetch *everything currently in the inbox* — not just the most
   recent N. Treat "archive" as "done," so any unarchived message is in scope
   for the digest: anything in the Fastmail Inbox mailbox. The pull is a JMAP
   `Email/query` on the Inbox (newest first) plus a batched `Email/get` of
   sender, recipients, subject, dates, preview, and the `List-Unsubscribe` /
   `List-Id` headers. All Fastmail calls go through the shared client in
   `system/apps/email_review/src/email_review/fastmail.py`.

   **Performance:** the sent-history checks run in parallel (12 workers). For
   day-to-day digest runs, restrict to a recent window to keep latency low; do
   the full sweep weekly or when explicitly asked. The `classify.py` script
   implements this pull.

2. **Pre-filter by headers.** Apply the table in RULES.md "Layer 1." First
   match wins; result is a *starting bucket*. Fastmail has no Gmail-style
   categories, so `classify.py` derives two hints from standard headers:
   `List-Unsubscribe` → `CATEGORY_PROMOTIONS`, `List-Id` alone →
   `CATEGORY_FORUMS`. Anything that hits "None of the above" is ambiguous and MUST go
   through the content pass.

3. **Cold-outreach detection.** Run the methodology in RULES.md "Cold-outreach
   detection" for every sender on every message whose starting bucket is
   1 / 2 / 3. The false-positive checks (FP1-FP5) re-route wrongly-flagged-cold
   items to buckets 3, 7, 8, 9, or 10.

4. **Content-aware pass.** Read snippet (and full body when inconclusive) for
   every message and apply the bucket-question order in RULES.md "Layer 2."
   Override the pre-filter bucket whenever the content disagrees. Labels alone
   are ~80% accurate; the remaining 20% are exactly the items that matter most.

5. **Self-review and fix (REQUIRED).** Before rendering anything, scan every
   bucket's items yourself looking for obvious misclassifications, e.g.:
   - Senders matching obvious automation patterns the list didn't catch.
   - Phishing tells (hex-string-in-subject signature requests, display-name
     impersonation, urgent-wire body language) in cold outreach instead of
     marketing/spam. TLD alone is NOT a phishing tell.
   - Action-needed items via the AP forwarder (`[DUE TODAY]`,
     `Approval needed:`) routed to bucket 4 instead of bucket 10.
   - **Event invites routed to bucket 7 (marketing) — they belong in bucket 6
     (Cold outreach + event invites)** so you actually see them.
   - **Same-thread messages in different buckets — apply thread continuity.**
     If two messages share a `threadId` they must share a bucket.
   - **Bucket 1 items where you aren't actually the actor.** For every bucket-1
     thread, read the latest inbound message and verify you are being directly
     asked (see RULES.md "Reply needed?"). Conservative default: if unsure,
     keep in bucket 1.

   Apply every fix you'd note. Encode the fix as either a code change or an
   addition to the maintained lists in RULES.md. Re-run and verify against
   specific cases (not just "rule implemented"). Only then proceed.

   **Synthesis quality bar.** Every item gets a one-line synthesis following
   the pattern `{Sender (optional org)} — {topic} {optional context}`. For
   event invites, **include WHAT the event is and roughly WHEN** so you can
   scan and decide.

6. **Dedup bucket 5.** Group by `threadId`. Collapse "same outgoing message to
   N recipients" into one entry with a list of who hasn't replied. Sort by age
   descending.

7. **Render.** The digest is rendered by the `email-review` app (a
   FastAPI app; see `system/apps/email_review`). Run `classify.py` to write
   `data/.apps/email-review/data.json`, then the app reads it and renders
   the bucket-grouped triage UI. The Refresh / Categorize buttons in that UI
   re-run this pipeline.

## One-click actions (the email-review UI)

The email-review web UI performs the triage actions directly against Fastmail
(JMAP via `latchkey curl`). For each thread you can:

- **Archive** — move the thread's inbox messages to Archive.
- **Mark spam** — move them to Spam (and flag them as junk for Fastmail's filter).
- **Mute** — move them to the app's `Muted` mailbox (created on first use).
  Future messages on a muted thread are archived on the next refresh by
  `scripts/propagate_mutes.py`.
- **Unsubscribe** — one-click via the message's `List-Unsubscribe` header
  (RFC 8058 POST or GET). Email-only (mailto) unsubscribes are never sent; the
  app has no permission to send mail, so those fall back to mute.

### Smart-action ruleset (agent use only)

The page no longer shows "smart", "smart all", or "save" buttons (removed at
the user's request): rows offer move / ask / draft reply / add to calendar /
archive, and noisy groups have "archive all". The ruleset below still backs
`/api/smart-action` for when the user asks the agent to clean something up.

The UI shows an "Archive / Unsub / Mute / Spam" button on buckets 6 (Cold
outreach + events) and 7 (Marketing / spam / phishing), and per-row on 8/9.
When you click it, the service decides per-thread which action to take. Decision
order (implemented in `mail_actions.smart_action`):

1. **Keep-subscribed override.** If the sender is on the `keep-subscribed`
   list (contacts.txt), archive only — never unsubscribe.
2. **Phishing in bucket 7 → mark spam.** Never click a phisher's unsubscribe
   link.
3. **Internal-forwarder guard.** If the `List-Unsubscribe` header points back
   at one of your own org domains (`account.ORG_DOMAINS`), archive only —
   unsubscribing would drop you from your own group. Preflight standalone with
   `scripts/check_unsub_target.py --thread-id <id>` (verdict `external` = safe,
   `internal-forwarder` / `no-header` = do not unsubscribe).
4. **Unsubscribe** via the `List-Unsubscribe` header if present and usable --
   but only after the user confirms. The first call returns
   `needs_confirm`; the page asks "Unsubscribe from X?" with Unsubscribe /
   Keep me subscribed / cancel. "Keep me subscribed" adds a `keep-subscribed`
   row to the app's own contacts file, `data/.apps/email-review/contacts.txt`
   (section "never unsubscribe"), and archives instead.
   Bulk "smart all" asks once for all such senders at the end.
5. **Mute** for buckets 6/7/8/9 when there's no usable unsubscribe option —
   moves the thread to the `Muted` mailbox.
6. **Mark as spam** if the snippet has phishing tells (urgent ACH/wire).
7. **Archive** otherwise.

Every action returns enough info for the undo toast to reverse it. Unsubscribe
undo can move the thread back to the Inbox but cannot re-subscribe.

## School group

Bucket 11, "School" (`email_review/school.py`). `classify.py` step 7 and the
AI review pass move a thread to it when a sender is on one of
`account.SCHOOL_DOMAINS` (subdomains included) and the thread had landed in
FYI (3), cold outreach (6), notifications (8), or reading (9). Reply needed, decisions,
TODOs, and marketing keep their bucket. Empty `SCHOOL_DOMAINS` turns it off.

## Learning from manual moves

Every move in the app is logged to `data/.apps/email-review/move_log.jsonl`.
`classify.py` reads it on every run: a sender whose last two moves went to the
same bucket gets a learned rule, and their mail lands there from then on
(reason shown as "Learned from your moves"). Moving that sender twice somewhere
else replaces the rule. The AI review pass never overrides a learned rule.
`system/scripts/review_email_moves.py` still reports domain-level patterns,
which are worth turning into a RULES.md / contacts.txt change by hand.

## Morning run

`scripts/daily_digest.py` is what the 7 AM schedule
(`data/.state/cron.d/inbox-digest-morning`) runs: the full Refresh &
Categorize pipeline, then one notification counting the threads that need a
reply, a decision, or a to-do.

## Add to calendar

Rows in Decision, FYI, TODO, Cold outreach + events, and School have an "add
to calendar" button (`email_review/calendar_events.py`). Claude reads the
latest message and returns the event (title, local start + IANA time zone,
duration, location, join link) or says there is none; the page shows it and
asks before adding. It goes to the default Fastmail calendar, or to a
calendar named "School" for School threads. Undo deletes the event. Needs the
`fastmail-api / fastmail-write-calendars` permission.

## Weekly reading summary

`scripts/reading_summary.py` (scheduled Sundays 8 AM via
`data/.state/cron.d/inbox-digest-reading-summary`) has Claude summarize the
Reading group's last 7 days into one page, saved with its sources to
`data/.apps/email-review/reading_summaries/<date>.json` and shown at the app's
`/reading-summary`. The weekly run then archives newsletters never opened
(Fastmail `$seen` unset); the page's Undo puts them back. "Summarize now" on
the page writes a summary without archiving.

## Maintaining the contacts file

`.agents/skills/email-digest/contacts.txt` is the persistent home for "who's
who" information — vendors, contractors, friends, brokers, and addresses that
should always route a certain way. The classifier reads it on every run.

**Whenever the user mentions a contact, vendor, or relationship — explicitly or
in passing — update this file before responding.** Format: tab-separated
`email-or-domain  TAB  name  TAB  category  TAB  notes`. The categories and
their routing are documented at the top of `contacts.txt`; the classifier
routes:
- `vendor` and `contractor` → bucket 10 (Work FYI) on invoice/payment subjects
- `org-fyi` → bucket 10 unconditionally
- `trusted-warm` / `personal-service` / `journalist` → treated warm
- `broker` → catches the "X introduces people to you" pattern (FP3)
- `keep-subscribed` → never auto-unsubscribed by smart-action

A bare-domain `trusted-warm` row (e.g. `university.example`) only lifts cold-outreach
detection for people on that exact domain. Automation-sender patterns,
mailing-list hints, and the phishing check still apply to its mail; those
exemptions need an exact-address row.

### Import the Fastmail address book: `import_fastmail_contacts.py`

Everyone in the Fastmail contacts with an email address becomes a
`trusted-warm` row in a managed block of the app's own contacts file,
`data/.apps/email-review/contacts.txt` (own addresses, automated senders,
one-off relay addresses, and anything with a hand-written row are skipped).
That file also holds the "never unsubscribe" rows the digest page adds; the
classifier reads it together with this skill's hand-written contacts.txt
(`email_review/contacts_files.py`). It runs every Sunday at 6 AM
(`data/.state/cron.d/inbox-digest-contacts`); run it by hand any time:

```bash
uv run python .agents/skills/email-digest/scripts/import_fastmail_contacts.py --write
```

### Bulk archive a sender (with exception list): `bulk_archive.py`

When the user says "clear out the X@Y.com emails from my inbox, but leave
anything that needs my eyes," use the bulk archive flow:

1. **Surface candidates.** Pull every message matching the query and run an
   LLM-judge pass (or a content filter) to identify the small set that needs
   review. Save those to a `look_list.json` file.

2. **Dry-run.** Show the user the candidate count and the look-list:

   ```bash
   uv run python .agents/skills/email-digest/scripts/bulk_archive.py \
       --query "from:notifications@example.com in:inbox" \
       --keep-from /path/to/look_list.json
   ```

   This writes `runtime/bulk_archive/pending.json` with every would-archive ID
   and prints a summary. Always confirm before passing `--yes`.

3. **Archive.** Re-run with `--yes`. The script writes
   `runtime/bulk_archive/last_run_<ts>.json` containing every archived ID, so
   the action is reversible.

4. **Undo if needed.**

   ```bash
   uv run python .agents/skills/email-digest/scripts/bulk_archive_undo.py last_run_<ts>.json
   ```

The query uses Gmail-style operators, translated to a Fastmail search:
`from:`, `to:`, `cc:`, `subject:`, `in:inbox|sent|archive|spam|trash`,
`newer_than:Nd`, and free text. Anything else is refused rather than guessed.

### Bulk audit: `contact_audit.py`

For a periodic refresh (run monthly), use the audit script:

```bash
# Dry-run: scan last 180 days of sent mail, LLM-judge candidates,
# write a review file to runtime/contact_audit/proposed_contacts_<date>.md
uv run python .agents/skills/email-digest/scripts/contact_audit.py

# Same but actually append to contacts.txt under a dated section
uv run python .agents/skills/email-digest/scripts/contact_audit.py --write
```

The script pulls your sent mail from Fastmail, filters out automated senders +
existing contacts + your own aliases (read from `account.ACCOUNT_ADDRS`), tiers
by frequency (3+ auto-included, 1-2 LLM-judged), and adds back mass-invite
recipients who didn't reply but are known contacts. Default output is a review
markdown file — always show that to the user before passing `--write`.

## When to update RULES.md

- A new automation sender pattern slips through → add to the list in RULES.md.
- A new vendor/contractor domain appears → add it to contacts.txt.
- A misclassification pattern surfaces in conversation → encode the rule in
  RULES.md, not in this file.
- The 10-bucket taxonomy changes → update RULES.md first, then this skill.

## Open work

- Slack integration not yet built. Use `latchkey services info slack` to
  confirm scope, then add a Slack pull step before the digest render.
- Replies are drafts only. The "draft reply" button on Reply-needed and
  Decision rows (`email_review/drafts.py`) has Claude write a reply from the
  thread and saves it to Fastmail Drafts on the same thread; it never sends.
  Undo deletes the draft (refused if it is no longer a draft).
