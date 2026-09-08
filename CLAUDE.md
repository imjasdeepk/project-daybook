# project-ledger

A personal ledger built so its answers can be checked. Beancount owns the records and
the arithmetic. This repository adds a thin command-line tool and a Claude skill.

## The rule that shapes everything

**Claude never calculates and never answers from memory.** Every figure in an answer
comes from a `ledger` command run in that turn, and cites the file and line it came
from. If no command produced it, it does not get said.

## Working here

```bash
uv sync --extra dev      # install
uv run ledger --help     # the tool
uv run pytest            # tests
uv run fava ledger/main.beancount   # browse the ledger in a browser
```

`.claude/skills/ledger/SKILL.md` is the operating manual for conversations. Read it
before changing behaviour that affects what Claude says to the user.

## Layout

| Path | What it is |
|---|---|
| `ledger_tools/` | The Python package behind the `ledger` command |
| `ledger_tools/store.py` | Finding, loading, appending to and committing the ledger |
| `ledger_tools/entities.py` | Turning spoken names into accounts; alias handling |
| `ledger_tools/queries.py` | Balances, counts, statements, search, duplicate detection |
| `ledger_tools/interest.py` | Interest projections, which are never treated as owed |
| `ledger_tools/events.py` | The iCalendar file for birthdays and reminders |
| `ledger_tools/capture.py` | Writing entries, validating them, rolling back failures |
| `ledger_tools/dates.py` | Turning phrases like "last tuesday" into exact dates |
| `ledger/` | Your actual records, in Beancount and iCalendar format |
| `tests/` | pytest, run on macOS, Windows and Linux in CI |

## Conventions that matter

- **Accounts.** `Equity:Entities:<Slug>` is the record of a person, place or thing and
  carries their aliases and loan terms as metadata. `Assets:Loans:<Slug>` is what they
  owe you, `Liabilities:Owed:<Slug>` what you owe them,
  `Income:Interest:<Slug>` interest actually received, `Assets:Cash:<CCY>` your side.
- **Money is `Decimal`, always.** Never float. Beancount enforces this; do not work
  around it.
- **Currencies never mix.** Every total is a dict keyed by currency. There is no
  conversion anywhere in this codebase and none should be added without the user
  asking for it explicitly.
- **Appends only.** `store.append_block` adds to a file; nothing rewrites history. A
  correction is a reversing entry via `ledger void`.
- **Every write is validated.** `capture.commit_entry` loads the ledger after writing
  and rolls the file back if Beancount rejects it.
- **Ambiguity stops the machine.** `entities.resolve` returns `ambiguous` rather than
  choosing, and `dates.parse` does the same. Both are deliberate. Do not add a
  "best guess" fallback.
- **Portability.** Use `pathlib`, never shell pipes or Unix-only tools. Tests must
  pass on Windows.

## Testing

New behaviour needs a test that would fail without it. Interest changes need a test
that checks the result against a hand calculation written out in the test, not against
whatever the code currently returns.
