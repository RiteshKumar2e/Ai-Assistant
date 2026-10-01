
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import platform
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
)

from core import user_paths
from actions import _uia_browser as uia

_OS = platform.system()   # "Windows" | "Darwin" | "Linux"

def _normalize_url(url: str) -> str:
    """
    Bare words like "instagram" → "https://instagram.com"
    Domains like "instagram.com" → "https://instagram.com"
    Full URLs pass through unchanged.
    """
    url = url.strip()
    if not url:
        return "about:blank"
    if "://" in url:
        return url
    # No dot at all → assume .com  (e.g. "instagram" → "instagram.com")
    if "." not in url:
        url = url + ".com"
    return "https://" + url


def _user_agent() -> str:
    if _OS == "Windows":
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    if _OS == "Darwin":
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    return (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


def _firefox_profile_dir() -> Optional[str]:
    home = Path.home()

    if _OS == "Windows":
        base = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"
    elif _OS == "Darwin":
        base = home / "Library" / "Application Support" / "Firefox"
    else:
        base = home / ".mozilla" / "firefox"

    ini = base / "profiles.ini"
    if not ini.exists():
        return None

    current: dict[str, str] = {}
    default_path: Optional[str] = None

    for line in ini.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("["):
            p = current.get("Path", "")
            if p and current.get("Default") == "1":
                is_rel = current.get("IsRelative", "1") == "1"
                default_path = str(base / p) if is_rel else p
            current = {}
        elif "=" in line:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()

    p = current.get("Path", "")
    if p and current.get("Default") == "1":
        is_rel = current.get("IsRelative", "1") == "1"
        default_path = str(base / p) if is_rel else p

    if default_path and Path(default_path).exists():
        print(f"[Browser] Firefox real profile: {default_path}")
        return default_path
    return None

def _find_opera_windows() -> Optional[str]:
    local  = os.environ.get("LOCALAPPDATA", "")
    prog   = os.environ.get("PROGRAMFILES", "")
    prog86 = os.environ.get("PROGRAMFILES(X86)", "")

    candidates = [
        Path(local)  / "Programs" / "Opera"    / "opera.exe",
        Path(local)  / "Programs" / "Opera GX" / "opera.exe",
        Path(prog)   / "Opera"    / "opera.exe",
        Path(prog86) / "Opera"    / "opera.exe",
    ]
    for p in candidates:
        if p.exists():
            print(f"[Browser] Opera found at: {p}")
            return str(p)

    try:
        import winreg
        keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe",
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\opera\shell\open\command",
        ]
        for key_path in keys:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        print(f"[Browser] Opera found via registry: {exe}")
                        return exe
                except Exception:
                    continue
    except Exception:
        pass

    return shutil.which("opera") or None

def _find_exe_windows(prog_name: str) -> Optional[str]:
    try:
        import winreg
        paths_to_try = [
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{prog_name}.exe",
            rf"SOFTWARE\Clients\StartMenuInternet\{prog_name}\shell\open\command",
        ]
        for key_path in paths_to_try:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    return None

_BROWSER_SPECS: dict[str, dict] = {
    "Windows": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": []},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox.exe"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera.exe"],  "special": "opera_windows"},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": [],             "special": "opera_windows"},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave.exe"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi.exe"]},
        "safari":   None,
    },
    "Darwin": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": ["microsoft-edge"]},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi"]},
        "safari":   {"engine": "webkit",   "channel": None,      "bins": []},
    },
    "Linux": {
        "chrome":   {"engine": "chromium", "channel": None,
                     "bins": ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]},
        "edge":     {"engine": "chromium", "channel": None,
                     "bins": ["microsoft-edge", "microsoft-edge-stable"]},
        "firefox":  {"engine": "firefox",  "channel": None, "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "operagx":  {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "brave":    {"engine": "chromium", "channel": None, "bins": ["brave-browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None, "bins": ["vivaldi-stable", "vivaldi"]},
        "safari":   None,
    },
}

_ALIASES: dict[str, str] = {
    "google chrome":   "chrome",
    "google-chrome":   "chrome",
    "microsoft edge":  "edge",
    "ms edge":         "edge",
    "msedge":          "edge",
    "mozilla firefox": "firefox",
    "opera gx":        "operagx",
    "opera_gx":        "operagx",
}


