"""Phase 3: languages, localisation, Gnani speech services and the /voice endpoint."""

import base64
import io

import pytest

import brain
import i18n
from conftest import TOMORROW
from database import connection
from services import speech


def say(msg, state, lang_code=None):
    reply, _audio, state = brain.get_ai_response(msg, state, stt_language=lang_code)
    return reply, state


def bookings():
    with connection() as con:
        return con.execute("SELECT date, time, people, name FROM bookings").fetchall()


# ── Detection and sticky language ─────────────────────────────────────────────
@pytest.mark.parametrize("text,code,expected", [
    ("I want to book a table", "en-IN", "en"),
    ("मुझे टेबल बुक करनी है", "hi-IN", "hi"),
    ("mujhe kal table book karna hai", "en-IN", "hinglish"),
    ("kal ke liye table chahiye", None, "hinglish"),
    ("table venum", "ta-IN", "other"),
])
def test_detect(text, code, expected):
    assert i18n.detect(text, code) == expected


def test_short_replies_never_switch_language():
    state = {}
    i18n.update_language(state, "मुझे कल टेबल बुक करनी है", "hi-IN")
    for short in ("yes", "Ravi", "9876543210", "ok"):
        assert i18n.update_language(state, short, "en-IN") == "hi"
    assert i18n.update_language(state, "Actually can we continue in English please", "en-IN") == "en"


def test_unsupported_language_keeps_current():
    state = {"lang": "en"}
    assert i18n.update_language(state, "naan table book pannanum", "ta-IN") == "en"


# ── Normalising input ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("कल रात 8 बजे 4 लोगों के लिए टेबल बुक करनी है", "tomorrow night 8 baje 4 people for table book करनी है"),
    ("मेरा नाम रवि है", "my name is रवि"),
    ("हाँ, बुक कर दो", "yes, book कर दो"),
    ("kal shaam saat baje do log", "tomorrow evening 7 baje 2 people"),
    ("साढ़े सात बजे", "7:30 baje"),
    ("९८७६५४३२१०", "9876543210"),
    ("परसों दोपहर एक बजे", "day after tomorrow afternoon 1 baje"),
])
def test_normalise(text, expected):
    assert i18n.normalise(text) == expected


# ── Localising output ─────────────────────────────────────────────────────────
def test_localise_read_back_in_hindi_and_hinglish():
    en = ("Just to confirm: a table for 4 on Thursday 19 November at 8 PM, under the name Ravi, "
          "mobile ending 3 2 1 0. Shall I book it?")
    hi = i18n.localise(en, "hi")
    assert "गुरुवार, 19 नवंबर" in hi and "रात 8 बजे" in hi and "Ravi" in hi and "3 2 1 0" in hi
    assert hi.endswith("क्या मैं बुक कर दूं?")
    hinglish = i18n.localise(en, "hinglish")
    assert "raat 8 baje" in hinglish and hinglish.endswith("Kya main book kar doon?")


def test_localise_hours_and_alternatives():
    hours = i18n.localise("On Thursday 19 November we are open 12 PM to 3:30 PM and 7 PM to 11 PM, "
                          "and the last seating is at 9:30 PM. What time works for you?", "hi")
    assert "दोपहर 12 बजे से दोपहर 3:30 बजे तक और शाम 7 बजे से रात 11 बजे तक" in hours
    offer = i18n.localise("I can offer 7:30 PM or 9 PM.", "hinglish")
    assert offer == "Main aapko shaam 7:30 baje ya raat 9 baje de sakti hoon."


def test_english_is_untouched_and_unknown_sentences_fall_back(fake_chat):
    assert i18n.localise("Shall I book it?", "en") == "Shall I book it?"
    fake_chat.script = []                       # LLM unavailable: keep the English sentence
    assert i18n.localise("Paneer tikka costs Rs 320.", "hi") == "Paneer tikka costs Rs 320."


def test_unknown_sentence_uses_llm_translation(fake_chat):
    i18n._llm_from_english.cache_clear()
    fake_chat.script = ["पनीर टिक्का Rs 320 का है।"]
    assert i18n.localise("Paneer tikka costs Rs 320.", "hi") == "पनीर टिक्का Rs 320 का है।"


def test_every_template_has_matching_placeholders():
    import re
    for en, (hi, hinglish) in i18n.SENTENCES.items():
        names = set(re.findall(r"\{(\w+)\}", en))
        assert set(re.findall(r"\{(\w+)\}", hi)) == names, en
        assert set(re.findall(r"\{(\w+)\}", hinglish)) == names, en


