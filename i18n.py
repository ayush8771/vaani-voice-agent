"""
i18n.py — Vaani speaks English, Hindi and Hinglish with English as the pivot.

  caller (any of en / hi / hinglish)
     |  1. detect the language (sticky: short replies never switch it)
     |  2. normalise to English: dictionary for common words, LLM only if needed
     v
  English pipeline (rules, agent, MCP tools) -- unchanged
     |  3. localise the reply: hand-written Hindi/Hinglish for every fixed
     |     sentence, LLM translation (cached) only for free-form sentences
     v
  reply in the caller's language -> Timbre speaks it

Languages: "en", "hi" (Devanagari), "hinglish" (Hindi in Latin script).
"""

import logging
import re
from functools import lru_cache
from typing import Optional

import i18n_kn as kn
from config import settings

logger = logging.getLogger(__name__)

LANGUAGES = ("en", "hi", "hinglish", "kn")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LETTER = re.compile(r"[^\W\d_]")

# Romanised Hindi words that signal Hinglish
_HINGLISH_MARKERS = re.compile(
    r"\b(hai|hain|kya|kaise|kitna|kitne|kitni|chahiye|karna|karni|karo|karein|kijiye|mujhe|hum|humein|"
    r"aap|apka|aapka|mera|meri|mere|hamara|kal|parso|baje|log|logon|logo|haan|nahi|nahin|theek|"
    r"bilkul|wala|wali|liye|ke|ki|ka|ko|mein|se|tak|abhi|naam|shaam|raat|subah|dopahar|bhai|ji)\b", re.I)


# ── 1. LANGUAGE DETECTION (sticky) ────────────────────────────────────────────
def detect(text: str, stt_code: Optional[str] = None) -> str:
    """Best guess for one utterance: en, hi, hinglish, kn or other (another Indian language)."""
    letters = _LETTER.findall(text or "")
    if letters and sum(1 for ch in letters if DEVANAGARI.match(ch)) / len(letters) > 0.3:
        return "hi"
    if letters and sum(1 for ch in letters if kn.KANNADA.match(ch)) / len(letters) > 0.3:
        return "kn"
    if len(_HINGLISH_MARKERS.findall(text or "")) >= 2:
        return "hinglish"
    if stt_code and stt_code not in ("en-IN", "hi-IN", "kn-IN"):
        return "other"
    if stt_code == "hi-IN" and letters:
        return "hinglish"          # Hindi detected but written in Latin script
    return "en"


def update_language(state: dict, text: str, stt_code: Optional[str] = None) -> str:
    """Set and return the session's reply language. Short replies ("haan", a phone
    number, a name) never switch it, so a misdetection cannot flip the call."""
    current = state.get("lang")
    guess = detect(text, stt_code)
    if guess == "other":
        state["lang"] = current or "en"
    elif current is None:
        state["lang"] = guess
    elif guess != current and len([w for w in (text or "").split() if _LETTER.search(w)]) >= 3:
        logger.info("Language switched %s -> %s", current, guess)
        state["lang"] = guess
    return state["lang"]


