"""A throwaway ledger for each test, built through the real CLI."""
from __future__ import annotations

import pytest

from daybook_tools.cli import main
from daybook_tools.store import Paths


@pytest.fixture(autouse=True)
def _no_ambient_roots(monkeypatch):
    """Forget any root the developer has exported.

    DAYBOOK_ROOT is a documented way to use the tool, so it is often set in a
    real shell -- and a test that expects no ledger to be findable would then
    find the developer's own."""
    for name in ("DAYBOOK_ROOT", "LEDGER_ROOT", "NOTES_ROOT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def ledger_root(tmp_path, monkeypatch):
    """An initialised ledger in a temp folder, with DAYBOOK_ROOT pointing at it."""
    monkeypatch.setenv("DAYBOOK_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert main(["init", str(tmp_path), "--currencies", "INR,USD", "--no-remember"]) == 0
    return tmp_path


@pytest.fixture
def paths(ledger_root):
    return Paths(ledger_root)


@pytest.fixture
def run():
    """Run a CLI command, asserting the exit code."""
    def _run(*args, expect: int = 0):
        code = main([*args])
        assert code == expect, f"expected exit {expect} from {args}, got {code}"
        return code
    return _run
