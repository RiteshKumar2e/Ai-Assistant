"""
agent/llm.py — JSON helpers for model replies, and the default LLM.

The providers themselves live in agent/providers/ (Groq → Gemini fallback over
JUDO's existing keys). Anything with `.complete(prompt) -> str` can stand in —
tests inject a scripted fake.
"""
from __future__ import annotations

import json
import re

from agent.providers.llm import FallbackLLM as LLM  # noqa: F401  (re-export: the default provider chain)


def parse_json(text: str) -> dict:
    """First JSON object in a model reply — tolerates ```json fences, prose around
    it, and trailing commas. Raises ValueError if there is none."""
    t = re.sub(r"```(?:json)?", "", text or "").strip()
    start = t.find("{")
    if start < 0:
        raise ValueError(f"no JSON object in reply: {t[:200]!r}")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            elif ch != "\\":
                esc = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = t[start:i + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", blob))
    raise ValueError("unterminated JSON object in reply")


def ask_json(llm, prompt: str, retries: int = 1) -> dict:
    last: Exception | None = None
    for attempt in range(retries + 1):
        reply = llm.complete(prompt if attempt == 0 else prompt + "\n\nYour previous reply was not valid JSON. Reply with ONE JSON object only.")
        try:
            return parse_json(reply)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
    raise ValueError(f"model did not return valid JSON: {last}")
