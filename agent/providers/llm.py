"""
agent/providers/llm.py — LLMProvider interface over JUDO's existing model chains.

    LLMProvider
    ├── GroqProvider      core/text_model._groq_generate   (models.json "groq" chain)
    ├── GeminiProvider    core/text_model._gemini_generate (models.json "gemini" chain)
    └── FallbackLLM       tries providers in order — Groq → Gemini, exactly the
                          behaviour core/text_model.TextModel already has

Agents receive "an object with .complete(prompt)", never a vendor client, so a
new provider is one class with `name`, `available()` and `complete()`.
Keys stay in config/api_keys.json and are read by core/text_model only.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    name = ""

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def complete(self, prompt: str) -> str: ...


class GroqProvider(LLMProvider):
    name = "groq"

    def available(self) -> bool:
        from core.text_model import _groq_api_key
        return bool(_groq_api_key())

    def complete(self, prompt: str) -> str:
        from core.text_model import _groq_generate
        return _groq_generate(prompt, None)


class GeminiProvider(LLMProvider):
    name = "gemini"

    def available(self) -> bool:
        from core.text_model import _gemini_api_key
        return bool(_gemini_api_key())

    def complete(self, prompt: str) -> str:
        from core.text_model import _gemini_generate
        return _gemini_generate(prompt, None)


class FallbackLLM:
    """First available provider that answers wins; the error of the last one is
    raised only if all of them fail."""

    def __init__(self, providers: list[LLMProvider] | None = None):
        self.providers = providers if providers is not None else [GroqProvider(), GeminiProvider()]
        self.last_provider = ""

    def complete(self, prompt: str) -> str:
        last: Exception | None = None
        for p in self.providers:
            try:
                if not p.available():
                    continue
                out = p.complete(prompt)
                if not (out or "").strip():          # an empty completion is a failure, not an answer
                    raise RuntimeError("empty reply")
                self.last_provider = p.name
                return out
            except Exception as e:
                last = e
                print(f"[Agent] {p.name} failed ({str(e)[:120]}) — falling back")
        raise last or RuntimeError("No LLM provider is configured (add groq_api_key or gemini_api_key to config/api_keys.json).")
