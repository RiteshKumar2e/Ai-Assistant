"""core/natural_date.py — spoken days → dates (found by the task exam)."""
from datetime import date

import pytest

from core.natural_date import resolve

SAT = date(2026, 9, 26)   # a Saturday


@pytest.mark.parametrize("raw,want", [
    ("", SAT), ("today", SAT), ("aaj", SAT), ("kal", date(2026, 9, 27)), ("tomorrow", date(2026, 9, 27)),
    ("parso", date(2026, 9, 28)), ("next week", date(2026, 10, 3)),
    ("monday", date(2026, 9, 28)), ("Monday ko", date(2026, 9, 28)), ("somvar", date(2026, 9, 28)),
    ("saturday", date(2026, 10, 3)), ("next friday", date(2026, 10, 9)), ("friday", date(2026, 10, 2)),
    ("on 5th October", date(2026, 10, 5)), ("5 oct", date(2026, 10, 5)), ("March 12", date(2027, 3, 12)),
    ("2026-12-25", date(2026, 12, 25)), ("25/12/2026", date(2026, 12, 25)),
])
def test_resolves_spoken_days(raw, want):
    assert resolve(raw, today=SAT) == want


def test_unreadable_is_none():
    assert resolve("whenever", today=SAT) is None


def test_calendar_accepts_weekday_and_reminder_accepts_12h_time(monkeypatch, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("cal", "plugins/calendar_agenda.py")
    cal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cal)
    monkeypatch.setattr(cal, "_STORE", tmp_path / "c.json")
    assert cal.run({"action": "add", "title": "project review", "date": "monday"}).startswith("Added")

    import actions.reminder as rem
    scheduled = []
    monkeypatch.setattr(rem, "_get_os", lambda: "windows")
    monkeypatch.setattr(rem, "_write_notify_script", lambda *a: tmp_path / "n.py")
    monkeypatch.setattr(rem, "_schedule_windows", lambda dt, *a: scheduled.append(dt) or "job")
    out = rem.reminder({"date": "kal", "time": "5 pm", "message": "call mom"})
    assert out.startswith("Reminder set") and scheduled[0].hour == 17


@pytest.mark.parametrize("d,t,want", [
    ("", "10 minutes", (27, 12, 10)), ("", "2 ghante baad", (27, 14, 0)), ("", "current_time + 10 mins", (27, 12, 10)),
    ("", "aadha ghanta", (27, 12, 30)), ("", "an hour", (27, 13, 0)), ("", "10min", (27, 12, 10)),
    ("kal", "subah 7 baje", (28, 7, 0)), ("", "shaam 4 baje", (27, 16, 0)), ("today", "5 pm", (27, 17, 0)),
    ("monday", "8:30", (28, 8, 30)), ("", "raat 11 baje", (27, 23, 0)),
])
def test_parse_when_reads_spoken_reminder_times(d, t, want):
    from datetime import datetime
    from core.natural_date import parse_when
    got = parse_when(d, t, now=datetime(2026, 9, 27, 12, 0))
    assert (got.day, got.hour, got.minute) == want


def test_parse_when_unreadable_is_none():
    from core.natural_date import parse_when
    assert parse_when("", "whenever") is None
