"""
open_folder.py — Opens a folder in the OS's native file manager
(File Explorer on Windows, Finder on macOS, the default file manager on Linux).

Separate from open_app.py on purpose: open_app launches applications and only
opens a path opportunistically (via Start Menu search or a literal "C:\\..." with
a colon in it). This action is a dedicated, reliable folder opener — it resolves
common shorthand names (Desktop, Downloads, Documents, ...) itself instead of
relying on OS search, and reports a clear failure instead of silently doing
nothing when the folder doesn't exist.
"""
import os
import platform
import subprocess
from pathlib import Path

_SYSTEM = platform.system()

_SHORTCUTS = {
    "desktop":   lambda: Path.home() / "Desktop",
    "downloads": lambda: Path.home() / "Downloads",
    "download":  lambda: Path.home() / "Downloads",
    "documents": lambda: Path.home() / "Documents",
    "document":  lambda: Path.home() / "Documents",
    "pictures":  lambda: Path.home() / "Pictures",
    "photos":    lambda: Path.home() / "Pictures",
    "videos":    lambda: Path.home() / "Videos",
    "music":     lambda: Path.home() / "Music",
    "home":      lambda: Path.home(),
}


def _resolve(raw: str) -> Path | None:
    """Turn what the user said into a real, existing directory — or None."""
    cleaned = raw.strip().strip('"').strip("'")

    key = cleaned.lower()
    for suffix in (" folder", " directory"):
        if key.endswith(suffix):
            key = key[: -len(suffix)].strip()
    if key in _SHORTCUTS:
        return _SHORTCUTS[key]()

    expanded = os.path.expandvars(cleaned)
    p = Path(expanded).expanduser()
    if p.is_dir():
        return p.resolve()

    # Bare folder name ("Projects") — try it relative to the user's home dir.
    p2 = Path.home() / expanded
    if p2.is_dir():
        return p2.resolve()

    return None


def _open_in_file_manager(path: Path) -> bool:
    try:
        if _SYSTEM == "Windows":
            subprocess.Popen(["explorer", str(path)])
        elif _SYSTEM == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception as e:
        print(f"[open_folder] launch failed: {e}")
        return False


def open_folder(parameters=None, player=None) -> str:
    folder = (parameters or {}).get("folder_path", "").strip()
    if not folder:
        return "No folder name or path provided."

    path = _resolve(folder)
    if path is None:
        return (
            f"Could not find a folder called '{folder}'. "
            f"Give a full path, or one of: Desktop, Downloads, Documents, Pictures, Videos, Music."
        )

    print(f"[open_folder] Opening: {path}")
    if player:
        player.write_log(f"[open_folder] {path}")

    if _open_in_file_manager(path):
        return f"Opened {path}."
    return f"Failed to open {path}."


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "open_folder",
    "description": (
        "Opens a folder in the system's file manager (File Explorer / Finder / file manager). "
        "Use this whenever the user asks to open, show, or browse a specific folder — either by "
        "full path (e.g. 'C:\\Users\\Ritesh\\Projects') or by a common shorthand (Desktop, Downloads, "
        "Documents, Pictures, Videos, Music, Home). Do NOT use this for opening files or applications — "
        "use file_processor for uploaded files and open_app for launching programs."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "folder_path": {
                "type": "STRING",
                "description": "The folder to open — a full path, or a shorthand like 'Downloads', 'Desktop', 'Documents'.",
            }
        },
        "required": ["folder_path"],
    },
    "handler": open_folder,
}
