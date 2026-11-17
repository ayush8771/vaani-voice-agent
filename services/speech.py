"""
services/speech.py — speech-to-text and text-to-speech.

  * Gnani Prisma (speech-to-text) and Gnani Timbre (text-to-speech) when a
    GNANI key is configured (https://docs.gnani.ai).
  * Otherwise the browser does speech recognition and Edge-TTS speaks.

Gnani allows one active request per account, so a 429 is retried once.
"""

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)

# Our reply languages -> Timbre language codes and Edge-TTS fallback voices
TIMBRE_LANGUAGE = {"en": "en-IN", "hi": "hi-IN", "hinglish": "hi-en"}
EDGE_VOICE = {"en": "en-IN-PrabhatNeural", "hi": "hi-IN-MadhurNeural", "hinglish": "en-IN-PrabhatNeural"}


@dataclass
class Transcript:
    text: str
    language: Optional[str] = None     # Prisma detected_language_code, e.g. "hi-IN"
    ok: bool = True
    error: str = ""
    latency: float = 0.0


@dataclass
class Speech:
    audio_b64: str
    mime: str


def _post_with_retry(url: str, **kwargs) -> requests.Response:
    response = requests.post(url, **kwargs)
    if response.status_code == 429:          # one active request per account
        time.sleep(0.4)
        response = requests.post(url, **kwargs)
    return response


# ── SPEECH-TO-TEXT (Gnani Prisma) ─────────────────────────────────────────────
def transcribe(audio: bytes, filename: str = "speech.wav", mime: str = "audio/wav") -> Transcript:
    if not settings.gnani_stt_enabled:
        return Transcript("", ok=False, error="Speech-to-text is not configured")
    started = time.time()
    data = {"language_code": settings.stt_language, "model": settings.stt_model}
    if settings.stt_format and settings.stt_format != "verbatim":
        data["format"] = settings.stt_format
    try:
        r = _post_with_retry(
            f"{settings.gnani_base_url}/stt/v3",
            headers={"X-API-Key-ID": settings.gnani_stt_key},
            files={"audio_file": (filename, audio, mime)},
            data=data, timeout=30,
        )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or not body.get("success", False):
            error = (body.get("error") or {}).get("message") or (body.get("detail") or {}) or r.text[:200]
            logger.error("Prisma STT failed (%s): %s", r.status_code, error)
            return Transcript("", ok=False, error=str(error), latency=time.time() - started)
        return Transcript(
            text=(body.get("transcript") or "").strip(),
            language=body.get("detected_language_code"),
            latency=time.time() - started,
        )
    except Exception as exc:
        logger.error("Prisma STT error: %s", exc)
        return Transcript("", ok=False, error=str(exc), latency=time.time() - started)


# ── TEXT-TO-SPEECH (Gnani Timbre, Edge-TTS fallback) ──────────────────────────
def _timbre(text: str, lang: str) -> Optional[Speech]:
    try:
        r = _post_with_retry(
            f"{settings.gnani_base_url}/api/v1/tts/inference",
            headers={"X-API-Key-ID": settings.gnani_tts_key, "Content-Type": "application/json"},
            json={
                "text": text, "model": settings.tts_model, "voice": settings.tts_voice,
                "language": TIMBRE_LANGUAGE.get(lang, "en-IN"), "speed": settings.tts_speed,
                "audio_config": {"sample_rate": 24000, "num_channels": 1, "sample_width": 2,
                                 "container": "mp3", "bitrate": "64k"},
            },
            timeout=30,
        )
        if r.status_code != 200 or not r.content or r.headers.get("content-type", "").startswith("application/json"):
            logger.error("Timbre TTS failed (%s): %s", r.status_code, r.text[:200])
            return None
        return Speech(base64.b64encode(r.content).decode(), "audio/mpeg")
    except Exception as exc:
        logger.error("Timbre TTS error: %s", exc)
        return None


async def _edge_async(text: str, voice: str) -> Optional[bytes]:
    from edge_tts import Communicate
    buf = bytearray()
    async for chunk in Communicate(text=text, voice=voice, rate="+5%").stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf) or None


def _edge(text: str, lang: str) -> Optional[Speech]:
    loop = asyncio.new_event_loop()
    try:
        audio = loop.run_until_complete(_edge_async(text, EDGE_VOICE.get(lang, EDGE_VOICE["en"])))
        return Speech(base64.b64encode(audio).decode(), "audio/mpeg") if audio else None
    except Exception as exc:
        logger.error("Edge-TTS error: %s", exc)
        return None
    finally:
        loop.close()


def synthesize(text: str, lang: str = "en") -> Optional[Speech]:
    """Spoken audio for a reply, or None. Timbre first, Edge-TTS as fallback."""
    if not settings.tts_enabled or not text:
        return None
    if settings.gnani_tts_enabled:
        speech = _timbre(text, lang)
        if speech:
            return speech
        logger.warning("Falling back to Edge-TTS")
    return _edge(text, lang)
