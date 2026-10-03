"""
judo_mail/look.py — JUDO Mail's look: palette (light / dark, following Windows),
Material-style icons drawn from SVG paths, sender avatars, friendly dates and
the window stylesheet.

The layout is the familiar three-pane mail client; the identity is JUDO's own —
JUDO Spectrum colours (violet, rose, amber, aqua), a violet→rose Compose
button, rounded cards on a softly tinted background, and a spectrum-ring app icon.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from functools import lru_cache

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import (QColor, QConicalGradient, QGuiApplication, QIcon, QPainter, QPainterPath, QPen,
                         QPixmap)
from PyQt6.QtSvg import QSvgRenderer

SPECTRUM = ("#7B5CFF", "#FF4D8D", "#FFA62B", "#12C9B4")       # JUDO's logo colours

PALETTES = {
    "light": dict(bg="#F3F2F9", card="#FFFFFF", text="#1C1A27", sub="#696579", faint="#9A97AA",
                  line="#E7E4F0", hover="#F2EFFB", select="#E9E3FF", accent="#6A4BF5", accent2="#FF4D8D",
                  chip="#F2F0F8", field="#F2F0F8", danger="#D93025", star="#F2A600"),
    "dark": dict(bg="#121118", card="#1B1A23", text="#ECEAF6", sub="#A9A5BD", faint="#77738A",
                 line="#2B2937", hover="#23212E", select="#2F2852", accent="#A792FF", accent2="#FF6FA3",
                 chip="#252331", field="#24222F", danger="#F28B82", star="#FFC94D"),
}


def palette() -> dict:
    """Light, dark, or "system" (Windows' own setting) — chosen in the account menu."""
    mode = load_settings().get("theme", "system")
    if mode not in PALETTES:
        mode = "dark" if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
    return PALETTES[mode]


ICONS = {   # Material Design paths, 24×24
    "inbox": "M19 3H4.99C3.88 3 3 3.9 3 5l.01 14c0 1.1.88 2 1.99 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 12h-4c0 "
             "1.66-1.35 3-3 3s-3-1.34-3-3H4.99V5H19v10z",
    "star": "M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z",
    "star_border": "M22 9.24l-7.19-.62L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21 12 17.27 18.18 21l-1.63-7.03L22 "
                   "9.24zM12 15.4l-3.76 2.27 1-4.28-3.32-2.88 4.38-.38L12 6.1l1.71 4.04 4.38.38-3.32 2.88 1 4.28L12 15.4z",
    "important": "M3.5 18.99l11 .01c.67 0 1.27-.33 1.63-.84L20.5 12l-4.37-6.16c-.36-.51-.96-.84-1.63-.84l-11 .01L8.34 12"
                 "L3.5 18.99z",
    "send": "M2.01 21L23 12 2.01 3 2 10l15 2-15 2z",
    "draft": "M21.99 8c0-.72-.37-1.35-.94-1.7L12 1 2.95 6.3C2.38 6.65 2 7.28 2 8v10c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2"
             "l-.01-10zM12 13L3.74 7.84 12 3l8.26 4.84L12 13z",
    "all": "M19 3H5c-1.1 0-2 .9-2 2v7c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 6h-4c0 1.62-1.38 3-3 3s-3-1.38"
           "-3-3H5V5h14v4zm-4 7h6v3c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2v-3h6c0 1.66 1.34 3 3 3s3-1.34 3-3z",
    "spam": "M15.73 3H8.27L3 8.27v7.46L8.27 21h7.46L21 15.73V8.27L15.73 3zM12 17.3c-.72 0-1.3-.58-1.3-1.3 0-.72.58-1.3 "
            "1.3-1.3.72 0 1.3.58 1.3 1.3 0 .72-.58 1.3-1.3 1.3zm1-4.3h-2V7h2v6z",
    "trash": "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
    "search": "M15.5 14h-.79l-.28-.27A6.471 6.471 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79"
              "l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z",
    "refresh": "M17.65 6.35A7.958 7.958 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08"
               "A5.99 5.99 0 0112 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z",
    "reply": "M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z",
    "reply_all": "M7 8V5l-7 7 7 7v-3l-4-4 4-4zm6 1V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z",
    "forward": "M14 9V5l7 7-7 7v-4.1c-5 0-8.5 1.6-11 5.1 1-5 4-10 11-11z",
    "unread": "M20 6H10v6H8V4h6V0H6v6H4v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 14H6V8h2v6h4V8h8v12z",
    "mail": "M20 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 "
            "5 8-5v2z",
    "attach": "M16.5 6v11.5c0 2.21-1.79 4-4 4s-4-1.79-4-4V5a2.5 2.5 0 015 0v10.5c0 .55-.45 1-1 1s-1-.45-1-1V6H10v9.5a2.5 "
              "2.5 0 005 0V5c0-2.21-1.79-4-4-4S7 2.79 7 5v12.5c0 3.04 2.46 5.5 5.5 5.5s5.5-2.46 5.5-5.5V6h-1.5z",
    "close": "M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
    "edit": "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a.996.996 0 000-1.41l-2.34-2.34a.996.996 0 "
            "00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z",
    "download": "M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z",
    "back": "M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z",
    "menu": "M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z",
    "chevron_left": "M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z",
    "chevron_right": "M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z",
    "archive": "M20.54 5.23l-1.39-1.68C18.88 3.21 18.47 3 18 3H6c-.47 0-.88.21-1.16.55L3.46 5.23C3.17 5.57 3 6.02 3 "
               "6.5V19c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6.5c0-.48-.17-.93-.46-1.27zM12 17.5L6.5 12H10v-2h4v2h3.5L12 "
               "17.5zM5.12 5l.81-1h12l.94 1H5.12z",
    "label": "M17.63 5.84C17.27 5.33 16.67 5 16 5L5 5.01C3.9 5.01 3 5.9 3 7v10c0 1.1.9 1.99 2 1.99L16 19c.67 0 1.27-.33 "
             "1.63-.84L22 12l-4.37-6.16z",
    "tag": "M21.41 11.58l-9-9C12.05 2.22 11.55 2 11 2H4c-1.1 0-2 .9-2 2v7c0 .55.22 1.05.59 1.42l9 9c.36.36.86.58 1.41.58"
           ".55 0 1.05-.22 1.41-.59l7-7c.37-.36.59-.86.59-1.41 0-.55-.23-1.06-.59-1.42zM5.5 7C4.67 7 4 6.33 4 5.5S4.67 "
           "4 5.5 4 7 4.67 7 5.5 6.33 7 5.5 7z",
    "people": "M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3"
              "S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 "
              "0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z",
    "info": "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z",
    "bag": "M18 6h-2c0-2.21-1.79-4-4-4S8 3.79 8 6H6c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-6"
           "-2c1.1 0 2 .9 2 2h-4c0-1.1.9-2 2-2zm6 16H6V8h2v2c0 .55.45 1 1 1s1-.45 1-1V8h4v2c0 .55.45 1 1 1s1-.45 1-1V8"
           "h2v12z",
    "drafts_read": "M21.99 8c0-.72-.37-1.35-.94-1.7L12 1 2.95 6.3C2.38 6.65 2 7.28 2 8v10c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2"
                   "l-.01-10zM12 13L3.74 7.84 12 3l8.26 4.84L12 13z",
    "camera": "M12 15.2a3.2 3.2 0 100-6.4 3.2 3.2 0 000 6.4zM9 2L7.17 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9"
              " 2-2V6c0-1.1-.9-2-2-2h-3.17L15 2H9zm3 15c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z",
    "person_add": "M15 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm-9-2V7H4v3H1v2h3v3h2v-3h3v-2H6zm9 4c-2.67 "
                  "0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z",
    "logout": "M17 7l-1.41 1.41L18.17 11H8v2h10.17l-2.58 2.58L17 17l5-5zM4 5h8V3H4c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h8v-2H4"
              "V5z",
    "manage": "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 4c1.93 0 3.5 1.57 3.5 3.5S13.93 13 12"
              " 13s-3.5-1.57-3.5-3.5S10.07 6 12 6zm0 14c-2.03 0-4.43-.82-6.14-2.88a9.947 9.947 0 0112.28 0C16.43 19.18 "
              "14.03 20 12 20z",
    "theme": "M12 3a9 9 0 109 9c0-.46-.04-.92-.1-1.36a5.389 5.389 0 01-4.4 2.26 5.403 5.403 0 01-3.14-9.8c-.44-.06-.9-.1"
             "-1.36-.1z",
}

SETTINGS_DIR = __import__("pathlib").Path.home() / ".judo" / "mail"


def photo_path(address: str):
    """Where a profile picture chosen for this account lives (local only)."""
    safe = "".join(c if c.isalnum() or c in "@._-" else "_" for c in (address or "account").lower())
    return SETTINGS_DIR / "photos" / f"{safe}.png"


def friendly_name(address: str) -> str:
    """The account's own name when Gmail told us (cached by the window), else a tidy
    guess from the address: riteshkumar90359@gmail.com -> "Riteshkumar"."""
    known = load_settings().get("names", {}).get((address or "").lower(), "")
    if known:
        return known
    local = (address or "").split("@")[0]
    words = [w for w in "".join(c if c.isalpha() else " " for c in local).split() if w]
    return " ".join(w.title() for w in words) or "there"


def remember_name(address: str, name: str) -> None:
    if address and name:
        s = load_settings()
        s.setdefault("names", {})[address.lower()] = name
        save_settings(s)


def load_settings() -> dict:
    import json
    try:
        return json.loads((SETTINGS_DIR / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(values: dict) -> None:
    import json
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    (SETTINGS_DIR / "settings.json").write_text(json.dumps(values, indent=1), encoding="utf-8")


@lru_cache(maxsize=256)
def icon(name: str, color: str, size: int = 20) -> QIcon:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="{color}" d="{ICONS[name]}"/></svg>'
    pm = QPixmap(QSize(size * 2, size * 2))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    QSvgRenderer(QByteArray(svg.encode())).render(p)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """JUDO Mail: an envelope inside the JUDO Spectrum ring (JUDO Browser has the J)."""
    out = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ring = QConicalGradient(size / 2, size / 2, 90)
        for i, c in enumerate(reversed(SPECTRUM)):        # counter-clockwise O-D-U-J = J-U-D-O clockwise
            ring.setColorAt(i / 4, QColor(c))
            ring.setColorAt((i + 1) / 4 - 0.0001, QColor(c))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ring)
        p.drawEllipse(0, 0, size, size)
        inset = size * 0.17
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
        w, h = size * 0.38, size * 0.27                    # envelope
        x, y = (size - w) / 2, (size - h) / 2
        p.setPen(QPen(QColor(SPECTRUM[0]), max(1.0, size * 0.045), cap=Qt.PenCapStyle.RoundCap,
                      join=Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(x, y, w, h), size * 0.03, size * 0.03)
        flap = QPainterPath()
        flap.moveTo(x, y)
        flap.lineTo(x + w / 2, y + h * 0.58)
        flap.lineTo(x + w, y)
        p.drawPath(flap)
        p.end()
        out.addPixmap(pm)
    return out


AVATAR_COLORS = ("#7B5CFF", "#E8457A", "#F08A24", "#11A894", "#3B82F6", "#9B51E0", "#D9485F", "#2BA160",
                 "#C27C0E", "#0E8FB5")


def avatar_color(key: str) -> str:
    """The same sender always gets the same colour."""
    digest = hashlib.md5((key or "?").lower().encode()).digest()
    return AVATAR_COLORS[digest[0] % len(AVATAR_COLORS)]


def initial(name: str) -> str:
    for ch in (name or "").removeprefix("To: "):
        if ch.isalnum():
            return ch.upper()
    return "?"


def when_text(when: datetime | None, fallback: str = "", now: datetime | None = None) -> str:
    """The list's date column: "3:45 PM" today, "12 Oct" this year, "12/10/24" before that."""
    if when is None:
        return fallback
    now = now or datetime.now().astimezone()          # this PC's clock and time zone
    if when.tzinfo and now.tzinfo:
        local = when.astimezone(now.tzinfo)
    elif when.tzinfo:
        local, now = when.astimezone(), now.astimezone()
    else:
        local = when
        now = now.replace(tzinfo=None)
    if local.date() == now.date():
        return local.strftime("%I:%M %p").lstrip("0")
    if local.year == now.year:
        return f"{local.day} {local:%b}"
    return local.strftime("%d/%m/%y")


def stylesheet(t: dict) -> str:
    return f"""
    QMainWindow, QDialog {{ background: {t['bg']}; }}
    QWidget {{ color: {t['text']}; font-family: 'Segoe UI'; font-size: 10pt; }}
    #Card {{ background: {t['card']}; border-radius: 16px; }}
    #Brand {{ font-size: 15pt; font-weight: 600; }}
    #Search {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: 21px; padding: 0 14px;
              min-height: 42px; max-height: 42px; font-size: 10.5pt; }}
    #Search:focus {{ background: {t['card']}; border: 1px solid {t['accent']}; }}
    #Compose {{ border: none; border-radius: 18px; padding: 14px 22px 14px 18px; color: #FFFFFF; font-size: 10.5pt;
               font-weight: 600; text-align: left;
               background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {SPECTRUM[0]}, stop:1 {SPECTRUM[1]}); }}
    #Compose:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #6A49F2, stop:1 #F23A7C); }}
    #Folders {{ background: transparent; border: none; outline: none; }}
    #Compose[compact="true"] {{ padding: 0; border-radius: 16px; text-align: center; }}
    #Position {{ color: {t['sub']}; font-size: 9pt; }}
    #Title {{ font-size: 15pt; font-weight: 600; }}
    #Sub, #Count {{ color: {t['sub']}; }}
    #Subject {{ font-size: 16pt; font-weight: 600; }}
    #Sender {{ font-weight: 600; font-size: 10.5pt; }}
    #Empty {{ color: {t['faint']}; font-size: 11pt; }}
    QToolButton {{ border: none; border-radius: 18px; padding: 7px; background: transparent; }}
    QToolButton:hover {{ background: {t['hover']}; }}
    QToolButton:disabled {{ background: transparent; }}
    #Pill {{ border: 1px solid {t['line']}; border-radius: 18px; padding: 8px 18px; background: {t['card']};
            color: {t['text']}; font-weight: 600; }}
    #Pill:hover {{ background: {t['hover']}; }}
    #Primary {{ border: none; border-radius: 18px; padding: 9px 26px; background: {t['accent']}; color: #FFFFFF;
               font-weight: 600; }}
    #Primary:hover {{ background: {SPECTRUM[0]}; }}
    #Primary:disabled {{ background: {t['faint']}; }}
    #Chip {{ border: 1px solid {t['line']}; border-radius: 12px; padding: 6px 12px; background: {t['chip']};
            color: {t['text']}; text-align: left; }}
    #Chip:hover {{ border-color: {t['accent']}; }}
    #Field {{ border: none; border-bottom: 1px solid {t['line']}; padding: 10px 4px; background: transparent;
             font-size: 10.5pt; }}
    #Field:focus {{ border-bottom: 1px solid {t['accent']}; }}
    #Body {{ border: none; background: transparent; font-size: 10.5pt; }}
    #Paper {{ background: #FFFFFF; border: 1px solid {t['line']}; border-radius: 12px; }}
    #ComposeHead {{ background: {t['select']}; border-top-left-radius: 14px; border-top-right-radius: 14px; }}
    #ComposeHead QLabel {{ font-weight: 600; }}
    QListView {{ background: transparent; border: none; outline: none; }}
    QScrollBar:vertical {{ width: 10px; background: transparent; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{ background: {t['line']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['faint']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QSplitter::handle {{ background: transparent; }}
    QStatusBar {{ color: {t['sub']}; background: {t['bg']}; }}
    QToolTip {{ background: {t['card']}; color: {t['text']}; border: 1px solid {t['line']}; padding: 4px 8px; }}
    QMenu {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: 8px; padding: 6px 0; }}
    QMenu::item {{ padding: 7px 26px; }}
    QMenu::item:selected {{ background: {t['hover']}; }}
    QMenu::separator {{ height: 1px; background: {t['line']}; margin: 6px 0; }}
    QMenu::indicator {{ width: 0; }}
    #Tab {{ border: none; border-bottom: 3px solid transparent; border-radius: 0; padding: 12px 18px 10px 14px;
           background: transparent; color: {t['sub']}; font-weight: 600; text-align: left; }}
    #Tab:hover {{ background: {t['hover']}; }}
    #Tab[on="true"] {{ color: {t['accent']}; border-bottom: 3px solid {t['accent']}; }}
    #Toolbar QCheckBox::indicator {{ width: 16px; height: 16px; border: 2px solid {t['faint']}; border-radius: 4px; }}
    #Toolbar QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}
    #Toolbar QCheckBox::indicator:indeterminate {{ background: {t['faint']}; border-color: {t['faint']}; }}
    #Selected {{ color: {t['accent']}; font-weight: 600; }}
    #Unsub {{ border: 1px solid {t['line']}; border-radius: 10px; padding: 2px 10px; background: {t['chip']};
             color: {t['sub']}; font-size: 9pt; }}
    #Unsub:hover {{ color: {t['accent']}; border-color: {t['accent']}; }}
    #Hint {{ color: {t['sub']}; font-size: 9pt; }}
    #SignTitle {{ font-size: 19pt; font-weight: 500; }}
    #SignSub {{ color: {t['sub']}; font-size: 10pt; }}
    #Box {{ border: 1px solid {t['faint']}; border-radius: 6px; padding: 13px 14px; background: transparent;
           font-size: 11pt; color: {t['text']}; }}
    #Box:focus {{ border: 2px solid {t['accent']}; padding: 12px 13px; }}
    #Box[error="true"] {{ border: 2px solid {t['danger']}; padding: 12px 13px; }}
    #Error {{ color: {t['danger']}; font-size: 9pt; }}
    #Who {{ border: 1px solid {t['line']}; border-radius: 17px; padding: 5px 14px 5px 6px; background: transparent;
           color: {t['text']}; font-weight: 500; }}
    #Who:hover {{ background: {t['hover']}; }}
    #Flat {{ border: none; background: transparent; color: {t['accent']}; font-weight: 600; padding: 9px 16px;
            border-radius: 18px; }}
    #Flat:hover {{ background: {t['hover']}; }}
    #Link {{ border: none; background: transparent; color: {t['accent']}; text-align: left; padding: 0; }}
    #Link:hover {{ text-decoration: underline; }}
    #AccountCard {{ background: {t['card']}; }}
    #AcctPopup {{ background: {t['bg']}; border: 1px solid {t['line']}; border-radius: 28px; }}
    #AcctEmail {{ color: {t['text']}; font-weight: 600; }}
    #AcctHi {{ font-size: 17pt; color: {t['text']}; }}
    #AcctManage {{ border: 1px solid {t['faint']}; border-radius: 20px; padding: 9px 22px; background: transparent;
                  color: {t['accent']}; font-weight: 600; }}
    #AcctManage:hover {{ background: {t['hover']}; }}
    #AcctRow {{ border: none; border-radius: 4px; padding: 13px 18px; background: {t['card']}; color: {t['text']};
               text-align: left; font-size: 10pt; }}
    #AcctRow:hover {{ background: {t['hover']}; }}
    #AcctRow[first="true"] {{ border-top-left-radius: 20px; border-top-right-radius: 20px; }}
    #AcctRow[last="true"] {{ border-bottom-left-radius: 20px; border-bottom-right-radius: 20px; }}
    #Seg {{ border: 1px solid {t['line']}; border-radius: 16px; padding: 7px 0; background: {t['card']};
           color: {t['sub']}; font-weight: 600; }}
    #Seg:hover {{ background: {t['hover']}; }}
    #Seg:checked {{ background: {t['select']}; color: {t['accent']}; border-color: {t['accent']}; }}
    #Cam {{ border: 2px solid {t['bg']}; border-radius: 15px; background: {t['card']}; padding: 4px; }}
    #Cam:hover {{ background: {t['hover']}; }}
    #Fine {{ color: {t['faint']}; font-size: 8.5pt; }}
    """
