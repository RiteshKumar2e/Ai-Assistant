"""
media_control.py — Universal play/pause/skip for whatever is currently
playing (Spotify, YouTube, any media/music player) — not tied to one app.

Uses the OS's own media-transport keys wherever possible, so it works with
anything that already responds to a hardware play/pause/next-track button:
no API key, no login, no per-app integration to maintain.

Volume/mute are deliberately NOT handled here — actions/computer_settings.py
owns system volume (undo support, precise pycaw-based levels on Windows,
consistent ±10% steps everywhere). Two tools both claiming volume_up/
volume_down/mute made Gemini pick between them non-deterministically, so a
"volume up" could land as this module's single, barely-perceptible key press
instead of computer_settings' full step — indistinguishable from "not
working" to the user. Keep this module to play/pause/skip only.
"""
import platform
import shutil
import subprocess

try:
    import pyautogui
    pyautogui.PAUSE = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

_OS = platform.system()

# Canonical transport key -> what the user might say
_ALIASES = {
    "play_pause": {"play", "pause", "play_pause", "toggle", "resume"},
    "next":       {"next", "skip", "next_track", "forward"},
    "previous":   {"previous", "prev", "back", "previous_track", "last_track"},
    "stop":       {"stop"},
}

# Canonical key -> pyautogui / Windows-Mac-Linux key name
_PYAUTOGUI_KEY = {
    "play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack",
    "stop": "stop",
}

_PLAYERCTL_CMD = {
    "play_pause": "play-pause", "next": "next", "previous": "previous", "stop": "stop",
}

_MAC_SCRIPT = {
    "play_pause": 'tell application "Spotify" to playpause',
    "next":       'tell application "Spotify" to next track',
    "previous":   'tell application "Spotify" to previous track',
}

_LABEL = {
    "play_pause": "Toggled play/pause.", "next": "Skipped to the next track.",
    "previous": "Went back to the previous track.", "stop": "Stopped playback.",
}


def _resolve(action: str) -> str | None:
    key = action.lower().strip().replace(" ", "_")
    for canonical, words in _ALIASES.items():
        if key in words:
            return canonical
    return None


def _linux_playerctl(canonical: str) -> bool:
    cmd = _PLAYERCTL_CMD.get(canonical)
    if not cmd or not shutil.which("playerctl"):
        return False
    try:
        subprocess.run(["playerctl", cmd], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def _mac_apple_events(canonical: str) -> bool:
    script = _MAC_SCRIPT.get(canonical)
    if not script:
        return False
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


PLUGIN = {
    "name": "media_control",
    "description": (
        "Controls playback of whatever is currently playing on the computer — Spotify, "
        "YouTube, or any other media/music player — using the system's play/pause/skip "
        "transport controls. Use for requests like 'pause the music', 'skip this song', "
        "'next track', 'go back a track', 'resume playback', 'stop the music'. Do NOT use "
        "this for volume or mute — use computer_settings for those. Do NOT use this to open "
        "an app or play a specific song by name — use open_app or youtube_video for that."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "play_pause | next | previous | stop",
            }
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters or {}).get("action", "")
    canonical = _resolve(str(action))
    if not canonical:
        return (
            f"Unknown media action: '{action}'. Try: play_pause, next, previous, stop. "
            f"For volume or mute, use computer_settings instead."
        )

    ok = False
    if _OS == "Linux":
        ok = _linux_playerctl(canonical)
    elif _OS == "Darwin":
        ok = _mac_apple_events(canonical)

    if not ok and _PYAUTOGUI:
        key = _PYAUTOGUI_KEY.get(canonical)
        try:
            pyautogui.press(key)
            ok = True
        except Exception as e:
            print(f"[MediaControl] key press failed: {e}")

    if not ok:
        return "Sir, I could not control media playback on this system."

    result = _LABEL.get(canonical, "Done.")
    if player:
        try:
            player.write_log(f"[media] {result}")
        except Exception:
            pass
    return result
