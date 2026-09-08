"""Birthdays and anniversaries, including the awkward dates."""
from __future__ import annotations

from datetime import date

import pytest

from ledger_tools import events
from ledger_tools.store import LedgerError


def test_yearly_birthday_is_found_a_year_out(paths, run):
    run("event", "add", "--summary", "Dad birthday", "--date", "14 March 1958")
    found = events.on_date(paths.events, date(2027, 3, 14))["events"]
    assert len(found) == 1
    assert found[0]["years_since_first"] == 69


def test_upcoming_window_crosses_the_year_boundary(paths, run):
    run("event", "add", "--summary", "January date", "--date", "1958-01-05")
    run("event", "add", "--summary", "December date", "--date", "1958-12-20")
    found = events.upcoming(paths.events, days=365, frm=date(2026, 9, 8))["events"]
    dates = [e["date"] for e in found]
    assert dates == ["2026-12-20", "2027-01-05"], dates


def test_leap_day_recurs_on_leap_years(paths, run):
    run("event", "add", "--summary", "Leap birthday", "--date", "2020-02-29")
    assert events.on_date(paths.events, date(2028, 2, 29))["events"]


def test_non_recurring_event_appears_once(paths, run):
    run("event", "add", "--summary", "One off", "--date", "2027-05-01", "--once")
    assert events.on_date(paths.events, date(2027, 5, 1))["events"]
    assert not events.on_date(paths.events, date(2028, 5, 1))["events"]


def test_duplicate_uid_is_refused(paths, run):
    run("event", "add", "--summary", "Dad birthday", "--date", "1958-03-14")
    with pytest.raises(LedgerError, match="already exists"):
        events.add_event(paths.events, uid="birthday-dad-birthday",
                         summary="Dad birthday", on=date(1958, 3, 14))


def test_source_phrase_is_kept_on_the_event(paths, run):
    run("event", "add", "--summary", "Dad birthday", "--date", "1958-03-14",
        "--source", 'chat: "dad born 14 March 1958"')
    found = events.on_date(paths.events, date(2027, 3, 14))["events"][0]
    assert "14 March 1958" in found["source"]


def test_file_is_a_real_calendar_other_apps_can_read(paths, run):
    run("event", "add", "--summary", "Dad birthday", "--date", "1958-03-14")
    raw = paths.events.read_bytes()
    assert raw.startswith(b"BEGIN:VCALENDAR")
    assert b"RRULE:FREQ=YEARLY" in raw
    assert b"END:VCALENDAR" in raw.rstrip()


def test_empty_calendar_returns_nothing_rather_than_failing(paths):
    assert events.upcoming(paths.events, days=365)["events"] == []
