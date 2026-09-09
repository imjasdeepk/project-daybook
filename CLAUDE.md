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
| `ledger_tools/contracts.py` | Loan contracts: who lent whom, on what terms -- one entity can have several |
| `ledger_tools/queries.py` | Balances, statements, search, duplicate detection, cross-contract aggregation |
| `ledger_tools/interest.py` | Interest projections, per contract, never treated as owed |
| `ledger_tools/events.py` | The iCalendar file for birthdays and reminders |
| `ledger_tools/capture.py` | Writing entries, validating them, rolling back failures |
| `ledger_tools/dates.py` | Turning phrases like "last tuesday" into exact dates |
| `ledger/` | Only if records are kept here. **Gitignored; normally they live outside this repo.** |
| `tests/` | pytest, run on macOS, Windows and Linux in CI |

## Conventions that matter

- **Accounts.** `Equity:Entities:<Slug>` is the record of a person, place or thing and
  carries their aliases as metadata -- no rate lives here any more. A loan is a
  **contract**, its own `open` directive under
  `Assets:Loans:<Owner>:<Counterparty>:<ContractId>` (owner is the lender) or
  `Liabilities:Owed:<Owner>:<Counterparty>:<ContractId>` (owner is the borrower),
  carrying `lender`, `borrower` and terms as metadata. "Owner" is whichever party is a
  *book* this ledger keeps (`book: "true"` on the entity); the same borrower can hold
  several contracts, from different lenders, at different rates, because the rate lives
  on the contract, not the entity. Cash is per book: `Assets:Cash:<Owner>:<CCY>`.
  `Income:Interest:<Owner>:<Counterparty>:<ContractId>` is interest received,
  `Expenses:Interest:<Owner>:<Counterparty>:<ContractId>` interest paid. The account
  path is a derived address, never parsed to decide anything -- the contract's metadata
  is the only source of truth, and `queries.py`/`interest.py` work from the contract
  registry (`contracts.load_contracts`), not from splitting account strings.
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
- **Records never enter this repository.** They normally live outside it entirely,
  found via a gitignored `.ledger-root` file or the `LEDGER_ROOT` variable.
  `store.git_repo_for` resolves commits to whichever repository actually contains the
  files, so entries land in the private one. Never add anything under `ledger/` to
  this repository, and never write example records there.

## Testing

New behaviour needs a test that would fail without it. Interest changes need a test
that checks the result against a hand calculation written out in the test, not against
whatever the code currently returns. A contract-model change needs a test with at
least two contracts at different rates, since that is the case a single-contract test
cannot catch (see `test_two_contracts_for_the_same_borrower_project_at_their_own_rates`
in `tests/test_contracts.py`).
