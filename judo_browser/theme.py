"""
judo_browser/theme.py — Chrome's colours (light / dark / incognito) and its
Material icons, drawn from inline SVG so they stay sharp at any scale.
"""
from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QColor, QConicalGradient, QFont, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PyQt6.QtSvg import QSvgRenderer

THEMES = {
    "light": dict(frame="#DEE1E6", tab="#FFFFFF", toolbar="#FFFFFF", omni="#F1F3F4", omni_focus="#FFFFFF",
                  text="#202124", sub="#5F6368", icon="#5F6368", hover="#E8EAED", hover_frame="#CDD0D6",
                  accent="#1A73E8", border="#DADCE0", popup="#FFFFFF", select="#E8F0FE", dark=False),
    "dark": dict(frame="#202124", tab="#35363A", toolbar="#35363A", omni="#202124", omni_focus="#202124",
                 text="#E8EAED", sub="#9AA0A6", icon="#C4C7C5", hover="#4A4C50", hover_frame="#2E3033",
                 accent="#8AB4F8", border="#5F6368", popup="#2D2E30", select="#394457", dark=True),
    "incognito": dict(frame="#202124", tab="#35363A", toolbar="#35363A", omni="#202124", omni_focus="#202124",
                      text="#E8EAED", sub="#9AA0A6", icon="#C4C7C5", hover="#4A4C50", hover_frame="#2E3033",
                      accent="#8AB4F8", border="#5F6368", popup="#2D2E30", select="#394457", dark=True),
}

# Material Design icon paths (24×24 viewBox)
PATHS = {
    "back": "M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z",
    "forward": "M12 4l-1.41 1.41L16.17 11H4v2h12.17l-5.58 5.59L12 20l8-8z",
    "reload": "M17.65 6.35A7.958 7.958 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08"
              "A5.99 5.99 0 0112 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z",
    "close": "M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
    "home": "M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z",
    "star": "M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z",
    "star_border": "M22 9.24l-7.19-.62L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21 12 17.27 18.18 21l-1.63-7.03L22 9.24z"
                   "M12 15.4l-3.76 2.27 1-4.28-3.32-2.88 4.38-.38L12 6.1l1.71 4.04 4.38.38-3.32 2.88 1 4.28L12 15.4z",
    "more": "M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"
            "m0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z",
    "add": "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z",
    "download": "M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z",
    "lock": "M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10"
            "c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1"
            " 1.71 0 3.1 1.39 3.1 3.1v2z",
    "info": "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z",
    "search": "M15.5 14h-.79l-.28-.27A6.471 6.471 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28"
              "v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z",
    "minimize": "M5 11.5h14v1H5z",
    "maximize": "M5 5v14h14V5H5zm1 1h12v12H6V6z",
    "restore": "M8 5v3H5v11h11v-3h3V5H8zm7 13H6V9h9v9zm3-3h-2V8H9V6h9v9z",
    "key": "M12.65 10A5.99 5.99 0 007 6c-3.31 0-6 2.69-6 6s2.69 6 6 6a5.99 5.99 0 005.65-4H17v4h4v-4h2v-4H12.65z"
           "M7 14c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z",
    "volume": "M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0014 7.97v8.05c1.48-.73 2.5-2.25 2.5-4.02z",
    "muted": "M16.5 12A4.5 4.5 0 0014 7.97v2.21l2.45 2.45c.03-.2.05-.41.05-.63zM4.27 3L3 4.27 7.73 9H3v6h4l5 5"
             "v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06a8.99 8.99 0 003.69-1.81L19.73 21 21 19.73l-9-9L4.27 3z"
             "M12 4L9.91 6.09 12 8.18V4z",
    "globe": "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93"
             " 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1"
             "-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z",
    "incognito": "M17.06 13c-1.86 0-3.42 1.33-3.82 3.1-.95-.41-1.82-.3-2.48-.01C10.35 14.31 8.79 13 6.94 13 4.77 13"
                 " 3 14.79 3 17s1.77 4 3.94 4c2.06 0 3.74-1.62 3.9-3.68.34-.24 1.23-.69 2.32.02.18 2.05 1.84 3.66"
                 " 3.9 3.66 2.17 0 3.94-1.79 3.94-4s-1.77-4-3.94-4zM22 10.5H2V12h20v-1.5zm-6.47-7.87c-.22-.49-.78-.75"
                 "-1.31-.58L12 2.79l-2.23-.74-.05-.01c-.53-.15-1.09.13-1.29.64L6 9h12l-2.44-6.32-.03-.05z",
    "spinner": "M12 4V2A10 10 0 002 12h2a8 8 0 018-8z",
}


