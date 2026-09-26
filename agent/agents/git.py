"""GitAgent — local git: status, diff, log, branches, stage, commit, pull, and a
push that shows the user repo / branch / remote / commits / test status first."""
from __future__ import annotations

import re

from agent.agents.base import Agent, int_arg, list_arg
from agent.tools.base import DESTRUCTIVE, EXTERNAL, READ, WRITE, ToolResult

_COMMIT_PROMPT = """Write a git commit message for the staged changes below.
Format: a subject line in imperative mood, <= 72 chars, no trailing period (Conventional Commit prefix like fix:/feat:/refactor:
only if it fits naturally); then a blank line and 1-4 short bullet lines on what changed and why. Describe only what the diff shows.
Output only the message.

Files:
{stat}

Diff (may be truncated):
{diff}"""


_PUNCT = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
                        "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", " ": " "})


def plain(msg: str) -> str:
    """Models love typographic dashes/quotes; in commit messages they render as
    mojibake in many terminals and tools. Keep other Unicode (names, languages)."""
    return (msg or "").translate(_PUNCT)


class GitAgent(Agent):
    name = "GitAgent"
    description = "Local git: status, diff, log, branches, create/switch branch, stage, commit (with a generated message), pull, push (with confirmation)."

    def _repo(self, repo: str = "") -> str:
        return repo or self.project()

    def commit_message(self, repo: str = "") -> ToolResult:
        g, r = self.cc.git, self._repo(repo)
        stat, diff = g.diff(r, staged=True, stat=True), g.diff(r, staged=True)
        if not stat.ok or not stat.output.strip():
            return ToolResult.fail("Nothing staged to describe.")
        msg = ""
        if self.llm:
            try:
                msg = self.llm.complete(_COMMIT_PROMPT.format(stat=stat.output[:3000], diff=diff.output[:12000])).strip().strip("`").strip()
            except Exception as e:
                print(f"[GitAgent] commit-message model failed: {e}")
        if not msg or len(msg.splitlines()[0]) > 100:
            files = re.findall(r"^\s*(\S+)\s+\|", stat.output, re.M)
            msg = f"Update {', '.join(files[:4])}{' and more' if len(files) > 4 else ''}" if files else "Update project files"
        msg = plain(msg)
        return ToolResult(True, msg, {"message": msg})

    def commit(self, message: str = "", repo: str = "") -> ToolResult:
        r = self._repo(repo)
        message = plain(message)
        if not message:
            m = self.commit_message(r)
            if not m.ok:
                return m
            message = m.data["message"]
        res = self.cc.git.commit(r, message)
        if res.ok:
            self.emit(f"Committed {res.data.get('sha')}: {message.splitlines()[0]}")
        return res

    def _tests_line(self, repo: str) -> tuple[str, bool]:
        t = self.cc.ctx.last_test
        if not t:
            return "Tests: not run this session", True
        same = t.head and t.head == self.cc.git.head_tree(repo)
        if not t.ok:
            return f"Tests: FAILED ({t.command})", True
        return (f"Tests: passed on exactly this code ({t.command})", False) if same else (f"Tests: passed earlier ({t.command}), but the code changed since", True)

    def prepare_push(self, args: dict) -> ToolResult:
        if (e := self.need_project()) and not args.get("repo"):
            return e
        r = self._repo(args.get("repo", ""))
        # Expected remote: an explicit setting, else the remote the project had when it was opened —
        # so a remote that was re-pointed mid-task is flagged rather than silently pushed to.
        expected = self.cc.settings.get("expected_remote") or self.cc.ctx.remote
        pf = self.cc.git.preflight(r, self.cc.settings.get("protected_branches", []), expected)
        if not pf.ok:
            return pf
        d = pf.data
        tests, tests_flag = self._tests_line(r)
        warnings = d["warnings"] + ([tests] if tests_flag else [])
        n = len(d["commits"])
        title = f"Push {n} commit{'s' * (n != 1)} to {d['remote']}/{d['branch']}"
        detail = (f"Repo {d['repo']}\nBranch {d['branch']} → {d['url']}\n{tests}\n"
                  + ("\n".join(d["commits"][:5])) + ("\n⚠ " + "; ".join(warnings) if warnings else ""))
        return ToolResult(True, pf.output + "\n" + tests, {"scope": self.cc.git.push_scope(d), "title": title,
                                                           "detail": detail, "flagged": bool(warnings)})

    def push(self, approval=None, repo: str = "") -> ToolResult:
        res = self.cc.git.push(self._repo(repo), approval)
        if res.ok:
            self.emit(res.output)
        return res

    def tools(self):
        g = self.cc.git
        R = self._repo
        return [
            self.tool("git_status", "Branch, upstream, ahead/behind, remote and changed files.", {"repo": "default current project"},
                      lambda repo="": g.status(R(repo)), READ),
            self.tool("git_diff", "Show changes (unstaged by default).", {"staged": "bool", "path": "limit to file", "stat": "bool summary only"},
                      lambda staged=False, path="", stat=False, repo="": g.diff(R(repo), bool(staged), path, bool(stat)), READ),
            self.tool("git_log", "Recent commits.", {"n": "count, default 10"}, lambda n=10, repo="": g.log(R(repo), int_arg(n, 10)), READ),
            self.tool("git_branches", "List branches.", {}, lambda repo="": g.branches(R(repo)), READ),
            self.tool("git_create_branch", "Create and switch to a new branch.", {"name": "branch name"},
                      lambda name, repo="": g.create_branch(R(repo), name), WRITE),
            self.tool("git_switch", "Switch to an existing branch.", {"name": "branch"}, lambda name, repo="": g.switch(R(repo), name), WRITE),
            self.tool("git_add", "Stage files (default: all changes; secret files are never staged).", {"paths": "list or 'all'"},
                      lambda paths="all", repo="": g.add(R(repo), list_arg(paths)), WRITE),
            self.tool("git_commit_message", "Draft a commit message from the staged diff.", {}, lambda repo="": self.commit_message(repo), READ),
            self.tool("git_commit", "Commit staged changes. Omit message to generate a good one from the diff.", {"message": "optional"},
                      lambda message="", repo="": self.commit(message, repo), WRITE),
            self.tool("git_pull", "Fast-forward pull from upstream.", {}, lambda repo="": g.pull(R(repo)), WRITE),
            self.tool("git_push", "Push the current branch to its remote. Shows the user repo/branch/remote/commits/tests and waits for approval. "
                      "Never force-pushes; reports success only after verifying the remote.", {},
                      lambda approval=None, repo="": self.push(approval, repo), EXTERNAL, prepare=self.prepare_push),
            self.tool("git_delete_branch", "Delete a merged local branch (asks first).", {"name": "branch"},
                      lambda name, repo="": g.delete_branch(R(repo), name), DESTRUCTIVE,
                      confirm_detail=lambda a: ("Delete branch", f"Delete local branch {a.get('name')}?")),
        ]
