"""
core/user_paths.py — the user's real Desktop / Downloads / Documents / ...

`Path.home() / "Desktop"` is wrong on any Windows machine where OneDrive (or
a domain policy) has redirected the known folders: the visible Desktop becomes
%USERPROFILE%\\OneDrive\\Desktop while an empty %USERPROFILE%\\Desktop is often
left behind. Writing to the stale one "works" — the file just never appears
where the user is looking, which is indistinguishable from the assistant
having ignored them.

Windows keeps the authoritative locations in the registry; Linux has the XDG
user-dirs config; macOS never redirects these. Each lookup falls back to the
old home-relative guess so a locked-down or unusual machine degrades to the
previous behaviour instead of failing.
"""
from __future__ import annotations

import os
import platform
from functools import lru_cache
from pathlib import Path

_OS = platform.system()

# HKCU\...\Explorer\User Shell Folders value names. Desktop/Personal are plain
# names; Downloads is only addressable by its known-folder GUID.
_WIN_KEYS = {
    "desktop":   "Desktop",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "documents": "Personal",
    "pictures":  "My Pictures",
    "music":     "My Music",
    "videos":    "My Video",
}

_XDG_VARS = {
    "desktop":   "XDG_DESKTOP_DIR",
    "downloads": "XDG_DOWNLOAD_DIR",
    "documents": "XDG_DOCUMENTS_DIR",
    "pictures":  "XDG_PICTURES_DIR",
    "music":     "XDG_MUSIC_DIR",
    "videos":    "XDG_VIDEOS_DIR",
}

_FALLBACK_NAMES = {
    "desktop":   "Desktop",
    "downloads": "Downloads",
    "documents": "Documents",
    "pictures":  "Pictures",
    "music":     "Music",
    "videos":    "Videos",
}


@lru_cache(maxsize=None)
def _lookup(kind: str) -> Path:
    fallback = Path.home() / _FALLBACK_NAMES[kind]

    if _OS == "Windows":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                raw, _ = winreg.QueryValueEx(key, _WIN_KEYS[kind])
            resolved = Path(os.path.expandvars(raw))
            if resolved.is_dir():
                return resolved
        except Exception:
            pass
    elif _OS == "Linux":
        xdg = os.environ.get(_XDG_VARS[kind], "")
        if xdg:
            resolved = Path(os.path.expandvars(xdg)).expanduser()
            if resolved.is_dir():
                return resolved

    return fallback


def desktop() -> Path:
    return _lookup("desktop")


def downloads() -> Path:
    return _lookup("downloads")


def documents() -> Path:
    return _lookup("documents")


def pictures() -> Path:
    return _lookup("pictures")


def music() -> Path:
    return _lookup("music")


def videos() -> Path:
    return _lookup("videos")
