"""Recording something in the ledger.

Every write is validated by Beancount before it is kept. A write that does not
parse is rolled back rather than left behind, and nothing is ever edited in
place: a correction is a new, reversing entry.

Direction (who is the lender, who is the borrower) lives on the contract, not
on the entry kind, so `principal` and `repayment` cover both directions --
lending out and borrowing are both "principal moving under a contract", just
mirrored. The old `lend`/`repay`/`borrow`/`repay-them` names still work and
now double as an assertion: if the name's direction disagrees with the
contract's, the entry is refused rather than silently reinterpreted.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from beancount import loader

from .contracts import Contract
from .entities import Entity
from .queries import find_duplicates
from .store import (
    CASH_ROOT, DaybookError, Paths, append_block, cite, ensure_year_file,
    git_commit, revert_files,
)

KINDS = {
    "principal": "Money moving under a contract: lent out, or taken on",
    "repayment": "Principal moving back the other way",
    "interest":  "Interest actually paid or received. Never interest that merely accrued.",
    "spend":     "Money spent",
    "receive":   "Money received that is not a loan",
}

# old name -> (new kind, the direction it asserts: which side the book owner is on)
LEGACY_KINDS = {
    "lend":       ("principal", "receivable"),
    "repay":      ("repayment", "receivable"),
    "borrow":     ("principal", "payable"),
    "repay-them": ("repayment", "payable"),
}

ALL_KINDS = sorted({*KINDS, *LEGACY_KINDS})


def parse_amount(raw: str) -> Decimal:
    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise DaybookError(f"{raw!r} is not a number I can record exactly.") from exc
    if value <= 0:
        raise DaybookError("Amount must be greater than zero; direction is set by the kind.")
    return value


def _accounts_for(kind: str, contract: Contract | None, owner: Entity | None,
                  currency: str, category: str) -> tuple[str, str, str]:
    """Returns (debit_account, credit_account, primary_account)."""
    if kind in ("spend", "receive"):
        if owner is None:
            raise DaybookError(f"A {kind} entry needs an owner's book. Pass --book.")
        cash = f"{CASH_ROOT}:{owner.slug}:{currency}"
        if kind == "spend":
            return f"Expenses:{category or 'Uncategorised'}", cash, cash
        return cash, f"Income:{category or 'Other'}", cash

    if contract is None:
        raise DaybookError(f"A {kind} entry needs a contract. Pass --contract, or --lender "
                          "and --borrower if the pair has exactly one.")
    cash = contract.cash_account(currency)
    receivable = contract.direction == "receivable"
    if kind == "principal":
        return (contract.account, cash, contract.account) if receivable \
            else (cash, contract.account, contract.account)
    if kind == "repayment":
        return (cash, contract.account, contract.account) if receivable \
            else (contract.account, cash, contract.account)
    if kind == "interest":
        return (cash, contract.interest_account, contract.account) if receivable \
            else (contract.interest_account, cash, contract.account)
    raise DaybookError(f"Unknown kind {kind!r}. Use one of: {', '.join(KINDS)}.")


def plan_entry(kind: str, *, contract: Contract | None = None, owner: Entity | None = None,
              counterparty: Entity | None = None, amount: Decimal, currency: str,
              when: date, narration: str, source: str, category: str = "") -> dict:
    """Work out the two postings without writing anything."""
    if kind not in KINDS and kind not in LEGACY_KINDS:
        raise DaybookError(f"Unknown kind {kind!r}. Use one of: {', '.join(ALL_KINDS)}.")

    resolved_kind = kind
    if kind in LEGACY_KINDS:
        resolved_kind, asserted_direction = LEGACY_KINDS[kind]
        if contract is not None and contract.direction != asserted_direction:
            wrong_side = "lender" if contract.direction == "receivable" else "borrower"
            right_kind = "principal" if resolved_kind == "principal" else "repayment"
            raise DaybookError(
                f"You said --kind {kind!r}, but contract {contract.contract_id} "
                f"({contract.citation}) has the book owner as the {wrong_side}. "
                f"Use --kind {right_kind}, or name a different contract with --contract."
            )

    debit, credit, primary = _accounts_for(resolved_kind, contract, owner, currency, category)
    return {
        "kind": resolved_kind, "date": when, "amount": amount, "currency": currency,
        "narration": narration, "source": source,
        "payee": counterparty.name if counterparty else "",
        "postings": [(debit, amount), (credit, -amount)],
        "primary_account": primary,
        "contract": contract.contract_id if contract else "",
        "direction": contract.direction if contract else "",
    }


def render_entry(plan: dict) -> str:
    payee = f' "{plan["payee"]}"' if plan["payee"] else ""
    lines = [f'{plan["date"].isoformat()} *{payee} "{plan["narration"]}"']
    if plan["source"]:
        escaped = plan["source"].replace('"', "'")
        lines.append(f'  source: "{escaped}"')
    width = max(len(a) for a, _ in plan["postings"]) + 2
    for account, value in plan["postings"]:
        lines.append(f"  {account.ljust(width)}{value:>14} {plan['currency']}")
    return "\n".join(lines)


def check_duplicates(entries, plan: dict, root: Path | None = None, window_days: int = 3) -> list[dict]:
    return find_duplicates(
        entries, plan["primary_account"], plan["amount"], plan["currency"],
        plan["date"], window_days=window_days, root=root,
    )


def commit_entry(p: Paths, plan: dict, *, commit: bool = True) -> dict:
    """Append, validate with Beancount, roll back on failure, then commit."""
    target = ensure_year_file(p, plan["date"].year)
    block = render_entry(plan)
    line = append_block(target, block)

    _, errors, _ = loader.load_file(str(p.main))
    if errors:
        revert_files(p.root, [target, p.main])
        rendered = "\n".join(f"  {cite(e.source)}: {e.message}" for e in errors[:10])
        raise DaybookError(
            "Beancount rejected that entry, so nothing was saved:\n" + rendered
        )

    citation = f"{target.name}:{line}"
    sha = git_commit(p.root, f"capture: {plan['narration']}", [target, p.main]) if commit else None
    return {
        "recorded": block,
        "citation": citation,
        "date": plan["date"].isoformat(),
        "amount": f"{plan['amount']} {plan['currency']}",
        "kind": plan["kind"],
        "contract": plan.get("contract", ""),
        "commit": sha,
    }


def reverse_entry(p: Paths, plan: dict, voids_citation: str, *, commit: bool = True) -> dict:
    """A correction: a new entry that undoes an earlier one and says which."""
    flipped = dict(plan)
    flipped["postings"] = [(a, -v) for a, v in plan["postings"]]
    flipped["narration"] = f"Voids {voids_citation}: {plan['narration']}"
    flipped["source"] = f"correction of {voids_citation}. {plan['source']}".strip()
    return commit_entry(p, flipped, commit=commit)
