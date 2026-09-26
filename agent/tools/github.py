"""
agent/tools/github.py — GitHub through the `gh` CLI the user already signed into.

No token is read, stored or passed around here: `gh` holds its own credentials.
When `gh` is missing or signed out, every method says so plainly instead of
pretending — the repository changes are still available locally through git.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time

from agent.tools.base import ToolResult, redact

_auth_cache: tuple[float, bool, str] = (0.0, False, "")


def owner_repo(url: str) -> str:
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def _gh(*args: str, cwd: str | None = None, timeout: int = 45) -> tuple[int, str]:
    try:
        p = subprocess.run(["gh", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, redact((p.stdout or "") + (p.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "gh timed out"
    except FileNotFoundError:
        return 127, "gh is not installed"


class GitHubTools:
    def available(self) -> tuple[bool, str]:
        global _auth_cache
        if not shutil.which("gh"):
            return False, "GitHub CLI (gh) isn't installed — GitHub access isn't connected. Install it and run `gh auth login`."
        at, ok, msg = _auth_cache
        if time.monotonic() - at < 120:
            return ok, msg
        code, out = _gh("auth", "status", timeout=20)
        ok = code == 0
        msg = "" if ok else "GitHub access isn't connected yet (`gh auth status` failed). Run `gh auth login`; local git still works."
        _auth_cache = (time.monotonic(), ok, msg)
        return ok, msg

    def _run(self, *args, cwd=None) -> ToolResult:
        ok, why = self.available()
        if not ok:
            return ToolResult.fail(why, "unavailable")
        code, out = _gh(*args, cwd=cwd)
        return ToolResult(code == 0, out)

    def _fmt(self, res: ToolResult, fmt) -> ToolResult:
        if not res.ok:
            return res
        try:
            return ToolResult(True, fmt(json.loads(res.output or "null")))
        except json.JSONDecodeError:
            return res

    def repo(self, repo: str) -> ToolResult:
        r = self._run("repo", "view", repo, "--json", "nameWithOwner,description,url,defaultBranchRef,visibility,pushedAt,stargazerCount,isPrivate")
        return self._fmt(r, lambda d: (f"{d['nameWithOwner']} ({d['visibility'].lower()}) — {d.get('description') or 'no description'}\n"
                                       f"{d['url']} · default branch {d['defaultBranchRef']['name']} · last push {d['pushedAt']} · ★{d['stargazerCount']}"))

    def issues(self, repo: str, state: str = "open", limit: int = 15) -> ToolResult:
        r = self._run("issue", "list", "-R", repo, "--state", state, "-L", str(limit), "--json", "number,title,labels,updatedAt,url")
        return self._fmt(r, lambda d: "\n".join(f"#{i['number']} {i['title']}  [{', '.join(l['name'] for l in i['labels'])}]" for i in d) or "No issues.")

    def prs(self, repo: str, state: str = "open", limit: int = 15) -> ToolResult:
        r = self._run("pr", "list", "-R", repo, "--state", state, "-L", str(limit), "--json", "number,title,headRefName,baseRefName,isDraft,url")
        return self._fmt(r, lambda d: "\n".join(f"#{p['number']} {p['title']}  ({p['headRefName']} → {p['baseRefName']}){' draft' if p['isDraft'] else ''}" for p in d) or "No pull requests.")

    def create_pr(self, repo_dir: str, title: str, body: str, base: str = "", draft: bool = False) -> ToolResult:
        args = ["pr", "create", "--title", title, "--body", body or ""] + (["--base", base] if base else []) + (["--draft"] if draft else [])
        return self._run(*args, cwd=repo_dir)
