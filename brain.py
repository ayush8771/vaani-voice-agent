"""
brain.py — Vaani conversation logic.

Phase 1 behaviour:
  * Booking: collect details -> check hours and a free table -> read back ->
    book only after the caller says yes. Any detail can be corrected.
  * Cancel: find the booking -> send a WhatsApp code -> cancel only after the
    caller says the right code.
  * A side question (menu, hours, FAQ) during a booking is answered and the
    booking continues; small talk such as "thanks" no longer wipes it.

State is a plain dict stored in the Flask session:
  flow   "booking" | "cancel"
  stage  "collect" | "confirm" | "otp"
  slots  {date, time, people, name, phone}
"""

import asyncio
import base64
import json
import logging
import re
from datetime import timedelta
from typing import Optional

import requests
from edge_tts import Communicate

import availability as av
import bookings
from config import settings
from database import connection
from rag_store import retrieve_hits
from security import OtpRateLimited, create_otp, verify_otp
from services import whatsapp

logger = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────────────────────
LLM_OPTIONS = {"temperature": 0, "num_predict": 80, "repeat_penalty": 1.0, "top_k": 1}
_LLM_TIMEOUT_SECS = 15
_LLM_RETRIES = 2


def _unsure_reply() -> str:
    return ("I want to be accurate, and I am not fully sure from our records. "
            f"Please call us at {settings.restaurant_phone}.")


def _large_group_reply() -> str:
    return (f"For groups of more than {settings.large_group_threshold} guests, we use our private "
            "dining room with at least 7 days advance notice. "
            f"Please call us at {settings.restaurant_phone} or email {settings.private_dining_email}.")


PAST_DATE_REPLY = "That date has already passed. Could you choose a future date for the booking?"

BOOKING_Q = {
    "date":   "What date would you like to book the table for?",
    "time":   "What time works best for you?",
    "people": "How many guests will be joining?",
    "name":   "What name should I put the reservation under?",
    "phone":  "Could I get your 10-digit mobile number for the booking?",
}
CANCEL_Q = {
    "phone": "What is the 10-digit mobile number registered for the booking?",
    "date":  "On which date was the reservation?",
    "time":  "At what time was the reservation?",
}
BOOKING_SLOTS = ["date", "time", "people", "name", "phone"]
CANCEL_SLOTS = ["phone", "date", "time"]


# ── DATE HELPERS ──────────────────────────────────────────────────────────────
def _today() -> str:
    return av.today().isoformat()


def _tomorrow() -> str:
    return (av.today() + timedelta(days=1)).isoformat()


def _is_past_date(date_str: str) -> bool:
    try:
        return av.parse_date(date_str) < av.today()
    except (ValueError, TypeError):
        return False


# ── PERSONAS ──────────────────────────────────────────────────────────────────
def get_persona() -> str:
    return (
        f"You are VAANI, the warm and concise voice assistant for {settings.restaurant_name}. "
        "Replies are read aloud - never use bullet points, markdown, or special characters. "
        f"Keep every reply under 35 words. Today is {_today()}."
    )


def _rag_persona() -> str:
    return (
        f"You are VAANI, the voice assistant for {settings.restaurant_name}. "
        "Answer ONLY from the provided context. Do not infer or assume any missing fact. "
        "If the context does not contain the answer, reply with the provided fallback sentence exactly. "
        "Never use bullet points or markdown. Keep the reply under 35 words."
    )


INTENT_SYSTEM = (
    "Classify the user message into exactly one word. "
    "Reply with ONLY that word, no punctuation, no explanation. "
    "Words: book  cancel  faq  menu  hours  greeting  goodbye  other"
)


# ── KEYWORD PATTERNS ──────────────────────────────────────────────────────────
_CANCEL_KW = re.compile(r"\b(cancel|cancellation|delete booking|remove booking|cancel my)\b", re.I)
_BOOK_KW = re.compile(r"\b(book|reserve|reservation|table for|seat|i want a table|can i book)\b", re.I)
_MENU_KW = re.compile(
    r"\b(menu|food|dish|dishes|eat|starter|main course|dessert|drink|price|cost|"
    r"how much|what do you serve|biryani|paneer|chicken|mutton|prawn|fish|"
    r"naan|roti|lassi|chai|coffee|what.*available)\b", re.I)