def _resolve_browser(name: str) -> dict | None:
    name   = _ALIASES.get(name.lower().strip(), name.lower().strip())
    os_map = _BROWSER_SPECS.get(_OS, {})
    spec   = os_map.get(name)
    if spec is None:
        return None

    engine  = spec["engine"]
    channel = spec.get("channel")
    bins    = spec.get("bins", [])
    exe     = None

    if spec.get("special") == "opera_windows":
        exe = _find_opera_windows()
        if not exe:
            print(f"[Browser] ⚠️  Opera executable not found on Windows.")
        return {"engine": engine, "exe": exe, "channel": channel}

    for b in bins:
        found = shutil.which(b)
        if found:
            exe = found
            break

    if not exe and _OS == "Darwin":
        app_names = {
            "chrome":  ["Google Chrome.app"],
            "edge":    ["Microsoft Edge.app"],
            "firefox": ["Firefox.app"],
            "opera":   ["Opera.app", "Opera GX.app"],
            "brave":   ["Brave Browser.app"],
            "vivaldi": ["Vivaldi.app"],
        }
        for app in app_names.get(name, []):
            app_dir = Path("/Applications") / app / "Contents" / "MacOS"
            if app_dir.exists():
                found_bins = list(app_dir.iterdir())
                if found_bins:
                    exe = str(found_bins[0])
                    break

    if not exe and _OS == "Windows" and not channel:
        exe = _find_exe_windows(name)

    return {"engine": engine, "exe": exe, "channel": channel}


def _detect_default_browser() -> str:
    try:
        if _OS == "Windows":
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations"
                r"\UrlAssociations\http\UserChoice",
            )
            prog_id = winreg.QueryValueEx(k, "ProgId")[0].lower()
            winreg.CloseKey(k)
            for kw in ("edge", "firefox", "opera", "brave", "vivaldi", "chrome"):
                if kw in prog_id:
                    return kw
        elif _OS == "Darwin":
            out = subprocess.run(
                ["defaults", "read",
                 "com.apple.LaunchServices/com.apple.launchservices.secure",
                 "LSHandlers"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "safari", "chrome", "edge"):
                if kw in out:
                    return kw
        elif _OS == "Linux":
            out = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "chrome", "edge"):
                if kw in out:
                    return kw
    except Exception:
        pass
    return "chrome"


def _preferred_browser() -> str:
    """The browser JUDO uses when the user doesn't name one. The user asked for
    Edge (ChatGPT and everything else there; Chrome left alone), so Edge wins
    whenever it is installed. Override with "browser" in config/api_keys.json."""
    try:
        cfg = json.loads((Path(__file__).resolve().parent.parent / "config" / "api_keys.json").read_text(encoding="utf-8"))
        choice = str(cfg.get("browser", "")).lower().strip()
        if choice:
            return _ALIASES.get(choice, choice)
    except Exception:
        pass
    return "edge" if _resolve_exe_path("edge") else _detect_default_browser()


def open_in_browser(url: str) -> bool:
    """Open a URL in JUDO Browser as a new tab — for actions that just need to
    show a page. Never opens Edge/Chrome (unless config "default_browser" says so)."""
    if _judo_browser_default():
        try:
            from judo_browser import client as judo_client
            return judo_client.send("new_tab", {"url": _normalize_url(url)}) is not None
        except Exception as e:
            print(f"[Browser] JUDO Browser could not open {url}: {e}")
            return False
    name = _preferred_browser()
    exe = _resolve_exe_path(name)
    return bool(exe) and _open_in_running(exe, _normalize_url(url))


_SEARCH_ENGINES: dict[str, str] = {
    "google":     "https://www.google.com/search?q=",
    "bing":       "https://www.bing.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
    "yandex":     "https://yandex.com/search/?text=",
}

_MAC_APP_NAMES: dict[str, str] = {
    "chrome":  "Google Chrome",
    "edge":    "Microsoft Edge",
    "firefox": "Firefox",
    "opera":   "Opera",
    "operagx": "Opera GX",
    "brave":   "Brave Browser",
    "vivaldi": "Vivaldi",
    "safari":  "Safari",
}

# Windows registry lookup names for browsers whose spec has no explicit binary
_WIN_EXE_HINTS: dict[str, str] = {"chrome": "chrome", "edge": "msedge"}

# ── CDP attach-to-real-browser support ───────────────────────────────────────
# Chromium-based browsers only read --remote-debugging-port at startup, and a
# single-instance browser that's already running just hands a second launch's
# args to the existing window and exits — so Playwright's launch_persistent_
# context() can never attach to whatever Chrome/Edge/etc. the user already has
# open. Instead of falling back to a second, separate, logged-out profile, we
# talk to the user's REAL browser over CDP: reuse it if it's already
# debuggable, otherwise (re)launch that exact browser on its real profile with
# the debug flag on, so there is only ever ONE window and it behaves exactly
# like the browser the user is used to (same history, cookies, extensions).
_CDP_PORTS: dict[str, int] = {
    "chrome": 9222, "edge": 9223, "brave": 9224,
    "vivaldi": 9225, "opera": 9226, "operagx": 9227,
}

def _cdp_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5):
            return True
    except Exception:
        return False


