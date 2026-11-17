"""
brain.py — Vaani conversation logic: a router in front of two paths.

  Caller turn
     |
   Router (rules, instant)
     |-- clear booking / cancel step ----> rule flow ---+
     |                                                  |--> vaani_mcp tools --> DB / WhatsApp
     '-- open-ended or multi-step -------> LLM agent ---+
                                           (MCP tool calls)

Rule flow (instant, no LLM for most turns):
  * Booking: collect details -> check hours and a free table -> read back ->
    book only after the caller says yes. Any detail can be corrected.
  * Cancel: find the booking -> send a WhatsApp code -> cancel only after the
    caller says the right code.
  * A side question during a booking is answered and the booking continues.

Agent (agent.py): questions, multi-intent requests, booking status, handoff to
staff. It acts only through the same tools, over MCP.

State is a plain dict stored in the Flask session:
  flow     "booking" | "cancel"
  stage    "collect" | "confirm" | "otp"
  slots    {date, time, people, name, phone}
  asked    the detail Vaani just asked for
  history  last few turns, for the agent
"""

import json
import logging
import re
from datetime import timedelta
from typing import Optional


import availability as av
import llm_client
import phrases
import slot_rules
from config import settings
from phrases import BOOKING_Q, CANCEL_Q, PAST_DATE_REPLY  # noqa: F401  (re-exported for callers)
from rag_store import retrieve_hits
from vaani_mcp import tools

logger = logging.getLogger(__name__)

BOOKING_SLOTS = ["date", "time", "people", "name", "phone"]
CANCEL_SLOTS = ["phone", "date", "time"]
_unsure_reply = phrases.unsure_reply


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
_BOOK_KW = re.compile(
    r"\b(book|booking|reserve|reservation|table for|seat|i want a table|can i book)\b"
    r"|\b\d+\s*(people|persons|guests|pax|log|logon)\b", re.I)
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
_NEW_BOOKING = re.compile(r"\b(new|another|different|fresh)\s+(booking|reservation|table)\b", re.I)
_EXISTING_BOOKING = re.compile(r"\b(my|existing|old|previous)\s+(booking|reservation|table)\b", re.I)
_RESET_KW = re.compile(r"\b(reset|start over|new request|new query|forget previous|restart)\b", re.I)
_NEEDS_AGENT = re.compile(
    r"\b(complain|complaint|manager|supervisor|owner|speak to|talk to|human|real person|staff|"
    r"refund|allerg\w*|special request|birthday|anniversary|surprise)\b", re.I)
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
def _say(text: str, state: dict):
    """Produce (reply_text, audio, state). Audio is added once, after localisation."""
    return text, None, state


# ── LLM ───────────────────────────────────────────────────────────────────────
LLM_FALLBACK = "Sorry, I did not catch that. Could you please repeat?"


def llm(prompt: str, system: str = None, max_tokens: int = 250,
        on_fail: Optional[str] = LLM_FALLBACK) -> Optional[str]:
    """Single-turn LLM call. On failure returns `on_fail` (None for callers that parse output)."""
    result = llm_client.complete(prompt, system or get_persona(), max_tokens=max_tokens)
    return result if result is not None else on_fail


def warm_up_llm() -> None:
    """Load the model into memory so the first caller does not wait."""
    try:
        llm("Hi", system="Reply with OK.", max_tokens=2, on_fail=None)
        logger.info("LLM warmed up (%s)", settings.llm_model)
    except Exception as exc:
        logger.warning("LLM warm-up failed: %s", exc)


def classify_intent(msg: str) -> str:
    result = (llm(msg, system=INTENT_SYSTEM, max_tokens=5, on_fail="") or "").lower().strip()
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


_normalize_phone = slot_rules.normalize_phone
_phone_from_text = slot_rules.parse_phone


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
        name = re.sub(r"[^\wऀ-ॣ०-ॿ\s.'-]", "", raw_name).strip()
        cleaned["name"] = name if 2 <= len(name) <= 60 else None

    cleaned["phone"] = _normalize_phone(slots.get("phone"))
    return cleaned