# ── 2. INPUT: NORMALISE TO ENGLISH ────────────────────────────────────────────
_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_HI_NUMBERS = {
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "छे": 6,
    "सात": 7, "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14,
    "पंद्रह": 15, "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20, "पच्चीस": 25, "तीस": 30,
}
_ROMAN_NUMBERS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "che": 6,
    "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "bees": 20,
}
# (pattern, English) applied in order; Devanagari and romanised forms side by side
_PHRASES = [
    (r"मेरा नाम\s+(.+?)\s+है", r"my name is \1"), (r"\bmera naam\s+(.+?)\s+hai\b", r"my name is \1"),
    (r"मेरा नाम", "my name is"), (r"\bmera naam\b", "my name is"),
    (r"मेरा (?:मोबाइल )?(?:नंबर|नम्बर)", "my number is"), (r"\bmera (?:mobile )?number\b", "my number is"),
    (r"परसों|\bparso[n]?\b", "day after tomorrow"),
    (r"कल|\bkal\b", "tomorrow"), (r"आज|\baaj\b", "today"),
    (r"साढ़े\s*(\d{1,2})", r"\1:30"), (r"\bsaadhe\s*(\d{1,2})", r"\1:30"),
    (r"बजे|\bbaje\b|\bbje\b", "baje"),
    (r"शाम|\bshaam\b|\bsham\b", "evening"), (r"रात|\braat\b", "night"),
    (r"सुबह|\bsubah\b", "morning"), (r"दोपहर|\bdopahar\b", "afternoon"),
    (r"लोगों|लोगो|लोग|व्यक्ति|मेहमान|\blogon\b|\blogo\b|\blog\b|\bvyakti\b|\bmehmaan\b", "people"),
    (r"के लिए|\bke liye\b|\bke liya\b", "for"),
    # Not "kar do": "saat baje kar do" means "make it 7", not "yes".
    (r"जी हाँ|जी हां|हाँ जी|हां जी|हाँ|हां|ठीक है|बिल्कुल|\bhaan ji\b|\bji haan\b|\bhaan\b|"
     r"\btheek hai\b|\bthik hai\b|\bbilkul\b", "yes"),
    (r"नहीं|नही|\bnahi\b|\bnahin\b", "no"),
    (r"टेबल", "table"), (r"बुकिंग", "booking"), (r"बुक", "book"), (r"रिज़र्व|रिजर्व", "reserve"),
    (r"कैंसल|कैंसिल|रद्द", "cancel"), (r"पार्किंग", "parking"), (r"मेन्यू|मेनू", "menu"),
    (r"शाकाहारी", "vegetarian"), (r"मोबाइल|नंबर|नम्बर", "number"), (r"नाम", "name"),
    (r"सोमवार|\bsomvar\b", "Monday"), (r"मंगलवार|\bmangalvar\b", "Tuesday"), (r"बुधवार|\bbudhvar\b", "Wednesday"),
    (r"गुरुवार|वीरवार|\bguruvar\b", "Thursday"), (r"शुक्रवार|\bshukravar\b", "Friday"),
    (r"शनिवार|\bshanivar\b", "Saturday"), (r"रविवार|इतवार|\bravivar\b|\bitvaar\b", "Sunday"),
    (r"जनवरी", "January"), (r"फ़रवरी|फरवरी", "February"), (r"मार्च", "March"), (r"अप्रैल", "April"),
    (r"मई", "May"), (r"जून", "June"), (r"जुलाई", "July"), (r"अगस्त", "August"), (r"सितंबर|सितम्बर", "September"),
    (r"अक्टूबर|अक्तूबर", "October"), (r"नवंबर|नवम्बर", "November"), (r"दिसंबर|दिसम्बर", "December"),
    (r"(\d{1,2})\s*तारीख", r"the \1th"), (r"(\d{1,2})\s*tarikh\b", r"the \1th"),
]
_PHRASES = [(re.compile(p, re.I), r) for p, r in _PHRASES]
_ROMAN_NUM_CONTEXT = re.compile(
    r"\b(" + "|".join(_ROMAN_NUMBERS) + r")\s+(people|baje|log|logon|guests)\b", re.I)


def normalise(text: str) -> str:
    """Rule-based Hindi/Hinglish/Kannada -> English for the words that matter to bookings."""
    out = (text or "").translate(_DEV_DIGITS).translate(kn.DIGITS)
    if kn.KANNADA.search(out):
        out = _normalise_kannada(out)
    for pattern, repl in _PHRASES:          # phrases first: "कर दो" is "yes", not "कर 2"
        out = pattern.sub(repl, out)
    for word, n in sorted(_HI_NUMBERS.items(), key=lambda kv: -len(kv[0])):
        # "कर दो" / "दे दो" mean "do it" / "give", not "two"
        guard = r"(?<!कर )(?<!दे )" if word == "दो" else ""
        out = re.sub(rf"{guard}(?<![ऀ-ॿ]){word}(?![ऀ-ॿ])", str(n), out)
    out = re.sub(r"साढ़े\s*(\d{1,2})", r"\1:30", out)       # साढ़े सात -> 7:30 (after numbers)
    out = _ROMAN_NUM_CONTEXT.sub(lambda m: f"{_ROMAN_NUMBERS[m.group(1).lower()]} {m.group(2)}", out)
    out = re.sub(r"\s*[।|]\s*", ". ", out)
    return re.sub(r"\s+", " ", out).strip()


_KN_PHRASES = [(re.compile(p), r) for p, r in kn.PHRASES]
_KN_HALVES = sorted(kn.half_past_patterns(), key=lambda kv: -len(kv[0]))


def _normalise_kannada(out: str) -> str:
    for word, value in _KN_HALVES:                     # ಏಳುವರೆ -> 7:30
        out = out.replace(word, f" {value} ")
    for word, n in sorted(kn.PEOPLE_WORDS.items(), key=lambda kv: -len(kv[0])):
        out = out.replace(word, f" {n} people ")       # ಇಬ್ಬರು -> 2 people
    for pattern, repl in _KN_PHRASES:
        out = pattern.sub(repl, out)
    for word, n in sorted(kn.NUMBERS.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"(?<![ಀ-೿]){word}(?![ಀ-೿])", f" {n} ", out)
    return re.sub(r"\s+", " ", out)


