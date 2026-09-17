"""
Tests for actions/browser_control.py — the parts that don't require a real
browser: the stale-profile-state cleanup (pure filesystem) and a static
source-level regression guard for the removed --disable-blink-features=
AutomationControlled flag.
"""
import json
from pathlib import Path

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
