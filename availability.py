"""
availability.py — opening hours, timezone-aware "now", and table assignment.

A booking occupies one table from its start time for DINING_MINUTES.
Two bookings clash when they share a table and their time ranges overlap.
"""

from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta
from typing import Optional

from config import DAY_KEYS, settings
from database import active_tables

SLOT_STEP_MINUTES = 30


# ── TIME HELPERS ──────────────────────────────────────────────────────────────
def now() -> datetime:
    return datetime.now(settings.tz)


def today() -> date_cls:
    return now().date()


def to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def from_minutes(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def end_time(start: str) -> str:
    return from_minutes(to_minutes(start) + settings.dining_minutes)


def parse_date(value: str) -> date_cls:
    return datetime.strptime(value, "%Y-%m-%d").date()


def speak_time(hhmm: str) -> str:
    """'19:30' -> '7:30 PM', '20:00' -> '8 PM'."""
    h, m = divmod(to_minutes(hhmm), 60)
    suffix = "AM" if h % 24 < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12} {suffix}" if m == 0 else f"{h12}:{m:02d} {suffix}"


def speak_date(value: str) -> str:
    """'2026-11-20' -> 'Friday 20 November'."""
    d = parse_date(value)
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


# ── OPENING HOURS ─────────────────────────────────────────────────────────────
def windows_for(value: str) -> list:
    """Opening windows for a date as (open_minutes, close_minutes) pairs."""
    day_key = DAY_KEYS[parse_date(value).weekday()]
    return [(to_minutes(a), to_minutes(b)) for a, b in settings.opening_hours.get(day_key, [])]


def hours_phrase(value: str) -> str:
    windows = windows_for(value)
    if not windows:
        return "closed"
    return " and ".join(
        f"{speak_time(from_minutes(a))} to {speak_time(from_minutes(b))}" for a, b in windows
    )


def latest_seating_phrase(value: str) -> str:
    windows = windows_for(value)
    if not windows:
        return ""
    return speak_time(from_minutes(windows[-1][1] - settings.dining_minutes))


@dataclass
class SlotCheck:
    ok: bool
    reason: str = ""      # past | closed | outside_hours | large_group | too_large
    message: str = ""


def check_slot_rules(value: str, start: str, party: Optional[int] = None) -> SlotCheck:
    """Rules that do not depend on other bookings."""
    if party is not None and party > settings.large_group_threshold:
        return SlotCheck(False, "large_group")

    slot_start = datetime.combine(parse_date(value), datetime.min.time()).replace(
        tzinfo=settings.tz) + timedelta(minutes=to_minutes(start))
    if slot_start <= now():
        return SlotCheck(False, "past", "That time has already passed. Could you choose a later time?")

    windows = windows_for(value)
    if not windows:
        return SlotCheck(False, "closed",
                         f"We are closed on {speak_date(value)}. Could you choose another day?")

    s = to_minutes(start)
    e = s + settings.dining_minutes
    if not any(a <= s and e <= b for a, b in windows):
        return SlotCheck(
            False, "outside_hours",
            f"On {speak_date(value)} we are open {hours_phrase(value)}, "
            f"and the last seating is at {latest_seating_phrase(value)}. What time works for you?",
        )
    return SlotCheck(True)


# ── TABLE ASSIGNMENT ──────────────────────────────────────────────────────────
def find_free_table(con, value: str, start: str, party: int):
    """Smallest configured table that seats the party and has no overlapping booking."""
    finish = end_time(start)
    for table_id, label, seats in active_tables(con):
        if seats < party:
            continue
        clash = con.execute(
            "SELECT 1 FROM bookings WHERE table_id=? AND date=? AND status='confirmed' "
            "AND time < ? AND end_time > ? LIMIT 1",
            (table_id, value, finish, start),
        ).fetchone()
        if not clash:
            return table_id, label, seats
    return None


def largest_table() -> int:
    return max(settings.table_seats, default=0)


def suggest_alternatives(con, value: str, start: str, party: int, limit: int = 3) -> list:
    """Nearest valid start times on the same day that have a free table."""
    target = to_minutes(start)
    candidates = []
    for a, b in windows_for(value):
        t = a
        while t + settings.dining_minutes <= b:
            candidates.append(t)
            t += SLOT_STEP_MINUTES
    candidates.sort(key=lambda t: (abs(t - target), t))

    found = []
    for t in candidates:
        hhmm = from_minutes(t)
        if hhmm == start or not check_slot_rules(value, hhmm, party).ok:
            continue
        if find_free_table(con, value, hhmm, party):
            found.append(hhmm)
        if len(found) >= limit:
            break
    return sorted(found)
