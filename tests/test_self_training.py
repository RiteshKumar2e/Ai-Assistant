"""
Tests for memory/self_training.py — the lesson store.

Every test points STORE_PATH at a tmp_path file, so none of them touch the
real memory/self_training.json.
"""
import json

import pytest

from memory import self_training as st


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "STORE_PATH", tmp_path / "self_training.json")


def test_log_and_retrieve_understanding_mistake():
    st.log_understanding_mistake("turn off the lights", "toggle_wifi")
    out = st.format_lessons_for_prompt()
    assert "turn off the lights" in out
    assert "toggle_wifi" in out


def test_log_and_retrieve_coding_lesson():
    st.log_coding_lesson("python", "SyntaxError: bad indent", "Watch indentation.")
    out = st.get_coding_lessons("python")
    assert "Watch indentation." in out


def test_get_coding_lessons_filters_by_language():
    st.log_coding_lesson("python", "err1", "python lesson")
    st.log_coding_lesson("javascript", "err2", "js lesson")
    py_out = st.get_coding_lessons("python")
    assert "python lesson" in py_out
    assert "js lesson" not in py_out


def test_dedup_on_repeated_identical_mistake():
    st.log_understanding_mistake("cmd", "wrong")
    st.log_understanding_mistake("cmd", "wrong")
    st.log_understanding_mistake("cmd", "wrong")
    data = st._read()
    matching = [e for e in data["understanding"] if e["command"] == "cmd"]
    assert len(matching) == 1


def test_cap_enforced_on_understanding():
    for i in range(st.MAX_UNDERSTANDING + 10):
        st.log_understanding_mistake(f"cmd-{i}", f"wrong-{i}")
    data = st._read()
    assert len(data["understanding"]) == st.MAX_UNDERSTANDING


def test_empty_command_or_action_not_logged():
    st.log_understanding_mistake("", "something")
    st.log_understanding_mistake("something", "")
    data = st._read()
    assert data["understanding"] == []


def test_missing_store_file_reads_as_empty():
    assert st._read() == {"understanding": [], "coding": []}
    assert st.format_lessons_for_prompt() == ""
    assert st.get_coding_lessons("python") == ""


def test_malformed_json_store_recovers_to_empty(tmp_path):
    st.STORE_PATH.write_text("{ not json", encoding="utf-8")
    assert st._read() == {"understanding": [], "coding": []}


def test_non_dict_store_root_recovers_to_empty():
    st.STORE_PATH.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert st._read() == {"understanding": [], "coding": []}


def test_non_list_section_recovers_to_empty_list():
    st.STORE_PATH.write_text(json.dumps({"understanding": "oops", "coding": []}), encoding="utf-8")
    data = st._read()
    assert data["understanding"] == []


def test_non_dict_entries_in_list_are_dropped():
    st.STORE_PATH.write_text(json.dumps({
        "understanding": [1, "garbage", None, {"command": "real", "wrong_action": "x"}],
        "coding": [],
    }), encoding="utf-8")
    data = st._read()
    assert len(data["understanding"]) == 1
    assert data["understanding"][0]["command"] == "real"


def test_dict_entry_missing_keys_does_not_crash_prompt_format():
    st.STORE_PATH.write_text(json.dumps({
        "understanding": [{"command": "no wrong_action field here"}],
        "coding": [],
    }), encoding="utf-8")
    out = st.format_lessons_for_prompt()   # must not raise KeyError
    assert out == ""


def test_dict_entry_missing_lesson_field_does_not_crash():
    st.STORE_PATH.write_text(json.dumps({
        "understanding": [],
        "coding": [{"language": "python", "error_signature": "e"}],   # no "lesson"
    }), encoding="utf-8")
    out = st.get_coding_lessons("python")   # must not raise KeyError
    assert out == ""


def test_write_failure_does_not_raise(monkeypatch):
    """Simulated disk-full: the caller must never see the OSError."""
    def boom(self, *a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(st.Path, "write_text", boom)
    st.log_understanding_mistake("disk full case", "wrong")   # must not raise


def test_write_failure_leaves_existing_store_untouched(monkeypatch):
    st.log_understanding_mistake("first, good entry", "wrong")

    def boom(self, *a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(st.Path, "write_text", boom)
    st.log_understanding_mistake("second entry during disk-full", "wrong")

    data = st._read()
    commands = [e["command"] for e in data["understanding"]]
    assert "first, good entry" in commands
    assert "second entry during disk-full" not in commands
