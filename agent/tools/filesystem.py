"""
agent/tools/filesystem.py — file access confined to the configured workspaces.

Rules the model cannot argue its way past:
  * a path outside every workspace is reported as outside; the executor turns
    that into a confirmation (orchestration/permissions.py), never a silent read
  * secret files (.env, keys, tokens, cookie stores — tools/base.is_secret_path)
    are refused outright, and everything returned is passed through redact()
  * delete goes to the recycle bin (send2trash) when available, and is gated as
    DESTRUCTIVE in every confirmation mode
  * writes and edits register an undo entry, so "undo" in the voice session
    reverses them like any other JUDO file action
"""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from agent.tools.base import FileController, ToolResult, is_secret_path, redact

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", ".next",
              ".pytest_cache", ".mypy_cache", ".idea", ".vscode", "target", ".gradle", "coverage", ".dart_tool"}
_MAX_READ   = 200_000
_MAX_SEARCH = 1_000_000     # skip files bigger than this when grepping
_MAX_HITS   = 80


def _undo(label: str, fn) -> None:
    try:
        from core.undo import push_undo
        push_undo(label, fn)
    except Exception:
        pass


def _is_binary(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return b"\0" in f.read(2048)
    except OSError:
        return True


class WorkspaceFS(FileController):
    def __init__(self, workspaces: list[Path], cwd_provider=None):
        self.workspaces = [Path(w).resolve() for w in workspaces]
        self._cwd = cwd_provider or (lambda: None)     # current project, from task state
        self.allowed_files: set[Path] = set()          # files the user explicitly handed over (HUD attachment)

    # ── path policy ──────────────────────────────────────────────────────────
    def resolve(self, path: str) -> Path:
        p = Path(os.path.expandvars(str(path or ".").strip().strip('"'))).expanduser()
        if not p.is_absolute():
            p = Path(self._cwd() or (self.workspaces[0] if self.workspaces else Path.cwd())) / p
        return p.resolve()

    def inside(self, p: Path) -> bool:
        p = Path(p).resolve()
        return p in self.allowed_files or any(p == w or w in p.parents for w in self.workspaces)

    def outside(self, path: str) -> bool:
        return not self.inside(self.resolve(path))

    def _guard(self, p: Path) -> ToolResult | None:
        if is_secret_path(p):
            return ToolResult.fail(f"{p.name} looks like a secret/credential file — I don't read or change those.", "denied")
        return None

    # ── read ─────────────────────────────────────────────────────────────────
    def list_dir(self, path: str = ".") -> ToolResult:
        p = self.resolve(path)
        if not p.is_dir():
            return ToolResult.fail(f"Not a folder: {p}")
        rows = []
        for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))[:300]:
            rows.append(f"{c.name}/" if c.is_dir() else f"{c.name}  ({c.stat().st_size:,} B)")
        return ToolResult(True, f"{p}\n" + "\n".join(rows), {"path": str(p)})

    def tree(self, path: str = ".", depth: int = 3) -> ToolResult:
        root = self.resolve(path)
        if not root.is_dir():
            return ToolResult.fail(f"Not a folder: {root}")
        lines, count = [str(root)], 0
        def walk(d: Path, lvl: int):
            nonlocal count
            try:
                kids = sorted(d.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
            except OSError:
                return
            for c in kids:
                if count > 400 or c.name in _SKIP_DIRS:
                    continue
                count += 1
                lines.append("  " * lvl + (c.name + "/" if c.is_dir() else c.name))
                if c.is_dir() and lvl + 1 < depth:
                    walk(c, lvl + 1)
        walk(root, 1)
        return ToolResult(True, "\n".join(lines), {"path": str(root)})

    def find_files(self, pattern: str, path: str = ".") -> ToolResult:
        root, hits = self.resolve(path), []
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in _SKIP_DIRS]
            hits += [str(Path(dp, f).relative_to(root)) for f in fns if fnmatch.fnmatch(f.lower(), pattern.lower())]
            if len(hits) >= 200:
                break
        return ToolResult(True, "\n".join(hits[:200]) or f"No files matching {pattern}", {"count": len(hits)})

    def search(self, pattern: str, path: str = ".", glob: str = "") -> ToolResult:
        root = self.resolve(path)
        try:
            rx = re.compile(pattern, re.I)
        except re.error:
            rx = re.compile(re.escape(pattern), re.I)
        hits: list[str] = []
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in _SKIP_DIRS]
            for f in fns:
                fp = Path(dp, f)
                if (glob and not fnmatch.fnmatch(f, glob)) or is_secret_path(fp):
                    continue
                try:
                    if fp.stat().st_size > _MAX_SEARCH or _is_binary(fp):
                        continue
                    for i, line in enumerate(fp.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if rx.search(line):
                            hits.append(f"{fp.relative_to(root)}:{i}: {line.strip()[:200]}")
                            if len(hits) >= _MAX_HITS:
                                break
                except OSError:
                    continue
                if len(hits) >= _MAX_HITS:
                    break
            if len(hits) >= _MAX_HITS:
                break
        out = redact("\n".join(hits)) or f"No matches for {pattern!r} in {root}"
        return ToolResult(True, out, {"count": len(hits), "root": str(root)})

    def read_file(self, path: str, start: int = 1, end: int | None = None) -> ToolResult:
        p = self.resolve(path)
        if (g := self._guard(p)):
            return g
        if not p.is_file():
            return ToolResult.fail(f"No such file: {p}")
        if _is_binary(p):
            return ToolResult.fail(f"{p.name} is a binary file.")
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, int(start or 1)); end = min(len(lines), int(end or len(lines)))
        body = "\n".join(f"{i:>5}  {lines[i-1]}" for i in range(start, end + 1))
        if len(body) > _MAX_READ:
            body = body[:_MAX_READ] + f"\n…[truncated — {len(lines)} lines total; read a smaller range]"
        return ToolResult(True, f"{p} (lines {start}-{end} of {len(lines)})\n{redact(body)}", {"path": str(p), "lines": len(lines)})

    # ── write ────────────────────────────────────────────────────────────────
    def write_file(self, path: str, content: str) -> ToolResult:
        p = self.resolve(path)
        if (g := self._guard(p)):
            return g
        old = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        if old is None:
            _undo(f"create {p.name}", lambda: (p.unlink(missing_ok=True), f"Removed {p.name}")[1])
        elif len(old) < 1_000_000:
            _undo(f"write {p.name}", lambda: (p.write_text(old, encoding="utf-8"), f"Restored {p.name}")[1])
        verb = "Created" if old is None else "Overwrote"
        return ToolResult(True, f"{verb} {p} ({len(content):,} chars)", {"path": str(p), "modified": str(p)})

    def edit_file(self, path: str, old: str, new: str) -> ToolResult:
        """Exact-string replacement; `old` must occur exactly once so an edit can
        never land somewhere the model didn't intend."""
        p = self.resolve(path)
        if (g := self._guard(p)):
            return g
        if not p.is_file():
            return ToolResult.fail(f"No such file: {p}")
        text = p.read_text(encoding="utf-8", errors="replace")
        n = text.count(old) if old else 0
        if n != 1:
            return ToolResult.fail(f"`old` text found {n} times in {p.name} — it must match exactly once. "
                                   "Re-read the file and copy the exact lines, including indentation.")
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        _undo(f"edit {p.name}", lambda: (p.write_text(text, encoding="utf-8"), f"Reverted {p.name}")[1])
        return ToolResult(True, f"Edited {p} (-{old.count(chr(10)) + 1}/+{new.count(chr(10)) + 1} lines)",
                          {"path": str(p), "modified": str(p)})

    def rename(self, src: str, dst: str) -> ToolResult:
        a, b = self.resolve(src), self.resolve(dst)
        if (g := self._guard(a) or self._guard(b)):
            return g
        if not a.exists():
            return ToolResult.fail(f"No such path: {a}")
        if b.exists():
            return ToolResult.fail(f"{b} already exists.")
        a.rename(b)
        _undo(f"rename {a.name}", lambda: (b.rename(a), f"Renamed back to {a.name}")[1])
        return ToolResult(True, f"Renamed {a} → {b}", {"modified": str(b)})

    def delete(self, path: str) -> ToolResult:
        p = self.resolve(path)
        if (g := self._guard(p)):
            return g
        if not p.exists():
            return ToolResult.fail(f"No such path: {p}")
        if any(p == w for w in self.workspaces):
            return ToolResult.fail("Refusing to delete a workspace root.", "denied")
        try:
            from send2trash import send2trash
            send2trash(str(p))
            return ToolResult(True, f"Moved {p} to the recycle bin.", {"modified": str(p)})
        except ImportError:
            return ToolResult.fail("send2trash is not installed, so I won't delete permanently.", "unavailable")
