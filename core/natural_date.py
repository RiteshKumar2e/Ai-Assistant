"""
core/natural_date.py — turn a spoken day into a real date.

The live model is asked to pass YYYY-MM-DD, but it often passes the user's own
words instead ("monday", "kal", "parso", "5th October"), and the calendar and
reminder tools used to reject everything except "today"/"tomorrow". Found by
the 20,000-task exam (core/routing_trainer): "project review monday ko agenda
me add karo" failed every time it was really run.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_WEEKDAYS = {
    "monday": 0, "mon": 0, "somvar": 0, "somwar": 0, "सोमवार": 0,
    "tuesday": 1, "tue": 1, "mangalvar": 1, "mangalwar": 1, "मंगलवार": 1,
    "wednesday": 2, "wed": 2, "budhvar": 2, "budhwar": 2, "बुधवार": 2,
    "thursday": 3, "thu": 3, "guruvar": 3, "guruwar": 3, "गुरुवार": 3,
    "friday": 4, "fri": 4, "shukravar": 4, "shukrawar": 4, "शुक्रवार": 4,
    "saturday": 5, "sat": 5, "shanivar": 5, "shaniwar": 5, "शनिवार": 5,
    "sunday": 6, "sun": 6, "ravivar": 6, "raviwar": 6, "itvaar": 6, "रविवार": 6,
}
_RELATIVE = {"today": 0, "aaj": 0, "आज": 0, "tomorrow": 1, "kal": 1, "कल": 1,
             "day after tomorrow": 2, "parso": 2, "parson": 2, "परसों": 2, "next week": 7, "agle hafte": 7}
_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
            "%d %B", "%d %b", "%B %d", "%b %d")


def resolve(raw: str, today: date | None = None) -> date | None:
    """A spoken or written day → the next matching date (never one in the past
    for weekdays / day-month without a year). None if it cannot be read."""
    today = today or datetime.now().date()
    text = re.sub(r"\s+", " ", (raw or "").strip().lower()).strip(" .,")
    text = re.sub(r"\b(on|ko|ka|ki|the|this|is|coming|aane wale|wale)\b", " ", text).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return today
    if text in _RELATIVE:
        return today + timedelta(days=_RELATIVE[text])
    nxt = text.startswith(("next ", "agle "))
    word = text.split(" ", 1)[1] if nxt else text
    if word in _WEEKDAYS:
        ahead = (_WEEKDAYS[word] - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead + (7 if nxt and ahead < 7 else 0))
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text).replace(",", "")
    for fmt in _FORMATS:
        try:
            d = datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
        if "%Y" not in fmt:
            d = d.replace(year=today.year)
            if d < today:
                d = d.replace(year=today.year + 1)
        return d
    return None


_UNIT_SECS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
              "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "minat": 60, "मिनट": 60,
              "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
              "ghanta": 3600, "ghante": 3600, "ghanton": 3600, "घंटे": 3600, "घंटा": 3600,
              "day": 86400, "days": 86400, "din": 86400}
_WORD_NUM = {"a": 1, "an": 1, "one": 1, "ek": 1, "do": 2, "two": 2, "teen": 3, "three": 3,
             "aadha": 0.5, "aadhe": 0.5, "half": 0.5, "half an": 0.5, "dedh": 1.5, "dhai": 2.5}
_REL_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?|half an|" + "|".join(sorted(_WORD_NUM, key=len, reverse=True)) +
                     r")\s*(" + "|".join(sorted(_UNIT_SECS, key=len, reverse=True)) + r")\b")
_PART_OF_DAY = {"subah": "am", "morning": "am", "dopahar": "pm", "afternoon": "pm",
                "shaam": "pm", "sham": "pm", "evening": "pm", "raat": "pm", "night": "pm", "tonight": "pm"}


def _relative(text: str) -> timedelta | None:
    total, found = 0.0, False
    for num, unit in _REL_RE.findall(text):
        n = _WORD_NUM.get(num)
        total += (float(num) if n is None else n) * _UNIT_SECS[unit]
        found = True
    return timedelta(seconds=total) if found and total > 0 else None


def _clock(text: str):
    t = text.strip().upper().replace(".", "")
    for fmt in ("%H:%M", "%I:%M %p", "%I %p", "%I:%M%p", "%I%p", "%H:%M:%S"):
        try:
            return datetime.strptime(t, fmt).time()
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|o'?clock)?", text.lower())
    if not m:
        return None
    h, mins, suffix = int(m.group(1)), int(m.group(2) or 0), m.group(3) or ""
    part = next((v for k, v in _PART_OF_DAY.items() if k in text.lower()), "")
    if suffix == "pm" or (part == "pm" and h < 12):
        h = h + 12 if h < 12 else h
    elif (suffix == "am" or part == "am") and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mins < 60):
        return None
    return datetime.min.replace(hour=h, minute=mins).time()


def parse_when(date_str: str, time_str: str, now: datetime | None = None) -> datetime | None:
    """Date + time as the model passes them for a reminder → a datetime.
    Handles "10 minutes" / "2 ghante baad" / "in an hour" (from now), and a
    spoken day + a clock time ("kal", "shaam 4 baje", "5 pm", "17:00")."""
    now = now or datetime.now()
    rel = _relative(f"{time_str} {date_str}".lower())
    if rel:
        return (now + rel).replace(second=0, microsecond=0)
    day, clock = resolve(date_str, now.date()), _clock(time_str or "")
    if day is None or clock is None:
        return None
    return datetime.combine(day, clock)
