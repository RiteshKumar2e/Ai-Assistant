"""actions/proactive.py — when JUDO starts a conversation on its own, what it asks,
and what it learns from the answer."""
import time
from datetime import datetime

import pytest

import actions.proactive as pro
from actions.proactive import ProactiveEngine

AFTERNOON = datetime(2026, 10, 2, 15, 0)


@pytest.fixture(autouse=True)
def own_greetings_file(tmp_path, monkeypatch):
    """Never read or write the real memory/greetings.json."""
    monkeypatch.setattr(pro, "GREETINGS", tmp_path / "greetings.json")


def test_asks_after_ten_quiet_minutes_then_every_ten():
    e = ProactiveEngine()
    e.mark_greeted(AFTERNOON)                                     # already greeted this afternoon
    assert e.min_silence_secs == 600 and e.cooldown_secs == 600
    now = time.monotonic()
    e._last_triggered = now - 700
    assert not e.should_trigger(now - 300, when=AFTERNOON)        # spoke 5 min ago
    assert e.should_trigger(now - 610, when=AFTERNOON)
    e.mark_triggered()
    assert not e.should_trigger(now - 5000, when=AFTERNOON)       # just asked
    e._last_triggered = time.monotonic() - 601
    assert e.should_trigger(now - 5000, when=AFTERNOON)           # still quiet: ask again 10 min later


def test_round_the_clock_even_at_3am():
    e = ProactiveEngine()
    night = datetime(2026, 10, 3, 3, 0)
    e.mark_greeted(night)
    now = time.monotonic()
    e._last_triggered = now - 700
    assert e.should_trigger(now - 99999, when=night)


def test_day_parts_and_their_greetings():
    g = lambda h: pro.day_period(datetime(2026, 10, 2, h, 30))[1:3]
    assert g(6) == ("morning", "Good morning") and g(12) == ("afternoon", "Good afternoon")
    assert g(18) == ("evening", "Good evening") and g(22) == ("night", "Good night")
    assert pro.day_period(datetime(2026, 10, 3, 2, 0))[0] == "2026-10-02:night"   # 2 am = last night


def test_greets_once_per_part_of_the_day_without_waiting_ten_minutes(monkeypatch):
    import memory.memory_manager as mm
    monkeypatch.setattr(mm, "format_memory_for_prompt", lambda m: "")
    monkeypatch.setattr(pro, "recent_questions", lambda n=8: [])
    e = ProactiveEngine()
    now = time.monotonic()
    morning = datetime(2026, 10, 2, 9, 0)
    assert e.greeting_due(morning) and e.should_trigger(now - 90, when=morning)   # 1.5 min quiet is enough
    assert not e.should_trigger(now - 30, when=morning)                           # not while they're talking
    e.mark_greeted(morning)
    assert not e.greeting_due(datetime(2026, 10, 2, 11, 59))
    assert e.greeting_due(datetime(2026, 10, 2, 12, 0))                           # afternoon begins
    assert ProactiveEngine().greeted == "2026-10-02:morning"                      # remembered across restarts


def test_opener_greets_when_a_new_part_of_the_day_began(monkeypatch):
    import memory.memory_manager as mm
    monkeypatch.setattr(mm, "format_memory_for_prompt", lambda m: "")
    monkeypatch.setattr(pro, "recent_questions", lambda n=8: [])
    e = ProactiveEngine()
    p = e.build_prompt({})
    _, _, greeting, _ = pro.day_period()
    assert e.last_focus == "greeting" and greeting in p and not e.greeting_due()
    e.build_prompt({})                                            # same part of the day: no second hello
    assert e.last_focus != "greeting"


def test_opener_is_human_rotates_and_avoids_repeats(monkeypatch):
    import memory.memory_manager as mm
    monkeypatch.setattr(mm, "format_memory_for_prompt", lambda m: "name: Ritesh")
    monkeypatch.setattr(pro, "recent_questions", lambda n=8: ["Khana kha liya?"])   # asked earlier
    e = ProactiveEngine()
    e.mark_greeted()
    kinds = set()
    for _ in range(len(ProactiveEngine.FOCUSES)):
        p = e.build_prompt({}, recent_turns=["User: kal interview hai"])
        assert "ONE question" in p and "Never default to English" in p and "save_memory" in p
        assert "kal interview hai" in p and "name: Ritesh" in p and "Khana kha liya?" in p
        kinds.add(e.last_focus)
        e.mark_triggered()
    assert len(kinds) >= 3


def test_learning_keeps_facts_and_the_exchange(tmp_path):
    lines = ["JUDO: Ritesh, weekend ka kya plan hai?", "User: Goa ja raha hoon dosto ke saath",
             "JUDO: Wah! Kaun kaun?", "User: Aman aur Priya"]
    q, a = pro.split_exchange(lines, "JUDO")
    assert q.startswith("Ritesh, weekend") and "Goa" in a and "Priya" in a
    facts = pro.parse_facts('Sure!\n```json\n[{"category": "wishes", "key": "Weekend Plan!", "value": "Goa trip"},'
                            ' {"category": "bogus", "key": "friend", "value": "Aman"}, {"key": "", "value": "x"}]\n```')
    assert facts == [{"category": "wishes", "key": "weekend_plan", "value": "Goa trip"},
                     {"category": "notes", "key": "friend", "value": "Aman"}]
    assert pro.parse_facts("no json here") == [] and pro.parse_facts("[not json]") == []
    log = tmp_path / "conv.jsonl"
    pro.record_exchange("follow_up", q, a, facts, path=log)
    pro.record_exchange("wellbeing", "Thak gaye?", "", [], path=log)
    assert pro.recent_questions(8, path=log) == [q, "Thak gaye?"]
    assert '"answered": false' in log.read_text(encoding="utf-8").splitlines()[-1]
