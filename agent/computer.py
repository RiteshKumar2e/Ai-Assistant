"""
agent/computer.py — ComputerController: one object holding every controller.

    ComputerController
    ├── browser    BrowserController   (tools/browser.PlaywrightBrowser)
    ├── vscode     VSCodeController    (tools/vscode.CodeCLI)
    ├── terminal   TerminalController  (tools/terminal.SafeTerminal)
    ├── files      FileController      (tools/filesystem.WorkspaceFS)
    ├── git        GitController       (tools/git.GitTools)
    ├── github     GitHubTools         (tools/github — `gh` CLI)
    ├── web        WebTools            (search + page reader, no browser needed)
    ├── messaging  Messaging           (tools/messaging — Telegram/Slack/Discord/WhatsApp Cloud)
    ├── email      Email               (tools/email — Gmail/Outlook over OAuth)
    ├── calendar   Calendar            (tools/calendar — Google Calendar / local)
    ├── deploy     Deployment          (tools/deploy — Vercel/Render)
    └── claude     ClaudeCodeAdapter   (integrations/claude_code)

Agents only ever see this object, so any member can be swapped for another
implementation (an MCP-backed browser, a remote terminal) without touching them.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import threading
from pathlib import Path

from agent.tools.base import ToolResult


class ComputerController:
    def __init__(self, settings: dict, ctx, player=None, **overrides):
        from agent.integrations.claude_code import ClaudeCodeAdapter
        from agent.tools.browser import PlaywrightBrowser
        from agent.tools.calendar import Calendar
        from agent.tools.deploy import Deployment
        from agent.tools.email import Email
        from agent.tools.messaging import Messaging
        from agent.tools.filesystem import WorkspaceFS
        from agent.tools.git import GitTools
        from agent.tools.github import GitHubTools
        from agent.tools.terminal import SafeTerminal
        from agent.tools.vscode import CodeCLI
        from agent.tools.web import WebTools

        self.settings, self.ctx, self.player = settings, ctx, player
        self.cancel_event = threading.Event()
        self.files    = overrides.get("files")    or WorkspaceFS(settings["workspaces"], cwd_provider=lambda: ctx.project or None)
        self.terminal = overrides.get("terminal") or SafeTerminal(self.files, settings["command_timeout_s"], self.cancel_event)
        self.git      = overrides.get("git")      or GitTools()
        self.github   = overrides.get("github")   or GitHubTools()
        self.browser  = overrides.get("browser")  or PlaywrightBrowser(player)
        self.vscode   = overrides.get("vscode")   or CodeCLI()
        self.web      = overrides.get("web")      or WebTools()
        self.claude   = overrides.get("claude")   or ClaudeCodeAdapter(settings.get("claude_code", {}))
        self.messaging = overrides.get("messaging") or Messaging(settings.get("contacts", {}))
        self.email    = overrides.get("email")    or Email(settings.get("email_provider", ""))
        self.calendar = overrides.get("calendar") or Calendar(settings.get("calendar_provider", ""))
        self.deploy   = overrides.get("deploy")   or Deployment(settings.get("deploy", {}))

    def capabilities(self) -> dict[str, tuple[bool, str]]:
        """What actually works on this machine right now — the planner is told,
        so it plans around a missing browser instead of 'using' it."""
        def any_ok(st: dict) -> tuple[bool, str]:
            return (True, "") if any(v for v, _ in st.values()) else (False, " / ".join(w for _, w in st.values()))
        caps = {"browser": self.browser.available(), "vscode": self.vscode.available(), "git": self.git.available(),
                "github": self.github.available(), "claude_code_local": self.claude.local_available(),
                "messaging": any_ok(self.messaging.status()), "email": any_ok(self.email.status()),
                "deployment": any_ok(self.deploy.status_all())}
        google = self.calendar.pick().name == "google"
        caps["calendar"] = (True, "" if google else "local calendar only — Google Calendar not connected, so invitations can't be sent")
        return caps

    # ── OS-level helpers used by ComputerAgent ───────────────────────────────
    def open_application(self, name: str) -> ToolResult:
        from actions.open_app import open_app
        out = str(open_app(parameters={"app_name": name}, player=self.player) or "")
        return ToolResult(not out.lower().startswith(("could not", "failed", "unable", "error")), out)

    def open_folder(self, path: str) -> ToolResult:
        from actions.open_folder import _open_in_file_manager
        p = self.files.resolve(path)
        return ToolResult(True, f"Opened {p}") if p.is_dir() and _open_in_file_manager(p) else ToolResult.fail(f"Could not open {p}")

    def open_terminal(self, path: str) -> ToolResult:
        """A visible terminal window for the user in the project folder — the
        agent itself never types into it (it runs commands via SafeTerminal)."""
        p = self.files.resolve(path or ".")
        if not p.is_dir():
            return ToolResult.fail(f"Not a folder: {p}")
        try:
            sysname = platform.system()
            if sysname == "Windows":
                wt = shutil.which("wt")
                subprocess.Popen([wt, "-d", str(p)] if wt else ["cmd", "/c", "start", "", "/D", str(p), "cmd"])
            elif sysname == "Darwin":
                subprocess.Popen(["open", "-a", "Terminal", str(p)])
            else:
                subprocess.Popen(["x-terminal-emulator"], cwd=str(p))
            return ToolResult(True, f"Opened a terminal in {p}")
        except OSError as e:
            return ToolResult.fail(f"Could not open a terminal: {e}")

    def read_screen(self, question: str = "") -> ToolResult:
        """Screenshot → Gemini vision → text description (the only way a text
        agent can 'see'). Fails honestly when capture or vision isn't available."""
        try:
            from actions.screen_processor import _capture_screen
            from google.genai import types
            from core.text_model import get_text_model
            img, mime = _capture_screen()
            q = question or "Describe what is on this screen: the focused app, any visible errors, dialogs, or key text."
            out = get_text_model().generate_content([types.Part.from_bytes(data=img, mime_type=mime), q]).text
            return ToolResult(True, out)
        except Exception as e:
            return ToolResult.fail(f"Screen reading isn't available: {e}", "unavailable")

    def screen_input(self, action: str, **kw) -> ToolResult:
        from actions.computer_control import computer_control
        out = str(computer_control(parameters={"action": action, **kw}, player=self.player) or "")
        return ToolResult(not out.lower().startswith(("error", "could not", "failed", "unknown")), out)

    def project_dir(self) -> str:
        return self.ctx.project or ""

    def is_project_set(self) -> bool:
        return bool(self.ctx.project) and Path(self.ctx.project).is_dir()
