"""End-to-end tests: write through the CLI, verify with the library."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from daybook_tools import interest, queries
from daybook_tools.contracts import load_contracts
from daybook_tools.entities import load_entities, resolve
from daybook_tools.store import DaybookError, Paths, load


def _add_me(run):
    run("entity", "add", "--name", "Me", "--aliases", "me", "--currency", "INR",
        "--book", "--self")


def _load_all(paths: Paths):
    entries, _ = load(paths)
    entities = load_entities(entries, paths.root)
    contracts, problems = load_contracts(entries, entities, paths.root)
    assert problems == [], problems
    return entries, entities, contracts


def _entity(paths: Paths, name: str):
    entries, entities, contracts = _load_all(paths)
    found = resolve(name, entities)
    assert found["status"] == "resolved", found
    entity = next(e for e in entities if e.slug == found["entity"]["slug"])
    return entries, contracts, entity


def _add_dad(run, **overrides):
    """A book owner ('me'), a borrower ('dad'), and a contract between them."""
    _add_me(run)
    run("entity", "add", "--name", "Harjit Singh", "--relation", "father",
        "--aliases", "dad, papa", "--currency", "INR")
    terms = {"--rate": "8", "--method": "simple", "--started": "2026-01-01"}
    terms.update(overrides)
    flat = [x for pair in terms.items() for x in pair]
    run("contract", "add", "--lender", "me", "--borrower", "dad", *flat)


# ------------------------------------------------------------------ resolution

def test_alias_resolves_to_one_person(paths, run):
    _add_dad(run)
    _, entities, _ = _load_all(paths)
    for alias in ("dad", "papa", "Harjit Singh", "HARJIT SINGH"):
        assert resolve(alias, entities)["status"] == "resolved", alias


def test_near_miss_asks_instead_of_guessing(paths, run):
    _add_dad(run)
    _, entities, _ = _load_all(paths)
    result = resolve("harjeet", entities)
    assert result["status"] == "ambiguous"
    assert result["candidates"][0]["name"] == "Harjit Singh"


def test_unknown_name_is_unknown(paths, run):
    _add_dad(run)
    _, entities, _ = _load_all(paths)
    assert resolve("Ravi", entities)["status"] == "unknown"


def test_duplicate_alias_is_refused(paths, run):
    _add_dad(run)
    run("entity", "add", "--name", "Dad Smith", "--aliases", "dad", expect=1)


def test_alias_can_be_added_later(paths, run):
    _add_dad(run)
    run("entity", "alias", "Harjit Singh", "--add", "pitaji")
    _, entities, _ = _load_all(paths)
    assert resolve("pitaji", entities)["status"] == "resolved"


# --------------------------------------------------------------------- balances

def _scenario(run):
    _add_dad(run)
    run("add", "--kind", "lend", "--who", "dad", "--amount", "5000",
        "--date", "2026-09-01", "--note", "for the car", "--no-commit")
    run("add", "--kind", "lend", "--who", "dad", "--amount", "3000",
        "--date", "2026-09-03", "--note", "school fees", "--no-commit")
    run("add", "--kind", "lend", "--who", "papa", "--amount", "200", "--currency", "USD",
        "--date", "2026-09-04", "--note", "travel", "--no-commit")
    run("add", "--kind", "repay", "--who", "dad", "--amount", "2000",
        "--date", "2026-09-05", "--note", "part repayment", "--no-commit")
    run("add", "--kind", "interest", "--who", "dad", "--amount", "100",
        "--date", "2026-09-06", "--note", "August interest", "--no-commit")


def test_balance_counts_and_currencies(paths, run):
    _scenario(run)
    entries, contracts, dad = _entity(paths, "dad")
    result = queries.balance(entries, dad, contracts, date(2026, 9, 30), paths.root)
    assert result["payable"] == {"INR": "6000", "USD": "200"}
    assert result["interest_paid"] == {"INR": "100"}
    assert result["counts"] == {
        "lent": 0, "repaid": 0, "borrowed": 3, "returned": 1,
        "interest_received": 0, "interest_paid": 1,
    }


def test_the_lender_sees_the_mirror_image(paths, run):
    _scenario(run)
    entries, contracts, me = _entity(paths, "me")
    result = queries.balance(entries, me, contracts, date(2026, 9, 30), paths.root)
    assert result["receivable"] == {"INR": "6000", "USD": "200"}
    assert result["interest_received"] == {"INR": "100"}
    assert result["counts"]["lent"] == 3
    assert result["counts"]["repaid"] == 1


def test_currencies_are_never_added_together(paths, run):
    _scenario(run)
    entries, contracts, dad = _entity(paths, "dad")
    owed = queries.balance(entries, dad, contracts, None, paths.root)["payable"]
    assert set(owed) == {"INR", "USD"}
    assert Decimal(owed["INR"]) == 6000 and Decimal(owed["USD"]) == 200


def test_balance_respects_as_of_date(paths, run):
    _scenario(run)
    entries, contracts, dad = _entity(paths, "dad")
    early = queries.balance(entries, dad, contracts, date(2026, 9, 2), paths.root)
    assert early["payable"] == {"INR": "5000"}
    assert early["counts"]["borrowed"] == 1


def test_every_row_cites_a_line(paths, run):
    _scenario(run)
    entries, contracts, dad = _entity(paths, "dad")
    rows = queries.statement(entries, dad, contracts, None, paths.root)["rows"]
    assert rows and all(":" in r["citation"] and r["citation"].split(":")[-1].isdigit() for r in rows)


def test_statement_running_balance(paths, run):
    _scenario(run)
    entries, contracts, dad = _entity(paths, "dad")
    statement = queries.statement(entries, dad, contracts, None, paths.root)
    inr = [r["balance_on_contract"] for r in statement["rows"]
          if r["currency"] == "INR" and r["role"] == "borrower"]
    assert inr == ["5000", "8000", "6000"]
    assert statement["final_net"] == {"INR": "-6000", "USD": "-200"}


# ------------------------------------------------------------------- duplicates

def test_duplicate_is_refused_then_allowed_with_force(paths, run, capsys):
    _add_dad(run)
    run("add", "--kind", "lend", "--who", "dad", "--amount", "3000",
        "--date", "2026-09-03", "--note", "school fees", "--no-commit")
    capsys.readouterr()
    run("--json", "add", "--kind", "lend", "--who", "dad", "--amount", "3000",
        "--date", "2026-09-03", "--note", "school fees again", "--no-commit")
    assert "possible_duplicate" in capsys.readouterr().out
    entries, contracts, dad = _entity(paths, "dad")
    assert queries.balance(entries, dad, contracts, None, paths.root)["counts"]["borrowed"] == 1

    run("add", "--kind", "lend", "--who", "dad", "--amount", "3000", "--date", "2026-09-03",
        "--note", "genuinely separate", "--force", "--no-commit")
    entries, contracts, dad = _entity(paths, "dad")
    assert queries.balance(entries, dad, contracts, None, paths.root)["counts"]["borrowed"] == 2


def test_duplicate_window_does_not_reach_a_week_out(paths, run):
    _add_dad(run)
    run("add", "--kind", "lend", "--who", "dad", "--amount", "3000",
        "--date", "2026-09-01", "--note", "one", "--no-commit")
    run("add", "--kind", "lend", "--who", "dad", "--amount", "3000",
        "--date", "2026-09-10", "--note", "two", "--no-commit")
    entries, contracts, dad = _entity(paths, "dad")
    assert queries.balance(entries, dad, contracts, None, paths.root)["counts"]["borrowed"] == 2


# -------------------------------------------------------------------- interest

def test_simple_interest_matches_hand_calculation(paths, run):
    _scenario(run)
    entries, contracts, dad = _entity(paths, "dad")
    result = interest.project(entries, dad, date(2027, 9, 1), paths.root, contracts=contracts)
    expected = sum(
        Decimal(p) * Decimal("0.08") * Decimal(d) / Decimal(365)
        for p, d in [("5000", 2), ("8000", 2), ("6000", 361)]
    )
    assert Decimal(result["by_currency"]["INR"]["projected_interest"]) == round(expected, 2)
    assert result["by_currency"]["INR"]["outstanding_principal"] == "6000"


def test_compound_interest_matches_hand_calculation(paths, run):
    _add_dad(run, **{"--rate": "10", "--method": "compound", "--compounding": "annual"})
    run("add", "--kind", "lend", "--who", "dad", "--amount", "1000",
        "--date", "2026-01-01", "--note", "loan", "--no-commit")
    entries, contracts, dad = _entity(paths, "dad")
    result = interest.project(entries, dad, date(2027, 1, 1), paths.root, contracts=contracts)
    assert Decimal(result["by_currency"]["INR"]["projected_interest"]) == Decimal("100.00")


def test_monthly_compounding_beats_annual(paths, run):
    _add_dad(run, **{"--rate": "10", "--method": "compound", "--compounding": "monthly"})
    run("add", "--kind", "lend", "--who", "dad", "--amount", "1000",
        "--date", "2026-01-01", "--note", "loan", "--no-commit")
    entries, contracts, dad = _entity(paths, "dad")
    result = interest.project(entries, dad, date(2027, 1, 1), paths.root, contracts=contracts)
    assert Decimal(result["by_currency"]["INR"]["projected_interest"]) == Decimal("104.71")


def test_projection_is_labelled_and_separate_from_owed(paths, run):
    _scenario(run)
    entries, contracts, dad = _entity(paths, "dad")
    projection = interest.project(entries, dad, date(2027, 9, 1), paths.root, contracts=contracts)
    assert "not owed" in projection["PROJECTION"].lower()
    assert projection["by_currency"]["INR"]["formula"]
    balance = queries.balance(entries, dad, contracts, date(2027, 9, 1), paths.root)
    assert balance["payable"]["INR"] == "6000"  # projection never folded in


def test_projection_without_a_matching_contract_refuses(paths, run):
    _add_me(run)
    run("entity", "add", "--name", "Ravi Kumar", "--aliases", "ravi", "--currency", "INR")
    with pytest.raises(DaybookError, match="no matching loan contract"):
        entries, contracts, ravi = _entity(paths, "ravi")
        interest.project(entries, ravi, date(2027, 1, 1), paths.root, contracts=contracts)


# ---------------------------------------------------------------- corrections

def test_void_reverses_without_editing_history(paths, run):
    _add_dad(run)
    run("add", "--kind", "lend", "--who", "dad", "--amount", "5000",
        "--date", "2026-09-01", "--note", "typo", "--no-commit")
    before = (paths.ledger_dir / "2026.beancount").read_text(encoding="utf-8")
    run("void", "--voids", "2026.beancount:3", "--kind", "lend", "--who", "dad",
        "--amount", "5000", "--date", "2026-09-01", "--note", "typo", "--no-commit")
    after = (paths.ledger_dir / "2026.beancount").read_text(encoding="utf-8")
    assert before in after, "the original entry must still be there"
    entries, contracts, dad = _entity(paths, "dad")
    assert queries.balance(entries, dad, contracts, None, paths.root)["payable"] == {}


# -------------------------------------------------------------------- capture

def test_bad_amount_is_refused(paths, run):
    _add_dad(run)
    run("add", "--kind", "lend", "--who", "dad", "--amount", "abc", "--no-commit", expect=1)
    run("add", "--kind", "lend", "--who", "dad", "--amount", "-5", "--no-commit", expect=1)


def test_unknown_person_is_refused_not_invented(paths, run):
    _add_me(run)
    run("add", "--kind", "lend", "--who", "nobody", "--amount", "10",
        "--currency", "INR", "--no-commit", expect=1)


def test_a_debt_with_no_terms_is_recorded_as_an_iou(paths, run):
    """Not everything somebody owes you is a loan. Recording "they owe me 10"
    must not require inventing a rate, and must still count."""
    _add_me(run)
    run("entity", "add", "--name", "Someone", "--aliases", "them", "--currency", "INR")
    run("add", "--kind", "lend", "--who", "them", "--amount", "10",
        "--currency", "INR", "--date", "2026-01-01", "--no-commit")

    entries, entities, contracts = _load_all(paths)
    assert len(contracts) == 1
    iou = contracts[0]
    assert iou.has_terms is False
    assert iou.kind == "iou"
    assert iou.rate_percent_pa == ""
    assert iou.method == "" and iou.day_count == ""
    assert iou.contract_id.endswith("-iou")

    # No rate was agreed, so no rate is written down.
    text = paths.accounts.read_text(encoding="utf-8")
    assert "rate_percent_pa" not in text.split("open " + iou.account)[1]

    # And it is counted exactly like any other debt.
    them = next(e for e in entities if e.slug == iou.counterparty_slug)
    balance = queries.balance(entries, them, contracts, None, paths.root)
    assert balance["payable"] == {"INR": "10"}


def test_a_zero_percent_loan_is_not_the_same_as_an_iou(paths, run):
    """"0%" is a term somebody agreed. Absent terms are not zero terms."""
    _add_me(run)
    run("entity", "add", "--name", "Someone", "--aliases", "them", "--currency", "INR")
    run("contract", "add", "--lender", "me", "--borrower", "them", "--rate", "0",
        "--started", "2026-01-01")
    _, _, contracts = _load_all(paths)
    assert contracts[0].has_terms is True
    assert contracts[0].rate_percent_pa == "0"
    assert contracts[0].kind == "loan"


def test_projection_skips_an_iou_rather_than_projecting_zero(paths, run):
    from daybook_tools.cli import main

    _add_me(run)
    run("entity", "add", "--name", "Someone", "--aliases", "them", "--currency", "INR")
    run("add", "--kind", "lend", "--who", "them", "--amount", "10",
        "--currency", "INR", "--date", "2026-01-01", "--no-commit")

    entries, entities, contracts = _load_all(paths)
    them = next(e for e in entities if e.slug != _entity(paths, "me")[2].slug)
    result = interest.project(entries, them, date(2026, 12, 31), paths.root,
                              contracts=contracts)
    assert result["by_currency"] == {}
    assert result["skipped"][0]["reason"].startswith("an IOU")
    assert "no loan with interest terms" in result["detail"]
    assert main(["projection", "them", "--as-of", "2026-12-31"]) == 0


def test_an_iou_can_be_given_terms_later(paths, run):
    """A debt that turns out to be a loan gets its terms filled in; it does not
    need a second record."""
    _add_me(run)
    run("entity", "add", "--name", "Someone", "--aliases", "them", "--currency", "INR")
    run("add", "--kind", "lend", "--who", "them", "--amount", "1000",
        "--currency", "INR", "--date", "2026-01-01", "--no-commit")
    _, _, contracts = _load_all(paths)
    iou = contracts[0]

    run("contract", "terms", iou.contract_id, "--rate", "12", "--method", "simple")

    _, _, after = _load_all(paths)
    promoted = after[0]
    assert promoted.contract_id == iou.contract_id, "the same record, not a new one"
    assert promoted.has_terms is True
    assert promoted.rate_percent_pa == "12"
    assert promoted.method == "simple"
    assert promoted.day_count == "actual/365"

    # Terms are filled in once, never edited: a second attempt is refused.
    run("contract", "terms", iou.contract_id, "--rate", "15", expect=1)


def test_a_bare_principal_with_no_record_still_asks_which_way(paths, run):
    """`lend` and `borrow` say a direction. `principal` does not, so with
    nothing on file there is no way to know, and inventing one would be a
    guess about who owes whom."""
    _add_me(run)
    run("entity", "add", "--name", "Someone", "--aliases", "them", "--currency", "INR")
    run("add", "--kind", "principal", "--who", "them", "--amount", "10",
        "--currency", "INR", "--no-commit", expect=1)


def test_source_phrase_is_kept(paths, run):
    _add_dad(run)
    run("add", "--kind", "lend", "--who", "dad", "--amount", "50", "--date", "2026-09-01",
        "--note", "tea", "--source", "lent dad 50 for tea", "--no-commit")
    text = (paths.ledger_dir / "2026.beancount").read_text(encoding="utf-8")
    assert 'source: "lent dad 50 for tea"' in text


def test_ledger_stays_valid_after_writes(paths, run):
    _scenario(run)
    run("check")


# ------------------------------------------------------ keeping records private

def test_records_can_live_in_their_own_folder(tmp_path, monkeypatch):
    """Your records may sit in a private folder of their own, so the tool can be
    published without publishing your finances."""
    from daybook_tools.cli import main
    from daybook_tools.store import Paths, project_root

    data = tmp_path / "private-records"
    data.mkdir()
    monkeypatch.setenv("LEDGER_ROOT", str(data))
    monkeypatch.chdir(tmp_path)
    assert main(["init", str(data), "--currencies", "INR", "--no-remember"]) == 0

    # The records go straight into the folder that was named, not a level down.
    assert (data / "main.beancount").exists()
    assert not (data / "ledger").exists()
    assert project_root() == data
    assert Paths(data).main.exists()
    assert main(["check"]) == 0


def test_ledger_subfolder_layout_still_works(tmp_path, monkeypatch):
    """Running init with no folder keeps records in ./ledger, which must still load."""
    from daybook_tools.cli import main
    from daybook_tools.store import Paths

    home = tmp_path / "project"
    home.mkdir()
    monkeypatch.chdir(home)
    monkeypatch.setenv("LEDGER_ROOT", str(home))
    assert main(["init", "--currencies", "INR"]) == 0
    assert (home / "ledger" / "main.beancount").exists()
    assert Paths(home).ledger_dir == home / "ledger"
    assert main(["check"]) == 0


def test_init_remembers_where_the_records_went(tmp_path, monkeypatch):
    """Naming a folder elsewhere leaves a .ledger-root pointer behind."""
    from daybook_tools.cli import main
    from daybook_tools.store import LOCATION_FILENAME, project_root

    code = tmp_path / "code"
    code.mkdir()
    records = tmp_path / "elsewhere" / "ledger"
    monkeypatch.chdir(code)
    monkeypatch.delenv("LEDGER_ROOT", raising=False)
    assert main(["init", str(records), "--currencies", "INR"]) == 0
    assert (code / LOCATION_FILENAME).read_text().strip() == str(records)
    assert project_root() == records


def test_init_can_make_the_records_a_git_repository(tmp_path, monkeypatch):
    from daybook_tools.cli import main
    from daybook_tools.store import git_repo_for

    records = tmp_path / "records"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LEDGER_ROOT", raising=False)
    assert main(["init", str(records), "--currencies", "INR", "--git", "--no-remember"]) == 0
    assert (records / ".git").exists()
    assert git_repo_for(records) == records


def test_writes_commit_to_the_repository_holding_the_records(tmp_path, monkeypatch):
    """A private records repo nested in a public code repo gets the commits."""
    import subprocess

    from daybook_tools.cli import main
    from daybook_tools.store import Paths, git_repo_for

    records = tmp_path / "records"
    monkeypatch.setenv("LEDGER_ROOT", str(records))
    monkeypatch.chdir(tmp_path)
    assert main(["init", str(records), "--currencies", "INR", "--no-remember"]) == 0
    paths = Paths(records)

    def git(*args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    for folder in (tmp_path, paths.ledger_dir):
        git("init", "-b", "main", cwd=folder)
        git("config", "user.email", "t@example.com", cwd=folder)
        git("config", "user.name", "Test", cwd=folder)
    (tmp_path / ".gitignore").write_text("records/\n", encoding="utf-8")
    git("add", "-A", cwd=tmp_path)
    git("commit", "-m", "code", cwd=tmp_path)
    git("add", "-A", cwd=paths.ledger_dir)
    git("commit", "-m", "records", cwd=paths.ledger_dir)

    assert git_repo_for(paths.ledger_dir) == paths.ledger_dir
    main(["entity", "add", "--name", "Me", "--aliases", "me", "--currency", "INR",
          "--book", "--self"])
    main(["entity", "add", "--name", "Someone", "--aliases", "them", "--currency", "INR"])
    main(["contract", "add", "--lender", "me", "--borrower", "them", "--rate", "0",
          "--started", "2026-09-01"])
    main(["add", "--kind", "lend", "--who", "them", "--amount", "10", "--date", "2026-09-01",
          "--note", "a loan"])

    def count(folder):
        out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=folder,
                             check=True, capture_output=True, text=True)
        return int(out.stdout.strip())

    assert count(paths.ledger_dir) > 1, "the entry should be committed with the records"
    assert count(tmp_path) == 1, "the code repository must not receive record commits"


def test_location_file_points_at_records_elsewhere(tmp_path, monkeypatch):
    """A .ledger-root file lets the code folder and the records live apart,
    without relying on an environment variable a session may not inherit."""
    from daybook_tools.cli import main
    from daybook_tools.store import project_root

    records = tmp_path / "Documents" / "ledger"
    records.mkdir(parents=True)
    code = tmp_path / "code"
    code.mkdir()

    monkeypatch.setenv("LEDGER_ROOT", str(records))
    assert main(["init", str(records), "--currencies", "INR", "--no-remember"]) == 0
    monkeypatch.delenv("LEDGER_ROOT")

    monkeypatch.delenv("DAYBOOK_ROOT", raising=False)
    (code / ".ledger-root").write_text(str(records), encoding="utf-8")
    monkeypatch.chdir(code)
    assert project_root() == records.resolve()
    assert main(["check"]) == 0


def test_location_file_naming_a_missing_folder_says_so(tmp_path, monkeypatch):
    from daybook_tools.store import DaybookError, project_root

    code = tmp_path / "code"
    code.mkdir()
    (code / ".ledger-root").write_text(str(tmp_path / "nowhere"), encoding="utf-8")
    monkeypatch.chdir(code)
    monkeypatch.delenv("LEDGER_ROOT", raising=False)
    monkeypatch.delenv("DAYBOOK_ROOT", raising=False)
    with pytest.raises(DaybookError, match="no main.beancount was found"):
        project_root()


# --------------------------------------------------------------- the rename

def _init_records(tmp_path, monkeypatch):
    """An initialised records folder, with no root variable left set."""
    from daybook_tools.cli import main

    records = tmp_path / "Documents" / "records"
    records.mkdir(parents=True)
    monkeypatch.setenv("DAYBOOK_ROOT", str(records))
    assert main(["init", str(records), "--currencies", "INR", "--no-remember"]) == 0
    monkeypatch.delenv("DAYBOOK_ROOT")
    monkeypatch.delenv("LEDGER_ROOT", raising=False)
    return records


def test_legacy_ledger_root_env_var_still_finds_the_records(tmp_path, monkeypatch):
    """The project was renamed after people had already installed it. LEDGER_ROOT
    names where somebody's real records live; dropping it would orphan them."""
    from daybook_tools.store import project_root

    records = _init_records(tmp_path, monkeypatch)
    monkeypatch.setenv("LEDGER_ROOT", str(records))
    assert project_root() == records.resolve()


