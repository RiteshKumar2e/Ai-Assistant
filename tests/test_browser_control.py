"""
Tests for actions/browser_control.py that don't need a real browser. The core
guarantee: JUDO never closes, kills or restarts the user's browser — pages open
as tabs in the window that is already open, and page interaction only attaches
to a browser that is already debuggable.
"""
import asyncio
from pathlib import Path

import pytest

import actions.browser_control as bc


# ── Regression guards (source level) ──────────────────────────────────────

def test_automation_controlled_flag_not_present_in_source():
    source = Path(bc.__file__).read_text(encoding="utf-8")
    assert "AutomationControlled" not in source
    assert "disable-blink-features" not in source


def test_no_code_path_can_kill_or_restart_the_browser():
    source = Path(bc.__file__).read_text(encoding="utf-8")
    for forbidden in ("taskkill", "pkill", "killall", "_terminate(", "--restore-last-session",
                      "--remote-debugging-port={port}\",", "exited_cleanly"):
        assert forbidden not in source, forbidden


# ── Registry: slow startup must not happen while holding the shared lock ────

def test_registry_lock_not_held_during_session_start(monkeypatch):
    """_get_or_create's slow sess.start() must run OUTSIDE the registry lock —
    otherwise one browser's 45s startup blocks every unrelated registry call
    (switch/list/a different browser)."""
    registry = bc._SessionRegistry()
    lock_held_during_start = []

    class _SlowFakeSession:
        def __init__(self, name):
            self.browser_name = name

        def start(self):
            lock_held_during_start.append(registry._lock.locked())

    monkeypatch.setattr(bc, "_BrowserSession", _SlowFakeSession)
    registry._get_or_create("chrome")

    assert lock_held_during_start == [False]


def test_registry_does_not_cache_a_failed_session(monkeypatch):
    class _FailingSession:
        def __init__(self, name):
            self.browser_name = name

        def start(self):
            raise RuntimeError("Playwright driver did not initialize in time")

    monkeypatch.setattr(bc, "_BrowserSession", _FailingSession)
    registry = bc._SessionRegistry()

    try:
        registry._get_or_create("chrome")
    except RuntimeError:
        pass

    assert "chrome" not in registry._sessions   # next call gets a fresh attempt


# ── Never kill / restart; open in the window that is already there ─────────

def _chromium_session():
    sess = bc._BrowserSession.__new__(bc._BrowserSession)
    sess.browser_name = "chrome"
    sess._spec = {"engine": "chromium", "exe": "C:/fake/chrome.exe", "channel": None}
    sess._context = None
    sess._page = None
    sess._pw = type("FakePlaywright", (), {"chromium": object()})()
    sess._cdp_browser = None
    return sess


@pytest.fixture
def no_processes(monkeypatch):
    """Records every process the module tries to start; none is actually run."""
    spawned = []
    monkeypatch.setattr(bc.subprocess, "Popen", lambda args, **kw: spawned.append(list(args)))
    monkeypatch.setattr(bc.subprocess, "run", lambda args, **kw: spawned.append(list(args)))
    return spawned


def test_interaction_without_debugging_refuses_and_starts_nothing(monkeypatch, no_processes):
    monkeypatch.setattr(bc, "_cdp_alive", lambda port: False)
    with pytest.raises(RuntimeError, match="never close or restart your browser"):
        asyncio.run(_chromium_session()._launch())
    assert no_processes == []                      # no taskkill, no relaunch, nothing


def test_already_debuggable_browser_is_attached(monkeypatch, no_processes):
    monkeypatch.setattr(bc, "_cdp_alive", lambda port: True)
    attached = []
    async def fake_attach(self, port, label):
        attached.append(port)
    monkeypatch.setattr(bc._BrowserSession, "_attach_cdp", fake_attach)
    asyncio.run(_chromium_session()._launch())
    assert attached == [bc._CDP_PORTS["chrome"]] and no_processes == []


def test_go_to_opens_a_tab_in_the_open_window(monkeypatch, no_processes):
    monkeypatch.setattr(bc, "_cdp_alive", lambda port: False)
    out = asyncio.run(_chromium_session().go_to("chatgpt.com"))
    assert no_processes == [["C:/fake/chrome.exe", "https://chatgpt.com"]]
    assert "new tab" in out


def test_new_tab_without_url_opens_in_the_open_window(monkeypatch, no_processes):
    monkeypatch.setattr(bc, "_cdp_alive", lambda port: False)
    asyncio.run(_chromium_session().new_tab())
    assert no_processes == [["C:/fake/chrome.exe", "chrome://newtab/"]]


def test_close_only_disconnects_from_the_users_browser():
    sess = _chromium_session()
    closed = []
    class FakeBrowser:
        async def close(self): closed.append("browser")
    class FakePW:
        async def stop(self): closed.append("playwright")
    sess._cdp_browser, sess._context, sess._pw = FakeBrowser(), object(), FakePW()
    asyncio.run(sess._async_close())
    assert closed == ["playwright"]                # the user's browser itself is never closed
