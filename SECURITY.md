# Security

## What this tool is

`daybook` is a local command-line tool. It reads and writes plain-text files
(Beancount ledgers, Markdown notes, an `.ics` calendar) in a folder you choose on
your own machine. There is no server, no account, no network call the tool itself
makes, and no telemetry. Your records never leave your machine unless you put them
in a synced folder or a git remote you configured yourself.

## Threat model

The records folder is as sensitive as its contents — usually financial history and
a personal diary. Treat it like any other private data on disk:

- Keep it out of a public git repository. `daybook init --git` creates a
  **private-by-default local** repository; you still choose whether and where to
  push it, and the README's instructions use `gh repo create --private`.
- If you sync it via Drive, Dropbox or iCloud, that service's own security and
  sharing settings govern who else can see it — this tool has no say over that.
- File permissions on the records folder follow your OS defaults. If you need
  something stricter (encryption at rest, a non-default umask), set that up at
  the filesystem level; the tool does not manage it.

## Reporting a vulnerability

If you find a security issue in this tool itself (not in Beancount, Fava, or
another dependency — report those upstream), please open a private report via
GitHub's "Report a vulnerability" button on this repository's Security tab,
rather than a public issue. Include what you found and how to reproduce it. We
aim to acknowledge within a few days.

Things worth reporting: a way for one person's records to leak into another's
output, a write path that bypasses validation, a command that executes something
from records data rather than treating it as inert text, or a rollback that
leaves partial state behind. Not a vulnerability: your own records folder being
readable by other users on a shared machine — that is standard filesystem
permissions, not something this tool changes.