def test_legacy_ledger_root_pointer_file_still_finds_the_records(tmp_path, monkeypatch):
    """Same promise for the pointer file, which is the form most installs use."""
    from daybook_tools.store import project_root

    records = _init_records(tmp_path, monkeypatch)
    code = tmp_path / "code"
    code.mkdir()
    (code / ".ledger-root").write_text(str(records), encoding="utf-8")
    monkeypatch.chdir(code)
    assert project_root() == records.resolve()


def test_daybook_root_pointer_file_is_the_new_spelling(tmp_path, monkeypatch):
    from daybook_tools.store import LOCATION_FILENAME, project_root

    assert LOCATION_FILENAME == ".daybook-root"
    records = _init_records(tmp_path, monkeypatch)
    code = tmp_path / "code"
    code.mkdir()
    (code / LOCATION_FILENAME).write_text(str(records), encoding="utf-8")
    monkeypatch.chdir(code)
    assert project_root() == records.resolve()


def test_daybook_root_wins_when_both_variables_are_set(tmp_path, monkeypatch):
    """Pin the precedence, so a stale LEDGER_ROOT cannot quietly shadow a
    deliberate DAYBOOK_ROOT."""
    from daybook_tools.cli import main
    from daybook_tools.store import project_root

    new = _init_records(tmp_path, monkeypatch)
    old = tmp_path / "old-records"
    old.mkdir()
    monkeypatch.setenv("DAYBOOK_ROOT", str(old))
    assert main(["init", str(old), "--currencies", "INR", "--no-remember"]) == 0

    monkeypatch.setenv("DAYBOOK_ROOT", str(new))
    monkeypatch.setenv("LEDGER_ROOT", str(old))
    assert project_root() == new.resolve()