# "Customize JUDO" colour themes, like Chrome's: one seed colour tints the whole frame.
COLORS = {"Blue": "#4285F4", "Cool grey": "#8D9BAF", "Aqua": "#24A1C1", "Green": "#34A853",
          "Viridian": "#0E9F8A", "Citron": "#C0B42C", "Orange": "#F29900", "Apricot": "#E8845C",
          "Rose": "#D9576E", "Pink": "#E25FA4", "Fuchsia": "#B44FC9", "Violet": "#7E69E0"}


def _mix(a: str, b: str, amount: float) -> str:
    """`amount` of colour a over colour b."""
    ca, cb = QColor(a), QColor(b)
    ch = lambda x, y: round(x * amount + y * (1 - amount))
    return QColor(ch(ca.red(), cb.red()), ch(ca.green(), cb.green()), ch(ca.blue(), cb.blue())).name().upper()


MODES = {"light": "Light", "dark": "Dark", "system": "Device"}   # "Device" follows Windows' own setting


def tinted(base: dict, seed: str, dark: bool) -> dict:
    """Chrome-style colour theme: a strongly tinted frame, lightly tinted tabs and toolbar."""
    if not QColor(seed).isValid():
        return base
    t = dict(base)
    if dark:
        t.update(frame=_mix(seed, "#1B1B1F", .22), hover_frame=_mix(seed, "#2A2A2E", .30),
                 tab=_mix(seed, "#2B2B2F", .14), toolbar=_mix(seed, "#2B2B2F", .14),
                 omni=_mix(seed, "#1B1B1F", .12), omni_focus=_mix(seed, "#1B1B1F", .12),
                 hover=_mix(seed, "#3C3C40", .22), select=_mix(seed, "#2B2B2F", .35),
                 accent=_mix(seed, "#FFFFFF", .55), popup=_mix(seed, "#2D2E30", .10))
    else:
        t.update(frame=_mix(seed, "#FFFFFF", .30), hover_frame=_mix(seed, "#FFFFFF", .42),
                 tab=_mix(seed, "#FFFFFF", .06), toolbar=_mix(seed, "#FFFFFF", .06),
                 omni=_mix(seed, "#FFFFFF", .14), omni_focus="#FFFFFF",
                 hover=_mix(seed, "#FFFFFF", .20), select=_mix(seed, "#FFFFFF", .16),
                 accent=_mix(seed, "#000000", .80), border=_mix(seed, "#DADCE0", .20))
    return t


def resolve(name: str, color: str = "") -> dict:
    """"system" follows Windows' own light/dark setting, like Chrome does;
    `color` (a hex seed from COLORS or the picker) tints it."""
    if name == "system":
        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        name = "dark" if dark else "light"
    base = THEMES.get(name, THEMES["light"])
    return tinted(base, color, name == "dark") if color else base