# ── The user's browser is never closed, killed or restarted by JUDO ──────────
# This module used to force-kill a Chromium browser that was running without
# remote debugging and relaunch it with the debug flag. That force-kill is a
# crash as far as the browser is concerned (the profile is left marked
# "Crashed", the open window disappears, and it can come back on a different
# profile), and on Chrome 136+ it could not even work: Chrome ignores
# --remote-debugging-port on the default user-data dir. So it is gone for good.
# Navigation opens tabs in the window that is already open (_open_in_running);
# page interaction attaches only when the browser is ALREADY debuggable.
_NO_AUTOMATION = (
    "{name} is open without automation access, and I never close or restart your browser to get it. "
    "I can open pages in it as new tabs (go_to / search / new_tab), but I can't click, type or read inside a page "
    "(Windows UI Automation of the open window was not available either). To allow that, start {name} yourself "
    "with remote debugging (e.g. a separate profile: {name} --remote-debugging-port={port} --user-data-dir=<folder>)."
)


class _NoAutomation(RuntimeError):
    """The browser has no CDP port and JUDO won't restart it to get one — the
    dispatcher then drives the open window through UI Automation instead."""


def _open_in_running(exe: str, url: str) -> bool:
    """`<browser> <url>` while the browser is running hands the URL to that same
    instance as a new tab (single-instance lock) — nothing is closed or
    restarted. If it isn't running, this simply starts it, like its icon would."""
    try:
        subprocess.Popen([exe, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        print(f"[Browser] Could not hand {url} to {exe}: {e}")
        return False


def _resolve_exe_path(name: str) -> Optional[str]:
    """Best-effort path to the real browser executable, for spawning it
    ourselves with the CDP debug flag (as opposed to Playwright's own
    launch(), which doesn't expose a path for channel-resolved browsers)."""
    if _OS == "Darwin":
        app = _MAC_APP_NAMES.get(name)
        if app:
            app_dir = Path("/Applications") / f"{app}.app" / "Contents" / "MacOS"
            if app_dir.exists():
                bins = [b for b in app_dir.iterdir() if b.is_file()]
                if bins:
                    return str(bins[0])
    spec = _resolve_browser(name)
    exe = spec.get("exe") if spec else None
    if not exe and _OS == "Windows":
        if name in ("opera", "operagx"):
            exe = _find_opera_windows()
        else:
            exe = _find_exe_windows(_WIN_EXE_HINTS.get(name, name))
    return exe


def _looks_like_dead_context(e: Exception) -> bool:
    """True for Playwright's family of 'the underlying browser/context/page is
    already gone' errors (e.g. 'Target page, context or browser has been
    closed') — as opposed to an ordinary navigation/selector error that should
    just be reported, not treated as a reason to relaunch the whole browser."""
    msg = str(e).lower()
    return "closed" in msg and ("target page" in msg or "context" in msg or "browser" in msg)


_INIT_TIMEOUT = 45  # seconds to wait for the Playwright driver process to come up


class _BrowserSession:
    """
    A full session for one browser instance.
    All browsers open on the real profile via launch_persistent_context.
    """

    def __init__(self, browser_name: str):
        self.browser_name = browser_name
        self._spec        = _resolve_browser(browser_name)

        self._loop:    asyncio.AbstractEventLoop | None = None
        self._thread:  threading.Thread | None          = None
        self._ready    = threading.Event()

        self._pw:      Playwright     | None = None
        self._context: BrowserContext | None = None
        self._page:    Page           | None = None

        # Set when self._context comes from connect_over_cdp() — i.e. we are
        # driving the user's ACTUAL browser (real profile, possibly windows/
        # tabs they opened themselves) rather than a context Playwright itself
        # launched. Closing that context/browser must be reserved for an
        # explicit user "close" request — never as a side effect of error
        # recovery, or a JUDO hiccup would slam the user's whole browser shut.
        self._cdp_browser: Browser | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"BrowserThread-{self.browser_name}",
        )
        self._thread.start()
        # If _async_init() (spinning up the Playwright driver) hasn't finished
        # within _INIT_TIMEOUT — a loaded system, first-run driver startup —
        # this used to return anyway and let the caller use self._pw while it
        # was still None, surfacing as a baffling "'NoneType' object has no
        # attribute 'chromium'" instead of a clear error. The registry never
        # caches this session on failure (see _get_or_create), so the next
        # call just tries again fresh. Raised from 20s to 45s after this fired
        # on a real machine where the driver process (normally ~2s) got starved
        # by other apps at 70-85% CPU/RAM — the process just needed more time,
        # not a different fix.
        if not self._ready.wait(timeout=_INIT_TIMEOUT):
            raise RuntimeError(
                f"Browser automation engine for '{self.browser_name}' did not "
                f"initialize within {_INIT_TIMEOUT}s (system under heavy load) — please try again."
            )

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_init())
        self._ready.set()
        self._loop.run_forever()

    async def _async_init(self):
        self._pw = await async_playwright().start()

    def run(self, coro, timeout: int = 60) -> str:
        if not self._loop:
            raise RuntimeError(f"Session for '{self.browser_name}' not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self):
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_close(), self._loop).result(10)

    async def _async_close(self):
        # Attached to the user's real browser over CDP: only DISCONNECT (stopping
        # Playwright below drops the connection). Never close it — that is the
        # user's own window with their tabs. A context Playwright itself launched
        # (Firefox/Safari automation profile) is JUDO's to close.
        if self._cdp_browser is None and self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._context = self._page = self._cdp_browser = None

    async def _adopt_page(self) -> Page:
        """
        launch_persistent_context already opens a starting tab.
        Instead of opening a new blank tab (about:blank), it adopts that tab —
        so the user never sees an extra blank tab.
        """
        await asyncio.sleep(0.3)
        pages = self._context.pages
        return pages[0] if pages else await self._context.new_page()

    async def _launch(self):
        """
        Launches the browser with the real user profile.
        Does nothing if the context is already open.
        """
        if self._context is not None:
            return

        if self._spec is None:
            raise RuntimeError(
                f"'{self.browser_name}' bu platformda ({_OS}) desteklenmiyor."
            )

        engine_name = self._spec["engine"]
        exe         = self._spec["exe"]
        engine_obj  = getattr(self._pw, engine_name)

        if engine_name == "firefox":
            profile = _firefox_profile_dir() or str(
                Path.home() / ".judo_profiles" / "firefox"
            )
            kwargs: dict = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
                "timeout":     25_000,
            }
            if exe:
                kwargs["executable_path"] = exe
            try:
                self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
            except Exception as e:
                print(f"[Browser] Firefox real profile failed ({e}), using JUDO profile")
                judo = str(Path.home() / ".judo_profiles" / "firefox_judo")
                Path(judo).mkdir(parents=True, exist_ok=True)
                self._context = await engine_obj.launch_persistent_context(judo, **kwargs)

            self._page = await self._adopt_page()
            print(f"[Browser] ✅ Firefox launched")
            return

        if engine_name == "webkit":
            safari_profile = str(Path.home() / ".judo_profiles" / "safari")
            Path(safari_profile).mkdir(parents=True, exist_ok=True)
            kwargs = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
                "timeout":     25_000,
            }
            self._context = await engine_obj.launch_persistent_context(safari_profile, **kwargs)
            self._page = await self._adopt_page()
            print(f"[Browser] ✅ Safari launched")
            return

        # Chromium browsers: always drive the user's REAL, already-existing
        # browser over CDP — never a second, separate, logged-out one.
        port  = _CDP_PORTS.get(self.browser_name, 9222)
        label = f"{self.browser_name}" + (f" @ {exe}" if exe else "")

        # 1) Already debuggable (the user started it with the flag): attach and
        #    reuse exactly what's open.
        if _cdp_alive(port):
            await self._attach_cdp(port, label)
            return

        # 2) Not debuggable — whether it is running or not, JUDO never kills,
        #    restarts or relaunches the user's browser to get automation access.
        raise _NoAutomation(_NO_AUTOMATION.format(name=self.browser_name.capitalize(), port=port))

    async def _attach_cdp(self, port: int, label: str) -> None:
        """Connects to the user's real, already-running browser over CDP and
        adopts its existing (visible, logged-in) context/tab — as opposed to
        launching a brand new context, which is what caused a second, blank,
        logged-out browser window to appear alongside the user's real one."""
        browser = await self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        self._cdp_browser = browser
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context(no_viewport=True)
        self._context = ctx
        pages = ctx.pages
        self._page = pages[0] if pages else await ctx.new_page()
        print(f"[Browser] ✅ Attached to your actual {label} via CDP :{port}")

    async def _discard_dead_context(self) -> None:
        """Drop a context/page Playwright reports as closed/disconnected so the
        next _launch() call starts fresh instead of reusing a dead reference —
        this is what turns 'the browser died underneath us' into a transparent
        relaunch instead of the same error repeating on every command.
        Never closes a CDP-attached context here: that's the user's real
        browser, and a mere reconnect shouldn't risk closing all their tabs."""
        if self._context is not None and self._cdp_browser is None:
            try:
                await self._context.close()
            except Exception:
                pass
        self._context = None
        self._page = None
        self._cdp_browser = None

    async def _get_page(self) -> Page:
        await self._launch()
        try:
            # If somehow page got closed, open a fresh one
            if self._page is None or self._page.is_closed():
                self._page = await self._context.new_page()
                await asyncio.sleep(0.2)
            return self._page
        except Exception as e:
            if not _looks_like_dead_context(e):
                raise
            print(f"[Browser] {self.browser_name} session died underneath us "
                  f"({e}) — reattaching to your real browser.")
            await self._discard_dead_context()
            await self._launch()
            self._page = await self._context.new_page()
            await asyncio.sleep(0.2)
            return self._page

    async def go_to(self, url: str) -> str:

        url = _normalize_url(url)

        # Not attached and no CDP session up: hand the URL to the browser that
        # is already open, as a new tab in its window (_open_in_running) —
        # nothing is closed or restarted.
        if (self._context is None and self._spec and self._spec["engine"] == "chromium"
                and not _cdp_alive(_CDP_PORTS.get(self.browser_name, 9222))):
            exe = self._spec["exe"] or _resolve_exe_path(self.browser_name)
            if exe and _open_in_running(exe, url):
                return f"Opened in a new tab of your open {self.browser_name}: {url}"

        page     = await self._get_page()
        prev_url = page.url

        async def _do_goto(p: Page) -> str:
            """Attempt navigation and return the resulting URL (may still be blank)."""
            try:
                await p.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(0.3)
            except PlaywrightTimeout:
                pass   # page may have partially loaded — check URL below
            except Exception as e:
                print(f"[Browser] goto exception (non-fatal): {e}")
            return p.url

        result_url = await _do_goto(page)

        if result_url in ("about:blank", "", None, prev_url) and prev_url in ("about:blank", "", None):
            print(f"[Browser] Still blank after goto — retrying on new tab: {url}")
            try:
                new_page   = await self._context.new_page()
                self._page = new_page
                result_url = await _do_goto(new_page)
            except Exception as e:
                print(f"[Browser] New-tab retry failed: {e}")

        if result_url and result_url not in ("about:blank", "", None):
            return f"Opened: {result_url}"
        return f"Could not open: {url}"

    async def search(self, query: str, engine: str = "google") -> str:
        base = _SEARCH_ENGINES.get(engine.lower(), _SEARCH_ENGINES["google"])
        return await self.go_to(base + query.replace(" ", "+"))

    async def _debug_screenshot(self, page: Page, tag: str) -> str:
        """Best-effort screenshot taken right when an interactive action fails,
        so a bare Playwright timeout ("waiting for locator...") turns into
        something a human can actually diagnose (wrong page? login wall?
        cookie banner covering the element?) instead of guessing blind."""
        try:
            path = str(user_paths.desktop() / f"judo_debug_{tag}.png")
            await page.screenshot(path=path, full_page=False)
            return f" Screenshot saved: {path}"
        except Exception:
            return ""

    async def _describe_missing(self, page: Page, selector: str, tag: str) -> str:
        """Distinguishes 'selector matches nothing' (wrong page / not loaded /
        behind a login wall) from 'selector matches but isn't interactable'
        (hidden, disabled, or covered by an overlay) — the two most common
        causes of a hung click/type, which a plain timeout can't tell apart."""
        try:
            count = await page.locator(selector).count()
        except Exception:
            count = 0
        shot = await self._debug_screenshot(page, tag)
        if count == 0:
            return (f"No element matches '{selector}' on {page.url}. The page may "
                    f"not have finished loading, may require signing in, or the "
                    f"selector no longer matches this page.{shot}")
        return (f"'{selector}' exists on {page.url} but isn't interactable — "
                f"hidden, disabled, or covered by an overlay (e.g. a cookie/login "
                f"prompt).{shot}")

    async def click(self, selector: str = None, text: str = None) -> str:
        page = await self._get_page()
        try:
            if text:
                await page.get_by_text(text, exact=False).first.click(timeout=8_000)
                return f"Clicked text: '{text}'"
            if selector:
                await page.click(selector, timeout=8_000)
                return f"Clicked selector: {selector}"
            return "No selector or text provided."
        except PlaywrightTimeout:
            if selector:
                return f"Click error: {await self._describe_missing(page, selector, 'click_timeout')}"
            return "Element not found (timeout)."
        except Exception as e:
            return f"Click error: {e}"

    async def type_text(self, selector: str = None, text: str = "",
                        clear_first: bool = True) -> str:
        page = await self._get_page()
        try:
            el = page.locator(selector).first if selector else page.locator(":focus")
            if clear_first:
                await el.clear(timeout=10_000)
            await el.type(text, delay=50, timeout=10_000)
            return "Text typed."
        except PlaywrightTimeout:
            if selector:
                return f"Type error: {await self._describe_missing(page, selector, 'type_timeout')}"
            return "Type error: no element focused (timeout)."
        except Exception as e:
            return f"Type error: {e}"

    async def scroll(self, direction: str = "down", amount: int = 500) -> str:
        page = await self._get_page()
        try:
            y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, y)
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    async def press(self, key: str) -> str:
        page = await self._get_page()
        try:
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Key error: {e}"

    async def get_text(self) -> str:
        page = await self._get_page()
        try:
            text = await page.inner_text("body")
            return text[:4_000]
        except Exception as e:
            return f"Could not get page text: {e}"

    async def get_url(self) -> str:
        page = await self._get_page()
        return page.url

    async def fill_form(self, fields: dict) -> str:
        page    = await self._get_page()
        results = []
        for selector, value in fields.items():
            try:
                el = page.locator(selector).first
                await el.clear()
                await el.type(str(value), delay=40)
                results.append(f"✓ {selector}")
            except Exception as e:
                results.append(f"✗ {selector}: {e}")
        return "Form filled: " + ", ".join(results)

    async def smart_click(self, description: str) -> str:
        page = await self._get_page()
        for role in ("button", "link", "searchbox", "textbox", "menuitem", "tab"):
            try:
                loc = page.get_by_role(role, name=description)
                if await loc.count() > 0:
                    await loc.first.click(timeout=5_000)
                    return f"Clicked ({role}): '{description}'"
            except Exception:
                pass
        for attempt in (
            lambda: page.get_by_text(description, exact=False).first.click(timeout=5_000),
            lambda: page.get_by_placeholder(description, exact=False).first.click(timeout=5_000),
            lambda: page.locator(
                f'[alt*="{description}" i],[title*="{description}" i],'
                f'[aria-label*="{description}" i]'
            ).first.click(timeout=5_000),
        ):
            try:
                await attempt()
                return f"Clicked: '{description}'"
            except Exception:
                pass
        shot = await self._debug_screenshot(page, "smart_click_miss")
        return f"Could not find element: '{description}' on {page.url}.{shot}"

    async def smart_type(self, description: str, text: str) -> str:
        page = await self._get_page()
        candidates = [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role",        page.get_by_role("textbox", name=description)),
            ("searchbox",   page.get_by_role("searchbox")),
            ("combobox",    page.get_by_role("combobox", name=description)),
        ]
        for method, loc in candidates:
            try:
                el = loc.first
                if await el.count() == 0:
                    continue
                await el.clear()
                await el.type(text, delay=50)
                return f"Typed into ({method}): '{description}'"
            except Exception:
                continue
        shot = await self._debug_screenshot(page, "smart_type_miss")
        return f"Could not find input: '{description}' on {page.url}.{shot}"

    async def new_tab(self, url: str = "") -> str:
        # With a URL, this is just navigation — let go_to's own fast path
        # decide whether that needs a real Playwright page at all (it won't,
        # for an already-open Chromium browser with no CDP session up).
        # Forcing _get_page() here first would launch/attach unconditionally
        # and defeat that.
        if url:
            return await self.go_to(url)
        if (self._context is None and self._spec and self._spec["engine"] == "chromium"
                and not _cdp_alive(_CDP_PORTS.get(self.browser_name, 9222))):
            exe = self._spec["exe"] or _resolve_exe_path(self.browser_name)
            if exe and _open_in_running(exe, "chrome://newtab/"):
                return f"New tab opened in your open {self.browser_name}."
        page = await self._get_page()
        ctx  = page.context
        new  = await ctx.new_page()
        self._page = new
        return "New tab opened."

    async def switch_tab(self, target: str) -> str:
        t = (target or "").lower().strip()
        await self._launch()
        for p in self._context.pages:
            try:
                title = await p.title()
            except Exception:
                continue
            if t and (t in title.lower() or t in p.url.lower()):
                await p.bring_to_front()
                self._page = p
                return f"Switched to your open tab: {title}"
        return f"Could not find an open tab matching '{target}'."

    async def close_tab(self) -> str:
        page = self._page
        if page and not page.is_closed():
            ctx   = page.context
            await page.close()
            pages = ctx.pages
            self._page = pages[-1] if pages else None
            return "Tab closed."
        return "No active tab to close."

    async def screenshot(self, path: str = None) -> str:
        page = await self._get_page()
        try:
            save_path = path or str(user_paths.desktop() / "judo_screenshot.png")
            await page.screenshot(path=save_path, full_page=False)
            return f"Screenshot saved: {save_path}"
        except Exception as e:
            return f"Screenshot error: {e}"

    async def back(self) -> str:
        page = await self._get_page()
        try:
            await page.go_back(timeout=10_000)
            return f"Navigated back: {page.url}"
        except Exception as e:
            return f"Back error: {e}"

    async def forward(self) -> str:
        page = await self._get_page()
        try:
            await page.go_forward(timeout=10_000)
            return f"Navigated forward: {page.url}"
        except Exception as e:
            return f"Forward error: {e}"

    async def reload(self) -> str:
        page = await self._get_page()
        try:
            await page.reload(timeout=15_000)
            return f"Page reloaded: {page.url}"
        except Exception as e:
            return f"Reload error: {e}"

    async def close_browser(self) -> str:
        await self._async_close()
        return f"{self.browser_name} closed."