def _needs_llm(normalised: str, lang: str, asked: Optional[str]) -> bool:
    if asked in ("name", "phone") or lang == "en":
        return False
    leftover = DEVANAGARI.sub("", re.sub(r"my name is .*", "", normalised))
    if lang == "kn":
        return bool(kn.KANNADA.search(re.sub(r"my name is .*", "", normalised)))
    if lang == "hi":
        return bool(DEVANAGARI.search(re.sub(r"my name is .*", "", normalised)))
    # Hinglish: translate longer sentences that still carry Hindi grammar
    return len(normalised.split()) >= 5 and len(_HINGLISH_MARKERS.findall(leftover)) >= 2


@lru_cache(maxsize=512)
def _llm_to_english(text: str) -> Optional[str]:
    import llm_client
    return llm_client.complete(
        text,
        system=("Translate the restaurant caller's message into simple English. Keep names, numbers, "
                "dates, times and phone numbers exactly as given. Output only the English translation."),
        max_tokens=120,
    )


def _facts(text: str) -> dict:
    """Date, time and party size the rule reader finds in a sentence."""
    import availability as av
    import slot_rules
    return {"date": slot_rules.parse_date(text, av.today()), "time": slot_rules.parse_time(text),
            "people": slot_rules.parse_people(text),
            # a "yes" or "no" matters as much as a date: "हां, बुक कर दो" must keep its yes
            "yes": True if re.search(r"\byes\b", text, re.I) else None,
            "no": True if re.search(r"\bno\b", text, re.I) else None}


def to_english(text: str, lang: str, asked: Optional[str] = None) -> str:
    """What the English pipeline should see for this caller utterance."""
    if lang == "en":
        return text
    normalised = normalise(text)
    if not _needs_llm(normalised, lang, asked):
        return normalised
    # The LLM gets the normalised text (dates and times already in English) and its
    # translation is only used if it keeps every date, time and party size the rules found.
    translated = _llm_to_english(normalised)
    if not translated:
        return normalised
    known, got = _facts(normalised), _facts(translated)
    if any(v is not None and got[k] != v for k, v in known.items()):
        logger.info("Rejected translation that changed %s: %r", known, translated[:80])
        return normalised
    logger.info("Translated input (%s): %r -> %r", lang, normalised[:60], translated[:60])
    return translated


# ── 3. OUTPUT: LOCALISE THE REPLY ─────────────────────────────────────────────
_HI_DAYS = {"Monday": "सोमवार", "Tuesday": "मंगलवार", "Wednesday": "बुधवार", "Thursday": "गुरुवार",
            "Friday": "शुक्रवार", "Saturday": "शनिवार", "Sunday": "रविवार"}
_HI_MONTHS = {"January": "जनवरी", "February": "फ़रवरी", "March": "मार्च", "April": "अप्रैल", "May": "मई",
              "June": "जून", "July": "जुलाई", "August": "अगस्त", "September": "सितंबर", "October": "अक्टूबर",
              "November": "नवंबर", "December": "दिसंबर"}
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))? (AM|PM)\b")


def _period(h: int, ampm: str, lang: str) -> str:
    if ampm == "AM":
        key = "night" if h in (12, 1, 2, 3) else "morning"
    else:
        # as people say it: 7 PM is "shaam", 8 PM onwards is "raat"
        key = "afternoon" if h in (12, 1, 2, 3) else "evening" if h in (4, 5, 6, 7) else "night"
    return {"hi": {"morning": "सुबह", "afternoon": "दोपहर", "evening": "शाम", "night": "रात"},
            "hinglish": {"morning": "subah", "afternoon": "dopahar", "evening": "shaam", "night": "raat"},
            "kn": kn.PERIODS}[lang][key]


def local_times(text: str, lang: str, at: bool = True) -> str:
    """'7:30 PM' -> 'शाम 7:30 बजे' / 'shaam 7:30 baje' / 'ಸಂಜೆ 7:30 ಗಂಟೆಗೆ' (ಗಂಟೆ when at=False)."""
    word = {"hi": "बजे", "hinglish": "baje", "kn": kn.HOUR_AT if at else kn.HOUR}[lang]

    def repl(m):
        h, mins, ampm = int(m.group(1)), m.group(2), m.group(3)
        clock = f"{h}:{mins}" if mins else str(h)
        return f"{_period(h, ampm, lang)} {clock} {word}"
    return _TIME.sub(repl, text)


