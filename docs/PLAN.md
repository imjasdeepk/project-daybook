# Personal Ledger: open-source Claude Code skill on top of Beancount, Fava and remind

## Context

You want a personal ledger you can talk to: capture loans, repayments, interest, purchases, birthdays and anniversaries, then ask "how much does Dad owe as of today", "how many times have I lent to X", "whose birthdays fall in the next year". Hard requirements: no wrong facts, every answer citable to a human-readable record, dedup of names ("dad" vs "Harjit Singh") with a question when unclear. Decisions: multiple currencies never silently converted; each loan carries a rate and simple/compound method; nothing exists unless captured; start fresh; **open source, built on existing tools rather than a new product; portable to macOS and Windows with a simple install**.

Design: **Claude never does arithmetic or recalls balances from memory.** Established open-source tools own storage, validation, math and dates. The only new code is a Claude Code skill (Markdown) plus two short helper scripts that call the Beancount Python API. The repo is published under MIT so anyone can drop the skill into their own Claude Code.

| Need | Existing tool | Why it fits |
|---|---|---|
| Ledger storage, validation, multi-currency math, counts, balances as-of-date | **Beancount** (plain-text double-entry, Python, `bean-check`, `bean-query` with SQL-like BQL) | Decimal precision, refuses to mix currencies, every transaction is a citable line in a text file, metadata fields hold the verbatim source phrase |
| Human review and browsing | **Fava** (local web UI for Beancount) | Statements, per-account registers, charts, click-through to the source line; works from a phone browser later |
| Birthdays, anniversaries, reminders | **iCalendar `.ics` file** queried with the `icalendar` + `recurring-ical-events` Python libraries | Open standard, yearly recurrence built in, readable text, and the same file can be subscribed to from Apple/Google/Outlook calendar on any phone for free notifications |
| Audit trail | **git** | Every capture is a commit |
| Install and run everywhere | **uv** (one binary for macOS/Windows/Linux) | `uv sync` installs Python and every dependency; every command runs as `uv run ...`, so nothing depends on PATH, shell, or Homebrew |

Portability rules: no Homebrew-only or Unix-only tools; all helper code is Python using `pathlib` and forward-slash relative paths; the skill never uses shell pipes, `grep`, or `$(...)`; GitHub Actions CI runs the test suite on `macos-latest` and `windows-latest`.

Assumption on interest (from your answer): balances come only from recorded transactions. The loan's rate and method are stored as account metadata and used only for a separately labelled projection that prints its formula.

## Repository layout (`/Users/jasdeepkatariya/projects/project-ledger`, public git repo, MIT)

```
LICENSE, README.md                 install (2 commands), conventions, example dialogue
CLAUDE.md                          guardrails Claude must follow (below)
.claude/skills/ledger/SKILL.md     capture + query protocol
pyproject.toml                     uv project: beancount, fava, icalendar, recurring-ical-events
                                   (Python 3.13 pin if 3.14 wheels are missing)
.github/workflows/ci.yml           pytest on macos-latest and windows-latest
ledger/
  main.beancount                   options, includes, plugins
  accounts.beancount               open directives per person/place/thing with metadata (see model)
  2026.beancount                   transactions, one file per year
  dates.ics                        birthdays, anniversaries, reminders (iCalendar, yearly RRULE)
scripts/
  init.py                          first-run wizard: your name, currencies, creates the files above
  resolve.py                       alias -> account resolution using beancount.loader + difflib
  projection.py                    expected interest for a loan account, labelled PROJECTION
  upcoming.py                      expands dates.ics recurrences: --days N, --on DATE
  queries/*.bql                    saved BQL queries the skill runs (balance, counts, statement, search)
tests/                             pytest: sample ledger, BQL golden outputs, resolve, projection, upcoming
```

Install on either OS: install uv (one line from the uv site), then `uv sync`, then `uv run scripts/init.py`. Open the folder in Claude Code and the skill is active. No other prerequisites besides git.

## Data model (all in Beancount's own syntax, no second format)

Entity = an account with metadata. Aliases live on the `open` directive so resolution and the ledger share one file:

```beancount
2026-01-01 open Assets:Loans:HarjitSingh  INR,USD
  name: "Harjit Singh"
  type: "person"
  relation: "father"
  aliases: "dad, papa, father"
  rate_percent_pa: "8"
  method: "simple"          ; simple | compound
  compounding: "monthly"    ; compound only
  day_count: "actual/365"
```