class _SessionRegistry:
    """Manages all active browser sessions."""

    def __init__(self):
        self._sessions:       dict[str, _BrowserSession] = {}
        self._active_browser: str                        = ""
        self._lock            = threading.Lock()

    def has(self, browser_name: str | None = None) -> bool:
        """Is there an active automation session for this browser (or any)?"""
        with self._lock:
            if not browser_name:
                return bool(self._sessions)
            name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
            return name in self._sessions

    def _get_or_create(self, browser_name: str) -> _BrowserSession:
        with self._lock:
            sess = self._sessions.get(browser_name)
        if sess is not None:
            return sess

        # sess.start() blocks for up to _INIT_TIMEOUT (45s) spinning up the
        # Playwright driver — done OUTSIDE the lock, or every unrelated
        # browser_control call (switch/list/close, even for a different
        # browser) would queue up behind it for no reason.
        new_sess = _BrowserSession(browser_name)
        new_sess.start()
        with self._lock:
            sess = self._sessions.get(browser_name)
            if sess is None:
                self._sessions[browser_name] = sess = new_sess
                print(f"[Registry] New session: {browser_name}")
            # else: another thread already created one for this browser_name in
            # the meantime (rare race) — new_sess is simply left unreferenced;
            # its daemon thread stays idle and dies with the process.
        return sess

    def get(self, browser_name: str | None = None) -> _BrowserSession:
        if not browser_name:
            browser_name = self._active_browser or _preferred_browser()
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        sess = self._get_or_create(browser_name)
        self._active_browser = browser_name
        return sess

    def switch(self, browser_name: str) -> str:
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        self._get_or_create(browser_name)
        self._active_browser = browser_name
        return f"Active browser → {browser_name}"

    def close_one(self, browser_name: str) -> str:
        with self._lock:
            sess = self._sessions.pop(browser_name, None)
        if sess:
            sess.close()
            if self._active_browser == browser_name:
                self._active_browser = ""
            return f"{browser_name} closed."
        return f"No active session for: {browser_name}"

    def close_all(self) -> str:
        with self._lock:
            names    = list(self._sessions.keys())
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._active_browser = ""
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        return "All browsers closed: " + (", ".join(names) if names else "none")

    def list_sessions(self) -> str:
        with self._lock:
            if not self._sessions:
                return "No active browser sessions."
            lines = []
            for name in self._sessions:
                marker = " ◀ active" if name == self._active_browser else ""
                lines.append(f"  • {name}{marker}")
            return "Open browsers:\n" + "\n".join(lines)


