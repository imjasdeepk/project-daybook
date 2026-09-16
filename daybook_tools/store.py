"""Locating, loading and writing the ledger files."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from beancount import loader
from beancount.core import data

# The filesystem and git plumbing lives in files.py, which has no Beancount in
# it so that notes.py can build on the same primitives. Re-exported here so
# every existing `from .store import ...` keeps working.
from .files import (  # noqa: F401
    DaybookError,
    Snapshot,
    append_block,
    commit_field,
    git_commit,
    git_repo_for,
    git_sync,
    revert_files,
    today,
    write_if_changed,
)

LEDGER_DIRNAME = "ledger"
MAIN_FILENAME = "main.beancount"
ACCOUNTS_FILENAME = "accounts.beancount"
EVENTS_FILENAME = "dates.ics"

ENTITY_ROOT = "Equity:Entities"
LOANS_ROOT = "Assets:Loans"
OWED_ROOT = "Liabilities:Owed"
INTEREST_ROOT = "Income:Interest"
EXPENSE_INTEREST_ROOT = "Expenses:Interest"
CASH_ROOT = "Assets:Cash"


LOCATION_FILENAME = ".daybook-root"
ROOT_ENV_VARS = ("DAYBOOK_ROOT",)


def _validated(root: Path, source: str) -> Path:
    """Accept a folder holding the ledger files, either directly or under ledger/."""
    if (root / LEDGER_DIRNAME / MAIN_FILENAME).exists() or (root / MAIN_FILENAME).exists():
        return root
    raise DaybookError(
        f"{source} points at {root}, but no {MAIN_FILENAME} was found there "
        f"or in {root / LEDGER_DIRNAME}."
    )


def _from_location_file(start: Path) -> Path | None:
    """A `.daybook-root` file naming where your records live.

    This is how the tool finds records kept outside the code folder without
    depending on an environment variable, which not every session inherits.
    """
    for candidate in [start, *start.parents]:
        pointer = candidate / LOCATION_FILENAME
        if pointer.exists():
            raw = pointer.read_text(encoding="utf-8").strip()
            if raw:
                return _validated(Path(raw).expanduser().resolve(), str(pointer))
    return None


def project_root(start: Path | None = None) -> Path:
    """Find the records: DAYBOOK_ROOT, then a .daybook-root file, then this folder."""
    for var in ROOT_ENV_VARS:
        env = os.environ.get(var)
        if not env:
            continue
        root = Path(env).expanduser().resolve()
        if (root / LEDGER_DIRNAME / MAIN_FILENAME).exists():
            return root
        # Also accept a folder that holds the ledger files directly, so your
        # records can live in their own private repository.
        if (root / MAIN_FILENAME).exists():
            return root
        raise DaybookError(
            f"{var} is set to {root} but no {MAIN_FILENAME} was found there "
            f"or in {root / LEDGER_DIRNAME}."
        )
    here = (start or Path.cwd()).resolve()
    pointed = _from_location_file(here)
    if pointed is not None:
        return pointed
    for candidate in [here, *here.parents]:
        if (candidate / LEDGER_DIRNAME / MAIN_FILENAME).exists():
            return candidate
    raise DaybookError(
        "No ledger found. Run 'uv run daybook init' to create one, or put the path to "
        f"your records in a {LOCATION_FILENAME} file."
    )


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def ledger_dir(self) -> Path:
        """Where the ledger files live: usually root/ledger, or root itself when
        your records are kept in their own private folder."""
        if (self.root / MAIN_FILENAME).exists() and not (self.root / LEDGER_DIRNAME / MAIN_FILENAME).exists():
            return self.root
        return self.root / LEDGER_DIRNAME

    @property
    def main(self) -> Path:
        return self.ledger_dir / MAIN_FILENAME

    @property
    def accounts(self) -> Path:
        return self.ledger_dir / ACCOUNTS_FILENAME

    @property
    def events(self) -> Path:
        return self.ledger_dir / EVENTS_FILENAME

    def year_file(self, year: int) -> Path:
        return self.ledger_dir / f"{year}.beancount"


def paths(start: Path | None = None) -> Paths:
    return Paths(project_root(start))


def load(p: Paths | None = None):
    """Load the ledger. Raises DaybookError with Beancount's own message on failure."""
    p = p or paths()
    entries, errors, options_map = loader.load_file(str(p.main))
    if errors:
        rendered = "\n".join(
            f"  {cite(e.source)}: {e.message}" for e in errors[:20]
        )
        raise DaybookError(f"The ledger does not parse:\n{rendered}")
    return entries, options_map