_HOURS_KW = re.compile(r"\b(hour|hours|open|close|timing|timings|when do you|what time)\b", re.I)
_FAQ_KW = re.compile(
    r"\b(parking|address|location|where are you|payment|cash|upi|card|"
    r"wifi|wi-fi|dress code|valet|alcohol|pet|pets|wheelchair|kids|children|"
    r"private dining|event|party|delivery|zomato|swiggy)\b", re.I)
_GREET_KW = re.compile(r"^\s*(hi|hello|hey|good morning|good afternoon|good evening|namaste)\b", re.I)
_BYE_KW = re.compile(r"\b(bye|goodbye|thank you|thanks|see you|thats all|that's all|done|exit)\b", re.I)
# Inside a booking, only a message that is nothing but a goodbye ends it.
_BYE_ONLY = re.compile(
    r"^\s*(ok(ay)?[\s,]*)?(bye|goodbye|bye bye|that'?s all|nothing else|exit|never ?mind|"
    r"forget it)[\s.!]*$", re.I)
_EXISTING_BOOKING = re.compile(r"\b(my|existing|old|previous)\s+(booking|reservation|table)\b", re.I)
_RESET_KW = re.compile(r"\b(reset|start over|new request|new query|forget previous|restart)\b", re.I)
_QUESTION = re.compile(r"\?\s*$|^\s*(what|when|where|which|how|do you|does|is there|are there|can i get)\b", re.I)
_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|correct|right|confirm|go ahead|book it|"
                  r"haan|ha|ji|theek hai|thik hai|ok|okay)\b", re.I)
_NO = re.compile(r"\b(no|nope|nah|wrong|not right|change|nahi|mat)\b", re.I)

_HOURS_SIGNAL_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|wed|thu|fri|sat|sun|am|pm|lunch|dinner|brunch|open|close|"
    r"\d{1,2}:\d{2})\b", re.I)
_MENU_SIGNAL_RE = re.compile(
    r"\b(menu|starter|main|dessert|dish|veg|non-veg|biryani|curry|thali|"
    r"rice|bread|beverage|mocktail|price|rs\.?|rupees|₹)\b", re.I)


def _fast_intent(msg: str) -> Optional[str]:
    if _CANCEL_KW.search(msg): return "cancel"
    if _BOOK_KW.search(msg):   return "book"
    if _HOURS_KW.search(msg):  return "hours"
    if _MENU_KW.search(msg):   return "menu"
    if _FAQ_KW.search(msg):    return "faq"
    if _GREET_KW.search(msg):  return "greeting"
    if _BYE_KW.search(msg):    return "goodbye"
    return None


# ── TTS ───────────────────────────────────────────────────────────────────────
async def _speak_async(text: str) -> Optional[str]:
    try:
        c = Communicate(text=text, voice="en-IN-PrabhatNeural", rate="+5%")
        buf = bytearray()
        async for chunk in c.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        if not buf:
            logger.warning("TTS returned empty audio for text: %.60s", text)
            return None
        return base64.b64encode(bytes(buf)).decode()
    except Exception as exc:
        logger.error("TTS error: %s", exc)
        return None


def speak(text: str) -> Optional[str]:
    if not settings.tts_enabled:
        return None
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_speak_async(text))
    except Exception as exc:
        logger.error("TTS speak() error: %s", exc)
        return None
    finally:
        loop.close()


def _say(text: str, state: dict):
    """Produce (reply_text, audio_b64_or_None, state)."""
    return text, speak(text), state