def test_both_console_scripts_point_at_the_same_entry_point():
    """`ledger` stays registered alongside `daybook` so existing installs and
    muscle memory keep working."""
    import tomllib

    root = Path(__file__).resolve().parent.parent
    with open(root / "pyproject.toml", "rb") as handle:
        config = tomllib.load(handle)
    scripts = config["project"]["scripts"]
    assert scripts["daybook"] == "daybook_tools.cli:main"
    assert scripts["ledger"] == scripts["daybook"]


# ------------------------------------------- backfilling, and honest rollback

def test_backfilling_moves_the_open_date_back(ledger_root, run, paths):
    """Recording a 2025 trip in 2026 is ordinary backfilling, and it is how
    almost every ledger gets started. It must not need a manual step."""
    from daybook_tools.cli import main
    from daybook_tools.store import opens

    run("entity", "add", "--name", "Me", "--aliases", "me", "--book", "--self",
        "--currency", "USD")
    run("entity", "add", "--name", "Nik", "--aliases", "nik", "--currency", "USD")
    run("contract", "add", "--lender", "Me", "--borrower", "Nik", "--rate", "0",
        "--method", "simple", "--started", "2026-09-11")

    run("add", "--kind", "lend", "--who", "nik", "--amount", "306",
        "--date", "2025-07-01", "--note", "Phuket trip")

    assert main(["check"]) == 0
    entries, _ = load(paths)
    contract = next(o for o in opens(entries) if o.account.startswith("Assets:Loans:"))
    assert contract.date == date(2025, 7, 1), "the open date should have moved back"

    # And the entry is really there, queryable.
    txns = queries.transactions(entries)
    assert [t.date for t in txns] == [date(2025, 7, 1)]


