"""A loan contract: who lent whom, on what terms, as its own record.

An entity is just a name (see `entities.py`). A contract is the thing that
carries interest terms and owns a set of transactions, so the same borrower
can hold several contracts -- from different lenders, at different rates,
started on different dates -- without them colliding.

The account PATH is owner-first (`Assets:Loans:<Owner>:<Counterparty>:<Id>` or
`Liabilities:Owed:<Owner>:<Counterparty>:<Id>`) and is a derived address, never
parsed to decide anything -- the `open` directive's metadata is the only
source of truth. "Owner" is whichever party's book this ledger keeps; if both
parties are book owners the contract is still recorded once, in the lender's
book, and flagged `internal`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from beancount.core import account as beancount_account

from .entities import Entity, book_owners
from .store import (
    CASH_ROOT, EXPENSE_INTEREST_ROOT, INTEREST_ROOT, LOANS_ROOT, OWED_ROOT,
    LedgerError, Paths, append_block, cite, opens,
)

PERIODS_PER_YEAR = {
    "annual": 1, "annually": 1, "yearly": 1,
    "semiannual": 2, "semiannually": 2, "halfyearly": 2,
    "quarterly": 4, "monthly": 12, "daily": 365,
}
DAY_COUNT_BASIS = {"actual/365": 365, "actual/360": 360, "act/365": 365, "act/360": 360}


@dataclass
class Contract:
    contract_id: str
    lender_slug: str
    borrower_slug: str
    owner_slug: str
    counterparty_slug: str
    is_internal: bool = False
    rate_percent_pa: str = "0"
    method: str = "simple"
    compounding: str = ""
    day_count: str = "actual/365"
    started: str = ""
    currency: str = ""
    note: str = ""
    citation: str = ""

    @property
    def direction(self) -> str:
        """'receivable' when the owner is the lender, 'payable' when the borrower."""
        return "receivable" if self.owner_slug == self.lender_slug else "payable"

    @property
    def account(self) -> str:
        root = LOANS_ROOT if self.direction == "receivable" else OWED_ROOT
        return f"{root}:{self.owner_slug}:{self.counterparty_slug}:{self.contract_id}"

    @property
    def interest_account(self) -> str:
        root = INTEREST_ROOT if self.direction == "receivable" else EXPENSE_INTEREST_ROOT
        return f"{root}:{self.owner_slug}:{self.counterparty_slug}:{self.contract_id}"

    def cash_account(self, currency: str) -> str:
        return f"{CASH_ROOT}:{self.owner_slug}:{currency}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["account"] = self.account
        d["interest_account"] = self.interest_account
        d["direction"] = self.direction
        return d


def encode_rate(rate: str) -> str:
    """'10.2' -> '10p2', '9' -> '9'. Dots are illegal in a Beancount account name."""
    text = str(rate).strip()
    if not text:
        raise LedgerError("A contract needs a rate. Pass --rate, e.g. --rate 10.2 or --rate 0.")
    try:
        Decimal(text)
    except InvalidOperation as exc:
        raise LedgerError(f"{rate!r} is not a number I can record exactly.") from exc
    return text.replace(".", "p").replace("-", "n")


def make_contract_id(started: date, rate: str, taken: set[str]) -> str:
    base = f"{started.isoformat()}-{encode_rate(rate)}"
    if base not in taken:
        return base
    suffix = ord("b")
    while True:
        candidate = f"{base}-{chr(suffix)}"
        if candidate not in taken:
            return candidate
        suffix += 1


def _resolve_owner(lender: Entity, borrower: Entity, entities: list[Entity]) -> tuple[str, str]:
    """Whichever party is a book owner becomes `owner`; the other is the counterparty.

    Enforces the scope invariant: every contract needs a book owner on one side.
    """
    if lender.book:
        return lender.slug, borrower.slug
    if borrower.book:
        return borrower.slug, lender.slug
    owners = book_owners(entities)
    if owners:
        names = " and ".join(f"{o.name!r} ({o.citation})" for o in owners)
        hint = (
            f"This ledger keeps books for: {names}. Name one of them as --lender or "
            f"--borrower, or start keeping a third book with:\n"
            f'  ledger entity book "{borrower.name}" --on'
        )
    else:
        hint = (
            "This ledger keeps no books yet, so a contract has no owner. Mark yourself with:\n"
            '  ledger entity add --name "<your full name>" --aliases "me" --book --self\n'
            "or, if you are already on record:\n"
            f'  ledger entity book "{lender.name}" --on'
        )
    raise LedgerError(
        f"Neither {lender.name!r} nor {borrower.name!r} is a book this ledger keeps, "
        f"so there is no one to record this contract for. {hint}"
    )


def build_contract(lender: Entity, borrower: Entity, entities: list[Entity], *,
                   rate: str, started: date, method: str = "", compounding: str = "",
                   day_count: str = "", currency: str = "", contract_id: str = "",
                   note: str = "", taken_ids: frozenset = frozenset()) -> Contract:
    """Validate terms and derive the account. Raises LedgerError on anything wrong."""
    owner_slug, counterparty_slug = _resolve_owner(lender, borrower, entities)

    rate_str = str(rate).strip() or "0"
    try:
        Decimal(rate_str)
    except InvalidOperation as exc:
        raise LedgerError(f"{rate!r} is not a number I can record exactly.") from exc

    method = (method or "simple").strip().lower()
    if method not in ("simple", "compound"):
        raise LedgerError(f"method must be 'simple' or 'compound', found {method!r}.")

    compounding = (compounding or "").strip().lower()
    if method == "compound" and not compounding:
        compounding = "annual"
    if method == "compound" and compounding not in PERIODS_PER_YEAR:
        raise LedgerError(f"Unsupported compounding {compounding!r}.")

    day_count = (day_count or "actual/365").strip().lower()
    if day_count not in DAY_COUNT_BASIS:
        raise LedgerError(f"Unsupported day_count {day_count!r}.")

    cid = contract_id.strip() if contract_id else make_contract_id(started, rate_str, set(taken_ids))

    contract = Contract(
        contract_id=cid, lender_slug=lender.slug, borrower_slug=borrower.slug,
        owner_slug=owner_slug, counterparty_slug=counterparty_slug,
        is_internal=lender.book and borrower.book,
        rate_percent_pa=rate_str, method=method, compounding=compounding,
        day_count=day_count, started=started.isoformat(),
        currency=(currency or "").upper(), note=note,
    )
    if not beancount_account.is_valid(contract.account):
        raise LedgerError(
            f"{contract.account!r} is not a valid account name. Check the rate and names "
            "for characters Beancount cannot use in an account component."
        )
    return contract


def format_open_directive(contract: Contract) -> str:
    """Render the `open` directive that *is* the contract record."""
    lines = [f"{contract.started} open {contract.account}"]
    def meta(key: str, value) -> None:
        if value:
            lines.append(f'  {key}: "{value}"')
    meta("lender", contract.lender_slug)
    meta("borrower", contract.borrower_slug)
    meta("rate_percent_pa", contract.rate_percent_pa)
    meta("method", contract.method)
    meta("compounding", contract.compounding)
    meta("day_count", contract.day_count)
    meta("started", contract.started)
    meta("currency", contract.currency)
    meta("note", contract.note)
    if contract.is_internal:
        meta("internal", "true")
    return "\n".join(lines)


def add_contract(p: Paths, contract: Contract, existing: list[Contract]) -> dict:
    """Append a new contract record. Refuses to create a duplicate account."""
    if any(c.account == contract.account for c in existing):
        raise LedgerError(f"A contract already exists at {contract.account}.")
    block = format_open_directive(contract)
    line = append_block(p.accounts, block)
    return {
        "created": contract.to_dict(),
        "citation": f"{p.accounts.name}:{line}",
        "block": block,
    }


def load_contracts(entries, entities: list[Entity], root: Path | None = None,
                   ) -> tuple[list[Contract], list[dict]]:
    """Every `open` under Assets:Loans:/Liabilities:Owed: carrying `lender` is a contract.

    Tolerant by design: a malformed or invariant-violating record comes back in
    `problems` rather than raising, because `store.load()` runs on every command
    and a hand-edited file must stay readable enough to explain what is wrong.
    """
    by_slug = {e.slug: e for e in entities}
    contracts: list[Contract] = []
    problems: list[dict] = []

    for directive in opens(entries):
        meta = directive.meta or {}
        lender_slug = meta.get("lender")
        borrower_slug = meta.get("borrower")
        if not lender_slug or not borrower_slug:
            continue
        account = directive.account
        citation = cite(meta, root)
        if not (account.startswith(LOANS_ROOT + ":") or account.startswith(OWED_ROOT + ":")):
            problems.append({
                "account": account, "citation": citation,
                "problem": f"carries lender/borrower metadata but is not under "
                           f"{LOANS_ROOT} or {OWED_ROOT}",
            })
            continue
        parts = account.split(":")
        if len(parts) != 5:
            problems.append({
                "account": account, "citation": citation,
                "problem": "does not have the four Owner:Counterparty:ContractId "
                           "components a contract account needs",
            })
            continue
        path_owner, path_cp, contract_id = parts[2], parts[3], parts[4]
        lender = by_slug.get(str(lender_slug))
        borrower = by_slug.get(str(borrower_slug))
        if lender is None or borrower is None:
            problems.append({
                "account": account, "citation": citation,
                "problem": f"lender {lender_slug!r} or borrower {borrower_slug!r} "
                           "does not resolve to a known entity",
            })
            continue
        try:
            owner_slug, counterparty_slug = _resolve_owner(lender, borrower, entities)
        except LedgerError as exc:
            problems.append({"account": account, "citation": citation, "problem": str(exc)})
            continue
        contract = Contract(
            contract_id=contract_id, lender_slug=str(lender_slug), borrower_slug=str(borrower_slug),
            owner_slug=owner_slug, counterparty_slug=counterparty_slug,
            is_internal=lender.book and borrower.book,
            rate_percent_pa=str(meta.get("rate_percent_pa", "0")),
            method=str(meta.get("method", "simple")).lower(),
            compounding=str(meta.get("compounding", "")).lower(),
            day_count=str(meta.get("day_count", "actual/365")).lower(),
            started=str(meta.get("started", directive.date.isoformat())),
            currency=str(meta.get("currency", "")).upper(),
            note=str(meta.get("note", "")),
            citation=citation,
        )
        if path_owner != owner_slug or path_cp != counterparty_slug:
            problems.append({
                "account": account, "citation": citation,
                "problem": f"account path does not match its lender/borrower metadata "
                           f"(expected {contract.account})",
            })
            continue
        contracts.append(contract)
    return contracts, problems


def find_contracts(contracts: list[Contract], *, lender: str = "", borrower: str = "",
                   owner: str = "", contract_id: str = "", currency: str = "") -> list[Contract]:
    """Filter contracts by slug (already-resolved) on any combination of fields."""
    out = contracts
    if lender:
        out = [c for c in out if c.lender_slug == lender]
    if borrower:
        out = [c for c in out if c.borrower_slug == borrower]
    if owner:
        out = [c for c in out if c.owner_slug == owner]
    if contract_id:
        out = [c for c in out if c.contract_id == contract_id or c.account == contract_id]
    if currency:
        out = [c for c in out if not c.currency or c.currency == currency]
    return out
