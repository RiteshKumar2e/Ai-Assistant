"""
agent/runtime.py — AgentService: assembles controllers, agents, supervisor and
runner once, and runs one task at a time in the background.

Front ends only talk to this class:
    voice  — actions/agent_task.py (Gemini Live tool) → submit() / status() / cancel()
    text   — `python -m agent "…"` → run()
A future front end (web chat, a different voice stack) is another caller of the
same three methods — nothing below knows which one is in use.
"""
from __future__ import annotations

import threading
from typing import Callable

from agent import config
from agent.agents.browser import BrowserAgent
from agent.agents.calendar import CalendarAgent
from agent.agents.coding import CodingAgent
from agent.agents.computer import ComputerAgent
from agent.agents.deployment import DeploymentAgent
from agent.agents.email import EmailAgent
from agent.agents.files import FileAgent
from agent.agents.general import GeneralAgent
from agent.agents.git import GitAgent
from agent.agents.github import GitHubAgent
from agent.agents.jobs import JobAgent
from agent.agents.messaging import MessagingAgent
from agent.agents.research import ResearchAgent
from agent.agents.supervisor import SupervisorAgent
from agent.agents.testing import TestingAgent
from agent.computer import ComputerController
from agent.events import EventBus
from agent.llm import LLM
from agent.providers.decision import DecisionProvider
from agent.orchestration.executor import TaskReport, TaskRunner
from agent.orchestration.permissions import ConfirmationBroker
from agent.orchestration.state import TaskContext

AGENTS = (GeneralAgent, BrowserAgent, ComputerAgent, CodingAgent, TestingAgent, GitAgent, GitHubAgent, ResearchAgent,
          FileAgent, MessagingAgent, EmailAgent, CalendarAgent, DeploymentAgent, JobAgent)


class AgentService:
    def __init__(self, settings: dict | None = None, llm=None, player=None, asker: Callable | None = None,
                 show_prompt: Callable | None = None, ctx: TaskContext | None = None, **overrides):
        self.settings = settings or config.load()
        self.ctx = ctx or TaskContext.load()
        self.llm = llm or LLM()
        self.events = EventBus()
        self.cc = ComputerController(self.settings, self.ctx, player, **overrides)
        self.cc.claude.llm = self.cc.claude.llm or self.llm
        services = {"events": self.events, "show_prompt": show_prompt}
        self.agents = {a.name: a for a in (cls(self.cc, self.llm, services) for cls in AGENTS)}
        if self.settings.get("mcp_servers"):
            from agent.integrations.mcp import load_mcp_agents
            self.agents.update({a.name: a for a in load_mcp_agents(self.cc, self.settings["mcp_servers"])})
        # Routing / next-action decisions may use a configured fast model; content always uses the main LLM.
        self.decider = DecisionProvider(self.llm, self.settings.get("decision_provider"))
        self.supervisor = SupervisorAgent(self.llm, self.agents, decider=self.decider)
        self.runner = TaskRunner(self.supervisor, self.cc, ConfirmationBroker(asker), self.events, self.settings, self.ctx)
        self._thread: threading.Thread | None = None
        self._request = ""
        self.last_report: TaskReport | None = None

    def run(self, request: str) -> TaskReport:
        self._request = request
        try:
            self.last_report = self.runner.run(request)
        except Exception as e:                      # the loop itself must never take the app down
            import traceback; traceback.print_exc()
            if self.ctx.task:
                self.ctx.task.set("failed"); self.ctx.task.errors.append(f"internal: {e}")
            self.last_report = TaskReport("failed", f"The task stopped because of an internal error: {e}")
            self.events.emit("error", self.last_report.summary, "SupervisorAgent", "failed")
        return self.last_report

    def busy(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def submit(self, request: str, on_done: Callable[[TaskReport], None] | None = None) -> bool:
        if self.busy():
            return False
        def work():
            rep = self.run(request)
            if on_done:
                on_done(rep)
        self._thread = threading.Thread(target=work, daemon=True, name="agent-task")
        self._thread.start()
        return True

    def cancel(self) -> str:
        if not self.busy():
            return "No task is running."
        self.cc.cancel_event.set()
        self.cc.terminal.stop()
        return f"Cancelling: {self._request[:80]}"

    def status(self) -> str:
        c = self.ctx
        lines = [f"Task {c.task.task_id if c.task else '-'} · status: {c.status}" + (f" — {c.goal[:120]}" if c.goal else "")]
        if c.current_agent:
            lines.append(f"Current agent: {c.current_agent}")
        if c.pending_confirmation:
            lines.append(f"Waiting for your confirmation: {c.pending_confirmation}")
        steps = [e.message for e in self.events.log if e.type == "tool_completed" and e.task_id == (c.task and c.task.task_id)][-6:]
        if steps:
            lines.append("Recent actions:\n" + "\n".join(steps))
        if (procs := self.cc.terminal.running()):
            lines.append("Background processes: " + ", ".join(procs))
        if not self.busy() and self.last_report:
            lines.append("Last result: " + self.last_report.text()[:800])
        lines.append(c.summary())
        return "\n".join(lines)
