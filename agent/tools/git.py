"""
agent/tools/git.py — git operations with a push that has to earn its "pushed".

push() is the one method here with real consequences outside the machine, so:
  * it requires an Approval minted by orchestration/permissions.ConfirmationBroker
    after a human answered (or after the policy allowed it in trusted mode) —
    a call without one is refused here, not just in the UI;
  * the approval is bound to the exact repo + branch + remote + HEAD it was
    shown for, so a commit made after the user said yes cannot ride along;
  * it never force-pushes;
  * success is reported only after `git ls-remote` shows the remote branch at
    our HEAD — exit code 0 alone is not taken as proof.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from agent.tools.base import GitController, ToolResult, is_secret_path, redact


def _git(repo: str | Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=_git_env(), stdin=subprocess.DEVNULL)
        return p.returncode, redact((p.stdout or "") + (p.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, f"git {' '.join(args[:2])} timed out after {timeout}s"
    except FileNotFoundError:
        return 127, "git is not installed."


def _git_env() -> dict:
    import os
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat", "LC_ALL": "C"})
    return env


_ARTIFACT_DIRS = {"__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", ".next",
                  ".venv", "venv", ".tox", "coverage", ".gradle", "target", ".dart_tool", ".parcel-cache"}
_ARTIFACT_EXT = (".pyc", ".pyo", ".class", ".o", ".obj", ".log", ".tmp")


def _is_artifact(path: str) -> bool:
    p = Path(path)
    return p.suffix.lower() in _ARTIFACT_EXT or p.name in (".DS_Store", "Thumbs.db") or any(x in _ARTIFACT_DIRS for x in p.parts)


def parse_status(porcelain: str) -> dict:
    """`git status --porcelain=v1 -b` → {branch, upstream, ahead, behind, files:[(code, path)]}."""
    info = {"branch": "", "upstream": "", "ahead": 0, "behind": 0, "files": []}
    for line in porcelain.splitlines():
        if line.startswith("## "):
            head = line[3:]
            m = re.match(r"(?:No commits yet on )?([^.\s]+)(?:\.\.\.(\S+))?(?: \[(.*)\])?", head)
            if m:
                info["branch"], info["upstream"] = m.group(1), m.group(2) or ""
                for part in (m.group(3) or "").split(","):
                    if (a := re.search(r"ahead (\d+)", part)): info["ahead"] = int(a.group(1))
                    if (b := re.search(r"behind (\d+)", part)): info["behind"] = int(b.group(1))
        elif len(line) > 3:
            info["files"].append((line[:2], line[3:].strip().strip('"')))
    return info


class GitTools(GitController):
    def available(self) -> tuple[bool, str]:
        return (True, "") if shutil.which("git") else (False, "git is not installed")

    def root(self, path: str) -> Path | None:
        code, out = _git(path, "rev-parse", "--show-toplevel")
        return Path(out.splitlines()[-1]).resolve() if code == 0 and out else None

    def _repo(self, path: str) -> tuple[Path | None, ToolResult | None]:
        if not path or not Path(path).exists():
            return None, ToolResult.fail("No project selected — open a project first (open_project).")
        r = self.root(path)
        return (r, None) if r else (None, ToolResult.fail(f"{path} is not inside a git repository."))

    def worktree_id(self, repo: str) -> str:
        """Tree id of the working tree as it is right now (tracked + untracked,
        ignoring .gitignore'd files), built in a throwaway index so the real
        index is untouched. Equal to `HEAD^{tree}` once everything is committed —
        which is how push knows the tests ran on exactly what is being pushed."""
        import os
        import tempfile
        r = self.root(repo)
        if not r:
            return ""
        fd, idx = tempfile.mkstemp(prefix="judo-idx-"); os.close(fd); os.unlink(idx)
        env = {**_git_env(), "GIT_INDEX_FILE": idx}
        try:
            run = lambda *a: subprocess.run(["git", *a], cwd=str(r), env=env, capture_output=True, text=True, timeout=60)
            if run("rev-parse", "--verify", "-q", "HEAD").returncode == 0:
                run("read-tree", "HEAD")
            run("add", "-A")
            # Same rule as add(): build artifacts are not part of "the code", so the
            # __pycache__ a test run leaves behind doesn't make tested code look changed.
            junk = [f for f in run("ls-files").stdout.splitlines() if _is_artifact(f)]
            for i in range(0, len(junk), 200):
                run("rm", "--cached", "-q", "--ignore-unmatch", "--", *junk[i:i + 200])
            p = run("write-tree")
            return p.stdout.strip() if p.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""
        finally:
            try:
                os.unlink(idx)
            except OSError:
                pass

    def head_tree(self, repo: str) -> str:
        code, out = _git(repo, "rev-parse", "HEAD^{tree}")
        return out.strip() if code == 0 else ""

    def remote_url(self, repo: Path, remote: str = "origin") -> str:
        code, out = _git(repo, "remote", "get-url", remote)
        return out if code == 0 else ""

    # ── read ─────────────────────────────────────────────────────────────────
    def status(self, repo: str) -> ToolResult:
        r, err = self._repo(repo)
        if err:
            return err
        code, out = _git(r, "status", "--porcelain=v1", "-b", "--untracked-files=all")
        if code:
            return ToolResult.fail(out)
        info = parse_status(out)
        info.update(repo=str(r), remote=self.remote_url(r))
        n = len(info["files"])
        summary = (f"Repo {r.name} · branch {info['branch'] or '?'}"
                   + (f" → {info['upstream']}" if info["upstream"] else " (no upstream)")
                   + (f" · ahead {info['ahead']}" if info["ahead"] else "") + (f" · behind {info['behind']}" if info["behind"] else "")
                   + f" · {n} changed file{'s' * (n != 1)}" + (f" · remote {info['remote']}" if info["remote"] else ""))
        files = "\n".join(f"  {c} {p}" for c, p in info["files"][:100])
        return ToolResult(True, summary + ("\n" + files if files else "\nWorking tree clean."), info)

    def diff(self, repo: str, staged: bool = False, path: str = "", stat: bool = False) -> ToolResult:
        r, err = self._repo(repo)
        if err:
            return err
        args = ["diff", "--no-color"] + (["--cached"] if staged else []) + (["--stat"] if stat else []) + (["--", path] if path else [])
        code, out = _git(r, *args)
        if code:
            return ToolResult.fail(out)
        if not staged and not out:
            _, untracked = _git(r, "ls-files", "--others", "--exclude-standard")
            out = f"(no diff to tracked files)\nUntracked:\n{untracked}" if untracked else "No changes."
        return ToolResult(True, out[:30000] + ("\n…[diff truncated]" if len(out) > 30000 else ""))

    def log(self, repo: str, n: int = 10) -> ToolResult:
        r, err = self._repo(repo)
        if err:
            return err
        code, out = _git(r, "log", f"-{max(1, min(int(n), 50))}", "--pretty=format:%h %ad %an  %s", "--date=short")
        return ToolResult(code == 0, out or "No commits yet.")

    def branches(self, repo: str) -> ToolResult:
        r, err = self._repo(repo)
        if err:
            return err
        code, out = _git(r, "branch", "-a", "-vv", "--no-color")
        return ToolResult(code == 0, out)

    # ── local writes ─────────────────────────────────────────────────────────
    def create_branch(self, repo: str, name: str) -> ToolResult:
        r, err = self._repo(repo)
        if err:
            return err
        if not re.fullmatch(r"[A-Za-z0-9._/\-]{1,100}", name or "") or ".." in name:
            return ToolResult.fail(f"Invalid branch name: {name!r}")
        code, out = _git(r, "switch", "-c", name)
        return ToolResult(code == 0, out or f"Created and switched to {name}", {"branch": name})

    def switch(self, repo: str, name: str) -> ToolResult:
        r, err = self._repo(repo)
        if err:
            return err
        code, out = _git(r, "switch", name)
        return ToolResult(code == 0, out or f"Switched to {name}", {"branch": name})

    def add(self, repo: str, paths: list[str] | None = None) -> ToolResult:
        """Stage files. Secret-looking files are never staged; with `all`, build
        artifacts (__pycache__, node_modules, dist, …) that a missing .gitignore
        lets through are skipped too — name them explicitly to stage them."""
        r, err = self._repo(repo)
        if err:
            return err
        info = parse_status(_git(r, "status", "--porcelain=v1", "-b", "--untracked-files=all")[1])
        changed = [p for _, p in info["files"]]
        stage_all = not paths or paths in (["."], ["all"], ["-A"])
        wanted = [p.split(" -> ")[-1] for p in (changed if stage_all else paths)]
        blocked = [p for p in wanted if is_secret_path(Path(p))]
        junk = [p for p in wanted if stage_all and p not in blocked and _is_artifact(p)]
        ok_paths = [p for p in wanted if p not in blocked and p not in junk]
        if not ok_paths:
            return ToolResult.fail("Nothing to stage." + (f" Refused secret files: {', '.join(blocked)}" if blocked else "")
                                   + (f" Skipped build artifacts: {', '.join(junk[:8])} (consider a .gitignore)" if junk else ""))
        code, out = _git(r, "add", "--", *ok_paths)
        msg = f"Staged {len(ok_paths)} file(s): {', '.join(ok_paths[:12])}{' …' if len(ok_paths) > 12 else ''}"
        if blocked:
            msg += f"\nNot staged (look like secrets): {', '.join(blocked)}"
        if junk:
            msg += f"\nNot staged (build artifacts — add a .gitignore): {', '.join(junk[:8])}{' …' if len(junk) > 8 else ''}"
        return ToolResult(code == 0, out if code else msg, {"staged": ok_paths, "blocked": blocked, "skipped": junk})

    def commit(self, repo: str, message: str) -> ToolResult:
        r, err = self._repo(repo)
        if err:
            return err
        if not (message or "").strip():
            return ToolResult.fail("Commit message is empty.")
        code, staged = _git(r, "diff", "--cached", "--name-only")
        if not staged.strip():
            return ToolResult.fail("Nothing is staged — stage files with git_add first.")
        code, out = _git(r, "commit", "-m", message.strip())
        if code:
            return ToolResult.fail(out)
        _, sha = _git(r, "rev-parse", "--short", "HEAD")
        return ToolResult(True, f"Committed {sha}: {message.strip().splitlines()[0]}\n{out}",
                          {"sha": sha, "files": staged.split()})

    def pull(self, repo: str) -> ToolResult:
        r, err = self._repo(repo)
        if err:
            return err
        code, out = _git(r, "pull", "--ff-only", timeout=120)
        return ToolResult(code == 0, out)

    # ── push ─────────────────────────────────────────────────────────────────
    def preflight(self, repo: str, protected: list[str] | tuple = (), expected_remote: str = "") -> ToolResult:
        """Everything the user needs to see before saying yes, plus warnings."""
        r, err = self._repo(repo)
        if err:
            return err
        st = self.status(str(r)).data
        branch, remote = st.get("branch", ""), "origin"
        if st.get("upstream"):
            remote = st["upstream"].split("/", 1)[0]
        url = self.remote_url(r, remote)
        _, head = _git(r, "rev-parse", "HEAD")
        warnings: list[str] = []
        if not url:
            return ToolResult.fail(f"Remote '{remote}' is not configured for {r.name}.")
        if branch in ("HEAD", "") or "no branch" in branch:
            return ToolResult.fail("Detached HEAD — check out a branch before pushing.")
        if branch in protected:
            warnings.append(f"'{branch}' is a protected branch")
        if expected_remote and expected_remote.lower().rstrip("/").removesuffix(".git") not in url.lower():
            warnings.append(f"remote {url} is not the expected {expected_remote}")
        if st.get("behind"):
            warnings.append(f"branch is {st['behind']} commit(s) behind {st['upstream']} — pull first")
        uncommitted = len([p for _, p in st.get("files", []) if not _is_artifact(p)])
        if uncommitted:
            warnings.append(f"{uncommitted} uncommitted change(s) will NOT be pushed")
        rng = f"{st['upstream']}..HEAD" if st.get("upstream") else "HEAD"
        _, commits = _git(r, "log", "--oneline", rng, "-20") if st.get("upstream") else _git(r, "log", "--oneline", "-5")
        _, files = _git(r, "diff", "--stat", f"{st['upstream']}..HEAD") if st.get("upstream") else (0, "")
        if st.get("upstream") and not commits.strip():
            return ToolResult.fail(f"Nothing to push — {branch} is up to date with {st['upstream']}.")
        data = {"repo": str(r), "branch": branch, "remote": remote, "url": url, "head": head.strip(),
                "upstream": st.get("upstream", ""), "commits": commits.splitlines(), "warnings": warnings}
        text = (f"Push {r.name} · {branch} → {remote} ({url})\n"
                f"Commits:\n{commits or '(new branch)'}\n{files}\n" + ("Warnings: " + "; ".join(warnings) if warnings else "No warnings."))
        return ToolResult(True, text, data)

    def push(self, repo: str, approval) -> ToolResult:
        from agent.orchestration.permissions import Approval
        r, err = self._repo(repo)
        if err:
            return err
        pf = self.preflight(str(r))
        if not pf.ok:
            return pf
        d = pf.data
        scope = f"git_push:{d['repo']}:{d['branch']}:{d['url']}:{d['head']}"
        if not isinstance(approval, Approval) or not approval.consume(scope):
            return ToolResult.fail("Push refused: no valid approval for this exact repo/branch/remote/commit.", "denied")
        args = ["push", d["remote"], d["branch"]] if d["upstream"] else ["push", "-u", d["remote"], d["branch"]]
        code, out = _git(r, *args, timeout=180)
        if code:
            hint = " (authentication failed — check git credentials / `gh auth login`)" if re.search(r"(?i)auth|denied|403|credential", out) else ""
            return ToolResult.fail(f"git push failed{hint}:\n{out}")
        code2, remote_head = _git(r, "ls-remote", d["remote"], f"refs/heads/{d['branch']}", timeout=60)
        verified = code2 == 0 and remote_head.split()[:1] == [d["head"]]
        if not verified:
            return ToolResult(False, f"git push exited 0 but the remote branch does not show {d['head'][:8]} yet:\n{out}\n{remote_head}",
                              {**d, "verified": False}, "error")
        return ToolResult(True, f"Pushed {d['branch']} to {d['remote']} ({d['url']}); remote now at {d['head'][:8]} ✓",
                          {**d, "verified": True})

    def push_scope(self, pf_data: dict) -> str:
        return f"git_push:{pf_data['repo']}:{pf_data['branch']}:{pf_data['url']}:{pf_data['head']}"

    def delete_branch(self, repo: str, name: str) -> ToolResult:
        r, err = self._repo(repo)
        if err:
            return err
        code, out = _git(r, "branch", "-d", name)       # -d, not -D: refuses unmerged branches
        return ToolResult(code == 0, out)
