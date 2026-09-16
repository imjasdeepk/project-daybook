"""The `daybook` command. Every answer Claude gives comes from one of these."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from importlib import metadata as _metadata
from pathlib import Path

from beancount import loader

from . import __version__ as _fallback_version
from . import capture, dates, events, interest, notes, queries
from .contracts import (
    Contract,
    add_contract,
    build_contract,
    load_contracts,
    set_terms,
)
from .entities import (
    Entity,
    add_alias,
    add_entity,
    load_entities,
    resolve,
    self_entity,
    set_book,
)
from .store import (
    LOCATION_FILENAME,
    DaybookError,
    cite,
    commit_field,
    git_commit,
    git_sync,
    load,
    paths,
    slugify,
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


def _open_iou(entries, entities, lender_slug: str, borrower_slug: str, args, root: Path):
    """Record a debt nobody agreed terms for, without stopping to ask.

    Not everything somebody owes you is a loan. An IOU carries no rate, method
    or day count -- writing 0% would state a term nobody set -- but it is the
    same kind of record as a loan, so one query path counts both and an IOU can
    never be quietly missing from a total.
    """
    p = paths()
    lender = next(e for e in entities if e.slug == lender_slug)
    borrower = next(e for e in entities if e.slug == borrower_slug)
    existing, _ = load_contracts(entries, entities, root)
    started = _as_of(args.date) or date.today()
    contract = build_contract(
        lender, borrower, entities, rate="", started=started,
        currency=(args.currency or "").upper(),
        note="IOU: no interest terms agreed",
        taken_ids=frozenset(c.contract_id for c in existing),
    )
    add_contract(p, contract, existing)
    return contract


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
            # Only invent a record when there is genuinely nothing between these
            # two. If something exists the other way round, the wording and the
            # record disagree -- which is a question, not an IOU.
            reversed_pair = [c for c in contracts
                             if c.lender_slug == borrower_slug
                             and c.borrower_slug == lender_slug]
            if reversed_pair:
                existing = "; ".join(
                    f"{c.lender_slug} lends to {c.borrower_slug} ({c.citation})"
                    for c in reversed_pair
                )
                raise DaybookError(
                    f"That runs the opposite way to what is on record: {existing}. "
                    f"Use the matching kind, or name --lender and --borrower explicitly."
                )
            return _open_iou(entries, entities, lender_slug, borrower_slug, args, root)
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
        # Direction is unknowable from a bare `principal` between two people,
        # so this is the one case that still has to ask.
        raise DaybookError(
            f"Nothing is on record between {owner.name!r} and {who.name!r}, and "
            f"--kind {args.kind} does not say which way it runs. Use --kind lend or "
            f"--kind borrow, or name --lender and --borrower."
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


# The folder holding this package -- a git checkout in the ordinary case, a
# `uv tool install` shim's site-packages otherwise. Records created under it
# would land in (or ride along with) the tool's own storage, and for a git
# checkout, `git_repo_for`'s nearest-enclosing-repository walk would commit
# them straight into the public repository. Refused, not silently allowed.
_TOOL_ROOT = Path(__file__).resolve().parent.parent


def _refuse_inside_tool(target: Path, what: str) -> None:
    resolved = target.resolve()
    if resolved == _TOOL_ROOT or resolved.is_relative_to(_TOOL_ROOT):
        raise DaybookError(
            f"{resolved} is inside {_TOOL_ROOT}, where the daybook tool itself lives. "
            f"Records are never kept there -- pick a folder outside it for your {what}, "
            "for example ~/Documents/daybook."
        )


def cmd_init(args) -> dict:
    """Create a ledger. Name a folder and the records go straight into it."""
    called_from = Path.cwd()
    if args.folder:
        root = Path(args.folder).expanduser().resolve()
        records = root
    else:
        root = called_from
        records = root / "ledger"
    _refuse_inside_tool(records, "ledger")
    root.mkdir(parents=True, exist_ok=True)
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
    result["commit"] = commit_field(git_commit(p.root, f"entity: add {entity.name}", [p.accounts]))
    return result


def cmd_entity_alias(args) -> dict:
    p = paths()
    entries, _ = load(p)
    all_entities = load_entities(entries, p.root)
    entity = _require_entity(entries, args.name, p.root)
    result = add_alias(p, entity, [a.strip() for a in args.add.split(",")], all_entities)
    load(p)
    result["commit"] = commit_field(git_commit(p.root, f"entity: alias {entity.name}", [p.accounts]))
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
    result["commit"] = commit_field(git_commit(p.root, f"entity: book {entity.name} {args.book}", [p.accounts]))
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
    result["commit"] = commit_field(git_commit(
        p.root, f"contract: add {lender.name} -> {borrower.name}", [p.accounts]
    ))
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

def cmd_contract_terms(args) -> dict:
    """Give an existing IOU the terms it turned out to have."""
    p = paths()
    entries, _ = load(p)
    entities = load_entities(entries, p.root)
    contracts, _ = load_contracts(entries, entities, p.root)
    matches = [c for c in contracts
               if c.contract_id == args.contract or c.account == args.contract]
    if not matches:
        raise DaybookError(f"No record found matching {args.contract!r}.")
    found = matches[0]
    if found.has_terms:
        raise DaybookError(
            f"{found.contract_id} already carries terms ({found.rate_percent_pa}% "
            f"{found.method}, {found.citation}). Terms are not edited in place: record a "
            f"new contract and move the balance across with `void` if they really changed."
        )
    updated = set_terms(p, found, rate=args.rate, method=args.method,
                        compounding=args.compounding, day_count=args.day_count)
    load(p)
    updated["commit"] = commit_field(git_commit(p.root, f"contract: terms for {found.contract_id}",
                                                 [p.accounts]))
    return updated


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
    was_new = contract is not None and not any(
        c.contract_id == contract.contract_id for c in contracts
    )
    result = capture.commit_entry(p, plan, commit=not args.no_commit)
    result["status"] = "recorded"
    if contract is not None and not contract.has_terms:
        # Say the assumption out loud. Nothing was silently decided, and the
        # user can correct it in one command.
        result["contract_kind"] = "iou"
        result["terms"] = "none agreed"
        result["note"] = (
            f"Recorded {'against a new' if was_new else 'against an'} IOU "
            f"({contract.contract_id}) with no interest terms. If it earns interest, "
            f"run: daybook contract terms {contract.contract_id} --rate <rate>"
        )
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



# -------------------------------------------------------------------------- notes

def _notes_context(args=None):
    """The notes folder and how it files things."""
    root = notes.notes_root()
    return root, notes.read_config(root)["period"]


def _note_date(args) -> date:
    """The date a note is filed under. Anything vague goes through the parser,
    which asks rather than guesses, the same as everywhere else."""
    if getattr(args, "date", None):
        return _as_of(args.date)
    return date.today()


def _resolve_people(names: list[str]) -> list[str]:
    """Turn spoken names into the names the ledger uses, when a ledger exists.

    A notes-only folder has no entities, so this falls back to what was typed.
    An `ambiguous` result is passed through as a refusal: the skill asks, this
    never picks. The import is local so notes stay free of Beancount.
    """
    if not names:
        return []
    try:
        from .store import load
        from .store import paths as ledger_paths
        entries, _ = load(ledger_paths())
        known = load_entities(entries, ledger_paths().root)
    except Exception:  # noqa: BLE001 - no ledger is the ordinary notes-only case
        return names
    settled = []
    for name in names:
        found = resolve(name, known)
        if found["status"] == "resolved":
            settled.append(found["entity"]["name"])
        elif found["status"] == "ambiguous":
            options = ", ".join(
                f"{c['name']} ({c['citation']})" for c in found["candidates"]
            )
            raise DaybookError(
                f"{name!r} could be more than one person: {options}. "
                f"Say which, or pass the full name."
            )
        else:
            settled.append(name)
    return settled


def cmd_note_init(args) -> dict:
    folder = Path(args.folder).expanduser().resolve() if args.folder else Path.cwd() / "notes"
    _refuse_inside_tool(folder, "notes")
    result = notes.init(folder, args.period)
    called_from = Path.cwd()
    if args.remember and folder != called_from and folder.parent != called_from:
        pointer = called_from / notes.LOCATION_FILENAME
        pointer.write_text(str(folder) + "\n", encoding="utf-8")
        result["remembered_in"] = str(pointer)
    result["next"] = 'Write your first note: daybook note add --title "..." --body "..."'
    return result


def cmd_note_add(args) -> dict:
    root, period = _notes_context()
    body = sys.stdin.read() if args.stdin else (args.body or "")
    note = notes.Note(
        date=_note_date(args),
        time=args.time or datetime.now().strftime("%H:%M"),
        title=args.title,
        kind=args.kind,
        who=_resolve_people([w.strip() for w in (args.who or "").split(",") if w.strip()]),
        tags=[t.strip() for t in (args.tags or "").split(",") if t.strip()],
        when=args.when or "",
        source=args.source or "",
        body=body,
    )
    result = notes.add_note(root, note, period=period)
    if not args.no_commit:
        commit = commit_field(git_commit(root, f"note: {note.title}",
                                         [root / f for f in result["files_touched"]]))
        if commit is not None:
            result["commit"] = commit
    return result


def cmd_note_find(args) -> dict:
    root, _ = _notes_context()
    return notes.find(
        root, args.text or "",
        who=[w.strip() for w in (args.who or "").split(",") if w.strip()],
        tags=[t.strip() for t in (args.tag or "").split(",") if t.strip()],
        kind=args.kind or "",
        since=_as_of(args.since), until=_as_of(args.until), limit=args.limit,
    )


def cmd_note_show(args) -> dict:
    root, _ = _notes_context()
    found = notes.find_by_citation(root, args.citation)
    if found is None:
        raise DaybookError(f"No note at {args.citation}.")
    return found.to_dict()


def cmd_note_week(args) -> dict:
    root, period = _notes_context()
    return notes.period_view(root, _as_of(args.date) or date.today(), period)


def cmd_note_on(args) -> dict:
    root, _ = _notes_context()
    return notes.on_date(root, _as_of(args.date) or date.today())


def cmd_note_topic(args) -> dict:
    root, _ = _notes_context()
    return notes.topic(root, args.tag, limit=args.limit)


def cmd_note_tally(args) -> dict:
    root, _ = _notes_context()
    return notes.tally(root, args.note_command)


def cmd_note_agenda(args) -> dict:
    root, _ = _notes_context()
    return notes.agenda(root, days=args.days, frm=_as_of(args.frm))


def cmd_note_amend(args) -> dict:
    root, period = _notes_context()
    body = sys.stdin.read() if args.stdin else (args.body or "")
    if not body.strip():
        raise DaybookError("An amendment needs a body: say what the correction is.")
    result = notes.amend(root, args.citation, body, period=period, at=_as_of(args.date))
    if not args.no_commit:
        commit = commit_field(git_commit(root, f"note: amends {args.citation}",
                                         [root / f for f in result["files_touched"]]))
        if commit is not None:
            result["commit"] = commit
    return result


def cmd_note_reindex(args) -> dict:
    root, _ = _notes_context()
    return notes.reindex(root)


def cmd_note_doctor(args) -> dict:
    root, _ = _notes_context()
    return notes.find_conflicts(root)


# --------------------------------------------------------------------------- main

def _version() -> str:
    """The installed package version, or the source checkout's fallback."""
    try:
        return _metadata.version("project-daybook")
    except _metadata.PackageNotFoundError:
        return _fallback_version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daybook",
        description="A daybook whose answers are retrieved or computed, never recalled.")
    parser.add_argument("--version", action="version", version=f"daybook {_version()}")
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
    ka.add_argument("--rate", default="",
                    help="annual interest rate, e.g. 10.2. Leave it off to record an "
                         "IOU: a debt with no interest terms")
    ka.add_argument("--method", default="", choices=["", "simple", "compound"])
    ka.add_argument("--compounding", default="",
                    choices=["", "annual", "semiannual", "quarterly", "monthly", "daily"])
    ka.add_argument("--day-count", dest="day_count", default="")
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
    kt = ksub.add_parser("terms", help="give an IOU the interest terms it turned out to have")
    kt.add_argument("contract", help="contract id or account")
    kt.add_argument("--rate", required=True)
    kt.add_argument("--method", default="simple", choices=["simple", "compound"])
    kt.add_argument("--compounding", default="")
    kt.add_argument("--day-count", dest="day_count", default="actual/365")
    kt.set_defaults(func=cmd_contract_terms)

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


    n = sub.add_parser("note", help="your diary and knowledge base")
    nsub = n.add_subparsers(dest="note_command", required=True)

    ni = nsub.add_parser("init", help="create a notes folder")
    ni.add_argument("folder", nargs="?", default="",
                    help="where your notes go, e.g. ~/Documents/daybook/notes")
    ni.add_argument("--period", choices=["week", "month"], default="week",
                    help="one file per week (default) or per month")
    ni.add_argument("--no-remember", dest="remember", action="store_false",
                    help="do not write a .daybook-notes-root pointer here")
    ni.set_defaults(func=cmd_note_init)

    na = nsub.add_parser("add", help="write something down")
    na.add_argument("--title", required=True,
                    help="a real summary: it is all the index shows")
    na.add_argument("--body", default="")
    na.add_argument("--stdin", action="store_true", help="read the body from stdin")
    na.add_argument("--kind", default="note",
                    help="log, decision, meeting, travel, idea, contact ... free text")
    na.add_argument("--who", default="", help="comma separated")
    na.add_argument("--tags", default="", help="comma separated; these are your topics")
    na.add_argument("--date", default="", help="defaults to today")
    na.add_argument("--time", default="", help="HH:MM, defaults to now")
    na.add_argument("--when", default="",
                    help="a date this note is ABOUT: 2026-10-03 or 2026-10-03..2026-10-09")
    na.add_argument("--source", default="", help="the user's own words, verbatim")
    na.add_argument("--no-commit", action="store_true")
    na.set_defaults(func=cmd_note_add)

    nf = nsub.add_parser("find", help="search your notes")
    nf.add_argument("text", nargs="?", default="")
    nf.add_argument("--who", default="")
    nf.add_argument("--tag", default="")
    nf.add_argument("--kind", default="")
    nf.add_argument("--since", default="")
    nf.add_argument("--until", default="")
    nf.add_argument("--limit", type=int, default=20)
    nf.set_defaults(func=cmd_note_find)

    ns = nsub.add_parser("show", help="one note in full, by citation")
    ns.add_argument("citation", help="e.g. 2026/2026-W37.md:5")
    ns.set_defaults(func=cmd_note_show)

    nw = nsub.add_parser("week", help="a whole period file")
    nw.add_argument("date", nargs="?", default="")
    nw.set_defaults(func=cmd_note_week)

    nm = nsub.add_parser("month", help="a whole period file (same as week in month mode)")
    nm.add_argument("date", nargs="?", default="")
    nm.set_defaults(func=cmd_note_week)

    no = nsub.add_parser("on", help="everything written on one day")
    no.add_argument("date")
    no.set_defaults(func=cmd_note_on)

    nt = nsub.add_parser("topic", help="everything filed under one tag")
    nt.add_argument("tag")
    nt.add_argument("--limit", type=int, default=50)
    nt.set_defaults(func=cmd_note_topic)

    for name, helptext in (("tags", "every tag, with counts"),
                           ("people", "everyone mentioned, with counts"),
                           ("kinds", "every kind of note, with counts")):
        tally_parser = nsub.add_parser(name, help=helptext)
        tally_parser.set_defaults(func=cmd_note_tally)

    ng = nsub.add_parser("agenda", help="notes about a date that is coming up")
    ng.add_argument("--days", type=int, default=30)
    ng.add_argument("--from", dest="frm", default="")
    ng.set_defaults(func=cmd_note_agenda)

    nd = nsub.add_parser("amend", help="correct a note by appending to it")
    nd.add_argument("citation")
    nd.add_argument("--body", default="")
    nd.add_argument("--stdin", action="store_true")
    nd.add_argument("--date", default="")
    nd.add_argument("--no-commit", action="store_true")
    nd.set_defaults(func=cmd_note_amend)

    nr = nsub.add_parser("reindex", help="rebuild every index from the notes")
    nr.set_defaults(func=cmd_note_reindex)

    nn = nsub.add_parser("doctor", help="check for sync conflicts and stale indexes")
    nn.set_defaults(func=cmd_note_doctor)

    rc = sub.add_parser("recall", help="search your notes (same as `note find`)")
    rc.add_argument("text", nargs="?", default="")
    rc.add_argument("--who", default="")
    rc.add_argument("--tag", default="")
    rc.add_argument("--kind", default="")
    rc.add_argument("--since", default="")
    rc.add_argument("--until", default="")
    rc.add_argument("--limit", type=int, default=20)
    rc.set_defaults(func=cmd_note_find)

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
