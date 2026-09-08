"""End-to-end tests: write through the CLI, verify with the library."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ledger_tools import interest, queries
from ledger_tools.entities import load_entities, resolve
from ledger_tools.store import LedgerError, Paths, load


def _entity(paths: Paths, name: str):
    entries, _ = load(paths)
    found = resolve(name, load_entities(entries, paths.root))
    assert found["status"] == "resolved", found
    return entries, next(e for e in load_entities(entries, paths.root) if e.slug == found["entity"]["slug"])


def _add_dad(run, **overrides):
    args = {
        "--name": "Harjit Singh", "--relation": "father", "--aliases": "dad, papa",
        "--currency": "INR", "--rate": "8", "--method": "simple", "--opened": "2026-01-01",
    }
    args.update(overrides)
    flat = [x for pair in args.items() for x in pair]
    run("entity", "add", *flat)


# ------------------------------------------------------------------ resolution

def test_alias_resolves_to_one_person(paths, run):
    _add_dad(run)
    entries, _ = load(paths)
    entities = load_entities(entries, paths.root)
    for alias in ("dad", "papa", "Harjit Singh", "HARJIT SINGH"):
        assert resolve(alias, entities)["status"] == "resolved", alias


def test_near_miss_asks_instead_of_guessing(paths, run):
    _add_dad(run)
    entries, _ = load(paths)
    result = resolve("harjeet", load_entities(entries, paths.root))
    assert result["status"] == "ambiguous"
    assert result["candidates"][0]["name"] == "Harjit Singh"


def test_unknown_name_is_unknown(paths, run):
    _add_dad(run)
    entries, _ = load(paths)
    assert resolve("Ravi", load_entities(entries, paths.root))["status"] == "unknown"


def test_duplicate_alias_is_refused(paths, run):
    _add_dad(run)
    run("entity", "add", "--name", "Dad Smith", "--aliases", "dad", expect=1)


def test_alias_can_be_added_later(paths, run):
    _add_dad(run)
    run("entity", "alias", "Harjit Singh", "--add", "pitaji")
    entries, _ = load(paths)
    assert resolve("pitaji", load_entities(entries, paths.root))["status"] == "resolved"


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
    entries, dad = _entity(paths, "dad")
    result = queries.balance(entries, dad, date(2026, 9, 30), paths.root)
    assert result["owed_to_you"] == {"INR": "6000", "USD": "200"}
    assert result["interest_received"] == {"INR": "100"}
    assert result["counts"] == {
        "lent": 3, "repaid": 1, "borrowed": 0, "repaid_to_them": 0, "interest_received": 1,
    }


def test_currencies_are_never_added_together(paths, run):
    _scenario(run)
    entries, dad = _entity(paths, "dad")
    owed = queries.balance(entries, dad, None, paths.root)["owed_to_you"]
    assert set(owed) == {"INR", "USD"}
    assert Decimal(owed["INR"]) == 6000 and Decimal(owed["USD"]) == 200


def test_balance_respects_as_of_date(paths, run):
    _scenario(run)
    entries, dad = _entity(paths, "dad")
    early = queries.balance(entries, dad, date(2026, 9, 2), paths.root)
    assert early["owed_to_you"] == {"INR": "5000"}
    assert early["counts"]["lent"] == 1


def test_every_row_cites_a_line(paths, run):
    _scenario(run)
    entries, dad = _entity(paths, "dad")
    rows = queries.statement(entries, dad, None, paths.root)["rows"]
    assert rows and all(":" in r["citation"] and r["citation"].split(":")[-1].isdigit() for r in rows)


def test_statement_running_balance(paths, run):
    _scenario(run)
    entries, dad = _entity(paths, "dad")
    statement = queries.statement(entries, dad, None, paths.root)
    inr = [r["balance_owed_to_you"] for r in statement["rows"] if r["currency"] == "INR" and r["role"] == "loans"]
    assert inr == ["5000", "8000", "6000"]
    assert statement["final_balance_owed_to_you"] == {"INR": "6000", "USD": "200"}


# ------------------------------------------------------------------- duplicates

def test_duplicate_is_refused_then_allowed_with_force(paths, run, capsys):
    _add_dad(run)
    run("add", "--kind", "lend", "--who", "dad", "--amount", "3000",
        "--date", "2026-09-03", "--note", "school fees", "--no-commit")
    capsys.readouterr()
    run("--json", "add", "--kind", "lend", "--who", "dad", "--amount", "3000",
        "--date", "2026-09-03", "--note", "school fees again", "--no-commit")
    assert "possible_duplicate" in capsys.readouterr().out
    entries, dad = _entity(paths, "dad")
    assert queries.balance(entries, dad, None, paths.root)["counts"]["lent"] == 1

    run("add", "--kind", "lend", "--who", "dad", "--amount", "3000", "--date", "2026-09-03",
        "--note", "genuinely separate", "--force", "--no-commit")
    entries, dad = _entity(paths, "dad")
    assert queries.balance(entries, dad, None, paths.root)["counts"]["lent"] == 2


def test_duplicate_window_does_not_reach_a_week_out(paths, run):
    _add_dad(run)
    run("add", "--kind", "lend", "--who", "dad", "--amount", "3000",
        "--date", "2026-09-01", "--note", "one", "--no-commit")
    run("add", "--kind", "lend", "--who", "dad", "--amount", "3000",
        "--date", "2026-09-10", "--note", "two", "--no-commit")
    entries, dad = _entity(paths, "dad")
    assert queries.balance(entries, dad, None, paths.root)["counts"]["lent"] == 2


# -------------------------------------------------------------------- interest

def test_simple_interest_matches_hand_calculation(paths, run):
    _scenario(run)
    entries, dad = _entity(paths, "dad")
    result = interest.project(entries, dad, date(2027, 9, 1), paths.root)
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
    entries, dad = _entity(paths, "dad")
    result = interest.project(entries, dad, date(2027, 1, 1), paths.root)
    assert Decimal(result["by_currency"]["INR"]["projected_interest"]) == Decimal("100.00")


def test_monthly_compounding_beats_annual(paths, run):
    _add_dad(run, **{"--rate": "10", "--method": "compound", "--compounding": "monthly"})
    run("add", "--kind", "lend", "--who", "dad", "--amount", "1000",
        "--date", "2026-01-01", "--note", "loan", "--no-commit")
    entries, dad = _entity(paths, "dad")
    result = interest.project(entries, dad, date(2027, 1, 1), paths.root)
    assert Decimal(result["by_currency"]["INR"]["projected_interest"]) == Decimal("104.71")


def test_projection_is_labelled_and_separate_from_owed(paths, run):
    _scenario(run)
    entries, dad = _entity(paths, "dad")
    projection = interest.project(entries, dad, date(2027, 9, 1), paths.root)
    assert "not owed" in projection["PROJECTION"].lower()
    assert projection["by_currency"]["INR"]["formula"]
    balance = queries.balance(entries, dad, date(2027, 9, 1), paths.root)
    assert balance["owed_to_you"]["INR"] == "6000"  # projection never folded in


def test_projection_without_a_rate_refuses(paths, run):
    run("entity", "add", "--name", "Ravi Kumar", "--aliases", "ravi", "--currency", "INR")
    run("add", "--kind", "lend", "--who", "ravi", "--amount", "500",
        "--date", "2026-01-01", "--note", "loan", "--no-commit")
    entries, ravi = _entity(paths, "ravi")
    with pytest.raises(LedgerError, match="rate_percent_pa"):
        interest.project(entries, ravi, date(2027, 1, 1), paths.root)


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
    entries, dad = _entity(paths, "dad")
    assert queries.balance(entries, dad, None, paths.root)["owed_to_you"] == {}


# -------------------------------------------------------------------- capture

def test_bad_amount_is_refused(paths, run):
    _add_dad(run)
    run("add", "--kind", "lend", "--who", "dad", "--amount", "abc", "--no-commit", expect=1)
    run("add", "--kind", "lend", "--who", "dad", "--amount", "-5", "--no-commit", expect=1)


def test_unknown_person_is_refused_not_invented(paths, run):
    run("add", "--kind", "lend", "--who", "nobody", "--amount", "10",
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
