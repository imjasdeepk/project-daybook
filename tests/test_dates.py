from datetime import date

import pytest

from daybook_tools.dates import parse

TUESDAY = date(2026, 9, 8)


@pytest.mark.parametrize("phrase,expected", [
    ("2026-09-08", "2026-09-08"),
    ("today", "2026-09-08"),
    ("yesterday", "2026-09-07"),
    ("tomorrow", "2026-09-09"),
    ("last tuesday", "2026-09-01"),
    ("last friday", "2026-09-04"),
    ("next monday", "2026-09-14"),
    ("3 weeks ago", "2026-08-18"),
    ("10 days ago", "2026-08-29"),
    ("6 months ago", "2026-03-08"),
    ("2 years ago", "2024-09-08"),
    ("14 March 1958", "1958-03-14"),
    ("14th of march 1958", "1958-03-14"),
    ("march 14", "2026-03-14"),
])
def test_resolves(phrase, expected):
    assert parse(phrase, TUESDAY) == {"status": "resolved", **parse(phrase, TUESDAY)}
    assert parse(phrase, TUESDAY)["date"] == expected


@pytest.mark.parametrize("phrase", ["tuesday", "this friday", "03/04/2026", "december 25"])
def test_asks_rather_than_guessing(phrase):
    assert parse(phrase, TUESDAY)["status"] == "ambiguous"


@pytest.mark.parametrize("phrase", ["banana", "", "2026-02-30", "the other day"])
def test_unknown(phrase):
    assert parse(phrase, TUESDAY)["status"] == "unknown"


def test_leap_day_is_real():
    assert parse("29 February 2024", TUESDAY)["date"] == "2024-02-29"
    assert parse("29 February 2023", TUESDAY)["status"] == "unknown"


def test_month_shift_clamps_to_end_of_month():
    assert parse("1 months ago", date(2026, 3, 31))["date"] == "2026-02-28"
