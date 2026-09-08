"""The `ledger` command. Every answer Claude gives comes from one of these."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from beancount import loader

from . import capture, dates, events, interest, queries
from .entities import Entity, add_alias, add_entity, load_entities, resolve
from .store import (
    LOCATION_FILENAME, LedgerError, Paths, cite, git_sync, load, paths, slugify,
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
        raise LedgerError(f"Could not read the date {value!r}: {parsed.get('reason', 'ambiguous')}")
    return date.fromisoformat(parsed["date"])


def _require_entity(entries, who: str, root: Path) -> Entity:
    found = resolve(who, load_entities(entries, root))
    if found["status"] == "resolved":
        data = found["entity"]
        return next(e for e in load_entities(entries, root) if e.slug == data["slug"])
    if found["status"] == "ambiguous":
        names = ", ".join(f"{c['name']} ({c['citation']})" for c in found["candidates"])
        raise LedgerError(
            f"{who!r} is not clear enough to act on. Did you mean: {names}? "
            f"Ask, then use the exact name or add an alias."
        )
    raise LedgerError(
        f"I have no record of {who!r}. Create it first with: "
        f"ledger entity add --name \"<full name>\" --aliases \"{who}\""
    )


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
    p = Paths(root)
    records.mkdir(parents=True, exist_ok=True)
    main_file = records / "main.beancount"
    accounts_file = records / "accounts.beancount"
    events_file = records / "dates.ics"
    if main_file.exists() and not args.force:
        raise LedgerError(f"{main_file} already exists. Pass --force only if you mean to replace it.")
    currencies = [c.strip().upper() for c in args.currencies.split(",") if c.strip()]
    if not currencies:
        raise LedgerError("Give at least one currency, for example --currencies INR,USD")
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
        raise LedgerError("The new ledger does not parse: " + "; ".join(e.message for e in errors))

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
        'Record something with: ledger add --kind lend --who "<name>" --amount 100 '
        f"--currency {currencies[0]}"
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
        "next": "Add a private remote: gh repo create <you>/my-ledger --private "
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
    entity = Entity(
        slug=slugify(args.slug or args.name),
        name=args.name,
        type=args.type,
        relation=args.relation or "",
        aliases=[a.strip() for a in (args.aliases or "").split(",") if a.strip()],
        default_currency=(args.currency or "").upper(),
        rate_percent_pa=args.rate or "",
        method=(args.method or "").lower(),
        compounding=(args.compounding or "").lower(),
        day_count=args.day_count,
    )
    if entity.rate_percent_pa and not entity.method:
        raise LedgerError("A rate needs a method: pass --method simple or --method compound.")
    if entity.method == "compound" and not entity.compounding:
        entity.compounding = "annual"
    result = add_entity(p, entity, args.opened or date.today(), existing)
    load(p)
    return result


def cmd_entity_alias(args) -> dict:
    p = paths()
    entries, _ = load(p)
    all_entities = load_entities(entries, p.root)
    entity = _require_entity(entries, args.name, p.root)
    result = add_alias(p, entity, [a.strip() for a in args.add.split(",")], all_entities)
    load(p)
    return result


def cmd_entity_list(args) -> dict:
    entries, _ = load()
    return {"entities": [e.to_dict() for e in load_entities(entries, paths().root)]}


def cmd_date(args) -> dict:
    return dates.parse(args.expression, _as_of(args.today))


def cmd_today(args) -> dict:
    today = date.today()
    return {"date": today.isoformat(), "weekday": today.strftime("%A")}


# ------------------------------------------------------------------------ capture

def cmd_add(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entity = None
    if args.who:
        entity = _require_entity(entries, args.who, p.root)
    elif args.kind not in ("spend", "receive"):
        raise LedgerError(f"A {args.kind} entry needs --who.")

    when = _as_of(args.date) or date.today()
    currency = (args.currency or (entity.default_currency if entity else "")).upper()
    if not currency:
        raise LedgerError("No currency given and no default recorded. Pass --currency.")

    plan = capture.plan_entry(
        args.kind, entity=entity, amount=capture.parse_amount(args.amount),
        currency=currency, when=when, narration=args.note or args.kind.title(),
        source=args.source or "", category=args.category or "",
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
    entity = _require_entity(entries, args.who, p.root) if args.who else None
    when = _as_of(args.date) or date.today()
    plan = capture.plan_entry(
        args.kind, entity=entity, amount=capture.parse_amount(args.amount),
        currency=(args.currency or (entity.default_currency if entity else "")).upper(),
        when=when, narration=args.note or args.kind.title(),
        source=args.source or "", category=args.category or "",
    )
    result = capture.reverse_entry(p, plan, args.voids, commit=not args.no_commit)
    result["status"] = "reversed"
    return result


# ------------------------------------------------------------------------ answers

def cmd_balance(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entity = _require_entity(entries, args.who, p.root)
    return queries.balance(entries, entity, _as_of(args.as_of), p.root)


def cmd_statement(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entity = _require_entity(entries, args.who, p.root)
    return queries.statement(entries, entity, _as_of(args.as_of), p.root)


def cmd_projection(args) -> dict:
    p = paths()
    entries, _ = load(p)
    entity = _require_entity(entries, args.who, p.root)
    return interest.project(entries, entity, _as_of(args.as_of) or date.today(), p.root)


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
    return {
        "status": "valid",
        "file": str(p.main),
        "transactions": len(queries.transactions(entries)),
        "entities": len(load_entities(entries, p.root)),
    }


# ------------------------------------------------------------------------- events

def cmd_event_add(args) -> dict:
    p = paths()
    when = _as_of(args.date)
    if when is None:
        raise LedgerError("An event needs a date: pass --date.")
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
        prog="ledger", description="A personal ledger whose answers are computed, never recalled.")
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
                   help="do not write a .ledger-root pointer here")
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
    ea.add_argument("--rate", default="", help="annual interest rate, e.g. 8")
    ea.add_argument("--method", default="", choices=["", "simple", "compound"])
    ea.add_argument("--compounding", default="",
                    choices=["", "annual", "semiannual", "quarterly", "monthly", "daily"])
    ea.add_argument("--day-count", dest="day_count", default="actual/365")
    ea.add_argument("--opened", type=date.fromisoformat, default=None)
    ea.set_defaults(func=cmd_entity_add)
    el = esub.add_parser("alias")
    el.add_argument("name")
    el.add_argument("--add", required=True, help="comma separated aliases")
    el.set_defaults(func=cmd_entity_alias)
    ez = esub.add_parser("list")
    ez.set_defaults(func=cmd_entity_list)

    d = sub.add_parser("date", help="turn a phrase into an exact date")
    d.add_argument("expression")
    d.add_argument("--today", default="")
    d.set_defaults(func=cmd_date)

    t = sub.add_parser("today")
    t.set_defaults(func=cmd_today)

    a = sub.add_parser("add", help="record something")
    a.add_argument("--kind", required=True, choices=sorted(capture.KINDS))
    a.add_argument("--who", default="")
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
    v.add_argument("--kind", required=True, choices=sorted(capture.KINDS))
    v.add_argument("--who", default="")
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
    b.set_defaults(func=cmd_balance)

    s = sub.add_parser("statement", help="every row, with a running balance")
    s.add_argument("who")
    s.add_argument("--as-of", dest="as_of", default="")
    s.set_defaults(func=cmd_statement)

    pj = sub.add_parser("projection", help="what interest would have accrued (not owed)")
    pj.add_argument("who")
    pj.add_argument("--as-of", dest="as_of", default="")
    pj.set_defaults(func=cmd_projection)

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
    except LedgerError as exc:
        message = {"status": "error", "error": str(exc)}
        if args.json:
            print(json.dumps(message, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
