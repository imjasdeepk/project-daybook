# Contributing

## Setup

```bash
uv sync --extra dev
uv run pytest
```

If you moved or renamed your checkout, `uv sync` alone will not repair `.venv` —
run `uv sync --extra dev --reinstall`, or delete `.venv` and sync again.

## The rules that shape everything here

These are load-bearing, not style preferences — a change that violates one will be
asked to change, whatever else it does right. `CLAUDE.md` has the full detail;
the short version:

- **Never calculate, never recall.** Every figure in an answer comes from a
  `daybook` command run that turn, cited `file:line`. A past note is quoted from
  a command, never paraphrased from memory. This is the entire premise of the
  project — code that makes an agent's job "trust this instead" is a regression,
  however convenient.
- **Money is `Decimal`, always**, and currencies are never added together. There
  is no conversion anywhere in the codebase, and none gets added without the
  maintainer asking for it explicitly.
- **Appends only.** `store.append_block` and `notes.append_block` add to a file;
  nothing rewrites history. A correction is a reversing entry (`daybook void`) or
  an amendment (`daybook note amend`) — never an edit in place. A citation, once
  given, must keep pointing at what it named.
- **Every write is validated**, and a failure really does leave nothing behind
  (`capture.commit_entry`'s `Snapshot`, byte-exact, never `git checkout`).
- **Ambiguity stops the machine.** `entities.resolve` and `dates.parse` return
  `ambiguous` rather than guessing. Do not add a "best guess" fallback anywhere.
- **`notes.py` never imports Beancount.** A diary should not require owning a
  ledger. There is a test (`test_notes_work_with_no_ledger_and_never_import_beancount`)
  that fails if an import creeps in — keep it passing.
- **Not every debt is a loan.** An IOU (no `rate_percent_pa`) is not a 0% loan;
  the two must stay distinguishable everywhere balances and interest are computed.

## Tests

New behaviour needs a test that would fail without it. A few standards this
codebase holds itself to, worth matching:

- **Interest changes** need a test that checks the result against a hand
  calculation written out in the test, not against whatever the code currently
  returns.
- **A contract-model change** needs a test with at least two contracts at
  different rates — a single-contract test cannot catch a blended-rate bug (see
  `test_two_contracts_for_the_same_borrower_project_at_their_own_rates`).
- Tests must pass on macOS, Windows and Linux (CI runs all three) — use
  `pathlib`, never shell pipes or Unix-only tools.
- Write CLI-level tests for behaviour a user would see (`main([...])`), and
  library-level tests for internals, matching the split already in
  `tests/test_ledger.py` and `tests/test_notes.py`.

## Before opening a PR

```bash
uv run pytest -q
uv run ruff check .
bash -n install.sh   # if you touched it
```

Describe what changed and why, not just what. If you're touching `capture.py`,
`notes.py`, `store.py` or `files.py`, say which of the rules above the change
keeps or touches — that's what a reviewer will be checking first.
