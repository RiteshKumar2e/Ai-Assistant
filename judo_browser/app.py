"""
judo_browser/app.py — JUDO Browser: a Chrome-like browser on the Chromium
engine (Qt WebEngine).

    python -m judo_browser [url]

Chrome's layout (tabs in the title bar, omnibox with padlock and star, ⋮
menu, bookmarks bar) and its everyday features: tab pin/mute/duplicate,
reopen closed tab, session restore, per-site zoom, find bar, downloads,
history, bookmarks, passwords (DPAPI-encrypted), incognito, print, save page,
DevTools, view source, site permissions, light/dark themes, Chrome shortcuts.

Profile data (cookies/logins, cache, bookmarks, history, settings) lives in
~/.judo/browser/; incognito windows use an in-memory profile and record nothing.
"""
from __future__ import annotations

import html
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QCursor, QKeySequence
from PyQt6.QtWebEngineCore import (QWebEngineDownloadRequest, QWebEnginePage, QWebEngineProfile,
                                   QWebEngineSettings, qWebEngineChromiumVersion)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
                             QMessageBox, QPushButton, QSizePolicy, QStackedWidget, QTabBar, QToolButton,
                             QVBoxLayout, QWidget, QWidgetAction)

from judo_browser import dialogs, passwords, theme
from judo_browser.app_data import DATA, SEARCH_ENGINES, load, load_settings, save

HOME_URL = "judo:newtab"
MAX_HISTORY = 5000
EDGE = 6                   # px band along a frameless window's border that resizes it
DEFAULT_TILES = [("Gmail", "https://mail.google.com"), ("YouTube", "https://www.youtube.com"),
                 ("ChatGPT", "https://chatgpt.com"), ("GitHub", "https://github.com"),
                 ("Google", "https://www.google.com"), ("News", "https://news.google.com")]
_search_base = SEARCH_ENGINES["google"][1]


def to_url(text: str) -> QUrl:
    """What was typed in the address bar -> a URL: a real address, else a web search."""
    t = text.strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I) or t.startswith(("about:", "file:", "view-source:")):
        return QUrl(t)
    if " " not in t and (re.match(r"^localhost(:\d+)?(/|$)", t) or re.match(r"^[\w-]+(\.[\w-]+)+(:\d+)?(/.*)?$", t)):
        return QUrl(("http://" if t.startswith("localhost") else "https://") + t)
    return QUrl(_search_base + QUrl.toPercentEncoding(t).data().decode())


def chrome_user_agent() -> str:
    """Sites (Google sign-in above all) treat an unknown engine as an old,
    unsafe browser; present as the Chrome this engine actually is."""
    major = qWebEngineChromiumVersion().split(".")[0]
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")


def new_tab_html(browser, t: dict) -> str:
    """Chrome's New Tab page: search box and most-visited shortcuts (letter
    tiles — no favicon lookups leave the machine)."""
    counts = Counter(QUrl(h["url"]).host() for h in browser.history if QUrl(h["url"]).host())
    tiles = [(host.removeprefix("www.").split(".")[0].capitalize(), f"https://{host}") for host, _ in counts.most_common(8)]
    for name, url in DEFAULT_TILES:
        if len(tiles) < 8 and all(QUrl(url).host() != QUrl(u).host() for _, u in tiles):
            tiles.append((name, url))
    links = "".join(f'<a href="{html.escape(u)}"><b>{html.escape(n[:1])}</b><span>{html.escape(n)}</span></a>'
                    for n, u in tiles)
    engine = SEARCH_ENGINES[browser.settings["search_engine"]]
    action, param = engine[1].split("?")[0], engine[1].split("?")[1].rstrip("=")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>New Tab</title><style>
body{{margin:0;background:{t['toolbar']};color:{t['text']};font:14px 'Segoe UI',system-ui,sans-serif;
 display:flex;justify-content:center;min-height:100vh}}main{{margin-top:18vh;width:min(584px,92vw);text-align:center}}
