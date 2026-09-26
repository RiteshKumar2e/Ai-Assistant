"""
agent/credentials.py — integration credentials, from config/api_keys.json.

The same gitignored file JUDO already keeps its Gemini/Groq keys in; the agent
adds optional keys next to them (see config/api_keys.example.json):

    telegram_bot_token · slack_bot_token · discord_webhooks {name: url}
    whatsapp_token · whatsapp_phone_number_id · outlook_client_id
    vercel_token · render_api_key · decision_api_key

Read only by the provider that needs them — never put in a prompt, an event or
a tool result. A missing key just means that integration reports "not connected".
"""
from __future__ import annotations

import json

from agent.config import BASE_DIR

API_KEYS = BASE_DIR / "config" / "api_keys.json"
_cache: tuple[float, dict] = (-1.0, {})


def _load() -> dict:
    global _cache
    try:
        mtime = API_KEYS.stat().st_mtime
    except OSError:
        return {}
    if mtime != _cache[0]:
        try:
            data = json.loads(API_KEYS.read_text(encoding="utf-8"))
            _cache = (mtime, data if isinstance(data, dict) else {})
        except (OSError, json.JSONDecodeError):
            _cache = (mtime, {})
    return _cache[1]


def secret(key: str) -> str:
    v = _load().get(key, "")
    return v.strip() if isinstance(v, str) else ""


def secret_map(key: str) -> dict:
    v = _load().get(key, {})
    return {str(k): str(u) for k, u in v.items()} if isinstance(v, dict) else {}