@lru_cache(maxsize=256)
def icon(name: str, color: str, size: int = 20) -> QIcon:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="{color}" d="{PATHS[name]}"/></svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ratio = 2   # draw at 2× so it stays crisp on high-DPI screens
    pm = QPixmap(QSize(size * ratio, size * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return QIcon(pm)


@lru_cache(maxsize=1)
def judo_icon() -> QIcon:
    """JUDO Browser's own icon — a ring in the logo's four colours around a blue J —
    for the taskbar, the window and JUDO's own pages' tabs (instead of Python's)."""
    out = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ring = QConicalGradient(size / 2, size / 2, 90)
        for i, c in enumerate(("#EA4335", "#4285F4", "#34A853", "#FBBC05")):   # hard quarters, clockwise from top
            ring.setColorAt(i / 4, QColor(c))
            ring.setColorAt((i + 1) / 4 - 0.0001, QColor(c))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ring)
        p.drawEllipse(0, 0, size, size)
        inset = size * 0.17
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(int(inset), int(inset), int(size - 2 * inset), int(size - 2 * inset))
        f = QFont("Segoe UI", -1, QFont.Weight.Black)
        f.setPixelSize(max(7, int(size * 0.52)))
        p.setFont(f)
        p.setPen(QColor("#4285F4"))
        p.drawText(pm.rect().adjusted(0, -int(size * 0.02), 0, 0), Qt.AlignmentFlag.AlignCenter, "J")
        p.end()
        out.addPixmap(pm)
    return out


def palette(t: dict) -> QPalette:
    pal = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "toolbar"), (QPalette.ColorRole.Base, "popup"),
                      (QPalette.ColorRole.AlternateBase, "omni"), (QPalette.ColorRole.Button, "toolbar"),
                      (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "accent"),
                      (QPalette.ColorRole.PlaceholderText, "sub"), (QPalette.ColorRole.ToolTipBase, "popup"),
                      (QPalette.ColorRole.ToolTipText, "text")):
        pal.setColor(role, QColor(t[key]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(t["toolbar"]))
    return pal


def stylesheet(t: dict) -> str:
    return f"""
    QMainWindow {{ background: {t['frame']}; }}
    #TitleBar {{ background: {t['frame']}; }}
    #Toolbar, #BookmarkBar, #InfoBar {{ background: {t['toolbar']}; }}
    #BookmarkBar {{ border-bottom: 1px solid {t['border']}; }}
    QTabBar {{ background: transparent; }}
    QTabBar::tab {{ background: transparent; color: {t['sub']}; border: none; padding: 7px 6px 8px 10px;
                    margin: 6px 0 0 0; border-top-left-radius: 9px; border-top-right-radius: 9px; }}
    QTabBar::tab:hover:!selected {{ background: {t['hover_frame']}; }}
    QTabBar::tab:selected {{ background: {t['tab']}; color: {t['text']}; }}
    QTabBar::scroller {{ width: 22px; }}
    QToolButton {{ border: none; border-radius: 15px; padding: 5px; color: {t['text']}; background: transparent; }}
    QToolButton:hover {{ background: {t['hover']}; }}
    QToolButton:disabled {{ opacity: 0.4; }}
    QToolButton::menu-indicator {{ image: none; }}
    #TabClose {{ border-radius: 9px; padding: 2px; }}
    #WinButton {{ border-radius: 0; padding: 8px 16px; }}
    #WinButton:hover {{ background: {t['hover_frame']}; }}
    #WinClose {{ border-radius: 0; padding: 8px 16px; }}
    #WinClose:hover {{ background: #E81123; }}
    #BookmarkBar QToolButton {{ border-radius: 12px; padding: 4px 8px; font-size: 12px; }}
    #Omnibox {{ background: {t['omni']}; color: {t['text']}; border: 2px solid transparent; border-radius: 17px;
               padding: 4px 8px; font-size: 14px; selection-background-color: {t['accent']}; }}
    #Omnibox:focus {{ background: {t['omni_focus']}; border: 2px solid {t['accent']}; }}
    #Suggestions {{ background: {t['popup']}; color: {t['text']}; border: 1px solid {t['border']};
                   border-radius: 10px; padding: 6px 0; font-size: 14px; outline: 0; }}
    #Suggestions::item {{ padding: 7px 14px; }}
    #Suggestions::item:selected {{ background: {t['select']}; color: {t['text']}; }}
    #FindBar {{ background: {t['popup']}; border: 1px solid {t['border']}; border-radius: 10px; }}
    #FindBar QLineEdit {{ border: none; background: transparent; color: {t['text']}; font-size: 14px; }}
    #StatusBubble {{ background: {t['popup']}; color: {t['sub']}; border: 1px solid {t['border']};
                    border-top-right-radius: 6px; padding: 3px 8px; font-size: 12px; }}
    #InfoBar {{ border-bottom: 1px solid {t['border']}; }}
    #InfoBar QLabel {{ color: {t['text']}; }}
    QPushButton {{ background: {t['toolbar']}; color: {t['accent']}; border: 1px solid {t['border']};
                   border-radius: 16px; padding: 6px 16px; }}
    QPushButton:hover {{ background: {t['hover']}; }}
    QPushButton#Primary {{ background: {t['accent']}; color: {t['toolbar']}; border: none; }}
    QMenu {{ background: {t['popup']}; color: {t['text']}; border: 1px solid {t['border']}; border-radius: 8px;
             padding: 6px 0; }}
    QMenu::item {{ padding: 7px 36px 7px 20px; }}
    QMenu::item:selected {{ background: {t['hover']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 0; }}
    QDialog, QTabWidget::pane {{ background: {t['toolbar']}; color: {t['text']}; }}
    QListWidget, QTableWidget {{ background: {t['toolbar']}; color: {t['text']}; border: 1px solid {t['border']};
                                 border-radius: 8px; }}
    QLineEdit, QComboBox, QSpinBox {{ background: {t['omni']}; color: {t['text']}; border: 1px solid {t['border']};
                                      border-radius: 6px; padding: 5px 8px; }}
    QLabel {{ color: {t['text']}; }}
    QCheckBox, QRadioButton {{ color: {t['text']}; }}
    """
