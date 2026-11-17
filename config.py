"""
config.py — single source of truth for every setting Vaani reads from the
environment. Every other module imports `settings` from here instead of
calling os.getenv() itself.
"""

import json
import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _env(name: str, default: str = "", *fallbacks: str) -> str:
    """Read an env var, trying older names as fallbacks (for old .env files)."""
    for key in (name, *fallbacks):
        value = os.getenv(key)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


def _default_hours(start_hour: int, end_hour: int) -> dict:
    window = [[f"{start_hour:02d}:00", f"{end_hour:02d}:00"]]
    return {day: window for day in DAY_KEYS}


def _parse_hours(raw: str, start_hour: int, end_hour: int) -> dict:
    """
    OPENING_HOURS is optional JSON, e.g.
    {"mon": [["12:00","15:30"],["19:00","23:00"]], "tue": [...], ... "sun": []}
    A missing day means closed. Without it, START_HOUR-END_HOUR applies daily.
    """
    if not raw:
        return _default_hours(start_hour, end_hour)
    data = json.loads(raw)
    return {day: [list(w) for w in data.get(day, [])] for day in DAY_KEYS}


def _parse_tables(raw: str) -> list:
    return [int(x) for x in raw.split(",") if x.strip().isdigit() and int(x) > 0]


@dataclass
class Settings:
    # Restaurant
    restaurant_name: str
    restaurant_phone: str
    private_dining_email: str
    timezone: str
    opening_hours: dict
    table_seats: list
    dining_minutes: int
    large_group_threshold: int
    max_party_size: int

    # App and security
    flask_secret_key: str
    is_production: bool
    db_path: str
    public_base_url: str
    ratelimit_storage_uri: str
    tts_enabled: bool
    rag_autobuild: bool
    otp_ttl_minutes: int
    otp_max_attempts: int
    otp_max_per_hour: int
    otp_dev_echo: bool
    login_max_fails: int
    login_lockout_seconds: int

    # LLM (any OpenAI-compatible endpoint: Ollama, Sarvam, ...)
    ollama_url: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_timeout: int
    agent_enabled: bool
    agent_max_steps: int
    llm_translate_languages: tuple

    # Speech (Gnani: Prisma speech-to-text, Timbre text-to-speech)
    gnani_base_url: str
    gnani_stt_key: str
    gnani_tts_key: str
    stt_model: str
    stt_language: str
    stt_format: str
    tts_model: str
    tts_voice: str
    tts_speed: float

    # WhatsApp (Twilio)
    twilio_sid: str
    twilio_auth_token: str
    twilio_whatsapp_number: str
    owner_whatsapp: str

    tz: ZoneInfo = field(init=False, repr=False)

    def __post_init__(self):
        self.tz = ZoneInfo(self.timezone)

    @property
    def gnani_stt_enabled(self) -> bool:
        return bool(self.gnani_stt_key)

    @property
    def gnani_tts_enabled(self) -> bool:
        return bool(self.gnani_tts_key)

    @property
    def whatsapp_configured(self) -> bool:
        return bool(self.twilio_sid and self.twilio_auth_token and self.twilio_whatsapp_number)

    @classmethod
    def from_env(cls) -> "Settings":
        start_hour = _env_int("START_HOUR", 10)
        end_hour = _env_int("END_HOUR", 22)
        return cls(
            restaurant_name=_env("RESTAURANT_NAME", "AM Restaurant"),
            restaurant_phone=_env("RESTAURANT_PHONE", "+91-98765-43210"),
            private_dining_email=_env("PRIVATE_DINING_EMAIL", "events@amrestaurant.in"),
            timezone=_env("TIMEZONE", "Asia/Kolkata"),
            opening_hours=_parse_hours(_env("OPENING_HOURS"), start_hour, end_hour),
            table_seats=_parse_tables(_env("TABLE_SEATS", "2,2,4,4,4,6,6,8,10")),
            dining_minutes=_env_int("DINING_MINUTES", 90),
            large_group_threshold=_env_int("LARGE_GROUP_THRESHOLD", 10),
            max_party_size=_env_int("MAX_PARTY_SIZE", 100),
            flask_secret_key=_env("FLASK_SECRET_KEY"),
            is_production=_env("FLASK_ENV", "development").lower() == "production",
            db_path=_env("DB_PATH", "bookings.db"),
            public_base_url=_env("PUBLIC_BASE_URL", "", "NGROK_URL").rstrip("/"),
            ratelimit_storage_uri=_env("RATELIMIT_STORAGE_URI", "memory://"),
            tts_enabled=_env_bool("TTS_ENABLED", True),
            rag_autobuild=_env_bool("RAG_AUTOBUILD", True),
            otp_ttl_minutes=_env_int("OTP_TTL_MINUTES", 10),
            otp_max_attempts=_env_int("OTP_MAX_ATTEMPTS", 5),
            otp_max_per_hour=_env_int("OTP_MAX_PER_HOUR", 3),
            otp_dev_echo=_env_bool("OTP_DEV_ECHO", False),
            login_max_fails=_env_int("LOGIN_MAX_FAILS", 5),
            login_lockout_seconds=_env_int("LOGIN_LOCKOUT_SECONDS", 300),
            ollama_url=_env("OLLAMA_URL", "http://localhost:11434/api/generate"),
            llm_base_url=_env("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/"),
            llm_model=_env("LLM_MODEL", "qwen2.5:7b", "OLLAMA_MODEL"),
            llm_api_key=_env("LLM_API_KEY", "ollama"),
            llm_timeout=_env_int("LLM_TIMEOUT", 45),
            agent_enabled=_env_bool("AGENT_ENABLED", True),
            agent_max_steps=_env_int("AGENT_MAX_STEPS", 5),
            llm_translate_languages=tuple(x.strip() for x in _env("LLM_TRANSLATE_LANGUAGES", "hi,hinglish")
                                          .split(",") if x.strip()),
            gnani_base_url=_env("GNANI_BASE_URL", "https://api.vachana.ai").rstrip("/"),
            gnani_stt_key=_env("GNANI_STT_KEY", "", "GNANI_API_KEY"),
            gnani_tts_key=_env("GNANI_TTS_KEY", "", "GNANI_API_KEY"),
            stt_model=_env("GNANI_STT_MODEL", "prisma-v3.0"),
            stt_language=_env("GNANI_STT_LANGUAGE", "hi-IN,en-IN,kn-IN"),
            stt_format=_env("GNANI_STT_FORMAT", "transcribe"),
            tts_model=_env("GNANI_TTS_MODEL", "timbre-v3.0"),
            tts_voice=_env("GNANI_TTS_VOICE", "Shivani"),
            tts_speed=float(_env("GNANI_TTS_SPEED", "1.0")),
            twilio_sid=_env("TWILIO_SID"),
            twilio_auth_token=_env("TWILIO_AUTH_TOKEN", "", "TWILIO_AUTH"),
            twilio_whatsapp_number=_env("TWILIO_WHATSAPP_NUMBER", "", "TWILIO_WHATSAPP_FROM"),
            owner_whatsapp=_env("OWNER_WHATSAPP"),
        )


settings = Settings.from_env()
