"""
memory.py — what Vaani remembers about returning callers, keyed by phone number.

Stored per caller: name, preferred language, number of visits, usual party size
and time, and dietary notes. Callers are told once that Vaani remembers their
preferences, and "forget me" deletes the profile.

Also detects dietary needs and special occasions in what the caller says, so
they reach the booking notes (for staff) and the upsell offer.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import availability as av
from database import connection

logger = logging.getLogger(__name__)

OFFERS_FILE = Path("knowledge") / "offers.json"

# Dietary needs and occasions, matched on the English (normalised) text
_DIETARY = [
    (re.compile(r"\b(nut|nuts|peanut)s?\b.*\ballerg|\ballerg\w*\b.*\b(nut|nuts|peanut)", re.I), "nut allergy"),
    (re.compile(r"\bgluten\b", re.I), "gluten-free"),
    (re.compile(r"\bjain\b", re.I), "Jain food"),
    (re.compile(r"\bvegan\b", re.I), "vegan"),
    (re.compile(r"\b(vegetarian|veg only|pure veg)\b", re.I), "vegetarian"),
    (re.compile(r"\b(wheelchair|accessible)\b", re.I), "wheelchair access"),
    (re.compile(r"\b(high chair|baby|infant|toddler)\b", re.I), "high chair"),
]
_OCCASIONS = [
    (re.compile(r"\bbirthday|जन्मदिन|janamdin|janmdin|ಹುಟ್ಟುಹಬ್ಬ", re.I), "birthday"),
    (re.compile(r"\banniversary|सालगिरह|saalgirah|ವಾರ್ಷಿಕೋತ್ಸವ", re.I), "anniversary"),
    (re.compile(r"\b(date night|proposal|propose)\b", re.I), "date night"),
    (re.compile(r"\b(business|client|corporate|office) (dinner|lunch|meeting)\b", re.I), "business meal"),
]
FORGET = re.compile(r"\b(forget me|delete my (data|details|information)|remove my (data|details)|"
                    r"mujhe bhool ja\w*)\b|मुझे भूल जा|मेरी जानकारी हटा|ನನ್ನನ್ನು ಮರೆತು|ನನ್ನ ವಿವರ\w* ಅಳಿಸಿ", re.I)
USUAL = re.compile(r"\b(usual|same as last time|same as before|like last time)\b", re.I)


def dietary_notes(text: str) -> list:
    return [label for pattern, label in _DIETARY if pattern.search(text or "")]


def occasion(text: str) -> Optional[str]:
    for pattern, label in _OCCASIONS:
        if pattern.search(text or ""):
            return label
    return None


# ── PROFILES ──────────────────────────────────────────────────────────────────
def get(phone: str) -> Optional[dict]:
    if not phone:
        return None
    with connection() as con:
        row = con.execute(
            "SELECT phone, name, lang, visits, last_people, last_time, notes FROM customers WHERE phone=?",
            (phone,),
        ).fetchone()
    if not row:
        return None
    keys = ("phone", "name", "lang", "visits", "last_people", "last_time", "notes")
    profile = dict(zip(keys, row))
    profile["notes"] = [n for n in (profile["notes"] or "").split("; ") if n]
    return profile


def remember_booking(phone: str, name: str, people: int, time: str, lang: str, notes: list) -> None:
    """Update the caller's profile after a confirmed booking."""
    existing = get(phone)
    merged = sorted(set((existing or {}).get("notes", [])) | set(notes or []))
    with connection() as con:
        con.execute(
            "INSERT INTO customers(phone, name, lang, visits, last_people, last_time, notes, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?, ?, ?) "
            "ON CONFLICT(phone) DO UPDATE SET name=excluded.name, lang=excluded.lang, visits=visits+1, "
            "last_people=excluded.last_people, last_time=excluded.last_time, notes=excluded.notes, "
            "updated_at=excluded.updated_at",
            (phone, name, lang, people, time, "; ".join(merged), av.now().isoformat(timespec="seconds")),
        )


def forget(phone: str) -> bool:
    with connection() as con:
        return con.execute("DELETE FROM customers WHERE phone=?", (phone,)).rowcount == 1


# ── UPSELL OFFERS ─────────────────────────────────────────────────────────────
def offer_for(occasion_label: Optional[str]) -> Optional[str]:
    """The owner's offer for an occasion, from knowledge/offers.json (editable)."""
    if not occasion_label or not OFFERS_FILE.exists():
        return None
    try:
        offers = json.loads(OFFERS_FILE.read_text(encoding="utf-8"))
        return offers.get(occasion_label) or None
    except (OSError, ValueError) as exc:
        logger.error("Could not read %s: %s", OFFERS_FILE, exc)
        return None
