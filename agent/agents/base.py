"""
agent/agents/base.py — what an agent is: a name, a one-line remit the
supervisor routes on, and the tools it owns. `requires` pulls in another
agent's tools when this one is chosen (coding needs file access), so the
supervisor never has to list both.
"""
from __future__ import annotations

from pathlib import Path

from agent.tools.base import Tool, ToolResult


class Agent:
    name = ""
    description = ""
    requires: tuple[str, ...] = ()

    def __init__(self, cc, llm=None, services=None):
        self.cc, self.llm, self.sv = cc, llm, services or {}

    def tools(self) -> list[Tool]:
        return []

    def tool(self, name, description, params, fn, risk="read", **kw) -> Tool:
        return Tool(name, description, params, fn, risk, self.name, **kw)

    def need_project(self) -> ToolResult | None:
        if not self.cc.is_project_set():
            return ToolResult.fail("No project is selected yet — call open_project first (or ask the user which project).")
        return None

    def project(self) -> str:
        return self.cc.project_dir()

    def emit(self, text: str, error: bool = False) -> None:
        """A milestone worth telling the user about (spoken, rate-limited)."""
        if (bus := self.sv.get("events")):
            bus.emit("error" if error else "agent_status", text, self.name, "running")


def int_arg(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def list_arg(v) -> list[str]:
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [s.strip() for s in str(v or "").split(",") if s.strip()]


def rel(p: str, root: str) -> str:
    try:
        return str(Path(p).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return p
