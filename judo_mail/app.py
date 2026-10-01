"""
judo_mail/app.py — JUDO Mail: a Gmail client window (IMAP/SMTP via mailbox.py).

    python -m judo_mail

Folders, paged message list, Gmail-syntax search, reading (HTML, with the
mail's JavaScript off and links opening in JUDO Browser), attachments,
compose / reply / reply-all / forward, star, mark unread, delete (to Trash).
All network work runs on one background thread so the window never freezes.
"""
from __future__ import annotations

import html
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QFont, QTextDocument
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton,
                             QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

from judo_mail.mailbox import FOLDERS, Mail, Mailbox

PAGE = 50
REFRESH_MS = 120_000


class Worker(QObject):
    """Runs mailbox calls one at a time off the UI thread; results come back
    on the UI thread through `finished` (imaplib is not thread-safe)."""
    finished = pyqtSignal(object, object)

    def __init__(self):
        super().__init__()
        self._pool = ThreadPoolExecutor(1)
        self._mb: Mailbox | None = None
        self.finished.connect(lambda cb, result: cb(result))

    def run(self, fn, cb=lambda r: None) -> None:
        def job():
            try:
                self._mb = self._mb or Mailbox()
                result = fn(self._mb)
            except Exception as e:
                result = e
            self.finished.emit(cb, result)
        self._pool.submit(job)

    def shutdown(self) -> None:
        if self._mb:
            self._pool.submit(self._mb.close)
        self._pool.shutdown(wait=False)


class MailPage(QWebEnginePage):
    """A mail is a document, not an app: links go to JUDO Browser."""

    def acceptNavigationRequest(self, url, nav_type, is_main):
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            ThreadPoolExecutor(1).submit(_open_link, url.toString())
            return False
        return True


def _open_link(url: str) -> None:
    """Links open in JUDO Browser only — never the system default (Edge/Chrome)."""
    try:
        from judo_browser import client
        client.send("new_tab", {"url": url})
    except Exception as e:
        print(f"[Mail] JUDO Browser could not open {url}: {e}")


def _plain(markup: str) -> str:
    doc = QTextDocument()
    doc.setHtml(markup)
    return doc.toPlainText()