# ── Whole conversations ───────────────────────────────────────────────────────
def test_full_booking_in_hindi(fake_llm):
    reply, state = say("कल रात 8 बजे 4 लोगों के लिए टेबल बुक करनी है", {}, "hi-IN")
    assert reply == "बुकिंग किस नाम से करूं?" and state["lang"] == "hi"
    reply, state = say("मेरा नाम रवि है", state, "hi-IN")
    assert "मोबाइल नंबर" in reply
    reply, state = say("9876543210", state, "en-IN")          # digits detected as English: stays Hindi
    assert reply.startswith("एक बार कन्फर्म कर लूं") and "रवि" in reply
    reply, state = say("हाँ", state, "hi-IN")
    assert "कन्फर्म हो गई है" in reply
    assert bookings() == [(TOMORROW, "20:00", 4, "रवि")]


def test_full_booking_in_hinglish(fake_llm):
    reply, state = say("kal shaam saat baje do log ke liye table book karna hai", {}, "hi-IN")
    assert state["lang"] == "hinglish" and reply == "Booking kis naam se karun?"
    reply, state = say("mera naam Priya hai", state)
    reply, state = say("9876543210", state)
    assert reply.startswith("Ek baar confirm kar loon") and "shaam 7 baje" in reply
    reply, state = say("haan", state)
    assert "confirm ho gayi hai" in reply
    assert bookings() == [(TOMORROW, "19:00", 2, "Priya")]


def test_english_conversation_unchanged(fake_llm):
    reply, state = say("Book a table for 4 tomorrow at 8 pm", {}, "en-IN")
    assert reply == "What name should I put the reservation under?" and state["lang"] == "en"


# ── Gnani speech services (network mocked) ────────────────────────────────────
class FakeResponse:
    def __init__(self, status=200, json_body=None, content=b"", ctype="application/json"):
        self.status_code, self._json, self.content = status, json_body, content
        self.headers = {"content-type": ctype}
        self.text = str(json_body)

    def json(self):
        return self._json


def test_prisma_request_and_response(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "gnani_stt_key", "test-key")
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        return FakeResponse(json_body={"success": True, "transcript": "कल टेबल चाहिए",
                                       "detected_language_code": "hi-IN"})
    monkeypatch.setattr(speech.requests, "post", fake_post)
    result = speech.transcribe(b"RIFF....", "speech.wav", "audio/wav")
    assert result.ok and result.text == "कल टेबल चाहिए" and result.language == "hi-IN"
    url, kw = calls[0]
    assert url == "https://api.vachana.ai/stt/v3"
    assert kw["headers"] == {"X-API-Key-ID": "test-key"}
    assert kw["data"] == {"language_code": "hi-IN,en-IN,kn-IN", "model": "prisma-v3.0", "format": "transcribe"}
    assert "audio_file" in kw["files"]


def test_prisma_retries_once_on_429(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "gnani_stt_key", "test-key")
    monkeypatch.setattr(speech.time, "sleep", lambda s: None)
    replies = [FakeResponse(429, {"detail": {"error_code": "RATE_LIMITED"}}),
               FakeResponse(json_body={"success": True, "transcript": "hello"})]
    monkeypatch.setattr(speech.requests, "post", lambda url, **kw: replies.pop(0))
    assert speech.transcribe(b"x").text == "hello"


def test_timbre_request_and_fallback(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "gnani_tts_key", "test-key")
    monkeypatch.setattr(settings, "tts_enabled", True)
    sent = []

    def fake_post(url, **kw):
        sent.append((url, kw))
        return FakeResponse(content=b"ID3mp3bytes", ctype="audio/mpeg")
    monkeypatch.setattr(speech.requests, "post", fake_post)
    out = speech.synthesize("नमस्ते", "hi")
    assert base64.b64decode(out.audio_b64) == b"ID3mp3bytes" and out.mime == "audio/mpeg"
    url, kw = sent[0]
    assert url.endswith("/api/v1/tts/inference")
    assert kw["json"]["language"] == "hi-IN" and kw["json"]["model"] == "timbre-v3.0"
    assert kw["json"]["audio_config"]["container"] == "mp3"

    # Timbre error -> Edge-TTS fallback is used
    monkeypatch.setattr(speech.requests, "post", lambda url, **kw: FakeResponse(400, {"success": False}))
    monkeypatch.setattr(speech, "_edge", lambda text, lang: speech.Speech("ZWRnZQ==", "audio/mpeg"))
    assert speech.synthesize("hello", "en").audio_b64 == "ZWRnZQ=="


# ── /voice endpoint ───────────────────────────────────────────────────────────
@pytest.fixture
def client():
    from app import create_app
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
    return app.test_client()


def test_voice_config_without_key(client):
    assert client.get("/voice/config").get_json()["stt"] == "browser"
    assert client.post("/voice", data={}).status_code == 503


