"""Resolving the names you speak ('dad') to the account the ledger uses.

Exact matches resolve. Near matches come back as `ambiguous` with candidates so
Claude asks you rather than guessing. Unknown names come back as `unknown`.

Interest terms no longer live here -- see `contracts.py`. An entity is just a
name, and optionally a book this ledger keeps (`book`) and a mark for whichever
entity is the person running the tool (`self`, exposed as `is_self`).
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from beancount.core import data

from .store import ENTITY_ROOT, LedgerError, Paths, append_block, cite, opens

FUZZY_CUTOFF = 0.72


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("true", "yes", "1")


@dataclass
class Entity:
    slug: str
    name: str
    type: str = "person"
    relation: str = ""
    aliases: list[str] = field(default_factory=list)
    default_currency: str = ""
    book: bool = False
    is_self: bool = False
    citation: str = ""

    @property
    def anchor_account(self) -> str:
        return f"{ENTITY_ROOT}:{self.slug}"

    def all_names(self) -> list[str]:
        return [self.name, self.slug, *self.aliases]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["anchor_account"] = self.anchor_account
        return d


def _split_aliases(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [a.strip() for a in str(raw).split(",") if a.strip()]


def load_entities(entries, root: Path | None = None) -> list[Entity]:
    """Every `open Equity:Entities:<Slug>` directive carrying a `name` is an entity."""
    found: list[Entity] = []
    for directive in opens(entries):
        if not directive.account.startswith(ENTITY_ROOT + ":"):
            continue
        meta = directive.meta or {}
        name = meta.get("name")
        if not name:
            continue
        slug = directive.account.split(":")[-1]
        found.append(
            Entity(
                slug=slug,
                name=str(name),
                type=str(meta.get("type", "person")),
                relation=str(meta.get("relation", "")),
                aliases=_split_aliases(meta.get("aliases")),
                default_currency=str(meta.get("default_currency", "")),
                book=_truthy(meta.get("book", "")),
                is_self=_truthy(meta.get("self", "")),
                citation=cite(meta, root),
            )
        )
    return found


def book_owners(entities: list[Entity]) -> list[Entity]:
    """Entities whose books this ledger actually keeps."""
    return [e for e in entities if e.book]


def self_entity(entities: list[Entity]) -> Entity | None:
    """The one entity marked as the person running the tool, if any."""
    marked = [e for e in entities if e.is_self]
    return marked[0] if marked else None


def resolve(query: str, entities: list[Entity]) -> dict:
    """Return {'status': resolved|ambiguous|unknown, ...}.

    `resolved` means one entity matched a name or alias exactly (case-insensitive).
    `ambiguous` means either several exact matches or one-or-more close matches.
    Claude must ask the user rather than pick.
    """
    needle = query.strip().casefold()
    if not needle:
        return {"status": "unknown", "query": query, "candidates": []}

    exact = [
        e for e in entities
        if any(n.strip().casefold() == needle for n in e.all_names())
    ]
    if len(exact) == 1:
        return {"status": "resolved", "query": query, "entity": exact[0].to_dict()}
    if len(exact) > 1:
        return {
            "status": "ambiguous", "query": query,
            "reason": "several entities share that name or alias",
            "candidates": [e.to_dict() for e in exact],
        }

    # Fuzzy matching also indexes individual name tokens, so 'harjeet' can find
    # 'Harjit Singh' -- comparing only against the full string would miss it.
    lookup: dict[str, Entity] = {}
    for e in entities:
        for n in e.all_names():
            key = n.strip().casefold()
            if key:
                lookup.setdefault(key, e)
            for token in re.findall(r"[a-z0-9]+", key):
                if len(token) >= 3:
                    lookup.setdefault(token, e)
    close = difflib.get_close_matches(needle, list(lookup), n=5, cutoff=FUZZY_CUTOFF)
    seen: dict[str, Entity] = {}
    for c in close:
        ent = lookup[c]
        seen.setdefault(ent.slug, ent)
    if seen:
        return {
            "status": "ambiguous", "query": query,
            "reason": "no exact match; these are close",
            "candidates": [e.to_dict() for e in seen.values()],
        }
    return {"status": "unknown", "query": query, "candidates": []}


def format_open_directive(entity: Entity, opened_on) -> str:
    """Render the `open` directive that *is* the entity record."""
    lines = [f"{opened_on.isoformat()} open {entity.anchor_account}"]
    def meta(key: str, value) -> None:
        if value:
            lines.append(f'  {key}: "{value}"')
    meta("name", entity.name)
    meta("type", entity.type)
    meta("relation", entity.relation)
    meta("aliases", ", ".join(entity.aliases))
    meta("default_currency", entity.default_currency)
    meta("book", "true" if entity.book else "")
    meta("self", "true" if entity.is_self else "")
    return "\n".join(lines)


def add_entity(p: Paths, entity: Entity, opened_on, existing: list[Entity]) -> dict:
    """Append a new entity record. Refuses to create a second entity for a taken name."""
    clash = resolve(entity.name, existing)
    if clash["status"] == "resolved":
        raise LedgerError(
            f"{entity.name!r} already resolves to {clash['entity']['name']} "
            f"({clash['entity']['citation']}). Add an alias instead of a new entity."
        )
    for alias in entity.aliases:
        clash = resolve(alias, existing)
        if clash["status"] == "resolved":
            raise LedgerError(
                f"The alias {alias!r} already points at {clash['entity']['name']} "
                f"({clash['entity']['citation']})."
            )
    if any(e.slug == entity.slug for e in existing):
        raise LedgerError(f"An entity with the account slug {entity.slug!r} already exists.")
    if entity.is_self and any(e.is_self for e in existing):
        mine = next(e for e in existing if e.is_self)
        raise LedgerError(
            f"{mine.name!r} ({mine.citation}) is already marked --self. "
            f"Only one entity can be the person running the tool."
        )

    block = format_open_directive(entity, opened_on)
    line = append_block(p.accounts, block)
    return {
        "created": entity.to_dict(),
        "citation": f"{p.accounts.name}:{line}",
        "block": block,
    }


def add_alias(p: Paths, entity: Entity, new_aliases: list[str], all_entities: list[Entity]) -> dict:
    """Rewrite an entity's `aliases:` metadata line in place, preserving everything else."""
    for alias in new_aliases:
        found = resolve(alias, all_entities)
        if found["status"] == "resolved" and found["entity"]["slug"] != entity.slug:
            raise LedgerError(
                f"The alias {alias!r} already points at {found['entity']['name']}."
            )
    merged = list(dict.fromkeys([*entity.aliases, *[a.strip() for a in new_aliases if a.strip()]]))
    text = p.accounts.read_text(encoding="utf-8")
    lines = text.splitlines()
    anchor = f"open {entity.anchor_account}"
    start = next((i for i, ln in enumerate(lines) if anchor in ln), None)
    if start is None:
        raise LedgerError(f"Could not find the record for {entity.name} to edit.")
    end = start + 1
    while end < len(lines) and lines[end].startswith("  "):
        end += 1
    alias_line = f'  aliases: "{", ".join(merged)}"'
    for i in range(start + 1, end):
        if lines[i].strip().startswith("aliases:"):
            lines[i] = alias_line
            break
    else:
        lines.insert(end, alias_line)
    p.accounts.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"slug": entity.slug, "aliases": merged}


def set_book(p: Paths, entity: Entity, on: bool) -> dict:
    """Rewrite an entity's `book:` metadata line in place, preserving everything else.

    Turning a book on or off is account setup, not a financial transaction -- the
    same reasoning that lets `add_alias` edit a line rather than needing a void.
    """
    text = p.accounts.read_text(encoding="utf-8")
    lines = text.splitlines()
    anchor = f"open {entity.anchor_account}"
    start = next((i for i, ln in enumerate(lines) if anchor in ln), None)
    if start is None:
        raise LedgerError(f"Could not find the record for {entity.name} to edit.")
    end = start + 1
    while end < len(lines) and lines[end].startswith("  "):
        end += 1
    book_line = '  book: "true"'
    existing_idx = next(
        (i for i in range(start + 1, end) if lines[i].strip().startswith("book:")), None
    )
    if on:
        if existing_idx is not None:
            lines[existing_idx] = book_line
        else:
            lines.insert(end, book_line)
    elif existing_idx is not None:
        lines.pop(existing_idx)
    p.accounts.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"slug": entity.slug, "book": on}
