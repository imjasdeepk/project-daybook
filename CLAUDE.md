# project-daybook

A daybook built so its answers can be checked. A daybook is both the accounting book
of original entry and an ordinary diary, which is exactly the two things this keeps:

- **the ledger** -- money and dates. Beancount owns the records and the arithmetic.
- **notes** -- a diary and knowledge base in plain Markdown.

This repository adds a thin command-line tool and a Claude skill for each.

## The rule that shapes everything

**Claude never calculates and never answers from memory.** Every figure in an answer
comes from a `daybook` command run in that turn, and cites the file and line it came
from. If no command produced it, it does not get said.

The prose half has the same rule in its own form: **never paraphrase a past note from
memory -- quote it, and cite its `file:line`.** Recall is retrieval, not recollection.

## Working here

```bash
uv sync --extra dev       # install
uv run daybook --help     # the tool
uv run pytest             # tests
uv run fava ledger/main.beancount   # browse the ledger in a browser
```

`.claude/skills/ledger/SKILL.md` and `.claude/skills/notes/SKILL.md` are the operating
manuals for conversations. Read them before changing behaviour that affects what Claude
says to the user.

## The name

The project is `daybook`; `ledger` is one of its two features. So `daybook balance`,
`daybook contract`, the `ledger/` records folder and the ledger skill all keep the word
"ledger" -- it is still the right word for the money side. Only the project, package,
command, root-pointer file and environment variable are spelled `daybook`. A blind
find-and-replace over this repository would be wrong.

`LEDGER_ROOT` and `.ledger-root` are read for ever alongside `DAYBOOK_ROOT` and
`.daybook-root`: they name where somebody's real records live, and dropping them would
orphan every install made before the rename.

## Layout

| Path | What it is |
|---|---|
| `daybook_tools/` | The Python package behind the `daybook` command |
| `daybook_tools/files.py` | Filesystem and git plumbing. **Stdlib only, no Beancount** -- this is what lets notes work without a ledger |
| `daybook_tools/notes.py` | The diary and knowledge base: Markdown period files, derived indexes, search |
| `daybook_tools/store.py` | Finding, loading, appending to and committing the ledger |
| `daybook_tools/entities.py` | Turning spoken names into accounts; alias handling |
| `daybook_tools/contracts.py` | Loan contracts: who lent whom, on what terms -- one entity can have several |
| `daybook_tools/queries.py` | Balances, statements, search, duplicate detection, cross-contract aggregation |
| `daybook_tools/interest.py` | Interest projections, per contract, never treated as owed |
| `daybook_tools/events.py` | The iCalendar file for birthdays and reminders |
| `daybook_tools/capture.py` | Writing entries, validating them, rolling back failures |
| `daybook_tools/dates.py` | Turning phrases like "last tuesday" into exact dates |
| `ledger/`, `notes/` | Only if records are kept here. **Gitignored; normally they live outside this repo.** |
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
  correction is a reversing entry via `daybook void`.
- **Every write is validated.** `capture.commit_entry` loads the ledger after writing
  and rolls the files back if Beancount rejects it. The rollback restores their exact
  bytes (`files.Snapshot`), **never `git checkout`** -- most records folders are not git
  repositories, and a brand-new year file is untracked even in one, so the git version
  silently did nothing while the error still said "nothing was saved".
- **Backfilling works.** An entry dated before the `open` date of an account it names
  moves that date back (`store.backdate_open`), because starting a ledger by entering
  last year's history is normal. An `open` date is account setup, so this is the same
  in-place rewrite that `entity book` and `entity alias` already do, and it only ever
  moves a date earlier.
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

## Notes conventions

- **Notes never import Beancount.** `notes.py` uses stdlib plus `files.py` and
  `dates.py`, both dependency-free. Somebody who wants a diary should not have to own
  a ledger, and there is a test that fails if an import creeps in. Resolving `--who`
  against ledger entities happens in `cli.py`, not in `notes.py`, and falls back to the
  name as typed when no ledger is reachable.
- **One file per period, appended.** `notes/2026/2026-W37.md` in week mode,
  `2026-09.md` in month mode, chosen per folder in `notes.toml`. A week may straddle
  two months: **files are organised by week, indexes by each entry's own date.**
- **The Markdown is the truth; every index is derived.** Reads parse the period files,
  never the indexes, so a stale index can only make the folder look untidy, never make
  an answer wrong. `daybook note reindex` must reproduce every index byte for byte --
  there is a test.
- **Index levels differ in kind, and that is what keeps writes cheap.** The month index
  lists every entry; the year rollup is counts and totals built **from the twelve month
  indexes**, never from note files; the root index is built from the year rollups. One
  write touches four files and reads at most twelve, whatever the corpus size. A test
  asserts exactly which files a write touches -- do not let it drift.
- **Generated files are written only when the bytes change** (`files.write_if_changed`).
  On a Drive or Dropbox folder an identical rewrite still costs an upload and can still
  produce a conflicted copy.
- **Amendments append.** `note amend` writes a new entry carrying `amends: <citation>`.
  Citations must stay stable, because one may already have been quoted in an answer.
- **A topic is a tag, not a second store.** `note topic` is a query. Do not add curated
  topic pages -- duplicated prose rots.
- **Sync conflicts are reported, never merged.** `note doctor` lists them and stops.

## Testing

New behaviour needs a test that would fail without it. Interest changes need a test
that checks the result against a hand calculation written out in the test, not against
whatever the code currently returns. A contract-model change needs a test with at
least two contracts at different rates, since that is the case a single-contract test
cannot catch (see `test_two_contracts_for_the_same_borrower_project_at_their_own_rates`
in `tests/test_contracts.py`).
