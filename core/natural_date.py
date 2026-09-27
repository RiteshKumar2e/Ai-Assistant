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
