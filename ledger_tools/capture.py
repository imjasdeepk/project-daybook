"""Recording something in the ledger.

Every write is validated by Beancount before it is kept. A write that does not
parse is rolled back rather than left behind, and nothing is ever edited in
place: a correction is a new, reversing entry.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from beancount import loader

from .entities import Entity
from .queries import find_duplicates
from .store import (
    CASH_ROOT, LedgerError, Paths, append_block, cite, ensure_year_file,
    git_commit, revert_files,
)

# kind -> (debit account template, credit account template, human description)
KINDS = {
    "lend":       ("loans", "cash",     "Money you handed over that is owed back"),
    "repay":      ("cash",  "loans",    "Principal coming back to you"),
    "interest":   ("cash",  "interest", "Interest you actually received"),
    "borrow":     ("cash",  "owed",     "Money you took that you owe back"),
    "repay-them": ("owed",  "cash",     "Principal you paid back"),
    "spend":      ("expense", "cash",   "Money spent"),
    "receive":    ("cash",  "income",   "Money received that is not a loan"),
}


def parse_amount(raw: str) -> Decimal:
    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise LedgerError(f"{raw!r} is not a number I can record exactly.") from exc
    if value <= 0:
        raise LedgerError("Amount must be greater than zero; direction is set by the kind.")
    return value


def _account_for(role: str, entity: Entity | None, currency: str, category: str) -> str:
    if role == "cash":
        return f"{CASH_ROOT}:{currency}"
    if role == "expense":
        return f"Expenses:{category or 'Uncategorised'}"
    if role == "income":
        return f"Income:{category or 'Other'}"
    if entity is None:
        raise LedgerError(f"A {role} entry needs a person or organisation.")
    return {"loans": entity.loans_account, "owed": entity.owed_account,
            "interest": entity.interest_account}[role]


def plan_entry(kind: str, *, entity: Entity | None, amount: Decimal, currency: str,
               when: date, narration: str, source: str, category: str = "") -> dict:
    """Work out the two postings without writing anything."""
    if kind not in KINDS:
        raise LedgerError(f"Unknown kind {kind!r}. Use one of: {', '.join(KINDS)}.")
    debit_role, credit_role, _ = KINDS[kind]
    debit = _account_for(debit_role, entity, currency, category)
    credit = _account_for(credit_role, entity, currency, category)
    return {
        "kind": kind, "date": when, "amount": amount, "currency": currency,
        "narration": narration, "source": source,
        "payee": entity.name if entity else "",
        "postings": [(debit, amount), (credit, -amount)],
        "primary_account": debit if debit_role != "cash" else credit,
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
        raise LedgerError(
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
        "commit": sha,
    }


def reverse_entry(p: Paths, plan: dict, voids_citation: str, *, commit: bool = True) -> dict:
    """A correction: a new entry that undoes an earlier one and says which."""
    flipped = dict(plan)
    flipped["postings"] = [(a, -v) for a, v in plan["postings"]]
    flipped["narration"] = f"Voids {voids_citation}: {plan['narration']}"
    flipped["source"] = f"correction of {voids_citation}. {plan['source']}".strip()
    return commit_entry(p, flipped, commit=commit)