def cite(meta: dict | None, root: Path | None = None) -> str:
    """Render 'file.beancount:123' from a Beancount meta dict."""
    if not meta or "filename" not in meta:
        return "<unknown>"
    filename = Path(str(meta["filename"]))
    try:
        base = root or project_root()
        shown = filename.resolve().relative_to(base).as_posix()
    except (ValueError, DaybookError):
        shown = filename.name
    return f"{shown}:{meta.get('lineno', '?')}"


def slugify(name: str) -> str:
    """'Robert Diaz' -> 'RobertDiaz'. Beancount account components need this shape."""
    parts = re.findall(r"[A-Za-z0-9]+", name)
    if not parts:
        raise DaybookError(f"Cannot build an account name from {name!r}.")
    slug = "".join(part[:1].upper() + part[1:] for part in parts)
    if not slug[0].isupper() and not slug[0].isdigit():
        slug = slug[:1].upper() + slug[1:]
    return slug


def ensure_year_file(p: Paths, year: int) -> Path:
    """Create ledger/<year>.beancount and include it from main if it is new."""
    target = p.year_file(year)
    if not target.exists():
        target.write_text(f"; Transactions for {year}.\n", encoding="utf-8")
    main_text = p.main.read_text(encoding="utf-8")
    include = f'include "{target.name}"'
    if include not in main_text:
        if not main_text.endswith("\n"):
            main_text += "\n"
        p.main.write_text(main_text + include + "\n", encoding="utf-8")
    return target


def backdate_open(p: Paths, account: str, to: date) -> dict:
    """Move an account's `open` date back so an earlier entry is valid.

    Backfilling is how a ledger normally gets started, and an `open` date is
    account setup rather than history: the same reasoning that lets
    `entity book` and `entity alias` rewrite a line in place. Nothing is lost
    -- the account simply existed earlier than we first wrote it down. Only
    ever moves the date backwards.

    Cash accounts (`Assets:Cash:<owner>:<currency>`) never get an explicit
    `open` line -- they rely entirely on the `auto_accounts` plugin, opened
    at whichever date first used them. When a later capture session records
    something *older* than that first use, there is no literal line here to
    move back, only a date `auto_accounts` invented. Writing one now, at the
    earlier date, replaces that invented date with the true one -- the same
    "the account simply existed earlier" reasoning as the ordinary case,
    just for an account nothing had written down at all yet.
    """
    text = p.accounts.read_text(encoding="utf-8")
    lines = text.splitlines()
    anchor = f"open {account}"
    index = next((i for i, line in enumerate(lines) if line.strip().endswith(anchor)), None)
    if index is None:
        append_block(p.accounts, f"{to.isoformat()} open {account}")
        return {"account": account, "from": None, "to": to.isoformat(), "changed": True}
    was, _, rest = lines[index].partition(" ")
    if date.fromisoformat(was) <= to:
        return {"account": account, "changed": False}
    lines[index] = f"{to.isoformat()} {rest}"
    p.accounts.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"account": account, "from": was, "to": to.isoformat(), "changed": True}


def transactions(entries) -> list:
    return [e for e in entries if isinstance(e, data.Transaction)]


def opens(entries) -> list:
    return [e for e in entries if isinstance(e, data.Open)]


