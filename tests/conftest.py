"""
Shared test fixtures. Nothing here calls Ollama, Edge-TTS or Twilio:
the LLM, retrieval and WhatsApp sends are replaced with fakes.
"""

import json
import os
import sys
from datetime import datetime

# Settings are read at import time, so the environment is set first.
os.environ.update({
    "FLASK_SECRET_KEY": "test-secret-key-" + "x" * 32,
    "TTS_ENABLED": "false",
    "RAG_AUTOBUILD": "false",
    "TIMEZONE": "Asia/Kolkata",
    "TABLE_SEATS": "2,4,6",
    "DINING_MINUTES": "90",
    "LARGE_GROUP_THRESHOLD": "10",
    # Closed on Mondays; lunch and dinner windows on other days.
    "OPENING_HOURS": json.dumps({d: [["12:00", "15:30"], ["19:00", "23:00"]]
                                 for d in ("tue", "wed", "thu", "fri", "sat", "sun")}),
    "TWILIO_SID": "", "TWILIO_AUTH_TOKEN": "", "TWILIO_WHATSAPP_NUMBER": "",
    "OTP_DEV_ECHO": "false",
})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import availability  # noqa: E402
import brain  # noqa: E402
from config import settings  # noqa: E402
from database import init_db  # noqa: E402
from services import whatsapp  # noqa: E402

# Wednesday 18 November 2026, 10:00 in Kolkata. "Tomorrow" is Thursday the 19th.
FIXED_NOW = datetime(2026, 11, 18, 10, 0, tzinfo=settings.tz)
TOMORROW = "2026-11-19"
MONDAY = "2026-11-23"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    init_db()


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(availability, "now", lambda: FIXED_NOW)


class FakeWhatsApp:
    def __init__(self):
        self.sent = []
        self.succeed = True

    def __call__(self, to, body, media_url=None):
        self.sent.append({"to": to, "body": body, "media_url": media_url})
        return self.succeed

    def last_code(self):
        import re
        for msg in reversed(self.sent):
            m = re.search(r"code is (\d{6})", msg["body"])
            if m:
                return m.group(1)
        return None


@pytest.fixture(autouse=True)
def fake_whatsapp(monkeypatch):
    fake = FakeWhatsApp()
    monkeypatch.setattr(whatsapp, "_send", fake)
    return fake


class FakeLLM:
    """Returns slot JSON for messages registered by the test; '{}' otherwise."""

    def __init__(self):
        self.slots = {}
        self.down = False      # simulate Ollama timing out
        self.calls = 0

    def __call__(self, prompt, system=None, max_tokens=250, on_fail=brain.LLM_FALLBACK):
        self.calls += 1
        if self.down:
            return on_fail
        if system and system.startswith("Extract booking details"):
            return json.dumps(self.slots.get(prompt, {}))
        if system and system.startswith("Classify"):
            return "other"
        return "Hello! How can I help?"


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(brain, "llm", fake)
    monkeypatch.setattr(brain, "retrieve_hits", lambda **kwargs: [])
    return fake
