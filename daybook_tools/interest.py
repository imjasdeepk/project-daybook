"""Projected interest on a loan contract.

This is the one number the ledger does not have on record, so it is always
labelled a projection, always shows its inputs and formula, and is never added
to what someone actually owes. Interest you were really paid (or really paid
out) is a recorded transaction like any other.

Each contract accrues at its own rate -- there is no blended or averaged rate
across contracts. When a borrower holds several contracts, `project()` sums
the already-rounded per-contract figures into a per-currency total, so that
total always equals the sum of the lines a person could check by hand.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, getcontext
from pathlib import Path

from .contracts import Contract, DAY_COUNT_BASIS, PERIODS_PER_YEAR
from .entities import Entity
from .store import DaybookError, cite, transactions

getcontext().prec = 28


def _principal_events(entries, accounts: set[str], as_of: date,
                      ) -> dict[str, list[tuple[date, Decimal, str, str]]]:
    """Per account: (date, signed principal change, currency, citation), chronological."""
    events: dict[str, list[tuple[date, Decimal, str, str]]] = defaultdict(list)
    for txn in transactions(entries):
        if txn.date > as_of:
            continue
        for p in txn.postings:
            if p.units is None or p.account not in accounts:
                continue
            events[p.account].append((txn.date, p.units.number, p.units.currency, cite(txn.meta)))
    for acc in events:
        events[acc].sort(key=lambda e: e[0])
    return events


def _accrue(events: list[tuple[date, Decimal, str]], *, rate: Decimal, method: str,
           basis: int, periods: int | None, as_of: date) -> dict | None:
    """Accrue interest across every period the outstanding principal was constant.

    `events` is (date, signed principal change, citation), already chronological,
    with signs already normalised so principal is positive for an outstanding loan.
    """
    if not events:
        return None
    principal = Decimal(0)
    value = Decimal(0)
    interest = Decimal(0)
    cursor = events[0][0]
    segments = []
    stream = [*events, (as_of, Decimal(0), "")]
    for when, change, citation in stream:
        days = (when - cursor).days
        if days > 0 and principal > 0:
            years = Decimal(days) / Decimal(basis)
            if method == "simple":
                grown = principal * rate * years
                interest += grown
                segments.append({
                    "from": cursor.isoformat(), "to": when.isoformat(), "days": days,
                    "principal": str(principal), "interest": str(grown.quantize(Decimal("0.01"))),
                })
            else:
                factor = (Decimal(1) + rate / Decimal(periods)) ** (Decimal(periods) * years)
                grown = value * factor - value
                interest += grown
                value += grown
                segments.append({
                    "from": cursor.isoformat(), "to": when.isoformat(), "days": days,
                    "balance_with_interest": str(value.quantize(Decimal("0.01"))),
                    "interest": str(grown.quantize(Decimal("0.01"))),
                })
        principal += change
        value += change
        cursor = when
    if principal <= 0 and interest == 0:
        return None
    return {
        "outstanding_principal": principal,
        "projected_interest": interest.quantize(Decimal("0.01")),
        "segments": segments,
    }


def _formula_for(contract: Contract, basis: int, periods: int | None) -> str:
    if contract.method == "simple":
        return (f"interest = principal x {contract.rate_percent_pa}%/yr x days/{basis}, "
                f"summed over each period the principal was unchanged")
    return (f"interest = balance x ((1 + {contract.rate_percent_pa}%/{periods})^"
            f"({periods} x days/{basis}) - 1), compounded {contract.compounding or 'annual'}, "
            f"applied over each period")


def project_contract(entries, contract: Contract, as_of: date) -> dict[str, dict] | None:
    """Project interest for one contract, at its own rate. None if there is nothing to show."""
    basis = DAY_COUNT_BASIS.get((contract.day_count or "actual/365").lower())
    if basis is None:
        raise DaybookError(f"Unsupported day_count {contract.day_count!r} on {contract.account}.")
    periods = None
    if contract.method == "compound":
        periods = PERIODS_PER_YEAR.get((contract.compounding or "annual").strip().lower())
        if periods is None:
            raise DaybookError(f"Unsupported compounding {contract.compounding!r} on {contract.account}.")
    rate = Decimal(str(contract.rate_percent_pa)) / Decimal(100)

    raw = _principal_events(entries, {contract.account}, as_of).get(contract.account, [])
    if not raw:
        return None

    # Liabilities:Owed is credit-normal (negative), so a payable contract's raw
    # postings are negative. Flip the sign here, once, so the accrual engine
    # above never needs to know which direction the contract runs.
    sign = Decimal(1) if contract.direction == "receivable" else Decimal(-1)
    by_currency: dict[str, list[tuple[date, Decimal, str]]] = defaultdict(list)
    for when, change, currency, citation in raw:
        by_currency[currency].append((when, change * sign, citation))

    results: dict[str, dict] = {}
    for currency, evts in by_currency.items():
        evts.sort(key=lambda e: e[0])
        block = _accrue(evts, rate=rate, method=contract.method, basis=basis,
                        periods=periods, as_of=as_of)
        if block is None:
            continue
        results[currency] = {
            "outstanding_principal": str(block["outstanding_principal"]),
            "projected_interest": str(block["projected_interest"]),
            "formula": _formula_for(contract, basis, periods),
            "segments": block["segments"],
        }
    return results or None


def project(entries, entity: Entity, as_of: date, root: Path | None = None, *,
           contracts: list[Contract], lender: str = "", borrower: str = "",
           contract: str = "") -> dict:
    """Project interest for every contract this entity is party to, as lender or
    borrower, narrowed by --lender/--borrower/--contract if given. Never blends rates."""
    mine = [c for c in contracts if entity.slug in (c.owner_slug, c.counterparty_slug)]
    if lender:
        mine = [c for c in mine if c.lender_slug == lender]
    if borrower:
        mine = [c for c in mine if c.borrower_slug == borrower]
    if contract:
        mine = [c for c in mine if c.contract_id == contract or c.account == contract]
    if not mine:
        raise DaybookError(f"{entity.name} has no matching loan contract to project interest for.")

    # An IOU has no terms, so there is nothing to project. Reporting it as 0%
    # would state a rate nobody agreed. It is listed as skipped instead, so the
    # answer can say plainly that part of what is owed accrues nothing.
    skipped = [
        {"contract": c.contract_id, "account": c.account, "kind": "iou",
         "lender": c.lender_slug, "borrower": c.borrower_slug,
         "reason": "an IOU: no interest terms were agreed", "source": c.citation}
        for c in sorted(mine, key=lambda c: c.started) if not c.has_terms
    ]
    mine = [c for c in mine if c.has_terms]
    if not mine:
        return {
            "PROJECTION": "Nothing to project.",
            "entity": entity.name,
            "as_of": as_of.isoformat(),
            "by_currency": {},
            "contracts": [],
            "skipped": skipped,
            "detail": (
                f"{entity.name} has no loan with interest terms. "
                f"{len(skipped)} IOU{'s' if len(skipped) != 1 else ''} accrue nothing."
            ),
        }

    flat: list[dict] = []
    principal_totals: dict[str, Decimal] = defaultdict(Decimal)
    interest_totals: dict[str, Decimal] = defaultdict(Decimal)
    counts: dict[str, int] = defaultdict(int)

    for c in sorted(mine, key=lambda c: c.started):
        by_currency = project_contract(entries, c, as_of)
        if not by_currency:
            continue
        for currency, block in by_currency.items():
            principal_totals[currency] += Decimal(block["outstanding_principal"])
            interest_totals[currency] += Decimal(block["projected_interest"])
            counts[currency] += 1
            flat.append({
                "contract": c.contract_id, "account": c.account,
                "lender": c.lender_slug, "borrower": c.borrower_slug,
                "currency": currency, "direction": c.direction,
                "inputs": {
                    "rate_percent_pa": c.rate_percent_pa, "method": c.method,
                    "compounding": c.compounding or ("n/a" if c.method == "simple" else "annual"),
                    "day_count": c.day_count, "started": c.started, "source": c.citation,
                },
                "outstanding_principal": block["outstanding_principal"],
                "projected_interest": block["projected_interest"],
                "formula": block["formula"],
                "segments": block["segments"],
            })

    same_terms = len({(c.rate_percent_pa, c.method, c.compounding, c.day_count) for c in mine}) == 1
    by_currency_out = {}
    for currency, n in counts.items():
        if n == 1:
            formula = next(f["formula"] for f in flat if f["currency"] == currency)
        elif same_terms:
            formula = "sum of the per-contract lines below (same terms, multiple contracts)"
        else:
            formula = "sum of the per-contract lines below; terms differ by contract"
        by_currency_out[currency] = {
            "outstanding_principal": str(principal_totals[currency]),
            "projected_interest": str(interest_totals[currency]),
            "formula": formula,
            "contracts": n,
        }

    if same_terms:
        c0 = mine[0]
        inputs = {
            "rate_percent_pa": c0.rate_percent_pa, "method": c0.method,
            "compounding": c0.compounding or ("n/a" if c0.method == "simple" else "annual"),
            "day_count": c0.day_count, "source": c0.citation,
        }
    else:
        inputs = {
            "terms": "vary by contract", "contracts": len(mine),
            "rates_percent_pa": sorted({c.rate_percent_pa for c in mine}),
            "methods": sorted({c.method for c in mine}),
            "day_counts": sorted({c.day_count for c in mine}),
            "source": "see each contract below",
        }

    return {
        "PROJECTION": "Not recorded and not owed. Interest is owed only once you record it.",
        "entity": entity.name,
        "as_of": as_of.isoformat(),
        "inputs": inputs,
        **({"skipped": skipped} if skipped else {}),
        "by_currency": by_currency_out,
        "contracts": flat,
        "note": "Each contract accrues at its own rate. The per-currency total is the "
                "sum of the contract lines below. Never a blended rate.",
    }
