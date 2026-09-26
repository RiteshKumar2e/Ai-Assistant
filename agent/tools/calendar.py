"""
agent/tools/calendar.py — CalendarProvider: list, free/busy, create, update, cancel.

    CalendarProvider
    ├── LocalCalendar    ~/.judo/calendar.json — the SAME store the calendar_agenda voice
    │                    plugin uses, so both see the same events. No account needed.
    │                    Cannot invite anyone (attendees are only noted).
    └── GoogleCalendar   Google Calendar API over the shared Google OAuth; real
                         free/busy and real invitations (sendUpdates=all).

create() always checks the requested slot for conflicts first and refuses to
double-book unless `allow_conflict` is set — the agent then has to tell the
user and choose. Times are ISO local datetimes ("2026-09-25T17:00").
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path

from agent.tools.base import ToolResult

LOCAL_STORE = Path.home() / ".judo" / "calendar.json"


def parse_dt(s: str) -> datetime:
    s = (s or "").strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if "T" in s or " " in s else s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date/time {s!r} — use ISO like 2026-09-25T17:00")


def overlaps(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


class CalendarProvider(ABC):
    name = ""
    can_invite = False

    @abstractmethod
    def available(self) -> tuple[bool, str]: ...
    @abstractmethod
    def events(self, start: datetime, end: datetime) -> list[dict]: ...   # {id, title, start, end, attendees}
    @abstractmethod
    def create(self, title, start, end, attendees, location, notes) -> ToolResult: ...
    @abstractmethod
    def update(self, event_id, **changes) -> ToolResult: ...
    @abstractmethod
    def cancel(self, event_id) -> ToolResult: ...


class LocalCalendar(CalendarProvider):
    name = "local"

    def __init__(self, store: Path = LOCAL_STORE):
        self.store = store

    def available(self):
        return True, ""

    def _load(self) -> list[dict]:
        try:
            d = json.loads(self.store.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, evs: list[dict]) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps(evs, indent=2), encoding="utf-8")

    @staticmethod
    def _span(e: dict) -> tuple[datetime, datetime]:
        s = parse_dt(f"{e['date']}T{e['time']}" if e.get("time") else e["date"])
        return s, s + timedelta(minutes=int(e.get("duration_min") or (60 if e.get("time") else 1440)))

    def events(self, start, end):
        out = []
        for e in self._load():
            try:
                s, f = self._span(e)
            except (ValueError, KeyError):
                continue
            if overlaps(s, f, start, end):
                out.append({"id": str(e.get("id")), "title": e.get("title", ""), "start": s, "end": f, "attendees": e.get("attendees", [])})
        return sorted(out, key=lambda x: x["start"])

    def create(self, title, start, end, attendees, location, notes):
        evs = self._load()
        new_id = max((int(e.get("id", 0)) for e in evs if str(e.get("id", "")).isdigit()), default=0) + 1
        evs.append({"id": new_id, "date": start.date().isoformat(), "time": start.strftime("%H:%M"), "title": title,
                    "notes": notes, "duration_min": int((end - start).total_seconds() // 60), "attendees": attendees, "location": location})
        self._save(evs)
        if not any(str(e.get("id")) == str(new_id) for e in self._load()):
            return ToolResult.fail("The local calendar did not save the event.")
        note = " Attendees noted but NOT invited (local calendar can't send invitations)." if attendees else ""
        return ToolResult(True, f"Added to the local calendar: {title}, {start:%a %d %b %H:%M}–{end:%H:%M} (id {new_id}).{note}",
                          {"event_id": str(new_id)})

    def update(self, event_id, **ch):
        evs = self._load()
        for e in evs:
            if str(e.get("id")) == str(event_id):
                if ch.get("title"): e["title"] = ch["title"]
                if ch.get("start"):
                    e["date"], e["time"] = ch["start"].date().isoformat(), ch["start"].strftime("%H:%M")
                if ch.get("end") and ch.get("start"):
                    e["duration_min"] = int((ch["end"] - ch["start"]).total_seconds() // 60)
                self._save(evs)
                return ToolResult(True, f"Updated local event {event_id}.", {"event_id": str(event_id)})
        return ToolResult.fail(f"No local event with id {event_id}.")

    def cancel(self, event_id):
        evs = self._load()
        keep = [e for e in evs if str(e.get("id")) != str(event_id)]
        if len(keep) == len(evs):
            return ToolResult.fail(f"No local event with id {event_id}.")
        self._save(keep)
        return ToolResult(True, f"Removed local event {event_id}.", {"event_id": str(event_id)})


class GoogleCalendar(CalendarProvider):
    name = "google"
    can_invite = True

    def available(self):
        from agent.integrations.google_oauth import status
        ok, why = status()
        return ok, "" if ok else why.replace("Google account", "Google Calendar")

    def _svc(self):
        from agent.integrations.google_oauth import service
        return service("calendar", "v3")

    @staticmethod
    def _iso(d: datetime) -> str:
        return d.astimezone().isoformat()

    def events(self, start, end):
        items = self._svc().events().list(calendarId="primary", timeMin=self._iso(start), timeMax=self._iso(end),
                                          singleEvents=True, orderBy="startTime", maxResults=100).execute().get("items", [])
        out = []
        for it in items:
            s = it["start"].get("dateTime") or it["start"].get("date"); f = it["end"].get("dateTime") or it["end"].get("date")
            out.append({"id": it["id"], "title": it.get("summary", "(busy)"), "start": datetime.fromisoformat(s).replace(tzinfo=None),
                        "end": datetime.fromisoformat(f).replace(tzinfo=None), "attendees": [a["email"] for a in it.get("attendees", [])]})
        return out

    def create(self, title, start, end, attendees, location, notes):
        body = {"summary": title, "location": location, "description": notes,
                "start": {"dateTime": self._iso(start)}, "end": {"dateTime": self._iso(end)},
                "attendees": [{"email": a} for a in attendees if "@" in a]}
        ev = self._svc().events().insert(calendarId="primary", body=body, sendUpdates="all" if attendees else "none").execute()
        if not ev.get("id"):
            return ToolResult.fail("Google Calendar did not return an event id.")
        inv = f" Invitations sent by Google to {', '.join(attendees)}." if attendees else ""
        return ToolResult(True, f"Google Calendar created “{title}” {start:%a %d %b %H:%M}–{end:%H:%M}.{inv} {ev.get('htmlLink', '')}",
                          {"event_id": ev["id"], "url": ev.get("htmlLink", "")})

    def update(self, event_id, **ch):
        patch = {}
        if ch.get("title"): patch["summary"] = ch["title"]
        if ch.get("start"): patch["start"] = {"dateTime": self._iso(ch["start"])}
        if ch.get("end"): patch["end"] = {"dateTime": self._iso(ch["end"])}
        if ch.get("attendees"): patch["attendees"] = [{"email": a} for a in ch["attendees"]]
        ev = self._svc().events().patch(calendarId="primary", eventId=event_id, body=patch, sendUpdates="all").execute()
        return ToolResult(True, f"Google Calendar updated “{ev.get('summary')}”.", {"event_id": ev["id"]})

    def cancel(self, event_id):
        self._svc().events().delete(calendarId="primary", eventId=event_id, sendUpdates="all").execute()
        return ToolResult(True, f"Google Calendar cancelled event {event_id} (attendees notified).", {"event_id": event_id})


class Calendar:
    def __init__(self, preferred: str = ""):
        self.providers = {"google": GoogleCalendar(), "local": LocalCalendar()}
        self.preferred = preferred

    def status(self) -> dict:
        return {n: p.available() for n, p in self.providers.items()}

    def pick(self) -> CalendarProvider:
        """Preferred provider if connected, else Google if connected, else local (always works)."""
        for n in (self.preferred, "google"):
            if n in self.providers and self.providers[n].available()[0]:
                return self.providers[n]
        return self.providers["local"]

    def _safe(self, fn) -> ToolResult:
        try:
            return fn()
        except ValueError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"Calendar error: {str(e)[:300]}")

    def list(self, start: str = "", end: str = "") -> ToolResult:
        def go():
            s = parse_dt(start) if start else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            f = parse_dt(end) if end else s + timedelta(days=7)
            p = self.pick()
            evs = p.events(s, f)
            rows = [f"[{e['id']}] {e['start']:%a %d %b %H:%M}–{e['end']:%H:%M} {e['title']}" for e in evs]
            return ToolResult(True, f"{p.name} calendar, {s:%d %b} → {f:%d %b}:\n" + ("\n".join(rows) or "Nothing scheduled."), {"count": len(rows)})
        return self._safe(go)

    def free_slots(self, date: str, duration_min: int = 60, day_start: str = "09:00", day_end: str = "19:00") -> ToolResult:
        def go():
            d = parse_dt(date).date()
            s, f = parse_dt(f"{d}T{day_start}"), parse_dt(f"{d}T{day_end}")
            p = self.pick()
            busy = [(e["start"], e["end"]) for e in p.events(s, f)]
            slots, cur, step = [], s, timedelta(minutes=30)
            while cur + timedelta(minutes=duration_min) <= f and len(slots) < 8:
                if not any(overlaps(cur, cur + timedelta(minutes=duration_min), b0, b1) for b0, b1 in busy):
                    slots.append(f"{cur:%H:%M}–{cur + timedelta(minutes=duration_min):%H:%M}")
                cur += step
            return ToolResult(True, f"Free {duration_min}-min slots on {d:%a %d %b} ({p.name}): " + (", ".join(slots) or "none"))
        return self._safe(go)

    def create(self, title: str, start: str, end: str = "", duration_min: int = 60, attendees: list | None = None,
               location: str = "", notes: str = "", allow_conflict: bool = False) -> ToolResult:
        def go():
            s = parse_dt(start)
            f = parse_dt(end) if end else s + timedelta(minutes=int(duration_min or 60))
            if f <= s:
                return ToolResult.fail("End time must be after start time.")
            if s < datetime.now() - timedelta(minutes=5):
                return ToolResult.fail(f"{s:%a %d %b %H:%M} is in the past — check the date.")
            p = self.pick()
            clash = [e for e in p.events(s, f) if overlaps(s, f, e["start"], e["end"])]
            if clash and not allow_conflict:
                return ToolResult.fail("Conflict: " + "; ".join(f"{e['title']} {e['start']:%H:%M}–{e['end']:%H:%M}" for e in clash)
                                       + ". Ask the user whether to pick another time or double-book (allow_conflict).", "conflict")
            return p.create(title, s, f, list(attendees or []), location, notes)
        return self._safe(go)

    def update(self, event_id: str, title: str = "", start: str = "", end: str = "", attendees: list | None = None) -> ToolResult:
        return self._safe(lambda: self.pick().update(event_id, title=title, start=parse_dt(start) if start else None,
                                                        end=parse_dt(end) if end else None, attendees=attendees))

    def cancel(self, event_id: str) -> ToolResult:
        return self._safe(lambda: self.pick().cancel(event_id))

