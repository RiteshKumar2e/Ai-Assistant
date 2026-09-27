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


def _groq_api_keys() -> list[str]:
    """Every configured Groq key, in fallback order: the plural
    `groq_api_keys` list, then the single `groq_api_key` — each once."""
    cfg = _load_config()
    many = cfg.get("groq_api_keys")
    keys = [k for k in (many if isinstance(many, list) else []) if isinstance(k, str)]
    keys.append(cfg.get("groq_api_key") or "")
    return list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))


def _groq_api_key() -> str:
    keys = _groq_api_keys()
    return keys[0] if keys else ""


# (key, model) -> monotonic time its rate-limit window reopens. A key+model
# that just hit 429 is skipped until then instead of re-hit on every call.
_groq_paused: dict[tuple[str, str], float] = {}
_GROQ_PAUSE_SECS = 60


def _gemini_api_key() -> str:
    keys = _gemini_api_keys()
    return keys[0] if keys else ""


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
    """Model-first, key-second: the best model is tried on EVERY key before
    dropping to a weaker model, so one key's quota running out just moves the
    call to the next key. A rejected key is skipped for the rest of the call,
    a rate-limited key+model rests for a minute."""
    import time
    keys = _groq_api_keys()
    if not keys:
        raise RuntimeError("No Groq API key configured.")
    cfg   = _load_config()
    chain = _model_chain(cfg, "groq_models", "groq_model", preferred, _load_models("groq", _EMERGENCY_GROQ))

    rejected: set[str] = set()
    last_err: Exception | None = None
    for model in chain:
        for n, key in enumerate(keys, 1):
            if key in rejected or _groq_paused.get((key, model), 0) > time.monotonic():
                continue
            try:
                resp = requests.post(
                    _GROQ_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": model, "messages": [{"role": "user", "content": prompt}]},
                    timeout=60,
                )
            except Exception as e:   # network trouble — the next key won't fare better
                last_err = e
                print(f"[TextModel] Groq {model} failed ({e}) — trying next model")
                break
            if resp.status_code in (401, 403):
                rejected.add(key)
                last_err = RuntimeError(f"Groq key #{n} rejected — check config/api_keys.json.")
                print(f"[TextModel] {last_err}")
                continue
            if resp.status_code == 429:
                _groq_paused[(key, model)] = time.monotonic() + _GROQ_PAUSE_SECS
                last_err = RuntimeError(f"Groq key #{n} rate-limited on {model}")
                print(f"[TextModel] {last_err} — trying next key")
                continue
            if resp.status_code == 404:   # model retired: same on every key
                last_err = RuntimeError(f"Groq model {model} not found")
                break
            try:
                resp.raise_for_status()
                return (resp.json()["choices"][0]["message"]["content"] or "").strip()
            except Exception as e:
                last_err = e
                print(f"[TextModel] Groq {model} failed ({e}) — trying next")
                break
        if len(rejected) == len(keys):
            break
    raise last_err or RuntimeError("No Groq models configured.")


def _gemini_api_keys() -> list[str]:
    """Every configured Gemini key, in fallback order: the plural
    `gemini_api_keys` list, then the single `gemini_api_key` — each once."""
    cfg = _load_config()
    many = cfg.get("gemini_api_keys")
    keys = [k for k in (many if isinstance(many, list) else []) if isinstance(k, str)]
    keys.append(cfg.get("gemini_api_key") or "")
    return list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))


_gemini_paused: dict[tuple[str, str], float] = {}   # (key, model) resting after a 429


def _gemini_is_quota_error(e: Exception) -> bool:
    msg = str(getattr(e, "message", "") or e)
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg


def _gemini_over_keys(call, chain: list[str], keys: list[str], what: str) -> str:
    """Model-first, key-second, like the Groq chain: the preferred model is
    tried on EVERY key before a weaker model. A rejected key is skipped for
    the rest of the call; a key+model that hit its quota rests a minute."""
    import time
    from google import genai
    if not keys:
        raise RuntimeError("No Gemini API key configured.")
    clients: dict[str, object] = {}
    rejected: set[str] = set()
    last_err: Exception | None = None
    for model in chain:
        for n, key in enumerate(keys, 1):
            if key in rejected or _gemini_paused.get((key, model), 0) > time.monotonic():
                continue
            try:
                client = clients.setdefault(key, genai.Client(api_key=key))
                return call(client, model)
            except Exception as e:
                last_err = e
                if _gemini_is_auth_error(e):
                    rejected.add(key)
                    print(f"[TextModel] Gemini key #{n} rejected — skipping it")
                    continue
                if _gemini_is_quota_error(e):
                    _gemini_paused[(key, model)] = time.monotonic() + 60
                    print(f"[TextModel] Gemini key #{n} out of quota on {model} — trying next key")
                    continue
                print(f"[TextModel] Gemini {model}{what} failed ({str(e)[:120]}) — trying next model")
                break
        if len(rejected) == len(keys):
            break
    raise last_err or RuntimeError("No Gemini models configured.")


def _gemini_generate(contents, preferred: str | None, only: list[str] | None = None,
                     keys: list[str] | None = None) -> str:
    cfg   = _load_config()
    chain = only or _model_chain(cfg, "gemini_models", "gemini_model", preferred, _load_models("gemini", _EMERGENCY_GEMINI))

    def call(client, model):
        return (client.models.generate_content(model=model, contents=contents).text or "").strip()

    return _gemini_over_keys(call, chain, keys or _gemini_api_keys(), "")


def generate_grounded_search(prompt: str, preferred: str | None = None) -> str:
    """Gemini-only grounded (google_search tool) generation — Groq has no
    web-grounding equivalent, so this always uses the Gemini chain. Callers
    (web_search.py) already fall back to DuckDuckGo if every model fails."""
    cfg   = _load_config()
    chain = _model_chain(cfg, "gemini_models", "gemini_model", preferred, _load_models("gemini", _EMERGENCY_GEMINI))

    def call(client, model):
        response = client.models.generate_content(
            model=model, contents=prompt,
            config={"tools": [{"google_search": {}}], "automatic_function_calling": {"disable": True}},
        )
        text = "".join(part.text for part in response.candidates[0].content.parts
                       if getattr(part, "text", None)).strip()
        if not text:
            raise ValueError("empty response")
        return text

    return _gemini_over_keys(call, chain, _gemini_api_keys(), " (grounded)")


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


_bulk_turn = 0
_bulk_lock = __import__("threading").Lock()


def generate_bulk(prompt: str, models: list[str] | None = None) -> str:
    """For bulk background jobs (core/routing_trainer): only the Gemma models
    (14,400 req/day each). Never falls through to the Flash-tier or Groq
    models live actions depend on — when Gemma is rate-limited this raises
    and the caller backs off instead of eating those daily quotas."""
    gemma = models or [m for m in _load_models("gemini", _EMERGENCY_GEMINI) if m.startswith("gemma")]
    if not gemma:
        raise RuntimeError("No Gemma model configured for bulk jobs.")
    keys = _gemini_api_keys()
    # Start each call on the next key, so parallel workers spread over every
    # key's per-minute token limit instead of all queuing on the first one.
    global _bulk_turn
    with _bulk_lock:
        start, _bulk_turn = _bulk_turn, _bulk_turn + 1
    return _gemini_generate(prompt, None, only=gemma, keys=keys[start % max(1, len(keys)):] + keys[:start % max(1, len(keys))])


def get_text_model(gemini_model: str | None = None, groq_model: str | None = None) -> TextModel:
    return TextModel(gemini_model, groq_model)
