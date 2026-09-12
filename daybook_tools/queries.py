"""Balances, counts, statements, search and cross-contract aggregation.

Every figure is summed by currency and never across currencies. Every row
carries the file and line it came from, so any answer can be checked by hand.

An entity can be a lender in one contract and a borrower in another, so
`balance`/`statement` report figures relative to the *entity being asked
about*, not to any one fixed "you": `receivable` is money owed TO that entity,
`payable` is money that entity owes. `portfolio` aggregates across any set of
contracts -- one contract, a lender/borrower pair, everything for a borrower,
everything for a lender, or the whole household -- with the sum always done
here, in Decimal, never left for the assistant driving this tool to add up.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from .contracts import Contract
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


def _principal_delta(contract: Contract, n: Decimal) -> Decimal:
    """Normalise a raw posting number to a signed change in outstanding principal."""
    return n if contract.direction == "receivable" else -n


def _entity_contracts(entity: Entity, contracts: list[Contract], *, lender: str = "",
                      borrower: str = "", contract_id: str = "") -> list[Contract]:
    """Every contract this entity is party to, as lender or as borrower."""
    mine = [c for c in contracts if entity.slug in (c.lender_slug, c.borrower_slug)]
    if lender:
        mine = [c for c in mine if c.lender_slug == lender]
    if borrower:
        mine = [c for c in mine if c.borrower_slug == borrower]
    if contract_id:
        mine = [c for c in mine if c.contract_id == contract_id or c.account == contract_id]
    return mine


def balance(entries, entity: Entity, contracts: list[Contract], as_of: date | None = None,
           root: Path | None = None, *, lender: str = "", borrower: str = "",
           contract_id: str = "") -> dict:
    """What this entity is owed, what it owes, interest either way, and the counts."""
    mine = _entity_contracts(entity, contracts, lender=lender, borrower=borrower,
                             contract_id=contract_id)

    receivable: dict[str, Decimal] = defaultdict(Decimal)
    payable: dict[str, Decimal] = defaultdict(Decimal)
    interest_received: dict[str, Decimal] = defaultdict(Decimal)
    interest_paid: dict[str, Decimal] = defaultdict(Decimal)
    lent_total: dict[str, Decimal] = defaultdict(Decimal)
    repaid_total: dict[str, Decimal] = defaultdict(Decimal)
    borrowed_total: dict[str, Decimal] = defaultdict(Decimal)
    returned_total: dict[str, Decimal] = defaultdict(Decimal)
    counts = {"lent": 0, "repaid": 0, "borrowed": 0, "returned": 0,
             "interest_received": 0, "interest_paid": 0}
    citations: list[str] = []
    contract_rows: list[dict] = []

    for c in mine:
        role = "lender" if entity.slug == c.lender_slug else "borrower"
        outstanding: dict[str, Decimal] = defaultdict(Decimal)
        c_citations: list[str] = []
        for txn in _touching(entries, {c.account, c.interest_account}, as_of):
            cited = False
            for p in txn.postings:
                if p.units is None:
                    continue
                n, cur = p.units.number, p.units.currency
                if p.account == c.account:
                    delta = _principal_delta(c, n)
                    outstanding[cur] += delta
                    if role == "lender":
                        receivable[cur] += delta
                        if delta > 0:
                            counts["lent"] += 1
                            lent_total[cur] += delta
                        elif delta < 0:
                            counts["repaid"] += 1
                            repaid_total[cur] += -delta
                    else:
                        payable[cur] += delta
                        if delta > 0:
                            counts["borrowed"] += 1
                            borrowed_total[cur] += delta
                        elif delta < 0:
                            counts["returned"] += 1
                            returned_total[cur] += -delta
                    cited = True
                elif p.account == c.interest_account:
                    # The lender always receives interest and the borrower always
                    # pays it, regardless of which side happens to be the book
                    # owner (that only decided Income: vs Expenses: and the sign
                    # convention) -- there is only ever one interest leg per
                    # contract, so its magnitude is all that matters here.
                    if role == "lender":
                        interest_received[cur] += abs(n)
                        counts["interest_received"] += 1
                    else:
                        interest_paid[cur] += abs(n)
                        counts["interest_paid"] += 1
                    cited = True
            if cited:
                c_citations.append(cite(txn.meta, root))
        citations.extend(c_citations)
        contract_rows.append({
            "contract": c.contract_id, "account": c.account, "role": role,
            "counterparty": c.borrower_slug if role == "lender" else c.lender_slug,
            "outstanding_principal": _amounts_to_dict(outstanding),
            "rate_percent_pa": c.rate_percent_pa, "citations": c_citations,
        })

    net: dict[str, Decimal] = defaultdict(Decimal)
    for cur, v in receivable.items():
        net[cur] += v
    for cur, v in payable.items():
        net[cur] -= v

    return {
        "entity": entity.name,
        "slug": entity.slug,
        "as_of": (as_of or date.today()).isoformat(),
        "receivable": _amounts_to_dict(receivable),
        "payable": _amounts_to_dict(payable),
        "net": _amounts_to_dict(net),
        "interest_received": _amounts_to_dict(interest_received),
        "interest_paid": _amounts_to_dict(interest_paid),
        "principal_lent_total": _amounts_to_dict(lent_total),
        "principal_repaid_total": _amounts_to_dict(repaid_total),
        "principal_borrowed_total": _amounts_to_dict(borrowed_total),
        "principal_returned_total": _amounts_to_dict(returned_total),
        "counts": counts,
        "contracts": contract_rows,
        "citations": sorted(set(citations)),
        "note": "Amounts are per currency and never converted or added together. "
                "'receivable' is money owed TO this entity, 'payable' is money it owes.",
    }


def statement(entries, entity: Entity, contracts: list[Contract], as_of: date | None = None,
             root: Path | None = None, *, lender: str = "", borrower: str = "",
             contract_id: str = "") -> dict:
    """Chronological rows, with a running balance per contract and an overall net."""
    mine = _entity_contracts(entity, contracts, lender=lender, borrower=borrower,
                             contract_id=contract_id)
    by_account = {c.account: c for c in mine}
    accounts = set(by_account)
    all_accounts = accounts | {c.interest_account for c in mine}
    running_on_contract: dict[str, Decimal] = defaultdict(Decimal)
    running_net: dict[str, Decimal] = defaultdict(Decimal)
    rows = []
    for txn in sorted(_touching(entries, all_accounts, as_of),
                      key=lambda t: (t.date, t.meta.get("lineno", 0))):
        for p in txn.postings:
            if p.account not in all_accounts or p.units is None:
                continue
            n, cur = p.units.number, p.units.currency
            is_principal = p.account in accounts
            contract_id_out, role = "", ""
            balance_on_contract, running_total = "", ""
            if is_principal:
                c = by_account[p.account]
                role = "lender" if entity.slug == c.lender_slug else "borrower"
                delta = _principal_delta(c, n)
                key = f"{p.account}|{cur}"
                running_on_contract[key] += delta
                running_net[cur] += delta if role == "lender" else -delta
                contract_id_out = c.contract_id
                balance_on_contract = str(running_on_contract[key])
                running_total = str(running_net[cur])
            rows.append({
                "date": txn.date.isoformat(),
                "narration": txn.narration,
                "payee": txn.payee or "",
                "account": p.account,
                "contract": contract_id_out,
                "role": role,
                "amount": str(n),
                "currency": cur,
                "balance_on_contract": balance_on_contract,
                "running_net": running_total,
                "source": str(txn.meta.get("source", "")),
                "citation": cite(txn.meta, root),
            })
    return {
        "entity": entity.name,
        "slug": entity.slug,
        "as_of": (as_of or date.today()).isoformat(),
        "rows": rows,
        "final_net": _amounts_to_dict(running_net),
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


def find_duplicates(entries, accounts: str | set[str], amount: Decimal, currency: str,
                    on: date, window_days: int = 3, root: Path | None = None) -> list[dict]:
    """Same account (any in `accounts`), same amount and currency, within +/- window_days."""
    account_set = {accounts} if isinstance(accounts, str) else set(accounts)
    out = []
    for txn in transactions(entries):
        if abs((txn.date - on).days) > window_days:
            continue
        for p in txn.postings:
            if (p.account in account_set and p.units is not None
                    and p.units.currency == currency and p.units.number == amount):
                out.append({
                    "date": txn.date.isoformat(),
                    "narration": txn.narration,
                    "amount": f"{amount} {currency}",
                    "account": p.account,
                    "citation": cite(txn.meta, root),
                })
    return out


GROUP_BY_CHOICES = ("contract", "owner", "lender", "borrower", "pair", "currency", "none")


def _group_key(contract: Contract, group_by: str) -> str:
    if group_by == "contract":
        return contract.account
    if group_by == "owner":
        return contract.owner_slug
    if group_by == "lender":
        return contract.lender_slug
    if group_by == "borrower":
        return contract.borrower_slug
    if group_by == "pair":
        return f"{contract.lender_slug}->{contract.borrower_slug}"
    if group_by == "currency":
        return contract.currency or "?"
    return "all"


def portfolio(entries, contracts: list[Contract], *, owner: str = "", lender: str = "",
             borrower: str = "", contract_id: str = "", currency: str = "",
             as_of: date | None = None, root: Path | None = None,
             group_by: str = "contract") -> dict:
    """Aggregate across any set of contracts, grouped however was asked.

    This is the one place totals are summed -- one contract, a lender/borrower
    pair, everything for a borrower or a lender, or the whole household, all
    through the same filter-then-sum path, in Decimal.
    """
    as_of = as_of or date.today()
    scoped = [
        c for c in contracts
        if (not owner or c.owner_slug == owner)
        and (not lender or c.lender_slug == lender)
        and (not borrower or c.borrower_slug == borrower)
        and (not contract_id or c.contract_id == contract_id or c.account == contract_id)
        and (not currency or not c.currency or c.currency == currency)
    ]

    receivable_total: dict[str, Decimal] = defaultdict(Decimal)
    payable_total: dict[str, Decimal] = defaultdict(Decimal)
    lent_total: dict[str, Decimal] = defaultdict(Decimal)
    repaid_total: dict[str, Decimal] = defaultdict(Decimal)
    interest_received_total: dict[str, Decimal] = defaultdict(Decimal)
    interest_paid_total: dict[str, Decimal] = defaultdict(Decimal)
    internal_receivable: dict[str, Decimal] = defaultdict(Decimal)
    citations: set[str] = set()
    counts = {"contracts": len(scoped), "lent": 0, "repaid": 0, "borrowed": 0,
             "returned": 0, "interest_received": 0, "interest_paid": 0}
    groups: dict[str, dict] = {}

    for c in scoped:
        outstanding: dict[str, Decimal] = defaultdict(Decimal)
        c_citations: list[str] = []
        for txn in _touching(entries, {c.account, c.interest_account}, as_of):
            cited = False
            for p in txn.postings:
                if p.units is None:
                    continue
                n, cur = p.units.number, p.units.currency
                if p.account == c.account:
                    delta = _principal_delta(c, n)
                    outstanding[cur] += delta
                    if c.direction == "receivable":
                        if delta > 0:
                            counts["lent"] += 1
                            lent_total[cur] += delta
                        elif delta < 0:
                            counts["repaid"] += 1
                            repaid_total[cur] += -delta
                    else:
                        if delta > 0:
                            counts["borrowed"] += 1
                            lent_total[cur] += delta
                        elif delta < 0:
                            counts["returned"] += 1
                            repaid_total[cur] += -delta
                    cited = True
                elif p.account == c.interest_account:
                    if c.direction == "receivable" and n < 0:
                        interest_received_total[cur] += -n
                        counts["interest_received"] += 1
                        cited = True
                    elif c.direction == "payable" and n > 0:
                        interest_paid_total[cur] += n
                        counts["interest_paid"] += 1
                        cited = True
            if cited:
                c_citations.append(cite(txn.meta, root))
        citations.update(c_citations)

        for cur, v in outstanding.items():
            if c.direction == "receivable":
                receivable_total[cur] += v
                if c.is_internal:
                    internal_receivable[cur] += v
            else:
                payable_total[cur] += v

        gk = _group_key(c, group_by)
        entry = groups.setdefault(gk, {
            "key": gk, "lender": c.lender_slug, "borrower": c.borrower_slug,
            "owner": c.owner_slug, "account": c.account if group_by == "contract" else "",
            "direction": c.direction, "internal": c.is_internal,
            "outstanding_principal": defaultdict(Decimal), "citations": [],
        })
        for cur, v in outstanding.items():
            entry["outstanding_principal"][cur] += v
        entry["citations"].extend(c_citations)

    group_list = []
    for g in groups.values():
        rendered = dict(g)
        rendered["outstanding_principal"] = _amounts_to_dict(g["outstanding_principal"])
        rendered["citations"] = sorted(set(g["citations"]))
        group_list.append(rendered)
    group_list.sort(key=lambda g: g["key"])

    net: dict[str, Decimal] = defaultdict(Decimal)
    for cur, v in receivable_total.items():
        net[cur] += v
    for cur, v in payable_total.items():
        net[cur] -= v

    internal_count = sum(1 for c in scoped if c.is_internal)
    internal_note = (
        "Internal loans (both parties are books this ledger keeps) are shown separately "
        "and excluded from net, since they cancel out across the household."
        if internal_count else ""
    )

    return {
        "scope": {"owner": owner, "lender": lender, "borrower": borrower,
                 "contract": contract_id, "currency": currency, "as_of": as_of.isoformat()},
        "group_by": group_by,
        "totals": {
            "receivable": _amounts_to_dict(receivable_total),
            "payable": _amounts_to_dict(payable_total),
            "net": _amounts_to_dict(net),
            "principal_lent_total": _amounts_to_dict(lent_total),
            "principal_repaid_total": _amounts_to_dict(repaid_total),
            "interest_received": _amounts_to_dict(interest_received_total),
            "interest_paid": _amounts_to_dict(interest_paid_total),
        },
        "counts": counts,
        "groups": group_list,
        "internal": {"contracts": internal_count,
                    "receivable": _amounts_to_dict(internal_receivable), "note": internal_note},
        "citations": sorted(citations),
        "note": "Amounts are per currency and never converted or added together. Every "
                "total is the sum of the groups below.",
    }
