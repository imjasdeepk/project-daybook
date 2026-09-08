"""Projected interest on a loan.

This is the one number the ledger does not have on record, so it is always
labelled a projection, always shows its inputs and formula, and is never added
to what someone actually owes. Interest you were really paid is a recorded
transaction like any other.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, getcontext
from pathlib import Path

from .entities import Entity
from .store import LedgerError, cite, transactions

getcontext().prec = 28

PERIODS_PER_YEAR = {
    "annual": 1, "annually": 1, "yearly": 1,
    "semiannual": 2, "semiannually": 2, "halfyearly": 2,
    "quarterly": 4, "monthly": 12, "daily": 365,
}
DAY_COUNT_BASIS = {"actual/365": 365, "actual/360": 360, "act/365": 365, "act/360": 360}


def _principal_events(entries, entity: Entity, as_of: date) -> dict[str, list[tuple[date, Decimal, str]]]:
    """Per currency: (date, signed principal change, citation), chronological."""
    prefix = entity.loans_account
    events: dict[str, list[tuple[date, Decimal, str]]] = defaultdict(list)
    for txn in transactions(entries):
        if txn.date > as_of:
            continue
        for p in txn.postings:
            if p.units is None:
                continue
            if p.account == prefix or p.account.startswith(prefix + ":"):
                events[p.units.currency].append((txn.date, p.units.number, cite(txn.meta)))
    for cur in events:
        events[cur].sort(key=lambda e: e[0])
    return events


def project(entries, entity: Entity, as_of: date, root: Path | None = None) -> dict:
    """Accrue interest across every period the outstanding principal was constant."""
    if not entity.rate_percent_pa:
        raise LedgerError(
            f"{entity.name} has no rate_percent_pa recorded, so no interest can be projected. "
            f"Add the rate to their record at {entity.citation} first."
        )
    method = (entity.method or "simple").strip().lower()
    if method not in ("simple", "compound"):
        raise LedgerError(f"method must be 'simple' or 'compound', found {method!r}.")

    rate = Decimal(str(entity.rate_percent_pa)) / Decimal(100)
    basis = DAY_COUNT_BASIS.get((entity.day_count or "actual/365").lower())
    if basis is None:
        raise LedgerError(f"Unsupported day_count {entity.day_count!r}.")
    periods = None
    if method == "compound":
        periods = PERIODS_PER_YEAR.get((entity.compounding or "annual").strip().lower())
        if periods is None:
            raise LedgerError(f"Unsupported compounding {entity.compounding!r}.")

    events = _principal_events(entries, entity, as_of)
    results = {}
    for currency, evts in events.items():
        if not evts:
            continue
        principal = Decimal(0)
        value = Decimal(0)
        interest = Decimal(0)
        cursor = evts[0][0]
        segments = []
        stream = [*evts, (as_of, Decimal(0), "")]
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
            continue
        if method == "simple":
            formula = f"interest = principal x {entity.rate_percent_pa}%/yr x days/{basis}, summed over each period the principal was unchanged"
        else:
            formula = (f"interest = balance x ((1 + {entity.rate_percent_pa}%/{periods})^({periods} x days/{basis}) - 1), "
                       f"compounded {entity.compounding or 'annual'}, applied over each period")
        results[currency] = {
            "outstanding_principal": str(principal),
            "projected_interest": str(interest.quantize(Decimal("0.01"))),
            "formula": formula,
            "segments": segments,
        }

    return {
        "PROJECTION": "Not recorded and not owed. Interest is owed only once you record it.",
        "entity": entity.name,
        "as_of": as_of.isoformat(),
        "inputs": {
            "rate_percent_pa": entity.rate_percent_pa,
            "method": method,
            "compounding": entity.compounding or ("n/a" if method == "simple" else "annual"),
            "day_count": entity.day_count or "actual/365",
            "source": entity.citation,
        },
        "by_currency": results,
    }