# ── LLM ───────────────────────────────────────────────────────────────────────
def llm(prompt: str, system: str = None, max_tokens: int = 250) -> str:
    opts = dict(LLM_OPTIONS, num_predict=max_tokens)
    last_err = "Unknown error"
    for attempt in range(1, _LLM_RETRIES + 1):
        try:
            r = requests.post(
                settings.ollama_url,
                json={"model": settings.ollama_model, "system": system or get_persona(),
                      "prompt": prompt, "stream": False, "options": opts},
                timeout=_LLM_TIMEOUT_SECS,
            )
            r.raise_for_status()
            return (r.json().get("response") or "").strip()
        except requests.Timeout:
            last_err = f"Timeout after {_LLM_TIMEOUT_SECS}s"
            logger.warning("LLM timeout (attempt %d/%d)", attempt, _LLM_RETRIES)
        except requests.HTTPError as exc:
            last_err = str(exc)
            break
        except Exception as exc:
            last_err = str(exc)
            logger.error("LLM error (attempt %d/%d): %s", attempt, _LLM_RETRIES, exc)
    logger.error("LLM failed after %d attempts: %s", _LLM_RETRIES, last_err)
    return "Sorry, I did not catch that. Could you please repeat?"


def classify_intent(msg: str) -> str:
    result = llm(msg, system=INTENT_SYSTEM, max_tokens=5).lower().strip()
    for intent in ("book", "cancel", "faq", "menu", "hours", "greeting", "goodbye"):
        if intent in result:
            return intent
    return "other"


# ── SLOT EXTRACTION ───────────────────────────────────────────────────────────
def _slot_system() -> str:
    return (
        "Extract booking details and return ONLY a JSON object.\n"
        f"Today: {_today()} ({av.today().strftime('%A')}). Tomorrow: {_tomorrow()}.\n"
        "Resolve relative dates (e.g. 'tomorrow', 'next Saturday'). "
        "Convert times to 24-hour HH:MM (e.g. '8 pm' -> '20:00'). "
        "Strip spaces/dots from phone numbers.\n"
        'Return ONLY this JSON, no markdown, no explanation:\n'
        '{ "date":"YYYY-MM-DD or null", "time":"HH:MM 24h or null", '
        '"people":number_or_null, "name":"string or null", "phone":"10digits or null" }\n'
        "If not mentioned, use null. Never guess."
    )


def _clean_json(raw: str) -> dict:
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    match = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group())
        return {k: (None if str(v).lower() in ("null", "none", "") else v) for k, v in data.items()}
    except json.JSONDecodeError:
        return {}