def local_date(text: str, lang: str) -> str:
    """'Thursday 19 November' -> 'गुरुवार, 19 नवंबर' / 'Thursday, 19 November'."""
    m = re.fullmatch(r"(\w+) (\d{1,2}) (\w+)", text.strip())
    if not m:
        return text
    day, num, month = m.groups()
    if lang == "hi":
        return f"{_HI_DAYS.get(day, day)}, {num} {_HI_MONTHS.get(month, month)}"
    if lang == "kn":
        return f"{kn.DAYS.get(day, day)}, {num} {kn.MONTHS.get(month, month)}"
    return f"{day}, {num} {month}"


# Booking notes (dietary needs and occasions) as spoken in each language
NOTE_LABELS = {
    "nut allergy": ("नट्स से एलर्जी", "nuts se allergy", "ನಟ್ಸ್ ಅಲರ್ಜಿ"),
    "gluten-free": ("ग्लूटेन-फ्री खाना", "gluten-free khana", "ಗ್ಲುಟೆನ್-ಫ್ರೀ ಆಹಾರ"),
    "Jain food": ("जैन खाना", "Jain khana", "ಜೈನ ಆಹಾರ"),
    "vegan": ("वीगन खाना", "vegan khana", "ವೀಗನ್ ಆಹಾರ"),
    "vegetarian": ("शाकाहारी खाना", "veg khana", "ಸಸ್ಯಾಹಾರಿ ಆಹಾರ"),
    "wheelchair access": ("व्हीलचेयर की सुविधा", "wheelchair access", "ವೀಲ್‌ಚೇರ್ ವ್ಯವಸ್ಥೆ"),
    "high chair": ("बच्चों की कुर्सी", "baby chair", "ಮಕ್ಕಳ ಕುರ್ಚಿ"),
    "birthday": ("जन्मदिन", "birthday", "ಹುಟ್ಟುಹಬ್ಬ"),
    "anniversary": ("सालगिरह", "anniversary", "ವಾರ್ಷಿಕೋತ್ಸವ"),
    "date night": ("डेट नाइट", "date night", "ಡೇಟ್ ನೈಟ್"),
    "business meal": ("बिज़नेस मील", "business meal", "ಬಿಸಿನೆಸ್ ಊಟ"),
}


def _local_value(kind: str, value: str, lang: str) -> str:
    if kind == "date":
        return local_date(value, lang)
    if kind == "notes":
        index = {"hi": 0, "hinglish": 1, "kn": 2}[lang]
        return ", ".join(NOTE_LABELS.get(n.strip(), (n,) * 3)[index] for n in value.split(","))
    if lang == "kn" and kind in ("hours", "times"):
        out = local_times(value, lang, at=False)
        if kind == "hours":                     # "X to Y" -> "Xಯಿಂದ Yಯವರೆಗೆ"
            out = re.sub(r"(.+?) to (.+?)(?= and |$)", rf"\1{kn.FROM} \2{kn.UNTIL}", out)
            return out.replace(" and ", kn.AND)
        return out.replace(" or ", kn.OR)
    if kind in ("time", "times", "hours"):
        out = local_times(value, lang)
        if kind == "hours":
            out = re.sub(r"(.+?) to (.+?)(?= and |$)",
                         (r"\1 से \2 तक" if lang == "hi" else r"\1 se \2 tak"), out)
            out = out.replace(" and ", " और " if lang == "hi" else " aur ")
        if kind == "times":
            out = out.replace(" or ", " या " if lang == "hi" else " ya ")
        return out
    return value


