"""Resolving the names you speak ('dad') to the account the ledger uses.

Exact matches resolve. Near matches come back as `ambiguous` with candidates so
Claude asks you rather than guessing. Unknown names come back as `unknown`.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from beancount.core import data

from .store import (
    ENTITY_ROOT, INTEREST_ROOT, LOANS_ROOT, OWED_ROOT,
    LedgerError, Paths, append_block, cite, opens, slugify,
)

FUZZY_CUTOFF = 0.72


@dataclass
class Entity:
    slug: str
    name: str
    type: str = "person"
    relation: str = ""
    aliases: list[str] = field(default_factory=list)
    default_currency: str = ""
    rate_percent_pa: str = ""
    method: str = ""
    compounding: str = ""
    day_count: str = "actual/365"
    citation: str = ""

    @property
    def anchor_account(self) -> str:
        return f"{ENTITY_ROOT}:{self.slug}"

    @property
    def loans_account(self) -> str:
        return f"{LOANS_ROOT}:{self.slug}"

    @property
    def owed_account(self) -> str:
        return f"{OWED_ROOT}:{self.slug}"

    @property
    def interest_account(self) -> str:
        return f"{INTEREST_ROOT}:{self.slug}"

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
                rate_percent_pa=str(meta.get("rate_percent_pa", "")),
                method=str(meta.get("method", "")),
                compounding=str(meta.get("compounding", "")),
                day_count=str(meta.get("day_count", "actual/365")),
                citation=cite(meta, root),
            )
        )
    return found


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
    def meta(key: str, value: str) -> None:
        if value:
            lines.append(f'  {key}: "{value}"')
    meta("name", entity.name)
    meta("type", entity.type)
    meta("relation", entity.relation)
    meta("aliases", ", ".join(entity.aliases))
    meta("default_currency", entity.default_currency)
    meta("rate_percent_pa", entity.rate_percent_pa)
    meta("method", entity.method)
    meta("compounding", entity.compounding)
    if entity.rate_percent_pa:
        meta("day_count", entity.day_count or "actual/365")
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
