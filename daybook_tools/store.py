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
EXPENSE_INTEREST_ROOT = "Expenses:Interest"
CASH_ROOT = "Assets:Cash"


class DaybookError(Exception):
    """A problem the user needs to see verbatim, not a traceback."""


# The project was called project-ledger before prose records joined the money
# ones. The old pointer file and environment variable are still read, for ever:
# they name where somebody's real records live, and breaking that would orphan
# every existing install. New folders get the daybook spelling.
LOCATION_FILENAME = ".daybook-root"
LEGACY_LOCATION_FILENAME = ".ledger-root"
ROOT_ENV_VARS = ("DAYBOOK_ROOT", "LEDGER_ROOT")


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
    The older `.ledger-root` name is honoured at every level of the walk, so a
    folder set up before the rename keeps working untouched.
    """
    for candidate in [start, *start.parents]:
        for name in (LOCATION_FILENAME, LEGACY_LOCATION_FILENAME):
            pointer = candidate / name
            if pointer.exists():
                raw = pointer.read_text(encoding="utf-8").strip()
                if raw:
                    return _validated(Path(raw).expanduser().resolve(), str(pointer))
    return None


def project_root(start: Path | None = None) -> Path:
    """Find the records: DAYBOOK_ROOT, then a .daybook-root file, then this folder.

    LEDGER_ROOT and .ledger-root are read too, so existing installs keep working.
    """
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
    """'Harjit Singh' -> 'HarjitSingh'. Beancount account components need this shape."""
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


def git_repo_for(path: Path) -> Path | None:
    """The nearest enclosing git repository, or None. Lets your private ledger
    keep its own history separate from the public code repository."""
    here = path.resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def git_commit(root: Path, message: str, files: list[Path]) -> str | None:
    """Commit the given files into whichever repository actually holds them."""
    repo = git_repo_for(files[0].parent) if files else git_repo_for(root)
    if repo is None:
        return None
    root = repo
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
    """Undo uncommitted changes to the given files (used when validation fails)."""
    repo = git_repo_for(files[0].parent) if files else git_repo_for(root)
    if repo is None:
        return
    root = repo
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


def git_sync(path: Path) -> dict:
    """Pull then push the repository holding your records, so the copy on your
    phone and the copy on your laptop agree. Never touches the code repository."""
    repo = git_repo_for(path)
    if repo is None:
        return {"status": "no_repository",
                "detail": f"{path} is not in a git repository, so there is nothing to sync."}
    try:
        remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo,
                                check=True, capture_output=True, text=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"status": "no_remote", "repository": str(repo),
                "detail": "No 'origin' remote. Add one to keep an off-machine copy."}
    steps = []
    for label, args in (("pull", ["pull", "--rebase", "--autostash"]), ("push", ["push"])):
        result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
        steps.append({"step": label, "ok": result.returncode == 0,
                      "output": (result.stderr or result.stdout).strip()[:400]})
        if result.returncode != 0 and label == "pull":
            return {"status": "conflict", "repository": str(repo), "remote": remote,
                    "steps": steps,
                    "detail": "Pull failed. Resolve it by hand before syncing again."}
    return {"status": "synced" if all(s["ok"] for s in steps) else "failed",
            "repository": str(repo), "remote": remote, "steps": steps}
