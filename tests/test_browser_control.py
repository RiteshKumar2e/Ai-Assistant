"""
Tests for actions/browser_control.py — the parts that don't require a real
browser: the stale-profile-state cleanup (pure filesystem) and a static
source-level regression guard for the removed --disable-blink-features=
AutomationControlled flag.
"""
import asyncio
import json
import time
from pathlib import Path

import pytest

import actions.browser_control as bc


# ── Regression guard: the unsupported Chrome flag must never come back ─────

def test_automation_controlled_flag_not_present_in_source():
    source = Path(bc.__file__).read_text(encoding="utf-8")
    assert "AutomationControlled" not in source
    assert "disable-blink-features" not in source


# ── Stale profile cleanup ────────────────────────────────────────────────────

def test_clear_stale_profile_state_removes_lock_files(tmp_path):
    root = tmp_path
    (root / "SingletonLock").write_text("stale", encoding="utf-8")
    (root / "SingletonCookie").write_text("stale", encoding="utf-8")
    (root / "SingletonSocket").write_text("stale", encoding="utf-8")

    bc._clear_stale_profile_state(str(root))

    assert not (root / "SingletonLock").exists()
    assert not (root / "SingletonCookie").exists()
    assert not (root / "SingletonSocket").exists()


def test_clear_stale_profile_state_patches_exit_flags(tmp_path):
    default_dir = tmp_path / "Default"
    default_dir.mkdir()
    prefs_path = default_dir / "Preferences"
    prefs_path.write_text(json.dumps({
        "profile": {"exit_type": "Crashed", "exited_cleanly": False, "name": "Person 1"},
        "other_key": "untouched",
    }), encoding="utf-8")

    bc._clear_stale_profile_state(str(tmp_path))

    prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert prefs["profile"]["exit_type"] == "Normal"
    assert prefs["profile"]["exited_cleanly"] is True
    assert prefs["profile"]["name"] == "Person 1"      # untouched
    assert prefs["other_key"] == "untouched"            # untouched


def test_clear_stale_profile_state_handles_missing_directory():
    bc._clear_stale_profile_state("/path/does/not/exist/anywhere")   # must not raise


def test_clear_stale_profile_state_handles_missing_preferences_file(tmp_path):
    (tmp_path / "Default").mkdir()
    bc._clear_stale_profile_state(str(tmp_path))   # no Preferences file — must not raise


def test_clear_stale_profile_state_handles_malformed_preferences(tmp_path):
    default_dir = tmp_path / "Default"
    default_dir.mkdir()
    (default_dir / "Preferences").write_text("{ not valid json", encoding="utf-8")
    bc._clear_stale_profile_state(str(tmp_path))   # must not raise


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


# ── Relaunch grace period: don't kill a Chrome we JUST started ─────────────

def _chromium_session():
    sess = bc._BrowserSession.__new__(bc._BrowserSession)
    sess.browser_name = "chrome"
    sess._spec = {"engine": "chromium", "exe": None, "channel": None}
    sess._context = None
    sess._page = None
    sess._pw = type("FakePlaywright", (), {"chromium": object()})()
    sess._cdp_browser = None
    sess._last_relaunch_attempt = 0.0
    return sess


def test_recent_relaunch_waits_instead_of_killing_again(monkeypatch):
    """Reproduces the real failure: action A's relaunch is still starting up
    when action B arrives a few seconds later. B must NOT kill and relaunch
    a second time — it must wait for the first relaunch to finish."""
    sess = _chromium_session()
    sess._last_relaunch_attempt = time.monotonic() - 5   # "just" relaunched

    terminate_calls = []
    monkeypatch.setattr(bc, "_terminate", lambda name: terminate_calls.append(name))
    monkeypatch.setattr(bc, "_cdp_alive", lambda port: False)
    monkeypatch.setattr(bc, "_is_running", lambda name: True)
    monkeypatch.setattr(bc, "_wait_for_cdp", lambda port, timeout: True)

    attached = []
    async def fake_attach(self, port, label):
        attached.append(port)
    monkeypatch.setattr(bc._BrowserSession, "_attach_cdp", fake_attach)

    asyncio.run(sess._launch())

    assert terminate_calls == []          # must NOT have killed Chrome again
    assert attached == [bc._CDP_PORTS.get("chrome", 9222)]


def test_recent_relaunch_still_failing_raises_without_killing(monkeypatch):
    """If the grace-period wait also times out, report a clear error — but
    still must not have killed the in-progress Chrome."""
    sess = _chromium_session()
    sess._last_relaunch_attempt = time.monotonic() - 5

    terminate_calls = []
    monkeypatch.setattr(bc, "_terminate", lambda name: terminate_calls.append(name))
    monkeypatch.setattr(bc, "_cdp_alive", lambda port: False)
    monkeypatch.setattr(bc, "_is_running", lambda name: True)
    monkeypatch.setattr(bc, "_wait_for_cdp", lambda port, timeout: False)

    with pytest.raises(RuntimeError, match="still starting up"):
        asyncio.run(sess._launch())

    assert terminate_calls == []


def test_stale_relaunch_beyond_grace_window_kills_normally(monkeypatch):
    """Once the grace window has passed, a still-not-debuggable Chrome goes
    through the normal kill+restart path again (this is the ordinary,
    intended recovery — must still work)."""
    sess = _chromium_session()
    sess._last_relaunch_attempt = time.monotonic() - 200   # long past the 90s grace window

    terminate_calls = []
    monkeypatch.setattr(bc, "_terminate", lambda name: terminate_calls.append(name))
    monkeypatch.setattr(bc, "_cdp_alive", lambda port: False)
    monkeypatch.setattr(bc, "_is_running", lambda name: True)
    monkeypatch.setattr(bc, "_resolve_exe_path", lambda name: None)   # stop before Popen

    with pytest.raises(RuntimeError, match="Could not locate an executable"):
        asyncio.run(sess._launch())

    assert terminate_calls == ["chrome"]   # the normal path DOES kill once, as designed