def extract_slots(msg: str, asked: Optional[str] = None) -> dict:
    """
    Rules first; the LLM is asked only when the caller mentions a detail the
    rules could not read. If the LLM fails, the rule results are used alone.
    """
    slots = _normalize_slots(slot_rules.parse_all(msg, av.today(), asked))
    unread = [k for k in ("date", "time", "people", "name")
              if slots.get(k) is None and (k == asked or _EVIDENCE[k].search(msg or ""))]
    if not unread:
        return slots

    raw = llm(msg, system=_slot_system(), max_tokens=120, on_fail=None)
    if raw is None:
        logger.warning("Slot LLM unavailable; using rule-based slots only")
        return slots
    from_llm = _normalize_slots(_clean_json(raw))
    for k in unread:
        slots[k] = from_llm.get(k)
    return slots


# A slot value from the LLM is only accepted when the caller's words mention
# that kind of detail, or it answers the question Vaani just asked. This stops
# a small model from inventing "today" when the caller only changed the time.
_EVIDENCE = {
    "date": re.compile(
        r"\b(today|tomorrow|tonight|day after|aaj|kal|parso|next|this|coming|weekend|date|"
        r"mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\w*\b"
        r"|\b\d{1,2}(st|nd|rd|th)\b|\b\d{1,4}[/-]\d{1,2}", re.I),
    "time": re.compile(
        r"\b\d{1,2}(:\d{2})?\s*(am|pm|a\.m\.?|p\.m\.?|baje|o'?clock)|\b\d{1,2}:\d{2}\b|"
        r"\b(noon|midnight|lunch|dinner|morning|afternoon|evening|night|time)\b", re.I),
    "people": re.compile(
        r"\b(people|persons?|guests?|pax|of us|members|log|logon|adults?|kids|children|party)\b"
        r"|\bfor\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I),
    "name": re.compile(r"\b(name|naam|under|call me|i am|i'm|this is|it's)\b", re.I),
}


def apply_slots(existing: dict, new: dict, msg: str = "", asked: Optional[str] = None) -> tuple:
    """Merge new values over old ones so callers can correct details. Returns (slots, changed)."""
    merged = dict(existing)
    changed = []
    for k, v in new.items():
        if v is None or merged.get(k) == v:
            continue
        pattern = _EVIDENCE.get(k)
        if pattern is not None and k != asked and not pattern.search(msg or ""):
            logger.info("Ignored unsupported %s=%r from the LLM", k, v)
            continue
        merged[k] = v
        changed.append(k)
    return merged, changed


def _ask(state: dict, key: str, questions: dict):
    state["asked"] = key
    return _say(questions[key], state)


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
    """Menu, hours, FAQ and general questions. A miss in one section retries across all sections."""
    answer = _answer_in_section(intent, msg)
    if answer == _unsure_reply() and intent in ("menu", "hours"):
        # e.g. "is there a vegetarian menu" is answered in the FAQ section, not the MENU section
        answer = _strict_rag_answer(msg, n=6, max_distance=0.55)
    return answer


def _answer_in_section(intent: str, msg: str) -> str:
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


# ── BOOKING FLOW (rules) ──────────────────────────────────────────────────────
def _start_booking(state: dict) -> None:
    history = state.get("history")
    state.clear()
    state.update({"flow": "booking", "stage": "collect", "slots": {}})
    if history:
        state["history"] = history


def _clear_flow(state: dict) -> None:
    """End the current booking or cancel flow but keep the conversation history."""
    history = state.get("history")
    state.clear()
    if history:
        state["history"] = history


def _booking_turn(msg: str, state: dict):
    state.pop("offer", None)
    slots = state.get("slots", {})

    if state.get("stage") == "confirm":
        plain_yes = (_YES.search(msg) and not _NO.search(msg) and len(msg.split()) <= 4
                     and not re.search(r"\d", msg))
        if plain_yes:
            return _finalise_booking(state)

    # Read any new details first: "no, make it 7 pm" is a correction, not just a "no".
    slots, changed = apply_slots(slots, extract_slots(msg, state.get("asked")), msg, state.get("asked"))
    state["slots"] = slots

    if state.get("stage") == "confirm" and not changed:
        if _YES.search(msg) and not _NO.search(msg):
            return _finalise_booking(state)
        if _NO.search(msg):
            state["stage"] = "collect"
            state.pop("asked", None)
            state.pop("hold_token", None)
            return _say("No problem. What would you like to change?", state)
        return _say("Sorry, I did not catch that. Shall I book it, or would you like to change something?",
                    state)

    state["stage"] = "collect"
    state.pop("asked", None)
    state.pop("hold_token", None)

    # Check hours, party size and free tables as soon as date and time are known,
    # before asking for name and phone.
    if slots.get("date") and slots.get("time"):
        check = tools.run("check_availability", date=slots["date"], time=slots["time"],
                          party_size=slots.get("people"))
        if not check["available"]:
            if check["reason"] == "large_group":
                _clear_flow(state)
                return _say(check["message"], state)
            clear = check.get("clear")
            if clear:
                slots[clear] = None
                state["asked"] = clear
            return _say(check["message"], state)
    elif slots.get("date") and _is_past_date(slots["date"]):
        slots["date"] = None
        state["asked"] = "date"
        return _say(PAST_DATE_REPLY, state)
    elif slots.get("people") and slots["people"] > settings.large_group_threshold:
        _clear_flow(state)
        return _say(phrases.large_group_reply(), state)
    elif slots.get("people") and slots["people"] > av.largest_table():
        slots["people"] = None
        state["asked"] = "people"
        return _say(phrases.too_large_reply(), state)

    missing = first_missing(slots, BOOKING_SLOTS)
    if missing:
        return _ask(state, missing, BOOKING_Q)

    prepared = tools.run("prepare_booking", date=slots["date"], time=slots["time"],
                         party_size=slots["people"], name=slots["name"], phone=slots["phone"])
    if not prepared["ok"]:
        clear = prepared.get("clear") or (prepared.get("missing") or [None])[0]
        if clear:
            slots[clear] = None
            state["asked"] = clear
        return _say(prepared["message"], state)

    state["stage"] = "confirm"
    state["hold_token"] = prepared["hold_token"]
    return _say(prepared["message"], state)


def _finalise_booking(state: dict):
    hold = state.get("hold_token")
    if not hold:
        state["stage"] = "collect"
        return _booking_turn("", state)

    result = tools.run("book_table", hold_token=hold, caller_confirmed=True)
    if result["ok"]:
        _clear_flow(state)
        return _say(result["message"], state)

    state["stage"] = "collect"
    state.pop("hold_token", None)
    if result["reason"] == "large_group":
        _clear_flow(state)
    elif result["reason"] in ("full", "expired", "past", "outside_hours", "closed"):
        state["slots"]["time"] = None
        state["asked"] = "time"
    return _say(result["message"], state)


# ── CANCEL FLOW (rules) ───────────────────────────────────────────────────────
def _start_cancel(state: dict) -> None:
    history = state.get("history")
    state.clear()
    state.update({"flow": "cancel", "stage": "collect", "slots": {}})
    if history:
        state["history"] = history


def _cancel_turn(msg: str, state: dict):
    if state.get("stage") == "otp":
        return _cancel_otp_turn(msg, state)

    slots, _ = apply_slots(state.get("slots", {}), extract_slots(msg, state.get("asked")), msg,
                           state.get("asked"))
    slots = {k: slots.get(k) for k in CANCEL_SLOTS}
    state["slots"] = slots

    missing = first_missing(slots, CANCEL_SLOTS)
    if missing:
        return _ask(state, missing, CANCEL_Q)

    result = tools.run("start_cancellation", phone=slots["phone"], date=slots["date"], time=slots["time"])
    if result["ok"]:
        state.update({"stage": "otp", "cancel_token": result["cancel_token"]})
        state.pop("asked", None)
    else:
        _clear_flow(state)
    return _say(result["message"], state)


def _cancel_otp_turn(msg: str, state: dict):
    code = _otp_from_text(msg)
    if not code:
        return _say("Please tell me the 6-digit code from your WhatsApp.", state)

    result = tools.run("confirm_cancellation", cancel_token=state.get("cancel_token", ""), code=code)
    if result["status"] not in ("wrong", "need_code"):
        _clear_flow(state)
    return _say(result["message"], state)


# ── SIDE QUESTIONS DURING A FLOW ──────────────────────────────────────────────
def _pending_prompt(state: dict) -> str:
    if state.get("stage") == "confirm":
        return "Shall I go ahead with the booking?"
    if state.get("stage") == "otp":
        return "Please tell me the 6-digit code when you are ready."
    keys, questions = (BOOKING_SLOTS, BOOKING_Q) if state.get("flow") == "booking" else (CANCEL_SLOTS, CANCEL_Q)
    missing = first_missing(state.get("slots", {}), keys)
    return f"Now, {questions[missing][0].lower()}{questions[missing][1:]}" if missing else ""


# ── ROUTER ────────────────────────────────────────────────────────────────────
# "Is there a table for 4 tomorrow?", "any tables free at 8", "do you have a table" -
# a question about availability, not yet a booking. Speech has no "?", so no punctuation needed.
_AVAILABILITY_Q = re.compile(
    r"\b(is there|are there|do you have|have you got|got|any)\b.{0,30}\b(table|tables|seat|seats|slot|space)\b"
    r"|\b(table|tables|seat|seats|slot)\b.{0,20}\b(available|free|open|left)\b"
    r"|\bavailab(le|ility)\b.{0,30}\b(table|tables|for \d+|tonight|tomorrow)\b", re.I)
# Questions that are not about booking itself ("party"/"event" excluded: "a party of 6" is a booking).
_INFO_Q = re.compile(
    r"\b(parking|valet|menu|price|prices|cost|veg|vegetarian|vegan|wifi|wi-fi|payment|upi|card|cash|"
    r"alcohol|dress code|wheelchair|pet|pets|delivery|address|location|where are you|hours|timings?|"
    r"open|close|kids|children|allerg\w*)\b", re.I)


def _is_availability_question(msg: str) -> bool:
    return bool(_AVAILABILITY_Q.search(msg))


def _is_multi_intent(msg: str) -> bool:
    """A booking, cancel or availability request that also asks something else."""
    action = _BOOK_KW.search(msg) or _CANCEL_KW.search(msg) or _AVAILABILITY_Q.search(msg)
    return bool(action and _INFO_Q.search(msg))


def _info_clause(msg: str) -> Optional[str]:
    """The part of a mixed request that is a menu/FAQ question, e.g. 'do you have parking'."""
    if not _INFO_Q.search(msg):
        return None
    for clause in re.split(r"\?|[,.;]|\b(?:and|also|plus)\b", msg, flags=re.I):
        if clause and _INFO_Q.search(clause) and not _AVAILABILITY_Q.search(clause):
            return clause.strip()
    return None


def _agent_turn(msg: str, state: dict):
    """Hand the turn to the tool-calling agent; fall back to the knowledge base if the LLM is down."""
    if settings.agent_enabled:
        import agent  # lazy: agent imports this module's tools indirectly
        reply = agent.run(msg, state, info_question=_info_clause(msg))
        if reply:
            return _say(reply, state)
    return _say(_answer_info("other", msg), state)


def _route(msg: str, state: dict):
    flow = state.get("flow")
    if flow in ("booking", "cancel"):
        if _BYE_ONLY.match(msg):
            _clear_flow(state)
            return _say(f"Okay. Thank you for calling {settings.restaurant_name}. Goodbye!", state)

        intent = _fast_intent(msg)
        if flow == "booking" and _NEW_BOOKING.search(msg):
            _start_booking(state)
            return _booking_turn(msg, state)
        if flow == "booking" and intent == "cancel":
            if not _EXISTING_BOOKING.search(msg):
                _clear_flow(state)
                return _say("Okay, I have stopped this booking. How else can I help?", state)
            _start_cancel(state)
            return _cancel_turn(msg, state)
        if flow == "cancel" and intent == "book" and not _CANCEL_KW.search(msg):
            _start_booking(state)
            return _booking_turn(msg, state)
        if intent in ("menu", "hours", "faq") and _QUESTION.search(msg):
            answer = tools.run("query_menu_faq", query=msg)["answer"]
            return _say(f"{answer} {_pending_prompt(state)}".strip(), state)

        return _booking_turn(msg, state) if flow == "booking" else _cancel_turn(msg, state)

    if _is_multi_intent(msg) or (_is_availability_question(msg) and settings.agent_enabled):
        return _agent_turn(msg, state)   # agent checks tables (and answers the other part)

    intent = _fast_intent(msg)
    if _EXISTING_BOOKING.search(msg) and intent != "cancel" and not _NEW_BOOKING.search(msg):
        return _agent_turn(msg, state)   # "is my booking still on?", "change my reservation"
    if _NEEDS_AGENT.search(msg) and intent not in ("book", "cancel"):
        return _agent_turn(msg, state)   # complaints, managers, special requests

    if intent == "goodbye" and not _BYE_ONLY.match(msg) and len(msg.split()) > 4:
        intent = None  # "thanks, and ..." carries a real request

    if intent == "book":
        _start_booking(state)
        return _booking_turn(msg, state)
    if intent == "cancel":
        _start_cancel(state)
        return _cancel_turn(msg, state)
    if intent in ("menu", "hours", "faq") and (_QUESTION.search(msg) or len(msg.split()) <= 6):
        return _say(tools.run("query_menu_faq", query=msg)["answer"], state)
    if intent == "greeting":
        greeting = llm(f"Customer said: {msg}\nGreet warmly as VAANI. One sentence.", max_tokens=60,
                       on_fail=None)
        return _say(greeting or "Hello! How can I help you today?", state)
    if intent == "goodbye":
        _clear_flow(state)
        return _say(f"Thank you for calling {settings.restaurant_name}. Have a wonderful day! Goodbye!",
                    state)

    return _agent_turn(msg, state)


# ── MAIN ENTRY POINT ──────────────────────────────────────────────────────────
WELCOME = ("Namaste, and welcome to {restaurant}! I am VAANI. You can talk to me in English or Hindi. "
           "I can book a table, cancel a reservation, or answer questions about our menu. How can I help?")


def get_ai_response(message: str, state: dict, stt_language: Optional[str] = None, speak: bool = True):
    """
    One caller turn. Returns (reply_text, audio_b64_or_None, new_state).

    stt_language: the language Prisma detected (e.g. "hi-IN"), if the turn was spoken.
    The reply comes back in the caller's language (state["lang"]).
    """
    import i18n
    from services import speech

    msg = (message or "").strip()

    if msg == "__WELCOME__":
        reply = WELCOME.format(restaurant=settings.restaurant_name)
        audio = speech.synthesize(reply, "en") if speak else None
        return reply, audio.audio_b64 if audio else None, state

    lang = i18n.update_language(state, msg, stt_language)
    english = i18n.to_english(msg, lang, asked=state.get("asked"))

    if _RESET_KW.search(english):
        history_lang = state.get("lang")
        state.clear()
        state["lang"] = history_lang
        reply_en = "Okay, I have cleared the current request. How can I help now?"
    else:
        reply_en, _, state = _route(english, state)
        try:
            import agent
            agent.remember(state, english, reply_en)
        except Exception:  # history is a convenience; never fail a turn over it
            pass

    state["lang"] = lang                       # flows clear state; keep the language
    reply = i18n.localise(reply_en, lang)
    audio = speech.synthesize(reply, lang) if speak else None
    return reply, audio.audio_b64 if audio else None, state