class Compose(QDialog):
    def __init__(self, parent, worker: Worker, to="", cc="", subject="", body="", reply_to: Mail | None = None):
        super().__init__(parent, windowTitle="New message")
        self.worker, self.reply_to, self.files = worker, reply_to, []
        self.resize(760, 620)
        form = QFormLayout()
        self.to, self.cc, self.subject = QLineEdit(to), QLineEdit(cc), QLineEdit(subject)
        self.to.setPlaceholderText("name@example.com, another@example.com")
        for label, w in (("To", self.to), ("Cc", self.cc), ("Subject", self.subject)):
            form.addRow(label, w)
        self.body = QTextEdit(acceptRichText=False)
        self.body.setPlainText(body)
        self.attached = QLabel("")
        attach, send = QPushButton("📎 Attach"), QPushButton("Send")
        send.setDefault(True)
        attach.clicked.connect(self._attach)
        send.clicked.connect(self._send)
        row = QHBoxLayout()
        row.addWidget(attach)
        row.addWidget(self.attached, 1)
        row.addWidget(send)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.body, 1)
        lay.addLayout(row)
        (self.body if to else self.to).setFocus()

    def _attach(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Attach files")
        self.files += files
        self.attached.setText(", ".join(Path(f).name for f in self.files))

    def _send(self) -> None:
        to = self.to.text().strip()
        if "@" not in to:
            QMessageBox.warning(self, "Send", "Add at least one recipient address.")
            return
        self.setEnabled(False)
        self.setWindowTitle("Sending…")
        args = (to, self.subject.text().strip(), self.body.toPlainText(), self.cc.text().strip(), self.files, self.reply_to)
        self.worker.run(lambda mb: mb.send(*args), self._sent)

    def _sent(self, result) -> None:
        if isinstance(result, Exception):
            self.setEnabled(True)
            self.setWindowTitle("New message")
            QMessageBox.critical(self, "Not sent", f"Gmail refused the message:\n{result}")
            return
        self.parent().statusBar().showMessage(f"Sent to {self.to.text().strip()}", 6000)
        self.accept()


class MailWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JUDO Mail")
        self.resize(1320, 860)
        self.worker = Worker()
        self.folder, self.query = FOLDERS["Inbox"], ""
        self.uids: list[str] = []
        self.rows: list = []              # Summary per table row
        self.current: Mail | None = None

        # left: compose + folders
        compose = QPushButton("✎  Compose")
        compose.setMinimumHeight(40)
        compose.clicked.connect(lambda: Compose(self, self.worker).exec())
        self.folders = QListWidget()
        self.folders.addItems(list(FOLDERS))
        self.folders.setCurrentRow(0)
        self.folders.currentTextChanged.connect(self._folder_changed)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(compose)
        lv.addWidget(self.folders)

        # middle: search + list
        self.search = QLineEdit(placeholderText="Search mail — Gmail syntax works: from:rahul  is:unread  has:attachment")
        self.search.returnPressed.connect(self._search)
        refresh = QPushButton("⟳")
        refresh.setToolTip("Refresh")
        refresh.clicked.connect(lambda: self.load_folder())
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(refresh)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "From", "Subject", "Date"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(1, 220)
        self.table.currentCellChanged.connect(lambda r, *_: self._open(r))
        self.more = QPushButton("Load more")
        self.more.clicked.connect(self._load_more)
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.addLayout(top)
        mv.addWidget(self.table, 1)
        mv.addWidget(self.more)

        # right: reader
        self.header = QLabel(textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse, wordWrap=True)
        acts = QHBoxLayout()
        self.actions = {}
        for name, fn in (("Reply", self._reply), ("Reply all", lambda: self._reply(all_=True)),
                         ("Forward", self._forward), ("☆ Star", self._star), ("Mark unread", self._unread),
                         ("🗑 Delete", self._delete)):
            b = QPushButton(name)
            b.clicked.connect(fn)
            acts.addWidget(b)
            self.actions[name] = b
        acts.addStretch(1)
        self.attachments = QListWidget(maximumHeight=90)
        self.attachments.itemDoubleClicked.connect(self._save_attachment)
        self.attachments.setToolTip("Double-click to save to Downloads")
        self.view = QWebEngineView()
        self.view.setPage(MailPage(self.view))
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(self.header)
        rv.addLayout(acts)
        rv.addWidget(self.attachments)
        rv.addWidget(self.view, 1)

        split = QSplitter()
        for w, stretch in ((left, 0), (mid, 2), (right, 3)):
            split.addWidget(w)
        split.setSizes([180, 520, 620])
        self.setCentralWidget(split)
        self._reader_enabled(False)
        self.load_folder()
        timer = QTimer(self, interval=REFRESH_MS)
        timer.timeout.connect(self._auto_refresh)
        timer.start()

    # list
    def _folder_changed(self, name: str) -> None:
        self.folder, self.query = FOLDERS[name], ""
        self.search.clear()
        self.load_folder()

    def _search(self) -> None:
        self.query = self.search.text().strip()
        self.load_folder()

    def load_folder(self) -> None:
        self.statusBar().showMessage("Loading…")
        folder, query = self.folder, self.query
        self.worker.run(lambda mb: (mb.uids(folder, query), folder, query), self._got_uids)

    def _got_uids(self, result) -> None:
        if self._failed(result):
            return
        uids, folder, query = result
        if (folder, query) != (self.folder, self.query):
            return   # the user moved on while this was loading
        self.uids, self.rows = uids, []
        self.table.setRowCount(0)
        self._reader_enabled(False)
        self._load_more()

    def _load_more(self) -> None:
        start = len(self.rows)
        chunk, folder = self.uids[start:start + PAGE], self.folder
        if not chunk:
            self._status()
            return
        self.worker.run(lambda mb: (mb.summaries(folder, chunk), folder), self._got_rows)

    def _got_rows(self, result) -> None:
        if self._failed(result):
            return
        rows, folder = result
        if folder != self.folder:
            return
        for s in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, text in enumerate(("★" if s.starred else "", s.sender, s.subject, s.date)):
                self.table.setItem(r, c, QTableWidgetItem(text))
            self._bold(r, s.unread)
            self.rows.append(s)
        self._status()

    def _status(self) -> None:
        self.more.setVisible(len(self.rows) < len(self.uids))
        name = next(k for k, v in FOLDERS.items() if v == self.folder)
        shown = f"“{self.query}” in {name}" if self.query else name
        self.statusBar().showMessage(f"{shown}: {len(self.uids):,} messages, showing {len(self.rows):,}")
        self.setWindowTitle(f"{name} — JUDO Mail")

    def _bold(self, row: int, on: bool) -> None:
        for c in range(4):
            item = self.table.item(row, c)
            if item:
                f = item.font()
                f.setBold(on)
                item.setFont(f)

    def _auto_refresh(self) -> None:
        # only when looking at the top of the inbox list, so nothing jumps under the user
        if self.folder == FOLDERS["Inbox"] and not self.query and self.table.currentRow() <= 0:
            self.load_folder()

    # reading
    def _open(self, row: int) -> None:
        if not 0 <= row < len(self.rows):
            return
        s, folder = self.rows[row], self.folder
        self.statusBar().showMessage("Opening…")

        def fetch(mb):
            mail = mb.get(folder, s.uid)
            if s.unread:
                mb.mark_read(folder, s.uid)
            return mail, row
        self.worker.run(fetch, self._show)

    def _show(self, result) -> None:
        if self._failed(result):
            return
        mail, row = result
        if row < len(self.rows) and self.rows[row].uid == mail.uid:
            self.rows[row].unread = False
            self._bold(row, False)
        self.current = mail
        e = html.escape
        self.header.setText(f"<h3 style='margin:0'>{e(mail.subject or '(no subject)')}</h3>"
                            f"<b>From:</b> {e(mail.sender)}<br><b>To:</b> {e(mail.to)}"
                            + (f"<br><b>Cc:</b> {e(mail.cc)}" if mail.cc else "") + f"<br><small>{e(mail.date)}</small>")
        self.attachments.clear()
        for name, size in mail.attachments:
            self.attachments.addItem(f"📎 {name}  ({size / 1024:.0f} KB)")
        self.attachments.setVisible(bool(mail.attachments))
        body = mail.html or f"<pre style='white-space:pre-wrap;font:14px system-ui'>{e(mail.text)}</pre>"
        self.view.setHtml(body, QUrl("https://mail.judo.local/"))
        self.actions["☆ Star"].setText("★ Unstar" if self.rows[row].starred else "☆ Star")
        self._reader_enabled(True)
        self._status()

    def _reader_enabled(self, on: bool) -> None:
        for b in self.actions.values():
            b.setEnabled(on)
        if not on:
            self.current = None
            self.header.setText("<span style='color:gray'>Select a message</span>")
            self.attachments.hide()
            self.view.setHtml("")

    def _save_attachment(self, item) -> None:
        mail, index = self.current, self.attachments.row(item)
        try:
            from core.user_paths import downloads
            folder = downloads()
        except Exception:
            folder = Path.home() / "Downloads"
        self.worker.run(lambda mb: mb.save_attachment(mail, index, folder),
                        lambda r: self._failed(r) or self.statusBar().showMessage(f"Saved {r}", 8000))

    # actions
    def _quote(self, m: Mail) -> str:
        text = m.text or _plain(m.html)
        return f"\n\nOn {m.date}, {m.sender} wrote:\n" + "\n".join("> " + l for l in text.splitlines())

    def _reply(self, all_: bool = False) -> None:
        m = self.current
        if not m:
            return
        cc = ", ".join(a for a in (m.to, m.cc) if a) if all_ else ""
        subject = m.subject if m.subject.lower().startswith("re:") else f"Re: {m.subject}"
        Compose(self, self.worker, to=m.sender, cc=cc, subject=subject, body=self._quote(m), reply_to=m).exec()

    def _forward(self) -> None:
        m = self.current
        if m:
            Compose(self, self.worker, subject=f"Fwd: {m.subject}",
                    body=f"\n\n---------- Forwarded message ----------\nFrom: {m.sender}\nDate: {m.date}\n"
                         f"Subject: {m.subject}\nTo: {m.to}\n\n{m.text or _plain(m.html)}").exec()

    def _selected(self):
        row = self.table.currentRow()
        return (row, self.rows[row]) if self.current and 0 <= row < len(self.rows) else (None, None)

    def _star(self) -> None:
        row, s = self._selected()
        if s is None:
            return
        on, folder = not s.starred, self.folder
        self.worker.run(lambda mb: mb.star(folder, s.uid, on), lambda r: self._failed(r) or self._starred(row, on))

    def _starred(self, row: int, on: bool) -> None:
        self.rows[row].starred = on
        self.table.item(row, 0).setText("★" if on else "")
        self.actions["☆ Star"].setText("★ Unstar" if on else "☆ Star")

    def _unread(self) -> None:
        row, s = self._selected()
        if s is None:
            return
        folder = self.folder
        self.worker.run(lambda mb: mb.mark_read(folder, s.uid, False),
                        lambda r: self._failed(r) or (setattr(s, "unread", True), self._bold(row, True)))

    def _delete(self) -> None:
        row, s = self._selected()
        if s is None:
            return
        folder = self.folder
        self.worker.run(lambda mb: mb.trash(folder, s.uid), lambda r: self._failed(r) or self._removed(row, s))

    def _removed(self, row: int, s) -> None:
        self.table.removeRow(row)
        self.rows.pop(row)
        self.uids.remove(s.uid)
        self.statusBar().showMessage("Moved to Trash", 5000)
        self._status()

    def _failed(self, result) -> bool:
        if isinstance(result, Exception):
            self.statusBar().showMessage(f"Gmail error: {result}", 10000)
            return True
        return False

    def closeEvent(self, e) -> None:
        self.worker.shutdown()
        super().closeEvent(e)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("JUDO Mail")
    app.setFont(QFont("Segoe UI", 10))
    try:
        w = MailWindow()
    except Exception as e:
        QMessageBox.critical(None, "JUDO Mail", str(e))
        return 1
    w.show()
    return app.exec()