_registry = _SessionRegistry()

def browser_control(
    parameters:    dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params  = parameters or {}
    action  = params.get("action", "").lower().strip()
    browser = params.get("browser", "").lower().strip() or None
    result  = "Unknown action."

    # ── JUDO Browser only ──
    # Every action goes to JUDO's own browser (judo_browser/), started on
    # demand — even when the user names Chrome or Edge, which JUDO never opens.
    # What JUDO Browser can't do is reported, not retried in another browser.
    if _judo_browser_default():
        result = _judo_browser(action, params)
        _log(player, result)
        return result

    if action == "switch":
        target = browser or params.get("target", "").lower().strip()
        result = _registry.switch(target) if target else "Please specify a browser."
        _log(player, result)
        return result

    if action == "list_browsers":
        result = _registry.list_sessions()
        _log(player, result)
        return result

    if action == "close_all":
        result = _registry.close_all()
        _log(player, result)
        return result

    if action == "close":
        target = browser or _registry._active_browser
        result = _registry.close_one(target) if target else "No browser specified."
        _log(player, result)
        return result

    # ── Every action drives ONE browser: the user's actual, already-open one ──
    # go_to / search / new_tab used to open natively (a separate, uncontrolled
    # window) while click/type attached automation to a second, isolated
    # profile — two different windows for one task. Now everything, including
    # plain navigation, runs through the same CDP-attached session, which is
    # the user's real browser (real profile, logged-in accounts, extensions).
    # There is never a second, blank window.
    try:
        sess = _registry.get(browser)
    except Exception as e:
        result = f"Could not start browser session: {e}"
        _log(player, result)
        return result

    no_cdp = bool(sess._spec and sess._spec.get("engine") == "chromium"
                  and not _cdp_alive(_CDP_PORTS.get(sess.browser_name, 9222)))

    # "Open ChatGPT" while a ChatGPT tab is already open → use that tab, don't add another.
    if action in ("go_to", "new_tab") and no_cdp and params.get("url"):
        switched = uia.switch_to_site(sess.browser_name, _normalize_url(params["url"]))
        if switched:
            _log(player, switched)
            return switched

    if action in ("go_to", "search", "new_tab"):
        try:
            if action == "search":
                result = sess.run(sess.search(params.get("query", ""),
                                              params.get("engine", "google")))
            elif action == "new_tab":
                result = sess.run(sess.new_tab(params.get("url", "")))
            else:
                result = sess.run(sess.go_to(params.get("url", "")))
        except concurrent.futures.TimeoutError:
            result = f"Browser action '{action}' timed out (60s)."
        except Exception as e:
            result = f"Browser error ({action}): {e}"
        _log(player, result)
        return result

    try:
        if action == "click":
            result = sess.run(sess.click(params.get("selector"), params.get("text")))
        elif action == "type":
            result = sess.run(sess.type_text(
                params.get("selector"), params.get("text", ""), params.get("clear_first", True)))
        elif action == "scroll":
            result = sess.run(sess.scroll(params.get("direction", "down"), int(params.get("amount", 500))))
        elif action == "fill_form":
            result = sess.run(sess.fill_form(params.get("fields", {})))
        elif action == "smart_click":
            result = sess.run(sess.smart_click(params.get("description", "")))
        elif action == "smart_type":
            result = sess.run(sess.smart_type(params.get("description", ""), params.get("text", "")))
        elif action == "get_text":
            result = sess.run(sess.get_text())
        elif action == "get_url":
            result = sess.run(sess.get_url())
        elif action == "press":
            result = sess.run(sess.press(params.get("key", "Enter")))
        elif action == "close_tab":
            result = sess.run(sess.close_tab())
        elif action == "screenshot":
            result = sess.run(sess.screenshot(params.get("path")))
        elif action == "back":
            result = sess.run(sess.back())
        elif action == "forward":
            result = sess.run(sess.forward())
        elif action == "reload":
            result = sess.run(sess.reload())
        elif action == "switch_tab":
            result = sess.run(sess.switch_tab(params.get("target") or params.get("text", "")))
        else:
            result = f"Unknown browser action: '{action}'"

    except concurrent.futures.TimeoutError:
        result = f"Browser action '{action}' timed out (60s)."
    except _NoAutomation as e:
        result = _drive_open_window(sess.browser_name, action, params) or str(e)
    except Exception as e:
        result = f"Browser error ({action}): {e}"

    _log(player, result)
    return result


_JUDO_ALIASES = {"switch": "focus", "list_browsers": "focus"}   # one browser: these just bring it forward


def _judo_browser(action: str, params: dict) -> str:
    """One browser_control action in JUDO Browser, as a sentence for the user."""
    params = {k: v for k, v in params.items() if k != "browser"}
    try:
        from judo_browser import client as judo_client
        r = judo_client.send(_JUDO_ALIASES.get(action, action), params)
    except Exception as e:
        return f"JUDO Browser could not be started ({e}). No other browser was opened."
    if r is None:
        return f"JUDO Browser can't do '{action}'. No other browser was opened."
    return r


def _judo_browser_default() -> bool:
    """config/api_keys.json "default_browser": "judo" (default) or "edge" — the
    only way back to driving Edge/Chrome."""
    try:
        cfg = json.loads((Path(__file__).resolve().parent.parent / "config" / "api_keys.json").read_text(encoding="utf-8"))
        return str(cfg.get("default_browser", "judo")).lower() == "judo"
    except Exception:
        return True


def _drive_open_window(browser: str, action: str, p: dict) -> str | None:
    """No CDP: do the action on the user's open window via UI Automation.
    None → UIA can't do this action / isn't available (caller reports why)."""
    text = p.get("text", "") or ""
    if action == "switch_tab":
        return uia.switch_tab(browser, p.get("target") or text)
    if action in ("type", "smart_type"):
        return uia.type_text(browser, text, p.get("description", "") or "")
    if action in ("click", "smart_click"):
        return uia.click(browser, p.get("description") or text or "")
    if action == "press":
        return uia.press(browser, p.get("key", "Enter"))
    if action == "get_text":
        return uia.get_text(browser)
    if action == "get_url":
        return uia.get_url(browser)
    return None


def _log(player, text: str):
    short = str(text)[:80]
    print(f"[Browser] {short}")
    if player:
        player.write_log(f"[browser] {short[:60]}")


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "browser_control",
    "description": "Controls JUDO Browser — JUDO's own Chrome-like browser and the ONLY browser JUDO uses. Use for: opening ANY website or URL ('YouTube kholo', 'open chatgpt', 'gmail kholo', 'amazon.in pe jao'), showing search results, clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. It starts by itself if closed. go_to/search/new_tab open as tabs in its window; go_to a site that already has an open tab (e.g. chatgpt.com) switches to that tab. switch_tab (target = words from the tab title, e.g. 'ChatGPT') brings an open tab to the front. Reading the page, clicking, typing and pressing keys all work in it. Chrome, Edge and other browsers are never opened — even 'Chrome kholo' / 'open in Edge' means JUDO Browser, so leave 'browser' empty. If an action can't be done the tool says so — report that honestly.",

    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | switch_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"
            },
            "browser": {
                "type": "STRING",
                "description": "Leave empty — every action runs in JUDO Browser (Chrome/Edge are never opened)."
            },
            "url": {
                "type": "STRING",
                "description": "URL for go_to / new_tab action"
            },
            "query": {
                "type": "STRING",
                "description": "Search query for search action"
            },
            "engine": {
                "type": "STRING",
                "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"
            },
            "selector": {
                "type": "STRING",
                "description": "CSS selector for click/type"
            },
            "text": {
                "type": "STRING",
                "description": "Text to click or type"
            },
            "description": {
                "type": "STRING",
                "description": "Element description for smart_click/smart_type"
            },
            "direction": {
                "type": "STRING",
                "description": "up | down for scroll"
            },
            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount in pixels (default: 500)"
            },
            "key": {
                "type": "STRING",
                "description": "Key name for press action (e.g. Enter, Escape, F5)"
            },
            "target": {
                "type": "STRING",
                "description": "switch_tab: words from the open tab's title or site, e.g. 'ChatGPT'"
            },
            "path": {
                "type": "STRING",
                "description": "Save path for screenshot"
            },
            "incognito": {
                "type": "BOOLEAN",
                "description": "Open in private/incognito mode"
            },
            "clear_first": {
                "type": "BOOLEAN",
                "description": "Clear field before typing (default: true)"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": browser_control,
}
