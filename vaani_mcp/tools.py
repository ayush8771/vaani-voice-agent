"""
vaani_mcp/tools.py — the only way Vaani acts on the world.

Every action (checking tables, holding and confirming a booking, cancelling,
answering from the knowledge base, handing off to staff) is one of these
functions. The rule-based conversation calls them directly; the LLM agent
calls them over MCP. Either way the same checks run here, in code:

  * a booking is only written by book_table(), which needs a signed hold from
    prepare_booking() AND caller_confirmed=True
  * a cancellation is only written by confirm_cancellation(), which needs the
    6-digit code sent to the booked WhatsApp number
  * arguments from the LLM are normalised and validated, never trusted

All functions return plain dicts with a spoken `message` where useful.
Every call is recorded in the tool_calls table (see `run`).
"""

import contextvars
import json
import logging
import re
import time
from typing import Annotated, Optional

from pydantic import BeforeValidator, Field

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import availability as av
import bookings
import phrases
import slot_rules
from config import settings
from database import connection
from security import OtpRateLimited, create_otp, verify_otp
from services import whatsapp


# ── ARGUMENT TYPES ────────────────────────────────────────────────────────────
# The JSON schema the LLM sees asks for strings in fixed formats, but whatever
# arrives (numbers, words, nulls) is converted before validation and then
# normalised by the _norm_* helpers below.
def _to_text(value):
    return None if value is None else str(value)


def _to_party(value):
    return _norm_people(value)


Text = Annotated[Optional[str], BeforeValidator(_to_text)]
DateArg = Annotated[Optional[str], BeforeValidator(_to_text),
                    Field(description="Date as a 'YYYY-MM-DD' string, e.g. '2026-11-20'")]
TimeArg = Annotated[Optional[str], BeforeValidator(_to_text),
                    Field(description="Time as an 'HH:MM' 24-hour string, e.g. '20:00'")]
PartyArg = Annotated[Optional[int], BeforeValidator(_to_party), Field(description="Number of guests")]
PhoneArg = Annotated[Optional[str], BeforeValidator(_to_text),
                     Field(description="10-digit Indian mobile number as a string")]
NameArg = Annotated[Optional[str], BeforeValidator(_to_text), Field(description="Name for the booking")]

logger = logging.getLogger(__name__)

HOLD_MAX_AGE = 15 * 60

# Set per request by the web app so tool calls can be grouped by conversation.
CURRENT_SESSION: contextvars.ContextVar = contextvars.ContextVar("vaani_session", default="")


# ── ARGUMENT NORMALISATION (LLM output is never trusted) ──────────────────────
def _norm_date(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):                       # 20261119
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            av.parse_date(text)
            return text
        except ValueError:
            return None
    return slot_rules.parse_date(text, av.today())


