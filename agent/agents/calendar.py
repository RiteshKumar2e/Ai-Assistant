"""CalendarAgent — inspect the calendar, find free time, and create / move /
cancel events. Always checks conflicts before creating; anything that sends
invitations (attendees) is confirmed first."""
from __future__ import annotations

from agent.agents.base import Agent, int_arg, list_arg
from agent.tools.base import EXTERNAL, READ, WRITE


def _risk(args: dict) -> str:
    return EXTERNAL if list_arg(args.get("attendees")) else WRITE


class CalendarAgent(Agent):
    name = "CalendarAgent"
    description = "Calendar (Google Calendar via OAuth, or the local calendar): list events, free/busy, create/update/cancel events, invite people."

    def tools(self):
        c = self.cc.calendar
        detail = lambda a: ("Calendar invitation", f"{a.get('title')} at {a.get('start')}\nInvite: {', '.join(list_arg(a.get('attendees')))}")
        return [
            self.tool("list_events", "Events between two ISO datetimes (default: the next 7 days).", {"start": "ISO", "end": "ISO"},
                      lambda start="", end="": c.list(start, end), READ),
            self.tool("find_free_slots", "Free slots on a day.", {"date": "YYYY-MM-DD", "duration_min": "int"},
                      lambda date, duration_min=60: c.free_slots(date, int_arg(duration_min, 60)), READ),
            self.tool("create_event", "Create an event after checking conflicts. Times are local ISO (resolve 'tomorrow at 5' using the current "
                      "date in the context). With attendees, invitations are sent (user confirms first).",
                      {"title": "title", "start": "ISO start", "end": "ISO end (or duration_min)", "duration_min": "int", "attendees": "emails",
                       "location": "text", "notes": "text", "allow_conflict": "bool — only after the user agreed to double-book"},
                      lambda title, start, end="", duration_min=60, attendees="", location="", notes="", allow_conflict=False:
                      c.create(title, start, end, int_arg(duration_min, 60), list_arg(attendees), location, notes, bool(allow_conflict)),
                      _risk, confirm_detail=detail),
            self.tool("update_event", "Move/rename an event or change attendees (attendees are notified).",
                      {"event_id": "id", "title": "new title", "start": "ISO", "end": "ISO", "attendees": "emails"},
                      lambda event_id, title="", start="", end="", attendees="": c.update(event_id, title, start, end, list_arg(attendees) or None),
                      EXTERNAL, confirm_detail=lambda a: ("Update event", f"Event {a.get('event_id')}: {a}")),
            self.tool("cancel_event", "Cancel an event (attendees are notified).", {"event_id": "id"},
                      lambda event_id: c.cancel(event_id), "destructive", confirm_detail=lambda a: ("Cancel event", f"Cancel event {a.get('event_id')}?")),
        ]
