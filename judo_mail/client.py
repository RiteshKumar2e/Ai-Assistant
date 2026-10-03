"""
judo_mail/client.py — how JUDO opens its own mail app, and only that one.

"Mail kholo", "open Gmail", "inbox dikhao", "open outlook", go_to gmail.com …
all mean JUDO Mail — never Edge/Chrome, the Windows Mail app or a Gmail tab.
is_mail_app() / is_mail_url() decide that for actions/open_app.py and
actions/browser_control.py, and open_mail() does it like JUDO Browser does:
an already-running JUDO Mail is brought to the front, otherwise a fresh one is
started (windowed, no console) without waiting for it — JUDO's voice loop
never blocks on a mail window.

One copy only. JUDO Mail holds a Windows named mutex for its whole life
(claim_instance) and serves a tiny 127.0.0.1 control server (random port,
fresh token, ~/.judo/mail/control.json — same shape as JUDO Browser's) whose
only command is "focus". A second launch, by JUDO or by hand, finds the mutex,
asks the first copy to come forward and exits. No Qt import here: this module
also runs inside JUDO's own process.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CONTROL_FILE = Path.home() / ".judo" / "mail" / "control.json"
ROOT = Path(__file__).resolve().parent.parent
MUTEX_NAME = "Local\\Ritesh.JUDO.Mail"     # per Windows session, like the AppUserModelID

# ── what counts as "the mail app" ────────────────────────────────────────────

# words that name a mail app on their own ("gmail", "outlook", "मेल")
_MAIL_WORDS = {"mail", "mails", "email", "emails", "e-mail", "gmail", "inbox", "mailbox", "outlook",
               "hotmail", "ymail", "मेल", "ईमेल", "जीमेल", "इनबॉक्स"}
# filler around the name in what the model passes through ("mail kholo", "open my gmail app")
_FILLER = {"open", "opens", "kholo", "khol", "kholna", "kholiye", "kholdo", "do", "de", "dena", "karo", "kar",
           "chalu", "start", "launch", "run", "show", "dikhao", "dikha", "check", "my", "mera", "meri", "mere",
           "the", "app", "application", "client", "please", "plz", "zara", "jarvis", "website", "site", "page",
           "login", "sign", "in", "me", "pe", "par", "web", "com", "www", "https:", "http:","खोलो", "खोल", "दो", "मेरा", "मेरी"}
_MAIL_HOSTS = {"gmail.com", "mail.google.com", "inbox.google.com", "outlook.live.com", "outlook.office.com",
               "outlook.office365.com", "outlook.com", "hotmail.com", "mail.yahoo.com", "mail.proton.me",
               "mail.aol.com", "mail.zoho.com", "mail.zoho.in", "mail.com"}


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\s_/,.!?।]+", (text or "").lower()) if w]


_BRANDS = {"judo", "google", "yahoo", "windows", "microsoft", "proton", "zoho", "aol", "live", "office"}


def is_mail_app(name: str, strict: bool = False) -> bool:
    """'mail', 'Gmail', 'open my email', 'Mail kholo', 'Windows Mail', 'yahoo mail', 'JUDO Mail'…
    → True. 'Spotify', 'voicemail', 'Mailchimp' → False. Domains go through is_mail_url.
    strict: nothing but the mail app's name ("gmail", "open outlook") — for search queries,
    where "how to make a gmail account" is a real search, not a request to open mail."""
    name = (name or "").strip()
    if not name:
        return False
    if "." in name and " " not in name and is_mail_url(name):
        return True
    name = re.sub(r"\be[\s-]+mail", "email", name, flags=re.I).replace("-", " ")
    words = [w for w in _words(name) if w not in _FILLER]
    if strict:
        return bool(words) and all(w in _MAIL_WORDS or w in _BRANDS for w in words) \
            and any(w in _MAIL_WORDS for w in words)
    return any(w in _MAIL_WORDS for w in words)


def is_mail_url(url: str) -> bool:
    """A plain visit to a webmail site (gmail.com, mail.google.com, outlook.live.com, mail.yahoo.com,
    proton.me/mail …) → True. A compose link (Gmail's ?view=cm&to=…, compose=new, mailto:) stays a
    web page: send_email's fallback without a JUDO Mail account fills one in JUDO Browser."""
    raw = (url or "").strip()
    if not raw or raw.lower().startswith("mailto:"):
        return False
    if "." not in raw and "/" not in raw:          # a bare word handed to go_to: "gmail", "outlook"
        return is_mail_app(raw, strict=True)
    if " " in raw:                                 # a sentence, not an address
        return False
    p = urlparse(raw if "://" in raw else "https://" + raw)
    host = (p.hostname or "").lower().removeprefix("www.")
    path = p.path.lower()
    if host in ("proton.me", "protonmail.com"):
        mail = path.startswith("/mail") or path.startswith("/inbox")
    elif host == "live.com":
        mail = path.startswith("/mail")
    else:
        mail = host in _MAIL_HOSTS
    if not mail:
        return False
    q = parse_qs(p.query)
    compose = (q.get("view") == ["cm"] or "to" in q or "compose" in p.fragment.lower()
               or "compose" in path or "deeplink/compose" in path)
    return not compose


# ── JUDO's side: running? focus or start ─────────────────────────────────────

def _info() -> dict | None:
    try:
        return json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _mutex_exists() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenMutexW.restype = ctypes.c_void_p
    h = k32.OpenMutexW(0x00100000, False, MUTEX_NAME)          # SYNCHRONIZE
    if h:
        k32.CloseHandle(ctypes.c_void_p(h))
        return True
    return False


def _post(path: str, timeout: float = 3) -> dict | None:
    info = _info()
    if not info:
        return None
    try:
        body = json.dumps({"token": info.get("token", "")}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}{path}", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def alive() -> bool:
    """JUDO Mail is up and answering its control server."""
    info = _info()
    if not info:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/ping", timeout=2) as r:
            return json.load(r).get("ok") is True
    except Exception:
        return False


def running() -> bool:
    """A JUDO Mail process exists — even one still starting up (it holds the mutex before its window)."""
    return _mutex_exists() or alive()


def focus() -> bool:
    """Ask the running JUDO Mail to bring its window (or sign-in dialog) to the front."""
    info = _info()
    if info and sys.platform == "win32":
        try:   # Windows only lets a process take the foreground when the foreground process allows it
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(int(info.get("pid", -1)))
        except Exception:
            pass
    r = _post("/focus")
    return bool(r and r.get("ok"))


def start() -> bool:
    """Launch JUDO Mail (pythonw if there is one, so no console), cwd = repo root. Doesn't wait."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    try:
        subprocess.Popen([str(pythonw if pythonw.exists() else exe), "-m", "judo_mail"], cwd=str(ROOT),
                         close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError as e:
        print(f"[Mail] JUDO Mail launch failed: {e}")
        return False


def open_mail() -> str:
    """The one way JUDO opens mail. Returns a sentence for the user."""
    if running():
        if focus():
            return "JUDO Mail is already open — brought it to the front."
        return "JUDO Mail is already starting — it will appear in a moment."
    if start():
        return "Opening JUDO Mail."
    return "Could not open JUDO Mail."


# ── JUDO Mail's side: one copy, and a "focus" command ────────────────────────

_held = None    # the mutex handle, kept for the life of the process


def claim_instance() -> bool:
    """True when this process is the only JUDO Mail; False when another one already runs.
    The named mutex is atomic, so two launches at the same moment still give one winner."""
    global _held
    if sys.platform != "win32":
        return not alive()
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    h = k32.CreateMutexW(None, False, MUTEX_NAME)
    if h and ctypes.get_last_error() == 183:                 # ERROR_ALREADY_EXISTS
        k32.CloseHandle(ctypes.c_void_p(h))
        return False
    _held = h
    return True


def hand_over(wait: float = 15) -> bool:
    """A second launch: bring the first copy forward (it may still be starting), then let this one exit."""
    deadline = time.monotonic() + wait
    while True:
        if focus():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.3)


def serve(on_focus) -> None:
    """Start the control server; on_focus() is called on its thread (the app relays it to Qt)."""
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):   # keep the console quiet
            pass

        def _reply(self, obj, code: int = 200) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._reply({"ok": True, "pid": os.getpid()}) if self.path == "/ping" else self._reply({}, 404)

        def do_POST(self):
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except ValueError:
                return self._reply({"error": "bad json"}, 400)
            if self.path != "/focus" or not hmac.compare_digest(str(req.get("token", "")), token):
                return self._reply({"error": "forbidden"}, 403)
            on_focus()
            self._reply({"ok": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="judo-mail-control", daemon=True).start()
    CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONTROL_FILE.write_text(json.dumps({"port": server.server_address[1], "token": token, "pid": os.getpid()}),
                            encoding="utf-8")


def cleanup() -> None:
    try:
        if json.loads(CONTROL_FILE.read_text(encoding="utf-8")).get("pid") == os.getpid():
            CONTROL_FILE.unlink()
    except Exception:
        pass


def bring_to_front(hwnd: int) -> bool:
    """Restore and foreground a window of this process. Qt's activateWindow() alone only flashes
    the taskbar button when another app has the focus, so borrow the foreground thread's input."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    u, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    h = wintypes.HWND(hwnd)
    u.ShowWindow(h, 9 if u.IsIconic(h) else 5)               # SW_RESTORE / SW_SHOW
    if u.SetForegroundWindow(h):
        return True
    fg_thread = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
    me = k32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != me and u.AttachThreadInput(me, fg_thread, True))
    try:
        u.BringWindowToTop(h)
        return bool(u.SetForegroundWindow(h))
    finally:
        if attached:
            u.AttachThreadInput(me, fg_thread, False)
