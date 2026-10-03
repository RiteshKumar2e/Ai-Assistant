"""
judo_mail/app.py — JUDO Mail: a Gmail client window (IMAP/SMTP via mailbox.py).

    python -m judo_mail

The familiar three panes — folders, the message list, the open message — in
JUDO's own look (look.py): sender avatars, two-line rows with friendly dates,
an unread badge, and a compose card. Folders, Gmail-syntax search, reading
(HTML, with the mail's JavaScript off and links opening in JUDO Browser),
attachments, compose / reply / reply-all / forward, star, mark unread, delete
(to Trash), and more mail loading as you scroll. All network work runs on one
background thread so the window never freezes.
"""
from __future__ import annotations

import html
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QKeySequence, QPainter, QShortcut, QStandardItem, QStandardItemModel,
                         QTextDocument)
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QListView, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
                             QSplitter, QStyle, QStyledItemDelegate, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from judo_mail import look
from judo_mail.mailbox import FOLDERS, Mail, Mailbox, Summary

PAGE = 50
REFRESH_MS = 120_000
SUMMARY = Qt.ItemDataRole.UserRole + 1
FOLDER_ICONS = {"Inbox": "inbox", "Starred": "star_border", "Important": "important", "Sent": "send",
                "Drafts": "draft", "All Mail": "all", "Spam": "spam", "Trash": "trash"}


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


def _tool(name: str, tip: str, fn, t: dict, size: int = 20) -> QToolButton:
    b = QToolButton(toolTip=tip)
    b.setIcon(look.icon(name, t["sub"], size))
    b.setIconSize(QSize(size, size))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(fn)
    return b


def _avatar(p: QPainter, rect: QRectF, name: str, key: str) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(look.avatar_color(key or name)))
    p.drawEllipse(rect)
    f = QFont("Segoe UI", -1, QFont.Weight.DemiBold)
    f.setPixelSize(int(rect.height() * 0.45))
    p.setFont(f)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, look.initial(name))


class Avatar(QWidget):
    def __init__(self, size: int, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.name, self.key = "", ""

    def set(self, name: str, key: str) -> None:
        self.name, self.key = name, key
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _avatar(p, QRectF(self.rect()), self.name, self.key)


# ── the message list ────────────────────────────────────────────────────────

class MessageDelegate(QStyledItemDelegate):
    """Two-line rows: avatar · sender · date / subject · star. Unread rows are bold
    with an accent dot; clicking the star toggles it."""
    ROW = 70

    def __init__(self, t: dict, on_star, parent=None):
        super().__init__(parent)
        self.t, self.on_star = t, on_star

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW)

    def _star_rect(self, r: QRect) -> QRect:
        return QRect(r.right() - 34, r.top() + 36, 22, 22)

    def paint(self, p: QPainter, option, index):
        s: Summary = index.data(SUMMARY)
        t, r = self.t, option.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = option.state & QStyle.StateFlag.State_Selected
        hover = option.state & QStyle.StateFlag.State_MouseOver
        bg = t["select"] if selected else t["hover"] if hover else None
        if bg:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(bg))
            p.drawRoundedRect(QRectF(r.adjusted(6, 2, -6, -2)), 12, 12)
        if s.unread:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["accent"]))
            p.drawEllipse(QRectF(r.left() + 11, r.center().y() - 3, 6, 6))
        _avatar(p, QRectF(r.left() + 24, r.top() + 15, 40, 40), s.sender, s.address)

        x, right = r.left() + 76, r.right() - 16
        bold = QFont("Segoe UI", 10, QFont.Weight.Bold if s.unread else QFont.Weight.Normal)
        date_font = QFont("Segoe UI", 9, QFont.Weight.DemiBold if s.unread else QFont.Weight.Normal)
        date = look.when_text(s.when, s.date)
        p.setFont(date_font)
        dw = p.fontMetrics().horizontalAdvance(date)
        p.setPen(QColor(t["accent"] if s.unread else t["sub"]))
        p.drawText(QRect(right - dw, r.top() + 14, dw, 20), Qt.AlignmentFlag.AlignVCenter, date)

        p.setFont(bold)
        p.setPen(QColor(t["text"]))
        sender = p.fontMetrics().elidedText(s.sender or "(unknown)", Qt.TextElideMode.ElideRight, right - dw - 12 - x)
        p.drawText(QRect(x, r.top() + 14, right - dw - 12 - x, 20), Qt.AlignmentFlag.AlignVCenter, sender)

        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold if s.unread else QFont.Weight.Normal))
        p.setPen(QColor(t["text"] if s.unread else t["sub"]))
        subject = p.fontMetrics().elidedText(s.subject, Qt.TextElideMode.ElideRight, right - 30 - x)
        p.drawText(QRect(x, r.top() + 37, right - 30 - x, 20), Qt.AlignmentFlag.AlignVCenter, subject)

        star = self._star_rect(r)
        name, color = ("star", t["star"]) if s.starred else ("star_border", t["faint"])
        if s.starred or hover or selected:
            look.icon(name, color, 18).paint(p, star)
        p.restore()

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.Type.MouseButtonRelease
                and self._star_rect(option.rect).contains(event.position().toPoint())):
            self.on_star(index.row())
            return True
        return super().editorEvent(event, model, option, index)


