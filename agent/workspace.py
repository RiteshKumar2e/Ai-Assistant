"""
agent/workspace.py — find "my AI Assistant project" on disk.

Scans the configured workspaces (two levels deep) for project roots — folders
with .git, package.json, pyproject.toml, … — and scores each against what the
user said by folder name AND git remote name, so a repo cloned into a folder
called "Judo" is still found as "my AI Assistant repo" when its remote is
github.com/<you>/Ai-Assistant.
"""
from __future__ import annotations

import configparser
import re
from difflib import SequenceMatcher
from pathlib import Path

_MARKERS = (".git", "package.json", "pyproject.toml", "requirements.txt", "setup.py", "Cargo.toml", "go.mod",
            "pom.xml", "build.gradle", "pubspec.yaml", "composer.json", "Gemfile", "*.sln", "CMakeLists.txt")
_SKIP = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".git", "AppData", "Library"}
_FILLER = re.compile(r"\b(my|the|a|an|project|repo|repository|folder|app|code|codebase|open|in|vs|vscode)\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def is_project(p: Path) -> bool:
    return any((any(p.glob(m)) if "*" in m else (p / m).exists()) for m in _MARKERS)


def git_remote_name(p: Path) -> str:
    cfg = p / ".git" / "config"
    if not cfg.is_file():
        return ""
    cp = configparser.ConfigParser(strict=False)
    try:
        cp.read(cfg, encoding="utf-8")
        url = cp.get('remote "origin"', "url", fallback="")
    except Exception:
        return ""
    m = re.search(r"[/:]([^/:]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else ""


def find_projects(workspaces: list[Path], depth: int = 2) -> list[Path]:
    found: list[Path] = []
    def walk(d: Path, lvl: int):
        try:
            kids = [c for c in d.iterdir() if c.is_dir() and c.name not in _SKIP and not c.name.startswith(".")]
        except OSError:
            return
        for c in kids:
            if is_project(c):
                found.append(c)
            elif lvl < depth:
                walk(c, lvl + 1)
    for w in workspaces:
        if is_project(w):
            found.append(w)
        walk(w, 1)
    return list(dict.fromkeys(p.resolve() for p in found))


def score(query: str, p: Path) -> float:
    q = _norm(_FILLER.sub(" ", query))
    if not q:
        return 0.0
    best = 0.0
    for name in (p.name, git_remote_name(p)):
        n = _norm(name)
        if not n:
            continue
        s = 1.0 if n == q else 0.9 if (q in n or n in q) and min(len(q), len(n)) >= 3 else SequenceMatcher(None, q, n).ratio()
        best = max(best, s)
    return best


def resolve_project(query: str, workspaces: list[Path]) -> tuple[Path | None, list[tuple[float, Path]]]:
    """(best match or None, ranked candidates). An explicit existing path wins."""
    raw = (query or "").strip().strip('"')
    if raw and Path(raw).expanduser().is_dir():
        return Path(raw).expanduser().resolve(), []
    ranked = sorted(((score(raw, p), p) for p in find_projects(workspaces)), key=lambda t: -t[0])
    ranked = [t for t in ranked if t[0] >= 0.55][:6]
    if not ranked:
        return None, []
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.05 and ranked[0][0] < 1.0:
        return None, ranked          # ambiguous — ask which one
    return ranked[0][1], ranked
