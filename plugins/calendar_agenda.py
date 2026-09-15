"""
calendar_agenda.py — Local calendar/agenda: add events, list what's coming up,
remove events. Pure local storage (~/.judo/calendar.json), no external account
needed. When an event has a time, it also schedules an OS notification through
the same mechanism as actions/reminder.py, so JUDO actually reminds you at
that moment — the calendar entry itself is never lost even if that scheduling
fails (e.g. unsupported OS scheduler).
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

_STORE = Path.home() / ".judo" / "calendar.json"


def _load() -> list[dict]:
    if not _STORE.exists():
        return []
    try:
        data = json.loads(_STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(events: list[dict]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(events, indent=2), encoding="utf-8")


def _resolve_date(raw: str) -> str | None:
    raw = (raw or "").strip().lower()
    today = datetime.now().date()
    if raw in ("", "today"):
        return today.isoformat()
    if raw == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _friendly_time(t: str) -> str:
    if not t:
        return ""
    try:
        return datetime.strptime(t, "%H:%M").strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return t


def _friendly_date(d: str) -> str:
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        return d
    today = datetime.now().date()
    if dt == today:
        return "today"
    if dt == today + timedelta(days=1):
        return "tomorrow"
    return dt.strftime("%B %d")


def _schedule_notification(date: str, time_: str, title: str, player) -> None:
    try:
        from actions.reminder import reminder as _os_reminder
        _os_reminder({"date": date, "time": time_, "message": title}, player=player)
    except Exception as e:
        print(f"[Calendar] Could not schedule OS notification (event still saved): {e}")


def _add(p: dict, player) -> str:
    date = _resolve_date(p.get("date", ""))
    title = str(p.get("title", "")).strip()
    time_ = str(p.get("time", "")).strip()
    notes = str(p.get("notes", "")).strip()

    if not date:
        return "Please give me a valid date (YYYY-MM-DD, 'today' or 'tomorrow')."
    if not title:
        return "Please tell me what the event is."
    if time_:
        try:
            datetime.strptime(time_, "%H:%M")
        except ValueError:
            return "Please give the time in 24-hour HH:MM format."

    events = _load()
    new_id = (max((e.get("id", 0) for e in events), default=0)) + 1
    events.append({"id": new_id, "date": date, "time": time_, "title": title, "notes": notes})
    _save(events)

    if time_:
        _schedule_notification(date, time_, title, player)

    when = _friendly_date(date) + (f" at {_friendly_time(time_)}" if time_ else "")
    return f"Added '{title}' to your calendar for {when}."


def _list(p: dict) -> str:
    scope = str(p.get("date", "today")).strip().lower()
    events = _load()
    if not events:
        return "Your calendar is empty."

    if scope == "all":
        matches = events
    elif scope == "week":
        today = datetime.now().date()
        end = today + timedelta(days=7)
        matches = [e for e in events if today.isoformat() <= e.get("date", "") <= end.isoformat()]
    else:
        date = _resolve_date(scope)
        if not date:
            return "Please give a valid date, 'today', 'tomorrow', 'week' or 'all'."
        matches = [e for e in events if e.get("date") == date]

    if not matches:
        label = "your agenda" if scope in ("all", "week") else f"{_friendly_date(_resolve_date(scope) or scope)}"
        return f"Nothing on {label}."

    matches.sort(key=lambda e: (e.get("date", ""), e.get("time", "")))
    lines = []
    for e in matches:
        when = _friendly_date(e["date"])
        t = _friendly_time(e.get("time", ""))
        lines.append(f"{when}{' at ' + t if t else ''}: {e['title']}")

    return f"You have {len(matches)} event(s) — " + "; ".join(lines) + "."


def _remove(p: dict) -> str:
    query = str(p.get("title", "")).strip().lower()
    date = _resolve_date(p.get("date", "")) if p.get("date") else None
    if not query:
        return "Please tell me which event to remove (by title)."

    events = _load()
    keep, removed = [], []
    for e in events:
        matches_title = query in e.get("title", "").lower()
        matches_date = date is None or e.get("date") == date
        if matches_title and matches_date:
            removed.append(e)
        else:
            keep.append(e)

    if not removed:
        return f"I couldn't find an event matching '{query}'."

    _save(keep)
    titles = ", ".join(e["title"] for e in removed)
    return f"Removed {len(removed)} event(s): {titles}."


PLUGIN = {
    "name": "calendar_agenda",
    "description": (
        "Manages a local calendar/agenda: add an event, list what's coming up, or "
        "remove an event. Use for requests like 'add a meeting tomorrow at 5pm', "
        "'what's on my calendar today', 'what do I have this week', 'remove the "
        "dentist appointment'. This is a personal local agenda — do NOT use this "
        "for one-off timed alerts (use 'reminder' for those)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "add | list | remove"},
            "date": {
                "type": "STRING",
                "description": "YYYY-MM-DD, 'today', 'tomorrow' — or for list: also 'week' / 'all'",
            },
            "time": {"type": "STRING", "description": "24h HH:MM, optional, for 'add'"},
            "title": {"type": "STRING", "description": "Event title (required for add/remove)"},
            "notes": {"type": "STRING", "description": "Optional extra notes, for 'add'"},
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "")).strip().lower()
    try:
        if action == "add":
            result = _add(p, player)
        elif action == "list":
            result = _list(p)
        elif action == "remove":
            result = _remove(p)
        else:
            return f"Unknown calendar action: '{action}'. Use add, list or remove."
    except Exception as e:
        return f"Sir, the calendar plugin failed: {e}"

    if player:
        try:
            player.write_log(f"[calendar] {result}")
        except Exception:
            pass
    return result