class FolderDelegate(QStyledItemDelegate):
    """Folder rows: icon, name and an unread count; the current folder is a filled pill."""

    def __init__(self, t: dict, parent=None):
        super().__init__(parent)
        self.t = t
        self.counts: dict[str, int] = {}

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 40)

    def paint(self, p: QPainter, option, index):
        t, r, name = self.t, option.rect, index.data()
        selected = option.state & QStyle.StateFlag.State_Selected
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or option.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["select"] if selected else t["hover"]))
            p.drawRoundedRect(QRectF(r.adjusted(0, 2, -6, -2)), 18, 18)
        color = t["accent"] if selected else t["sub"]
        look.icon(FOLDER_ICONS.get(name, "inbox"), color, 20).paint(p, QRect(r.left() + 16, r.top() + 10, 20, 20))
        count = self.counts.get(name, 0)
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold if selected or count else QFont.Weight.Normal))
        p.setPen(QColor(t["accent"] if selected else t["text"]))
        p.drawText(QRect(r.left() + 50, r.top(), r.width() - 110, r.height()), Qt.AlignmentFlag.AlignVCenter, name)
        if count:
            p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            p.drawText(QRect(r.right() - 64, r.top(), 50, r.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{count:,}")
        p.restore()


# ── compose ─────────────────────────────────────────────────────────────────

class Compose(QDialog):
    def __init__(self, parent, worker: Worker, to="", cc="", subject="", body="", reply_to: Mail | None = None,
                 title="New message"):
        super().__init__(parent, windowTitle=title)
        self.worker, self.reply_to, self.files, self.title = worker, reply_to, [], title
        t = parent.t
        self.setWindowIcon(look.app_icon())
        self.resize(720, 640)
        card = QFrame(objectName="Card")
        head = QFrame(objectName="ComposeHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(18, 10, 10, 10)
        self.head_label = QLabel(title)
        hl.addWidget(self.head_label, 1)
        hl.addWidget(_tool("close", "Discard", self.reject, t, 18))

        self.to, self.cc, self.subject = (QLineEdit(to, objectName="Field"), QLineEdit(cc, objectName="Field"),
                                          QLineEdit(subject, objectName="Field"))
        self.to.setPlaceholderText("name@example.com, another@example.com")
        self.subject.setPlaceholderText("What is it about?")
        self.body = QTextEdit(objectName="Body", acceptRichText=False)
        self.body.setPlaceholderText("Write your message…")
        self.body.setPlainText(body)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)

        self.send_btn = QPushButton("Send", objectName="Primary")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setDefault(True)
        self.send_btn.clicked.connect(self._send)
        bottom = QHBoxLayout()
        bottom.addWidget(self.send_btn)
        bottom.addWidget(_tool("attach", "Attach files", self._attach, t))
        bottom.addStretch(1)
        bottom.addWidget(_tool("trash", "Discard draft", self.reject, t))

        inner = QVBoxLayout()
        inner.setContentsMargins(20, 6, 20, 16)
        for label, w in (("To", self.to), ("Cc", self.cc), ("Subject", self.subject)):
            row = QHBoxLayout()
            tag = QLabel(label, objectName="Sub")
            tag.setFixedWidth(62)
            row.addWidget(tag)
            row.addWidget(w, 1)
            inner.addLayout(row)
        inner.addWidget(self.body, 1)
        inner.addLayout(self.chips)
        inner.addLayout(bottom)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(head)
        cl.addLayout(inner, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(card)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._send)
        (self.body if to else self.to).setFocus()

    def _attach(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Attach files")
        for f in files:
            self.files.append(f)
            chip = QPushButton(f"📎 {Path(f).name}", objectName="Chip", toolTip="Click to remove")
            chip.clicked.connect(lambda _, f=f, chip=chip: (self.files.remove(f), chip.deleteLater()))
            self.chips.insertWidget(self.chips.count(), chip)

    def _send(self) -> None:
        to = self.to.text().strip()
        if "@" not in to:
            QMessageBox.warning(self, "Send", "Add at least one recipient address.")
            return
        self.setEnabled(False)
        self.head_label.setText("Sending…")
        args = (to, self.subject.text().strip(), self.body.toPlainText(), self.cc.text().strip(), self.files, self.reply_to)
        self.worker.run(lambda mb: mb.send(*args), self._sent)

    def _sent(self, result) -> None:
        if isinstance(result, Exception):
            self.setEnabled(True)
            self.head_label.setText(self.title)
            QMessageBox.critical(self, "Not sent", f"Gmail refused the message:\n{result}")
            return
        self.parent().statusBar().showMessage(f"Message sent to {self.to.text().strip()}", 6000)
        self.accept()


# ── the window ──────────────────────────────────────────────────────────────

class MailWindow(QMainWindow):
    def __init__(self, worker: Worker | None = None, address: str = ""):
        super().__init__()
        self.t = t = look.palette()
        self.setWindowTitle("JUDO Mail")
        self.setWindowIcon(look.app_icon())
        self.setStyleSheet(look.stylesheet(t))
        self.resize(1360, 880)
        self.worker = worker or Worker()
        self.folder, self.query = FOLDERS["Inbox"], ""
        self.uids: list[str] = []
        self.rows: list[Summary] = []
        self.current: Mail | None = None
        self._loading = False
        if not address:
            try:
                from judo_mail.mailbox import credentials
                address = credentials()[0]
            except Exception:
                address = ""

        # top bar: brand · search · refresh · account
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(look.app_icon().pixmap(34, 34))
        name = QLabel("<span style='color:%s'>J</span><span style='color:%s'>U</span><span style='color:%s'>D</span>"
                      "<span style='color:%s'>O</span>&nbsp;<span style='font-weight:400'>Mail</span>" % look.SPECTRUM,
                      objectName="Brand")
        brand.addWidget(logo)
        brand.addWidget(name)
        self.search = QLineEdit(objectName="Search",
                                placeholderText="Search mail   ·   from:rahul   is:unread   has:attachment")
        self.search.addAction(look.icon("search", t["sub"], 18), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._search)
        self.search.setMaximumWidth(720)
        me = Avatar(36)
        me.set(address or "?", address)
        me.setToolTip(address or "Gmail account not set")
        top = QHBoxLayout()
        top.setContentsMargins(18, 10, 18, 6)
        top.addLayout(brand)
        top.addSpacing(40)
        top.addWidget(self.search, 1)
        top.addStretch(0)
        top.addWidget(_tool("refresh", "Refresh (F5)", lambda: self.load_folder(), t, 22))
        top.addSpacing(6)
        top.addWidget(me)

        # left: compose + folders
        compose = QPushButton("  Compose", objectName="Compose")
        compose.setIcon(look.icon("edit", "#FFFFFF", 20))
        compose.setIconSize(QSize(20, 20))
        compose.setCursor(Qt.CursorShape.PointingHandCursor)
        compose.clicked.connect(self.compose)
        self.folders = QListWidget(objectName="Folders")
        self.folder_delegate = FolderDelegate(t, self.folders)
        self.folders.setItemDelegate(self.folder_delegate)
        self.folders.setMouseTracking(True)
        for f in FOLDERS:
            self.folders.addItem(QListWidgetItem(f))
        self.folders.setCurrentRow(0)
        self.folders.currentTextChanged.connect(self._folder_changed)
        left = QWidget()
        left.setFixedWidth(250)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(12, 8, 6, 12)
        lv.addWidget(compose, 0, Qt.AlignmentFlag.AlignLeft)
        lv.addSpacing(14)
        lv.addWidget(self.folders, 1)

        # middle: the list
        self.title = QLabel("Inbox", objectName="Title")
        self.count = QLabel("", objectName="Count")
        lh = QHBoxLayout()
        lh.setContentsMargins(20, 16, 16, 6)
        lh.addWidget(self.title)
        lh.addStretch(1)
        lh.addWidget(self.count)
        self.model = QStandardItemModel(self)
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setItemDelegate(MessageDelegate(t, self._star_row, self.list))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list.setUniformItemSizes(True)
        self.list.selectionModel().currentChanged.connect(lambda cur, _: self._open(cur.row()))
        self.list.verticalScrollBar().valueChanged.connect(self._maybe_more)
        self.list_empty = QLabel("", objectName="Empty", alignment=Qt.AlignmentFlag.AlignCenter)
        self.list_empty.hide()
        mid = QFrame(objectName="Card")
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 8)
        mv.addLayout(lh)
        mv.addWidget(self.list, 1)
        mv.addWidget(self.list_empty, 1)

        # right: the open message
        self.subject = QLabel(objectName="Subject", wordWrap=True,
                              textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sender_avatar = Avatar(44)
        self.sender = QLabel(objectName="Sender", textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        self.recipients = QLabel(objectName="Sub", wordWrap=True,
                                 textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        self.when = QLabel(objectName="Sub")
        who = QVBoxLayout()
        who.setSpacing(2)
        who.addWidget(self.sender)
        who.addWidget(self.recipients)
        meta = QHBoxLayout()
        meta.setSpacing(12)
        meta.addWidget(self.sender_avatar, 0, Qt.AlignmentFlag.AlignTop)
        meta.addLayout(who, 1)
        meta.addWidget(self.when, 0, Qt.AlignmentFlag.AlignTop)
        self.actions: dict[str, QToolButton] = {}
        bar = QHBoxLayout()
        bar.setSpacing(2)
        for key, ic, tip, fn in (("reply", "reply", "Reply (Ctrl+R)", self._reply),
                                 ("reply_all", "reply_all", "Reply all", lambda: self._reply(all_=True)),
                                 ("forward", "forward", "Forward", self._forward),
                                 ("star", "star_border", "Star", self._star),
                                 ("unread", "mail", "Mark as unread", self._unread),
                                 ("delete", "trash", "Delete (Del)", self._delete)):
            b = _tool(ic, tip, fn, t)
            bar.addWidget(b)
            self.actions[key] = b
            if key == "forward":
                bar.addSpacing(10)
        bar.addStretch(1)
        self.attach_row = QHBoxLayout()
        self.attach_row.setSpacing(8)
        self.view = QWebEngineView()
        self.view.setPage(MailPage(self.view))
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
        self.paper = QFrame(objectName="Paper")      # mails bring their own (usually light) design
        pl = QVBoxLayout(self.paper)
        pl.setContentsMargins(1, 1, 1, 1)
        pl.addWidget(self.view)
        reply_pill = QPushButton("  Reply", objectName="Pill")
        reply_pill.setIcon(look.icon("reply", t["text"], 18))
        reply_pill.clicked.connect(self._reply)
        fwd_pill = QPushButton("  Forward", objectName="Pill")
        fwd_pill.setIcon(look.icon("forward", t["text"], 18))
        fwd_pill.clicked.connect(self._forward)
        pills = QHBoxLayout()
        pills.addWidget(reply_pill)
        pills.addWidget(fwd_pill)
        pills.addStretch(1)
        self.reader = QWidget()
        rv = QVBoxLayout(self.reader)
        rv.setContentsMargins(24, 16, 24, 16)
        rv.setSpacing(12)
        rv.addLayout(bar)
        rv.addWidget(self.subject)
        rv.addLayout(meta)
        rv.addLayout(self.attach_row)
        rv.addWidget(self.paper, 1)
        rv.addLayout(pills)
        self.placeholder = QLabel(objectName="Empty", alignment=Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setText(f"<div style='font-size:40pt;color:{t['line']}'>✉</div>"
                                 "<div>Select a message to read it here</div>")
        right = QFrame(objectName="Card")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.reader, 1)
        rl.addWidget(self.placeholder, 1)

        split = QSplitter()
        split.setHandleWidth(12)
        split.addWidget(mid)
        split.addWidget(right)
        split.setSizes([500, 760])
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 14, 0)
        body.addWidget(left)
        body.addWidget(split, 1)
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 6)
        cl.addLayout(top)
        cl.addLayout(body, 1)
        self.setCentralWidget(central)

        for keys, fn in (("Ctrl+N", self.compose), ("Ctrl+R", self._reply), ("Delete", self._delete),
                         ("F5", lambda: self.load_folder()), ("Ctrl+F", self.search.setFocus),
                         ("/", self.search.setFocus)):
            QShortcut(QKeySequence(keys), self, activated=fn)
        self._reader_enabled(False)
        self.load_folder()
        timer = QTimer(self, interval=REFRESH_MS)
        timer.timeout.connect(self._auto_refresh)
        timer.start()

    # list
    def compose(self) -> None:
        Compose(self, self.worker).exec()

    def _folder_changed(self, name: str) -> None:
        self.folder, self.query = FOLDERS[name], ""
        self.search.clear()
        self.load_folder()

    def _search(self) -> None:
        self.query = self.search.text().strip()
        self.load_folder()

    def _folder_name(self) -> str:
        return next(k for k, v in FOLDERS.items() if v == self.folder)

    def load_folder(self) -> None:
        self.statusBar().showMessage("Loading…")
        folder, query = self.folder, self.query
        self.worker.run(lambda mb: (mb.uids(folder, query), folder, query), self._got_uids)
        self.worker.run(lambda mb: mb.unseen(FOLDERS["Inbox"]), self._got_unseen)

    def _got_unseen(self, result) -> None:
        if isinstance(result, int):
            self.folder_delegate.counts["Inbox"] = result
            self.folders.viewport().update()

    def _got_uids(self, result) -> None:
        if self._failed(result):
            return
        uids, folder, query = result
        if (folder, query) != (self.folder, self.query):
            return   # the user moved on while this was loading
        self.uids, self.rows = uids, []
        self.model.clear()
        self._reader_enabled(False)
        self._load_more()

    def _load_more(self) -> None:
        start = len(self.rows)
        chunk, folder = self.uids[start:start + PAGE], self.folder
        if not chunk:
            self._status()
            return
        self._loading = True
        self.worker.run(lambda mb: (mb.summaries(folder, chunk), folder), self._got_rows)

    def _maybe_more(self, value: int) -> None:
        """Load the next page as the list nears its end — no "Load more" button."""
        bar = self.list.verticalScrollBar()
        if not self._loading and value >= bar.maximum() - 200 and len(self.rows) < len(self.uids):
            self._load_more()

    def _got_rows(self, result) -> None:
        self._loading = False
        if self._failed(result):
            return
        rows, folder = result
        if folder != self.folder:
            return
        for s in rows:
            item = QStandardItem()
            item.setData(s, SUMMARY)
            item.setEditable(False)
            self.model.appendRow(item)
            self.rows.append(s)
        self._status()

    def _status(self) -> None:
        name = self._folder_name()
        self.title.setText(f"Results for “{self.query}”" if self.query else name)
        n, shown = len(self.uids), len(self.rows)
        self.count.setText(f"{min(shown, 1) if n else 0}–{shown:,} of {n:,}" if n else "")
        empty = n == 0
        self.list.setVisible(not empty)
        self.list_empty.setVisible(empty)
        self.list_empty.setText("No messages match your search." if self.query else f"Nothing in {name}.")
        self.statusBar().showMessage(f"{name}: {n:,} conversations" if not self.query else f"{n:,} results", 4000)
        self.setWindowTitle(f"{name} — JUDO Mail")

    def _auto_refresh(self) -> None:
        # only when looking at the top of the inbox list, so nothing jumps under the user
        if self.folder == FOLDERS["Inbox"] and not self.query and self.list.currentIndex().row() <= 0:
            self.load_folder()

    def _refresh_row(self, row: int) -> None:
        idx = self.model.index(row, 0)
        self.list.update(idx)

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
            if self.rows[row].unread and self.folder == FOLDERS["Inbox"]:
                self.folder_delegate.counts["Inbox"] = max(0, self.folder_delegate.counts.get("Inbox", 0) - 1)
                self.folders.viewport().update()
            self.rows[row].unread = False
            self._refresh_row(row)
        self.current = mail
        e = html.escape
        from email.utils import getaddresses
        name, addr = (getaddresses([mail.sender]) or [("", "")])[0]
        self.subject.setText(e(mail.subject or "(no subject)"))
        self.sender_avatar.set(name or addr, addr)
        self.sender.setText(f"{e(name or addr)} <span style='font-weight:400;color:{self.t['sub']}'>"
                            f"{'&lt;' + e(addr) + '&gt;' if name else ''}</span>")
        self.recipients.setText(f"to {e(mail.to)}" + (f" · cc {e(mail.cc)}" if mail.cc else ""))
        s = self.rows[row] if row < len(self.rows) else None
        self.when.setText(s.when.strftime("%a, %d %b %Y, %I:%M %p") if s and s.when else e(mail.date))
        while self.attach_row.count():
            w = self.attach_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, (fname, size) in enumerate(mail.attachments):
            chip = QPushButton(f"  {fname}   ·   {max(1, size // 1024):,} KB", objectName="Chip",
                               toolTip="Save to Downloads")
            chip.setIcon(look.icon("download", self.t["accent"], 16))
            chip.clicked.connect(lambda _, i=i: self._save_attachment(i))
            self.attach_row.addWidget(chip)
        self.attach_row.addStretch(1)
        # HTML mail is laid out by its sender for a white page, so it gets one even in dark mode;
        # plain text follows the theme
        paper = bool(mail.html)
        self.paper.setStyleSheet("" if paper else "#Paper { background: transparent; border: none; }")
        self.view.page().setBackgroundColor(QColor("#FFFFFF" if paper else self.t["card"]))
        base = "<style>body{margin:18px 22px;font:15px 'Segoe UI',system-ui,sans-serif;word-wrap:break-word}</style>"
        body = (base + mail.html) if paper else (
            f"<pre style='white-space:pre-wrap;font:15px Segoe UI, system-ui;color:{self.t['text']};margin:4px'>"
            f"{e(mail.text)}</pre>")
        self.view.setHtml(body, QUrl("https://mail.judo.local/"))
        if s:
            self._star_button(s.starred)
        self._reader_enabled(True)
        self._status()

    def _reader_enabled(self, on: bool) -> None:
        self.reader.setVisible(on)
        self.placeholder.setVisible(not on)
        if not on:
            self.current = None
            self.view.setHtml("")

    def _save_attachment(self, index: int) -> None:
        mail = self.current
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
        Compose(self, self.worker, to=m.sender, cc=cc, subject=subject, body=self._quote(m), reply_to=m,
                title="Reply all" if all_ else "Reply").exec()

    def _forward(self) -> None:
        m = self.current
        if m:
            Compose(self, self.worker, subject=f"Fwd: {m.subject}", title="Forward",
                    body=f"\n\n---------- Forwarded message ----------\nFrom: {m.sender}\nDate: {m.date}\n"
                         f"Subject: {m.subject}\nTo: {m.to}\n\n{m.text or _plain(m.html)}").exec()

    def _selected(self):
        row = self.list.currentIndex().row()
        return (row, self.rows[row]) if self.current and 0 <= row < len(self.rows) else (None, None)

    def _star_button(self, on: bool) -> None:
        b = self.actions["star"]
        b.setIcon(look.icon("star" if on else "star_border", self.t["star"] if on else self.t["sub"], 20))
        b.setToolTip("Unstar" if on else "Star")

    def _star(self) -> None:
        row, _ = self._selected()
        if row is not None:
            self._star_row(row)

    def _star_row(self, row: int) -> None:
        if not 0 <= row < len(self.rows):
            return
        s, folder = self.rows[row], self.folder
        on = not s.starred
        self.worker.run(lambda mb: mb.star(folder, s.uid, on), lambda r: self._failed(r) or self._starred(row, on))

    def _starred(self, row: int, on: bool) -> None:
        self.rows[row].starred = on
        self._refresh_row(row)
        if row == self.list.currentIndex().row() and self.current:
            self._star_button(on)
        self.statusBar().showMessage("Starred" if on else "Unstarred", 3000)

    def _unread(self) -> None:
        row, s = self._selected()
        if s is None:
            return
        folder = self.folder
        self.worker.run(lambda mb: mb.mark_read(folder, s.uid, False),
                        lambda r: self._failed(r) or self._marked_unread(row))

    def _marked_unread(self, row: int) -> None:
        self.rows[row].unread = True
        self._refresh_row(row)
        if self.folder == FOLDERS["Inbox"]:
            self.folder_delegate.counts["Inbox"] = self.folder_delegate.counts.get("Inbox", 0) + 1
            self.folders.viewport().update()
        self.statusBar().showMessage("Marked as unread", 3000)

    def _delete(self) -> None:
        row, s = self._selected()
        if s is None:
            return
        folder = self.folder
        self.worker.run(lambda mb: mb.trash(folder, s.uid), lambda r: self._failed(r) or self._removed(row, s))

    def _removed(self, row: int, s) -> None:
        self.model.removeRow(row)
        self.rows.pop(row)
        self.uids.remove(s.uid)
        self._reader_enabled(False)
        self.statusBar().showMessage("Conversation moved to Trash", 5000)
        self._status()

    def _failed(self, result) -> bool:
        if isinstance(result, Exception):
            self._loading = False
            self.statusBar().showMessage(f"Gmail error: {result}", 10000)
            return True
        return False

    def closeEvent(self, e) -> None:
        self.worker.shutdown()
        super().closeEvent(e)


def main() -> int:
    if sys.platform == "win32":
        import ctypes   # its own taskbar identity and icon instead of Python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Ritesh.JUDO.Mail")
    app = QApplication(sys.argv)
    app.setApplicationName("JUDO Mail")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(look.app_icon())
    try:
        w = MailWindow()
    except Exception as e:
        QMessageBox.critical(None, "JUDO Mail", str(e))
        return 1
    w.show()
    return app.exec()
