"""
agent/events.py — the live task stream, decoupled from whoever is listening.

Event types (safe to show or send anywhere — no model reasoning, only what
happened; tool args are redacted and truncated):

    task_started · agent_started · agent_status · agent_completed
    tool_started · tool_completed · confirmation_required
    clarification_needed · error · task_completed

JUDO's HUD logs every event and *speaks* only those worth interrupting for —
start, a milestone status, an important error, a confirmation, a question, the
result — rate-limited so a 20-step task doesn't narrate itself. The phone
dashboard receives them as JSON over its websocket.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from agent.tools.base import redact

TYPES = ("task_started", "agent_started", "agent_status", "agent_completed", "tool_started", "tool_completed",
         "confirmation_required", "clarification_needed", "error", "task_completed")
SPOKEN = {"task_started", "agent_status", "error", "confirmation_required", "clarification_needed", "task_completed"}


@dataclass
class Event:
    type: str
    message: str
    agent: str = ""
    status: str = ""
    task_id: str = ""
    data: dict = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


def _safe(v):
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(v)
    s = redact(s)
    return json.loads(s) if len(s) <= 600 else s[:600] + "…"


class EventBus:
    def __init__(self):
        self._subs: list[Callable[[Event], None]] = []
        self.log: list[Event] = []
        self.task_id = ""

    def subscribe(self, fn: Callable[[Event], None]) -> None:
        self._subs.append(fn)

    def emit(self, type: str, message: str, agent: str = "", status: str = "", **data) -> Event:
        assert type in TYPES, type
        ev = Event(type, redact(str(message))[:600], agent, status, self.task_id, {k: _safe(v) for k, v in data.items()})
        self.log.append(ev)
        del self.log[:-500]
        for fn in list(self._subs):
            try:
                fn(ev)
            except Exception as e:
                print(f"[Agent] event subscriber failed: {e}")
        return ev


class SpeechThrottle:
    """Which events get spoken. Errors, confirmations, questions and the final
    report always; task start and milestones at most once per `gap` seconds."""

    def __init__(self, gap: float = 20.0):
        self.gap, self._last = gap, -1e9

    def should_speak(self, ev: Event) -> bool:
        if ev.type not in SPOKEN:
            return False
        if ev.type in ("agent_status", "task_started") and time.monotonic() - self._last < self.gap:
            return False
        self._last = time.monotonic()
        return True
