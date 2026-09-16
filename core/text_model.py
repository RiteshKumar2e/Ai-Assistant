"""
core/text_model.py — Shared one-shot text-generation adapter used by the
non-Live actions (code_helper, dev_agent, file_processor, flight_finder,
youtube_video, desktop) that each used to build their own Gemini client
inline. Text-only prompts now try Groq first (huge free-tier quota, very
fast) and fall back to Gemini on any failure — the Gemini Live *voice*
session in main.py is a completely separate system and is unaffected either
way, and so is web_search.py's grounded google_search (Groq has no
equivalent web-grounding tool, so that stays Gemini-only).

Multimodal contents (images, audio bytes) always go straight to Gemini —
Groq's chat-completions endpoint wrapped here is text-only.

Configure by adding to config/api_keys.json:
    "groq_api_key": "gsk_...",
    "groq_model":   "openai/gpt-oss-120b"   (optional, this is the default)

Groq retires models on a rolling basis and a retired name returns 404 here,
which silently sends every text action down the slow Gemini path. If that
starts happening, list what the key can actually reach —
GET https://api.groq.com/openai/v1/models — and update the name.
Leaving groq_api_key unset just means every call goes straight to Gemini,
exactly like before this module existed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_CONFIG_PATH = _base_dir() / "config" / "api_keys.json"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _groq_api_key() -> str:
    return (_load_config().get("groq_api_key") or "").strip()


def _groq_model() -> str:
    return (_load_config().get("groq_model") or "").strip() or _DEFAULT_GROQ_MODEL


def _gemini_api_key() -> str:
    return (_load_config().get("gemini_api_key") or "").strip()


def _is_text_only(contents) -> bool:
    if isinstance(contents, str):
        return True
    if isinstance(contents, (list, tuple)):
        return all(isinstance(c, str) for c in contents)
    return False


def _flatten_text(contents) -> str:
    return contents if isinstance(contents, str) else "\n\n".join(contents)


def _groq_generate(prompt: str, model: str) -> str:
    key = _groq_api_key()
    if not key:
        raise RuntimeError("No Groq API key configured.")
    resp = requests.post(
        _GROQ_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )
    resp.raise_for_status()
    return (resp.json()["choices"][0]["message"]["content"] or "").strip()


def _gemini_generate(contents, model: str) -> str:
    from google import genai
    key = _gemini_api_key()
    if not key:
        raise RuntimeError("No Gemini API key configured.")
    client = genai.Client(api_key=key)
    response = client.models.generate_content(model=model, contents=contents)
    return (response.text or "").strip()


class _Response:
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


class TextModel:
    """Drop-in replacement for the `_W`-style local wrapper each action used
    to define inline: `.generate_content(contents).text`. Same shape, so
    existing call sites (`model.generate_content(prompt).text`) don't change."""

    def __init__(self, gemini_model: str = "gemini-flash-latest", groq_model: str | None = None):
        self._gemini_model = gemini_model
        self._groq_model = groq_model or _groq_model()

    def generate_content(self, contents) -> _Response:
        if _is_text_only(contents) and _groq_api_key():
            try:
                return _Response(_groq_generate(_flatten_text(contents), self._groq_model))
            except Exception as e:
                print(f"[TextModel] Groq failed ({e}) — falling back to Gemini")
        return _Response(_gemini_generate(contents, self._gemini_model))


def get_text_model(gemini_model: str = "gemini-flash-latest", groq_model: str | None = None) -> TextModel:
    return TextModel(gemini_model, groq_model)
