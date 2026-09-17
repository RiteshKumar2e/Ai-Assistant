"""
core/text_model.py — Shared one-shot text-generation adapter used by the
non-Live actions (code_helper, dev_agent, file_processor, flight_finder,
youtube_video, desktop) that each used to build their own Gemini client
inline. Text-only prompts try Groq first (huge free-tier quota, very fast),
falling through a chain of models on BOTH providers before giving up — the
Gemini Live *voice* session in main.py is a completely separate system and
is unaffected either way, and so is web_search.py's grounded google_search
(Groq has no equivalent web-grounding tool, so that stays Gemini-only).

WHY A CHAIN, NOT ONE MODEL
    A single hardcoded model is one quota away from taking down every text
    action at once — this happened twice already: Groq retired
    "llama-3.3-70b-versatile" outright (404 on every call), and Gemini's
    "gemini-flash-latest" alias currently points at a model sitting at
    19/20 requests-per-day on the free tier (see the account's rate-limit
    dashboard). Each provider therefore gets an ordered list of real,
    verified-working models; a failure on one (retired, rate-limited,
    temporarily overloaded) just advances to the next.

    Order matters: models are listed highest-quota / least-loaded first, so
    the chain naturally avoids whichever model the rest of the app has
    already been hammering.

Multimodal contents (images, audio bytes) always go straight to Gemini —
Groq's chat-completions endpoint wrapped here is text-only.

The model chains themselves — which models, in what order, and why — live in
config/models.json, not in this file. Edit that file to change the chain;
its "_readme" and per-model "note" fields explain the ordering.

Configure by adding to config/api_keys.json:
    "groq_api_key":  "gsk_...",
    "groq_models":   ["model-a", "model-b", ...]   (optional, overrides models.json)
    "gemini_models": ["model-a", "model-b", ...]   (optional, overrides models.json)
    "groq_model" / "gemini_model" (singular, optional) are still honoured —
    each is tried first, ahead of its provider's chain.

Leaving groq_api_key unset just means every call goes straight to the Gemini
chain, exactly like before Groq support existed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Used only if config/models.json is missing, unreadable, or empty for a
# provider — models.json is the real source of truth and documents WHY each
# model is in its position. This is just enough to keep the app alive.
_EMERGENCY_GROQ   = ["openai/gpt-oss-120b"]
_EMERGENCY_GEMINI = ["gemini-3.1-flash-lite", "gemini-flash-latest"]


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_CONFIG_PATH = _base_dir() / "config" / "api_keys.json"
_MODELS_PATH = _base_dir() / "config" / "models.json"


def _load_config() -> dict:
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_models(provider: str, emergency: list[str]) -> list[str]:
    try:
        data = json.loads(_MODELS_PATH.read_text(encoding="utf-8"))
        ids  = [m["id"] for m in data.get(provider, []) if isinstance(m, dict) and m.get("id")]
        return ids or emergency
    except Exception as e:
        print(f"[TextModel] Could not read models.json ({e}) — using emergency {provider} list")
        return emergency


def _model_chain(cfg: dict, list_key: str, single_key: str, preferred: str | None, default: list[str]) -> list[str]:
    """Build one provider's try-order: an explicit call-site override first,
    then the singular config key, then the configured (or default) list —
    each appearing once, in that priority order."""
    custom = cfg.get(list_key)
    base = custom if isinstance(custom, list) and custom else default
    ordered: list[str] = []
    for m in [preferred, (cfg.get(single_key) or "").strip(), *base]:
        m = (m or "").strip()
        if m and m not in ordered:
            ordered.append(m)
    return ordered


def _groq_api_key() -> str:
    return (_load_config().get("groq_api_key") or "").strip()


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


def _gemini_is_auth_error(e: Exception) -> bool:
    """A rejected Gemini key returns 400 INVALID_ARGUMENT (not 401/403), so the
    HTTP code alone can't distinguish it from an ordinary bad request — match
    on the SDK's own message text instead."""
    msg = str(getattr(e, "message", "") or e).lower()
    return "api key not valid" in msg or "api_key_invalid" in msg


