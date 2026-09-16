#!/usr/bin/env python3
"""End-to-end regression suite: a clean install, a battery of capture and
query flows with real value assertions (not just exit codes), and a clean
uninstall -- run entirely inside temp directories, fully isolated from the
machine running it.

    uv run python scripts/e2e_test.py
    python3 scripts/e2e_test.py           # once `daybook` itself is on PATH

Nothing here touches your real ~/.daybook-root, ~/.claude/skills or
~/.gitconfig: HOME (and, on Windows, USERPROFILE) is pointed at a fresh temp
directory for every subprocess. Exits non-zero on the first failing
assertion, printing the command and its output.

This is the outer loop: it exercises the actual installers and the actual
installed binary, the way a new user would encounter them. `uv run pytest`
is the inner loop that covers the logic in detail; this script exists so a
change cannot break the install-and-first-session path without the test
suite noticing, the way `install.sh` breaking bare `daybook` did before this
suite existed.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import textwrap
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = platform.system() == "Windows"

PASSED = 0
FAILURES: list[str] = []


# --------------------------------------------------------------- reporting

def step(title: str) -> None:
    print(f"\n\033[1;32m==>\033[0m \033[1m{title}\033[0m")


def ok(desc: str) -> None:
    global PASSED
    PASSED += 1
    print(f"  \033[32mok\033[0m   {desc}")


def fail(desc: str, detail: str = "") -> None:
    FAILURES.append(desc)
    print(f"  \033[31mFAIL\033[0m {desc}")
    if detail:
        print(textwrap.indent(detail.strip(), "       "))


def require(condition: bool, desc: str, detail: str = "") -> None:
    """Record the assertion, but keep going -- one failure should not hide
    the next ten, since that is exactly the information a regression needs."""
    if condition:
        ok(desc)
    else:
        fail(desc, detail)


# ------------------------------------------------------------- environment

class Home:
    """One isolated fake-HOME sandbox: its own git identity, its own
    ~/.claude/skills, its own uv tool shims. Torn down on exit."""

    def __init__(self, label: str):
        self.label = label
        self.root = Path(tempfile.mkdtemp(prefix=f"daybook-e2e-{label}-"))
        self.home = self.root / "home"
        self.home.mkdir()
        self.env_extra = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "DAYBOOK_NONINTERACTIVE": "1",
        }
        self._git_identity()

    def _git_identity(self) -> None:
        run_ok(["git", "config", "--global", "user.email", "e2e@example.com"], env=self.env)
        run_ok(["git", "config", "--global", "user.name", "E2E"], env=self.env)

    @property
    def env(self) -> dict:
        env = dict(os.environ)
        env.update(self.env_extra)
        return env

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def run(args: list[str], *, cwd: Path | None = None, env: dict | None = None,
       check: bool = False, no_stdin: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=str(cwd) if cwd else None, env=env,
        capture_output=True, text=True, check=check,
        stdin=subprocess.DEVNULL if no_stdin else None,
    )


def run_ok(args: list[str], *, cwd: Path | None = None, env: dict | None = None) -> str:
    result = run(args, cwd=cwd, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result.stdout


# ---------------------------------------------------------------- install

def install(home: Home, *, install_dir: Path, records_dir: Path, keep: str,
           backup: str = "git", currencies: str = "USD") -> None:
    env = home.env
    env.update({
        "DAYBOOK_REPO": str(REPO_ROOT),
        "DAYBOOK_INSTALL_DIR": str(install_dir),
        "DAYBOOK_DIR": str(records_dir),
        "DAYBOOK_KEEP": keep,
        "DAYBOOK_BACKUP": backup,
        "DAYBOOK_CURRENCIES": currencies,
        "DAYBOOK_GLOBAL_SKILL": "no",
    })
    if IS_WINDOWS:
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell is None:
            raise RuntimeError("neither pwsh nor powershell found on PATH")
        result = run([shell, "-NoProfile", "-File", str(REPO_ROOT / "install.ps1")], env=env,
                     no_stdin=True)
    else:
        result = run(["bash", str(REPO_ROOT / "install.sh")], env=env,
                     cwd=REPO_ROOT, no_stdin=True)
    require(result.returncode == 0, f"install.sh/.ps1 exits 0 (keep={keep}, backup={backup})",
           f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}" if result.returncode != 0 else "")
    if result.returncode != 0:
        raise RuntimeError("install failed; see above")


def resolved_binary(home: Home) -> Path:
    """The real path `uv tool install` put the entry point at -- used
    directly rather than trusting the current process's PATH, matching what
    the installer itself does internally."""
    bin_dir = Path(run_ok(["uv", "tool", "dir", "--bin"], env=home.env).strip())
    exe = bin_dir / ("daybook.exe" if IS_WINDOWS else "daybook")
    return exe


# ------------------------------------------------------------------ daybook

class Daybook:
    """Runs the installed binary with --json, in a given records folder,
    parsing the result. Every call is a real subprocess -- this is not
    calling into the Python package directly."""

    def __init__(self, binary: Path, cwd: Path, env: dict):
        self.binary = binary
        self.cwd = cwd
        self.env = env

    def __call__(self, *args: str) -> tuple[int, dict]:
        result = run([str(self.binary), "--json", *args], cwd=self.cwd, env=self.env)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {"__raw_stdout": result.stdout, "__raw_stderr": result.stderr}
        return result.returncode, payload


# --------------------------------------------------------------- scenarios

def capture_and_query_flows(db: Daybook) -> None:
    """The battery: the same shape of session a real install would see in
    its first hour, with the trickier corners this review found deliberately
    included (a quote in a note, a duplicate, an IOU, a void)."""

    step("Entities")
    code, out = db("entity", "add", "--name", "Alex Rivera", "--aliases", "me",
                   "--currency", "USD", "--book", "--self")
    require(code == 0 and out.get("created", {}).get("slug") == "AlexRivera",
           "self entity created", json.dumps(out))

    code, out = db("entity", "add", "--name", "Robert Diaz", "--relation", "father",
                   "--aliases", "dad, papa", "--currency", "USD")
    require(code == 0, "counterparty entity created", json.dumps(out))

    code, out = db("resolve", "dad")
    require(code == 0 and out.get("status") == "resolved"
           and out.get("entity", {}).get("name") == "Robert Diaz",
           "'dad' resolves to Robert Diaz", json.dumps(out))

    code, out = db("resolve", "someone nobody has heard of")
    require(code == 0 and out.get("status") == "unknown",
           "an unknown name comes back unknown, not invented", json.dumps(out))

    step("Contract and a quoted capture (regression: quotes must not break the write)")
    code, out = db("contract", "add", "--lender", "me", "--borrower", "dad",
                   "--rate", "8", "--method", "simple", "--started", "2026-01-01")
    require(code == 0, "contract opened at 8% simple", json.dumps(out))

    code, out = db("add", "--kind", "lend", "--who", "dad", "--amount", "5000",
                   "--date", "2026-01-01", "--note", 'the "new" car',
                   "--source", 'he said "just this once"')
    first_citation = out.get("citation", "")
    require(code == 0 and out.get("status") == "recorded" and first_citation,
           "lend with a quoted note records cleanly", json.dumps(out))

    step("Duplicate detection")
    code, out = db("add", "--kind", "lend", "--who", "dad", "--amount", "5000",
                   "--date", "2026-01-01", "--note", 'the "new" car',
                   "--source", 'he said "just this once"')
    require(code == 0 and out.get("status") == "possible_duplicate"
           and out.get("nothing_was_written") is True,
           "an identical entry is flagged, not silently recorded twice", json.dumps(out))

    code, out = db("add", "--kind", "lend", "--who", "dad", "--amount", "5000",
                   "--date", "2026-01-01", "--note", "genuinely separate", "--force")
    require(code == 0 and out.get("status") == "recorded",
           "--force records the genuinely-separate second entry", json.dumps(out))

    step("Repayment and interest")
    code, out = db("add", "--kind", "repay", "--who", "dad", "--amount", "2000",
                   "--date", "2026-02-01", "--note", "part repayment")
    require(code == 0, "repayment recorded", json.dumps(out))

    code, out = db("add", "--kind", "interest", "--who", "dad", "--amount", "100",
                   "--date", "2026-03-01", "--note", "interest paid")
    require(code == 0, "interest payment recorded", json.dumps(out))

    step("Balance and statement")
    code, out = db("balance", "dad")
    require(code == 0 and out.get("payable") == {"USD": "8000"},
           "balance is 5000 + 5000 - 2000 = 8000, exactly, not estimated",
           json.dumps(out))
    require(out.get("interest_paid") == {"USD": "100"},
           "interest actually paid shows up, separately from principal", json.dumps(out))
    require(out.get("counts", {}).get("borrowed") == 2
           and out.get("counts", {}).get("returned") == 1,
           "counts match: 2 lends, 1 repayment", json.dumps(out))

    code, out = db("statement", "dad")
    rows = out.get("rows", [])
    require(code == 0 and len(rows) == 4
           and all(":" in r.get("citation", "") for r in rows),
           "statement has one row per entry, each citing file:line", json.dumps(out))

    step("Void reverses without editing history")
    code, out = db("void", "--voids", first_citation, "--kind", "lend", "--who", "dad",
                   "--amount", "5000", "--date", "2026-01-01", "--note", 'the "new" car',
                   "--source", 'he said "just this once"')
    require(code == 0 and out.get("status") == "reversed",
           "void records a reversing entry", json.dumps(out))

    code, out = db("balance", "dad")
    require(out.get("payable") == {"USD": "3000"},
           "balance reflects the void: 8000 - 5000 = 3000", json.dumps(out))

    step("An IOU opens itself, and interest is skipped until it has terms")
    code, out = db("entity", "add", "--name", "Jordan", "--aliases", "jordan",
                   "--currency", "USD")
    require(code == 0, "IOU counterparty entity created", json.dumps(out))

    code, out = db("add", "--kind", "lend", "--who", "jordan", "--amount", "306",
                   "--date", "2025-07-01", "--note", "trip")
    require(code == 0 and out.get("contract_kind") == "iou",
           "a debt with no contract on file opens as an IOU, not a 0% loan",
           json.dumps(out))
    iou_contract = out.get("contract", "")
    require(iou_contract.endswith("-iou"), "the IOU's contract id says so", iou_contract)

    code, out = db("projection", "jordan", "--as-of", "2026-06-01")
    skipped = out.get("skipped", [])
    require(code == 0 and len(skipped) == 1 and skipped[0].get("kind") == "iou",
           "projecting an IOU lists it under skipped, never as a 0% loan", json.dumps(out))

    code, out = db("contract", "terms", iou_contract, "--rate", "12", "--method", "simple")
    require(code == 0, "the same IOU is given terms, not replaced", json.dumps(out))

    code, out = db("projection", "jordan", "--as-of", "2026-06-01")
    by_currency = out.get("by_currency", {})
    require(code == 0 and "USD" in by_currency and not out.get("skipped"),
           "now it projects, and is no longer skipped", json.dumps(out))
    require(Decimal(by_currency.get("USD", {}).get("projected_interest", "-1")) > 0,
           "the projection is a positive number, not a placeholder", json.dumps(out))

    step("Portfolio and search")
    code, out = db("portfolio")
    require(code == 0 and "totals" in out, "portfolio aggregates across contracts",
           json.dumps(out))

    code, out = db("search", "car")
    require(code == 0 and out.get("matches", 0) >= 1,
           "search finds the quoted note by substring", json.dumps(out))

    step("Events and upcoming")
    code, out = db("event", "add", "--summary", "Robert's birthday", "--date", "14 March 1962")
    require(code == 0, "birthday recorded to dates.ics", json.dumps(out))

    code, out = db("upcoming", "--days", "365")
    events = out.get("events", [])
    require(code == 0 and any("Robert's birthday" in e.get("summary", "") for e in events),
           "a yearly birthday is always within 365 days of any date", json.dumps(out))

    step("check: the ledger is still valid after everything above")
    code, out = db("check")
    require(code == 0 and out.get("status") == "valid",
           "daybook check reports valid after the whole session", json.dumps(out))


def notes_flows(db: Daybook) -> None:
    step("Notes: capture (regression: a body that looks like a heading)")
    code, out = db("note", "add", "--title", "Lunch with dad", "--kind", "log",
                   "--tags", "family", "--who", "Robert Diaz",
                   "--body", 'He said "maybe next month" about the car.\n'
                             "## 2099-01-01 00:00  Not a real second entry\n"
                             "Still the same note.",
                   "--source", 'lunch with dad, he said "maybe next month"')
    citation = out.get("citation", "")
    require(code == 0 and citation, "note recorded", json.dumps(out))

    code, out = db("note", "find", "")
    require(code == 0 and out.get("matches") == 1,
           "a body containing a forged heading still counts as one note, not two",
           json.dumps(out))

    code, out = db("note", "find", "maybe next month")
    require(code == 0 and out.get("matches") == 1,
           "the real note is still found by its actual content", json.dumps(out))

    step("Notes: topic, week, agenda")
    code, out = db("note", "topic", "family")
    require(code == 0 and out.get("matches", 0) >= 1,
           "topic query finds the tagged note", json.dumps(out))

    code, out = db("note", "week")
    require(code == 0, "week view returns", json.dumps(out))

    code, out = db("note", "add", "--title", "Trip planning", "--kind", "travel",
                   "--tags", "travel", "--when", "2026-10-03..2026-10-09",
                   "--body", "Flights booked.")
    require(code == 0, "a dated note is recorded", json.dumps(out))

    code, out = db("note", "agenda", "--days", "3650")
    agenda = out.get("entries", [])
    require(code == 0 and len(agenda) >= 1,
           "agenda finds the dated note within a wide window", json.dumps(out))

    step("Notes: amend never edits the original")
    code, out = db("note", "amend", citation, "--body", "He actually said next week.")
    require(code == 0, "amendment recorded", json.dumps(out))

    code, out = db("note", "show", citation)
    require(code == 0 and "maybe next month" in out.get("body", ""),
           "the original note is untouched by the amendment", json.dumps(out))

    step("Notes: reindex and doctor")
    code, out = db("note", "reindex")
    require(code == 0, "reindex rebuilds cleanly", json.dumps(out))

    code, out = db("note", "doctor")
    require(code == 0 and not out.get("sync_conflicts")
           and not out.get("stale_indexes"),
           "doctor reports a clean folder", json.dumps(out))


# ---------------------------------------------------------------- passes

def full_pass() -> None:
    """Both halves, --git backup: the harder path, and the one that exercises
    the commit-status fix end to end."""
    home = Home("full")
    try:
        install_dir = home.root / "tool"
        records_dir = home.root / "records"
        step("Installing (keep=both, backup=git)")
        install(home, install_dir=install_dir, records_dir=records_dir,
               keep="both", backup="git", currencies="USD")

        binary = resolved_binary(home)
        require(binary.exists(), f"the installed binary exists at {binary}")
        if not binary.exists():
            raise RuntimeError("no installed binary; cannot continue this pass")

        version = run([str(binary), "--version"], env=home.env)
        require(version.returncode == 0 and version.stdout.strip().startswith("daybook "),
               "daybook --version works from the resolved install path",
               version.stdout + version.stderr)

        db = Daybook(binary, cwd=install_dir, env=home.env)
        capture_and_query_flows(db)
        notes_flows(db)

        step("Records are a git repository with real commits")
        log = run(["git", "log", "--oneline"], cwd=records_dir, env=home.env)
        commit_count = len(log.stdout.strip().splitlines())
        require(log.returncode == 0 and commit_count > 1,
               f"records repo has {commit_count} commits, not just the initial one",
               log.stdout + log.stderr)

        step("Uninstall")
        pre_files = sorted(p.relative_to(records_dir) for p in records_dir.rglob("*")
                           if p.is_file())
        uninstall = run(["uv", "tool", "uninstall", "project-daybook"], env=home.env)
        require(uninstall.returncode == 0, "uv tool uninstall exits 0",
               uninstall.stdout + uninstall.stderr)
        require(not binary.exists(), "the installed binary is gone after uninstall")

        post_files = sorted(p.relative_to(records_dir) for p in records_dir.rglob("*")
                            if p.is_file())
        require(pre_files == post_files,
               "uninstalling the tool did not touch a single record file",
               f"before: {pre_files}\nafter:  {post_files}")
        accounts_file = records_dir / "accounts.beancount"
        require(accounts_file.exists() and "AlexRivera" in accounts_file.read_text(),
               "the ledger's own content survives uninstall, unreachable but intact")
    finally:
        home.cleanup()


def diary_only_pass() -> None:
    """The headline open-source case: no ledger question, no Beancount file
    ever created, notes still fully functional, uninstall still leaves
    everything behind."""
    home = Home("diary")
    try:
        install_dir = home.root / "tool"
        records_dir = home.root / "records"
        step("Installing (keep=diary, backup=synced)")
        install(home, install_dir=install_dir, records_dir=records_dir,
               keep="diary", backup="synced")

        require(not (records_dir / "main.beancount").exists(),
               "diary-only install never created a ledger")

        binary = resolved_binary(home)
        if not binary.exists():
            fail("the installed binary exists after a diary-only install",
                f"expected {binary}")
            return
        db = Daybook(binary, cwd=install_dir, env=home.env)

        code, out = db("note", "add", "--title", "First entry", "--body",
                       'Nothing here needs a ledger, said "out loud".')
        require(code == 0, "a note is recorded with no ledger present", json.dumps(out))

        code, out = db("note", "find", "out loud")
        require(code == 0 and out.get("matches") == 1,
               "it is found again", json.dumps(out))

        code, out = db("note", "doctor")
        require(code == 0 and not out.get("sync_conflicts"),
               "doctor is clean in a diary-only folder", json.dumps(out))

        # A ledger command must fail cleanly (DaybookError), never crash.
        code, out = db("balance", "anyone")
        require(code == 1 and "error" in out,
               "a ledger command in a diary-only folder fails cleanly, not with a traceback",
               json.dumps(out))

        run(["uv", "tool", "uninstall", "project-daybook"], env=home.env)
        require(not binary.exists(), "uninstall removes the diary-only install's binary too")
        require((records_dir / "notes").exists(),
               "the diary itself survives uninstall")
    finally:
        home.cleanup()


def main() -> int:
    step(f"daybook end-to-end suite -- repo at {REPO_ROOT}")
    full_pass()
    diary_only_pass()

    print()
    total = PASSED + len(FAILURES)
    if FAILURES:
        print(f"\033[1;31m{len(FAILURES)}/{total} checks failed:\033[0m")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print(f"\033[1;32mAll {total} checks passed.\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