def _normalize_phone(raw) -> Optional[str]:
    """Indian mobile: 10 digits starting 6-9, with optional +91 / 0 prefix."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def _phone_from_text(msg: str) -> Optional[str]:
    """Find a phone number in free text, ignoring dates and times."""
    text = re.sub(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}:\d{2}", " ", msg or "")
    for candidate in re.findall(r"\+?\d[\d\s-]{8,}\d", text):
        phone = _normalize_phone(candidate)
        if phone:
            return phone
    return None


def _normalize_slots(slots: dict) -> dict:
    cleaned = {}

    raw_date = slots.get("date")
    cleaned["date"] = None
    if isinstance(raw_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date.strip()):
        try:
            av.parse_date(raw_date.strip())
            cleaned["date"] = raw_date.strip()
        except ValueError:
            pass

    raw_time = slots.get("time")
    cleaned["time"] = None
    if isinstance(raw_time, str) and re.fullmatch(r"\d{1,2}:\d{2}", raw_time.strip()):
        h, m = (int(x) for x in raw_time.strip().split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            cleaned["time"] = f"{h:02d}:{m:02d}"

    try:
        people = int(slots.get("people")) if slots.get("people") is not None else None
        cleaned["people"] = people if people and 1 <= people <= settings.max_party_size else None
    except (TypeError, ValueError):
        cleaned["people"] = None

    raw_name = slots.get("name")
    cleaned["name"] = None
    if isinstance(raw_name, str):
        name = re.sub(r"[^\w\s.'-]", "", raw_name).strip()
        cleaned["name"] = name if 2 <= len(name) <= 60 else None

    cleaned["phone"] = _normalize_phone(slots.get("phone"))
    return cleaned


def extract_slots(msg: str) -> dict:
    raw = llm(msg, system=_slot_system(), max_tokens=120)
    slots = _normalize_slots(_clean_json(raw))
    if not slots.get("phone"):
        slots["phone"] = _phone_from_text(msg)
    return slots


def apply_slots(existing: dict, new: dict) -> tuple:
    """Merge new values over old ones so callers can correct details. Returns (slots, changed)."""
    merged = dict(existing)
    changed = []
    for k, v in new.items():
        if v is not None and merged.get(k) != v:
            merged[k] = v
            changed.append(k)
    return merged, changed


def first_missing(slots: dict, keys: list) -> Optional[str]:
    for k in keys:
        if not slots.get(k):
            return k
    return None


def _otp_from_text(msg: str) -> Optional[str]:
    digits = re.sub(r"\D", "", msg or "")
    return digits if len(digits) == 6 else None


# ── RAG ───────────────────────────────────────────────────────────────────────
def _numbers(text: str):
    return set(re.findall(r"\b\d[\d:./-]*\b", text or ""))


def _content_tokens(text: str):
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "have", "has",
        "are", "was", "were", "our", "your", "you", "can", "will", "about",
        "into", "under", "over", "their", "they", "them", "not", "only", "but",
        "what", "when", "where", "which", "would", "could", "should", "please",
    }
    return [t for t in re.findall(r"[a-zA-Z]{3,}", (text or "").lower()) if t not in stop]


def _canonical_retrieval_query(msg: str, intent: str = None) -> str:
    text = (msg or "").strip()
    if len(text.split()) > 2:
        return text
    if intent == "hours":
        return "What are your opening hours and timings for lunch and dinner?"
    if intent == "menu":
        return "Please share menu highlights with categories and prices."
    if intent == "faq":
        return "What are your restaurant policies and frequently asked questions?"
    return text


def _limit_words(text: str, max_words: int = 35) -> str:
    normalized = re.sub(r"\s*\|\s*", ", ", (text or "").strip())
    words = normalized.split()
    if len(words) <= max_words:
        return normalized
    shortened = " ".join(words[:max_words])
    cut = max(shortened.rfind("."), shortened.rfind("!"), shortened.rfind("?"), shortened.rfind(";"))
    if cut >= int(len(shortened) * 0.55):
        return shortened[: cut + 1].strip()
    return shortened.rstrip(" ,;:") + "."


def _verify_grounded_answer(context: str, answer: str) -> bool:
    if not context or not answer:
        return False
    if not _numbers(answer).issubset(_numbers(context)):
        return False
    answer_tokens = _content_tokens(answer)
    if not answer_tokens:
        return False
    context_tokens = set(_content_tokens(context))
    overlap = sum(1 for t in answer_tokens if t in context_tokens) / len(answer_tokens)
    return overlap >= 0.65


def _strict_rag_answer(msg: str, *, section_filter: str = None, n: int = 4,
                       max_distance: float = 0.5, intent_hint: str = None, signal_re=None,
                       max_words: int = 35, llm_max_tokens: int = 120,
                       extra_instruction: str = "") -> str:
    retrieval_query = _canonical_retrieval_query(msg, intent=intent_hint)
    generation_query = retrieval_query if len((msg or "").split()) <= 2 else msg

    hits = retrieve_hits(query=retrieval_query, n=n, section_filter=section_filter,
                         max_distance=max_distance)
    if signal_re is not None:
        hits = [h for h in hits if signal_re.search(h.get("text", ""))]
    if not hits:
        return _unsure_reply()

    context = "\n\n".join(h["text"] for h in hits)
    extra_line = f"{extra_instruction.strip()}\n" if extra_instruction else ""
    unsure = _unsure_reply()
    candidate = llm(
        f"Context:\n{context}\n\nCustomer: {generation_query}\n"
        f"If context is insufficient, reply exactly: {unsure}\n{extra_line}"
        "Use only facts present in context. Answer in one or two short sentences.",
        system=_rag_persona(), max_tokens=llm_max_tokens,
    ).strip()

    if not candidate or candidate == unsure or not _verify_grounded_answer(context, candidate):
        return unsure
    return _limit_words(candidate, max_words=max_words)


def _extract_day_window(text: str, day_expr: str):
    pattern = (
        rf"{day_expr}\s*"
        rf"(\d{{1,2}}:\d{{2}}\s*[AP]M)\s*to\s*(\d{{1,2}}:\d{{2}}\s*[AP]M)"
        rf".*?\|\s*(\d{{1,2}}:\d{{2}}\s*[AP]M)\s*to\s*(\d{{1,2}}:\d{{2}}\s*[AP]M)"
    )
    m = re.search(pattern, text, re.I)
    return (m.group(1), m.group(2), m.group(3), m.group(4)) if m else None


def _deterministic_hours_answer(msg: str) -> Optional[str]:
    hits = retrieve_hits(query=_canonical_retrieval_query(msg, intent="hours"), n=12,
                         section_filter="RESTAURANT INFORMATION", max_distance=0.72)
    texts = [h.get("text", "") for h in hits if _HOURS_SIGNAL_RE.search(h.get("text", ""))]
    if not texts:
        return None

    merged = " ".join(" ".join(t.split()) for t in texts)
    parts = []
    for label, expr in (("Mon-Thu", r"Monday\s*[–-]\s*Thursday"), ("Fri", r"Friday"),
                        ("Sat", r"Saturday"), ("Sun", r"Sunday")):
        w = _extract_day_window(merged, expr)
        if w:
            parts.append(f"{label} {w[0]}-{w[1]} and {w[2]}-{w[3]}")
    if not parts:
        return None

    holidays = " Public holidays follow Sunday timings." if re.search(
        r"Public\s+Holidays\s+Same\s+as\s+Sunday", merged, re.I) else ""
    return _limit_words(f"Our opening hours are: {'; '.join(parts)}.{holidays}", max_words=60)


def _answer_info(intent: str, msg: str) -> str:
    """Menu, hours, FAQ and general questions."""
    if intent == "menu":
        return _strict_rag_answer(msg, section_filter="MENU", n=10, max_distance=0.52,
                                  intent_hint="menu", signal_re=_MENU_SIGNAL_RE)
    if intent == "hours":
        return _deterministic_hours_answer(msg) or _strict_rag_answer(
            msg, section_filter="RESTAURANT INFORMATION", n=10, max_distance=0.62,
            intent_hint="hours", signal_re=_HOURS_SIGNAL_RE, max_words=55, llm_max_tokens=180,
            extra_instruction=("If day-wise timings are present, include Monday through Sunday "
                               "coverage in compact form. Do not include unrelated policies."))
    if intent == "faq":
        return _strict_rag_answer(msg, n=6, max_distance=0.52, intent_hint="faq")
    return _strict_rag_answer(msg, n=4, max_distance=0.47)


# ── BOOKING FLOW ──────────────────────────────────────────────────────────────
def _read_back(slots: dict) -> str:
    return (
        f"Just to confirm: a table for {slots['people']} on {av.speak_date(slots['date'])} "
        f"at {av.speak_time(slots['time'])}, under the name {slots['name']}, "
        f"mobile ending {' '.join(slots['phone'][-4:])}. Shall I book it?"
    )


def _alternatives_phrase(times: list) -> str:
    if not times:
        return "We have no free tables that day. Would you like to try another date?"
    spoken = [av.speak_time(t) for t in times]
    joined = spoken[0] if len(spoken) == 1 else ", ".join(spoken[:-1]) + " or " + spoken[-1]
    return f"I can offer {joined}. Which would you prefer?"


def _start_booking(state: dict) -> None:
    state.clear()
    state.update({"flow": "booking", "stage": "collect", "slots": {}})


def _booking_turn(msg: str, state: dict):
    slots = state.get("slots", {})

    if state.get("stage") == "confirm":
        short = len(msg.split()) <= 4
        if _YES.search(msg) and not _NO.search(msg) and short:
            return _finalise_booking(state)
        if _NO.search(msg) and short:
            state["stage"] = "collect"
            return _say("No problem. What would you like to change?", state)

    slots, changed = apply_slots(slots, extract_slots(msg))
    state["slots"] = slots

    if state.get("stage") == "confirm" and not changed:
        if _YES.search(msg) and not _NO.search(msg):
            return _finalise_booking(state)
        return _say("Sorry, I did not catch that. Shall I book it, or would you like to change something?",
                    state)

    state["stage"] = "collect"

    if slots.get("date") and _is_past_date(slots["date"]):
        slots["date"] = None
        return _say(PAST_DATE_REPLY, state)

    if slots.get("people") and slots["people"] > settings.large_group_threshold:
        state.clear()
        return _say(_large_group_reply(), state)

    if slots.get("people") and slots["people"] > av.largest_table():
        slots["people"] = None
        return _say(f"Our largest table seats {av.largest_table()}. "
                    f"For bigger groups please call us at {settings.restaurant_phone}.", state)

    missing = first_missing(slots, BOOKING_SLOTS)
    if missing:
        return _say(BOOKING_Q[missing], state)

    rule = av.check_slot_rules(slots["date"], slots["time"], slots["people"])
    if not rule.ok:
        if rule.reason == "closed":
            slots["date"] = None
        else:
            slots["time"] = None
        return _say(rule.message, state)

    with connection() as con:
        free = av.find_free_table(con, slots["date"], slots["time"], slots["people"])
        alternatives = [] if free else av.suggest_alternatives(
            con, slots["date"], slots["time"], slots["people"])
    if not free:
        taken = av.speak_time(slots["time"])
        slots["time"] = None
        return _say(f"Sorry, we are fully booked at {taken}. " + _alternatives_phrase(alternatives),
                    state)

    state["stage"] = "confirm"
    return _say(_read_back(slots), state)


def _finalise_booking(state: dict):
    slots = state["slots"]
    result = bookings.book(slots["date"], slots["time"], slots["people"],
                           slots["name"], slots["phone"])
    if not result.ok:
        state["stage"] = "collect"
        if result.reason == "full":
            taken = av.speak_time(slots["time"])
            slots["time"] = None
            return _say(f"Sorry, {taken} was just taken. " + _alternatives_phrase(result.alternatives),
                        state)
        if result.reason == "large_group":
            state.clear()
            return _say(_large_group_reply(), state)
        slots["time"] = None
        return _say(result.message or BOOKING_Q["time"], state)

    reply = (f"Your table for {slots['people']} on {av.speak_date(slots['date'])} at "
             f"{av.speak_time(slots['time'])} is confirmed under {slots['name']}.")
    if result.whatsapp_sent:
        reply += " I have sent the confirmation and QR code to your WhatsApp."
    else:
        reply += (" I could not send the WhatsApp confirmation right now, but your booking is saved. "
                  "Just give your name at the entrance.")
    state.clear()
    return _say(reply, state)


# ── CANCEL FLOW ───────────────────────────────────────────────────────────────
def _start_cancel(state: dict) -> None:
    state.clear()
    state.update({"flow": "cancel", "stage": "collect", "slots": {}})


def _cancel_turn(msg: str, state: dict):
    if state.get("stage") == "otp":
        return _cancel_otp_turn(msg, state)

    slots, _ = apply_slots(state.get("slots", {}), extract_slots(msg))
    slots = {k: slots.get(k) for k in CANCEL_SLOTS}
    state["slots"] = slots

    missing = first_missing(slots, CANCEL_SLOTS)
    if missing:
        return _say(CANCEL_Q[missing], state)

    row = bookings.find_active_booking(slots["phone"], slots["date"], slots["time"])
    if not row:
        state.clear()
        return _say("I could not find an active booking matching those details.", state)

    try:
        code = create_otp(slots["phone"], "cancel")
    except OtpRateLimited:
        state.clear()
        return _say("Too many verification codes were requested for this number. "
                    f"Please try again in an hour or call us at {settings.restaurant_phone}.", state)

    state.update({"stage": "otp", "booking_id": row[0]})
    if whatsapp.send_otp(slots["phone"], code):
        return _say("To keep your booking safe, I have sent a 6-digit code to your WhatsApp. "
                    "Please tell me the code to confirm the cancellation.", state)
    if settings.otp_dev_echo:
        return _say(f"Demo mode: your verification code is {' '.join(code)}. "
                    "Please say it to confirm the cancellation.", state)
    state.clear()
    return _say("I cannot verify your number right now, so I cannot cancel by voice. "
                f"Please call us at {settings.restaurant_phone}.", state)


def _cancel_otp_turn(msg: str, state: dict):
    code = _otp_from_text(msg)
    if not code:
        return _say("Please tell me the 6-digit code from your WhatsApp.", state)

    result = verify_otp(state["slots"]["phone"], "cancel", code)
    if result == "ok":
        booking_id = state["booking_id"]
        state.clear()
        if bookings.cancel(booking_id):
            return _say("Your booking has been cancelled successfully.", state)
        return _say("That booking is no longer active.", state)
    if result == "wrong":
        return _say("That code does not match. Please try again.", state)
    state.clear()
    if result == "locked":
        return _say("Too many wrong codes. For your security I have stopped this cancellation. "
                    f"Please call us at {settings.restaurant_phone}.", state)
    return _say("That code has expired. Say cancel to start again.", state)


# ── SIDE QUESTIONS DURING A FLOW ──────────────────────────────────────────────
def _pending_prompt(state: dict) -> str:
    if state.get("stage") == "confirm":
        return "Shall I go ahead with the booking?"
    if state.get("stage") == "otp":
        return "Please tell me the 6-digit code when you are ready."
    keys, questions = (BOOKING_SLOTS, BOOKING_Q) if state.get("flow") == "booking" else (CANCEL_SLOTS, CANCEL_Q)
    missing = first_missing(state.get("slots", {}), keys)
    return f"Now, {questions[missing][0].lower()}{questions[missing][1:]}" if missing else ""


# ── MAIN ENTRY POINT ──────────────────────────────────────────────────────────
def get_ai_response(message: str, state: dict):
    """Returns (reply_text, audio_b64_or_None, new_state)."""
    msg = (message or "").strip()

    if msg == "__WELCOME__":
        return _say(
            f"Hello and welcome to {settings.restaurant_name}! I am VAANI. "
            "I can help you book a table, cancel a reservation, "
            "or answer questions about our menu. How can I help?",
            state,
        )

    if _RESET_KW.search(msg):
        state.clear()
        return _say("Okay, I have cleared the current request. How can I help now?", state)

    flow = state.get("flow")
    if flow in ("booking", "cancel"):
        if _BYE_ONLY.match(msg):
            state.clear()
            return _say(f"Okay. Thank you for calling {settings.restaurant_name}. Goodbye!", state)

        intent = _fast_intent(msg)
        if flow == "booking" and intent == "cancel":
            if not _EXISTING_BOOKING.search(msg):
                state.clear()
                return _say("Okay, I have stopped this booking. How else can I help?", state)
            _start_cancel(state)
            return _cancel_turn(msg, state)
        if flow == "cancel" and intent == "book" and not _CANCEL_KW.search(msg):
            _start_booking(state)
            return _booking_turn(msg, state)
        if intent in ("menu", "hours", "faq") and _QUESTION.search(msg):
            answer = _answer_info(intent, msg)
            return _say(f"{answer} {_pending_prompt(state)}".strip(), state)

        return _booking_turn(msg, state) if flow == "booking" else _cancel_turn(msg, state)

    intent = _fast_intent(msg) or classify_intent(msg)
    logger.info("[VAANI] intent=%r", intent)

    if intent == "book":
        _start_booking(state)
        return _booking_turn(msg, state)

    if intent == "cancel":
        _start_cancel(state)
        return _cancel_turn(msg, state)

    if intent == "greeting":
        return _say(llm(f"Customer said: {msg}\nGreet warmly as VAANI. One sentence.", max_tokens=60),
                    state)

    if intent == "goodbye":
        state.clear()
        return _say(f"Thank you for calling {settings.restaurant_name}. Have a wonderful day! Goodbye!",
                    state)

    return _say(_answer_info(intent, msg), state)