# English sentence template -> (Hindi, Hinglish). {placeholders} keep their values;
# date/time/times/hours placeholders are localised.
SENTENCES = {
    "What date would you like to book the table for?":
        ("आप किस तारीख के लिए टेबल बुक करना चाहेंगे?", "Aap kis date ke liye table book karna chahenge?"),
    "What time works best for you?": ("आपके लिए कौन सा समय ठीक रहेगा?", "Aapke liye kaunsa time theek rahega?"),
    "How many guests will be joining?": ("कितने लोग आएंगे?", "Kitne log aayenge?"),
    "What name should I put the reservation under?":
        ("बुकिंग किस नाम से करूं?", "Booking kis naam se karun?"),
    "Could I get your 10-digit mobile number for the booking?":
        ("बुकिंग के लिए अपना 10 अंकों का मोबाइल नंबर बताइए।", "Booking ke liye apna 10-digit mobile number bataiye."),
    "What is the 10-digit mobile number registered for the booking?":
        ("बुकिंग किस 10 अंकों के मोबाइल नंबर पर है?", "Booking kis 10-digit mobile number par hai?"),
    "On which date was the reservation?": ("बुकिंग किस तारीख की थी?", "Booking kis date ki thi?"),
    "At what time was the reservation?": ("बुकिंग किस समय की थी?", "Booking kis time ki thi?"),
    "That date has already passed.": ("वह तारीख निकल चुकी है।", "Woh date nikal chuki hai."),
    "Could you choose a future date for the booking?":
        ("कृपया आगे की कोई तारीख चुनिए।", "Please aage ki koi date choose kijiye."),
    "I want to be accurate, and I am not fully sure from our records.":
        ("मैं सही जानकारी देना चाहती हूं, और हमारे रिकॉर्ड से मुझे इसका पक्का पता नहीं है।",
         "Main sahi jaankari dena chahti hoon, aur hamare records se mujhe iska pakka pata nahi hai."),
    "Please call us at {phone}.": ("कृपया हमें {phone} पर कॉल करें।", "Please humein {phone} par call kijiye."),
    "For groups of more than {n} guests, we use our private dining room with at least 7 days advance notice.":
        ("{n} से ज़्यादा लोगों के लिए हमारा प्राइवेट डाइनिंग रूम है, जिसके लिए कम से कम 7 दिन पहले बताना होता है।",
         "{n} se zyada logon ke liye hamara private dining room hai, jiske liye kam se kam 7 din pehle batana hota hai."),
    "Please call us at {phone} or email {email}.":
        ("कृपया हमें {phone} पर कॉल करें या {email} पर ईमेल करें।",
         "Please humein {phone} par call kijiye ya {email} par email kijiye."),
    "Our largest table seats {n}.": ("हमारी सबसे बड़ी टेबल पर {n} लोग बैठ सकते हैं।",
                                     "Hamari sabse badi table par {n} log baith sakte hain."),
    "For bigger groups please call us at {phone}.":
        ("बड़े ग्रुप के लिए कृपया हमें {phone} पर कॉल करें।", "Bade group ke liye please humein {phone} par call kijiye."),
    "We have no free tables that day.": ("उस दिन कोई टेबल खाली नहीं है।", "Us din koi table khaali nahi hai."),
    "Would you like to try another date?": ("क्या आप कोई और तारीख देखना चाहेंगे?", "Kya aap koi aur date dekhna chahenge?"),
    "I can offer {times}.": ("मैं आपको {times} दे सकती हूं।", "Main aapko {times} de sakti hoon."),
    "Which would you prefer?": ("आप कौन सा समय लेना चाहेंगे?", "Aap kaunsa time lena chahenge?"),
    "Just to confirm: a table for {people} on {date} at {time}, under the name {name}, mobile ending {digits}.":
        ("एक बार कन्फर्म कर लूं: {date} को {time}, {people} लोगों के लिए टेबल, नाम {name}, मोबाइल नंबर के आखिरी अंक {digits}।",
         "Ek baar confirm kar loon: {date} ko {time}, {people} logon ke liye table, naam {name}, mobile ke last digits {digits}."),
    "Shall I book it?": ("क्या मैं बुक कर दूं?", "Kya main book kar doon?"),
    "Your table for {people} on {date} at {time} is confirmed under {name}.":
        ("{name} के नाम से {date} को {time}, {people} लोगों के लिए आपकी टेबल कन्फर्म हो गई है।",
         "{name} ke naam se {date} ko {time}, {people} logon ke liye aapki table confirm ho gayi hai."),
    "I have sent the confirmation and QR code to your WhatsApp.":
        ("मैंने कन्फर्मेशन और QR कोड आपके WhatsApp पर भेज दिया है।",
         "Maine confirmation aur QR code aapke WhatsApp par bhej diya hai."),
    "I could not send the WhatsApp confirmation right now, but your booking is saved.":
        ("अभी WhatsApp पर कन्फर्मेशन नहीं भेज पाई, लेकिन आपकी बुकिंग सेव हो गई है।",
         "Abhi WhatsApp par confirmation nahi bhej payi, lekin aapki booking save ho gayi hai."),
    "Just give your name at the entrance.": ("आने पर बस अपना नाम बता दीजिए।", "Aane par bas apna naam bata dijiye."),
    "That time has already passed.": ("वह समय निकल चुका है।", "Woh time nikal chuka hai."),
    "Could you choose a later time?": ("कृपया बाद का कोई समय चुनिए।", "Please baad ka koi time choose kijiye."),
    "We are closed on {date}.": ("{date} को हम बंद रहते हैं।", "{date} ko hum band rehte hain."),
    "Could you choose another day?": ("कृपया कोई और दिन चुनिए।", "Please koi aur din choose kijiye."),
    "On {date} we are open {hours}, and the last seating is at {time}.":
        ("{date} को हम {hours} खुले रहते हैं, और आखिरी बुकिंग {time} की है।",
         "{date} ko hum {hours} khule rehte hain, aur last seating {time} ki hai."),
    "What time works for you?": ("आपके लिए कौन सा समय ठीक रहेगा?", "Aapke liye kaunsa time theek rahega?"),
    "On {date} we are open {hours}.": ("{date} को हम {hours} खुले रहते हैं।", "{date} ko hum {hours} khule rehte hain."),
    "The last seating is at {time}.": ("आखिरी बुकिंग {time} की है।", "Last seating {time} ki hai."),
    "I need a valid date and time to check.": ("देखने के लिए मुझे सही तारीख और समय चाहिए।",
                                               "Check karne ke liye mujhe sahi date aur time chahiye."),
    "Sorry, we are fully booked at {time}.": ("माफ़ कीजिए, {time} सब टेबल बुक हैं।", "Sorry, {time} sab tables booked hain."),
    "Yes, we have a table on {date} at {time}.": ("जी हां, {date} को {time} टेबल खाली है।",
                                                   "Haan ji, {date} ko {time} table available hai."),
    "Would you like me to book it?": ("क्या मैं इसे बुक कर दूं?", "Kya main ise book kar doon?"),
    "That booking hold expired.": ("वह बुकिंग होल्ड खत्म हो गया।", "Woh booking hold expire ho gaya."),
    "Let us check again.": ("चलिए फिर से देखते हैं।", "Chaliye phir se dekhte hain."),
    "Sorry, {time} was just taken.": ("माफ़ कीजिए, {time} अभी-अभी बुक हो गया।", "Sorry, {time} abhi-abhi book ho gaya."),
    "I could not find any upcoming booking for that number.":
        ("उस नंबर पर कोई आने वाली बुकिंग नहीं मिली।", "Us number par koi upcoming booking nahi mili."),
    "I could not find an active booking matching those details.":
        ("इन जानकारियों से मेल खाती कोई बुकिंग नहीं मिली।", "In details se match karti koi booking nahi mili."),
    "Too many verification codes were requested for this number.":
        ("इस नंबर के लिए बहुत ज़्यादा कोड मांगे गए हैं।", "Is number ke liye bahut zyada codes maange gaye hain."),
    "Please try again in an hour or call us at {phone}.":
        ("कृपया एक घंटे बाद कोशिश करें या हमें {phone} पर कॉल करें।",
         "Please ek ghante baad try kijiye ya humein {phone} par call kijiye."),
    "To keep your booking safe, I have sent a 6-digit code to your WhatsApp.":
        ("आपकी बुकिंग सुरक्षित रखने के लिए मैंने आपके WhatsApp पर 6 अंकों का कोड भेजा है।",
         "Aapki booking safe rakhne ke liye maine aapke WhatsApp par 6-digit code bheja hai."),
    "Please tell me the code to confirm the cancellation.":
        ("कैंसलेशन कन्फर्म करने के लिए कृपया वह कोड बताइए।", "Cancellation confirm karne ke liye please woh code bataiye."),
    "Demo mode: your verification code is {code}.": ("डेमो मोड: आपका कोड है {code}।", "Demo mode: aapka code hai {code}."),
    "Please say it to confirm the cancellation.": ("कैंसलेशन कन्फर्म करने के लिए इसे बोलिए।",
                                                   "Cancellation confirm karne ke liye ise boliye."),
    "I cannot verify your number right now, so I cannot cancel by voice.":
        ("अभी मैं आपका नंबर वेरिफाई नहीं कर पा रही, इसलिए फ़ोन पर कैंसल नहीं हो सकता।",
         "Abhi main aapka number verify nahi kar pa rahi, isliye voice par cancel nahi ho sakta."),
    "That code has expired.": ("वह कोड एक्सपायर हो गया है।", "Woh code expire ho gaya hai."),
    "Say cancel to start again.": ("फिर से शुरू करने के लिए कैंसल बोलिए।", "Phir se shuru karne ke liye cancel boliye."),
    "Your booking has been cancelled successfully.": ("आपकी बुकिंग कैंसल हो गई है।", "Aapki booking cancel ho gayi hai."),
    "That booking is no longer active.": ("वह बुकिंग अब एक्टिव नहीं है।", "Woh booking ab active nahi hai."),
    "That code does not match.": ("यह कोड मेल नहीं खाता।", "Yeh code match nahi karta."),
    "Please try again.": ("कृपया फिर से बताइए।", "Please phir se bataiye."),
    "Too many wrong codes.": ("बहुत ज़्यादा गलत कोड।", "Bahut zyada galat codes."),
    "For your security I have stopped this cancellation.":
        ("आपकी सुरक्षा के लिए मैंने यह कैंसलेशन रोक दिया है।", "Aapki security ke liye maine yeh cancellation rok diya hai."),
    "I have passed this to our team with a note.": ("मैंने यह बात नोट के साथ हमारी टीम तक पहुंचा दी है।",
                                                    "Maine yeh baat note ke saath hamari team tak pahuncha di hai."),
    "For anything urgent, please call us at {phone}.": ("किसी भी ज़रूरी बात के लिए कृपया हमें {phone} पर कॉल करें।",
                                                       "Kuch urgent ho toh please humein {phone} par call kijiye."),
    "Please tell me the 6-digit code from your WhatsApp.": ("कृपया अपने WhatsApp पर आया 6 अंकों का कोड बताइए।",
                                                           "Please apne WhatsApp par aaya 6-digit code bataiye."),
    "Shall I go ahead with the booking?": ("क्या मैं बुकिंग कर दूं?", "Kya main booking kar doon?"),
    "Please tell me the 6-digit code when you are ready.": ("जब तैयार हों, 6 अंकों का कोड बताइए।",
                                                           "Jab ready hon, 6-digit code bataiye."),
    "No problem.": ("कोई बात नहीं।", "Koi baat nahi."),
    "What would you like to change?": ("आप क्या बदलना चाहेंगे?", "Aap kya badalna chahenge?"),
    "Sorry, I did not catch that.": ("माफ़ कीजिए, मैं समझ नहीं पाई।", "Sorry, main samajh nahi payi."),
    "Shall I book it, or would you like to change something?":
        ("क्या मैं बुक कर दूं, या आप कुछ बदलना चाहेंगे?", "Kya main book kar doon, ya aap kuch badalna chahenge?"),
    "Could you please repeat?": ("क्या आप दोबारा बोल सकते हैं?", "Kya aap dobara bol sakte hain?"),
    "Okay.": ("ठीक है।", "Theek hai."),
    "Okay, I have stopped this booking.": ("ठीक है, मैंने यह बुकिंग रोक दी है।", "Theek hai, maine yeh booking rok di hai."),
    "How else can I help?": ("मैं और कैसे मदद करूं?", "Main aur kaise help karun?"),
    "Thank you for calling {restaurant}.": ("{restaurant} में कॉल करने के लिए धन्यवाद।",
                                           "{restaurant} mein call karne ke liye dhanyavaad."),
    "Have a wonderful day!": ("आपका दिन शुभ हो!", "Aapka din achha ho!"),
    "Goodbye!": ("नमस्ते!", "Bye!"),
    "Okay, I have cleared the current request.": ("ठीक है, मैंने पिछली बात हटा दी है।", "Theek hai, maine pichhli request clear kar di hai."),
    "How can I help now?": ("अब मैं कैसे मदद करूं?", "Ab main kaise help karun?"),
    "Hello!": ("नमस्ते!", "Namaste!"),
    "How can I help you today?": ("आज मैं आपकी क्या मदद कर सकती हूं?", "Aaj main aapki kya help kar sakti hoon?"),
    # Phase 4: memory and waitlist
    "Welcome back, {name}!": ("फिर से स्वागत है, {name}!", "Welcome back, {name}!"),
    "I will remember your preferences to make your next booking quicker.":
        ("अगली बार बुकिंग जल्दी करने के लिए मैं आपकी पसंद याद रखूंगी।",
         "Agli baar booking jaldi karne ke liye main aapki pasand yaad rakhungi."),
    "Say forget me any time to remove them.": ("इन्हें हटाने के लिए कभी भी 'forget me' बोलिए।",
                                               "Inhe hatane ke liye kabhi bhi 'forget me' boliye."),
    "Please tell me the 10-digit mobile number whose saved details I should remove.":
        ("कृपया वह 10 अंकों का मोबाइल नंबर बताइए जिसकी जानकारी मुझे हटानी है।",
         "Please woh 10-digit mobile number bataiye jiski details mujhe hatani hain."),
    "Done, I have removed the details I remembered for that number.":
        ("हो गया, उस नंबर के लिए याद रखी गई जानकारी मैंने हटा दी है।",
         "Ho gaya, us number ki saved details maine hata di hain."),
    "I had nothing saved for that number.": ("उस नंबर के लिए मेरे पास कुछ भी सेव नहीं था।",
                                             "Us number ke liye mere paas kuch save nahi tha."),
    "Or I can add you to the waitlist for {time}.": ("या मैं आपको {time} की वेटिंग लिस्ट में डाल सकती हूं।",
                                                     "Ya main aapko {time} ki waiting list mein daal sakti hoon."),
    "Say waitlist if you would like that.": ("अगर चाहें तो वेटिंग लिस्ट बोलिए।", "Agar chahein toh waiting list boliye."),
    "You are on the waitlist for {date} at {time}.": ("आप {date} को {time} की वेटिंग लिस्ट में हैं।",
                                                      "Aap {date} ko {time} ki waiting list mein hain."),
    "I have noted: {notes}.": ("मैंने नोट कर लिया है: {notes}।", "Maine note kar liya hai: {notes}."),
    "I will message you on WhatsApp if a table frees up.":
        ("टेबल खाली होते ही मैं आपको WhatsApp पर बताऊंगी।", "Table khaali hote hi main aapko WhatsApp par bataungi."),
}

