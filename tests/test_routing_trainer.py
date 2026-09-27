"""
Tests for core/training_corpus.py, core/routing_trainer.py and
core/task_sandbox.py — the 20,000-task exam. All model calls are mocked; no
network. Sandbox runs happen only in the tests that say so, inside temp dirs.
"""
import json
import os

import pytest

from core import routing_trainer as rt
from core import task_sandbox as sb
from core import training_corpus as tc

DECLS = [{"name": "open_app", "description": "Opens apps."},
         {"name": "media_control", "description": "Media keys.",
          "parameters": {"properties": {"action": {"description": "play_pause | next | previous | stop"}}}}]


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "STATE_PATH", tmp_path / "routing_training.json")
    monkeypatch.setattr(sb, "should_run", lambda *a: False)   # opt in per test


def _perfect(cmd: str) -> str:
    """The exactly-right call for a corpus command, as the model would write it."""
    label, want = rt._by_cmd[cmd]
    args = {k.rstrip("?").split("|")[0]: v.lstrip("~#@") for k, v in want.items()}
    if label == "calendar_agenda.add":
        args["date"] = "tomorrow"
    return f"{label} {json.dumps(args, ensure_ascii=False)}"


def _answer_with(fn):
    """Fake router: numbers each command in the prompt and answers fn(command)."""
    def ask(prompt):
        cmds = [l.split(". ", 1)[1] for l in prompt.splitlines() if l[:1].isdigit() and ". " in l]
        return "\n".join(f"{i} {fn(c)}" for i, c in enumerate(cmds, 1))
    return ask


# ── Corpus ───────────────────────────────────────────────────────────────────

def test_corpus_is_20000_unique_deterministic_tasks():
    a, b = tc.build(), tc.build()
    assert len(a) == 20_000
    assert len({c for c, _, _ in a}) == 20_000
    assert a == b and tc.fingerprint(a) == tc.fingerprint(b)
    assert sum(1 for *_, args in a if args) > 10_000   # most tasks carry details to get right


def test_every_label_and_arg_names_a_real_tool_param():
    decls = {d["name"]: d for d in rt.load_tool_decls()}
    for _, label, args in tc.build():
        if label == "none":
            continue
        tool, _, action = label.partition(".")
        assert tool in decls, label
        props = decls[tool]["parameters"]["properties"]
        if action:
            enum = next(props[k]["description"] for k in rt._SELECTORS if k in props)
            assert action in enum, label
        for spec in args:
            assert any(p in props for p in spec.rstrip("?").split("|")), (label, spec)


def test_corpus_is_balanced_across_tools():
    from collections import Counter
    counts = Counter(l.split(".")[0] for _, l, _ in tc.build())
    assert max(counts.values()) < 0.06 * 20_000   # no tool swamps the exam


def test_expected_args_follow_the_template():
    assert tc.expected_args("send_message", "telegram pe {contact} ko bhejo {msg}",
                            {"contact": "Rahul", "msg": "call me back"}) == \
        {"receiver": "Rahul", "message_text": "~call me back", "platform": "telegram"}
    assert tc.expected_args("open_folder", "open {folder} in VS Code", {"folder": "Work"}) == \
        {"folder_path": "Work", "open_in": "vscode"}
    assert tc.expected_args("reminder", "{time} alarm laga do", {"time": "5 baje"}) == {}


# ── Grading ──────────────────────────────────────────────────────────────────

def test_is_correct_checks_action_only_when_labelled():
    assert rt._is_correct("open_app", "open_app")
    assert rt._is_correct("open_app", "open_app.whatever")
    assert rt._is_correct("media_control.next", "media_control.next")
    assert not rt._is_correct("media_control.next", "media_control.stop")
    assert not rt._is_correct("media_control.next", "media_control")
    assert not rt._is_correct("open_app", "browser_control")
    assert rt._is_correct("desktop_control.organize", "file_controller.organize_desktop")


@pytest.mark.parametrize("want,got,ok", [
    ("Rahul", "rahul", True), ("Rahul", "Priya", False),
    ("Google Drive", "https://drive.google.com", True), ("YouTube", "youtube.com", True),
    ("~main late aaunga", "Main late aaunga!", True), ("~main late aaunga", "I will be late", False),
    ("~pay the electricity bill", "pay electricity bill", True),
    ("#75", 75, True), ("#75", "75%", True), ("#75", "57", False),
    ("@fahrenheit", "F", True), ("@rupees", "INR", True), ("@km", "miles", False),
])
def test_value_matching(want, got, ok):
    assert rt._value_ok(want, got) is ok