h1{{font-weight:500;font-size:56px;letter-spacing:.06em;margin:0 0 30px;color:{t['text']}}}
form{{display:flex;align-items:center;background:{t['omni']};border-radius:24px;padding:0 20px;height:46px;
 border:1px solid {t['border']}}}form:focus-within{{box-shadow:0 1px 6px #0003}}
input{{flex:1;border:0;background:transparent;color:{t['text']};font-size:16px;outline:none;margin-left:12px}}
.tiles{{display:grid;grid-template-columns:repeat(4,112px);gap:8px;justify-content:center;margin-top:34px}}
a{{display:flex;flex-direction:column;align-items:center;padding:16px 4px;border-radius:8px;color:{t['text']};
 text-decoration:none}}a:hover{{background:{t['hover']}}}
a b{{width:48px;height:48px;border-radius:50%;background:{t['omni']};display:grid;place-items:center;font-size:20px;
 margin-bottom:10px;color:{t['accent']}}}a span{{font-size:13px;max-width:100px;overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap}}</style></head><body><main><h1>JUDO</h1>
<form action="{action}"><span style="color:{t['sub']}">🔍</span>
<input name="{param}" autofocus placeholder="Search {engine[0]} or type a URL"></form>
<div class="tiles">{links}</div></main></body></html>"""


CRASH_HTML = """<!doctype html><meta charset="utf-8"><body style="font:15px 'Segoe UI';display:grid;place-items:center;
height:90vh;background:#fff;color:#202124"><div><h2>Aw, Snap!</h2><p>Something went wrong while displaying this page.</p>
<p style="color:#5F6368">Reload the tab (F5) to try again.</p></div>"""


# ── engine plumbing ─────────────────────────────────────────────────────────

class Page(QWebEnginePage):
    def __init__(self, profile, win: "MainWindow"):
        super().__init__(profile, win)
        self.win = win
        self.fullScreenRequested.connect(self._fullscreen)
        if hasattr(self, "permissionRequested"):          # Qt >= 6.8
            self.permissionRequested.connect(self._permission)

    def createWindow(self, kind):
        bg = kind == QWebEnginePage.WebWindowType.WebBrowserBackgroundTab
        return self.win.new_tab(background=bg, blank=True).page()   # the engine loads the target itself

    def javaScriptConsoleMessage(self, *a):   # pages' console noise stays out of JUDO's log
        pass

    def _fullscreen(self, req):
        req.accept()
        self.win.set_fullscreen(req.toggleOn())

    def _permission(self, perm):
        kind = re.sub(r"(?<!^)([A-Z])", r" \1", perm.permissionType().name).lower()
        ask = QMessageBox.question(self.win, "Permission", f"{perm.origin().host()} wants to: {kind}\n\nAllow?")
        perm.grant() if ask == QMessageBox.StandardButton.Yes else perm.deny()


class View(QWebEngineView):
    def contextMenuEvent(self, e):
        menu = self.createStandardContextMenu()
        sel = self.page().selectedText().strip()
        if sel:
            menu.insertAction(menu.actions()[0] if menu.actions() else None, QAction(
                f"Search {SEARCH_ENGINES[self.window().browser.settings['search_engine']][0]} for “{sel[:30]}”",
                menu, triggered=lambda: self.window().new_tab(to_url(sel))))
        menu.addSeparator()
        menu.addAction("Inspect", self.window().devtools)
        menu.popup(e.globalPos())


class TabStrip(QTabBar):
    """Chrome's tab strip: tabs share the width (max 240px), pinned tabs are
    icon-only, empty strip drags the window, middle-click closes a tab."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win, self.room = win, 1000
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setIconSize(QSize(16, 16))
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def tabSizeHint(self, i):
        if self.win.is_pinned(i):
            return QSize(52, 40)
        unpinned = sum(1 for j in range(self.count()) if not self.win.is_pinned(j)) or 1
        room = self.room - 52 * (self.count() - unpinned)
        return QSize(int(max(76, min(240, room / unpinned))), 40)

    def minimumTabSizeHint(self, i):
        return self.tabSizeHint(i)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.tabAt(e.position().toPoint()) < 0:
            self.window().windowHandle().startSystemMove()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        i = self.tabAt(e.position().toPoint())
        if e.button() == Qt.MouseButton.MiddleButton and i >= 0:
            self.win.close_tab(i)
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.tabAt(e.position().toPoint()) < 0:
            self.win.toggle_maximized()
        else:
            super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        i = self.tabAt(e.pos())
        if i >= 0:
            self.win.tab_menu(i, e.globalPos())


class TitleBar(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__(objectName="TitleBar")
        self.win = win

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        self.win.toggle_maximized()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.win.tab_strip.room = max(200, self.width() - 240)
        self.win.tab_strip.updateGeometry()


class Tabs:
    """The QTabWidget-like view of the tab strip + page stack that
    judo_browser/control.py (JUDO's voice control) talks to."""

    def __init__(self, win: "MainWindow"):
        self.win = win

    def count(self) -> int:
        return self.win.tab_strip.count()

    def widget(self, i: int):
        return self.win.tab_strip.tabData(i) if 0 <= i < self.count() else None

    def currentIndex(self) -> int:
        return self.win.tab_strip.currentIndex()

    def setCurrentIndex(self, i: int) -> None:
        self.win.tab_strip.setCurrentIndex(i)

    def indexOf(self, view) -> int:
        return next((i for i in range(self.count()) if self.widget(i) is view), -1)

    def currentWidget(self):
        return self.widget(self.currentIndex())


class EdgeResizer(QObject):
    """A frameless window has no border to drag: resize from its outer EDGE px."""

    def __init__(self, app):
        super().__init__(app)
        self._cursor = False

    def _edges(self, w, gp: QPoint):
        if w.isMaximized() or w.isFullScreen():
            return Qt.Edge(0)
        r, edges = w.frameGeometry(), Qt.Edge(0)
        if gp.x() - r.left() < EDGE:
            edges |= Qt.Edge.LeftEdge
        if r.right() - gp.x() < EDGE:
            edges |= Qt.Edge.RightEdge
        if gp.y() - r.top() < EDGE:
            edges |= Qt.Edge.TopEdge
        if r.bottom() - gp.y() < EDGE:
            edges |= Qt.Edge.BottomEdge
        return edges

    def eventFilter(self, obj, e):
        if e.type() not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.HoverMove):
            return False
        w = obj.window() if isinstance(obj, QWidget) else None
        if not isinstance(w, MainWindow):
            return False
        edges = self._edges(w, QCursor.pos())
        if e.type() == QEvent.Type.MouseButtonPress and edges and e.button() == Qt.MouseButton.LeftButton:
            w.windowHandle().startSystemResize(edges)
            return True
        shape = {Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor, Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
                 Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor, Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
                 Qt.Edge.TopEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeFDiagCursor,
                 Qt.Edge.BottomEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeFDiagCursor,
                 Qt.Edge.TopEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeBDiagCursor,
                 Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeBDiagCursor}.get(edges)
        if shape and not self._cursor:
            QApplication.setOverrideCursor(shape)
            self._cursor = True
        elif shape and self._cursor:
            QApplication.changeOverrideCursor(shape)
        elif not shape and self._cursor:
            QApplication.restoreOverrideCursor()
            self._cursor = False
        return False


# ── the browser (shared by every window) ────────────────────────────────────

class Browser:
    def __init__(self, app: QApplication):
        self.app = app
        DATA.mkdir(parents=True, exist_ok=True)
        self.settings = load_settings()
        self.profile = QWebEngineProfile("judo", app)
        self.profile.setPersistentStoragePath(str(DATA / "profile"))
        self.profile.setCachePath(str(DATA / "cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        if hasattr(self.profile, "setPersistentPermissionsPolicy"):   # remember "Allow camera" per site
            self.profile.setPersistentPermissionsPolicy(QWebEngineProfile.PersistentPermissionsPolicy.StoreOnDisk)
        self.incognito = QWebEngineProfile(app)   # no storage name = off the record: nothing hits the disk
        for p in (self.profile, self.incognito):
            p.setHttpUserAgent(chrome_user_agent())
            p.downloadRequested.connect(self._download)
        self.passwords = passwords.PasswordStore()
        passwords.install_script(self.profile)
        self.bookmarks: list[dict] = load("bookmarks.json", [])
        self.history: list[dict] = load("history.json", [])
        self.downloads: list[QWebEngineDownloadRequest] = []
        self.windows: list[MainWindow] = []
        self.closed: list[str] = []             # for Ctrl+Shift+T
        self.theme = theme.resolve(self.settings["theme"])
        self.apply_settings(True)
        app.installEventFilter(EdgeResizer(app))
        self._session_timer = QTimer(singleShot=True, interval=1500)
        self._session_timer.timeout.connect(self.save_session)
        app.aboutToQuit.connect(self.save_session)
        from judo_browser.control import ControlServer   # lets JUDO drive this browser by voice
        self.control = ControlServer(self)

    # windows / session
    def new_window(self, url: QUrl | None = None, incognito: bool = False, tabs: list | None = None) -> "MainWindow":
        w = MainWindow(self, incognito)
        self.windows.append(w)
        if tabs:
            for t in tabs:
                w.new_tab(QUrl(t["url"]), background=True, pinned=t.get("pinned", False))
            w.tab_strip.setCurrentIndex(min(max(0, tabs[0].get("current", 0)), w.tab_strip.count() - 1))
        else:
            w.new_tab(url)
        geo = self.settings.get("window") or {}
        if geo.get("maximized", True):
            w.showMaximized()
        else:
            w.resize(geo.get("w", 1280), geo.get("h", 820))
            w.show()
        return w

    def start(self, url: QUrl | None) -> None:
        session = load("session.json", [])
        if self.settings["startup"] == "continue" and session:
            for win_tabs in session:
                self.new_window(tabs=win_tabs)
            if url:
                self.windows[-1].new_tab(url)
            return
        start_page = self.settings["startup_page"] if self.settings["startup"] == "page" else ""
        self.new_window(url or (to_url(start_page) if start_page else None))

    def session_changed(self) -> None:
        self._session_timer.start()

    def save_session(self) -> None:
        out = []
        for w in self.windows:
            if w.incognito:
                continue
            tabs = [{"url": v.url().toString(), "pinned": w.is_pinned(i)}
                    for i in range(w.tabs.count()) if (v := w.tabs.widget(i)) and v.url().scheme() in ("http", "https", "file")]
            if tabs:
                tabs[0]["current"] = w.tabs.currentIndex()
                out.append(tabs)
        save("session.json", out)

    # settings
    def save_settings(self) -> None:
        save("settings.json", self.settings)

    def apply_settings(self, restyle: bool = False) -> None:
        global _search_base
        _search_base = SEARCH_ENGINES[self.settings["search_engine"]][1]
        if restyle:
            self.theme = theme.resolve(self.settings["theme"])
            self.app.setPalette(theme.palette(self.theme))
            self.app.setStyleSheet(theme.stylesheet(self.theme))
        for w in self.windows:
            w.apply_settings(restyle)

    # history / bookmarks
    def record(self, url: str, title: str) -> None:
        if not url.startswith(("http://", "https://")):
            return
        if self.history and self.history[-1]["url"] == url:
            self.history[-1]["title"] = title or self.history[-1]["title"]
        else:
            self.history.append({"url": url, "title": title or url, "t": time.time()})
            del self.history[:-MAX_HISTORY]
        save("history.json", self.history)

    def is_bookmarked(self, url: str) -> bool:
        return any(b["url"] == url for b in self.bookmarks)

    def toggle_bookmark(self, url: str, title: str) -> bool:
        if self.is_bookmarked(url):
            self.bookmarks = [b for b in self.bookmarks if b["url"] != url]
        else:
            self.bookmarks.append({"url": url, "title": title or url})
        self.save_bookmarks()
        return self.is_bookmarked(url)

    def save_bookmarks(self) -> None:
        save("bookmarks.json", self.bookmarks)
        for w in self.windows:
            w.refresh_bookmarks()

    # downloads
    def download_dir(self, default: bool = False) -> Path:
        if not default and self.settings["download_dir"]:
            return Path(self.settings["download_dir"])
        try:
            from core.user_paths import downloads
            return downloads()
        except Exception:
            return Path.home() / "Downloads"

    def _download(self, d: QWebEngineDownloadRequest) -> None:
        if not d.isSavePageDownload():   # "Save page as" already chose its own path
            folder = self.download_dir()
            if self.settings["ask_download_location"]:
                path, _ = QFileDialog.getSaveFileName(None, "Save as", str(folder / d.downloadFileName()))
                if not path:
                    d.cancel()
                    return
                folder, name = Path(path).parent, Path(path).name
                d.setDownloadFileName(name)
            d.setDownloadDirectory(str(folder))
        d.accept()
        self.downloads.append(d)
        for w in self.windows:
            w.download_started(d)


# ── a browser window ────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, browser: Browser, incognito: bool = False):
        super().__init__()
        self.browser, self.incognito = browser, incognito
        self.profile = browser.incognito if incognito else browser.profile
        self.t = theme.THEMES["incognito"] if incognito else browser.theme
        self.tabs = Tabs(self)
        self._pinned: set[int] = set()          # ids of pinned views
        self._devtools: QWebEngineView | None = None
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(520, 360)
        self.setWindowTitle("JUDO Browser" + (" — Incognito" if incognito else ""))

        # title bar: tabs + window buttons
        self.title_bar = TitleBar(self)
        self.tab_strip = TabStrip(self)
        self.tab_strip.currentChanged.connect(self._tab_changed)
        self.tab_strip.tabMoved.connect(lambda *_: browser.session_changed())
        self.new_tab_btn = QToolButton(toolTip="New tab (Ctrl+T)")
        self.new_tab_btn.clicked.connect(lambda: self.new_tab())
        tl = QHBoxLayout(self.title_bar)
        tl.setContentsMargins(8, 0, 0, 0)
        tl.setSpacing(2)
        tl.addWidget(self.tab_strip)
        tl.addWidget(self.new_tab_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        tl.addStretch(1)
        self.incog_label = QLabel(" Incognito ")
        self.incog_label.setVisible(incognito)
        tl.addWidget(self.incog_label)
        self.win_buttons = {}
        for name, fn in (("minimize", self.showMinimized), ("maximize", self.toggle_maximized), ("close", self.close)):
            b = QToolButton(objectName="WinClose" if name == "close" else "WinButton")
            b.clicked.connect(fn)
            tl.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
            self.win_buttons[name] = b

        # toolbar
        self.toolbar = QWidget(objectName="Toolbar")
        bl = QHBoxLayout(self.toolbar)
        bl.setContentsMargins(6, 4, 6, 4)
        bl.setSpacing(2)
        self.nav = {}
        for name, tip, fn in (("back", "Back (Alt+Left)", lambda: self.view().back()),
                              ("forward", "Forward (Alt+Right)", lambda: self.view().forward()),
                              ("reload", "Reload (Ctrl+R)", self._reload_or_stop),
                              ("home", "Home (Alt+Home)", self.go_home)):
            b = QToolButton(toolTip=tip)
            b.clicked.connect(fn)
            bl.addWidget(b)
            self.nav[name] = b
        from judo_browser.omnibox import Omnibox
        self.omnibox = Omnibox(browser, self.toolbar)
        self.omnibox.navigate.connect(self._navigate)
        self.site_action = self.omnibox.addAction(theme.icon("search", self.t["icon"]), QLineEdit.ActionPosition.LeadingPosition)
        self.site_action.triggered.connect(lambda: dialogs.site_info(self, self.omnibox))
        self.star_action = self.omnibox.addAction(theme.icon("star_border", self.t["icon"]), QLineEdit.ActionPosition.TrailingPosition)
        self.star_action.setToolTip("Bookmark this tab (Ctrl+D)")
        self.star_action.triggered.connect(self.toggle_star)
        bl.addWidget(self.omnibox, 1)
        self.dl_btn = QToolButton(toolTip="Downloads", popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        self.dl_btn.setMenu(QMenu(self))
        self.dl_btn.menu().aboutToShow.connect(self._fill_download_menu)
        self.dl_btn.hide()
        bl.addWidget(self.dl_btn)
        self.menu_btn = QToolButton(toolTip="Customize and control JUDO Browser",
                                    popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_btn.setMenu(self._build_menu())
        bl.addWidget(self.menu_btn)

        # bookmarks bar, password bar, pages
        self.bookmark_bar = QWidget(objectName="BookmarkBar")
        self.bookmark_layout = QHBoxLayout(self.bookmark_bar)
        self.bookmark_layout.setContentsMargins(8, 2, 8, 3)
        self.bookmark_layout.setSpacing(2)
        self.info_bar = QWidget(objectName="InfoBar")
        il = QHBoxLayout(self.info_bar)
        il.setContentsMargins(12, 6, 12, 6)
        self.info_label = QLabel()
        self.info_save, self.info_never, self.info_close = (QPushButton("Save", objectName="Primary"),
                                                            QPushButton("Never"), QToolButton())
        for wdg in (self.info_label, self.info_save, self.info_never, self.info_close):
            il.addWidget(wdg, 1 if wdg is self.info_label else 0)
        self.info_close.clicked.connect(self.info_bar.hide)
        self.info_bar.hide()
        self.stack = QStackedWidget()

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        for wdg in (self.title_bar, self.toolbar, self.bookmark_bar, self.info_bar):
            cl.addWidget(wdg)
        cl.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # overlays: find bar, link status bubble, toast
        self.find_bar = QWidget(central, objectName="FindBar")
        fl = QHBoxLayout(self.find_bar)
        fl.setContentsMargins(12, 4, 6, 4)
        self.find_edit, self.find_count = QLineEdit(), QLabel("")
        self.find_edit.textChanged.connect(lambda: self._find())
        self.find_edit.returnPressed.connect(lambda: self._find())
        fl.addWidget(self.find_edit, 1)
        fl.addWidget(self.find_count)
        self.find_prev, self.find_next, self.find_close = QToolButton(), QToolButton(), QToolButton()
        self.find_prev.clicked.connect(lambda: self._find(backward=True))
        self.find_next.clicked.connect(lambda: self._find())
        self.find_close.clicked.connect(self.close_find)
        for b in (self.find_prev, self.find_next, self.find_close):
            fl.addWidget(b)
        self.find_bar.hide()
        self.status_bubble = QLabel(self.stack, objectName="StatusBubble")
        self.status_bubble.hide()
        self.toast = QLabel(central, objectName="StatusBubble")
        self.toast.hide()

        self._shortcuts()
        self.apply_settings(True)
        self.refresh_bookmarks()

    # look
    def apply_settings(self, restyle: bool = False) -> None:
        s = self.browser.settings
        if not self.incognito:
            self.t = self.browser.theme
        else:
            self.setStyleSheet(theme.stylesheet(self.t))
        if restyle:
            ic = lambda n, size=20: theme.icon(n, self.t["icon"], size)
            for name, b in self.nav.items():
                b.setIcon(ic("reload" if name == "reload" else name))
            self.new_tab_btn.setIcon(ic("add"))
            self.dl_btn.setIcon(ic("download"))
            self.menu_btn.setIcon(ic("more"))
            self.win_buttons["minimize"].setIcon(ic("minimize", 16))
            self.win_buttons["close"].setIcon(ic("close", 16))
            self._update_max_icon()
            self.info_close.setIcon(ic("close", 16))
            self.find_prev.setIcon(ic("back", 16))
            self.find_next.setIcon(ic("forward", 16))
            self.find_close.setIcon(ic("close", 16))
            self.incog_label.setPixmap(theme.icon("incognito", self.t["icon"], 22).pixmap(22, 22))
            for i in range(self.tabs.count()):
                self._tab_close_button(i)
            v = self.view()
            if v:
                self._sync_omnibox(v)
        self.nav["home"].setVisible(s["show_home"])
        self.bookmark_bar.setVisible(s["show_bookmarks_bar"])

    def _update_max_icon(self) -> None:
        self.win_buttons["maximize"].setIcon(theme.icon("restore" if self.isMaximized() else "maximize", self.t["icon"], 16))

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange and "maximize" in getattr(self, "win_buttons", {}):
            self._update_max_icon()

    def toggle_maximized(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def set_fullscreen(self, on: bool) -> None:
        for wdg in (self.title_bar, self.toolbar):
            wdg.setVisible(not on)
        self.bookmark_bar.setVisible(not on and self.browser.settings["show_bookmarks_bar"])
        self.showFullScreen() if on else self.showMaximized()

    def flash(self, text: str, ms: int = 3500) -> None:
        self.toast.setText(text)
        self.toast.adjustSize()
        c = self.centralWidget()
        self.toast.move((c.width() - self.toast.width()) // 2, c.height() - self.toast.height() - 24)
        self.toast.raise_()
        self.toast.show()
        QTimer.singleShot(ms, self.toast.hide)

    # tabs
    def view(self) -> QWebEngineView | None:
        return self.tabs.currentWidget()

    def is_pinned(self, i: int) -> bool:
        v = self.tabs.widget(i)
        return v is not None and id(v) in self._pinned

    def new_tab(self, url: QUrl | None = None, background: bool = False, blank: bool = False,
                pinned: bool = False, index: int | None = None) -> QWebEngineView:
        v = View()
        page = Page(self.profile, self)
        v.setPage(page)
        s = v.settings()
        for attr in (QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,
                     QWebEngineSettings.WebAttribute.PdfViewerEnabled,
                     QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled,
                     QWebEngineSettings.WebAttribute.PluginsEnabled,
                     QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows):
            s.setAttribute(attr, True)
        if not self.incognito:
            bridge = passwords.attach(page, self.browser.passwords)
            bridge.offer.connect(self._offer_password)
        self.stack.addWidget(v)
        if index is None:
            index = self.tabs.count()
        i = self.tab_strip.insertTab(index, theme.icon("globe", self.t["sub"], 16), "New Tab")
        self.tab_strip.setTabData(i, v)
        if pinned:
            self._pinned.add(id(v))
        self._tab_close_button(i)
        v.titleChanged.connect(lambda t_, v=v: self._set_title(v, t_))
        v.iconChanged.connect(lambda icon, v=v: self._set_icon(v, icon))
        v.urlChanged.connect(lambda u, v=v: self._url_changed(v, u))
        v.loadStarted.connect(lambda v=v: self._loading(v, True))
        v.loadFinished.connect(lambda ok, v=v: self._loaded(v, ok))
        page.linkHovered.connect(self._hover)
        page.recentlyAudibleChanged.connect(lambda _, v=v: self._audio(v))
        page.renderProcessTerminated.connect(lambda status, code, v=v: self._crashed(v, status))
        if not background or self.tabs.count() == 1:
            self.tab_strip.setCurrentIndex(i)
        if not blank:
            self.load(url or QUrl(HOME_URL), v)
        self.browser.session_changed()
        return v

    def _tab_close_button(self, i: int) -> None:
        v = self.tabs.widget(i)
        if self.is_pinned(i):
            self.tab_strip.setTabButton(i, QTabBar.ButtonPosition.RightSide, None)
            return
        b = QToolButton(objectName="TabClose", toolTip="Close tab (Ctrl+W)")
        b.setIcon(theme.icon("close", self.t["sub"], 14))
        b.setIconSize(QSize(14, 14))
        b.clicked.connect(lambda _, v=v: self.close_tab(self.tabs.indexOf(v)))
        self.tab_strip.setTabButton(i, QTabBar.ButtonPosition.RightSide, b)

    def close_tab(self, i: int) -> None:
        v = self.tabs.widget(i)
        if v is None:
            return
        if v.url().scheme() in ("http", "https"):
            self.browser.closed.append(v.url().toString())
        if self.tabs.count() == 1:
            self.close()   # Chrome closes the window with its last tab
            return
        self.tab_strip.removeTab(i)
        self._pinned.discard(id(v))
        self.stack.removeWidget(v)
        v.deleteLater()
        self.browser.session_changed()

    def reopen_closed(self) -> None:
        if self.browser.closed:
            self.new_tab(QUrl(self.browser.closed.pop()))

    def tab_menu(self, i: int, pos: QPoint) -> None:
        v = self.tabs.widget(i)
        m = QMenu(self)
        m.addAction("New tab to the right", lambda: self.new_tab(index=i + 1))
        m.addSeparator()
        m.addAction("Reload", v.reload)
        m.addAction("Duplicate", lambda: self.new_tab(v.url(), index=i + 1))
        m.addAction("Unpin" if self.is_pinned(i) else "Pin", lambda: self.pin(i))
        m.addAction("Unmute site" if v.page().isAudioMuted() else "Mute site", lambda: self._toggle_mute(v))
        m.addSeparator()
        m.addAction("Close", lambda: self.close_tab(self.tabs.indexOf(v)))
        m.addAction("Close other tabs", lambda: self._close_where(lambda j: j != self.tabs.indexOf(v)))
        m.addAction("Close tabs to the right", lambda: self._close_where(lambda j: j > self.tabs.indexOf(v)))
        m.addSeparator()
        m.addAction("Reopen closed tab\tCtrl+Shift+T", self.reopen_closed).setEnabled(bool(self.browser.closed))
        m.exec(pos)

    def _close_where(self, pred) -> None:
        for j in reversed(range(self.tabs.count())):
            if pred(j) and not self.is_pinned(j):
                self.close_tab(j)

    def pin(self, i: int) -> None:
        v = self.tabs.widget(i)
        if id(v) in self._pinned:
            self._pinned.discard(id(v))
            self.tab_strip.moveTab(i, len(self._pinned))
        else:
            self._pinned.add(id(v))
            self.tab_strip.moveTab(i, len(self._pinned) - 1)
        j = self.tabs.indexOf(v)
        self._tab_close_button(j)
        self._set_title(v, v.title())
        self.tab_strip.updateGeometry()
        self.browser.session_changed()

    def _toggle_mute(self, v) -> None:
        v.page().setAudioMuted(not v.page().isAudioMuted())
        self._audio(v)

    def load(self, url: QUrl, view: QWebEngineView | None = None) -> None:
        view = view or self.view()
        if url.toString() == HOME_URL:
            view.setHtml(new_tab_html(self.browser, self.t), QUrl("about:blank"))
        else:
            view.load(url)

    def _navigate(self, text: str) -> None:
        internal = {"judo://settings": self.settings, "chrome://settings": self.settings,
                    "judo://history": self.history, "chrome://history": self.history,
                    "judo://downloads": self.downloads, "chrome://downloads": self.downloads,
                    "judo://bookmarks": self.bookmarks, "chrome://bookmarks": self.bookmarks}
        if text.lower() in internal:
            return internal[text.lower()]()
        self.load(to_url(text))
        self.view().setFocus()

    def go_home(self) -> None:
        home = self.browser.settings["home_page"]
        self.load(to_url(home) if home else QUrl(HOME_URL))

    def _reload_or_stop(self) -> None:
        v = self.view()
        v.stop() if getattr(v, "_loading", False) else v.reload()

    # per-tab state → UI
    def _set_title(self, v, title: str) -> None:
        i = self.tabs.indexOf(v)
        if i < 0:
            return
        self.tab_strip.setTabText(i, "" if self.is_pinned(i) else (title or "New Tab"))
        self.tab_strip.setTabToolTip(i, title)
        if v is self.view():
            self.setWindowTitle(f"{title or 'New Tab'} - JUDO Browser" + (" (Incognito)" if self.incognito else ""))

    def _set_icon(self, v, icon) -> None:
        if not getattr(v, "_loading", False):
            i = self.tabs.indexOf(v)
            if i >= 0:
                self.tab_strip.setTabIcon(i, icon if not icon.isNull() else theme.icon("globe", self.t["sub"], 16))

    def _loading(self, v, on: bool) -> None:
        v._loading = on
        i = self.tabs.indexOf(v)
        if i >= 0 and on:
            self.tab_strip.setTabIcon(i, theme.icon("spinner", self.t["accent"], 16))
        if v is self.view():
            self.nav["reload"].setIcon(theme.icon("close" if on else "reload", self.t["icon"]))
            self.nav["reload"].setToolTip("Stop loading (Esc)" if on else "Reload (Ctrl+R)")

    def _loaded(self, v, ok: bool) -> None:
        self._loading(v, False)
        self._set_icon(v, v.icon())
        host = v.url().host()
        zoom = self.browser.settings["zoom"].get(host)
        if zoom:
            v.setZoomFactor(zoom)
        if ok and not self.incognito:
            self.browser.record(v.url().toString(), v.title())
        self.browser.session_changed()

    def _url_changed(self, v, u: QUrl) -> None:
        if v is self.view():
            self._sync_omnibox(v)
        self.browser.session_changed()

    def _sync_omnibox(self, v) -> None:
        u = v.url()
        self.omnibox.show_url(u)
        blank = u.toString() in ("", "about:blank")
        name = "search" if blank else "lock" if u.scheme() == "https" else "info"
        self.site_action.setIcon(theme.icon(name, self.t["icon"], 18))
        self.site_action.setToolTip("" if blank else "View site information")
        on = self.browser.is_bookmarked(u.toString())
        self.star_action.setIcon(theme.icon("star" if on else "star_border", self.t["accent"] if on else self.t["icon"], 18))
        self.star_action.setVisible(not blank)
        self.nav["back"].setEnabled(v.history().canGoBack())
        self.nav["forward"].setEnabled(v.history().canGoForward())

    def _tab_changed(self, i: int) -> None:
        v = self.tabs.widget(i)
        if v is None:
            return
        self.stack.setCurrentWidget(v)
        self._sync_omnibox(v)
        self._set_title(v, v.title())
        self._loading(v, getattr(v, "_loading", False))
        if v.url().toString() in ("", "about:blank"):
            QTimer.singleShot(0, self.omnibox.setFocus)
        self.browser.session_changed()

    def _hover(self, url: str) -> None:
        if not url:
            return self.status_bubble.hide()
        self.status_bubble.setText(url if len(url) < 120 else url[:117] + "…")
        self.status_bubble.adjustSize()
        self.status_bubble.move(0, self.stack.height() - self.status_bubble.height())
        self.status_bubble.raise_()
        self.status_bubble.show()

    def _audio(self, v) -> None:
        i = self.tabs.indexOf(v)
        if i < 0:
            return
        muted, audible = v.page().isAudioMuted(), v.page().recentlyAudible()
        if muted or audible:
            b = QToolButton(objectName="TabClose", toolTip="Unmute site" if muted else "Mute site")
            b.setIcon(theme.icon("muted" if muted else "volume", self.t["sub"], 14))
            b.clicked.connect(lambda _, v=v: self._toggle_mute(v))
            self.tab_strip.setTabButton(i, QTabBar.ButtonPosition.LeftSide, b)
        else:
            self.tab_strip.setTabButton(i, QTabBar.ButtonPosition.LeftSide, None)

    def _crashed(self, v, status) -> None:
        if status != QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus:
            v.setHtml(CRASH_HTML)

    # bookmarks
    def toggle_star(self) -> None:
        v = self.view()
        if v.url().scheme() in ("http", "https"):
            on = self.browser.toggle_bookmark(v.url().toString(), v.title())
            self.flash("Bookmark added" if on else "Bookmark removed")
            self._sync_omnibox(v)

    def refresh_bookmarks(self) -> None:
        while self.bookmark_layout.count():
            item = self.bookmark_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        shown = self.browser.bookmarks[:14]
        for b in shown:
            btn = QToolButton(text=b["title"][:24], toolTip=b["url"])
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            btn.clicked.connect(lambda _, u=b["url"]: self.load(QUrl(u)))
            self.bookmark_layout.addWidget(btn)
        rest = self.browser.bookmarks[14:]
        if rest:
            more = QToolButton(text="»", popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
            m = QMenu(more)
            for b in rest:
                m.addAction(b["title"][:40], lambda u=b["url"]: self.load(QUrl(u)))
            more.setMenu(m)
            self.bookmark_layout.addWidget(more)
        if not shown:
            self.bookmark_layout.addWidget(QLabel("  For quick access, bookmark pages with ☆ (Ctrl+D)"))
        self.bookmark_layout.addStretch(1)
        v = self.view()
        if v:
            self._sync_omnibox(v)

    # downloads
    def download_started(self, d) -> None:
        self.dl_btn.show()
        if self.isActiveWindow():
            self.flash(f"Downloading {d.downloadFileName()}")
            d.isFinishedChanged.connect(lambda: self.flash(f"Downloaded {d.downloadFileName()}"))

    def _fill_download_menu(self) -> None:
        m = self.dl_btn.menu()
        m.clear()
        for label, path in dialogs.download_rows(self.browser)[:8]:
            m.addAction(label, lambda p=path: os.startfile(p) if Path(p).exists() else None)
        m.addSeparator()
        m.addAction("Show all downloads\tCtrl+J", self.downloads)

    # passwords
    def _offer_password(self, origin: str, username: str, password: str) -> None:
        if not self.browser.settings["offer_passwords"]:
            return
        self.info_label.setText(f"🔑  Save password for <b>{html.escape(QUrl(origin).host())}</b>"
                                + (f" ({html.escape(username)})" if username else "") + "?")
        for b in (self.info_save, self.info_never):
            try:
                b.clicked.disconnect()
            except TypeError:
                pass
        self.info_save.clicked.connect(lambda: (self.browser.passwords.put(origin, username, password),
                                                self.info_bar.hide(), self.flash("Password saved")))
        self.info_never.clicked.connect(lambda: (self.browser.passwords.block(origin), self.info_bar.hide()))
        self.info_bar.show()

    # find in page
    def show_find(self) -> None:
        c = self.centralWidget()
        self.find_bar.setFixedWidth(380)
        self.find_bar.move(c.width() - 400, self.stack.geometry().top() + 6)
        self.find_bar.raise_()
        self.find_bar.show()
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def _find(self, backward: bool = False) -> None:
        flags = QWebEnginePage.FindFlag.FindBackward if backward else QWebEnginePage.FindFlag(0)
        self.view().findText(self.find_edit.text(), flags, lambda r: self.find_count.setText(
            f"{r.activeMatch()}/{r.numberOfMatches()}" if self.find_edit.text() else ""))

    def close_find(self) -> None:
        if self.find_bar.isVisible():
            self.view().findText("")
            self.find_bar.hide()
            self.find_count.clear()

    # zoom
    def zoom(self, step: float | None) -> None:
        v = self.view()
        factor = 1.0 if step is None else max(0.25, min(5.0, round(v.zoomFactor() + step, 2)))
        v.setZoomFactor(factor)
        host = v.url().host()
        if host and not self.incognito:
            if factor == 1.0:
                self.browser.settings["zoom"].pop(host, None)
            else:
                self.browser.settings["zoom"][host] = factor
            self.browser.save_settings()
        self.flash(f"Zoom {round(factor * 100)}%", 1500)
        if hasattr(self, "zoom_label"):
            self.zoom_label.setText(f"{round(factor * 100)}%")

    # tools
    def devtools(self) -> None:
        if self._devtools is None:
            self._devtools = QWebEngineView()
            self._devtools.setWindowTitle("DevTools - JUDO Browser")
            self._devtools.resize(1000, 700)
            self._devtools.destroyed.connect(lambda: setattr(self, "_devtools", None))
        self.view().page().setDevToolsPage(self._devtools.page())
        self._devtools.show()
        self._devtools.raise_()

    def print_page(self) -> None:
        from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
        self._printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if QPrintDialog(self._printer, self).exec():
            self.view().print(self._printer)

    def save_page(self) -> None:
        v = self.view()
        name = re.sub(r'[\\/:*?"<>|]', "_", v.title() or "page")[:80] + ".html"
        path, _ = QFileDialog.getSaveFileName(self, "Save page as", str(self.browser.download_dir() / name),
                                              "Webpage, complete (*.html)")
        if path:
            v.page().save(path, QWebEngineDownloadRequest.SavePageFormat.CompleteHtmlSaveFormat)

    def settings(self) -> None:
        dialogs.Settings(self).exec()

    def history(self) -> None:
        dialogs.history_manager(self)

    def downloads(self) -> None:
        dialogs.downloads_manager(self)

    def bookmarks(self) -> None:
        dialogs.bookmarks_manager(self)

    # menu & shortcuts
    def _build_menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction("New tab\tCtrl+T", lambda: self.new_tab())
        m.addAction("New window\tCtrl+N", lambda: self.browser.new_window())
        m.addAction("New Incognito window\tCtrl+Shift+N", lambda: self.browser.new_window(incognito=True))
        m.addSeparator()
        m.addAction("Passwords", lambda: dialogs.Settings(self, tab="Passwords").exec())
        hist = m.addMenu("History")
        hist.aboutToShow.connect(lambda: self._fill_history_menu(hist))
        m.addAction("Downloads\tCtrl+J", self.downloads)
        bm = m.addMenu("Bookmarks")
        bm.aboutToShow.connect(lambda: self._fill_bookmarks_menu(bm))
        m.addSeparator()
        zoom_row = QWidget()
        zl = QHBoxLayout(zoom_row)
        zl.setContentsMargins(20, 2, 10, 2)
        zl.addWidget(QLabel("Zoom"), 1)
        minus, plus, full = QToolButton(text="−"), QToolButton(text="+"), QToolButton(text="⛶")
        self.zoom_label = QLabel("100%")
        minus.clicked.connect(lambda: self.zoom(-0.1))
        plus.clicked.connect(lambda: self.zoom(0.1))
        full.clicked.connect(lambda: (m.close(), self.set_fullscreen(not self.isFullScreen())))
        for wdg in (minus, self.zoom_label, plus, full):
            zl.addWidget(wdg)
        act = QWidgetAction(m)
        act.setDefaultWidget(zoom_row)
        m.addAction(act)
        m.aboutToShow.connect(lambda: self.zoom_label.setText(f"{round(self.view().zoomFactor() * 100)}%"))
        m.addSeparator()
        m.addAction("Print…\tCtrl+P", self.print_page)
        m.addAction("Find…\tCtrl+F", self.show_find)
        m.addAction("Save page as…\tCtrl+S", self.save_page)
        tools = m.addMenu("More tools")
        tools.addAction("Clear browsing data…\tCtrl+Shift+Del", lambda: dialogs.ClearData(self).exec())
        tools.addAction("Developer tools\tCtrl+Shift+I", self.devtools)
        tools.addAction("View page source\tCtrl+U", lambda: self.new_tab(QUrl("view-source:" + self.view().url().toString())))
        m.addSeparator()
        m.addAction("Settings", self.settings)
        m.addAction("About JUDO Browser", lambda: QMessageBox.about(
            self, "About JUDO Browser", f"JUDO Browser\nChromium {qWebEngineChromiumVersion()}"))
        m.addSeparator()
        m.addAction("Exit", QApplication.quit)
        return m

    def _fill_history_menu(self, menu: QMenu) -> None:
        menu.clear()
        menu.addAction("History\tCtrl+H", self.history)
        menu.addSeparator()
        if self.browser.closed:
            menu.addSection("Recently closed")
            for url in reversed(self.browser.closed[-8:]):
                menu.addAction(url[:60], lambda u=url: (self.browser.closed.remove(u), self.new_tab(QUrl(u))))
        menu.addSection("Recent")
        for h in reversed(self.browser.history[-10:]):
            menu.addAction(h["title"][:60], lambda u=h["url"]: self.load(QUrl(u)))

    def _fill_bookmarks_menu(self, menu: QMenu) -> None:
        menu.clear()
        menu.addAction("Bookmark this tab…\tCtrl+D", self.toggle_star)
        menu.addAction(("Hide" if self.bookmark_bar.isVisible() else "Show") + " bookmarks bar\tCtrl+Shift+B",
                       self.toggle_bookmark_bar)
        menu.addAction("Bookmark manager\tCtrl+Shift+O", self.bookmarks)
        menu.addSeparator()
        for b in self.browser.bookmarks[:25]:
            menu.addAction(b["title"][:60], lambda u=b["url"]: self.load(QUrl(u)))

    def toggle_bookmark_bar(self) -> None:
        self.browser.settings["show_bookmarks_bar"] = not self.browser.settings["show_bookmarks_bar"]
        self.browser.save_settings()
        self.browser.apply_settings()

    def _select_tab(self, n: int) -> None:
        count = self.tabs.count()
        self.tab_strip.setCurrentIndex(count - 1 if n == 9 else min(n - 1, count - 1))

    def _shortcuts(self) -> None:
        cur = lambda: self.tabs.currentIndex()
        keys = {
            "Ctrl+T": lambda: self.new_tab(), "Ctrl+N": lambda: self.browser.new_window(),
            "Ctrl+Shift+N": lambda: self.browser.new_window(incognito=True),
            "Ctrl+W": lambda: self.close_tab(cur()), "Ctrl+F4": lambda: self.close_tab(cur()),
            "Ctrl+Shift+W": self.close, "Ctrl+Shift+T": self.reopen_closed,
            "Ctrl+L": lambda: (self.omnibox.setFocus(), self.omnibox.selectAll()),
            "Alt+D": lambda: (self.omnibox.setFocus(), self.omnibox.selectAll()),
            "F6": lambda: (self.omnibox.setFocus(), self.omnibox.selectAll()),
            "Ctrl+K": lambda: (self.omnibox.setFocus(), self.omnibox.setText("")),
            "Ctrl+R": lambda: self.view().reload(), "F5": lambda: self.view().reload(),
            "Ctrl+Shift+R": lambda: self.view().triggerPageAction(QWebEnginePage.WebAction.ReloadAndBypassCache),
            "Shift+F5": lambda: self.view().triggerPageAction(QWebEnginePage.WebAction.ReloadAndBypassCache),
            "Escape": lambda: (self.close_find(), self.view().stop()),
            "Alt+Left": lambda: self.view().back(), "Alt+Right": lambda: self.view().forward(),
            "Alt+Home": self.go_home,
            "Ctrl+Tab": lambda: self.tab_strip.setCurrentIndex((cur() + 1) % self.tabs.count()),
            "Ctrl+PgDown": lambda: self.tab_strip.setCurrentIndex((cur() + 1) % self.tabs.count()),
            "Ctrl+Shift+Tab": lambda: self.tab_strip.setCurrentIndex((cur() - 1) % self.tabs.count()),
            "Ctrl+PgUp": lambda: self.tab_strip.setCurrentIndex((cur() - 1) % self.tabs.count()),
            "Ctrl+D": self.toggle_star, "Ctrl+Shift+B": self.toggle_bookmark_bar,
            "Ctrl+Shift+O": self.bookmarks, "Ctrl+H": self.history, "Ctrl+J": self.downloads,
            "Ctrl+Shift+Del": lambda: dialogs.ClearData(self).exec(),
            "Ctrl+F": self.show_find, "F3": lambda: self._find(), "Shift+F3": lambda: self._find(True),
            "Ctrl+G": lambda: self._find(), "Ctrl+Shift+G": lambda: self._find(True),
            "Ctrl+P": self.print_page, "Ctrl+S": self.save_page,
            "Ctrl+U": lambda: self.new_tab(QUrl("view-source:" + self.view().url().toString())),
            "F12": self.devtools, "Ctrl+Shift+I": self.devtools, "Ctrl+Shift+J": self.devtools,
            "Ctrl++": lambda: self.zoom(0.1), "Ctrl+=": lambda: self.zoom(0.1),
            "Ctrl+-": lambda: self.zoom(-0.1), "Ctrl+0": lambda: self.zoom(None),
            "F11": lambda: self.set_fullscreen(not self.isFullScreen()),
            "Alt+F": lambda: self.menu_btn.showMenu(), "F10": lambda: self.menu_btn.showMenu(),
        }
        for n in range(1, 10):
            keys[f"Ctrl+{n}"] = lambda n=n: self._select_tab(n)
        for key, fn in keys.items():
            a = QAction(self)
            a.setShortcut(QKeySequence(key))
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            a.triggered.connect(fn)
            self.addAction(a)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.find_bar.isVisible():
            self.show_find()

    def closeEvent(self, e) -> None:
        if not self.incognito and not self.isMaximized() and not self.isFullScreen():
            self.browser.settings["window"] = {"maximized": False, "w": self.width(), "h": self.height()}
        elif not self.incognito:
            self.browser.settings["window"] = {"maximized": True}
        self.browser.save_settings()
        self.browser.save_session()
        if self in self.browser.windows:
            self.browser.windows.remove(self)
        super().closeEvent(e)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    from judo_browser import client
    if client.alive():   # one browser per profile: hand the URL to the running one instead
        client.send("new_tab" if len(argv) > 1 else "focus", {"url": argv[1]} if len(argv) > 1 else {})
        return 0
    if not load_settings()["sandbox"]:
        # On this PC the sandboxed renderer can't load its DLLs (exit 0xC0000135) and every page
        # stays blank; Settings → Advanced turns it back on to re-test.
        os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(argv)
    app.setApplicationName("JUDO Browser")
    app.setStyle("Fusion")
    browser = Browser(app)
    browser.start(to_url(argv[1]) if len(argv) > 1 else None)
    return app.exec()
