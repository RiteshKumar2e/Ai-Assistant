"""
judo_browser/client.py — JUDO's side of the JUDO Browser remote control.

No Qt import here: this runs inside JUDO's own process. send() starts the
browser if it isn't running, then asks it to do one browser_control action.
It returns the browser's answer, or None when the browser can't do that
action — the caller (actions/browser_control.py) then uses its Edge path.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CONTROL_FILE = Path.home() / ".judo" / "browser" / "control.json"
ROOT = Path(__file__).resolve().parent.parent
START_WAIT = 30


def _info() -> dict | None:
    try:
        return json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def alive() -> bool:
    info = _info()
    if not info:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/ping", timeout=2) as r:
            return json.load(r).get("ok") is True
    except Exception:
        return False


def start(url: str = "") -> bool:
    """Launch JUDO Browser (windowed, no console) and wait until it answers."""
    if alive():
        return True
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    cmd = [str(pythonw if pythonw.exists() else exe), "-m", "judo_browser"] + ([url] if url else [])
    subprocess.Popen(cmd, cwd=str(ROOT), close_fds=True)
    deadline = time.monotonic() + START_WAIT
    while time.monotonic() < deadline:
        if alive():
            return True
        time.sleep(0.4)
    return False


def send(action: str, params: dict | None = None, timeout: float = 75) -> str | None:
    if not start():
        raise RuntimeError("JUDO Browser did not start")
    info = _info()
    body = json.dumps({"token": info["token"], "action": action, "params": params or {}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/cmd", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("result")
