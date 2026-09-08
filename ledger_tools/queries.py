"""Balances, counts, statements and search.

Every figure is summed by currency and never across currencies. Every row
carries the file and line it came from, so any answer can be checked by hand.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount.core import data

from .entities import Entity
from .store import cite, transactions


def _amounts_to_dict(totals: dict[str, Decimal]) -> dict[str, str]:
    """Render per-currency totals as strings, dropping exact zeroes."""
    return {c: str(v) for c, v in sorted(totals.items()) if v != 0}


def _touching(entries, accounts: set[str], as_of: date | None):
    """Transactions with at least one posting to one of `accounts`, date-filtered."""
    out = []
    for txn in transactions(entries):
        if as_of is not None and txn.date > as_of:
            continue
        if any(p.account in accounts for p in txn.postings):
            out.append(txn)
    return out


def _person_accounts(entity: Entity, entries) -> dict[str, set[str]]:
    """Group this entity's accounts by role, including any sub-accounts."""
    roles = {
        "loans": entity.loans_account,
        "owed": entity.owed_account,
        "interest": entity.interest_account,
    }
    found: dict[str, set[str]] = {k: set() for k in roles}
    seen = set()
    for txn in transactions(entries):
        for p in txn.postings:
            seen.add(p.account)
    for role, prefix in roles.items():
        for account in seen:
            if account == prefix or account.startswith(prefix + ":"):
                found[role].add(account)
    return found


def balance(entries, entity: Entity, as_of: date | None = None, root: Path | None = None) -> dict:
    """What this person owes, what you owe them, interest received, and the counts."""
    accounts = _person_accounts(entity, entries)
    all_accounts = set().union(*accounts.values()) if accounts else set()

    owed_to_you: dict[str, Decimal] = defaultdict(Decimal)
    you_owe: dict[str, Decimal] = defaultdict(Decimal)
    interest: dict[str, Decimal] = defaultdict(Decimal)
    lent_total: dict[str, Decimal] = defaultdict(Decimal)
    repaid_total: dict[str, Decimal] = defaultdict(Decimal)
    counts = {"lent": 0, "repaid": 0, "borrowed": 0, "repaid_to_them": 0, "interest_received": 0}
    citations: list[str] = []

    for txn in _touching(entries, all_accounts, as_of):
        c = cite(txn.meta, root)
        used = False
        for p in txn.postings:
            if p.units is None:
                continue
            n, cur = p.units.number, p.units.currency
            if p.account in accounts["loans"]:
                owed_to_you[cur] += n
                used = True
                if n > 0:
                    counts["lent"] += 1
                    lent_total[cur] += n
                elif n < 0:
                    counts["repaid"] += 1
                    repaid_total[cur] += -n
            elif p.account in accounts["owed"]:
                you_owe[cur] += -n
                used = True
                if n < 0:
                    counts["borrowed"] += 1
                elif n > 0:
                    counts["repaid_to_them"] += 1
            elif p.account in accounts["interest"]:
                interest[cur] += -n
                used = True
                if n < 0:
                    counts["interest_received"] += 1
        if used:
            citations.append(c)

    return {
        "entity": entity.name,
        "slug": entity.slug,
        "as_of": (as_of or date.today()).isoformat(),
        "owed_to_you": _amounts_to_dict(owed_to_you),
        "you_owe": _amounts_to_dict(you_owe),
        "interest_received": _amounts_to_dict(interest),
        "principal_lent_total": _amounts_to_dict(lent_total),
        "principal_repaid_total": _amounts_to_dict(repaid_total),
        "counts": counts,
        "citations": citations,
        "note": "Amounts are per currency and never converted or added together.",
    }


def statement(entries, entity: Entity, as_of: date | None = None, root: Path | None = None) -> dict:
    """Chronological rows with a running per-currency balance of what they owe you."""
    accounts = _person_accounts(entity, entries)
    all_accounts = set().union(*accounts.values()) if accounts else set()
    running: dict[str, Decimal] = defaultdict(Decimal)
    rows = []
    for txn in sorted(_touching(entries, all_accounts, as_of), key=lambda t: (t.date, t.meta.get("lineno", 0))):
        for p in txn.postings:
            if p.account not in all_accounts or p.units is None:
                continue
            n, cur = p.units.number, p.units.currency
            role = next((r for r, s in accounts.items() if p.account in s), "")
            if role == "loans":
                running[cur] += n
            rows.append({
                "date": txn.date.isoformat(),
                "narration": txn.narration,
                "payee": txn.payee or "",
                "account": p.account,
                "role": role,
                "amount": str(n),
                "currency": cur,
                "balance_owed_to_you": str(running[cur]) if role == "loans" else "",
                "source": str(txn.meta.get("source", "")),
                "citation": cite(txn.meta, root),
            })
    return {
        "entity": entity.name,
        "slug": entity.slug,
        "as_of": (as_of or date.today()).isoformat(),
        "rows": rows,
        "final_balance_owed_to_you": _amounts_to_dict(running),
    }


def search(entries, needle: str, root: Path | None = None, limit: int = 50) -> dict:
    """Substring search over narration, payee and the recorded source phrase."""
    q = needle.strip().casefold()
    hits = []
    for txn in transactions(entries):
        haystack = " ".join([
            txn.narration or "", txn.payee or "", str(txn.meta.get("source", "")),
            *[p.account for p in txn.postings],
        ]).casefold()
        if q in haystack:
            hits.append({
                "date": txn.date.isoformat(),
                "narration": txn.narration,
                "payee": txn.payee or "",
                "amounts": [
                    f"{p.units.number} {p.units.currency} {p.account}"
                    for p in txn.postings if p.units is not None
                ],
                "source": str(txn.meta.get("source", "")),
                "citation": cite(txn.meta, root),
            })
    hits.sort(key=lambda h: h["date"], reverse=True)
    return {"query": needle, "matches": len(hits), "results": hits[:limit]}


def find_duplicates(entries, account: str, amount: Decimal, currency: str,
                    on: date, window_days: int = 3, root: Path | None = None) -> list[dict]:
    """Same account, same amount and currency, within +/- window_days."""
    out = []
    for txn in transactions(entries):
        if abs((txn.date - on).days) > window_days:
            continue
        for p in txn.postings:
            if (p.account == account and p.units is not None
                    and p.units.currency == currency and p.units.number == amount):
                out.append({
                    "date": txn.date.isoformat(),
                    "narration": txn.narration,
                    "amount": f"{amount} {currency}",
                    "account": account,
                    "citation": cite(txn.meta, root),
                })
    return out
