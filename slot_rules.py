"""
slot_rules.py — rule-based reading of booking details from what the caller said.

Dates, times, party size, phone numbers and names follow a handful of spoken
patterns. Reading them with rules is instant and never times out, so the LLM is
only asked when these rules find nothing.

All functions return None when unsure; they never guess.
"""

import re
from datetime import date, timedelta
from typing import Optional

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
WEEKDAYS = {d: i for i, d in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])}
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
}
_NUM = r"(\d{1,3}|" + "|".join(NUMBER_WORDS) + r")"
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|" \
         r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_FILLERS = re.compile(r"\b(uh+|um+|hmm+|er+|ah+|okay|ok|so|well|please|ji)\b[,.]?", re.I)


def _num(token: str) -> Optional[int]:
    token = token.lower()
    return int(token) if token.isdigit() else NUMBER_WORDS.get(token)


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


# ── DATE ──────────────────────────────────────────────────────────────────────
def parse_date(text: str, today: date) -> Optional[str]:
    t = (text or "").lower()

    if re.search(r"\b(day after tomorrow|parso|parson)\b", t):
        return (today + timedelta(days=2)).isoformat()
    if re.search(r"\b(tomorrow|tmrw|kal)\b", t):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r"\b(today|tonight|aaj)\b", t):
        return today.isoformat()

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)                      # 2026-11-20
    if m:
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d.isoformat() if d else None

    m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", t)        # 20/11, 20-11-2026
    if m and not re.search(r"\d{1,2}[.:]\d{2}\s*(am|pm)", m.group(0) + t[m.end():m.end() + 4]):
        year = int(m.group(3)) if m.group(3) else today.year
        year = year + 2000 if year < 100 else year
        d = _safe_date(year, int(m.group(2)), int(m.group(1)))
        if d:
            return d.isoformat()

    m = (re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b(?:\s+(\d{{4}}))?", t)
         or re.search(rf"\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", t))
    if m:
        if m.group(1).isdigit():
            day, month_word, year = int(m.group(1)), m.group(2), m.group(3)
        else:
            month_word, day, year = m.group(1), int(m.group(2)), m.group(3)
        d = _safe_date(int(year) if year else today.year, MONTHS[month_word[:3]], day)
        return d.isoformat() if d else None

    m = re.search(r"\b(?:(next|this|coming)\s+)?(mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\b", t)
    if m:
        target = WEEKDAYS[m.group(2)[:3]]
        ahead = (target - today.weekday()) % 7
        if m.group(1) == "next" and ahead == 0:
            ahead = 7
        return (today + timedelta(days=ahead)).isoformat()

    m = re.search(r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b", t)           # "the 20th"
    if m:
        d = _safe_date(today.year, today.month, int(m.group(1)))
        if d and d < today:
            nxt = today.replace(day=1) + timedelta(days=32)
            d = _safe_date(nxt.year, nxt.month, int(m.group(1)))
        return d.isoformat() if d else None
    return None


# ── TIME ──────────────────────────────────────────────────────────────────────
def parse_time(text: str) -> Optional[str]:
    t = (text or "").lower().replace("a.m.", "am").replace("p.m.", "pm").replace("a.m", "am").replace("p.m", "pm")

    if re.search(r"\bnoon\b", t):
        return "12:00"
    if re.search(r"\bmidnight\b", t):
        return "00:00"

    m = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)\b", t)
    if m:
        h, mins = int(m.group(1)), int(m.group(2) or 0)
        if 1 <= h <= 12 and mins < 60:
            h = h % 12 + (12 if m.group(3) == "pm" else 0)
            return f"{h:02d}:{mins:02d}"
        return None

    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)                          # 20:00
    if m:
        h, mins = int(m.group(1)), int(m.group(2))
        if h <= 11 and not re.search(r"\b(morning|subah)\b", t) and re.search(
                r"\b(evening|night|dinner|baje|o'?clock)\b", t):     # "7:30 baje" means 7:30 PM
            h += 12
        return f"{h:02d}:{mins:02d}"

    m = re.search(r"\b(?:at|around|by)\s+(\d{1,2})(?:\s*(?:o'?clock|baje))?\b(?!\s*(?:people|persons|guests|th|st|nd|rd|/|-))", t) \
        or re.search(r"\b(\d{1,2})\s*(?:o'?clock|baje)\b", t)
    if m:
        h = int(m.group(1))
        if not 1 <= h <= 12:
            return None
        morning = re.search(r"\b(morning|subah)\b", t)
        if not morning and h < 12:
            h += 12            # a restaurant "at 8" means 8 in the evening
        return f"{h:02d}:00"
    return None