def _groq_generate(prompt: str, preferred: str | None) -> str:
    key = _groq_api_key()
    if not key:
        raise RuntimeError("No Groq API key configured.")
    cfg   = _load_config()
    chain = _model_chain(cfg, "groq_models", "groq_model", preferred, _load_models("groq", _EMERGENCY_GROQ))

    last_err: Exception | None = None
    for model in chain:
        try:
            resp = requests.post(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}]},
                timeout=60,
            )
            # A rejected key fails identically for every model in the chain —
            # detected once, there is no point burning the rest of it on the
            # same 401/403. Anything else (404 retired, 429 quota, 5xx, a
            # network timeout) is exactly the kind of per-model failure the
            # chain exists to route around, so it just moves on.
            if resp.status_code in (401, 403):
                raise RuntimeError("Groq API key rejected — check config/api_keys.json's groq_api_key.")
            resp.raise_for_status()
            return (resp.json()["choices"][0]["message"]["content"] or "").strip()
        except RuntimeError as e:
            last_err = e
            print(f"[TextModel] {e} — skipping remaining Groq models")
            break
        except Exception as e:
            last_err = e
            print(f"[TextModel] Groq {model} failed ({e}) — trying next")
    raise last_err or RuntimeError("No Groq models configured.")


def _gemini_generate(contents, preferred: str | None) -> str:
    from google import genai
    key = _gemini_api_key()
    if not key:
        raise RuntimeError("No Gemini API key configured.")
    cfg    = _load_config()
    chain  = _model_chain(cfg, "gemini_models", "gemini_model", preferred, _load_models("gemini", _EMERGENCY_GEMINI))
    client = genai.Client(api_key=key)

    last_err: Exception | None = None
    for model in chain:
        try:
            response = client.models.generate_content(model=model, contents=contents)
            return (response.text or "").strip()
        except Exception as e:
            last_err = e
            if _gemini_is_auth_error(e):
                print("[TextModel] Gemini API key rejected — skipping remaining Gemini models")
                break
            print(f"[TextModel] Gemini {model} failed ({e}) — trying next")
    raise last_err or RuntimeError("No Gemini models configured.")


def generate_grounded_search(prompt: str, preferred: str | None = None) -> str:
    """Gemini-only grounded (google_search tool) generation — Groq has no
    web-grounding equivalent, so this always uses the Gemini chain. Callers
    (web_search.py) already fall back to DuckDuckGo if every model fails."""
    from google import genai
    key = _gemini_api_key()
    if not key:
        raise RuntimeError("No Gemini API key configured.")
    cfg    = _load_config()
    chain  = _model_chain(cfg, "gemini_models", "gemini_model", preferred, _load_models("gemini", _EMERGENCY_GEMINI))
    client = genai.Client(api_key=key)

    last_err: Exception | None = None
    for model in chain:
        try:
            response = client.models.generate_content(
                model=model, contents=prompt,
                config={"tools": [{"google_search": {}}], "automatic_function_calling": {"disable": True}},
            )
            text = "".join(
                part.text for part in response.candidates[0].content.parts if getattr(part, "text", None)
            ).strip()
            if not text:
                raise ValueError("empty response")
            return text
        except Exception as e:
            last_err = e
            if _gemini_is_auth_error(e):
                print("[TextModel] Gemini API key rejected — skipping remaining Gemini models")
                break
            print(f"[TextModel] Gemini {model} (grounded) failed ({e}) — trying next")
    raise last_err or RuntimeError("No Gemini models configured.")


class _Response:
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


class TextModel:
    """Drop-in replacement for the `_W`-style local wrapper each action used
    to define inline: `.generate_content(contents).text`. Same shape, so
    existing call sites (`model.generate_content(prompt).text`) don't change."""

    def __init__(self, gemini_model: str | None = None, groq_model: str | None = None):
        self._gemini_model = gemini_model
        self._groq_model   = groq_model

    def generate_content(self, contents) -> _Response:
        if _is_text_only(contents) and _groq_api_key():
            try:
                return _Response(_groq_generate(_flatten_text(contents), self._groq_model))
            except Exception as e:
                print(f"[TextModel] Groq chain exhausted ({e}) — falling back to Gemini")
        return _Response(_gemini_generate(contents, self._gemini_model))


def get_text_model(gemini_model: str | None = None, groq_model: str | None = None) -> TextModel:
    return TextModel(gemini_model, groq_model)
