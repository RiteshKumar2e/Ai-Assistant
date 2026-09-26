"""
agent/tools/base.py — the shapes every controller and tool share.

ToolResult is the only thing a tool ever returns. `ok` is set from what the
underlying operation actually reported (exit code, HTTP status, exception) —
never from what the model hoped would happen — and the executor's final report
is built from these, so a claim like "pushed" can only come from a push whose
result said ok.

The controller interfaces are abstract on purpose: the agent talks to
`BrowserController`, not to Playwright. Swapping in an MCP browser, an OS
automation layer or a remote VS Code only means providing another class with
the same methods.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Risk classes, from least to most consequential. orchestration/permissions.py
# maps (risk, confirmation_mode) → allow / confirm / deny.
READ, WRITE, EXEC, EXTERNAL, DESTRUCTIVE, FORBIDDEN = (
    "read", "write", "exec", "external", "destructive", "forbidden")


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    data: dict = field(default_factory=dict)
    status: str = ""          # ok | error | denied | cancelled | unavailable | needs_confirmation

    def __post_init__(self):
        self.status = self.status or ("ok" if self.ok else "error")

    @classmethod
    def fail(cls, msg: str, status: str = "error", **data) -> "ToolResult":
        return cls(False, msg, data, status)

    def brief(self, n: int = 3000) -> str:
        head = "OK" if self.ok else self.status.upper()
        body = self.output if len(self.output) <= n else self.output[:n // 3] + "\n…[truncated]…\n" + self.output[-2 * n // 3:]
        return f"[{head}] {body}"


@dataclass
class Tool:
    """One capability the step-decider may call. `risk` may be a callable
    (args) → risk, for tools whose consequence depends on arguments —
    e.g. a terminal command, or a browser click on 'Place order'."""
    name: str
    description: str
    params: dict[str, str]
    fn: Callable[..., ToolResult]
    risk: Any = READ
    agent: str = ""
    confirm_detail: Callable[[dict], tuple[str, str]] | None = None   # args → (title, detail) for the HUD
    long_running: bool = False
    # For tools that need an Approval object (git push): args → ToolResult whose
    # data holds {scope, title, detail, flagged}. A failed prepare is returned
    # as the tool's result without asking anyone anything.
    prepare: Callable[[dict], "ToolResult"] | None = None

    def risk_for(self, args: dict) -> str:
        return self.risk(args) if callable(self.risk) else self.risk

    # MCP-style view of the same tool: {name, description, inputSchema}, a
    # permission class, and execute(). execute() is the raw call — permission
    # checks happen in orchestration/executor.TaskRunner.invoke, which is the
    # only caller inside the agent.
    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {k: {"type": "string", "description": v} for k, v in self.params.items()}}

    @property
    def permissions(self) -> str:
        return "dynamic (depends on arguments)" if callable(self.risk) else self.risk

    def execute(self, args: dict, **extra) -> "ToolResult":
        from agent.orchestration.executor import call_tool
        return call_tool(self, args, **extra)

    def to_mcp(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema,
                "annotations": {"permission": self.permissions, "agent": self.agent}}

    def signature(self) -> str:
        ps = ", ".join(f"{k}: {v}" for k, v in self.params.items())
        return f"- {self.name}({ps}) — {self.description}"


# ── Secret hygiene, shared by filesystem, terminal and git output ─────────────
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_\-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
               r"|sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}|xox[abprs]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)((?:api[_-]?key|secret|token|passw(?:or)?d|client_secret|private_key)[\"']?\s*[:=]\s*[\"']?)([^\s\"',}]{8,})"),
    re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"),       # credentials embedded in a URL
]


def redact(text: str) -> str:
    if not text:
        return text
    text = _SECRET_PATTERNS[0].sub("[REDACTED PRIVATE KEY]", text)
    text = _SECRET_PATTERNS[1].sub("[REDACTED]", text)
    text = _SECRET_PATTERNS[2].sub(lambda m: m.group(1) + "[REDACTED]", text)
    return _SECRET_PATTERNS[3].sub(r"\1[REDACTED]@", text)


_SECRET_NAMES = re.compile(
    r"(?i)^(\.env(\..*)?|.*\.pem|.*\.key|.*\.p12|.*\.pfx|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|known_hosts|"
    r"api_keys\.json|.*credentials.*|token.*\.json|client_secret.*\.json|\.npmrc|\.pypirc|\.netrc|_netrc|"
    r"login data|cookies|web data|key[34]\.db|logins\.json|.*\.kdbx)$")
_SECRET_DIRS = {".ssh", ".gnupg", ".aws", ".azure", "whatsapp_web", "certs"}


def is_secret_path(p: Path) -> bool:
    """Files the model has no business reading: keys, tokens, browser cookie
    stores, .env files (in the user's projects). `*.example` files are fine — they exist to be read."""
    name = p.name
    if name.lower().endswith((".example", ".sample", ".template")):
        return False
    return bool(_SECRET_NAMES.match(name)) or any(part.lower() in _SECRET_DIRS for part in p.parts)


# ── Controller interfaces ────────────────────────────────────────────────────
class TerminalController(ABC):
    @abstractmethod
    def run(self, command: str, cwd: str | None = None, timeout: int | None = None) -> ToolResult: ...
    @abstractmethod
    def start(self, command: str, cwd: str | None = None) -> ToolResult: ...
    @abstractmethod
    def read(self, proc_id: str) -> ToolResult: ...
    @abstractmethod
    def stop(self, proc_id: str) -> ToolResult: ...


class FileController(ABC):
    @abstractmethod
    def list_dir(self, path: str) -> ToolResult: ...
    @abstractmethod
    def read_file(self, path: str, start: int = 1, end: int | None = None) -> ToolResult: ...
    @abstractmethod
    def write_file(self, path: str, content: str) -> ToolResult: ...
    @abstractmethod
    def edit_file(self, path: str, old: str, new: str) -> ToolResult: ...
    @abstractmethod
    def search(self, pattern: str, path: str = ".", glob: str = "") -> ToolResult: ...


class GitController(ABC):
    @abstractmethod
    def status(self, repo: str) -> ToolResult: ...
    @abstractmethod
    def diff(self, repo: str, staged: bool = False, path: str = "") -> ToolResult: ...
    @abstractmethod
    def commit(self, repo: str, message: str) -> ToolResult: ...
    @abstractmethod
    def push(self, repo: str, approval) -> ToolResult: ...


class BrowserController(ABC):
    @abstractmethod
    def available(self) -> tuple[bool, str]: ...
    @abstractmethod
    def open_url(self, url: str) -> ToolResult: ...
    @abstractmethod
    def read_page(self) -> ToolResult: ...


class VSCodeController(ABC):
    @abstractmethod
    def available(self) -> tuple[bool, str]: ...
    @abstractmethod
    def open_folder(self, path: str) -> ToolResult: ...
    @abstractmethod
    def open_file(self, path: str, line: int = 0) -> ToolResult: ...
