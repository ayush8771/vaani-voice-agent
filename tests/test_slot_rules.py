"""Rule-based slot reading. Today is Wednesday 18 November 2026 in these tests."""

from datetime import date

import pytest

import slot_rules as r

TODAY = date(2026, 11, 18)


@pytest.mark.parametrize("text,expected", [
    ("I wanted to book a table for 4 to for tomorrow at 8:00 PM", "2026-11-19"),
    ("Book for 18th November", "2026-11-18"),
    ("on November 20", "2026-11-20"),
    ("20 Nov 2026", "2026-11-20"),
    ("21st of december", "2026-12-21"),
    ("20/11", "2026-11-20"),
    ("2026-11-25", "2026-11-25"),
    ("today", "2026-11-18"),
    ("tonight at 9", "2026-11-18"),
    ("day after tomorrow", "2026-11-20"),
    ("kal shaam", "2026-11-19"),
    ("this Friday", "2026-11-20"),
    ("next Wednesday", "2026-11-25"),
    ("on saturday", "2026-11-21"),
    ("on the 25th", "2026-11-25"),
    ("on the 5th", "2026-12-05"),
    ("at 8:00 PM", None),
    ("for 4 people", None),
    ("my number is 98765-43210", None),
    ("31st February", None),
])
def test_dates(text, expected):
    assert r.parse_date(text, TODAY) == expected


@pytest.mark.parametrize("text,expected", [
    ("tomorrow at 8:00 PM", "20:00"),
    ("8 pm", "20:00"),
    ("8:30pm", "20:30"),
    ("7.45 p.m.", "19:45"),
    ("12 am", "00:00"),
    ("12 pm", "12:00"),
    ("at noon", "12:00"),
    ("20:00", "20:00"),
    ("at 8", "20:00"),
    ("8 baje", "20:00"),
    ("at 11 in the morning", "11:00"),
    ("8:00 AM", "08:00"),
    ("for 4 people", None),
    ("Book for 18th November", None),
    ("at 25 people", None),
])
def test_times(text, expected):
    assert r.parse_time(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("I wanted to book a table for 4 to for tomorrow at 8:00 PM", 4),
    ("25 people", 25),
    ("table for two", 2),
    ("we are 6 guests", 6),
    ("party of 8", 8),
    ("for 8 pm", None),
    ("Book for 18th November", None),
    ("at 8:00 PM", None),
])
def test_people(text, expected):
    assert r.parse_people(text) == expected


@pytest.mark.parametrize("text,asked,expected", [
    ("Uh, thanks. My name is uh Ravi and I want to make a new booking", False, "Ravi"),
    ("under the name Priya Sharma, phone 9876543210", False, "Priya Sharma"),
    ("this is Ravi", False, "Ravi"),
    ("I am looking for a table", False, None),
    ("Ravi", True, "Ravi"),
    ("ravi kumar", True, "Ravi Kumar"),
    ("Ravi", False, None),
    ("yes", True, None),
    ("8310398093", True, None),
])
def test_names(text, asked, expected):
    assert r.parse_name(text, asked=asked) == expected
