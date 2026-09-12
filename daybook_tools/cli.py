"""The `daybook` command. Every answer Claude gives comes from one of these."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from beancount import loader

from . import capture, dates, events, interest, queries
from .contracts import Contract, add_contract, build_contract, load_contracts
from .entities import (
    Entity, add_alias, add_entity, load_entities, resolve, self_entity, set_book,
)
from .store import (
    LOCATION_FILENAME, DaybookError, Paths, cite, git_commit, git_sync, load, paths, slugify,
)


def _out(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(_render(payload))


def _render(payload: dict, indent: int = 0) -> str:
    pad = "  " * indent
    lines = []
    for key, value in payload.items():
        if isinstance(value, dict) and value:
            lines.append(f"{pad}{key}:")
            lines.append(_render(value, indent + 1))
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            lines.append(f"{pad}{key}:")
            for item in value:
                lines.append(_render(item, indent + 1))
                lines.append("")
        elif isinstance(value, list):
            lines.append(f"{pad}{key}: {', '.join(str(v) for v in value) if value else '(none)'}")
        elif value not in ("", None, {}):
            lines.append(f"{pad}{key}: {value}")
    return "\n".join(lines)


def _as_of(value: str | None) -> date | None:
    if not value:
        return None
    parsed = dates.parse(value)
    if parsed["status"] != "resolved":
        raise DaybookError(f"Could not read the date {value!r}: {parsed.get('reason', 'ambiguous')}")
    return date.fromisoformat(parsed["date"])

def _require_entity(entries, who: str, root: Path) -> Entity:
    found = resolve(who, load_entities(entries, root))
    if found["status"] == "resolved":
        data = found["entity"]
        return next(e for e in load_entities(entries, root) if e.slug == data["slug"])
    if found["status"] == "ambiguous":
        names = ", ".join(f"{c['name']} ({c['citation']})" for c in found["candidates"])
        raise DaybookError(
            f"{who!r} is not clear enough to act on. Did you mean: {names}? "
            f"Ask, then use the exact name or add an alias."
        )
    raise DaybookError(
        f"I have no record of {who!r}. Create it first with: "
        f"daybook entity add --name \"<full name>\" --aliases \"{who}\""
    )


def _book_owner(entries, book: str, entities: list[Entity], root: Path) -> Entity:
    if book:
        return _require_entity(entries, book, root)
    owner = self_entity(entities)
    if owner is None:
        raise DaybookError(
            "No book owner given and no entity marked --self. Pass --book, or mark "
            'yourself with: daybook entity book "<name>" --on'
        )
    return owner


def _pick_contract(entries, contracts: list[Contract], entities: list[Entity],
                   root: Path, args) -> Contract:
    """Resolve which contract an add/void is against: explicit, or by (lender, borrower)."""
    if args.contract:
        matches = [c for c in contracts if c.contract_id == args.contract or c.account == args.contract]
        if not matches:
            raise DaybookError(f"No contract found matching {args.contract!r}.")
        return matches[0]

    def _by_pair(lender_slug: str, borrower_slug: str) -> Contract:
        candidates = [c for c in contracts
                     if c.lender_slug == lender_slug and c.borrower_slug == borrower_slug]
        if args.currency:
            candidates = [c for c in candidates if not c.currency or c.currency == args.currency.upper()]
        if not candidates:
            raise DaybookError(
                "No loan contract is on record for that pair, so there is nothing to record "
                "this against. Create one with:\n"
                '  daybook contract add --lender "<name>" --borrower "<name>" --rate <rate> '
                "--started <date>"
            )
        if len(candidates) > 1:
            listing = "; ".join(
                f"--contract {c.contract_id} ({c.rate_percent_pa}% from {c.started}, {c.citation})"
                for c in candidates
            )
            raise DaybookError(f"More than one contract matches. Say which: {listing}")
        return candidates[0]

    if args.lender and args.borrower:
        return _by_pair(_require_entity(entries, args.lender, root).slug,
                        _require_entity(entries, args.borrower, root).slug)

    if not args.who:
        raise DaybookError(f"A {args.kind} entry needs --who, or both --lender and --borrower.")
    owner = _book_owner(entries, args.book, entities, root)
    who = _require_entity(entries, args.who, root)

    if args.kind in capture.LEGACY_KINDS:
        _, direction = capture.LEGACY_KINDS[args.kind]
        if direction == "receivable":
            return _by_pair(owner.slug, who.slug)
        return _by_pair(who.slug, owner.slug)

    # A new-style kind (principal/repayment/interest) with --who carries no
    # direction of its own -- the contract already on file between the two
    # says which way it runs.
    candidates = [c for c in contracts if {c.lender_slug, c.borrower_slug} == {owner.slug, who.slug}]
    if args.currency:
        candidates = [c for c in candidates if not c.currency or c.currency == args.currency.upper()]
    if not candidates:
        raise DaybookError(
            f"No loan contract is on record between {owner.name!r} and {who.name!r}. "
            "Create one with:\n"
            '  daybook contract add --lender "<name>" --borrower "<name>" --rate <rate> '
            "--started <date>"
        )
    if len(candidates) > 1:
        listing = "; ".join(
            f"--contract {c.contract_id} ({c.lender_slug}->{c.borrower_slug}, "
            f"{c.rate_percent_pa}%, {c.citation})"
            for c in candidates
        )
        raise DaybookError(f"More than one contract matches. Say which: {listing}")
    return candidates[0]


# --------------------------------------------------------------------------- init

MAIN_TEMPLATE = '''; Personal ledger. Every figure in an answer is computed from this file.
; Human-readable on purpose: any number can be checked by reading the lines it cites.
option "title" "{title}"
{currencies}
plugin "beancount.plugins.auto_accounts"

include "accounts.beancount"
'''


def cmd_init(args) -> dict:
    """Create a ledger. Name a folder and the records go straight into it."""
    called_from = Path.cwd()
    if args.folder:
        root = Path(args.folder).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        records = root
    else:
        root = called_from
        records = root / "ledger"
    records.mkdir(parents=True, exist_ok=True)
    main_file = records / "main.beancount"
    accounts_file = records / "accounts.beancount"
    events_file = records / "dates.ics"
    if main_file.exists() and not args.force:
        raise DaybookError(f"{main_file} already exists. Pass --force only if you mean to replace it.")
    currencies = [c.strip().upper() for c in args.currencies.split(",") if c.strip()]
    if not currencies:
        raise DaybookError("Give at least one currency, for example --currencies INR,USD")
    main_file.write_text(
        MAIN_TEMPLATE.format(
            title=args.title,
            currencies="\n".join(f'option "operating_currency" "{c}"' for c in currencies),
        ),
        encoding="utf-8",
    )
    if not accounts_file.exists() or args.force:
        accounts_file.write_text(
            "; People, places and things. Each record is one `open` directive:\n"
            "; the aliases here are how Claude turns what you say into an account.\n",
            encoding="utf-8",
        )
    if not events_file.exists() or args.force:
        events.write_calendar(events_file, events.empty_calendar())
    _, errors, _ = loader.load_file(str(main_file))
    if errors:
        raise DaybookError("The new ledger does not parse: " + "; ".join(e.message for e in errors))

    result = {
        "records_folder": str(records),
        "created": [f.name for f in (main_file, accounts_file, events_file)],
        "currencies": currencies,
    }

    # Remember where the records are, so any session finds them without a shell
    # variable. Only needed when they do not sit under the folder we were run from.
    if args.remember and records != called_from and records.parent != called_from:
        pointer = called_from / LOCATION_FILENAME
        pointer.write_text(str(records) + "\n", encoding="utf-8")
        result["remembered_in"] = str(pointer)

    if args.git:
        result["git"] = _init_records_repo(records)
    else:
        result["backup"] = (
            "No git repository created. Put this folder in Google Drive, Dropbox or "
            "iCloud to back it up, or re-run with --git to version it instead."
        )
    result["next"] = (
        'Mark yourself with: daybook entity add --name "<your name>" --aliases "me" '
        "--book --self"
    )
    return result


def _init_records_repo(records: Path) -> dict:
    """Make the records folder a git repository of its own, so its history stays
    separate from any public code repository."""
    import subprocess
    if (records / ".git").exists():
        return {"status": "already_a_repository"}
    try:
        for cmd in (["git", "init", "-b", "main"], ["git", "add", "-A"],
                    ["git", "commit", "-m", "Start the ledger"]):
            subprocess.run(cmd, cwd=records, check=True, capture_output=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return {"status": "failed", "detail": str(exc)[:200]}
    return {
        "status": "created",
        "next": "Add a private remote: gh repo create <you>/my-daybook --private "
                "--source . --remote origin --push",
    }


# ---------------------------------------------------------------- entities & dates

def cmd_resolve(args) -> dict:
    entries, _ = load()
    return resolve(args.name, load_entities(entries, paths().root))


def cmd_entity_add(args) -> dict:
    p = paths()
    entries, _ = load(p)
    existing = load_entities(entries, p.root)
    slug = slugify(args.slug or args.name)
    if re.fullmatch(r"[A-Z]{3}", slug):
        raise DaybookError(
            f"{slug!r} looks like a 3-letter currency code, which would make "
            f"Assets:Cash:{slug}:<CUR> ambiguous. Pick a different --slug."
        )
    entity = Entity(
        slug=slug,
        name=args.name,
        type=args.type,
        relation=args.relation or "",
        aliases=[a.strip() for a in (args.aliases or "").split(",") if a.strip()],
        default_currency=(args.currency or "").upper(),
        book=args.book,
        is_self=args.self,
    )
    result = add_entity(p, entity, args.opened or date.today(), existing)
    load(p)
    result["commit"] = git_commit(p.root, f"entity: add {entity.name}", [p.accounts])
    return result


def cmd_entity_alias(args) -> dict:
    p = paths()
    entries, _ = load(p)
    all_entities = load_entities(entries, p.root)
    entity = _require_entity(entries, args.name, p.root)
    result = add_alias(p, entity, [a.strip() for a in args.add.split(",")], all_entities)
    load(p)
    result["commit"] = git_commit(p.root, f"entity: alias {entity.name}", [p.accounts])
    return result


def cmd_entity_list(args) -> dict:
    entries, _ = load()
    return {"entities": [e.to_dict() for e in load_entities(entries, paths().root)]}


def cmd_entity_book(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entity = _require_entity(entries, args.name, p.root)
    if args.book is None:
        raise DaybookError("Say --on or --off.")
    result = set_book(p, entity, args.book)
    load(p)
    result["commit"] = git_commit(p.root, f"entity: book {entity.name} {args.book}", [p.accounts])
    return result


def cmd_date(args) -> dict:
    return dates.parse(args.expression, _as_of(args.today))


def cmd_today(args) -> dict:
    today = date.today()
    return {"date": today.isoformat(), "weekday": today.strftime("%A")}


# --------------------------------------------------------------------- contracts

def cmd_contract_add(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entities = load_entities(entries, p.root)
    existing, _ = load_contracts(entries, entities, p.root)
    lender = _require_entity(entries, args.lender, p.root)
    borrower = _require_entity(entries, args.borrower, p.root)
    started = _as_of(args.started) or date.today()
    taken = {c.contract_id for c in existing
            if c.lender_slug == lender.slug and c.borrower_slug == borrower.slug}
    contract = build_contract(
        lender, borrower, entities, rate=args.rate, started=started,
        method=args.method, compounding=args.compounding, day_count=args.day_count,
        currency=args.currency, contract_id=args.contract_id, note=args.note,
        taken_ids=frozenset(taken),
    )
    result = add_contract(p, contract, existing)
    load(p)
    result["commit"] = git_commit(
        p.root, f"contract: add {lender.name} -> {borrower.name}", [p.accounts]
    )
    return result


def cmd_contract_list(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entities = load_entities(entries, p.root)
    contracts, problems = load_contracts(entries, entities, p.root)
    if args.lender:
        slug = _require_entity(entries, args.lender, p.root).slug
        contracts = [c for c in contracts if c.lender_slug == slug]
    if args.borrower:
        slug = _require_entity(entries, args.borrower, p.root).slug
        contracts = [c for c in contracts if c.borrower_slug == slug]
    if args.owner:
        slug = _require_entity(entries, args.owner, p.root).slug
        contracts = [c for c in contracts if c.owner_slug == slug]
    result = {"contracts": [c.to_dict() for c in contracts]}
    if problems:
        result["problems"] = problems
    return result


def cmd_contract_show(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entities = load_entities(entries, p.root)
    contracts, _ = load_contracts(entries, entities, p.root)
    matches = [c for c in contracts if c.contract_id == args.contract or c.account == args.contract]
    if not matches:
        raise DaybookError(f"No contract found matching {args.contract!r}.")
    return matches[0].to_dict()


# ------------------------------------------------------------------------ capture

def cmd_add(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entities = load_entities(entries, p.root)
    contracts, _ = load_contracts(entries, entities, p.root)

    contract = None
    owner = None
    counterparty = None
    if args.kind in ("spend", "receive"):
        owner = _book_owner(entries, args.book, entities, p.root)
    else:
        contract = _pick_contract(entries, contracts, entities, p.root, args)
        lender = next(e for e in entities if e.slug == contract.lender_slug)
        borrower = next(e for e in entities if e.slug == contract.borrower_slug)
        counterparty = borrower if contract.direction == "receivable" else lender

    when = _as_of(args.date) or date.today()
    currency = (args.currency or (contract.currency if contract else "")
               or (counterparty.default_currency if counterparty else "")
               or (owner.default_currency if owner else "")).upper()
    if not currency:
        raise DaybookError("No currency given and no default recorded. Pass --currency.")

    plan = capture.plan_entry(
        args.kind, contract=contract, owner=owner, counterparty=counterparty,
        amount=capture.parse_amount(args.amount), currency=currency, when=when,
        narration=args.note or args.kind.title(), source=args.source or "",
        category=args.category or "",
    )
    duplicates = capture.check_duplicates(entries, plan, p.root)
    if duplicates and not args.force:
        return {
            "status": "possible_duplicate",
            "nothing_was_written": True,
            "existing": duplicates,
            "proposed": capture.render_entry(plan),
            "next": "Confirm with the user, then repeat the command with --force if it is genuinely separate.",
        }
    result = capture.commit_entry(p, plan, commit=not args.no_commit)
    result["status"] = "recorded"
    return result


def cmd_void(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entities = load_entities(entries, p.root)
    contracts, _ = load_contracts(entries, entities, p.root)

    contract = None
    owner = None
    counterparty = None
    if args.kind in ("spend", "receive"):
        owner = _book_owner(entries, args.book, entities, p.root)
    else:
        contract = _pick_contract(entries, contracts, entities, p.root, args)
        lender = next(e for e in entities if e.slug == contract.lender_slug)
        borrower = next(e for e in entities if e.slug == contract.borrower_slug)
        counterparty = borrower if contract.direction == "receivable" else lender

    when = _as_of(args.date) or date.today()
    currency = (args.currency or (contract.currency if contract else "")
               or (counterparty.default_currency if counterparty else "")
               or (owner.default_currency if owner else "")).upper()
    plan = capture.plan_entry(
        args.kind, contract=contract, owner=owner, counterparty=counterparty,
        amount=capture.parse_amount(args.amount), currency=currency, when=when,
        narration=args.note or args.kind.title(), source=args.source or "",
        category=args.category or "",
    )
    result = capture.reverse_entry(p, plan, args.voids, commit=not args.no_commit)
    result["status"] = "reversed"
    return result


# ------------------------------------------------------------------------ answers

def cmd_balance(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entities = load_entities(entries, p.root)
    contracts, _ = load_contracts(entries, entities, p.root)
    entity = _require_entity(entries, args.who, p.root)
    lender = _require_entity(entries, args.lender, p.root).slug if args.lender else ""
    borrower = _require_entity(entries, args.borrower, p.root).slug if args.borrower else ""
    return queries.balance(entries, entity, contracts, _as_of(args.as_of), p.root,
                           lender=lender, borrower=borrower, contract_id=args.contract)


def cmd_statement(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entities = load_entities(entries, p.root)
    contracts, _ = load_contracts(entries, entities, p.root)
    entity = _require_entity(entries, args.who, p.root)
    lender = _require_entity(entries, args.lender, p.root).slug if args.lender else ""
    borrower = _require_entity(entries, args.borrower, p.root).slug if args.borrower else ""
    return queries.statement(entries, entity, contracts, _as_of(args.as_of), p.root,
                             lender=lender, borrower=borrower, contract_id=args.contract)


def cmd_projection(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entities = load_entities(entries, p.root)
    contracts, _ = load_contracts(entries, entities, p.root)
    entity = _require_entity(entries, args.who, p.root)
    lender = _require_entity(entries, args.lender, p.root).slug if args.lender else ""
    borrower = _require_entity(entries, args.borrower, p.root).slug if args.borrower else ""
    return interest.project(entries, entity, _as_of(args.as_of) or date.today(), p.root,
                            contracts=contracts, lender=lender, borrower=borrower,
                            contract=args.contract)


def cmd_portfolio(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entities = load_entities(entries, p.root)
    contracts, problems = load_contracts(entries, entities, p.root)
    owner = _require_entity(entries, args.owner, p.root).slug if args.owner else ""
    lender = _require_entity(entries, args.lender, p.root).slug if args.lender else ""
    borrower = _require_entity(entries, args.borrower, p.root).slug if args.borrower else ""
    currency = (args.currency or "").upper()
    as_of = _as_of(args.as_of)
    result = queries.portfolio(
        entries, contracts, owner=owner, lender=lender, borrower=borrower,
        contract_id=args.contract, currency=currency, as_of=as_of, root=p.root,
        group_by=args.group_by,
    )
    if args.projection:
        scoped = [
            c for c in contracts
            if (not owner or c.owner_slug == owner)
            and (not lender or c.lender_slug == lender)
            and (not borrower or c.borrower_slug == borrower)
            and (not args.contract or c.contract_id == args.contract or c.account == args.contract)
            and (not currency or not c.currency or c.currency == currency)
        ]
        as_of_p = as_of or date.today()
        projected = []
        for c in scoped:
            by_currency = interest.project_contract(entries, c, as_of_p)
            if not by_currency:
                continue
            for cur, block in by_currency.items():
                projected.append({
                    "contract": c.contract_id, "account": c.account,
                    "lender": c.lender_slug, "borrower": c.borrower_slug,
                    "currency": cur, **block,
                })
        result["projection"] = projected
    if problems:
        result["contract_problems"] = problems
    return result


def cmd_search(args) -> dict:
    p = paths()
    entries, _ = load(p)
    return queries.search(entries, args.text, p.root, limit=args.limit)


def cmd_sync(args) -> dict:
    p = paths()
    return git_sync(p.ledger_dir)


def cmd_check(args) -> dict:
    p = paths()
    _, errors, _ = loader.load_file(str(p.main))
    if errors:
        return {"status": "invalid", "errors": [f"{cite(e.source)}: {e.message}" for e in errors]}
    entries, _ = load(p)
    entities = load_entities(entries, p.root)
    contracts, problems = load_contracts(entries, entities, p.root)
    result = {
        "status": "valid" if not problems else "valid_with_problems",
        "file": str(p.main),
        "transactions": len(queries.transactions(entries)),
        "entities": len(entities),
        "contracts": len(contracts),
    }
    if problems:
        result["contract_problems"] = problems
    return result


# ------------------------------------------------------------------------- events

def cmd_event_add(args) -> dict:
    p = paths()
    when = _as_of(args.date)
    if when is None:
        raise DaybookError("An event needs a date: pass --date.")
    uid = args.uid or events.make_uid(args.kind, args.summary)
    return events.add_event(
        p.events, uid=uid, summary=args.summary, on=when, kind=args.kind,
        yearly=not args.once, source=args.source or "",
        remind_days_before=args.remind_days_before,
    )


def cmd_upcoming(args) -> dict:
    p = paths()
    if args.on:
        return events.on_date(p.events, _as_of(args.on))
    return events.upcoming(p.events, days=args.days, frm=_as_of(args.frm))


# --------------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daybook",
        description="A daybook whose answers are retrieved or computed, never recalled.")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    i = sub.add_parser("init", help="create a new ledger in this folder")
    i.add_argument("--title", default="Personal Ledger")
    i.add_argument("--currencies", default="USD", help="comma separated, e.g. INR,USD")
    i.add_argument("folder", nargs="?", default="",
                   help="where your records go, e.g. ~/Documents/ledger. "
                        "Defaults to ./ledger")
    i.add_argument("--git", action="store_true",
                   help="also make that folder a git repository of its own")
    i.add_argument("--no-remember", dest="remember", action="store_false",
                   help="do not write a .daybook-root pointer here")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    r = sub.add_parser("resolve", help="what account does this name mean?")
    r.add_argument("name")
    r.set_defaults(func=cmd_resolve)

    e = sub.add_parser("entity", help="people, places and things")
    esub = e.add_subparsers(dest="entity_command", required=True)
    ea = esub.add_parser("add")
    ea.add_argument("--name", required=True)
    ea.add_argument("--slug", default="")
    ea.add_argument("--type", default="person", choices=["person", "org", "place", "thing"])
    ea.add_argument("--relation", default="")
    ea.add_argument("--aliases", default="")
    ea.add_argument("--currency", default="")
    ea.add_argument("--book", action="store_true",
                    help="this ledger keeps this person's own books (cash, contracts)")
    ea.add_argument("--self", action="store_true",
                    help="this is the person running the tool (at most one entity)")
    ea.add_argument("--opened", type=date.fromisoformat, default=None)
    ea.set_defaults(func=cmd_entity_add)
    el = esub.add_parser("alias")
    el.add_argument("name")
    el.add_argument("--add", required=True, help="comma separated aliases")
    el.set_defaults(func=cmd_entity_alias)
    ez = esub.add_parser("list")
    ez.set_defaults(func=cmd_entity_list)
    eb = esub.add_parser("book", help="turn a book on or off for someone")
    eb.add_argument("name")
    eb.add_argument("--on", dest="book", action="store_true", default=None)
    eb.add_argument("--off", dest="book", action="store_false", default=None)
    eb.set_defaults(func=cmd_entity_book)

    d = sub.add_parser("date", help="turn a phrase into an exact date")
    d.add_argument("expression")
    d.add_argument("--today", default="")
    d.set_defaults(func=cmd_date)

    t = sub.add_parser("today")
    t.set_defaults(func=cmd_today)

    k = sub.add_parser("contract", help="loan contracts: who lent whom, on what terms")
    ksub = k.add_subparsers(dest="contract_command", required=True)
    ka = ksub.add_parser("add")
    ka.add_argument("--lender", required=True)
    ka.add_argument("--borrower", required=True)
    ka.add_argument("--rate", default="0", help="annual interest rate, e.g. 10.2, or 0")
    ka.add_argument("--method", default="simple", choices=["simple", "compound"])
    ka.add_argument("--compounding", default="",
                    choices=["", "annual", "semiannual", "quarterly", "monthly", "daily"])
    ka.add_argument("--day-count", dest="day_count", default="actual/365")
    ka.add_argument("--started", required=True, help="a date or a phrase")
    ka.add_argument("--currency", default="")
    ka.add_argument("--id", dest="contract_id", default="", help="override the derived id")
    ka.add_argument("--note", default="")
    ka.set_defaults(func=cmd_contract_add)
    kl = ksub.add_parser("list")
    kl.add_argument("--lender", default="")
    kl.add_argument("--borrower", default="")
    kl.add_argument("--owner", default="")
    kl.set_defaults(func=cmd_contract_list)
    ks = ksub.add_parser("show")
    ks.add_argument("contract", help="a contract id or its full account")
    ks.set_defaults(func=cmd_contract_show)

    a = sub.add_parser("add", help="record something")
    a.add_argument("--kind", required=True, choices=capture.ALL_KINDS)
    a.add_argument("--who", default="", help="the counterparty (with a legacy kind name)")
    a.add_argument("--lender", default="")
    a.add_argument("--borrower", default="")
    a.add_argument("--contract", default="")
    a.add_argument("--book", default="", help="whose book this belongs to (default: --self)")
    a.add_argument("--amount", required=True)
    a.add_argument("--currency", default="")
    a.add_argument("--date", default="")
    a.add_argument("--note", default="")
    a.add_argument("--source", default="", help="what you actually said, kept for the record")
    a.add_argument("--category", default="")
    a.add_argument("--force", action="store_true", help="record even if it looks like a duplicate")
    a.add_argument("--no-commit", action="store_true")
    a.set_defaults(func=cmd_add)

    v = sub.add_parser("void", help="reverse an earlier entry with a new one")
    v.add_argument("--voids", required=True, help="citation of the entry being corrected")
    v.add_argument("--kind", required=True, choices=capture.ALL_KINDS)
    v.add_argument("--who", default="")
    v.add_argument("--lender", default="")
    v.add_argument("--borrower", default="")
    v.add_argument("--contract", default="")
    v.add_argument("--book", default="")
    v.add_argument("--amount", required=True)
    v.add_argument("--currency", default="")
    v.add_argument("--date", default="")
    v.add_argument("--note", default="")
    v.add_argument("--source", default="")
    v.add_argument("--category", default="")
    v.add_argument("--no-commit", action="store_true")
    v.set_defaults(func=cmd_void)

    b = sub.add_parser("balance", help="what is owed, as of a date")
    b.add_argument("who")
    b.add_argument("--as-of", dest="as_of", default="")
    b.add_argument("--lender", default="")
    b.add_argument("--borrower", default="")
    b.add_argument("--contract", default="")
    b.set_defaults(func=cmd_balance)

    s = sub.add_parser("statement", help="every row, with a running balance")
    s.add_argument("who")
    s.add_argument("--as-of", dest="as_of", default="")
    s.add_argument("--lender", default="")
    s.add_argument("--borrower", default="")
    s.add_argument("--contract", default="")
    s.set_defaults(func=cmd_statement)

    pj = sub.add_parser("projection", help="what interest would have accrued (not owed)")
    pj.add_argument("who")
    pj.add_argument("--as-of", dest="as_of", default="")
    pj.add_argument("--lender", default="")
    pj.add_argument("--borrower", default="")
    pj.add_argument("--contract", default="")
    pj.set_defaults(func=cmd_projection)

    pf = sub.add_parser("portfolio", help="totals across any set of contracts")
    pf.add_argument("--owner", default="")
    pf.add_argument("--lender", default="")
    pf.add_argument("--borrower", default="")
    pf.add_argument("--contract", default="")
    pf.add_argument("--currency", default="")
    pf.add_argument("--by", dest="group_by", default="contract",
                    choices=list(queries.GROUP_BY_CHOICES))
    pf.add_argument("--as-of", dest="as_of", default="")
    pf.add_argument("--projection", action="store_true",
                    help="also project interest for every contract in scope")
    pf.set_defaults(func=cmd_portfolio)

    sr = sub.add_parser("search")
    sr.add_argument("text")
    sr.add_argument("--limit", type=int, default=50)
    sr.set_defaults(func=cmd_search)

    sy = sub.add_parser("sync", help="pull and push your records to their private remote")
    sy.set_defaults(func=cmd_sync)

    c = sub.add_parser("check", help="validate the whole ledger")
    c.set_defaults(func=cmd_check)

    ev = sub.add_parser("event", help="birthdays, anniversaries, reminders")
    evsub = ev.add_subparsers(dest="event_command", required=True)
    eva = evsub.add_parser("add")
    eva.add_argument("--summary", required=True)
    eva.add_argument("--date", required=True)
    eva.add_argument("--kind", default="birthday",
                     choices=["birthday", "anniversary", "reminder"])
    eva.add_argument("--uid", default="")
    eva.add_argument("--source", default="")
    eva.add_argument("--once", action="store_true", help="does not repeat yearly")
    eva.add_argument("--remind-days-before", dest="remind_days_before", type=int, default=7)
    eva.set_defaults(func=cmd_event_add)

    u = sub.add_parser("upcoming", help="what is coming up")
    u.add_argument("--days", type=int, default=365)
    u.add_argument("--on", default="", help="a single date instead of a window")
    u.add_argument("--from", dest="frm", default="")
    u.set_defaults(func=cmd_upcoming)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _out(args.func(args), args.json)
    except DaybookError as exc:
        message = {"status": "error", "error": str(exc)}
        if args.json:
            print(json.dumps(message, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