def test_args_error_names_the_wrong_detail():
    want = {"receiver": "Rahul", "message_text": "~call me back", "platform?": "whatsapp"}
    assert rt._args_error(want, {"receiver": "Rahul", "message_text": "call me back"}) is None
    assert rt._args_error(want, {"receiver": "Rahul", "message_text": "hi"}) == "message_text"
    assert rt._args_error(want, {"message_text": "call me back"}) == "receiver"
    assert rt._args_error({"path|name": "notes.txt"}, {"name": "notes.txt"}) is None


def test_grade_reports_tool_then_args():
    want = {"app_name": "Spotify"}
    assert rt.grade("open_app", want, "c", "browser_control.go_to", {})[:2] == (False, "open_app|browser_control.go_to")
    assert rt.grade("open_app", want, "c", "open_app", {"app_name": "Notepad"})[:2] == (False, "open_app|args:app_name")
    assert rt.grade("open_app", want, "c", "open_app", {"app_name": "spotify"})[0] is True


@pytest.mark.parametrize("blob", ['{"app_name": "Notion"}', "{'app_name': 'Notion'}",
                                  "{app_name: 'Notion'}", '{ app_name: "Notion" }'])
def test_parse_accepts_every_object_style_the_model_writes(blob):
    assert rt._parse(f"1 open_app {blob}", 1) == {1: ("open_app", {"app_name": "Notion"})}


def test_loose_obj_handles_js_literals():
    assert rt._loose_obj("{save: true, path: null, n: 5}") == {"save": True, "path": None, "n": 5}


def test_parse_reads_tool_action_and_args():
    picks = rt._parse('1. open_app {"app_name": "Spotify"}\n2) `media_control.next` {}\n'
                      "  3 - none\nnoise\n4 file_controller {'action': 'delete', 'path': 'a.txt'}\n9 open_app", 4)
    assert picks == {1: ("open_app", {"app_name": "Spotify"}), 2: ("media_control.next", {}),
                     3: ("none", {}), 4: ("file_controller.delete", {"path": "a.txt"})}


# ── Real runs in the sandbox ─────────────────────────────────────────────────

def test_sandbox_really_does_file_tasks_without_touching_real_desktop():
    from core import user_paths
    before = set(os.listdir(user_paths.desktop()))
    assert sb.run("file_controller", "create_folder", {"path": "exam test folder"}, {})[0]
    assert sb.run("file_controller", "move", {"path": "resume.pdf", "destination": "Downloads"}, {})[0]
    assert sb.run("file_controller", "read", {"path": "todo.txt"}, {})[0]
    ok, why = sb.run("file_controller", "delete", {"path": "no_such_file.txt"}, {})
    assert not ok
    ok, why = sb.run("file_controller", "create_folder", {"path": "C:/Users/x/Desktop/demo"}, {})
    assert not ok and "absolute" in why
    assert set(os.listdir(user_paths.desktop())) == before


def test_sandbox_checks_the_answer_not_just_that_it_ran():
    want = {"value": "#10", "from_unit": "@km", "to_unit": "@miles"}
    assert sb.run("unit_converter", "", {"value": 10, "from_unit": "km", "to_unit": "miles"}, want)[0]
    assert not sb.run("unit_converter", "", {"value": 100, "from_unit": "km", "to_unit": "miles"}, want)[0]


def test_sandbox_calendar_uses_temp_store_and_no_os_reminder(monkeypatch):
    import actions.reminder as rem
    monkeypatch.setattr(rem, "reminder", lambda *a, **k: pytest.fail("real OS reminder scheduled"))
    ok, detail = sb.run("calendar_agenda", "add", {"title": "exam", "date": "tomorrow", "time": "17:00"}, {})
    assert ok and "exam" in detail


def test_sandbox_reminder_never_schedules_a_real_task(monkeypatch):
    import actions.reminder as rem
    monkeypatch.setattr(rem, "_get_os", lambda: "windows")
    ok, detail = sb.run("reminder", "", {"date": "kal", "time": "18:30", "message": "drink water"}, {})
    assert ok and "Reminder set" in detail
    assert not sb.run("reminder", "", {"date": "someday", "time": "18:30"}, {})[0]


def test_rename_without_new_name_is_not_run():
    import importlib
    real = importlib.reload(sb)
    assert not real.should_run("file_controller", "rename", {"path": "a.txt"}, "c")
    assert real.should_run("file_controller", "rename", {"path": "a.txt", "new_name": "b.txt"}, "c")
    assert not real.should_run("send_message", "", {}, "c")


