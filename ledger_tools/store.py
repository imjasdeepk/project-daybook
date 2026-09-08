"""Locating, loading and writing the ledger files."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from beancount import loader
from beancount.core import data

LEDGER_DIRNAME = "ledger"
MAIN_FILENAME = "main.beancount"
ACCOUNTS_FILENAME = "accounts.beancount"
EVENTS_FILENAME = "dates.ics"

ENTITY_ROOT = "Equity:Entities"
LOANS_ROOT = "Assets:Loans"
OWED_ROOT = "Liabilities:Owed"
INTEREST_ROOT = "Income:Interest"
CASH_ROOT = "Assets:Cash"


class LedgerError(Exception):
    """A problem the user needs to see verbatim, not a traceback."""


def project_root(start: Path | None = None) -> Path:
    """Walk up from `start` until a directory containing ledger/main.beancount."""
    env = os.environ.get("LEDGER_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if (root / LEDGER_DIRNAME / MAIN_FILENAME).exists():
            return root
        raise LedgerError(
            f"LEDGER_ROOT is set to {root} but {LEDGER_DIRNAME}/{MAIN_FILENAME} is not there."
        )
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / LEDGER_DIRNAME / MAIN_FILENAME).exists():
            return candidate
    raise LedgerError(
        "No ledger found. Run 'uv run ledger init' in the project folder first."
    )


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def ledger_dir(self) -> Path:
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
    """Load the ledger. Raises LedgerError with Beancount's own message on failure."""
    p = p or paths()
    entries, errors, options_map = loader.load_file(str(p.main))
    if errors:
        rendered = "\n".join(
            f"  {cite(e.source)}: {e.message}" for e in errors[:20]
        )
        raise LedgerError(f"The ledger does not parse:\n{rendered}")
    return entries, options_map


def cite(meta: dict | None, root: Path | None = None) -> str:
    """Render 'file.beancount:123' from a Beancount meta dict."""
    if not meta or "filename" not in meta:
        return "<unknown>"
    filename = Path(str(meta["filename"]))
    try:
        base = root or project_root()
        shown = filename.resolve().relative_to(base).as_posix()
    except (ValueError, LedgerError):
        shown = filename.name
    return f"{shown}:{meta.get('lineno', '?')}"


def slugify(name: str) -> str:
    """'Harjit Singh' -> 'HarjitSingh'. Beancount account components need this shape."""
    parts = re.findall(r"[A-Za-z0-9]+", name)
    if not parts:
        raise LedgerError(f"Cannot build an account name from {name!r}.")
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


def append_block(path: Path, block: str) -> int:
    """Append a block of ledger text; return the 1-based line it starts on."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing and not existing.endswith("\n\n"):
        existing += "\n"
    start_line = existing.count("\n") + 1
    path.write_text(existing + block.rstrip("\n") + "\n", encoding="utf-8")
    return start_line


def git_commit(root: Path, message: str, files: list[Path]) -> str | None:
    """Commit the given files. Returns the short hash, or None if git is unavailable."""
    if not (root / ".git").exists():
        return None
    try:
        subprocess.run(
            ["git", "add", "--", *[str(f) for f in files]],
            cwd=root, check=True, capture_output=True, text=True,
        )
        status = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=root, check=True, capture_output=True, text=True,
        )
        if not status.stdout.strip():
            return None
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=root, check=True, capture_output=True, text=True,
        )
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, check=True, capture_output=True, text=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def revert_files(root: Path, files: list[Path]) -> None:
    """Undo uncommitted changes to the given files (used when bean-check fails)."""
    if not (root / ".git").exists():
        return
    try:
        subprocess.run(
            ["git", "checkout", "--", *[str(f) for f in files]],
            cwd=root, check=True, capture_output=True, text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def transactions(entries) -> list:
    return [e for e in entries if isinstance(e, data.Transaction)]


def opens(entries) -> list:
    return [e for e in entries if isinstance(e, data.Open)]


def today() -> date:
    return date.today()
