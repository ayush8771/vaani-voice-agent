"""
llm_client.py — one chat client for every LLM call Vaani makes.

Two wire formats, one interface:
  * Ollama (base URL on port 11434): native /api/chat, which supports tools and
    `keep_alive` so the model stays loaded between calls.
  * Anything else (Sarvam, OpenAI-compatible servers): /chat/completions.

chat() always returns a normalised message, or None on failure:
  {"content": str, "tool_calls": [{"id": str, "name": str, "arguments": dict}]}
"""

import json
import logging
import time
import uuid
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)

KEEP_ALIVE = "30m"
RETRIES = 2


def _is_ollama() -> bool:
    return ":11434" in settings.llm_base_url


def _ollama_root() -> str:
    base = settings.llm_base_url
    return base[: -len("/v1")] if base.endswith("/v1") else base


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _to_wire(messages: list) -> list:
    """Convert our normalised history into the provider's message format."""
    wire = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            calls = m["tool_calls"]
            if _is_ollama():
                wire.append({"role": "assistant", "content": m.get("content") or "",
                             "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}}
                                            for c in calls]})
            else:
                wire.append({"role": "assistant", "content": m.get("content") or None,
                             "tool_calls": [{"id": c["id"], "type": "function",
                                             "function": {"name": c["name"],
                                                          "arguments": json.dumps(c["arguments"])}}
                                            for c in calls]})
        elif m["role"] == "tool":
            if _is_ollama():
                wire.append({"role": "tool", "content": m["content"], "tool_name": m.get("name", "")})
            else:
                wire.append({"role": "tool", "content": m["content"], "tool_call_id": m["tool_call_id"]})
        else:
            wire.append({"role": m["role"], "content": m.get("content") or ""})
    return wire


def chat(messages: list, tools: Optional[list] = None, max_tokens: int = 300,
         temperature: float = 0.0) -> Optional[dict]:
    if _is_ollama():
        url = f"{_ollama_root()}/api/chat"
        body = {"model": settings.llm_model, "messages": _to_wire(messages), "stream": False,
                "keep_alive": KEEP_ALIVE,
                "options": {"temperature": temperature, "num_predict": max_tokens}}
    else:
        url = f"{settings.llm_base_url}/chat/completions"
        body = {"model": settings.llm_model, "messages": _to_wire(messages),
                "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}

    for attempt in range(1, RETRIES + 1):
        started = time.time()
        try:
            r = requests.post(url, json=body, headers=headers, timeout=settings.llm_timeout)
            r.raise_for_status()
            data = r.json()
            msg = data.get("message") if _is_ollama() else data["choices"][0]["message"]
            calls = []
            for c in msg.get("tool_calls") or []:
                fn = c.get("function", {})
                calls.append({"id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                              "name": fn.get("name", ""), "arguments": _parse_args(fn.get("arguments"))})
            logger.debug("LLM %.1fs (%d tool calls)", time.time() - started, len(calls))
            return {"content": (msg.get("content") or "").strip(), "tool_calls": calls}
        except requests.Timeout:
            logger.warning("LLM timeout after %ss (attempt %d/%d)", settings.llm_timeout, attempt, RETRIES)
        except requests.HTTPError as exc:
            logger.error("LLM HTTP error: %s", exc)
            return None
        except Exception as exc:
            logger.error("LLM error (attempt %d/%d): %s", attempt, RETRIES, exc)
    return None


def complete(prompt: str, system: str, max_tokens: int = 250) -> Optional[str]:
    """Single-turn text completion. None on failure."""
    msg = chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}],
               max_tokens=max_tokens)
    return msg["content"] if msg else None
