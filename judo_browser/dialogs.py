"""
judo_browser/dialogs.py — Settings, Clear browsing data, the History /
Bookmarks / Downloads managers and the site-info bubble.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog,
                             QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QTableWidget,
                             QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from judo_browser.app_data import SEARCH_ENGINES, save

RANGES = {"Last hour": 3600, "Last 24 hours": 86400, "Last 7 days": 7 * 86400, "Last 4 weeks": 28 * 86400,
          "All time": None}


def _row(*widgets, stretch_first=False) -> QHBoxLayout:
    lay = QHBoxLayout()
    for i, w in enumerate(widgets):
        lay.addWidget(w, 1 if stretch_first and i == 0 else 0)
    return lay


class ListManager(QDialog):
    """History / bookmarks / downloads: a searchable list; double-click opens."""

    def __init__(self, parent, title: str, rows_fn, on_open, on_delete=None, on_edit=None):
        super().__init__(parent, windowTitle=title)
        self.resize(820, 580)
        self.rows_fn, self.on_open, self.on_delete, self.on_edit = rows_fn, on_open, on_delete, on_edit
        self.search = QLineEdit(placeholderText=f"Search {title.lower()}")
        self.search.textChanged.connect(self.fill)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda it: (on_open(it.data(Qt.ItemDataRole.UserRole)), self.accept()))
        lay = QVBoxLayout(self)
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        buttons = []
        if on_edit:
            b = QPushButton("Edit")
            b.clicked.connect(self._edit)
            buttons.append(b)
        if on_delete:
            b = QPushButton("Delete selected")
            b.clicked.connect(self._delete)
            buttons.append(b)
        if buttons:
            lay.addLayout(_row(QLabel(""), *buttons, stretch_first=True))
        self.fill()

    def fill(self, q: str = "") -> None:
        q = (q or self.search.text()).lower()
        self.list.clear()
        for label, data in self.rows_fn():
            if q in label.lower():
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, data)
                self.list.addItem(it)

    def _delete(self) -> None:
        for it in self.list.selectedItems():
            self.on_delete(it.data(Qt.ItemDataRole.UserRole))
        self.fill()

    def _edit(self) -> None:
        it = self.list.currentItem()
        if it:
            self.on_edit(it.data(Qt.ItemDataRole.UserRole))
            self.fill()


def history_manager(win) -> None:
    b = win.browser

    def rows():
        return [(f"{time.strftime('%d %b %Y  %H:%M', time.localtime(h['t']))}    {h['title']}    —    {h['url']}", h)
                for h in reversed(b.history)]

    def delete(h):
        b.history.remove(h)
        save("history.json", b.history)
    ListManager(win, "History", rows, lambda h: win.new_tab(QUrl(h["url"])), on_delete=delete).exec()


def bookmarks_manager(win) -> None:
    b = win.browser

    def edit(bm):
        title, ok = QInputDialog.getText(win, "Edit bookmark", "Name", text=bm["title"])
        if ok and title.strip():
            bm["title"] = title.strip()
            b.save_bookmarks()

    def delete(bm):
        b.bookmarks.remove(bm)
        b.save_bookmarks()
    ListManager(win, "Bookmarks", lambda: [(f"★  {bm['title']}    —    {bm['url']}", bm) for bm in b.bookmarks],
                lambda bm: win.new_tab(QUrl(bm["url"])), on_delete=delete, on_edit=edit).exec()


def download_rows(browser) -> list[tuple[str, str]]:
    S = QWebEngineDownloadRequest.DownloadState
    state = {S.DownloadRequested: "Starting", S.DownloadInProgress: "Downloading", S.DownloadCompleted: "Done",
             S.DownloadCancelled: "Cancelled", S.DownloadInterrupted: "Failed"}
    rows = []
    for d in reversed(browser.downloads):
        pct = f" {100 * d.receivedBytes() // max(1, d.totalBytes())}%" if d.state() == S.DownloadInProgress else ""
        size = f"{d.totalBytes() / 1e6:.1f} MB" if d.totalBytes() > 0 else ""
        rows.append((f"{d.downloadFileName()}    {state.get(d.state(), '')}{pct}    {size}",
                     str(Path(d.downloadDirectory()) / d.downloadFileName())))
    return rows


def downloads_manager(win) -> None:
    ListManager(win, "Downloads (double-click to open)", lambda: download_rows(win.browser),
                lambda p: os.startfile(p) if Path(p).exists() else None).exec()


class ClearData(QDialog):
    def __init__(self, win):
        super().__init__(win, windowTitle="Clear browsing data")
        self.win = win
        self.range = QComboBox()
        self.range.addItems(list(RANGES))
        self.range.setCurrentText("All time")
        self.history, self.cookies, self.cache = (QCheckBox("Browsing history"),
                                                  QCheckBox("Cookies and other site data (signs you out of most sites)"),
                                                  QCheckBox("Cached images and files"))
        for c in (self.history, self.cache):
            c.setChecked(True)
        go = QPushButton("Clear data", objectName="Primary")
        go.clicked.connect(self._clear)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Time range", self.range)
        lay.addLayout(form)
        for c in (self.history, self.cookies, self.cache):
            lay.addWidget(c)
        lay.addLayout(_row(QLabel(""), go, stretch_first=True))

    def _clear(self) -> None:
        b, span = self.win.browser, RANGES[self.range.currentText()]
        if self.history.isChecked():
            cutoff = 0 if span is None else time.time() - span
            b.history[:] = [h for h in b.history if h["t"] < cutoff] if span else []
            save("history.json", b.history)
            b.profile.clearAllVisitedLinks()
        if self.cookies.isChecked():
            b.profile.cookieStore().deleteAllCookies()
        if self.cache.isChecked():
            b.profile.clearHttpCache()
        self.accept()
        self.win.flash("Browsing data cleared")


class Settings(QDialog):
    def __init__(self, win, tab: str = ""):
        super().__init__(win, windowTitle="Settings")
        self.win, self.b = win, win.browser
        s = self.b.settings
        self.resize(760, 560)
        tabs = QTabWidget()

        # General
        g = QWidget()
        gl = QVBoxLayout(g)
        gl.addWidget(QLabel("<b>On startup</b>"))
        self.startup = QButtonGroup(self)
        for key, text in (("newtab", "Open the New Tab page"), ("continue", "Continue where you left off"),
                          ("page", "Open a specific page")):
            r = QRadioButton(text)
            r.setChecked(s["startup"] == key)
            self.startup.addButton(r)
            r.setProperty("key", key)
            gl.addWidget(r)
        self.startup_page = QLineEdit(s["startup_page"], placeholderText="https://…")
        gl.addWidget(self.startup_page)
        form = QFormLayout()
        self.engine = QComboBox()
        for key, (name, _) in SEARCH_ENGINES.items():
            self.engine.addItem(name, key)
        self.engine.setCurrentIndex(list(SEARCH_ENGINES).index(s["search_engine"]))
        self.theme = QComboBox()
        self.theme.addItems(["system", "light", "dark"])
        self.theme.setCurrentText(s["theme"])
        self.home = QLineEdit(s["home_page"], placeholderText="Empty = New Tab page")
        self.show_home, self.show_bar = QCheckBox("Show home button"), QCheckBox("Show bookmarks bar")
        self.show_home.setChecked(s["show_home"])
        self.show_bar.setChecked(s["show_bookmarks_bar"])
        form.addRow("Search engine", self.engine)
        form.addRow("Theme", self.theme)
        form.addRow("Home page", self.home)
        gl.addLayout(form)
        gl.addWidget(self.show_home)
        gl.addWidget(self.show_bar)
        gl.addStretch(1)
        tabs.addTab(g, "General")

        # Downloads
        d = QWidget()
        dl = QVBoxLayout(d)
        self.dl_dir = QLineEdit(s["download_dir"] or str(self.b.download_dir()))
        browse = QPushButton("Change")
        browse.clicked.connect(lambda: self.dl_dir.setText(
            QFileDialog.getExistingDirectory(self, "Download location", self.dl_dir.text()) or self.dl_dir.text()))
        self.ask_dl = QCheckBox("Ask where to save each file before downloading")
        self.ask_dl.setChecked(s["ask_download_location"])
        dl.addWidget(QLabel("<b>Location</b>"))
        dl.addLayout(_row(self.dl_dir, browse, stretch_first=True))
        dl.addWidget(self.ask_dl)
        dl.addStretch(1)
        tabs.addTab(d, "Downloads")

        # Passwords
        p = QWidget()
        pl = QVBoxLayout(p)
        self.offer = QCheckBox("Offer to save passwords")
        self.offer.setChecked(s["offer_passwords"])
        self.pw_table = QTableWidget(0, 3)
        self.pw_table.setHorizontalHeaderLabels(["Site", "Username", "Password"])
        self.pw_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pw_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pw_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        show, delete = QPushButton("Show password"), QPushButton("Delete")
        show.clicked.connect(self._show_pw)
        delete.clicked.connect(self._delete_pw)
        pl.addWidget(self.offer)
        pl.addWidget(QLabel("Saved passwords are encrypted for this Windows account only."))
        pl.addWidget(self.pw_table, 1)
        pl.addLayout(_row(QLabel(""), show, delete, stretch_first=True))
        self._fill_pw()
        tabs.addTab(p, "Passwords")

        # Privacy
        pr = QWidget()
        prl = QVBoxLayout(pr)
        clear = QPushButton("Clear browsing data…")
        clear.clicked.connect(lambda: ClearData(win).exec())
        prl.addWidget(QLabel("<b>Privacy and security</b>"))
        prl.addWidget(clear)
        prl.addWidget(QLabel("Site permissions (camera, microphone, location, notifications) are remembered per "
                             "site — click the icon left of the address to review or reset them.", wordWrap=True))
        prl.addStretch(1)
        tabs.addTab(pr, "Privacy")

        # Advanced
        a = QWidget()
        al = QVBoxLayout(a)
        self.sandbox = QCheckBox("Use the Chromium sandbox (restart needed)")
        self.sandbox.setChecked(s["sandbox"])
        al.addWidget(self.sandbox)
        al.addWidget(QLabel("The sandbox isolates web pages from the computer. On this PC the sandboxed page "
                            "process fails to start, so it is off by default — turn it on to test after "
                            "Windows/Python updates.", wordWrap=True))
        al.addStretch(1)
        tabs.addTab(a, "Advanced")

        names = [tabs.tabText(i) for i in range(tabs.count())]
        if tab in names:
            tabs.setCurrentIndex(names.index(tab))
        done = QPushButton("Done", objectName="Primary")
        done.clicked.connect(self._apply)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs, 1)
        lay.addLayout(_row(QLabel(""), done, stretch_first=True))

    def _fill_pw(self) -> None:
        items = self.b.passwords.items
        self.pw_table.setRowCount(len(items))
        for r, it in enumerate(items):
            for c, text in enumerate((it["origin"], it["username"], "••••••••")):
                self.pw_table.setItem(r, c, QTableWidgetItem(text))

    def _show_pw(self) -> None:
        r = self.pw_table.currentRow()
        if r < 0:
            return
        it = self.b.passwords.items[r]
        creds = [c for c in self.b.passwords.for_origin(it["origin"]) if c["username"] == it["username"]]
        if creds and QMessageBox.question(self, "Show password", f"Show the password for {it['origin']}?") \
                == QMessageBox.StandardButton.Yes:
            self.pw_table.item(r, 2).setText(creds[0]["password"])

    def _delete_pw(self) -> None:
        r = self.pw_table.currentRow()
        if r >= 0:
            it = self.b.passwords.items[r]
            self.b.passwords.delete(it["origin"], it["username"])
            self._fill_pw()

    def _apply(self) -> None:
        s = self.b.settings
        s["startup"] = next(bt.property("key") for bt in self.startup.buttons() if bt.isChecked())
        s["startup_page"] = self.startup_page.text().strip()
        s["search_engine"] = self.engine.currentData()
        s["home_page"] = self.home.text().strip()
        s["show_home"], s["show_bookmarks_bar"] = self.show_home.isChecked(), self.show_bar.isChecked()
        s["download_dir"] = "" if self.dl_dir.text() == str(self.b.download_dir(default=True)) else self.dl_dir.text()
        s["ask_download_location"], s["offer_passwords"] = self.ask_dl.isChecked(), self.offer.isChecked()
        theme_changed = s["theme"] != self.theme.currentText()
        s["theme"], s["sandbox"] = self.theme.currentText(), self.sandbox.isChecked()
        self.b.save_settings()
        self.b.apply_settings(theme_changed)
        self.accept()


def site_info(win, anchor) -> None:
    """The bubble under the padlock: connection state and this site's permissions."""
    v = win.view()
    url = v.url()
    secure = url.scheme() == "https"
    dlg = QDialog(win, windowTitle=url.host() or "Site information")
    dlg.setWindowFlags(Qt.WindowType.Popup)
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel(f"<b>{url.host() or url.toString()}</b>"))
    lay.addWidget(QLabel("🔒 Connection is secure" if secure else "⚠️ Your connection to this site is not secure"))
    perms = win.browser.profile.listPermissionsForOrigin(url) if hasattr(win.browser.profile,
                                                                           "listPermissionsForOrigin") else []
    for perm in perms:
        state = perm.state().name.replace("Granted", "Allowed").replace("Denied", "Blocked")
        reset = QPushButton("Reset")
        reset.clicked.connect(lambda _, p=perm, b=reset: (p.reset(), b.setEnabled(False)))
        lay.addLayout(_row(QLabel(f"{perm.permissionType().name}: {state}"), reset, stretch_first=True))
    if not perms:
        lay.addWidget(QLabel("No special permissions for this site."))
    cookies = QPushButton("Site settings / clear data…")
    cookies.clicked.connect(lambda: (dlg.close(), ClearData(win).exec()))
    lay.addWidget(cookies)
    dlg.move(anchor.mapToGlobal(anchor.rect().bottomLeft()))
    dlg.show()
