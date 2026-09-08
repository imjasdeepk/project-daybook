# project-ledger

A personal ledger you talk to in plain language, built so it cannot make numbers up.

Tell it what happened — money lent, interest received, something bought, a birthday —
and ask it questions later: *how much does Dad owe me today?*, *how many times have I
lent to him?*, *whose birthdays are coming up this year?*

It is a [Claude Code](https://claude.com/claude-code) skill plus a small command-line
tool. There is no app to run, no account to make, and no database. Your records are
plain text files in a git repository that you can read, edit and back up yourself.

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
- **Corrections are new entries.** History is appended to, never rewritten.

## Install

Works the same on macOS, Windows and Linux. The only prerequisites are
[git](https://git-scm.com/downloads) and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/imjasdeepk/project-ledger.git
cd project-ledger
uv sync
uv run ledger init ~/Documents/ledger --currencies INR,USD --title "My Ledger"
```

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
uv run ledger entity add --name "Harjit Singh" --relation father \
    --aliases "dad, papa" --currency INR --rate 8 --method simple

uv run ledger add --kind lend --who dad --amount 5000 --date 2026-09-01 \
    --note "for the car" --source "lent dad 5k for the car last tuesday"

uv run ledger balance dad
uv run ledger statement dad
uv run ledger projection dad --as-of 2027-09-01
uv run ledger event add --summary "Dad's birthday" --date "14 March 1958"
uv run ledger upcoming --days 365
uv run ledger check
uv run ledger sync
```

Run `uv run ledger --help` for the full list. Add `--json` to any command for
machine-readable output.

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
project-ledger/          <- this repository: the tool and the skill
  ledger_tools/
  .claude/skills/ledger/
  .ledger-root           <- one line naming your folder; not published

~/Documents/ledger/      <- your records, wherever you put them
  main.beancount
  accounts.beancount
  2026.beancount
  dates.ics
```

`ledger init <folder>` writes the path into `.ledger-root`, which is gitignored
and personal to your machine. Every session reads it, including ones that never
load your shell profile. `LEDGER_ROOT` in the environment does the same job and
wins when both are set. To move your records later, move the folder and update
that one file.

### Backing them up is your choice

**A synced folder.** Put your records folder inside Google Drive, Dropbox or
iCloud and let it sync. Nothing else to do, and no git involved.

```bash
uv run ledger init ~/"Google Drive/My Drive/ledger" --currencies USD
```

**A private git repository.** Add `--git` and your records become a repository
of their own. Each entry is then committed as you record it, giving you a dated
history of every change.

```bash
uv run ledger init ~/Documents/ledger --currencies USD --git
cd ~/Documents/ledger
gh repo create <you>/my-ledger --private --source . --remote origin --push
```

After that, `uv run ledger sync` pulls and pushes your records when you move
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
