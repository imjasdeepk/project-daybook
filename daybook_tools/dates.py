"""Turning what you say about a date into an exact one.

Anything the parser cannot pin down comes back as `ambiguous` or `unknown` so
Claude asks instead of guessing. A wrong date is a wrong record.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}
UNITS = {"day": 1, "days": 1, "week": 7, "weeks": 7}


def _ok(when: date, how: str) -> dict:
    return {"status": "resolved", "date": when.isoformat(),
            "weekday": when.strftime("%A"), "interpretation": how}


def _ask(reason: str, options: list[str] | None = None) -> dict:
    return {"status": "ambiguous", "reason": reason, "options": options or []}


def _shift_months(anchor: date, months: int) -> date:
    total = anchor.month - 1 - months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def parse(expression: str, today: date | None = None) -> dict:
    """Resolve a date phrase against `today`, or explain why it cannot be resolved."""
    now = today or date.today()
    text = " ".join(expression.strip().lower().split())
    if not text:
        return {"status": "unknown", "reason": "no date given"}

    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return _ok(date(int(m[1]), int(m[2]), int(m[3])), "an exact date")
        except ValueError:
            return {"status": "unknown", "reason": f"{text} is not a real date"}

    if text in ("today", "now"):
        return _ok(now, "today")
    if text == "yesterday":
        return _ok(now - timedelta(days=1), "yesterday")
    if text == "tomorrow":
        return _ok(now + timedelta(days=1), "tomorrow")

    m = re.fullmatch(r"(\d+) (day|days|week|weeks) ago", text)
    if m:
        return _ok(now - timedelta(days=int(m[1]) * UNITS[m[2]]), f"{m[1]} {m[2]} before today")
    m = re.fullmatch(r"in (\d+) (day|days|week|weeks)", text)
    if m:
        return _ok(now + timedelta(days=int(m[1]) * UNITS[m[2]]), f"{m[1]} {m[2]} after today")
    m = re.fullmatch(r"(\d+) (month|months) ago", text)
    if m:
        return _ok(_shift_months(now, int(m[1])), f"{m[1]} {m[2]} before today")
    m = re.fullmatch(r"(\d+) (year|years) ago", text)
    if m:
        return _ok(_shift_months(now, int(m[1]) * 12), f"{m[1]} {m[2]} before today")

    m = re.fullmatch(r"(last|past) (\w+)", text)
    if m and m[2] in WEEKDAYS:
        back = (now.weekday() - WEEKDAYS[m[2]]) % 7 or 7
        when = now - timedelta(days=back)
        return _ok(when, f"the most recent {when.strftime('%A')} before today")
    m = re.fullmatch(r"next (\w+)", text)
    if m and m[1] in WEEKDAYS:
        ahead = (WEEKDAYS[m[1]] - now.weekday()) % 7 or 7
        when = now + timedelta(days=ahead)
        return _ok(when, f"the next {when.strftime('%A')} after today")

    if text in WEEKDAYS or re.fullmatch(r"this (\w+)", text) and text.split()[-1] in WEEKDAYS:
        name = text.split()[-1]
        back = (now.weekday() - WEEKDAYS[name]) % 7 or 7
        ahead = (WEEKDAYS[name] - now.weekday()) % 7 or 7
        return _ask(
            f"{name.title()} on its own could be the past one or the coming one",
            [(now - timedelta(days=back)).isoformat(), (now + timedelta(days=ahead)).isoformat()],
        )

    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? (?:of )?([a-z]+)(?: (\d{4}))?", text)
    if m and m[2] in MONTHS:
        return _month_day(int(m[1]), MONTHS[m[2]], m[3], now)
    m = re.fullmatch(r"([a-z]+) (\d{1,2})(?:st|nd|rd|th)?,? ?(\d{4})?", text)
    if m and m[1] in MONTHS:
        return _month_day(int(m[2]), MONTHS[m[1]], m[3], now)

    if re.fullmatch(r"\d{1,2}[/.]\d{1,2}[/.]\d{2,4}", text):
        return _ask("that could be day/month or month/day", ["write it as YYYY-MM-DD"])
    return {"status": "unknown", "reason": f"I cannot turn {expression!r} into a date"}


def _month_day(day: int, month: int, year_text: str | None, now: date) -> dict:
    if year_text:
        try:
            return _ok(date(int(year_text), month, day), "an exact date")
        except ValueError:
            return {"status": "unknown", "reason": "that is not a real date"}
    try:
        this_year = date(now.year, month, day)
    except ValueError:
        return {"status": "unknown", "reason": "that is not a real date"}
    last_year = date(now.year - 1, month, day)
    if this_year <= now:
        return _ok(this_year, f"{this_year.strftime('%d %B')} this year")
    return _ask(
        "no year given, and that date has not happened yet this year",
        [last_year.isoformat(), this_year.isoformat()],
    )
