"""Structural tests over the CLI surface itself, not any one command's
behaviour: every subcommand dispatches cleanly, and a bare `daybook` or
`daybook note` fails with usage rather than a traceback. These walk
`build_parser()` so a new command that is wired up wrong cannot slip in
unnoticed.
"""
from __future__ import annotations

import argparse
import json

import pytest

from daybook_tools.cli import build_parser, main


def _leaf_commands() -> list[tuple[str, ...]]:
    """Every full command path with no further subcommand under it, e.g.
    `("entity", "add")` or `("note", "find")`."""
    def walk(parser: argparse.ArgumentParser, prefix: tuple[str, ...]):
        subactions = [a for a in parser._actions
                     if isinstance(a, argparse._SubParsersAction)]
        if not subactions:
            yield prefix
            return
        for action in subactions:
            for name, sub in action.choices.items():
                yield from walk(sub, prefix + (name,))

    return list(walk(build_parser(), ()))


LEAF_COMMANDS = _leaf_commands()


def test_every_leaf_command_is_reachable():
    """A canary for the enumeration itself: if `build_parser()` changes shape
    in a way that breaks the walk, this is where it shows up."""
    assert ("add",) in LEAF_COMMANDS
    assert ("note", "find") in LEAF_COMMANDS
    assert ("entity", "add") in LEAF_COMMANDS
    assert len(LEAF_COMMANDS) >= 35


@pytest.mark.parametrize("command", LEAF_COMMANDS, ids=lambda c: " ".join(c))
def test_every_command_dispatches_without_a_bare_exception(command, tmp_path, monkeypatch):
    """Run every registered command with `--json` and no other arguments, in
    a folder with nothing set up. Almost all of them have nothing to work
    with there, so the expected outcomes are: a clean `DaybookError` (`main`
    returns 1), an argparse usage error for a missing required argument
    (`SystemExit`), or -- for the handful of commands that need nothing --
    a normal success (`main` returns 0). What must never happen is any other
    exception escaping: that would mean some command trusts an argument it
    was never actually given.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DAYBOOK_ROOT", raising=False)
    monkeypatch.delenv("DAYBOOK_NOTES_ROOT", raising=False)
    try:
        code = main(["--json", *command])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        assert code in (0, 1)


@pytest.mark.parametrize("command", LEAF_COMMANDS, ids=lambda c: " ".join(c))
def test_a_json_error_is_valid_json(command, tmp_path, monkeypatch, capsys):
    """When a command fails with `DaybookError` under `--json`, the output on
    stdout must be a JSON object with a `status`/`error` pair -- an agent
    parsing it should never have to guess."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DAYBOOK_ROOT", raising=False)
    monkeypatch.delenv("DAYBOOK_NOTES_ROOT", raising=False)
    try:
        code = main(["--json", *command])
    except SystemExit:
        return  # an argparse usage error, not a DaybookError -- out of scope here
    out = capsys.readouterr().out
    if code == 1:
        payload = json.loads(out)
        assert payload["status"] == "error"
        assert payload["error"]
    else:
        json.loads(out)  # success output must also be valid JSON


def test_bare_daybook_fails_with_usage_not_a_traceback(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code != 0
    assert "usage:" in capsys.readouterr().err


def test_bare_note_fails_with_usage_not_a_traceback(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["note"])
    assert excinfo.value.code != 0
    assert "usage:" in capsys.readouterr().err
