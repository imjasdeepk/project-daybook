"""Birthdays, anniversaries and reminders, stored as a standard iCalendar file.

The file is plain text you can read, and any phone or desktop calendar can
subscribe to it, so reminders arrive without this project running anywhere.
Recurrence is expanded by the recurring-ical-events library, never by hand.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import recurring_ical_events
from icalendar import Calendar, Event

from .store import DaybookError

PRODID = "-//project-daybook//EN"


def empty_calendar() -> Calendar:
    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", "Personal Ledger Dates")
    return cal


def read_calendar(path: Path) -> Calendar:
    if not path.exists():
        return empty_calendar()
    raw = path.read_bytes()
    if not raw.strip():
        return empty_calendar()
    try:
        return Calendar.from_ical(raw)
    except Exception as exc:  # noqa: BLE001 - surface the parser's own words
        raise DaybookError(f"{path.name} is not a valid calendar file: {exc}") from exc


def write_calendar(path: Path, cal: Calendar) -> None:
    path.write_bytes(cal.to_ical())


def make_uid(kind: str, label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return f"{kind.lower()}-{slug}"


def add_event(path: Path, *, uid: str, summary: str, on: date, kind: str = "birthday",
              yearly: bool = True, source: str = "", remind_days_before: int = 7) -> dict:
    """Append one VEVENT. Refuses to create a second event with the same UID."""
    cal = read_calendar(path)
    for component in cal.walk("VEVENT"):
        if str(component.get("UID")) == uid:
            raise DaybookError(
                f"An event with id {uid!r} already exists ({component.get('SUMMARY')}). "
                f"Use a different name, or delete the old one by hand."
            )
    ev = Event()
    ev.add("uid", uid)
    ev.add("summary", summary)
    ev.add("dtstart", on)
    ev.add("dtstamp", datetime.now())
    ev.add("transp", "TRANSPARENT")
    ev.add("categories", [kind])
    if yearly:
        ev.add("rrule", {"freq": "yearly"})
    if source:
        ev.add("description", source)
    if remind_days_before:
        ev.add("x-remind-days-before", str(remind_days_before))
    cal.add_component(ev)
    write_calendar(path, cal)
    return {"uid": uid, "summary": summary, "start": on.isoformat(),
            "recurs": "yearly" if yearly else "once", "kind": kind}


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


def occurrences(path: Path, start: date, end: date) -> list[dict]:
    """Every occurrence between start and end inclusive, soonest first."""
    cal = read_calendar(path)
    if not list(cal.walk("VEVENT")):
        return []
    # The expanded occurrences carry the occurrence date, not the original one,
    # so keep a UID -> first-date map to work out ages and anniversary counts.
    first_seen = {
        str(v.get("UID")): _as_date(v.get("DTSTART").dt)
        for v in cal.walk("VEVENT") if v.get("DTSTART") is not None
    }
    found = recurring_ical_events.of(cal).between(start, end + timedelta(days=1))
    rows = []
    for ev in found:
        when = _as_date(ev.get("DTSTART").dt)
        if when < start or when > end:
            continue
        first = first_seen.get(str(ev.get("UID")))
        row = {
            "uid": str(ev.get("UID")),
            "summary": str(ev.get("SUMMARY")),
            "date": when.isoformat(),
            "days_until": (when - date.today()).days,
            "kind": str(ev.get("CATEGORIES").cats[0]) if ev.get("CATEGORIES") else "",
            "source": str(ev.get("DESCRIPTION") or ""),
        }
        if first and first.year < when.year:
            row["years_since_first"] = when.year - first.year
        rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows


def upcoming(path: Path, days: int = 365, frm: date | None = None) -> dict:
    start = frm or date.today()
    end = start + timedelta(days=days)
    return {
        "from": start.isoformat(), "to": end.isoformat(), "window_days": days,
        "events": occurrences(path, start, end),
    }


def on_date(path: Path, when: date) -> dict:
    return {"on": when.isoformat(), "events": occurrences(path, when, when)}