_TYPED = {"date": "date", "time": "time", "times": "times", "hours": "hours", "notes": "notes"}


def _compile(template: str):
    names = re.findall(r"\{(\w+)\}", template)
    pattern = re.escape(template)
    for n in names:
        pattern = pattern.replace(re.escape("{" + n + "}"), f"(?P<{n}>.+?)", 1)
    return re.compile(f"^{pattern}$"), names


_COMPILED = [(_compile(en), {"hi": hi, "hinglish": hinglish, "kn": kn.SENTENCES.get(en)})
             for en, (hi, hinglish) in SENTENCES.items()]
_NOW = {"hi": "अब, ", "hinglish": "Ab, ", "kn": kn.NOW}


def _split_sentences(text: str) -> list:
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def _from_table(sentence: str, lang: str) -> Optional[str]:
    prefix = ""
    if sentence.startswith("Now, "):                       # "Now, what date would you ..."
        sentence = sentence[5].upper() + sentence[6:]
        prefix = _NOW[lang]
    for (regex, names), targets in _COMPILED:
        m = regex.match(sentence)
        if m and targets.get(lang):
            values = {n: _local_value(_TYPED.get(n, "text"), m.group(n), lang) for n in names}
            return prefix + targets[lang].format(**values)
    return None


_TARGETS = {
    "hi": "Hindi in Devanagari script, polite (aap)",
    "hinglish": "Hinglish: Hindi written in Latin script the way people text in India, keeping common English words",
    "kn": "Kannada in Kannada script, polite (neevu)",
}


