"""
Tests for core/self_trainer.py — idle-time self-test drills.

Verifies the two hard safety rules (never executes anything; only failures
are persisted) and the idle/cooldown scheduling gate. All model calls are
mocked — no network, no real Groq/Gemini traffic.
"""
import json

import pytest

import core.self_trainer as st_mod
from memory import self_training as st


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "STORE_PATH", tmp_path / "self_training.json")


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg = config_dir / "api_keys.json"
    cfg.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(st_mod, "_base_dir", lambda: tmp_path)
    return cfg


TOOLS = [
    {"name": "open_app", "description": "Opens an application by name."},
    {"name": "reminder", "description": "Sets a reminder for later."},
    {"name": "web_search", "description": "Searches the web."},
]


# ── Master off switch ────────────────────────────────────────────────────────

def test_disabled_prevents_trigger(isolated_config):
    isolated_config.write_text(json.dumps({"self_training_enabled": False}), encoding="utf-8")
    trainer = st_mod.SelfTrainer(min_idle_secs=0, cooldown_secs=0)
    assert trainer.should_trigger(last_user_speech=0.0) is False


def test_enabled_by_default_when_key_absent(isolated_config):
    trainer = st_mod.SelfTrainer(min_idle_secs=0, cooldown_secs=0)
    assert trainer.should_trigger(last_user_speech=0.0) is True


def test_missing_config_file_defaults_to_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(st_mod, "_base_dir", lambda: tmp_path / "does_not_exist")
    trainer = st_mod.SelfTrainer(min_idle_secs=0, cooldown_secs=0)
    assert trainer.should_trigger(last_user_speech=0.0) is True


# ── Idle / cooldown scheduling ───────────────────────────────────────────────

def test_does_not_trigger_before_idle_threshold(isolated_config):
    import time
    trainer = st_mod.SelfTrainer(min_idle_secs=120, cooldown_secs=0)
    assert trainer.should_trigger(last_user_speech=time.monotonic()) is False


def test_triggers_after_idle_threshold(isolated_config):
    import time
    trainer = st_mod.SelfTrainer(min_idle_secs=120, cooldown_secs=0)
    long_ago = time.monotonic() - 200
    assert trainer.should_trigger(last_user_speech=long_ago) is True


def test_cooldown_blocks_repeated_triggering(isolated_config):
    import time
    trainer = st_mod.SelfTrainer(min_idle_secs=0, cooldown_secs=300)
    long_ago = time.monotonic() - 1000
    assert trainer.should_trigger(last_user_speech=long_ago) is True
    trainer.mark_triggered()
    # Immediately after marking, still well inside the 300s cooldown.
    assert trainer.should_trigger(last_user_speech=long_ago) is False


def test_rotation_increments_on_each_trigger(isolated_config):
    trainer = st_mod.SelfTrainer()
    assert trainer._rotation == 0
    trainer.mark_triggered()
    assert trainer._rotation == 1
    trainer.mark_triggered()
    assert trainer._rotation == 2


# ── Routing drill: never executes a tool, grades itself correctly ──────────

def test_routing_drill_pass_does_not_write_a_lesson(monkeypatch, isolated_config):
    trainer = st_mod.SelfTrainer()
    monkeypatch.setattr(
        st_mod.random, "choice",
        lambda seq: next(t for t in TOOLS if t["name"] == "open_app") if isinstance(seq[0], dict) else seq[0]
    )
    responses = iter(["Open Chrome for me", "open_app"])
    monkeypatch.setattr(st_mod, "_ask", lambda prompt: next(responses))

    line = trainer._routing_drill(TOOLS)
    assert "passed" in line
    assert trainer.passed == 1
    data = st._read()
    assert data["understanding"] == []   # a pass must not be persisted


