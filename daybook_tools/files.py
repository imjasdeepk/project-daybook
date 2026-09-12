"""Filesystem and git plumbing, with no Beancount in it.

This module is deliberately dependency-free: `notes.py` builds on it, and notes
must work in a folder that has no ledger in it at all. `store.py` re-exports
everything here, so the rest of the package can keep importing from `store`.

If you add something to this file, keep it stdlib-only.
"""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path


class DaybookError(Exception):
    """A problem the user needs to see verbatim, not a traceback."""


def append_block(path: Path, block: str) -> int:
    """Append a block of text; return the 1-based line it starts on.

    That line number is the citation, which is why nothing here ever rewrites
    what came before: an existing citation must keep pointing at what it named.
    """
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing and not existing.endswith("\n\n"):
        existing += "\n"
    start_line = existing.count("\n") + 1
    path.write_text(existing + block.rstrip("\n") + "\n", encoding="utf-8")
    return start_line


def write_if_changed(path: Path, text: str) -> bool:
    """Write only when the bytes differ. Returns whether anything was written.

    Generated files are rewritten constantly. On a folder synced by Dropbox,
    Drive or Syncthing an unchanged rewrite still costs an upload and can still
    produce a conflicted copy, so not writing is the point.
    """
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def git_repo_for(path: Path) -> Path | None:
    """The nearest enclosing git repository, or None. Lets your private records
    keep their own history separate from the public code repository."""
    here = path.resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def git_commit(root: Path, message: str, files: list[Path]) -> str | None:
    """Commit the given files into whichever repository actually holds them.

    Returns None when there is no repository, which is the ordinary case for
    records kept in a Drive or Dropbox folder. That is not an error.
    """
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


def today() -> date:
    return date.today()
