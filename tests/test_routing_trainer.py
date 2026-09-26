"""
Tests for core/training_corpus.py and core/routing_trainer.py — the 20,000
command routing exam. All model calls are mocked; no network.
"""
import pytest

from core import routing_trainer as rt
from core import training_corpus as tc

DECLS = [{"name": "open_app", "description": "Opens apps."},
         {"name": "media_control", "description": "Media keys.",
          "parameters": {"properties": {"action": {"description": "play_pause | next | previous | stop"}}}}]


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "STATE_PATH", tmp_path / "routing_training.json")


def _answer_with(fn):
    """Fake router: numbers each command in the prompt and answers fn(command)."""
    def ask(prompt):
        cmds = [l.split(". ", 1)[1] for l in prompt.splitlines() if l[:1].isdigit() and ". " in l]
        return "\n".join(f"{i} {fn(c)}" for i, c in enumerate(cmds, 1))
    return ask


# ── Corpus ───────────────────────────────────────────────────────────────────

def test_corpus_is_20000_unique_and_deterministic():
    a, b = tc.build(), tc.build()
    assert len(a) == 20_000
    assert len({c for c, _ in a}) == 20_000
    assert a == b and tc.fingerprint(a) == tc.fingerprint(b)


def test_every_label_names_a_real_tool_and_action():
    decls = {d["name"]: d for d in rt.load_tool_decls()}
    for label in {l for _, l in tc.build()} - {"none"}:
        tool, _, action = label.partition(".")
        assert tool in decls, label
        if action:
            props = decls[tool]["parameters"]["properties"]
            enum = next(props[k]["description"] for k in rt._SELECTORS if k in props)
            assert action in enum, label


def test_corpus_is_balanced_across_tools():
    from collections import Counter
    counts = Counter(l.split(".")[0] for _, l in tc.build())
    assert max(counts.values()) < 0.06 * 20_000   # no tool swamps the exam


# ── Scoring / parsing ────────────────────────────────────────────────────────

def test_is_correct_checks_action_only_when_labelled():
    assert rt._is_correct("open_app", "open_app")
    assert rt._is_correct("open_app", "open_app.whatever")
    assert rt._is_correct("media_control.next", "media_control.next")
    assert not rt._is_correct("media_control.next", "media_control.stop")
    assert not rt._is_correct("media_control.next", "media_control")
    assert not rt._is_correct("open_app", "browser_control")
    assert rt._is_correct("desktop_control.organize", "file_controller.organize_desktop")


def test_parse_tolerates_formatting_noise():
    picks = rt._parse("1. open_app\n2) `media_control.next`\n  3 - none\nnoise\n9 open_app", 3)
    assert picks == {1: "open_app", 2: "media_control.next", 3: "none"}


# ── Training loop ────────────────────────────────────────────────────────────

def test_perfect_batch_advances_cursor_and_learns_nothing(monkeypatch):
    items = rt.corpus()
    truth = {c: l for c, l in items}
    monkeypatch.setattr(rt, "_ask", _answer_with(lambda c: truth[c]))
    r = rt.run_batch(DECLS, 20)
    assert r == {"ok": 20, "total": 20, "seen": 20, "correct": 20, "round": 1}
    assert rt._read()["cursor"] == 20
    assert rt.format_routing_lessons() == ""


def test_repeated_mistake_becomes_a_lesson_then_fades(monkeypatch):
    truth = dict(rt.corpus())
    monkeypatch.setattr(rt, "_ask", _answer_with(lambda c: "none"))
    rt.run_batch(DECLS, 200)
    lessons = rt.format_routing_lessons()
    assert "[ROUTING LESSONS" in lessons and "NOT none" in lessons
    assert rt._read()["wrong"]

    monkeypatch.setattr(rt, "_ask", _answer_with(lambda c: truth[c]))
    for _ in range(5):
        rt.run_batch(DECLS, 200)
    assert rt._read()["wrong"] == {}
    assert rt.format_routing_lessons() == ""


def test_unparseable_reply_raises_and_saves_nothing(monkeypatch):
    monkeypatch.setattr(rt, "_ask", lambda p: "sorry, I can't help")
    with pytest.raises(RuntimeError):
        rt.run_batch(DECLS, 10)
    assert rt._read()["seen"] == 0


def test_changed_corpus_resets_progress(monkeypatch):
    s = rt._fresh()
    s["seen"], s["fingerprint"] = 500, "stale"
    rt._write(s)
    assert rt._read()["seen"] == 0


def test_train_resumes_and_backs_off(monkeypatch):
    truth = dict(rt.corpus())
    calls = {"n": 0}

    def flaky(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("429")
        return _answer_with(lambda c: truth[c])(prompt)

    monkeypatch.setattr(rt, "_ask", flaky)
    monkeypatch.setattr(rt, "load_tool_decls", lambda: DECLS)
    monkeypatch.setattr(rt.time, "sleep", lambda s: None)
    rt.train(limit=60, size=25, log=lambda m: None)
    assert rt._read()["seen"] == 60


def test_parallel_workers_route_each_command_once(monkeypatch):
    truth = dict(rt.corpus())
    asked = []

    def ask(prompt):
        reply = _answer_with(lambda c: asked.append(c) or truth[c])(prompt)
        return reply

    monkeypatch.setattr(rt, "_ask", ask)
    monkeypatch.setattr(rt, "load_tool_decls", lambda: DECLS)
    rt.train(limit=300, size=25, workers=4, log=lambda m: None)
    s = rt._read()
    assert s["seen"] == 300 and s["cursor"] == 300
    assert len(asked) == len(set(asked)) == 300


def test_failed_batch_items_are_queued_not_lost(monkeypatch):
    monkeypatch.setattr(rt, "_ask", lambda p: (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(ConnectionError):
        rt.run_batch(DECLS, 10)
    s = rt._read()
    assert s["cursor"] == 10 and set(s["wrong"]) == {str(k) for k in range(10)}
    truth = dict(rt.corpus())
    monkeypatch.setattr(rt, "_ask", _answer_with(lambda c: truth[c]))
    for _ in range(4):
        rt.run_batch(DECLS, 30)
    assert rt._read()["wrong"] == {}
