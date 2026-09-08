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
uv run ledger init --currencies INR,USD --title "My Ledger"
```

That creates your records in `ledger/`, which this repository deliberately does
not track. See [Your records stay private](#your-records-stay-private) below.

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

```
project-ledger/          <- this repository, public: the tool and the skill
  ledger_tools/
  .claude/skills/ledger/
  ledger/                <- a separate private repository: your records
```

`ledger/` is listed in `.gitignore` here, so nothing you record can reach the
public repository even by accident. Entries are committed to the private
repository instead, which keeps the audit trail without publishing it.

Give that private repository a home of its own:

```bash
cd ledger
gh repo create <you>/my-ledger-data --private --source . --remote origin --push
```

After that, `uv run ledger sync` pulls and pushes your records. Run it when you
switch between machines. If you would rather not use GitHub, put the `ledger`
folder inside Google Drive, Dropbox or iCloud and let that sync it. The tool does
not care which, and works the same with no remote at all.

To point the tool at records kept somewhere else entirely:

```bash
export LEDGER_ROOT=/path/to/my-ledger-data
```

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
