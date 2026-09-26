"""
agent/orchestration/state.py — short-term task memory.

One TaskContext lives for the whole app session and is shared by every task,
which is what lets "open my project" → "check the login" → "fix it" →
"push it" work: each later request is planned with the project, branch, last
test run, last error and files touched by the earlier ones. The project and the
last URL are also persisted (memory/agent/state.json) so a restart still knows
which project "the project" is; everything else is deliberately RAM-only.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from agent.config import DATA_DIR

_STATE_FILE = DATA_DIR / "state.json"


@dataclass
class TestRun:
    command: str
    ok: bool
    summary: str
    at: float = field(default_factory=time.time)
    head: str = ""          # git HEAD + dirty-hash when it ran — lets push know if tests still apply


STATUSES = ("pending", "planning", "running", "waiting_confirmation", "waiting_input", "completed", "failed", "cancelled")
FINAL = ("completed", "failed", "cancelled", "waiting_input")


@dataclass
class Task:
    """One user request being worked on. `tool_calls` holds every call with its
    real outcome; `artifacts` the things it produced (files, commits, URLs, prompts)."""
    goal: str
    task_id: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S-") + __import__("uuid").uuid4().hex[:4])
    status: str = "pending"
    current_agent: str = ""
    agents: list = field(default_factory=list)
    steps: list = field(default_factory=list)          # planned steps (strings)
    tool_calls: list = field(default_factory=list)     # {agent, tool, args, ok, status, summary, at}
    artifacts: list = field(default_factory=list)      # {kind, value}
    errors: list = field(default_factory=list)
    requires_confirmation: bool = False
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    def set(self, status: str) -> None:
        assert status in STATUSES, status
        self.status = status
        if status in FINAL:
            self.finished = time.time()

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("task_id", "goal", "status", "current_agent", "agents", "steps",
                                               "tool_calls", "artifacts", "errors", "requires_confirmation",
                                               "started", "finished")}


@dataclass
class TaskContext:
    project: str = ""
    project_name: str = ""
    branch: str = ""
    remote: str = ""
    last_url: str = ""
    task: Task | None = None             # the active (or most recent) task
    tasks: deque = field(default_factory=lambda: deque(maxlen=20))
    current_agent: str = ""
    files_modified: set = field(default_factory=set)
    last_test: TestRun | None = None
    last_error: str = ""
    last_claude_prompt: str = ""
    attached_file: str = ""              # file the user dropped on the HUD ("this PDF")
    pending_confirmation: str = ""
    history: deque = field(default_factory=lambda: deque(maxlen=12))    # (request, outcome) of past tasks
    recent_results: deque = field(default_factory=lambda: deque(maxlen=10))
    state_file: Path = _STATE_FILE
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── persistence (project + url only) ─────────────────────────────────────
    @classmethod
    def load(cls, path: Path = _STATE_FILE) -> "TaskContext":
        ctx = cls(state_file=path)
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("project") and Path(d["project"]).is_dir():
                ctx.project, ctx.project_name = d["project"], d.get("project_name", Path(d["project"]).name)
            ctx.last_url = d.get("last_url", "")
        except Exception:
            pass
        return ctx

    def save(self, path: Path | None = None) -> None:
        path = path or self.state_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"project": self.project, "project_name": self.project_name,
                                        "last_url": self.last_url}, indent=2), encoding="utf-8")
        except OSError:
            pass

    def set_project(self, path: str, name: str = "", branch: str = "", remote: str = "") -> None:
        with self._lock:
            if path != self.project:
                self.files_modified.clear(); self.last_test = None; self.last_error = ""
            self.project, self.project_name = path, name or Path(path).name
            self.branch, self.remote = branch, remote
        self.save()

    @property
    def status(self) -> str:
        return self.task.status if self.task else "idle"

    @property
    def goal(self) -> str:
        return self.task.goal if self.task else ""

    def new_task(self, goal: str) -> Task:
        with self._lock:
            self.task = Task(goal)
            self.tasks.append(self.task)
        return self.task

    def note(self, tool: str, result) -> None:
        with self._lock:
            self.recent_results.append(f"{tool}: {'ok' if result.ok else result.status} — {result.output[:160]}")
            if (m := result.data.get("modified")):
                self.files_modified.add(m)
            if (u := result.data.get("url")) and tool in ("open_url", "fetch_url"):
                self.last_url = u
            if (b := result.data.get("branch")) and tool.startswith("git"):
                self.branch = b
            if not result.ok and result.status == "error":
                self.last_error = f"{tool}: {result.output[:1500]}"

    def summary(self) -> str:
        """Compact context block for the planner — this is how 'it' gets resolved."""
        lines = [f"Now: {time.strftime('%A %Y-%m-%d %H:%M')} (local time)"]
        if self.project:
            lines.append(f"Current project: {self.project_name} at {self.project}"
                         + (f" (branch {self.branch})" if self.branch else "") + (f", remote {self.remote}" if self.remote else ""))
        else:
            lines.append("Current project: none selected yet")
        if self.last_url:
            lines.append(f"Last website: {self.last_url}")
        if self.attached_file:
            lines.append(f"Attached file (\"this file/PDF/document\"): {self.attached_file}")
        if self.files_modified:
            lines.append("Files modified this session: " + ", ".join(sorted(Path(f).name for f in self.files_modified)[:15]))
        if self.last_test:
            t = self.last_test
            lines.append(f"Last test run: `{t.command}` → {'PASSED' if t.ok else 'FAILED'} ({time.strftime('%H:%M', time.localtime(t.at))}): {t.summary[:300]}")
        if self.last_error:
            lines.append(f"Last error: {self.last_error[:600]}")
        if self.last_claude_prompt:
            lines.append("A Claude Code prompt was prepared earlier this session.")
        if self.history:
            lines.append("Earlier requests this session (oldest first):")
            lines += [f"  - \"{r[:120]}\" → {o[:200]}" for r, o in self.history]
        return "\n".join(lines)