def test_routing_drill_failure_writes_exactly_one_lesson(monkeypatch, isolated_config):
    trainer = st_mod.SelfTrainer()
    monkeypatch.setattr(
        st_mod.random, "choice",
        lambda seq: next(t for t in TOOLS if t["name"] == "open_app") if isinstance(seq[0], dict) else seq[0]
    )
    responses = iter(["Open Chrome for me", "web_search"])   # wrong tool on purpose
    monkeypatch.setattr(st_mod, "_ask", lambda prompt: next(responses))

    line = trainer._routing_drill(TOOLS)
    assert "FAILED" in line
    assert trainer.failed == 1
    data = st._read()
    assert len(data["understanding"]) == 1
    assert data["understanding"][0]["command"] == "Open Chrome for me"


def test_routing_drill_never_calls_a_real_tool(monkeypatch, isolated_config):
    """The whole safety argument rests on this: the drill must never invoke
    anything resembling ActionRegistry.run()."""
    called = []
    monkeypatch.setattr(st_mod, "_ask", lambda prompt: "some command")

    class _FakeRegistry:
        def run(self, *a, **kw):
            called.append((a, kw))

    trainer = st_mod.SelfTrainer()
    trainer._routing_drill(TOOLS)
    assert called == []


def test_routing_drill_needs_at_least_two_tools(monkeypatch, isolated_config):
    trainer = st_mod.SelfTrainer()
    monkeypatch.setattr(st_mod, "_ask", lambda prompt: "irrelevant")
    assert trainer._routing_drill(TOOLS[:1]) == ""


# ── Coding drill: compiles, never executes ──────────────────────────────────

def test_coding_drill_never_executes_generated_code(monkeypatch, isolated_config):
    """If the model were ever asked to `exec()` this, the sentinel file below
    would appear on disk. It must not, no matter what the drill grades."""
    import tempfile, os
    sentinel = os.path.join(tempfile.gettempdir(), "judo_test_should_never_exist.txt")
    if os.path.exists(sentinel):
        os.remove(sentinel)

    malicious_code = f"open(r'{sentinel}', 'w').write('executed')"
    responses = iter(["Write a function", malicious_code])
    monkeypatch.setattr(st_mod, "_ask", lambda prompt: next(responses))

    trainer = st_mod.SelfTrainer()
    trainer._coding_drill()

    assert not os.path.exists(sentinel), "coding drill executed generated code!"


def test_coding_drill_syntax_error_writes_lesson(monkeypatch, isolated_config):
    responses = iter(["Write a function", "def broken(:\n    pass"])
    monkeypatch.setattr(st_mod, "_ask", lambda prompt: next(responses))

    trainer = st_mod.SelfTrainer()
    line = trainer._coding_drill()

    assert "FAILED" in line
    assert trainer.failed == 1
    lessons = st.get_coding_lessons("python")
    assert lessons != ""


def test_coding_drill_valid_code_passes_without_lesson(monkeypatch, isolated_config):
    responses = iter(["Write a function", "def add(a, b):\n    return a + b\n"])
    monkeypatch.setattr(st_mod, "_ask", lambda prompt: next(responses))

    trainer = st_mod.SelfTrainer()
    line = trainer._coding_drill()

    assert "passed" in line
    assert trainer.passed == 1
    assert st.get_coding_lessons("python") == ""


# ── run_drill() never raises, even when the model call fails ────────────────

def test_run_drill_never_raises_on_model_failure(monkeypatch, isolated_config):
    def boom(prompt):
        raise ConnectionError("network is down")

    monkeypatch.setattr(st_mod, "_ask", boom)
    trainer = st_mod.SelfTrainer()
    result = trainer.run_drill(TOOLS)   # must not raise
    assert result == ""


def test_run_drill_alternates_routing_and_coding(monkeypatch, isolated_config):
    calls = []
    trainer = st_mod.SelfTrainer()
    monkeypatch.setattr(trainer, "_routing_drill", lambda tools: calls.append("routing") or "r")
    monkeypatch.setattr(trainer, "_coding_drill", lambda: calls.append("coding") or "c")

    trainer._rotation = 0
    trainer.run_drill(TOOLS)
    trainer._rotation = 1
    trainer.run_drill(TOOLS)

    assert calls == ["routing", "coding"]