def test_voice_turn_in_hindi(client, monkeypatch, fake_llm):
    from config import settings
    monkeypatch.setattr(settings, "gnani_stt_key", "test-key")
    monkeypatch.setattr(speech, "transcribe", lambda audio, filename, mime: speech.Transcript(
        "कल रात 8 बजे 4 लोगों के लिए टेबल बुक करनी है", "hi-IN"))
    wav = io.BytesIO(b"RIFF" + b"\0" * 4000)
    data = client.post("/voice", data={"audio": (wav, "speech.wav")},
                       content_type="multipart/form-data").get_json()
    assert data["transcript"].startswith("कल रात")
    assert data["response"] == "बुकिंग किस नाम से करूं?" and data["language"] == "hi"


def test_voice_ignores_silence(client, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "gnani_stt_key", "test-key")
    monkeypatch.setattr(speech, "transcribe", lambda audio, filename, mime: speech.Transcript("", None))
    data = client.post("/voice", data={"audio": (io.BytesIO(b"\0" * 4000), "s.wav")},
                       content_type="multipart/form-data").get_json()
    assert data == {"transcript": "", "response": None}


def test_translation_that_changes_a_date_is_rejected(fake_chat):
    """Eval failure: the LLM turned 'kal' (tomorrow) into 'tonight'."""
    i18n._llm_to_english.cache_clear()
    fake_chat.script = ["I want a table for 4 people at 8 pm tonight"]
    out = i18n.to_english("mujhe kal raat 8 baje 4 logon ke liye table chahiye", "hinglish")
    assert "tomorrow" in out and "tonight" not in out


def test_good_translation_is_used(fake_chat):
    i18n._llm_to_english.cache_clear()
    fake_chat.script = ["I need to book a table for 4 people tomorrow night at 8"]
    out = i18n.to_english("कल रात 8 बजे 4 लोगों के लिए टेबल बुक करनी है", "hi")
    assert out == "I need to book a table for 4 people tomorrow night at 8"


def test_correction_with_no_in_hindi(fake_llm):
    """Eval failure: 'नहीं, सात बजे कर दो' (no, make it 7) was treated as a plain no."""
    _, state = say("कल रात 8 बजे 4 लोगों के लिए टेबल बुक करनी है", {}, "hi-IN")
    _, state = say("मेरा नाम रवि है", state, "hi-IN")
    reply, state = say("9876543210", state)
    reply, state = say("नहीं, सात बजे कर दो", state, "hi-IN")
    assert reply.startswith("एक बार कन्फर्म कर लूं") and "शाम 7 बजे" in reply
    reply, state = say("हाँ", state, "hi-IN")
    assert bookings() == [(TOMORROW, "19:00", 4, "रवि")]


def test_translation_that_drops_yes_is_rejected(fake_chat):
    """Eval failure hi-03: 'हां, बुक कर दो' was translated to 'book please'."""
    i18n._llm_to_english.cache_clear()
    fake_chat.script = ["book please"]
    assert i18n.to_english("हां, बुक कर दो", "hi").startswith("yes")


@pytest.mark.parametrize("raw,expected", [
    ("Your table for 4:00 am on Thursday 19 November at 8 pm", "Your table for 4 on Thursday 19 November at 8 pm"),
    ("a party of 6:00 am tomorrow", "a party of 6 tomorrow"),
    ("book for 7:30 pm", "book for 7:30 pm"),
    ("we open at 10:00 am", "we open at 10:00 am"),
])
def test_prisma_party_size_read_as_time_is_fixed(raw, expected):
    """Live finding: Prisma's number formatting turned 'a table for four' into 'for 4:00 am'."""
    assert speech.clean_transcript(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("9, 8, 7, 6, 5, 4, 3, 2, 1, 0।", "9876543210"),
    ("मेरा नाम रवि है।", "मेरा नाम रवि है"),
    ("Yes.", "Yes"),
    ("कल 8 बजे, 4 लोग", "कल 8 बजे, 4 लोग"),
])
def test_clean_transcript_digits_and_full_stops(raw, expected):
    """Live finding: spoken digits arrive comma-separated; '।' ended up inside names."""
    assert speech.clean_transcript(raw) == expected


def test_digits_alone_never_switch_language():
    state = {"lang": "hi"}
    assert i18n.update_language(state, "9 8 7 6 5 4 3 2 1 0", None) == "hi"


def test_hindi_full_stop_not_part_of_name():
    import slot_rules
    assert slot_rules.parse_name("आशा।", asked=True) == "आशा"


# ── Kannada ───────────────────────────────────────────────────────────────────
def test_every_sentence_has_kannada_with_same_placeholders():
    import re
    import i18n_kn
    missing = [en for en in i18n.SENTENCES if en not in i18n_kn.SENTENCES]
    assert not missing, missing
    for en, kn_text in i18n_kn.SENTENCES.items():
        assert set(re.findall(r"\{(\w+)\}", kn_text)) == set(re.findall(r"\{(\w+)\}", en)), en