def test_backdating_only_ever_moves_a_date_earlier(ledger_root, run, paths):
    from daybook_tools.store import backdate_open

    run("entity", "add", "--name", "Nik", "--aliases", "nik", "--currency", "USD")
    account = "Equity:Entities:Nik"
    moved = backdate_open(paths, account, date(2020, 1, 1))
    assert moved["changed"] is True and moved["to"] == "2020-01-01"

    # A later date is not applied: an open directive never drifts forwards.
    again = backdate_open(paths, account, date(2030, 1, 1))
    assert again["changed"] is False
    assert "2020-01-01 open Equity:Entities:Nik" in paths.accounts.read_text(encoding="utf-8")


def test_a_rejected_entry_really_does_leave_nothing_behind(ledger_root, run, paths,
                                                           monkeypatch):
    """The error says 'nothing was saved', so that has to be true.

    It was not: rollback went through `git checkout`, which silently does
    nothing for an untracked file or a records folder that is not a git
    repository at all -- the installer's default. A rejected entry was left
    behind and the ledger became permanently unloadable.
    """
    from daybook_tools import capture
    from daybook_tools.cli import main

    run("entity", "add", "--name", "Me", "--aliases", "me", "--book", "--self",
        "--currency", "USD")
    run("entity", "add", "--name", "Nik", "--aliases", "nik", "--currency", "USD")
    run("contract", "add", "--lender", "Me", "--borrower", "Nik", "--rate", "0",
        "--method", "simple", "--started", "2026-01-01")
    assert not (ledger_root / ".git").exists(), "this test is about the no-git case"

    before = {
        path.name: path.read_bytes()
        for path in ledger_root.glob("*.beancount")
    }
    assert "2025.beancount" not in before

    class Rejects:
        """Beancount, but it hates this entry."""
        @staticmethod
        def load_file(path):
            entry = type("E", (), {"source": None, "message": "nope"})()
            return [], [entry], {}

    # A context, not monkeypatch.undo(), which would also undo the autouse
    # fixture that clears the ambient root variables.
    with monkeypatch.context() as patched:
        patched.setattr(capture, "loader", Rejects)
        # main() turns a DaybookError into exit 1, which is what the user sees.
        assert main(["add", "--kind", "lend", "--who", "nik", "--amount", "306",
                     "--date", "2025-07-01", "--note", "Phuket"]) == 1

    after = {
        path.name: path.read_bytes()
        for path in ledger_root.glob("*.beancount")
    }
    assert after == before, "a rejected entry must leave the files exactly as they were"
    assert not (ledger_root / "2025.beancount").exists()
    assert main(["check"]) == 0, "the ledger must still load"
