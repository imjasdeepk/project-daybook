"""Contracts: the same borrower can hold several loans, at different rates,
from different lenders, and every aggregation is a real sum computed here --
never a number the assistant driving this tool adds up itself."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from daybook_tools import interest, queries
from daybook_tools.contracts import encode_rate, load_contracts
from daybook_tools.entities import load_entities, resolve
from daybook_tools.store import load


def _entity(paths, name):
    entries, _ = load(paths)
    entities = load_entities(entries, paths.root)
    contracts, problems = load_contracts(entries, entities, paths.root)
    assert problems == [], problems
    found = resolve(name, entities)
    assert found["status"] == "resolved", found
    entity = next(e for e in entities if e.slug == found["entity"]["slug"])
    return entries, contracts, entity


def _two_lender_scenario(run):
    """Alex and Casey both keep books; Blue Moon borrows from each at a different rate."""
    run("entity", "add", "--name", "Alex Rivera", "--aliases", "me, alex",
        "--currency", "INR", "--book", "--self")
    run("entity", "add", "--name", "Casey Nolan", "--aliases", "casey",
        "--currency", "INR", "--book")
    run("entity", "add", "--name", "Blue Moon Jewellers", "--aliases", "bluemoon", "--currency", "INR")
    run("contract", "add", "--lender", "casey", "--borrower", "bluemoon", "--rate", "10.2",
        "--method", "compound", "--compounding", "annual", "--started", "2026-04-01")
    run("contract", "add", "--lender", "alex", "--borrower", "bluemoon", "--rate", "12",
        "--method", "compound", "--compounding", "annual", "--started", "2026-06-08")
    run("add", "--kind", "lend", "--lender", "casey", "--borrower", "bluemoon",
        "--amount", "7000000", "--date", "2026-04-01", "--no-commit")
    run("add", "--kind", "lend", "--lender", "alex", "--borrower", "bluemoon",
        "--amount", "5000000", "--date", "2026-06-08", "--no-commit")


def test_two_contracts_for_the_same_borrower_project_at_their_own_rates(paths, run):
    _two_lender_scenario(run)
    entries, contracts, blue_moon = _entity(paths, "bluemoon")
    as_of = date(2027, 4, 1)
    result = interest.project(entries, blue_moon, as_of, paths.root, contracts=contracts)

    rate1 = Decimal("10.2") / 100
    days1 = (as_of - date(2026, 4, 1)).days
    interest1 = (Decimal(7000000) * (1 + rate1) ** (Decimal(days1) / 365)
                - Decimal(7000000)).quantize(Decimal("0.01"))
    rate2 = Decimal("12") / 100
    days2 = (as_of - date(2026, 6, 8)).days
    interest2 = (Decimal(5000000) * (1 + rate2) ** (Decimal(days2) / 365)
                - Decimal(5000000)).quantize(Decimal("0.01"))

    assert Decimal(result["by_currency"]["INR"]["projected_interest"]) == interest1 + interest2
    assert result["by_currency"]["INR"]["outstanding_principal"] == "12000000"
    assert len(result["contracts"]) == 2
    assert {c["rate_percent_pa"] for c in [x["inputs"] for x in result["contracts"]]} == {"10.2", "12"}


def test_the_currency_total_equals_the_sum_of_the_contract_lines(paths, run):
    _two_lender_scenario(run)
    entries, contracts, blue_moon = _entity(paths, "bluemoon")
    result = interest.project(entries, blue_moon, date(2027, 4, 1), paths.root, contracts=contracts)
    lines = sum(Decimal(c["projected_interest"]) for c in result["contracts"])
    assert Decimal(result["by_currency"]["INR"]["projected_interest"]) == lines


def test_a_rate_with_a_decimal_point_is_encoded_into_a_valid_account_name(paths, run):
    from beancount.core import account as beancount_account
    assert encode_rate("10.2") == "10p2"
    _two_lender_scenario(run)
    entries, contracts, blue_moon = _entity(paths, "bluemoon")
    casey_contract = next(c for c in contracts if c.lender_slug == "CaseyNolan")
    assert beancount_account.is_valid(casey_contract.account)
    assert "10p2" in casey_contract.account
    run("check")


def test_a_borrower_balance_sums_every_lenders_contract(paths, run):
    _two_lender_scenario(run)
    entries, contracts, blue_moon = _entity(paths, "bluemoon")
    result = queries.balance(entries, blue_moon, contracts, None, paths.root)
    assert result["payable"] == {"INR": "12000000"}
    assert result["counts"]["borrowed"] == 2
    assert len(result["contracts"]) == 2


def test_filtering_by_lender_returns_only_that_lenders_contract(paths, run):
    _two_lender_scenario(run)
    entries, contracts, blue_moon = _entity(paths, "bluemoon")
    result = queries.balance(entries, blue_moon, contracts, None, paths.root, lender="CaseyNolan")
    assert result["payable"] == {"INR": "7000000"}
    assert result["counts"]["borrowed"] == 1


def test_portfolio_aggregates_by_lender_and_by_borrower(paths, run):
    _two_lender_scenario(run)
    entries, contracts, _ = _entity(paths, "bluemoon")
    by_borrower = queries.portfolio(entries, contracts, borrower="BlueMoonJewellers", root=paths.root)
    assert by_borrower["totals"]["receivable"] == {"INR": "12000000"}
    assert by_borrower["counts"]["contracts"] == 2

    by_lender = queries.portfolio(entries, contracts, lender="CaseyNolan", root=paths.root)
    assert by_lender["totals"]["receivable"] == {"INR": "7000000"}
    assert by_lender["counts"]["contracts"] == 1

    household = queries.portfolio(entries, contracts, root=paths.root)
    assert household["totals"]["receivable"] == {"INR": "12000000"}
    assert household["counts"]["contracts"] == 2


def test_adding_with_one_contract_uses_it_without_being_told(paths, run):
    run("entity", "add", "--name", "Alex Rivera", "--aliases", "me",
        "--currency", "INR", "--book", "--self")
    run("entity", "add", "--name", "Ashok Gupta", "--aliases", "ashok", "--currency", "INR")
    run("contract", "add", "--lender", "me", "--borrower", "ashok", "--rate", "5",
        "--started", "2026-01-01")
    run("add", "--kind", "interest", "--who", "ashok", "--amount", "10",
        "--date", "2026-06-01", "--no-commit")
    entries, contracts, ashok = _entity(paths, "ashok")
    result = queries.balance(entries, ashok, contracts, None, paths.root)
    assert result["interest_paid"] == {"INR": "10"}


def test_adding_when_several_contracts_exist_lists_them_instead_of_guessing(paths, run):
    run("entity", "add", "--name", "Alex Rivera", "--aliases", "me",
        "--currency", "INR", "--book", "--self")
    run("entity", "add", "--name", "Blue Moon Jewellers", "--aliases", "bluemoon", "--currency", "INR")
    run("contract", "add", "--lender", "me", "--borrower", "bluemoon", "--rate", "12",
        "--started", "2026-06-08")
    run("contract", "add", "--lender", "me", "--borrower", "bluemoon", "--rate", "8",
        "--started", "2026-08-01")
    run("add", "--kind", "lend", "--who", "bluemoon", "--amount", "100", "--no-commit", expect=1)


def test_the_same_person_can_lend_in_one_contract_and_borrow_in_another(paths, run):
    run("entity", "add", "--name", "Alex Rivera", "--aliases", "me",
        "--currency", "INR", "--book", "--self")
    run("entity", "add", "--name", "Rahul Kumar", "--aliases", "rahul", "--currency", "INR")
    run("contract", "add", "--lender", "me", "--borrower", "rahul", "--rate", "5",
        "--started", "2026-01-01")
    run("contract", "add", "--lender", "rahul", "--borrower", "me", "--rate", "6",
        "--started", "2026-02-01")
    run("add", "--kind", "lend", "--who", "rahul", "--amount", "1000",
        "--date", "2026-01-01", "--no-commit")
    run("add", "--kind", "borrow", "--who", "rahul", "--amount", "400",
        "--date", "2026-02-01", "--no-commit")
    entries, contracts, rahul = _entity(paths, "rahul")
    result = queries.balance(entries, rahul, contracts, None, paths.root)
    # Rahul borrowed 1000 on the first contract, and lent 400 back on the second.
    assert result["payable"] == {"INR": "1000"}
    assert result["receivable"] == {"INR": "400"}
    assert result["net"] == {"INR": "-600"}


def test_a_contract_where_the_owner_borrows_posts_to_a_liability(paths, run):
    run("entity", "add", "--name", "Alex Rivera", "--aliases", "me",
        "--currency", "INR", "--book", "--self")
    run("entity", "add", "--name", "Madan Lal Agarwal", "--aliases", "fil", "--currency", "INR")
    run("contract", "add", "--lender", "fil", "--borrower", "me", "--rate", "9",
        "--method", "compound", "--compounding", "annual", "--started", "2026-04-01")
    run("add", "--kind", "borrow", "--who", "fil", "--amount", "8500000",
        "--date", "2026-04-01", "--no-commit")
    text = (paths.ledger_dir / "2026.beancount").read_text(encoding="utf-8")
    assert "Liabilities:Owed:" in text
    entries, contracts, fil = _entity(paths, "fil")
    result = queries.balance(entries, fil, contracts, None, paths.root)
    assert result["receivable"] == {"INR": "8500000"}


def test_interest_paid_on_a_borrowed_contract_is_an_expense_not_negative_income(paths, run):
    run("entity", "add", "--name", "Alex Rivera", "--aliases", "me",
        "--currency", "INR", "--book", "--self")
    run("entity", "add", "--name", "Madan Lal Agarwal", "--aliases", "fil", "--currency", "INR")
    run("contract", "add", "--lender", "fil", "--borrower", "me", "--rate", "9",
        "--started", "2026-04-01")
    run("add", "--kind", "borrow", "--who", "fil", "--amount", "1000000",
        "--date", "2026-04-01", "--no-commit")
    run("add", "--kind", "interest", "--who", "fil", "--amount", "5000",
        "--date", "2026-10-01", "--no-commit")
    text = (paths.ledger_dir / "2026.beancount").read_text(encoding="utf-8")
    assert "Expenses:Interest:" in text
    entries, contracts, fil = _entity(paths, "fil")
    result = queries.balance(entries, fil, contracts, None, paths.root)
    assert result["interest_received"] == {"INR": "5000"}


def test_a_contract_between_two_strangers_is_refused(paths, run):
    run("entity", "add", "--name", "Alex Rivera", "--aliases", "me",
        "--currency", "INR", "--book", "--self")
    run("entity", "add", "--name", "Rahul Kumar", "--aliases", "rahul", "--currency", "INR")
    run("entity", "add", "--name", "Blue Moon Jewellers", "--aliases", "bluemoon", "--currency", "INR")
    run("contract", "add", "--lender", "rahul", "--borrower", "bluemoon", "--rate", "5",
        "--started", "2026-01-01", expect=1)


def test_a_household_total_does_not_double_count_a_loan_between_two_books(paths, run):
    run("entity", "add", "--name", "Alex Rivera", "--aliases", "me",
        "--currency", "INR", "--book", "--self")
    run("entity", "add", "--name", "Casey Nolan", "--aliases", "casey",
        "--currency", "INR", "--book")
    run("contract", "add", "--lender", "me", "--borrower", "casey", "--rate", "0",
        "--started", "2026-01-01")
    run("add", "--kind", "lend", "--who", "casey", "--amount", "1000",
        "--date", "2026-01-01", "--no-commit")
    entries, contracts, _ = _entity(paths, "casey")
    household = queries.portfolio(entries, contracts, root=paths.root)
    assert household["internal"]["contracts"] == 1
    assert household["totals"]["receivable"] == {"INR": "1000"}
    owner_view = queries.portfolio(entries, contracts, owner="AlexRivera", root=paths.root)
    assert owner_view["totals"]["receivable"] == {"INR": "1000"}


def test_cash_is_scoped_to_the_book_that_paid(paths, run):
    _two_lender_scenario(run)
    text = (paths.ledger_dir / "2026.beancount").read_text(encoding="utf-8")
    assert "Assets:Cash:CaseyNolan:INR" in text
    assert "Assets:Cash:AlexRivera:INR" in text


def test_old_kind_names_still_work(paths, run):
    run("entity", "add", "--name", "Alex Rivera", "--aliases", "me",
        "--currency", "INR", "--book", "--self")
    run("entity", "add", "--name", "Someone", "--aliases", "them", "--currency", "INR")
    run("contract", "add", "--lender", "me", "--borrower", "them", "--rate", "5",
        "--started", "2026-01-01")
    run("add", "--kind", "lend", "--who", "them", "--amount", "100",
        "--date", "2026-01-01", "--no-commit")
    run("add", "--kind", "repay", "--who", "them", "--amount", "40",
        "--date", "2026-02-01", "--no-commit")
    entries, contracts, them = _entity(paths, "them")
    result = queries.balance(entries, them, contracts, None, paths.root)
    assert result["payable"] == {"INR": "60"}


def test_an_old_kind_that_contradicts_the_contract_direction_is_refused(paths, run):
    run("entity", "add", "--name", "Alex Rivera", "--aliases", "me",
        "--currency", "INR", "--book", "--self")
    run("entity", "add", "--name", "Someone", "--aliases", "them", "--currency", "INR")
    run("contract", "add", "--lender", "them", "--borrower", "me", "--rate", "5",
        "--started", "2026-01-01")
    # "them" is the lender on this contract, so "lend" (which asserts *you* lend
    # to "who") contradicts it.
    run("add", "--kind", "lend", "--who", "them", "--amount", "100",
        "--date", "2026-01-01", "--no-commit", expect=1)


def test_a_contract_opened_but_never_drawn_on_still_appears(paths, run):
    run("entity", "add", "--name", "Alex Rivera", "--aliases", "me",
        "--currency", "INR", "--book", "--self")
    run("entity", "add", "--name", "Someone", "--aliases", "them", "--currency", "INR")
    run("contract", "add", "--lender", "me", "--borrower", "them", "--rate", "5",
        "--started", "2026-01-01")
    entries, contracts, them = _entity(paths, "them")
    assert len(contracts) == 1
    result = queries.balance(entries, them, contracts, None, paths.root)
    assert result["contracts"][0]["contract"] == contracts[0].contract_id