@lru_cache(maxsize=1024)
def _llm_from_english(sentence: str, lang: str) -> Optional[str]:
    """LLM translation, accepted only if it is written in the right script.
    Small models sometimes answer a Kannada request in Hindi; then we retry once
    and otherwise keep the English sentence (right words beat the wrong language)."""
    import llm_client
    system = (f"Translate this restaurant assistant's sentence into {_TARGETS[lang]}. Keep names, numbers, "
              "prices, phone numbers and email addresses exactly. Output only the translation.")
    for attempt in range(2):
        out = llm_client.complete(sentence, system=system, max_tokens=200)
        if out and _right_script(out, lang):
            return out
        system += f" The answer MUST be in {_TARGETS[lang].split(',')[0]}; do not use any other language."
    logger.warning("No %s translation in the right script for %r; keeping English", lang, sentence[:60])
    return None


def _right_script(text: str, lang: str) -> bool:
    letters = _LETTER.findall(text)
    if not letters:
        return True
    share = {"hi": DEVANAGARI, "kn": kn.KANNADA}.get(lang)
    if share is None:                           # Hinglish: Latin script, no Devanagari/Kannada
        return not DEVANAGARI.search(text) and not kn.KANNADA.search(text)
    return sum(1 for ch in letters if share.match(ch)) / len(letters) > 0.5


def localise(reply: str, lang: str) -> str:
    """The English reply in the caller's language."""
    if lang not in ("hi", "hinglish", "kn") or not reply:
        return reply
    out = []
    for sentence in _split_sentences(reply):
        local = _from_table(sentence, lang)
        if local is None and lang in settings.llm_translate_languages:
            local = _llm_from_english(sentence, lang)
        if local is None:
            # No hand-written version and the LLM is not trusted for this language
            # (qwen 7B writes broken Kannada): say it in clear English instead.
            local = sentence
        out.append(local)
    return " ".join(out)


def stt_language_name(code: Optional[str]) -> str:
    return {"hi-IN": "Hindi", "en-IN": "English", "kn-IN": "Kannada"}.get(code or "", code or "unknown")


def supported_languages() -> list:
    return list(LANGUAGES)


__all__ = ["detect", "update_language", "normalise", "to_english", "localise", "settings"]
