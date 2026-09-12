# project-daybook

A daybook you talk to in plain language, built so it cannot make things up.

*A daybook is the accounting book of original entry, where things are written down
chronologically as they happen — and an ordinary word for a diary. It keeps both.*

It keeps two kinds of record, in one folder you own:

- **the ledger** — money lent, interest received, something bought, a birthday.
  *How much does Dad owe me today? Whose birthdays are coming up?*
- **notes** — a diary, a work log and a knowledge base, in plain Markdown.
  *What did I say about the pipeline rollback? What did I do last week?*

It is two [Claude Code](https://claude.com/claude-code) skills plus a small
command-line tool. There is no app to run, no account to make, and no database. Your
records are plain text files in a folder you can read, edit and back up yourself —
git, Google Drive, Dropbox, whatever you already use.

## Why you can trust the answers

The rule this project is built around: **Claude never does the arithmetic and never
answers from memory.** Every figure comes from running a command, and every figure
cites the file and line it came from.

- **Beancount** owns the ledger, the parsing and the Decimal maths. It is a mature
  double-entry accounting system that refuses to add two currencies together.
- **Amounts are per currency, always.** Rupees and dollars are reported side by side
  and never converted behind your back.
- **Nothing exists until you record it.** Interest you were actually paid is a
  transaction. Interest that has merely accrued is shown separately, labelled a
  projection, with its formula printed next to it.
- **Names are resolved, not guessed.** "dad", "papa" and "Harjit Singh" map to one
  record. A name that is close but not exact stops and asks you rather than picking.
- **Not everything owed is a loan.** "Nikhil owes me $306 from a trip" is recorded
  immediately as an IOU — a debt with no interest terms — and it is counted in every
  balance and total exactly like a loan is. It is never shown as a 0% loan, because
  nobody agreed 0%; nobody agreed anything. If it turns out to earn interest, the
  same record is given terms later, rather than replaced.
- **A rejected entry really does leave nothing behind.** Every write is checked by
  Beancount before it counts, and a check that fails restores the file's exact bytes
  — not a `git checkout`, which does nothing for an untracked file or a folder that
  isn't a git repository at all, the default for a Drive- or Dropbox-synced ledger.
- **Corrections are new entries.** History is appended to, never rewritten.

The prose half has the same rule in its own form: **Claude never paraphrases a note
from memory.** It quotes what you actually wrote and cites the file and line. If a
search found nothing, it says so instead of reconstructing what you probably meant.

## What this exports

The whole integration surface is **one command-line binary and two Claude Code
skills.** No server, no daemon, nothing to authenticate against, no database — an
agent can only do what you could type yourself, so every action it takes is one you
can reproduce and check by hand.

**The binary.** `daybook` and `ledger` are the same program (the second name is kept
so an install made before this project was renamed keeps working). Every command
takes `--json`, placed *before* the subcommand, for structured output; drop it for a
readable one. The same command serves the agent mid-conversation and you at a
terminal.

**Commands that capture something:**

| Command | Records |
|---|---|
| `entity add` / `alias` / `list` / `book` | a person, place or thing, and their aliases |
| `contract add` / `terms` / `list` / `show` | loan terms; `terms` gives an IOU interest terms later, without opening a new record |
| `add --kind lend\|repay\|borrow\|interest\|spend\|receive` | money moving |
| `void --voids <citation>` | a correction, as a reversing entry |
| `event add` | a birthday, anniversary or reminder → `dates.ics` |
| `note add` / `note amend` | a diary entry; amend appends a correction, never edits one |

**Commands that answer a question:**

| Command | Returns |
|---|---|
| `resolve "name"` | `resolved` / `ambiguous` / `unknown` — never a guess |
| `date "phrase"` | an exact date, or `ambiguous` |
| `balance` / `statement` / `portfolio` | what's owed, per currency, with citations |
| `projection --as-of` | interest that *would* accrue; IOUs are listed under `skipped`, not shown as 0% |
| `search` / `check` / `upcoming` | find an entry, validate the ledger, what's coming up |
| `note find` / `show` / `on` / `week` / `topic` | notes, quoted verbatim, cited `file:line` |
| `note tags` / `people` / `kinds` | the vocabulary already in use, so a tag is reused rather than duplicated |
| `note agenda --days N` | dated notes in a window — **the hook for scheduled automation** |
| `recall "..."` | shorthand for `note find` |

**The two skills** — `.claude/skills/ledger/` and `.claude/skills/notes/` — are not
capability; every command above works without them. They are the *behavioural
contract*: when to reach for which command, and what to never do regardless of what
the tool technically allows. Each states one rule (money: every figure comes from a
command run this turn, cited `file:line`; prose: never paraphrase a note from memory,
quote and cite it) and a `## Never` list — never pick between ambiguous candidates,
never add two currencies, never call an IOU 0%, never edit a record file directly.

**The refusals are the point.** `resolve` returns `ambiguous` instead of guessing a
name. `add` returns `possible_duplicate` instead of silently recording something
twice. `date` refuses a vague phrase instead of assuming one reading. A debt with no
agreed terms becomes an IOU instead of inventing a rate. Each one hands a decision
back to you rather than making it up, which is what keeps every answer checkable.

**For scheduled automation.** `daybook --json note agenda --days N` and
`daybook --json upcoming --days N` are side-effect-free reads that return structured
JSON — a cron job or scheduled agent can ask "what is coming up" without touching
anything. Nothing is built on top of that yet; it is the seam for it.

## Install

Works the same on macOS, Windows and Linux. The only prerequisites are
[git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/).

One command. It asks where you want your records and how to back them up, then
you are recording.

**macOS and Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/imjasdeepk/project-daybook/main/install.sh | bash
```

**Windows**

```powershell
irm https://raw.githubusercontent.com/imjasdeepk/project-daybook/main/install.ps1 | iex
```

Piping a script from the internet into a shell deserves a look first, always.
Download it, read it, then run it:

```bash
curl -fsSLO https://raw.githubusercontent.com/imjasdeepk/project-daybook/main/install.sh
less install.sh && bash install.sh
```

The installer needs [git](https://git-scm.com/downloads) and offers to install
[uv](https://docs.astral.sh/uv/), which brings its own Python. It is safe to run
again later; an existing ledger is left alone.

To skip the questions, answer them up front:

```bash
DAYBOOK_DIR=~/Documents/daybook DAYBOOK_CURRENCIES=INR,USD DAYBOOK_BACKUP=git \
  bash install.sh
```

<details>
<summary>Or install by hand</summary>

```bash
git clone https://github.com/imjasdeepk/project-daybook.git
cd project-daybook
uv sync
uv run daybook init ~/Documents/daybook --currencies INR,USD --title "My Ledger"
```

</details>

Name any folder you like. Your records are written straight into it, and the
tool remembers where they are. Nothing you record is ever stored in this
repository. See [Your records stay private](#your-records-stay-private).

On Windows use PowerShell and the same commands. `uv` installs the right Python for
you, so nothing depends on what is already on your machine.

Then open the folder in Claude Code. The skill in `.claude/skills/ledger/` loads
automatically and you can start talking to it.

## Using it in conversation

```
you>  lent dad 5000 rupees for the car last tuesday

Claude runs: ledger resolve "dad"          -> Harjit Singh
             ledger date "last tuesday"    -> 2026-09-01
             (shows you the entry, then writes it)

you>  how much does dad owe me?

Claude runs: ledger balance "dad"
             -> owed to you: 5000 INR, lent 1 time, cites 2026.beancount:12
```

Claude always shows you what it is about to write, asks when something is unclear,
and quotes the line number of every number it reports back.

## Using it directly

The same commands work on their own, without Claude:

```bash
uv run daybook entity add --name "Jasdeep Katariya" --aliases me --book --self \
    --currency INR

uv run daybook entity add --name "Harjit Singh" --relation father \
    --aliases "dad, papa" --currency INR

uv run daybook contract add --lender me --borrower dad --rate 8 --method simple \
    --started 2026-09-01

uv run daybook add --kind lend --who dad --amount 5000 --date 2026-09-01 \
    --note "for the car" --source "lent dad 5k for the car last tuesday"

uv run daybook balance dad
uv run daybook statement dad
uv run daybook projection dad --as-of 2027-09-01
uv run daybook event add --summary "Dad's birthday" --date "14 March 1958"
uv run daybook upcoming --days 365
uv run daybook check
uv run daybook sync
```

A debt with no agreed rate needs none of that — leave `contract add` out entirely and
`daybook add` opens an IOU on the spot:

```bash
uv run daybook entity add --name Nikhil --aliases nikhil --currency USD
uv run daybook add --kind lend --who nikhil --amount 306 --date 2025-07-01 \
    --note "Phuket trip"
# -> recorded as an IOU with no interest terms; it still counts in balance/portfolio

# if it later turns out to earn interest, give the same record terms:
uv run daybook contract terms 2025-07-01-iou --rate 12 --method simple
```

And for notes:

```bash
uv run daybook note add --title "Ingest pipeline cutover" --kind log \
    --who "Alice Chen, Ravi" --tags "infra, oncall" \
    --body "Cut over at 09:02. Rollback plan was the old consumer group."

uv run daybook note add --title "Tokyo trip booked" --kind travel \
    --when "2026-10-03..2026-10-09" --tags travel

uv run daybook note find "rollback"       # cites 2026/2026-W37.md:5
uv run daybook note topic infra           # everything filed under one tag
uv run daybook note week                  # what happened this week
uv run daybook note agenda --days 30      # notes about a date coming up
uv run daybook note reindex
```

Run `uv run daybook --help` for the full list. Add `--json` to any command for
machine-readable output.

## How notes are filed

One Markdown file per week (or per month — your choice, per folder), plus indexes
that are generated for you and that you never edit:

```
notes/
  notes.toml              <- period = "week" | "month"
  INDEX.md                <- years, counts, date range
  2026/
    2026-W37.md           <- the notes themselves
    INDEX-2026-09.md      <- one line per note: title, who, tags, file:line
    INDEX-2026.md         <- counts and totals for the year
```

Open the folder on your phone and the three index levels tell you what exists and
where, without opening a single note file. The indexes are **derived**: searches read
the Markdown itself, so an index that is behind can only make the folder look untidy —
never make an answer wrong. Delete them all and `daybook note reindex` rebuilds them
byte for byte.

The levels differ in kind, which is what keeps writing fast. The month index lists
every note; the year index is counts built from the twelve month indexes, never from
the notes; the root index is built from the year indexes. So writing one note touches
four small files and reads at most twelve — the same cost on your first day and in
your tenth year. Generated files are only rewritten when their contents actually
change, so a synced folder is not asked to re-upload a file that is already correct.

A note is written once and never edited. Its line number is its citation, and a
correction is `daybook note amend`, which appends a new entry pointing back at the
original. That is also what makes the folder safe to sync from two machines: nothing
ever rewrites what came before.

## Reading your own records

```bash
uv run fava ledger/main.beancount
```

[Fava](https://beancount.github.io/fava/) opens a web page showing balances, account
registers and charts, with every row linking back to the exact line in your file.

Your dates live in `ledger/dates.ics`, a standard calendar file. Subscribe your phone
or desktop calendar to it and birthdays arrive as normal notifications, with nothing
running on your machine.

## Your records stay private

**The tool is public. Your finances are not.** They are two separate git
repositories, and this one ignores the other.

This repository holds the tool. Your records live in a folder of your choosing,
somewhere else entirely.

```
project-daybook/          <- this repository: the tool and the skill
  daybook_tools/
  .claude/skills/ledger/
  .daybook-root           <- one line naming your folder; not published

~/Documents/daybook/      <- your records, wherever you put them
  ledger/
    main.beancount
    accounts.beancount
    2026.beancount
    dates.ics
```

`daybook init <folder>` writes the path into `.daybook-root`, which is gitignored
and personal to your machine. Every session reads it, including ones that never
load your shell profile. `DAYBOOK_ROOT` in the environment does the same job and
wins when both are set. To move your records later, move the folder and update
that one file.

The project was called `project-ledger` until prose records joined the money ones.
`LEDGER_ROOT` and `.ledger-root` are still read, and the `ledger` command still works,
so an install made before the rename keeps working untouched.

### Backing them up is your choice

**A synced folder.** Put your records folder inside Google Drive, Dropbox or
iCloud and let it sync. Nothing else to do, and no git involved.

```bash
uv run daybook init ~/"Google Drive/My Drive/ledger" --currencies USD
```

**A private git repository.** Add `--git` and your records become a repository
of their own. Each entry is then committed as you record it, giving you a dated
history of every change.

```bash
uv run daybook init ~/Documents/daybook --currencies USD --git
cd ~/Documents/ledger
gh repo create <you>/my-ledger --private --source . --remote origin --push
```

After that, `uv run daybook sync` pulls and pushes your records when you move
between machines. Keep that repository **private**. This one can be public
without ever exposing it, because your records are not inside it.

Both options work equally well, and you can start with one and move to the other
by moving the folder.

## What the files are

| Path | What it holds |
|---|---|
| `ledger/main.beancount` | Settings and the list of included files |
| `ledger/accounts.beancount` | People, places and things, with their aliases and loan terms |
| `ledger/<year>.beancount` | Transactions for that year, one block each |
| `ledger/dates.ics` | Birthdays, anniversaries and reminders |
| `.claude/skills/ledger/SKILL.md` | How Claude is required to behave |

Each recorded entry keeps a `source:` line holding the words you actually used, so you
can see later what a number was based on.

## On your phone

The ledger is text in a git repository, so it goes wherever the repository goes. With
your records in their own private repository, open both with Claude Code on the web
from your phone and the skill, the commands and the guardrails travel with them. Run
`ledger sync` before and after so your machines agree.

Subscribe your phone calendar to `dates.ics` for birthday and anniversary
notifications, which then arrive with nothing running anywhere.

## Development

```bash
uv sync --extra dev
uv run pytest
```

Tests run on macOS, Windows and Linux in CI.

## Licence

MIT. See [LICENSE](LICENSE).