@pytest.mark.parametrize("text,expected", [
    ("ನಾಳೆ ರಾತ್ರಿ 8 ಗಂಟೆಗೆ ನಾಲ್ಕು ಜನರಿಗೆ ಟೇಬಲ್ ಬುಕ್ ಮಾಡಬೇಕು",
     {"date": TOMORROW, "time": "20:00", "people": 4}),
    ("ನಾಡಿದ್ದು ಸಂಜೆ ಏಳುವರೆಗೆ ಇಬ್ಬರಿಗೆ ಟೇಬಲ್ ಬೇಕು", {"date": "2026-11-20", "time": "19:30", "people": 2}),
    ("ಶನಿವಾರ ಮಧ್ಯಾಹ್ನ ಒಂದು ಗಂಟೆಗೆ ಮೂವರು", {"date": "2026-11-21", "time": "13:00", "people": 3}),
    ("೯೮೭೬೫೪೩೨೧೦", {"phone": "9876543210"}),
])
def test_kannada_booking_words(text, expected):
    import slot_rules
    from datetime import date
    found = slot_rules.parse_all(i18n.normalise(text), date(2026, 11, 18))
    assert {k: found[k] for k in expected} == expected


def test_kannada_detection_and_localisation():
    assert i18n.detect("ನಾಳೆ ಟೇಬಲ್ ಬೇಕು", "kn-IN") == "kn"
    out = i18n.localise("Just to confirm: a table for 4 on Thursday 19 November at 8 PM, under the name ರವಿ, "
                        "mobile ending 3 2 1 0. Shall I book it?", "kn")
    assert "ಗುರುವಾರ, 19 ನವೆಂಬರ್" in out and "ರಾತ್ರಿ 8 ಗಂಟೆಗೆ" in out and out.endswith("ಬುಕ್ ಮಾಡಲೇ?")
    hours = i18n.localise("On Thursday 19 November we are open 10 AM to 10 PM.", "kn")
    assert "ಬೆಳಿಗ್ಗೆ 10 ಗಂಟೆಯಿಂದ ರಾತ್ರಿ 10 ಗಂಟೆಯವರೆಗೆ" in hours


def test_full_booking_in_kannada(fake_llm):
    reply, state = say("ನಾಳೆ ರಾತ್ರಿ 8 ಗಂಟೆಗೆ ನಾಲ್ಕು ಜನರಿಗೆ ಟೇಬಲ್ ಬುಕ್ ಮಾಡಬೇಕು", {}, "kn-IN")
    assert state["lang"] == "kn" and reply == "ಬುಕಿಂಗ್ ಯಾರ ಹೆಸರಿನಲ್ಲಿ ಮಾಡಲಿ?"
    reply, state = say("ನನ್ನ ಹೆಸರು ರವಿ", state, "kn-IN")
    reply, state = say("9876543210", state)
    assert reply.startswith("ಒಮ್ಮೆ ಖಚಿತಪಡಿಸುತ್ತೇನೆ") and "ರವಿ" in reply
    reply, state = say("ಹೌದು", state, "kn-IN")
    assert "ಖಚಿತವಾಗಿದೆ" in reply
    assert bookings() == [(TOMORROW, "20:00", 4, "ರವಿ")]


def test_translation_in_wrong_script_is_retried_then_english(fake_chat, monkeypatch):
    """Eval failure kn-07: a Kannada translation came back in Hindi."""
    from config import settings
    monkeypatch.setattr(settings, "llm_translate_languages", ("hi", "hinglish", "kn"))
    i18n._llm_from_english.cache_clear()
    fake_chat.script = ["पनीर टिक्का Rs 320 का है।", "ಪನೀರ್ ಟಿಕ್ಕಾ ಬೆಲೆ Rs 320."]
    assert i18n.localise("Paneer tikka costs Rs 320.", "kn") == "ಪನೀರ್ ಟಿಕ್ಕಾ ಬೆಲೆ Rs 320."
    i18n._llm_from_english.cache_clear()
    fake_chat.script = ["पनीर टिक्का Rs 320 का है।", "पनीर टिक्का Rs 320 का है।"]
    assert i18n.localise("Paneer tikka costs Rs 320.", "kn") == "Paneer tikka costs Rs 320."


def test_kannada_free_text_stays_english_unless_enabled(fake_chat):
    """qwen 7B writes broken Kannada, so free-form Kannada is off by default."""
    i18n._llm_from_english.cache_clear()
    fake_chat.script = ["ಮೇಖೌbred ಸಟ್ಡಿಯೋ"]
    assert i18n.localise("Parking is at Mehboob Studio.", "kn") == "Parking is at Mehboob Studio."
    assert i18n.localise("Shall I book it?", "kn") == "ಬುಕ್ ಮಾಡಲೇ?"      # hand-written still used
