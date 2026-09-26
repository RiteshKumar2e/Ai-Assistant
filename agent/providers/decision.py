"""
agent/providers/decision.py — optional fast model for routing / next-action decisions.

Planning and "which tool next" are short structured-JSON decisions; a small,
fast model can make them cheaper and quicker than the main chain. Configure any
OpenAI-compatible chat endpoint (a hosted fast model, a local Ollama/vLLM
server, …) in config/agent.json:

    "decision_provider": {"base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b"}

An API key, if the endpoint needs one, goes in config/api_keys.json → decision_api_key.
Not configured, unreachable, or returning non-JSON → the call falls through to
the main LLM (FallbackLLM). Content generation (summaries, commit messages,
Claude Code prompts) always uses the main LLM.
"""
from __future__ import annotations


class OpenAICompatible:
    name = "decision"

    def __init__(self, base_url: str, model: str, timeout: int = 30):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.timeout = model, timeout

    def available(self) -> bool:
        return bool(self.url and self.model)

    def complete(self, prompt: str) -> str:
        import requests
        from agent.credentials import secret
        key = secret("decision_api_key")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        r = requests.post(self.url, headers=headers, timeout=self.timeout, json={
            "model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"] or ""


class DecisionProvider:
    """complete() for structured decisions: configured fast model first, main LLM fallback."""

    def __init__(self, main, cfg: dict | None = None):
        self.main = main
        cfg = cfg or {}
        self.fast = OpenAICompatible(cfg["base_url"], cfg["model"]) \
            if cfg.get("base_url") and cfg.get("model") else None
        self._fast_failures = 0

    def complete(self, prompt: str) -> str:
        if self.fast and self._fast_failures < 3:
            try:
                out = self.fast.complete(prompt)
                if "{" in out:
                    self._fast_failures = 0
                    return out
            except Exception as e:
                print(f"[Agent] decision model failed ({str(e)[:100]}) — using main LLM")
            self._fast_failures += 1          # 3 strikes → stop trying it this session
        return self.main.complete(prompt)