# ── Training loop ────────────────────────────────────────────────────────────

def test_perfect_batch_advances_cursor_and_learns_nothing(monkeypatch):
    rt.corpus()
    monkeypatch.setattr(rt, "_ask", _answer_with(_perfect))
    r = rt.run_batch(DECLS, 40)
    assert r == {"ok": 40, "total": 40, "seen": 40, "correct": 40, "round": 1}
    assert rt._read()["cursor"] == 40
    assert rt.format_routing_lessons() == ""


def test_wrong_details_become_a_lesson_with_the_exact_call(monkeypatch):
    rt.corpus()

    def garble(cmd):   # right tool, every detail blanked out
        label, want = rt._by_cmd[cmd]
        return f"{label} {{}}" if want else _perfect(cmd)

    monkeypatch.setattr(rt, "_ask", _answer_with(garble))
    rt.run_batch(DECLS, 300)
    lessons = rt.format_routing_lessons()
    assert "[TASK LESSONS" in lessons and "exactly right" in lessons
    assert rt._read()["tool_ok"] == rt._read()["seen"]


def test_repeated_mistake_becomes_a_lesson_then_fades(monkeypatch):
    rt.corpus()
    monkeypatch.setattr(rt, "_ask", _answer_with(lambda c: "none {}"))
    rt.run_batch(DECLS, 200)
    lessons = rt.format_routing_lessons()
    assert "[TASK LESSONS" in lessons and "NOT none" in lessons
    assert rt._read()["wrong"]

    monkeypatch.setattr(rt, "_ask", _answer_with(_perfect))
    for _ in range(5):
        rt.run_batch(DECLS, 200)
    assert rt._read()["wrong"] == {}
    assert rt.format_routing_lessons() == ""


def test_unparseable_reply_raises_and_saves_nothing(monkeypatch):
    monkeypatch.setattr(rt, "_ask", lambda p: "sorry, I can't help")
    with pytest.raises(RuntimeError):
        rt.run_batch(DECLS, 10)
    assert rt._read()["seen"] == 0


def test_changed_corpus_resets_progress():
    s = rt._fresh()
    s["seen"], s["fingerprint"] = 500, "stale"
    rt._write(s)
    assert rt._read()["seen"] == 0


def test_train_resumes_and_backs_off(monkeypatch):
    rt.corpus()
    calls = {"n": 0}

    def flaky(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("429")
        return _answer_with(_perfect)(prompt)

    monkeypatch.setattr(rt, "_ask", flaky)
    monkeypatch.setattr(rt, "load_tool_decls", lambda: DECLS)
    monkeypatch.setattr(rt.time, "sleep", lambda s: None)
    rt.train(limit=60, size=25, log=lambda m: None)
    assert rt._read()["seen"] == 60


def test_parallel_workers_route_each_command_once(monkeypatch):
    rt.corpus()
    asked = []
    monkeypatch.setattr(rt, "_ask", _answer_with(lambda c: asked.append(c) or _perfect(c)))
    monkeypatch.setattr(rt, "load_tool_decls", lambda: DECLS)
    rt.train(limit=300, size=25, workers=4, log=lambda m: None)
    s = rt._read()
    assert s["seen"] == 300 and s["cursor"] == 300
    assert len(asked) == len(set(asked)) == 300


def test_failed_batch_items_are_queued_not_lost(monkeypatch):
    rt.corpus()
    monkeypatch.setattr(rt, "_ask", lambda p: (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(ConnectionError):
        rt.run_batch(DECLS, 10)
    s = rt._read()
    assert s["cursor"] == 10 and set(s["wrong"]) == {str(k) for k in range(10)}
    monkeypatch.setattr(rt, "_ask", _answer_with(_perfect))
    for _ in range(4):
        rt.run_batch(DECLS, 30)
    assert rt._read()["wrong"] == {}


def test_train_waits_out_an_internet_outage(monkeypatch):
    rt.corpus()
    state = {"calls": 0}

    def ask(prompt):
        state["calls"] += 1
        if state["calls"] <= 10:   # well past the 8-strike limit
            raise OSError("getaddrinfo failed")
        return _answer_with(_perfect)(prompt)

    monkeypatch.setattr(rt, "_ask", ask)
    monkeypatch.setattr(rt, "_online", lambda: state["calls"] > 10)
    monkeypatch.setattr(rt, "load_tool_decls", lambda: DECLS)
    monkeypatch.setattr(rt.time, "sleep", lambda s: None)
    rt.train(limit=50, size=25, log=lambda m: None)
    assert rt._read()["seen"] == 50
