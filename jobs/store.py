"""
jobs/store.py — one private folder per user.

    ~/.judo/jobs/users/<user id>/
        profile.json        the parsed + user-confirmed candidate profile and preferences
        resumes/            the user's own resume files (copied in), and tailored versions
        jobs.json           jobs found for this user, with their match results
        applications.json   this user's application tracker

A UserStore can only ever see its own folder: the user id is validated (no path
tricks), and nothing here takes a path from outside except resume files the
user hands over. Writes are atomic (temp file + replace) so a crash never leaves
half a profile behind.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

ROOT = Path.home() / ".judo" / "jobs" / "users"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def current_user_id() -> str:
    """The person using this JUDO install (memory/config_manager keeps the id)."""
    try:
        from memory.config_manager import get_user_id
        return get_user_id()
    except Exception:
        return "local"


class UserStore:
    def __init__(self, user_id: str | None = None, root: Path | None = None):
        user_id = user_id or current_user_id()
        if not _ID.match(user_id or ""):
            raise ValueError("invalid user id")
        self.user_id = user_id
        self.dir = Path(root or ROOT) / user_id
        self.resumes = self.dir / "resumes"
        self.resumes.mkdir(parents=True, exist_ok=True)

    # ── json documents ─────────────────────────────────────────────────────
    def _path(self, name: str) -> Path:
        if not re.match(r"^[a-z_]+$", name):
            raise ValueError("bad document name")
        return self.dir / f"{name}.json"

    def load(self, name: str, default):
        try:
            return json.loads(self._path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def save(self, name: str, value) -> None:
        path = self._path(name)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=f".{name}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    # ── resume files ───────────────────────────────────────────────────────
    def add_resume(self, src: str | Path) -> Path:
        """Copy the user's resume into their folder (the original may move or be deleted)."""
        src = Path(src)
        safe = re.sub(r"[^A-Za-z0-9._ -]", "_", src.name)[:80] or "resume"
        dest = self.resumes / safe
        n = 1
        while dest.exists() and dest.read_bytes() != src.read_bytes():
            dest = self.resumes / f"{Path(safe).stem} ({n}){Path(safe).suffix}"
            n += 1
        if not dest.exists():
            shutil.copy2(src, dest)
        return dest

    def owns(self, path: str | Path) -> bool:
        """True only for files inside this user's own folder."""
        try:
            Path(path).resolve().relative_to(self.dir.resolve())
            return True
        except ValueError:
            return False
