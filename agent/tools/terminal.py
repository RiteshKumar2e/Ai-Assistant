"""
agent/tools/terminal.py — a controlled terminal, not a shell.

The model never gets `shell=True`. A command string is:
  1. rejected if it contains shell operators (; & | > < ` $( newline) — one
     program per call, so "npm test && git push" cannot smuggle a push past
     the git gate;
  2. split into argv and classified by its program + subcommand into a risk
     (tools/base: read / exec / destructive / forbidden);
  3. run with cwd confined to a workspace, a timeout, an output cap, secrets
     scrubbed from the environment, and its whole process tree killable.

`classify()` is pure, so the permission policy and the tests can ask "what
would this be?" without running anything.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from agent.tools.base import DESTRUCTIVE, EXEC, FORBIDDEN, READ, WRITE, TerminalController, ToolResult, is_secret_path, redact

_WIN = os.name == "nt"
_SHELL_OPS = re.compile(r"(;|&|\||>|<|`|\$\(|\r|\n)")
_BAT_META  = re.compile(r"[%^!]")            # expanded by cmd.exe when the target is a .cmd/.bat shim

# Never runnable, in any mode: OS/disk/credential/security tooling, and shells
# or encoders that would let a command escape this validator.
_FORBIDDEN = {
    "format", "diskpart", "mkfs", "fdisk", "dd", "bcdedit", "reg", "regedit", "vssadmin", "cipher",
    "takeown", "icacls", "cacls", "netsh", "sc", "schtasks", "crontab", "launchctl", "systemctl",
    "sudo", "su", "runas", "doas", "useradd", "userdel", "usermod", "passwd", "net", "cmdkey", "security",
    "mimikatz", "certutil", "wmic", "shutdown", "reboot", "halt", "poweroff",
    "cmd", "powershell", "pwsh", "bash", "sh", "zsh", "fish", "wsl", "csh", "mshta", "wscript", "cscript",
    "rundll32", "regsvr32", "bitsadmin", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp", "curl", "wget",
}
_DESTRUCTIVE = {"rm", "rmdir", "del", "erase", "rd", "shred", "unlink", "chmod", "chown", "kill", "taskkill", "pkill", "killall"}
_READ_ONLY   = {"ls", "dir", "pwd", "where", "which", "whoami", "echo", "tree", "type", "cat", "head", "tail", "wc", "findstr", "grep", "rg"}
_DEV = {
    "npm", "pnpm", "yarn", "bun", "node", "deno", "tsc", "eslint", "prettier", "jest", "vitest", "next", "vite",
    "python", "python3", "py", "pytest", "pip", "pip3", "uv", "poetry", "ruff", "black", "mypy", "flake8", "tox",
    "cargo", "rustc", "go", "make", "cmake", "ninja", "dotnet", "mvn", "gradle", "gradlew", "java", "javac",
    "flutter", "dart", "gcc", "g++", "clang", "swift", "ruby", "bundle", "rake", "php", "composer", "git", "gh", "code",
}
# git is split: read subcommands run here; anything that changes history or a
# remote goes through tools/git.py, where push has its preflight + gate.
_GIT_READ = {"status", "diff", "log", "show", "branch", "remote", "rev-parse", "ls-files", "blame", "describe",
             "shortlog", "tag", "config", "stash"}
_GIT_ROUTED = {"push", "commit", "add", "pull", "fetch", "merge", "rebase", "reset", "checkout", "switch",
               "restore", "clean", "cherry-pick", "revert", "am", "apply", "rm", "mv", "gc", "filter-branch", "worktree"}
_SECRET_ENV = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|COOKIE|SESSION|AUTH|PRIVATE)")
_ENV_KEEP   = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE",
               "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "WINDIR",
               "USERNAME", "LANG", "TERM", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "SHELL", "USER",
               "VIRTUAL_ENV", "CONDA_PREFIX", "JAVA_HOME", "GOPATH", "GOROOT", "CARGO_HOME", "RUSTUP_HOME", "NVM_HOME",
               "NVM_SYMLINK", "NODE_PATH", "PYTHONPATH", "ANDROID_HOME", "FLUTTER_ROOT"}
_OUT_CAP = 16_000


def split(command: str) -> list[str]:
    if _WIN:
        return [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t
                for t in shlex.split(command, posix=False)]
    return shlex.split(command)


def classify(command: str) -> tuple[str, str]:
    """(risk, reason). Pure — no side effects."""
    if not command or not command.strip():
        return FORBIDDEN, "empty command"
    if _SHELL_OPS.search(command):
        return FORBIDDEN, "shell operators (; & | > < ` $() are not allowed — run one program per call"
    try:
        argv = split(command)
    except ValueError as e:
        return FORBIDDEN, f"could not parse command: {e}"
    prog = Path(argv[0]).name.lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1", ".sh"):
        prog = prog[:-len(ext)] if prog.endswith(ext) else prog
    rest = [a.lower() for a in argv[1:]]
    if prog in _FORBIDDEN:
        return FORBIDDEN, f"'{prog}' is a system/credential/shell tool the agent may not run"
    if any(is_secret_path(Path(a)) for a in argv[1:] if not a.startswith("-")):
        return FORBIDDEN, "the command names a secret/credential file"
    if prog in _DESTRUCTIVE:
        return DESTRUCTIVE, f"'{prog}' deletes or kills things"
    if prog in _READ_ONLY:
        return READ, "read-only command"
    if prog == "git":
        sub = next((a for a in rest if not a.startswith("-")), "")
        if sub in _GIT_ROUTED:
            return FORBIDDEN, f"use the git tools for 'git {sub}' (they verify the repo/branch and gate pushes)"
        if sub in ("branch", "tag") and any(a in ("-d", "-D", "--delete") for a in rest):
            return DESTRUCTIVE, f"git {sub} delete"
        if (sub == "config" and len([a for a in rest if not a.startswith("-")]) > 2) or sub == "stash"                 or (sub == "remote" and any(a in ("add", "remove", "rm", "set-url", "rename") for a in rest)):
            return WRITE, f"git {sub} changes repository settings/state"
        return (READ, "git read") if sub in _GIT_READ else (EXEC, f"git {sub}")
    if prog == "gh":
        sub = " ".join(a for a in rest[:2] if not a.startswith("-"))
        if re.match(r"(auth|secret|ssh-key|gpg-key|api|repo delete|release delete|extension)", sub):
            return FORBIDDEN, f"'gh {sub}' touches credentials or deletes remote data"
        if re.match(r"(pr|issue|repo|release|workflow) (create|merge|close|edit|comment|delete|run|fork)", sub):
            return EXEC + ":external", f"gh {sub} changes GitHub"
        return READ, "gh read"
    if prog in ("python", "python3", "py", "node", "deno", "bun") and any(a in ("-c", "-e", "--eval", "-p") for a in rest):
        return EXEC + ":inline", "runs inline code"
    if prog in ("pip", "pip3") or (prog in ("python", "python3", "py") and rest[:2] == ["-m", "pip"]):
        if "uninstall" in rest:
            return DESTRUCTIVE, "uninstalls packages"
    if prog in ("npm", "pnpm", "yarn", "bun") and any(a in ("publish", "unpublish", "deprecate", "login", "adduser", "token", "owner") for a in rest):
        return EXEC + ":external", f"{prog} publishes or changes registry/account state"
    if prog in _DEV:
        return EXEC, "development command"
    return EXEC + ":unknown", f"'{prog}' is not a recognised development tool"


def _clean_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_KEEP or not _SECRET_ENV.search(k)}
    env.update({"CI": "1", "FORCE_COLOR": "0", "NO_COLOR": "1", "PYTHONUNBUFFERED": "1", "GIT_TERMINAL_PROMPT": "0",
                "PYTHONIOENCODING": "utf-8"})
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        import psutil
        parent = psutil.Process(proc.pid)
        for c in parent.children(recursive=True):
            c.kill()
        parent.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _cap(text: str) -> str:
    text = redact(text)
    return text if len(text) <= _OUT_CAP else text[:4000] + f"\n…[{len(text) - _OUT_CAP:,} chars omitted]…\n" + text[-(_OUT_CAP - 4000):]


class _Proc:
    def __init__(self, cmd: str, popen: subprocess.Popen):
        self.cmd, self.p, self.buf, self.read_pos, self.started = cmd, popen, [], 0, time.monotonic()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in iter(self.p.stdout.readline, ""):
            self.buf.append(line)
            if len(self.buf) > 5000:
                del self.buf[:1000]; self.read_pos = max(0, self.read_pos - 1000)


class SafeTerminal(TerminalController):
    def __init__(self, fs, default_timeout: int = 300, cancel_event: threading.Event | None = None):
        self.fs, self.default_timeout = fs, default_timeout
        self.cancel_event = cancel_event or threading.Event()
        self._procs: dict[str, _Proc] = {}

    def _prepare(self, command: str, cwd: str | None):
        risk, reason = classify(command)
        if risk == FORBIDDEN:
            return None, ToolResult.fail(f"Not run: {reason}.", "denied")
        wd = self.fs.resolve(cwd or ".")
        if not wd.is_dir():
            return None, ToolResult.fail(f"Working directory does not exist: {wd}")
        argv = split(command)
        exe = shutil.which(argv[0], path=os.environ.get("PATH"))
        if argv[0].lower() in ("python", "python3", "py") and not exe:
            exe = sys.executable
        if not exe:
            return None, ToolResult.fail(f"'{argv[0]}' is not installed or not on PATH.", "unavailable")
        if exe.lower().endswith((".cmd", ".bat")) and any(_BAT_META.search(a) for a in argv[1:]):
            return None, ToolResult.fail("Arguments contain % ^ or ! which cmd.exe would expand — not run.", "denied")
        return ([exe, *argv[1:]], wd), None

    def run(self, command: str, cwd: str | None = None, timeout: int | None = None) -> ToolResult:
        prep, err = self._prepare(command, cwd)
        if err:
            return err
        argv, wd = prep
        timeout = min(int(timeout or self.default_timeout), 1800)
        t0 = time.monotonic()
        try:
            p = subprocess.Popen(argv, cwd=str(wd), env=_clean_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult.fail(f"Could not start {argv[0]}: {e}")
        proc = _Proc(command, p)
        while p.poll() is None:
            if self.cancel_event.is_set():
                _kill_tree(p)
                return ToolResult.fail(f"Cancelled: {command}\n{_cap(''.join(proc.buf))}", "cancelled")
            if time.monotonic() - t0 > timeout:
                _kill_tree(p)
                return ToolResult.fail(f"Timed out after {timeout}s: {command}\n{_cap(''.join(proc.buf))}",
                                       "error", timed_out=True)
            time.sleep(0.1)
        time.sleep(0.05)
        out, code, secs = "".join(proc.buf), p.returncode, time.monotonic() - t0
        return ToolResult(code == 0, f"$ {command}  (exit {code}, {secs:.1f}s, in {wd})\n{_cap(out)}",
                          {"exit_code": code, "cwd": str(wd), "command": command, "seconds": round(secs, 1)})

    def start(self, command: str, cwd: str | None = None, settle: float = 6.0) -> ToolResult:
        """Background process (dev server, watcher). Returns an id plus whatever
        it printed in its first few seconds — enough to see 'listening on :3000'
        or an immediate crash."""
        prep, err = self._prepare(command, cwd)
        if err:
            return err
        argv, wd = prep
        try:
            p = subprocess.Popen(argv, cwd=str(wd), env=_clean_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult.fail(f"Could not start {argv[0]}: {e}")
        pid = uuid.uuid4().hex[:6]
        self._procs[pid] = proc = _Proc(command, p)
        end = time.monotonic() + settle
        while time.monotonic() < end and p.poll() is None and not self.cancel_event.is_set():
            time.sleep(0.2)
        out = "".join(proc.buf); proc.read_pos = len(proc.buf)
        if p.poll() is not None:
            self._procs.pop(pid, None)
            return ToolResult(p.returncode == 0, f"$ {command} exited immediately (exit {p.returncode})\n{_cap(out)}",
                              {"exit_code": p.returncode})
        return ToolResult(True, f"Started [{pid}] {command} (pid {p.pid}) in {wd}\n{_cap(out)}", {"proc_id": pid})

    def read(self, proc_id: str) -> ToolResult:
        proc = self._procs.get(proc_id)
        if not proc:
            return ToolResult.fail(f"No running process {proc_id}. Running: {', '.join(self._procs) or 'none'}")
        new = "".join(proc.buf[proc.read_pos:]); proc.read_pos = len(proc.buf)
        state = "running" if proc.p.poll() is None else f"exited ({proc.p.returncode})"
        return ToolResult(True, f"[{proc_id}] {proc.cmd} — {state}\n{_cap(new) or '(no new output)'}")

    def stop(self, proc_id: str = "") -> ToolResult:
        ids = [proc_id] if proc_id else list(self._procs)
        stopped = []
        for i in ids:
            if (proc := self._procs.pop(i, None)):
                _kill_tree(proc.p); stopped.append(f"[{i}] {proc.cmd}")
        return ToolResult(bool(stopped), "Stopped " + ", ".join(stopped) if stopped else "Nothing to stop.")

    def running(self) -> list[str]:
        return [f"[{i}] {p.cmd}" for i, p in self._procs.items() if p.p.poll() is None]