# ── PARTY SIZE ────────────────────────────────────────────────────────────────
def parse_people(text: str) -> Optional[int]:
    t = (text or "").lower()
    m = (re.search(rf"\b{_NUM}\s*(?:people|persons?|guests?|pax|of us|adults|members|log|logon)\b", t)
         or re.search(rf"\btable\s+for\s+{_NUM}\b", t)
         or re.search(rf"\bfor\s+{_NUM}\b(?!\s*(?:am|pm|:|\.\d|o'?clock|baje|st\b|nd\b|rd\b|th\b))", t)
         or re.search(rf"\bparty\s+of\s+{_NUM}\b", t))
    if m:
        n = _num(m.group(1))
        return n if n and n > 0 else None
    return None


# ── PHONE ─────────────────────────────────────────────────────────────────────
def normalize_phone(raw) -> Optional[str]:
    """Indian mobile: 10 digits starting 6-9, with optional +91 / 0 prefix."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def parse_phone(text: str) -> Optional[str]:
    """Find a phone number in free text, ignoring dates and times."""
    t = re.sub(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}:\d{2}", " ", text or "")
    for candidate in re.findall(r"\+?\d[\d\s-]{8,}\d", t):
        phone = normalize_phone(candidate)
        if phone:
            return phone
    return None


# ── NAME ──────────────────────────────────────────────────────────────────────
_NAME_STOP = r"(?=\s*(?:,|\.|!|\?|$|\band\b|\bmy\b|\bnumber\b|\bphone\b|\bfor\b|\bat\b|\bon\b|\bi\b))"
_NOT_NAMES = {"yes", "no", "yeah", "nope", "sure", "okay", "ok", "fine", "book", "table", "booking",
              "cancel", "thanks", "thank", "hello", "hi", "the", "a", "looking", "calling", "here"}


def _clean_name(raw: str) -> Optional[str]:
    name = _FILLERS.sub(" ", raw or "")
    name = re.sub(r"[^\wऀ-ॿ .'-]|[\d_]", " ", name)   # Latin or Devanagari (Ravi, रवि)
    name = " ".join(w for w in name.split() if w.lower() not in ("is", "the", "name"))
    words = name.split()
    if not words or len(words) > 4 or words[0].lower() in _NOT_NAMES:
        return None
    name = " ".join(w.capitalize() if w.islower() else w for w in words)
    return name if 2 <= len(name) <= 60 else None


def parse_name(text: str, asked: bool = False) -> Optional[str]:
    t = _FILLERS.sub(" ", text or "")
    m = re.search(
        r"\b(?:my name is|my name's|name is|name's|under the name(?: of)?|under|"
        r"call me|this is|i am|i'm|mera naam)\s+((?:[^\W\d_]|[ऀ-ॿ])[\wऀ-ॿ .'-]{0,40}?)"
        + _NAME_STOP, t, re.I)
    if m:
        return _clean_name(m.group(1))
    if asked and len(t.split()) <= 4 and not re.search(r"\d", t):
        return _clean_name(t)
    return None


def parse_all(text: str, today: date, asked: Optional[str] = None) -> dict:
    return {
        "date": parse_date(text, today),
        "time": parse_time(text),
        "people": parse_people(text),
        "name": parse_name(text, asked=(asked == "name")),
        "phone": parse_phone(text),
    }