def _norm_time(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if re.fullmatch(r"\d{3,4}", text):                     # 2000 -> 20:00
        text = f"{text[:-2]}:{text[-2:]}"
    elif re.fullmatch(r"\d{1,2}", text):                   # 19 -> 19:00, 8 -> 20:00
        hour = int(text)
        text = f"{hour + 12 if 1 <= hour <= 11 else hour}:00"
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", text)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return slot_rules.parse_time(text)


def _norm_people(value) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(str(value).strip())
    except ValueError:
        n = slot_rules._num(str(value).strip()) or slot_rules.parse_people(str(value))
    return n if n and 1 <= n <= settings.max_party_size else None


def _norm_name(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    name = re.sub(r"[^\wऀ-ॣ०-ॿ\s.'-]", "", value).strip()   # keep Hindi vowel signs (रवि)
    return name if 2 <= len(name) <= 60 else None


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.flask_secret_key, salt=salt)


# ── AVAILABILITY ──────────────────────────────────────────────────────────────
def check_availability(date: DateArg, time: TimeArg, party_size: PartyArg = None) -> dict:
    """Check opening hours and free tables for a date (YYYY-MM-DD), time (HH:MM, 24h) and party size."""
    d, t, p = _norm_date(date), _norm_time(time), _norm_people(party_size)
    if not d or not t:
        return {"available": False, "reason": "invalid",
                "message": "I need a valid date and time to check."}
    result = {"date": d, "time": t, "party_size": p}

    if av.parse_date(d) < av.today():
        return {**result, "available": False, "reason": "past_date", "clear": "date",
                "message": phrases.PAST_DATE_REPLY}

    rule = av.check_slot_rules(d, t, p)
    if not rule.ok:
        if rule.reason == "large_group":
            return {**result, "available": False, "reason": "large_group", "clear": "people",
                    "message": phrases.large_group_reply()}
        return {**result, "available": False, "reason": rule.reason,
                "clear": "date" if rule.reason == "closed" else "time", "message": rule.message}

    if p and p > av.largest_table():
        return {**result, "available": False, "reason": "too_large", "clear": "people",
                "message": phrases.too_large_reply()}

    if p:
        with connection() as con:
            table = av.find_free_table(con, d, t, p)
            if not table:
                alternatives = av.suggest_alternatives(con, d, t, p)
                return {**result, "available": False, "reason": "full", "clear": "time",
                        "alternatives": alternatives,
                        "message": f"Sorry, we are fully booked at {av.speak_time(t)}. "
                                   + phrases.alternatives_phrase(alternatives)}
    return {**result, "available": True, "reason": "",
            "message": f"Yes, we have a table on {av.speak_date(d)} at {av.speak_time(t)}."}


def get_opening_hours(date: DateArg = None) -> dict:
    """Opening hours and last seating for a date (defaults to today)."""
    d = _norm_date(date) or av.today().isoformat()
    hours = av.hours_phrase(d)
    if hours == "closed":
        return {"date": d, "open": False, "message": f"We are closed on {av.speak_date(d)}."}
    last = av.latest_seating_phrase(d)
    return {"date": d, "open": True, "hours": hours, "last_seating": last,
            "message": f"On {av.speak_date(d)} we are open {hours}. The last seating is at {last}."}


# ── BOOKING ───────────────────────────────────────────────────────────────────
def prepare_booking(date: DateArg = None, time: TimeArg = None, party_size: PartyArg = None,
                    name: NameArg = None, phone: PhoneArg = None) -> dict:
    """Validate a booking and return a read-back for the caller. Writes nothing.
    The booking is only made by book_table() after the caller says yes."""
    slots = {"date": _norm_date(date), "time": _norm_time(time), "people": _norm_people(party_size),
             "name": _norm_name(name), "phone": slot_rules.normalize_phone(phone)}
    missing = [k for k in ("date", "time", "people", "name", "phone") if not slots[k]]
    if missing:
        return {"ok": False, "reason": "missing", "missing": missing, "slots": slots,
                "message": phrases.BOOKING_Q[missing[0]]}

    check = check_availability(slots["date"], slots["time"], slots["people"])
    if not check["available"]:
        return {"ok": False, "reason": check["reason"], "clear": check.get("clear"),
                "alternatives": check.get("alternatives", []), "slots": slots,
                "message": check["message"]}

    hold = _serializer("vaani-hold").dumps(slots)
    return {"ok": True, "hold_token": hold, "slots": slots,
            "message": phrases.read_back(**slots)}


def book_table(hold_token: str, caller_confirmed: bool) -> dict:
    """Make the booking from a prepare_booking() hold. Only call after reading the
    booking back and hearing a clear yes from the caller."""
    if caller_confirmed is not True:
        return {"ok": False, "reason": "not_confirmed",
                "message": "Read the booking back to the caller and get a clear yes first."}
    try:
        slots = _serializer("vaani-hold").loads(hold_token, max_age=HOLD_MAX_AGE)
    except SignatureExpired:
        return {"ok": False, "reason": "expired", "message": "That booking hold expired. Let us check again."}
    except BadSignature:
        return {"ok": False, "reason": "invalid_hold", "message": "That booking hold is not valid."}

    result = bookings.book(slots["date"], slots["time"], slots["people"], slots["name"], slots["phone"])
    if not result.ok:
        if result.reason == "full":
            message = (f"Sorry, {av.speak_time(slots['time'])} was just taken. "
                       + phrases.alternatives_phrase(result.alternatives))
        elif result.reason == "large_group":
            message = phrases.large_group_reply()
        else:
            message = result.message or phrases.BOOKING_Q["time"]
        return {"ok": False, "reason": result.reason, "alternatives": result.alternatives,
                "slots": slots, "message": message}

    return {"ok": True, "booking_id": result.booking_id, "table": result.table,
            "whatsapp_sent": result.whatsapp_sent,
            "message": phrases.booking_confirmed(slots["date"], slots["time"], slots["people"],
                                                 slots["name"], result.whatsapp_sent)}


def get_booking_status(phone: PhoneArg = None) -> dict:
    """Upcoming confirmed bookings for a mobile number (dates and times only)."""
    p = slot_rules.normalize_phone(phone)
    if not p:
        return {"ok": False, "message": phrases.CANCEL_Q["phone"]}
    with connection() as con:
        rows = con.execute(
            "SELECT date, time, people FROM bookings WHERE phone=? AND status='confirmed' "
            "AND date >= ? ORDER BY date, time LIMIT 5",
            (p, av.today().isoformat()),
        ).fetchall()
    found = [{"date": d, "time": t, "people": n} for d, t, n in rows]
    if not found:
        return {"ok": True, "bookings": [], "message": "I could not find any upcoming booking for that number."}
    spoken = "; ".join(f"{n} guests on {av.speak_date(d)} at {av.speak_time(t)}" for d, t, n in rows)
    return {"ok": True, "bookings": found, "message": f"I found: {spoken}."}


# ── CANCELLATION ──────────────────────────────────────────────────────────────
def start_cancellation(phone: PhoneArg = None, date: DateArg = None, time: TimeArg = None) -> dict:
    """Find a booking and send a 6-digit code to its WhatsApp number. Cancels nothing."""
    slots = {"phone": slot_rules.normalize_phone(phone), "date": _norm_date(date), "time": _norm_time(time)}
    missing = [k for k in ("phone", "date", "time") if not slots[k]]
    if missing:
        return {"ok": False, "reason": "missing", "missing": missing, "slots": slots,
                "message": phrases.CANCEL_Q[missing[0]]}

    row = bookings.find_active_booking(slots["phone"], slots["date"], slots["time"])
    if not row:
        return {"ok": False, "reason": "not_found",
                "message": "I could not find an active booking matching those details."}
    try:
        code = create_otp(slots["phone"], "cancel")
    except OtpRateLimited:
        return {"ok": False, "reason": "rate_limited",
                "message": "Too many verification codes were requested for this number. "
                           f"Please try again in an hour or call us at {settings.restaurant_phone}."}

    token = _serializer("vaani-cancel").dumps({"b": row[0], "ph": slots["phone"]})
    if whatsapp.send_otp(slots["phone"], code):
        return {"ok": True, "otp_sent": True, "cancel_token": token,
                "message": "To keep your booking safe, I have sent a 6-digit code to your WhatsApp. "
                           "Please tell me the code to confirm the cancellation."}
    if settings.otp_dev_echo:
        return {"ok": True, "otp_sent": False, "cancel_token": token, "dev_code": code,
                "message": f"Demo mode: your verification code is {' '.join(code)}. "
                           "Please say it to confirm the cancellation."}
    return {"ok": False, "reason": "cannot_verify",
            "message": "I cannot verify your number right now, so I cannot cancel by voice. "
                       f"Please call us at {settings.restaurant_phone}."}


def confirm_cancellation(cancel_token: str, code: Text = None) -> dict:
    """Cancel the booking if the 6-digit code is right."""
    try:
        data = _serializer("vaani-cancel").loads(cancel_token, max_age=settings.otp_ttl_minutes * 60)
    except (BadSignature, SignatureExpired):
        return {"status": "expired", "message": "That code has expired. Say cancel to start again."}

    digits = re.sub(r"\D", "", str(code or ""))
    if len(digits) != 6:
        return {"status": "need_code", "message": "Please tell me the 6-digit code from your WhatsApp."}

    result = verify_otp(data["ph"], "cancel", digits)
    if result == "ok":
        if bookings.cancel(data["b"]):
            return {"status": "cancelled", "message": "Your booking has been cancelled successfully."}
        return {"status": "inactive", "message": "That booking is no longer active."}
    if result == "wrong":
        return {"status": "wrong", "message": "That code does not match. Please try again."}
    if result == "locked":
        return {"status": "locked",
                "message": "Too many wrong codes. For your security I have stopped this cancellation. "
                           f"Please call us at {settings.restaurant_phone}."}
    return {"status": "expired", "message": "That code has expired. Say cancel to start again."}


# ── KNOWLEDGE AND HANDOFF ─────────────────────────────────────────────────────
def query_menu_faq(query: Text = None) -> dict:
    """Answer a question about the menu, prices, hours, parking or policies from the restaurant's documents."""
    from brain import _answer_info, _fast_intent  # lazy: brain imports this module
    intent = _fast_intent(str(query or ""))
    query = str(query or "").strip()
    if 0 < len(query.split()) <= 3:   # "parking" -> a full question the knowledge search can match
        query = f"Tell me about {query} at the restaurant."
    answer = _answer_info(intent if intent in ("menu", "hours", "faq") else "other", query)
    return {"answer": answer, "grounded": answer != phrases.unsure_reply(), "message": answer}


def escalate_to_human(reason: Text = None, summary: Text = None) -> dict:
    """Pass the conversation to restaurant staff (complaints, special requests, anything you cannot do)."""
    reason = str(reason or "unspecified")[:200]
    summary = str(summary or "")[:500]
    with connection() as con:
        cur = con.execute(
            "INSERT INTO handoffs(session_id, reason, summary, created_at) VALUES (?, ?, ?, ?)",
            (CURRENT_SESSION.get(), reason, summary, av.now().isoformat(timespec="seconds")),
        )
        ticket = cur.lastrowid
    if settings.owner_whatsapp:
        whatsapp._send(f"whatsapp:{settings.owner_whatsapp}",
                       f"*Vaani handoff #{ticket}*\nReason: {reason}\n{summary}")
    return {"ok": True, "ticket": ticket,
            "message": "I have passed this to our team with a note. "
                       f"For anything urgent, please call us at {settings.restaurant_phone}."}


# ── REGISTRY, LOGGING AND DIRECT CALLS ────────────────────────────────────────
TOOLS = {
    f.__name__: f for f in (
        check_availability, get_opening_hours, prepare_booking, book_table, get_booking_status,
        start_cancellation, confirm_cancellation, query_menu_faq, escalate_to_human,
    )
}

_SECRET_KEYS = {"hold_token", "cancel_token", "code", "dev_code"}


def _mask(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _SECRET_KEYS:
                out[k] = "***"
            elif k == "phone" and v:
                out[k] = whatsapp.mask_phone(str(v))
            else:
                out[k] = _mask(v)
        return out
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def log_call(path: str, tool: str, args: dict, result: dict, latency_ms: int) -> None:
    ok = bool(result.get("ok", result.get("available", result.get(
        "grounded", result.get("status") == "cancelled"))))
    try:
        with connection() as con:
            con.execute(
                "INSERT INTO tool_calls(ts, session_id, path, tool, args, result, ok, latency_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (av.now().isoformat(timespec="seconds"), CURRENT_SESSION.get(), path, tool,
                 json.dumps(_mask(args))[:1000], json.dumps(_mask(result))[:2000], int(ok), latency_ms),
            )
    except Exception as exc:  # logging must never break a call
        logger.error("Could not log tool call %s: %s", tool, exc)


def run(tool: str, path: str = "rules", **args) -> dict:
    """Call a tool directly (used by the rule-based flow) and log it."""
    started = time.time()
    try:
        result = TOOLS[tool](**args)
    except Exception as exc:
        logger.exception("Tool %s failed", tool)
        result = {"ok": False, "reason": "error", "message": phrases.unsure_reply(), "error": str(exc)}
    log_call(path, tool, args, result, int((time.time() - started) * 1000))
    return result
