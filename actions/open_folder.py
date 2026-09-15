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
import shutil
import subprocess
from pathlib import Path

from core import user_paths

_SYSTEM = platform.system()

_SHORTCUTS = {
    "desktop":   user_paths.desktop,
    "downloads": user_paths.downloads,
    "download":  user_paths.downloads,
    "documents": user_paths.documents,
    "document":  user_paths.documents,
    "pictures":  user_paths.pictures,
    "photos":    user_paths.pictures,
    "videos":    user_paths.videos,
    "music":     user_paths.music,
    "home":      Path.home,
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
    if p.is_absolute() and p.is_dir():
        return p.resolve()

    # A leading shortcut word anchors the rest ("desktop/Projects").
    parts = p.parts
    if parts and parts[0].lower() in _SHORTCUTS:
        anchored = _SHORTCUTS[parts[0].lower()]().joinpath(*parts[1:])
        if anchored.is_dir():
            return anchored.resolve()

    # Bare folder name ("Projects"). Desktop first — that is where a spoken
    # folder name almost always lives, and where this assistant creates them.
    for base in (user_paths.desktop(), Path.home(),
                 user_paths.documents(), user_paths.downloads()):
        candidate = base / expanded
        if candidate.is_dir():
            return candidate.resolve()

    if p.is_dir():
        return p.resolve()

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


def _find_vscode() -> str | None:
    """The `code` launcher — what right-click → 'Open with Code' runs."""
    found = shutil.which("code")
    if found:
        return found
    candidates: list[Path] = []
    if _SYSTEM == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            Path(local) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd",
            Path(r"C:\Program Files\Microsoft VS Code\bin\code.cmd"),
        ]
    elif _SYSTEM == "Darwin":
        candidates = [
            Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
            Path("/usr/local/bin/code"),
        ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _open_in_vscode(path: Path) -> tuple[bool, str]:
    exe = _find_vscode()
    if not exe:
        return False, "VS Code is not installed (no 'code' command found)."
    try:
        # A .cmd shim needs a shell on Windows; a real executable does not.
        subprocess.Popen(
            [exe, str(path)],
            shell=exe.lower().endswith((".cmd", ".bat")),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, f"Opened {path} in VS Code."
    except Exception as e:
        return False, f"Could not open VS Code: {e}"


def _open_in_claude_code(path: Path) -> tuple[bool, str]:
    """Claude Code ships two ways: a `claude` CLI, or the VS Code extension.

    Prefer the CLI in a terminal when it exists; otherwise open the folder in
    VS Code and press the extension's own shortcut (Ctrl/Cmd+Esc), which is
    the only way in when Claude Code is installed as an extension."""
    cli = shutil.which("claude")
    if cli:
        try:
            if _SYSTEM == "Windows":
                wt = shutil.which("wt")
                if wt:
                    subprocess.Popen([wt, "-d", str(path), "cmd", "/k", "claude"])
                else:
                    subprocess.Popen(
                        f'start "Claude Code" cmd /k "cd /d {path} && claude"',
                        shell=True,
                    )
            elif _SYSTEM == "Darwin":
                subprocess.Popen([
                    "osascript", "-e",
                    f'tell application "Terminal" to do script "cd {path!s} && claude"',
                    "-e", 'tell application "Terminal" to activate',
                ])
            else:
                subprocess.Popen([
                    "x-terminal-emulator", "-e",
                    f"bash -c 'cd {path!s}; claude; exec bash'",
                ])
            return True, f"Claude Code started in {path}."
        except Exception as e:
            return False, f"Could not start Claude Code: {e}"

    ok, msg = _open_in_vscode(path)
    if not ok:
        return False, "Claude Code is not installed (no 'claude' command, and VS Code is missing)."
    try:
        import time
        import pyautogui
        # VS Code needs to be focused before the extension shortcut lands.
        time.sleep(4)
        pyautogui.hotkey("command" if _SYSTEM == "Darwin" else "ctrl", "esc")
        return True, f"Opened {path} in VS Code and launched Claude Code."
    except Exception:
        return True, (f"Opened {path} in VS Code. Press Ctrl+Esc there to start "
                      "Claude Code — I could not send the shortcut myself.")


def open_folder(parameters=None, player=None) -> str:
    params  = parameters or {}
    folder  = (params.get("folder_path", "") or "").strip()
    open_in = (params.get("open_in", "") or "explorer").strip().lower()
    if not folder:
        return "No folder name or path provided."

    path = _resolve(folder)
    if path is None:
        return (
            f"Could not find a folder called '{folder}'. "
            f"Give a full path, or one of: Desktop, Downloads, Documents, Pictures, Videos, Music."
        )

    print(f"[open_folder] Opening: {path} (in {open_in})")
    if player:
        player.write_log(f"[open_folder] {open_in}: {path}")

    if open_in in ("vscode", "vs code", "code", "editor"):
        return _open_in_vscode(path)[1]
    if open_in in ("claude", "claude code", "claude_code", "agent"):
        return _open_in_claude_code(path)[1]

    if _open_in_file_manager(path):
        return f"Opened {path}."
    return f"Failed to open {path}."


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "open_folder",
    "description": (
        "Opens a folder — in the file manager, in VS Code, or in Claude Code. "
        "Use this whenever the user asks to open, show or browse a folder, by full path "
        "(e.g. 'C:\\Users\\Ritesh\\Projects') or by name ('Projects', 'Desktop', 'Downloads'). "
        "A bare folder name is looked for on the Desktop first. "
        "Set open_in='vscode' when they say to open it in VS Code / in the editor / "
        "'right click and open with code' — this does exactly what that menu item does. "
        "Set open_in='claude' when they ask for Claude Code (the coding agent) in that folder. "
        "Do NOT use this for uploaded files (file_processor) or for launching an app with no "
        "particular folder (open_app). To make a new folder first, call file_controller "
        "create_folder, then call this."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "folder_path": {
                "type": "STRING",
                "description": "The folder to open — a full path, or a name like 'Projects', 'Downloads', 'Desktop'.",
            },
            "open_in": {
                "type": "STRING",
                "description": (
                    "Where to open it: 'explorer' (file manager, default) | "
                    "'vscode' (open the folder in VS Code) | "
                    "'claude' (open it in Claude Code, the coding agent)."
                ),
            },
        },
        "required": ["folder_path"],
    },
    "handler": open_folder,
}