Transaction = one Beancount entry. Kind is expressed by the accounts, verbatim phrasing is metadata for citation:

```beancount
2026-09-08 * "Harjit Singh" "Lent for car repair"
  source: "2026-09-08 chat: 'lent dad 5k for car repair'"
  Assets:Loans:HarjitSingh     5000.00 INR
  Assets:Cash:INR

2026-10-01 * "Harjit Singh" "Interest received"
  source: "..."
  Assets:Cash:INR               100.00 INR
  Income:Interest:HarjitSingh
```

Account conventions: `Assets:Loans:<Person>` (money owed to you), `Liabilities:Owed:<Person>` (money you owe), `Income:Interest:<Person>`, `Expenses:<Category>`, `Assets:Cash:<CCY>`. Places and things are payees or `Expenses:` subaccounts with the same metadata scheme.

Events in `dates.ics` (standard iCalendar; the UID is the citation, the description holds the verbatim source):

```
BEGIN:VEVENT
UID:birthday-harjit-singh
SUMMARY:Harjit Singh (dad) birthday
DTSTART;VALUE=DATE:19580314
RRULE:FREQ=YEARLY
DESCRIPTION:source 2026-09-08 chat: "dad's birthday is 14 March 1958"
END:VEVENT
```

Corrections: never edit history by hand. Add a reversing transaction with `note: "voids <date> <narration>"` and a fresh entry.

## What Claude runs (no custom query engine)

All commands are `uv run ...` so they behave identically on macOS and Windows.

- Resolve name: `uv run scripts/resolve.py "dad"` → `{status: resolved|ambiguous|unknown, candidates}`. Exact alias/name match resolves; difflib matches above a threshold are `ambiguous`; else `unknown`.
- Validate: `uv run bean-check ledger/main.beancount` after every write. A failed check is reverted (git checkout) and reported.
- Balance as of today: `uv run bean-query ledger/main.beancount "SELECT account, sum(position) WHERE account ~ 'HarjitSingh' AND date <= today()"`.
- Counts: `SELECT count(*) WHERE account = 'Assets:Loans:HarjitSingh' AND number > 0` (lent), `number < 0` (repaid), `account ~ 'Income:Interest:HarjitSingh'` (interest received).
- Statement with citations: `SELECT date, narration, position, balance, filename, lineno WHERE account ~ 'HarjitSingh'`. `filename:lineno` is the citation.
- Projection: `uv run scripts/projection.py Assets:Loans:HarjitSingh --as-of 2026-12-31` → prints inputs, formula, result, header `PROJECTION — not recorded, not owed`.
- Upcoming events: `uv run scripts/upcoming.py --days 365` (every occurrence in the window, with days-until) and `uv run scripts/upcoming.py --on 2027-09-08` for "on this date". Recurrence expansion is done by `recurring-ical-events`, not by hand.
- Search: `SELECT date, narration, meta('source') WHERE narration ~ 'car' OR any_meta('source') ~ 'car'`.
- Duplicate check before a write: BQL for same account, same amount, date within ±3 days. If a row comes back, show it and ask.
- Human review: `uv run fava ledger/main.beancount` opens the browser UI; git log shows one commit per capture; `dates.ics` opens in any calendar app.

## Guardrails (`CLAUDE.md` and `SKILL.md`)

1. Never state a number, count, balance, or date that did not come from a `bean-query` or script run in the current turn. Cite `filename:lineno` for transactions and the event UID for dates.
2. Never write with an unresolved party. Run resolve first; on `ambiguous`/`unknown`, ask, then add the alias to the `open` directive so the question is not repeated.
3. Confirm inferred fields before writing: relative dates (state the absolute date), currency when not stated and the account has more than one, direction (lent vs received), which loan account when a person has several.
4. On a duplicate-check hit, show the existing entry and ask before appending.
5. Every write = append to the year file, `bean-check`, `git commit -m "capture: <narration>"`, then echo the entry as stored with its line number.
6. Corrections are reversing entries, never edits.
7. If a question cannot be answered by BQL or one of the scripts, say so instead of estimating.

## Mobile-ready, not laptop-locked

