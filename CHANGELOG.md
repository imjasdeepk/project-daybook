# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-09-16

First public release.

### Added
- The `daybook` command line tool: a ledger (Beancount-backed loans, IOUs,
  interest projections, purchases, birthdays and reminders) and a diary/knowledge
  base (plain Markdown, indexed, never edited in place).
- `.claude/skills/ledger/` and `.claude/skills/notes/` — the Claude Code
  behavioural contract for each half.
- `install.sh` / `install.ps1` — a one-command installer for macOS, Linux and
  Windows, asking whether you want the diary, the ledger, or both, and putting
  `daybook` on your `PATH` via `uv tool install`.
- `daybook --version`.

### Fixed
- A `"` in a narration, payee or note body no longer breaks the write it's part
  of (a straight quote is now rendered as `'`, and a note body that happens to
  contain another entry's heading line no longer splits into two notes).
- A failed git commit (e.g. no `user.email` configured) is now reported as
  `commit: {"status": "failed", ...}` instead of silently looking identical to
  "there was no repository to commit to."
- `daybook init` / `daybook note init` refuse a target folder inside the tool's
  own checkout, so records can never land in the public repository.
- Backdating an entry now works even when it involves a cash account
  (`Assets:Cash:<owner>:<currency>`) that a *later* entry already used: those
  accounts have no explicit `open` line of their own, so a genuinely older
  entry (an old receipt found after the fact, say) used to be refused with
  "Could not find the `open` directive."

### Added since
- `scripts/e2e_test.py` — a real end-to-end suite: installs through the actual
  installer, drives the actually-installed binary through capture and query
  flows on both halves, confirms a reinstall never loses data, and uninstalls
  cleanly. Runs in CI on every push and PR.