Nothing in the design assumes a laptop: no local database, no daemon, no OS-specific tool. The ledger is text in a git repo and every operation is a `uv run` command, so the same skill works wherever Claude Code runs:

- **Now**: Mac or Windows terminal, or the Claude Code desktop app.
- **Phone, no extra build**: push the repo to a private GitHub repo and open it in Claude Code on the web (claude.ai/code) from the phone; the skill, scripts and guardrails travel with the repo. Subscribe the phone calendar to `dates.ics` for native birthday notifications.
- **Later, if wanted**: expose Fava over Tailscale for phone review, or wrap the same scripts in a small remote MCP server for the regular Claude mobile app. Both reuse the files and scripts unchanged.

## Implementation steps

1. **Bootstrap**: `git init`, MIT `LICENSE`, `uv init` and add `beancount`, `fava`, `icalendar`, `recurring-ical-events` (check Windows and macOS wheels for Python 3.14; pin 3.13 if needed), `.gitignore`, CI workflow for macOS + Windows.
2. **Ledger skeleton + init wizard**: `main.beancount` with `option "operating_currency"` per chosen currency, `plugin "beancount.plugins.check_commodity"`, includes; empty `accounts.beancount`, `2026.beancount`, `dates.ics` with a valid VCALENDAR wrapper. `scripts/init.py` asks for name and currencies and writes these files. `uv run bean-check` passes on the fresh ledger.
3. **resolve.py**: load ledger with `beancount.loader.load_file`, index `name` and `aliases` metadata across `Open` directives, exact then fuzzy match, JSON output. ~80 lines.
4. **projection.py**: read rate/method/compounding/day_count from the account's `open` metadata, fetch dated postings via `beancount.core.realization` or BQL, compute simple or compound interest on outstanding principal, print formula and inputs. ~120 lines. Decimal only.
5. **Saved BQL queries** in `scripts/queries/` with a `{account}` placeholder, so the skill invokes stable, tested queries rather than composing SQL ad hoc. **upcoming.py** loads `dates.ics`, expands recurrences with `recurring-ical-events` for `--days N` or `--on DATE`, prints UID, summary, next date, days-until; also `--add` to append a VEVENT with UID, RRULE and source description. ~80 lines.
6. **SKILL.md + CLAUDE.md**: Capture protocol and Query protocol with example dialogues: alias ambiguity, duplicate, relative date, multi-currency, "birthdays in the next year", correction.
7. **Tests**: sample ledger fixture; golden `bean-query` output for balance/counts/statement; resolve exact/ambiguous/unknown; projection simple and compound against hand-computed values; upcoming across a year boundary and 29 Feb; all paths via `pathlib` so the suite passes on Windows CI.
8. **README**: install (install uv, `uv sync`, `uv run scripts/init.py`) with both macOS and Windows PowerShell lines, how to add the skill to your own Claude Code, conventions, screenshots of Fava, how to subscribe a phone calendar to `dates.ics`.
9. **Optional later**: push to GitHub and use Claude Code on the web from a phone; expose Fava over Tailscale; remote MCP wrapper for the Claude mobile app.

## Verification (end-to-end)

1. `uv run pytest` green locally and on both CI runners; `uv run bean-check ledger/main.beancount` clean.
2. Scripted scenario: open `Assets:Loans:HarjitSingh` (aliases dad/papa, 8% simple); lend 5000 INR twice and 200 USD once; repay 2000 INR; record 100 INR interest. Then confirm:
   - balance query → INR 8000 and USD 200 as separate rows, never summed; counts lent 3, repaid 1, interest 1; each row cites `2026.beancount:<line>`.
   - projection script → labelled projection with formula and inputs matching a hand calculation.
   - `resolve.py papa` resolved; `resolve.py harjeet` ambiguous with candidate; `resolve.py ravi` unknown.
   - Appending the same lend again trips the duplicate query.
   - Birthday 14 Mar 1958 added to `dates.ics`; `upcoming.py --on 2027-03-14` and `--days 365` both return it with the UID; the file opens cleanly in Apple Calendar or Outlook.
   - Open Fava, click the Harjit Singh account, confirm every row links to the source line.
3. Conversational check in Claude Code with the skill: "lent dad 5k for the car last tuesday" → Claude resolves, states the absolute date, shows the entry to be written, appends, `bean-check`, commits, echoes the stored entry. "How much does dad owe today?" → the answer contains only query output and citations.
