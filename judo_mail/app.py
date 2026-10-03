"""
judo_mail/app.py — JUDO Mail: a Gmail client window (IMAP/SMTP via mailbox.py).

    python -m judo_mail

The familiar three panes — folders and labels, the message list, the open
message — in JUDO's own look (look.py). Inbox tabs (Primary / Promotions /
Social / Updates with "N new"), two-line rows with sender avatars, previews,
attachment and star marks; check several messages to archive, delete, mark
read/unread or star them together; Gmail-syntax search; reading (HTML on a
white page, the mail's JavaScript off, links in JUDO Browser), attachments,
Unsubscribe, compose / reply / reply-all / forward. The avatar opens the
account menu: profile photo, switch or add Gmail accounts, sign out, theme.
All network work runs on one background thread so the window never freezes.
"""
from __future__ import annotations

import html
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut,
                         QStandardItem, QStandardItemModel, QTextDocument)
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
                             QSplitter, QStackedWidget, QStyle, QStyledItemDelegate, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from judo_mail import look
from judo_mail import mailbox as mbx
from judo_mail.mailbox import FOLDERS, Mail, Mailbox, Summary

PAGE = 50
SYNC_MS = 60_000            # fallback sync; the Inbox also updates the moment Gmail pushes a change
SUMMARY = Qt.ItemDataRole.UserRole + 1
KIND, TARGET, FIXED_QUERY = Qt.ItemDataRole.UserRole + 2, Qt.ItemDataRole.UserRole + 3, Qt.ItemDataRole.UserRole + 4
# sidebar: (name, icon, IMAP folder, fixed search) — Purchases is an Inbox category, like in Gmail
SIDEBAR = [("Inbox", "inbox", FOLDERS["Inbox"], ""), ("Starred", "star_border", FOLDERS["Starred"], ""),
           ("Important", "important", FOLDERS["Important"], ""), ("Sent", "send", FOLDERS["Sent"], ""),
           ("Drafts", "draft", FOLDERS["Drafts"], ""), ("Purchases", "bag", FOLDERS["Inbox"], "category:purchases"),
           ("All Mail", "all", FOLDERS["All Mail"], ""), ("Spam", "spam", FOLDERS["Spam"], ""),
           ("Trash", "trash", FOLDERS["Trash"], "")]
TABS = [("primary", "Primary", "inbox"), ("promotions", "Promotions", "tag"), ("social", "Social", "people"),
        ("updates", "Updates", "info")]
_windows: list = []          # open windows (a theme or account change opens a fresh one)
_retired: list = []          # closed ones, kept referenced until Qt has deleted them itself — letting Python
                             # destroy a window whose mail view is still busy crashes Qt WebEngine


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


class IdleWatcher(QObject):
    """Live sync with Gmail: a second connection sits in IMAP IDLE on the Inbox and
    `changed` fires the moment Gmail reports new mail, a deletion, or a flag change
    (read / starred elsewhere). Reconnects by itself after a network drop."""
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()
        threading.Thread(target=self._loop, name="judo-mail-idle", daemon=True).start()

    def _loop(self) -> None:
        mb = None
        while not self._stop.is_set():
            try:
                mb = mb or Mailbox()
                if mb.wait_for_change(FOLDERS["Inbox"], 240) and not self._stop.is_set():
                    self.changed.emit()
            except Exception:
                if mb:
                    mb.close()
                mb = None
                self._stop.wait(20)          # offline or logged out: try again shortly
        if mb:
            mb.close()

    def stop(self) -> None:
        self._stop.set()


class _Relay(QObject):
    done = pyqtSignal(object, object)


_relay: _Relay | None = None


def run_async(fn, cb) -> None:
    """One-off background call (e.g. checking a new account's password), result on the UI thread."""
    global _relay
    if _relay is None:
        _relay = _Relay()
        _relay.done.connect(lambda cb_, r: cb_(r))

    def job():
        try:
            r = fn()
        except Exception as e:
            r = e
        _relay.done.emit(cb, r)
    ThreadPoolExecutor(1).submit(job)


class MailPage(QWebEnginePage):
    """A mail is a document, not an app: links go to JUDO Browser, and its page noise stays quiet."""

    def acceptNavigationRequest(self, url, nav_type, is_main):
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            ThreadPoolExecutor(1).submit(_open_link, url.toString())
            return False
        return True

    def javaScriptConsoleMessage(self, *a):
        pass   # newsletters' own warnings (meta tags, mixed content…) are not JUDO Mail's errors


class MailView(QWebEngineView):
    """The mail body. Right-click gives a normal menu: Copy, Select all, links (open in JUDO
    Browser / copy address), images (copy / copy address)."""

    def contextMenuEvent(self, e):
        from judo_browser.context_menu import web_menu
        menu = web_menu(self, navigation=False, downloads=False,
                        open_tab=lambda u: ThreadPoolExecutor(1).submit(_open_link, u.toString()),
                        search=("Google", lambda text: ThreadPoolExecutor(1).submit(
                            _open_link, "https://www.google.com/search?q=" + quote_plus(text))))
        if menu is None:
            return super().contextMenuEvent(e)
        self._menu = menu
        menu.popup(e.globalPos())


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


def _round_photo(path: Path, size: int) -> QPixmap | None:
    img = QImage(str(path))
    if img.isNull():
        return None
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addEllipse(0, 0, size, size)
    p.setClipPath(clip)
    p.drawImage(QRect(0, 0, size, size), img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                    Qt.TransformationMode.SmoothTransformation))
    p.end()
    return pm


def _avatar(p: QPainter, rect: QRectF, name: str, key: str, photo: QPixmap | None = None) -> None:
    if photo is not None:
        p.drawPixmap(rect.toRect(), photo)
        return
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(look.avatar_color(key or name)))
    p.drawEllipse(rect)
    f = QFont("Segoe UI", -1, QFont.Weight.DemiBold)
    f.setPixelSize(int(rect.height() * 0.45))
    p.setFont(f)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, look.initial(name))


class Avatar(QWidget):
    clicked = pyqtSignal()

    def __init__(self, size: int, parent=None, clickable: bool = False):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.name, self.key, self.photo = "", "", None
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set(self, name: str, key: str, photo: Path | None = None) -> None:
        self.name, self.key = name, key
        self.photo = _round_photo(photo, self.width() * 2) if photo and photo.exists() else None
        self.update()

    def mousePressEvent(self, e):
        self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _avatar(p, QRectF(self.rect()), self.name, self.key, self.photo)


# ── the message list ────────────────────────────────────────────────────────

class MessageDelegate(QStyledItemDelegate):
    """Two-line rows: ☐ · avatar · sender · date / subject — preview · 📎 · ☆.
    Unread rows are bold with an accent mark; the checkbox and star are clickable."""
    ROW = 72

    def __init__(self, t: dict, is_checked, on_check, on_star, parent=None):
        super().__init__(parent)
        self.t, self.is_checked, self.on_check, self.on_star = t, is_checked, on_check, on_star

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW)

    @staticmethod
    def _check_rect(r: QRect) -> QRect:
        return QRect(r.left() + 16, r.top() + 27, 18, 18)

    @staticmethod
    def _star_rect(r: QRect) -> QRect:
        return QRect(r.right() - 34, r.top() + 38, 22, 22)

    def paint(self, p: QPainter, option, index):
        s: Summary = index.data(SUMMARY)
        t, r = self.t, option.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        checked = self.is_checked(s.uid)
        selected = option.state & QStyle.StateFlag.State_Selected
        hover = option.state & QStyle.StateFlag.State_MouseOver
        bg = t["select"] if selected or checked else t["hover"] if hover else None
        if bg:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(bg))
            p.drawRoundedRect(QRectF(r.adjusted(6, 2, -6, -2)), 12, 12)
        if s.unread:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["accent"]))
            p.drawRoundedRect(QRectF(r.left() + 7, r.top() + 20, 3, r.height() - 40), 1.5, 1.5)

        cb = QRectF(self._check_rect(r))
        if checked:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["accent"]))
            p.drawRoundedRect(cb, 4, 4)
            p.setPen(QPen(QColor("#FFFFFF"), 2.2, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
            tick = QPainterPath()
            tick.moveTo(cb.left() + 4, cb.center().y())
            tick.lineTo(cb.left() + 7.5, cb.bottom() - 4.5)
            tick.lineTo(cb.right() - 4, cb.top() + 5)
            p.drawPath(tick)
        else:
            p.setPen(QPen(QColor(t["faint"] if (hover or selected) else t["line"]), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(cb.adjusted(1, 1, -1, -1), 4, 4)
        _avatar(p, QRectF(r.left() + 46, r.top() + 16, 40, 40), s.sender, s.address)

        x, right = r.left() + 98, r.right() - 16
        date = look.when_text(s.when, s.date)
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold if s.unread else QFont.Weight.Normal))
        dw = p.fontMetrics().horizontalAdvance(date)
        p.setPen(QColor(t["accent"] if s.unread else t["sub"]))
        p.drawText(QRect(right - dw, r.top() + 14, dw, 20), Qt.AlignmentFlag.AlignVCenter, date)

        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold if s.unread else QFont.Weight.Normal))
        p.setPen(QColor(t["text"]))
        w1 = right - dw - 12 - x
        p.drawText(QRect(x, r.top() + 14, w1, 20), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(s.sender or "(unknown)", Qt.TextElideMode.ElideRight, w1))

        line2 = r.top() + 39
        w2 = right - 32 - x - (24 if s.attachment else 0)
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold if s.unread else QFont.Weight.Normal))
        subject = p.fontMetrics().elidedText(s.subject, Qt.TextElideMode.ElideRight, int(w2 * 0.72))
        sw = p.fontMetrics().horizontalAdvance(subject)
        p.setPen(QColor(t["text"] if s.unread else t["sub"]))
        p.drawText(QRect(x, line2, sw + 2, 20), Qt.AlignmentFlag.AlignVCenter, subject)
        if s.snippet and w2 - sw > 40:
            p.setFont(QFont("Segoe UI", 9))
            p.setPen(QColor(t["faint"]))
            rest = p.fontMetrics().elidedText(f"  —  {s.snippet}", Qt.TextElideMode.ElideRight, w2 - sw)
            p.drawText(QRect(x + sw, line2, w2 - sw, 20), Qt.AlignmentFlag.AlignVCenter, rest)
        if s.attachment:
            look.icon("attach", t["sub"], 16).paint(p, QRect(right - 56, line2 + 2, 16, 16))

        star = self._star_rect(r)
        name, color = ("star", t["star"]) if s.starred else ("star_border", t["faint"])
        if s.starred or hover or selected:
            look.icon(name, color, 18).paint(p, star)
        p.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            pos = event.position().toPoint()
            on_check = self._check_rect(option.rect).adjusted(-8, -8, 8, 8).contains(pos)
            on_star = self._star_rect(option.rect).contains(pos)
            if on_check or on_star:
                if event.type() == QEvent.Type.MouseButtonRelease:
                    (self.on_check if on_check else self.on_star)(index.row())
                return True         # don't also open the message
        return super().editorEvent(event, model, option, index)


class FolderDelegate(QStyledItemDelegate):
    """Sidebar rows: icon, name and an unread count; the current one is a filled pill.
    Section headers ("Labels") are plain captions."""

    def __init__(self, t: dict, parent=None):
        super().__init__(parent)
        self.t = t
        self.counts: dict[str, int] = {}
        self.compact = False          # the collapsed menu: icons only, a dot for unread

    def sizeHint(self, option, index):
        if index.data(KIND) == "header":
            return QSize(option.rect.width(), 18 if self.compact else 46)
        return QSize(option.rect.width(), 40)

    def paint(self, p: QPainter, option, index):
        t, r, name, kind = self.t, option.rect, index.data(), index.data(KIND)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if kind == "header" and self.compact:
            p.setPen(QColor(t["line"]))
            p.drawLine(r.left() + 16, r.center().y(), r.right() - 16, r.center().y())
            p.restore()
            return
        if kind == "header":
            p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            p.setPen(QColor(t["text"]))
            p.drawText(QRect(r.left() + 16, r.top() + 14, r.width() - 30, 28), Qt.AlignmentFlag.AlignVCenter, name)
            p.restore()
            return
        selected = option.state & QStyle.StateFlag.State_Selected
        if self.compact:
            ic = "label" if kind == "label" else next((i for n, i, *_ in SIDEBAR if n == name), "inbox")
            pill = QRectF(r.center().x() - 26, r.top() + 4, 52, 32)
            if selected or option.state & QStyle.StateFlag.State_MouseOver:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t["select"] if selected else t["hover"]))
                p.drawRoundedRect(pill, 16, 16)
            look.icon(ic, t["accent"] if selected else t["sub"], 20).paint(
                p, QRect(int(pill.center().x()) - 10, r.top() + 10, 20, 20))
            if self.counts.get(name, 0):
                p.setPen(QPen(QColor(t["card"]), 2))
                p.setBrush(QColor(look.SPECTRUM[1]))
                p.drawEllipse(QRectF(pill.center().x() + 5, r.top() + 7, 9, 9))
            p.restore()
            return
        if selected or option.state & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["select"] if selected else t["hover"]))
            p.drawRoundedRect(QRectF(r.adjusted(0, 2, -6, -2)), 18, 18)
        color = t["accent"] if selected else t["sub"]
        ic = "label" if kind == "label" else next((i for n, i, *_ in SIDEBAR if n == name), "inbox")
        look.icon(ic, color, 20).paint(p, QRect(r.left() + 16, r.top() + 10, 20, 20))
        count = self.counts.get(name, 0)
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold if selected or count else QFont.Weight.Normal))
        p.setPen(QColor(t["accent"] if selected else t["text"]))
        p.drawText(QRect(r.left() + 50, r.top(), r.width() - 120, r.height()), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, r.width() - 120))
        if count:
            p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            p.drawText(QRect(r.right() - 74, r.top(), 60, r.height()),
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


# ── accounts ────────────────────────────────────────────────────────────────

class AccountDialog(QDialog):
    """Add a Gmail account the way Google signs you in: first the email, then, on its own
    page with the address shown as a chip, the password. Gmail only lets other apps in
    with an App Password, so that is what the second page asks for; it is checked with
    Gmail before anything is saved."""

    def __init__(self, parent=None, first: bool = False):
        super().__init__(parent, windowTitle="Sign in – JUDO Mail")
        self.t = parent.t if parent else look.palette()
        self.setStyleSheet(look.stylesheet(self.t))
        self.setWindowIcon(look.app_icon())
        self.setFixedWidth(480)
        self.first = first
        self.pages = QStackedWidget()
        self.pages.addWidget(self._email_page())
        self.pages.addWidget(self._password_page())
        card = QFrame(objectName="Card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(36, 32, 36, 28)
        cl.addWidget(self.pages)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(card)

    @staticmethod
    def _page() -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        logo = QLabel()
        logo.setPixmap(look.app_icon().pixmap(44, 44))
        v.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(14)
        return page, v

    # page 1: the email
    def _email_page(self) -> QWidget:
        page, v = self._page()
        v.addWidget(QLabel("Sign in", objectName="SignTitle", alignment=Qt.AlignmentFlag.AlignCenter))
        v.addSpacing(6)
        v.addWidget(QLabel("to continue to JUDO Mail" if self.first else "Add another Google account",
                           objectName="SignSub", alignment=Qt.AlignmentFlag.AlignCenter))
        v.addSpacing(30)
        self.address = QLineEdit(objectName="Box", placeholderText="Email or Gmail username")
        self.address.returnPressed.connect(self._next)
        self.address.textEdited.connect(lambda _: self._error(self.address, self.email_error, ""))
        v.addWidget(self.address)
        v.addSpacing(6)
        self.email_error = QLabel("", objectName="Error", wordWrap=True)
        self.email_error.hide()
        v.addWidget(self.email_error)
        v.addSpacing(8)
        v.addWidget(_link("Forgot email?", "https://accounts.google.com/signin/usernamerecovery"),
                    0, Qt.AlignmentFlag.AlignLeft)
        v.addSpacing(26)
        v.addWidget(QLabel("Only Gmail accounts can be added. Your sign-in stays on this PC.",
                           objectName="SignSub", wordWrap=True))
        v.addSpacing(34)
        row = QHBoxLayout()
        row.addWidget(_link("Create account", "https://accounts.google.com/signup"))
        row.addStretch(1)
        if not self.first:
            cancel = QPushButton("Cancel", objectName="Flat")
            cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel.clicked.connect(self.reject)
            row.addWidget(cancel)
        self.next = QPushButton("Next", objectName="Primary")
        self.next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next.clicked.connect(self._next)
        row.addWidget(self.next)
        v.addLayout(row)
        return page

    # page 2: the password
    def _password_page(self) -> QWidget:
        page, v = self._page()
        self.welcome = QLabel("Welcome", objectName="SignTitle", alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.welcome)
        v.addSpacing(12)
        self.who = QPushButton(objectName="Who")            # the address chip; click it to go back
        self.who.setCursor(Qt.CursorShape.PointingHandCursor)
        self.who.setToolTip("Use a different account")
        self.who.setIconSize(QSize(22, 22))
        self.who.clicked.connect(self._back)
        v.addWidget(self.who, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(26)
        v.addWidget(QLabel("To continue, enter the <b>App Password</b> for this account: the 16-letter "
                           "password Google makes for one app. Your normal Google password won't work in "
                           "mail apps.", objectName="SignSub", wordWrap=True))
        v.addSpacing(16)
        self.password = QLineEdit(objectName="Box", placeholderText="Enter your App Password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.returnPressed.connect(self._check)
        self.password.textEdited.connect(lambda _: self._error(self.password, self.pw_error, ""))
        v.addWidget(self.password)
        v.addSpacing(6)
        self.pw_error = QLabel("", objectName="Error", wordWrap=True)
        self.pw_error.hide()
        v.addWidget(self.pw_error)
        v.addSpacing(8)
        self.show_pw = QCheckBox("Show password")
        self.show_pw.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_pw.toggled.connect(lambda on: self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        v.addWidget(self.show_pw)
        v.addSpacing(34)
        row = QHBoxLayout()
        row.addWidget(_link("Get an App Password", "https://myaccount.google.com/apppasswords"))
        row.addStretch(1)
        self.ok = QPushButton("Next", objectName="Primary")
        self.ok.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok.clicked.connect(self._check)
        row.addWidget(self.ok)
        v.addLayout(row)
        return page

    # the flow
    @staticmethod
    def _error(field: QLineEdit, label: QLabel, text: str) -> None:
        label.setText(("\u26a0  " + text) if text else "")
        label.setVisible(bool(text))
        field.setProperty("error", bool(text))
        field.style().unpolish(field)
        field.style().polish(field)

    def _next(self) -> None:
        address = self.address.text().strip().lower()
        if address and "@" not in address:
            address += "@gmail.com"                        # like Google: a bare username means Gmail
        name, _, domain = address.partition("@")
        if not address:
            return self._error(self.address, self.email_error, "Enter an email")
        if not name or "." not in domain or " " in address:
            return self._error(self.address, self.email_error, "Enter a valid email")
        if address in [a.lower() for a in mbx.accounts()]:
            return self._error(self.address, self.email_error, "This account is already added")
        self.address.setText(address)
        self.who.setText("  " + address + "   \u25be")
        self.who.setIcon(QIcon(_avatar_pixmap(address, 44)))
        self.welcome.setText("Hi " + look.friendly_name(address).split()[0])
        self.password.clear()
        self._error(self.password, self.pw_error, "")
        self.pages.setCurrentIndex(1)
        self.password.setFocus()

    def _back(self) -> None:
        self.pages.setCurrentIndex(0)
        self.address.setFocus()
        self.address.selectAll()

    def _check(self) -> None:
        address, pw = self.address.text().strip(), self.password.text().replace(" ", "")
        if not pw:
            return self._error(self.password, self.pw_error, "Enter a password")
        if len(pw) != 16 or not pw.isalpha():
            return self._error(self.password, self.pw_error,
                               "That isn't an App Password. It is 16 letters, like abcd efgh ijkl mnop")
        self.ok.setEnabled(False)
        self.ok.setText("Checking\u2026")
        run_async(lambda: mbx.check_login(address, pw), lambda r: self._checked(r, address, pw))

    def _checked(self, result, address: str, pw: str) -> None:
        self.ok.setEnabled(True)
        self.ok.setText("Next")
        if isinstance(result, Exception):
            text = str(result)
            if "AUTHENTICATIONFAILED" in text.upper() or "credentials" in text.lower():
                text = "Wrong App Password, or IMAP is off for this account. Try again or get a new one."
            else:
                text = f"Couldn't reach Gmail. Check your internet and try again. ({text})"
            return self._error(self.password, self.pw_error, text)
        mbx.add_account(address, pw)
        self.accept()


def _avatar_pixmap(address: str, size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    photo = look.photo_path(address)
    _avatar(p, QRectF(0, 0, size, size), look.friendly_name(address), address,
            _round_photo(photo, size) if photo.exists() else None)
    p.end()
    return pm


def _link(text: str, url: str) -> QPushButton:
    b = QPushButton(text, objectName="Link")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(lambda: ThreadPoolExecutor(1).submit(_open_link, url))
    return b


class AccountPopup(QFrame):
    """The account card under the avatar, laid out like Google's: the address, a large
    photo with a camera badge, "Hi, <name>!", Manage your Google Account, the other
    accounts with Add / Sign out in one rounded group, and Device / Light / Dark."""

    def __init__(self, win: "MailWindow"):
        super().__init__(win, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.win, t, me = win, win.t, win.address
        self.setStyleSheet(look.stylesheet(t))
        card = QFrame(objectName="AcctPopup")
        card.setFixedWidth(392)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(0)

        top = QHBoxLayout()
        top.addSpacing(36)
        email = QLabel(html.escape(me or "No account"), objectName="AcctEmail", alignment=Qt.AlignmentFlag.AlignCenter)
        top.addWidget(email, 1)
        top.addWidget(_tool("close", "Close", self.close, t, 18))
        lay.addLayout(top)
        lay.addSpacing(14)

        pic = QWidget()
        pic.setFixedSize(84, 84)
        big = Avatar(80, pic, clickable=True)
        big.move(2, 2)
        big.set(look.friendly_name(me) if me else "?", me, look.photo_path(me))
        big.setToolTip("Change profile photo")
        big.clicked.connect(lambda: self._then(win._change_photo))
        cam = QToolButton(pic, objectName="Cam", toolTip="Change profile photo")
        cam.setIcon(look.icon("camera", t["text"], 16))
        cam.setIconSize(QSize(16, 16))
        cam.setFixedSize(30, 30)
        cam.move(56, 56)
        cam.setCursor(Qt.CursorShape.PointingHandCursor)
        cam.clicked.connect(lambda: self._then(win._change_photo))
        lay.addWidget(pic, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(10)
        first = look.friendly_name(me).split()[0] if me else "there"
        lay.addWidget(QLabel(f"Hi, {html.escape(first)}!", objectName="AcctHi", alignment=Qt.AlignmentFlag.AlignCenter))
        lay.addSpacing(12)
        manage = QPushButton("Manage your Google Account", objectName="AcctManage")
        manage.setFixedHeight(40)                    # radius 20 needs the full 40 px to come out round
        manage.setCursor(Qt.CursorShape.PointingHandCursor)
        manage.clicked.connect(lambda: self._then(
            lambda: ThreadPoolExecutor(1).submit(_open_link, "https://myaccount.google.com/")))
        lay.addWidget(manage, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(18)

        rows = []
        for a in [x for x in mbx.accounts() if x != me]:
            rows.append(self._account_row(a))
        rows.append(self._row("Add another account", win._add_account, "person_add"))
        if me and look.photo_path(me).exists():
            rows.append(self._row("Remove profile photo", win._remove_photo, "close"))
        if me:
            rows.append(self._row("Sign out of this account", win._sign_out, "logout"))
        for i, b in enumerate(rows):
            b.setProperty("first", i == 0)
            b.setProperty("last", i == len(rows) - 1)
            lay.addWidget(b)
            if i < len(rows) - 1:
                lay.addSpacing(2)

        lay.addSpacing(16)
        theme_label = QLabel("Theme", objectName="Sub")
        lay.addWidget(theme_label)
        lay.addSpacing(6)
        seg = QHBoxLayout()
        seg.setSpacing(6)
        current = look.load_settings().get("theme", "system")
        for key, label in (("system", "Device"), ("light", "Light"), ("dark", "Dark")):
            b = QPushButton(label, objectName="Seg", checkable=True)
            b.setChecked(key == current)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, k=key: self._then(lambda: win._set_theme(k)))
            seg.addWidget(b)
        lay.addLayout(seg)
        lay.addSpacing(12)
        lay.addWidget(QLabel("Accounts, photos and App Passwords stay on this PC.", objectName="Fine",
                             alignment=Qt.AlignmentFlag.AlignCenter))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

    def _row(self, text: str, fn, icon_name: str = "") -> QPushButton:
        b = QPushButton(("   " if icon_name else "  ") + text, objectName="AcctRow")
        if icon_name:
            b.setIcon(look.icon(icon_name, self.win.t["sub"], 20))
            b.setIconSize(QSize(20, 20))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda: self._then(fn))
        return b

    def _account_row(self, address: str) -> QPushButton:
        """Another saved account: its avatar, name and address, a click away."""
        b = QPushButton(objectName="AcctRow", toolTip=f"Switch to {address}")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(60)
        b.clicked.connect(lambda: self._then(lambda: self.win._switch(address)))
        row = QHBoxLayout(b)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(14)
        av = Avatar(34)
        av.set(look.friendly_name(address), address, look.photo_path(address))
        text = QVBoxLayout()
        text.setSpacing(0)
        name = QLabel(html.escape(look.friendly_name(address)), objectName="Sender")
        mail = QLabel(html.escape(address), objectName="Sub")
        for w in (av, name, mail):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text.addWidget(name)
        text.addWidget(mail)
        row.addWidget(av)
        row.addLayout(text, 1)
        return b

    def _then(self, fn) -> None:
        """Close the card first, then act (dialogs and window swaps shouldn't open under a popup)."""
        self.close()
        QTimer.singleShot(0, fn)

    def show_under(self, anchor: QWidget) -> None:
        self.adjustSize()
        pos = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 8))
        self.move(pos.x() - self.width() + 6, pos.y())
        self.show()

    def hideEvent(self, e) -> None:
        # tell the window the card is gone, and when, so the avatar click that closed it
        # doesn't straight away open a new one
        self.win._popup, self.win._popup_closed = None, time.monotonic()
        super().hideEvent(e)


# ── the window ──────────────────────────────────────────────────────────────

class MailWindow(QMainWindow):
    def __init__(self, worker: Worker | None = None, address: str = ""):
        super().__init__()
        self.t = t = look.palette()
        self.setWindowTitle("JUDO Mail")
        self.setWindowIcon(look.app_icon())
        self.setStyleSheet(look.stylesheet(t))
        self.resize(1400, 880)
        self.worker = worker or Worker()
        self._keep_worker = False
        self._closed = False
        self._popup: AccountPopup | None = None
        self._popup_closed = 0.0
        self.folder, self.fixed_query, self.tab, self.query = FOLDERS["Inbox"], "", "primary", ""
        self.uids: list[str] = []
        self.rows: list[Summary] = []
        self.checked: set[str] = set()
        self.current: Mail | None = None
        self.current_summary: Summary | None = None
        self._loading = False
        if not address:
            try:
                address = mbx.credentials()[0]
            except Exception:
                address = ""
        self.address = address

        # top bar: brand · search · refresh · account
        brand = QHBoxLayout()
        brand.addWidget(_tool("menu", "Main menu", self._toggle_menu, t, 24))
        brand.addSpacing(10)
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
        self.me = Avatar(38, clickable=True)
        self.me.set(look.friendly_name(address) if address else "?", address, look.photo_path(address))
        self.me.setToolTip(f"JUDO Mail account\n{address}" if address else "Add a Gmail account")
        self.me.clicked.connect(self._account_menu)
        top = QHBoxLayout()
        top.setContentsMargins(18, 10, 18, 6)
        top.addLayout(brand)
        top.addSpacing(40)
        top.addWidget(self.search, 1)
        top.addStretch(0)
        top.addWidget(_tool("refresh", "Refresh (F5)", lambda: self.load_folder(), t, 22))
        top.addSpacing(6)
        top.addWidget(self.me)

        # left: compose + folders + labels
        self.compose_btn = compose = QPushButton("  Compose", objectName="Compose")
        compose.setIcon(look.icon("edit", "#FFFFFF", 20))
        compose.setIconSize(QSize(20, 20))
        compose.setCursor(Qt.CursorShape.PointingHandCursor)
        compose.setToolTip("Compose (Ctrl+N)")
        compose.clicked.connect(self.compose)
        self.folders = QListWidget(objectName="Folders")
        self.folder_delegate = FolderDelegate(t, self.folders)
        self.folders.setItemDelegate(self.folder_delegate)
        self.folders.setMouseTracking(True)
        for n, _, folder, q in SIDEBAR:
            self._sidebar_item(n, "folder", folder, q)
        self.folders.setCurrentRow(0)
        self.folders.currentItemChanged.connect(self._folder_changed)
        self.left = left = QWidget()
        left.setFixedWidth(256)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(12, 8, 6, 12)
        lv.addWidget(compose, 0, Qt.AlignmentFlag.AlignLeft)
        lv.addSpacing(14)
        lv.addWidget(self.folders, 1)

        # middle: title, Inbox tabs, bulk toolbar, the list
        self.title = QLabel("Inbox", objectName="Title")
        self.count = QLabel("", objectName="Count")
        lh = QHBoxLayout()
        lh.setContentsMargins(20, 14, 16, 2)
        lh.addWidget(self.title)
        lh.addStretch(1)
        lh.addWidget(self.count)
        self.tabs: dict[str, QPushButton] = {}
        self.tab_bar = QWidget()
        tl = QHBoxLayout(self.tab_bar)
        tl.setContentsMargins(10, 0, 10, 0)
        tl.setSpacing(0)
        for key, label, ic in TABS:
            b = QPushButton(f"  {label}", objectName="Tab")
            b.setIcon(look.icon(ic, t["sub"], 18))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, k=key: self._tab(k))
            tl.addWidget(b, 1)
            self.tabs[key] = b
        self.toolbar = QWidget(objectName="Toolbar")
        tb = QHBoxLayout(self.toolbar)
        tb.setContentsMargins(22, 4, 16, 4)
        tb.setSpacing(2)
        self.select_all = QCheckBox()
        self.select_all.setToolTip("Select all loaded conversations")
        self.select_all.clicked.connect(self._select_all)
        tb.addWidget(self.select_all)
        tb.addSpacing(8)
        tb.addWidget(_tool("refresh", "Refresh", lambda: self.load_folder(), t, 18))
        self.bulk: dict[str, QToolButton] = {}
        for key, ic, tip in (("archive", "archive", "Archive"), ("delete", "trash", "Delete"),
                             ("read", "drafts_read", "Mark as read"), ("unread", "mail", "Mark as unread"),
                             ("star", "star_border", "Star")):
            b = _tool(ic, tip, lambda _, k=key: self._bulk(k), t, 18)
            tb.addWidget(b)
            self.bulk[key] = b
        tb.addSpacing(10)
        self.selected_label = QLabel("", objectName="Selected")
        tb.addWidget(self.selected_label)
        tb.addStretch(1)
        self.model = QStandardItemModel(self)
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setItemDelegate(MessageDelegate(t, lambda uid: uid in self.checked, self._check_row,
                                                  self._star_row, self.list))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.list.setUniformItemSizes(True)
        self.list.clicked.connect(lambda idx: self._open(idx.row()))
        self.list.activated.connect(lambda idx: self._open(idx.row()))
        self.list.verticalScrollBar().valueChanged.connect(self._maybe_more)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._row_menu)
        QShortcut(QKeySequence.StandardKey.Copy, self.list, activated=lambda: self._copy_row(
            self.list.currentIndex().row(), "line"), context=Qt.ShortcutContext.WidgetShortcut)
        self.list_empty = QLabel("", objectName="Empty", alignment=Qt.AlignmentFlag.AlignCenter)
        self.list_empty.hide()
        mid = QFrame(objectName="Card")
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 8)
        mv.setSpacing(0)
        mv.addLayout(lh)
        mv.addWidget(self.tab_bar)
        mv.addWidget(self.toolbar)
        mv.addWidget(self.list, 1)
        mv.addWidget(self.list_empty, 1)

        # right: the open message
        self.subject = QLabel(objectName="Subject", wordWrap=True,
                              textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sender_avatar = Avatar(44)
        self.sender = QLabel(objectName="Sender", textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        self.unsub = QPushButton("Unsubscribe", objectName="Unsub")
        self.unsub.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unsub.clicked.connect(self._unsubscribe)
        sender_row = QHBoxLayout()
        sender_row.setSpacing(10)
        sender_row.addWidget(self.sender)
        sender_row.addWidget(self.unsub)
        sender_row.addStretch(1)
        self.recipients = QLabel(objectName="Sub", wordWrap=True,
                                 textInteractionFlags=Qt.TextInteractionFlag.TextSelectableByMouse)
        self.when = QLabel(objectName="Sub")
        who = QVBoxLayout()
        who.setSpacing(2)
        who.addLayout(sender_row)
        who.addWidget(self.recipients)
        meta = QHBoxLayout()
        meta.setSpacing(12)
        meta.addWidget(self.sender_avatar, 0, Qt.AlignmentFlag.AlignTop)
        meta.addLayout(who, 1)
        meta.addWidget(self.when, 0, Qt.AlignmentFlag.AlignTop)
        self.actions: dict[str, QToolButton] = {}
        bar = QHBoxLayout()
        bar.setSpacing(2)
        bar.addWidget(_tool("back", "Back to list (Esc)", self._back_to_list, t))
        bar.addSpacing(10)
        for key, ic, tip, fn in (("reply", "reply", "Reply (Ctrl+R)", self._reply),
                                 ("reply_all", "reply_all", "Reply all", lambda: self._reply(all_=True)),
                                 ("forward", "forward", "Forward", self._forward),
                                 ("archive", "archive", "Archive (E)", lambda: self._act_current("archive")),
                                 ("delete", "trash", "Delete (Del)", lambda: self._act_current("delete")),
                                 ("unread", "mail", "Mark as unread", lambda: self._act_current("unread")),
                                 ("star", "star_border", "Star", self._star)):
            b = _tool(ic, tip, fn, t)
            bar.addWidget(b)
            self.actions[key] = b
            if key == "forward":
                bar.addSpacing(10)
        bar.addStretch(1)
        self.position = QLabel("", objectName="Position")
        bar.addWidget(self.position)
        bar.addSpacing(6)
        self.newer = _tool("chevron_left", "Newer", lambda: self._step(-1), t)
        self.older = _tool("chevron_right", "Older", lambda: self._step(1), t)
        bar.addWidget(self.newer)
        bar.addWidget(self.older)
        self.attach_row = QHBoxLayout()
        self.attach_row.setSpacing(8)
        self.view = MailView()
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
        right = QFrame(objectName="Card")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.reader, 1)

        self.stage = QStackedWidget()        # page 0: the list · page 1: the open mail
        self.stage.addWidget(mid)
        self.stage.addWidget(right)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 14, 0)
        body.addWidget(left)
        body.addWidget(self.stage, 1)
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 6)
        cl.addLayout(top)
        cl.addLayout(body, 1)
        self.setCentralWidget(central)

        for keys, fn in (("Ctrl+N", self.compose), ("Ctrl+R", self._reply),
                         ("Delete", lambda: self._act_current("delete")), ("E", lambda: self._act_current("archive")),
                         ("F5", lambda: self.load_folder()), ("Ctrl+F", self.search.setFocus),
                         ("/", self.search.setFocus), ("Esc", self._back_to_list), ("U", self._back_to_list),
                         ("K", lambda: self._step(-1)), ("J", lambda: self._step(1))):
            QShortcut(QKeySequence(keys), self, activated=fn)
        self._set_compact(bool(look.load_settings().get("menu_collapsed")), save=False)
        self._reader_enabled(False)
        self._update_toolbar()
        self.load_folder()
        self._run(lambda mb: mb.labels(), self._got_labels)
        if address and address.lower() not in look.load_settings().get("names", {}):
            self._run(lambda mb: mb.display_name(), self._got_name)
        # live sync: Gmail pushes changes through IDLE; a slower timer covers other folders and dropped pushes
        self.watcher = IdleWatcher() if isinstance(self.worker, Worker) else None
        if self.watcher:
            self.watcher.changed.connect(self._sync)
        timer = QTimer(self, interval=SYNC_MS)
        timer.timeout.connect(self._sync)
        timer.start()

    def _run(self, fn, cb=lambda r: None) -> None:
        """Gmail work on the shared connection. A theme or account change swaps this window
        for a new one while answers are still on their way; once closed, it ignores them
        instead of touching widgets Qt has already deleted."""
        self.worker.run(fn, lambda r: None if self._closed else cb(r))

    # ── sidebar, tabs, list ────────────────────────────────────────────────
    def _sidebar_item(self, name: str, kind: str, folder: str = "", query: str = "") -> QListWidgetItem:
        item = QListWidgetItem(name)
        item.setToolTip(name)
        item.setData(KIND, kind)
        item.setData(TARGET, folder)
        item.setData(FIXED_QUERY, query)
        if kind == "header":
            item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.folders.addItem(item)
        return item

    def _got_name(self, result) -> None:
        if isinstance(result, str) and result:
            look.remember_name(self.address, result)
            self.me.set(result, self.address, look.photo_path(self.address))
            self.me.setToolTip(f"JUDO Mail account\n{result}\n{self.address}")

    def _got_labels(self, result) -> None:
        if isinstance(result, list) and result:
            self._sidebar_item("Labels", "header")
            for name in result:
                self._sidebar_item(name, "label", name, "")

    def compose(self) -> None:
        Compose(self, self.worker).exec()

    def _folder_changed(self, item, _prev=None) -> None:
        if item is None or item.data(KIND) == "header":
            return
        self.folder, self.fixed_query, self.query = item.data(TARGET), item.data(FIXED_QUERY) or "", ""
        self.tab = "primary"
        self.search.clear()
        self.load_folder()

    def _tabs_on(self) -> bool:
        return self.folder == FOLDERS["Inbox"] and not self.fixed_query and not self.query

    def _tab(self, key: str) -> None:
        self.tab = key
        self.load_folder()

    def _search(self) -> None:
        self.query = self.search.text().strip()
        self.load_folder()

    def _effective_query(self) -> str:
        parts = [self.fixed_query, f"category:{self.tab}" if self._tabs_on() else "", self.query]
        return " ".join(p for p in parts if p)

    def _view_name(self) -> str:
        item = self.folders.currentItem()
        name = item.text() if item else "Inbox"
        return dict((k, l) for k, l, _ in TABS)[self.tab] if self._tabs_on() and self.tab != "primary" else name

    def load_folder(self) -> None:
        self.statusBar().showMessage("Loading…")
        folder, query = self.folder, self._effective_query()
        self.tab_bar.setVisible(self._tabs_on())
        icons = {k: i for k, _, i in TABS}
        for key, b in self.tabs.items():
            b.setProperty("on", key == self.tab)
            b.setIcon(look.icon(icons[key], self.t["accent" if key == self.tab else "sub"], 18))
            b.style().unpolish(b)
            b.style().polish(b)
        self._run(lambda mb: (mb.uids(folder, query), folder, query), self._got_uids)
        self._refresh_counts()

    def _refresh_counts(self) -> None:
        self._run(lambda mb: mb.unseen(FOLDERS["Inbox"]), self._got_unseen)
        if self._tabs_on():
            self._run(lambda mb: mb.category_new(), self._got_new)

    def _got_unseen(self, result) -> None:
        if isinstance(result, int):
            self.folder_delegate.counts["Inbox"] = result
            self.folders.viewport().update()

    def _got_new(self, result) -> None:
        if not isinstance(result, dict):
            return
        for key, label, _ in TABS:
            n = result.get(key, 0)
            self.tabs[key].setText(f"  {label}" + (f"   ·  {n} new" if n else ""))

    def _got_uids(self, result) -> None:
        if self._failed(result):
            return
        uids, folder, query = result
        if (folder, query) != (self.folder, self._effective_query()):
            return   # the user moved on while this was loading
        self.uids, self.rows = uids, []
        self.checked.clear()
        self.model.clear()
        self._reader_enabled(False)
        self._update_toolbar()
        self._load_more()

    def _load_more(self) -> None:
        start = len(self.rows)
        chunk, folder = self.uids[start:start + PAGE], self.folder
        if not chunk:
            self._status()
            return
        self._loading = True
        self._run(lambda mb: (mb.summaries(folder, chunk), folder), self._got_rows)

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
        self._update_toolbar()
        self._status()

    def _status(self) -> None:
        name = self._view_name()
        self.title.setText(f"Results for “{self.query}”" if self.query else name)
        n, shown = len(self.uids), len(self.rows)
        self.count.setText(f"1–{shown:,} of {n:,}" if n else "")
        empty = n == 0
        self.list.setVisible(not empty)
        self.list_empty.setVisible(empty)
        self.list_empty.setText("No messages match your search." if self.query else f"Nothing in {name}.")
        self.statusBar().showMessage(f"{name}: {n:,} conversations" if not self.query else f"{n:,} results", 4000)
        self.setWindowTitle(f"{name} — JUDO Mail")

    def _sync(self) -> None:
        """Bring the open list in line with Gmail without reloading it: new mail is added
        at the top, mail deleted or moved elsewhere disappears, and read / starred changes
        made in Gmail (web, phone) show up. The open message and the scroll position stay."""
        if self._loading:
            return
        folder, query, loaded = self.folder, self._effective_query(), [s.uid for s in self.rows]

        def work(mb):
            uids = mb.uids(folder, query)
            have = set(loaded)
            fresh = [u for u in uids[:PAGE] if u not in have]
            still = set(uids)
            return (folder, query, uids, mb.summaries(folder, fresh) if fresh else [],
                    mb.flags(folder, [u for u in loaded if u in still]))
        self._run(work, self._synced)

    def _synced(self, result) -> None:
        if isinstance(result, Exception):
            return                                   # a missed sync is retried by the next one
        folder, query, uids, fresh, flags = result
        if (folder, query) != (self.folder, self._effective_query()):
            return
        still = set(uids)
        for row in reversed(range(len(self.rows))):          # gone from Gmail (deleted, archived elsewhere)
            uid = self.rows[row].uid
            if uid not in still:
                self.model.removeRow(row)
                self.rows.pop(row)
                self.checked.discard(uid)
                if self.current and self.current.uid == uid:
                    self._reader_enabled(False)
        for row, s in enumerate(self.rows):                  # read / starred in Gmail itself
            if s.uid in flags and (s.unread, s.starred) != flags[s.uid]:
                s.unread, s.starred = flags[s.uid]
                self._refresh_row(row)
        order = {u: i for i, u in enumerate(uids)}
        for s in sorted(fresh, key=lambda x: order.get(x.uid, 0)):   # new mail, in Gmail's order
            pos = next((i for i, r in enumerate(self.rows) if order.get(r.uid, 0) > order.get(s.uid, 0)), len(self.rows))
            item = QStandardItem()
            item.setData(s, SUMMARY)
            item.setEditable(False)
            self.model.insertRow(pos, item)
            self.rows.insert(pos, s)
        self.uids = uids
        self._update_toolbar()
        self._status()
        self._refresh_counts()
        if fresh:
            n = sum(1 for s in fresh if s.unread)
            if n:
                self.statusBar().showMessage(f"{n} new message{'s' if n != 1 else ''}", 6000)

    def _refresh_row(self, row: int) -> None:
        self.list.update(self.model.index(row, 0))

    # ── checking several messages ──────────────────────────────────────────
    def _check_row(self, row: int) -> None:
        if 0 <= row < len(self.rows):
            self.checked.symmetric_difference_update({self.rows[row].uid})
            self._refresh_row(row)
            self._update_toolbar()

    def _select_all(self) -> None:
        loaded = {s.uid for s in self.rows}
        self.checked = set() if loaded and self.checked >= loaded else loaded
        self.list.viewport().update()
        self._update_toolbar()

    def _update_toolbar(self) -> None:
        n = len(self.checked)
        for key, b in self.bulk.items():
            b.setVisible(n > 0 and (key != "archive" or self.folder == FOLDERS["Inbox"]))
        self.selected_label.setText(f"{n:,} selected" if n else "")
        loaded = len(self.rows)
        self.select_all.setTristate(False)
        self.select_all.setCheckState(Qt.CheckState.Checked if n and n >= loaded else
                                      Qt.CheckState.PartiallyChecked if n else Qt.CheckState.Unchecked)

    def _bulk(self, op: str, uids: list[str] | None = None) -> None:
        uids = uids if uids is not None else [s.uid for s in self.rows if s.uid in self.checked]
        if not uids:
            return
        folder, joined = self.folder, ",".join(uids)
        work = {"archive": lambda mb: mb.archive(folder, joined), "delete": lambda mb: mb.trash(folder, joined),
                "read": lambda mb: mb.mark_read(folder, joined, True),
                "unread": lambda mb: mb.mark_read(folder, joined, False),
                "star": lambda mb: mb.star(folder, joined, True)}[op]
        self._run(work, lambda r: self._failed(r) or self._bulk_done(op, set(uids)))

    def _bulk_done(self, op: str, uids: set[str]) -> None:
        if op in ("archive", "delete"):
            for row in reversed(range(len(self.rows))):
                if self.rows[row].uid in uids:
                    self.model.removeRow(row)
                    self.rows.pop(row)
            self.uids = [u for u in self.uids if u not in uids]
            if self.current and self.current.uid in uids:
                self._reader_enabled(False)
        else:
            for row, s in enumerate(self.rows):
                if s.uid in uids:
                    if op == "star":
                        s.starred = True
                    else:
                        s.unread = op == "unread"
                    self._refresh_row(row)
        self.checked -= uids
        self._update_toolbar()
        self._status()
        self._refresh_counts()
        n = len(uids)
        what = {"archive": "archived", "delete": "moved to Trash", "read": "marked as read",
                "unread": "marked as unread", "star": "starred"}[op]
        self.statusBar().showMessage(f"{n} conversation{'s' if n != 1 else ''} {what}", 5000)

    # ── reading ────────────────────────────────────────────────────────────
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
        self._run(fetch, self._show)

    def _show(self, result) -> None:
        if self._failed(result):
            return
        mail, row = result
        s = self.rows[row] if row < len(self.rows) and self.rows[row].uid == mail.uid else None
        if s and s.unread:
            if self.folder == FOLDERS["Inbox"]:
                self.folder_delegate.counts["Inbox"] = max(0, self.folder_delegate.counts.get("Inbox", 0) - 1)
                self.folders.viewport().update()
            s.unread = False
            self._refresh_row(row)
        self.current, self.current_summary = mail, s
        e = html.escape
        from email.utils import getaddresses
        name, addr = (getaddresses([mail.sender]) or [("", "")])[0]
        self.subject.setText(e(mail.subject or "(no subject)"))
        self.sender_avatar.set(name or addr, addr)
        self.sender.setText(f"{e(name or addr)} <span style='font-weight:400;color:{self.t['sub']}'>"
                            f"{'&lt;' + e(addr) + '&gt;' if name else ''}</span>")
        self.unsub.setVisible(bool(s and s.unsubscribe))
        self.recipients.setText(f"to {e(mail.to)}" + (f" · cc {e(mail.cc)}" if mail.cc else ""))
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
        # plain text follows the theme. The page is served as plain http so a newsletter's http://
        # images and fonts load like they do in a browser instead of being blocked as mixed content.
        paper = bool(mail.html)
        self.paper.setStyleSheet("" if paper else "#Paper { background: transparent; border: none; }")
        self.view.page().setBackgroundColor(QColor("#FFFFFF" if paper else self.t["card"]))
        base = "<style>body{margin:18px 22px;font:15px 'Segoe UI',system-ui,sans-serif;word-wrap:break-word}</style>"
        body = (base + mail.html) if paper else (
            f"<pre style='white-space:pre-wrap;font:15px Segoe UI, system-ui;color:{self.t['text']};margin:4px'>"
            f"{e(mail.text)}</pre>")
        self.view.setHtml(body, QUrl("http://mail.judo.local/"))
        self.actions["archive"].setVisible(self.folder == FOLDERS["Inbox"])
        if s:
            self._star_button(s.starred)
        self._reader_enabled(True)
        self._status()

    def _reader_enabled(self, on: bool) -> None:
        self.stage.setCurrentIndex(1 if on else 0)
        if on:
            self._update_position()
        else:
            self.current, self.current_summary = None, None
            self.view.setHtml("")

    def _save_attachment(self, index: int) -> None:
        mail = self.current
        try:
            from core.user_paths import downloads
            folder = downloads()
        except Exception:
            folder = Path.home() / "Downloads"
        self._run(lambda mb: mb.save_attachment(mail, index, folder),
                        lambda r: self._failed(r) or self.statusBar().showMessage(f"Saved {r}", 8000))

    def _unsubscribe(self) -> None:
        s = self.current_summary
        if not (s and s.unsubscribe):
            return
        if QMessageBox.question(self, "Unsubscribe", f"Unsubscribe from {s.sender}?") != QMessageBox.StandardButton.Yes:
            return
        if s.unsubscribe.lower().startswith("mailto:"):
            u = urlparse(s.unsubscribe)
            q = parse_qs(u.query)
            Compose(self, self.worker, to=unquote(u.path), subject=q.get("subject", ["unsubscribe"])[0],
                    body=q.get("body", ["unsubscribe"])[0], title="Unsubscribe").exec()
        else:
            ThreadPoolExecutor(1).submit(_open_link, s.unsubscribe)
            self.statusBar().showMessage("Opened the unsubscribe page in JUDO Browser", 6000)

    # ── actions on the open message ────────────────────────────────────────
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

    def _row_menu(self, pos: QPoint) -> None:
        """Right-click on a message: copy its sender, address, subject or preview."""
        row = self.list.indexAt(pos).row()
        if not 0 <= row < len(self.rows):
            return
        s = self.rows[row]
        m = QMenu(self.list)
        m.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if s.address:
            m.addAction("Copy email address", lambda: self._copy_row(row, "address"))
        m.addAction("Copy sender name", lambda: self._copy_row(row, "sender"))
        m.addAction("Copy subject", lambda: self._copy_row(row, "subject"))
        if s.snippet:
            m.addAction("Copy preview text", lambda: self._copy_row(row, "snippet"))
        m.addAction("Copy all\tCtrl+C", lambda: self._copy_row(row, "line"))
        m.popup(self.list.viewport().mapToGlobal(pos))

    def _copy_row(self, row: int, what: str) -> None:
        if not 0 <= row < len(self.rows):
            return
        s = self.rows[row]
        text = {"address": s.address, "sender": s.sender, "subject": s.subject, "snippet": s.snippet,
                "line": f"{s.sender} <{s.address}>\n{s.subject}\n{s.snippet}".strip() if s.address
                else f"{s.sender}\n{s.subject}\n{s.snippet}".strip()}[what]
        QApplication.clipboard().setText(text or "")
        self.statusBar().showMessage("Copied", 2000)

    def _act_current(self, op: str) -> None:
        if self.current and (op != "archive" or self.folder == FOLDERS["Inbox"]):
            self._bulk(op, [self.current.uid])

    def _star_button(self, on: bool) -> None:
        b = self.actions["star"]
        b.setIcon(look.icon("star" if on else "star_border", self.t["star"] if on else self.t["sub"], 20))
        b.setToolTip("Unstar" if on else "Star")

    def _star(self) -> None:
        row = self.list.currentIndex().row()
        if self.current and 0 <= row < len(self.rows):
            self._star_row(row)

    def _star_row(self, row: int) -> None:
        if not 0 <= row < len(self.rows):
            return
        s, folder = self.rows[row], self.folder
        on = not s.starred
        self._run(lambda mb: mb.star(folder, s.uid, on), lambda r: self._failed(r) or self._starred(row, on))

    def _starred(self, row: int, on: bool) -> None:
        self.rows[row].starred = on
        self._refresh_row(row)
        if row == self.list.currentIndex().row() and self.current:
            self._star_button(on)
        self.statusBar().showMessage("Starred" if on else "Unstarred", 3000)

    # ── Gmail-style navigation ─────────────────────────────────────────────
    def _back_to_list(self) -> None:
        if self.stage.currentIndex() == 1:
            self._reader_enabled(False)
            self.list.setFocus()

    def _current_row(self) -> int:
        if self.current is None:
            return -1
        return next((i for i, s in enumerate(self.rows) if s.uid == self.current.uid), -1)

    def _step(self, delta: int) -> None:
        """Newer (-1) / older (+1) mail while one is open."""
        if self.stage.currentIndex() != 1:
            return
        row = self._current_row() + delta
        if 0 <= row < len(self.rows):
            self.list.setCurrentIndex(self.model.index(row, 0))
            self._open(row)

    def _update_position(self) -> None:
        row, total = self._current_row(), (len(self.uids) or len(self.rows))
        self.position.setText(f"{row + 1:,} of {total:,}" if row >= 0 else "")
        self.newer.setEnabled(row > 0)
        self.older.setEnabled(0 <= row < len(self.rows) - 1)

    def _toggle_menu(self) -> None:
        self._set_compact(not self.folder_delegate.compact)

    def _set_compact(self, on: bool, save: bool = True) -> None:
        """☰ collapses the menu to icons (unread shows as a dot) and expands it again."""
        self.folder_delegate.compact = on
        self.left.setFixedWidth(84 if on else 256)
        self.compose_btn.setText("" if on else "  Compose")
        self.compose_btn.setProperty("compact", on)
        self.compose_btn.setFixedSize(QSize(56, 56) if on else QSize(16777215, 16777215))
        if not on:
            self.compose_btn.setMinimumSize(0, 0)
        self.compose_btn.style().unpolish(self.compose_btn)
        self.compose_btn.style().polish(self.compose_btn)
        self.folders.doItemsLayout()
        self.folders.viewport().update()
        if save:
            settings = look.load_settings()
            settings["menu_collapsed"] = on
            look.save_settings(settings)

    # ── account menu ───────────────────────────────────────────────────────
    def _account_menu(self) -> None:
        """The avatar toggles the account card: open it, or close it when it is already open."""
        if self._popup is not None:
            self._popup.close()
            return
        if time.monotonic() - self._popup_closed < 0.3:     # this very click just closed the card
            return
        self._popup = AccountPopup(self)
        self._popup.show_under(self.me)

    def _change_photo(self) -> None:
        if not self.address:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose a profile photo", str(Path.home() / "Pictures"),
                                              "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Profile photo", "That file isn't a picture JUDO Mail can read.")
            return
        side = min(img.width(), img.height())
        img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side).scaled(
            256, 256, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        dest = look.photo_path(self.address)
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(dest), "PNG")
        self.me.set(look.friendly_name(self.address), self.address, dest)
        self.statusBar().showMessage("Profile photo updated (kept on this PC)", 5000)

    def _remove_photo(self) -> None:
        look.photo_path(self.address).unlink(missing_ok=True)
        self.me.set(look.friendly_name(self.address), self.address, None)

    def _add_account(self) -> None:
        if AccountDialog(self).exec():
            self.reopen(new_worker=True)

    def _switch(self, address: str) -> None:
        try:
            mbx.switch_account(address)
        except Exception as e:
            self._failed(e)
            return
        self.reopen(new_worker=True)

    def _sign_out(self) -> None:
        if QMessageBox.question(self, "Sign out", f"Sign out of {self.address} in JUDO Mail?\n\n"
                                "Its App Password is removed from this PC. Your Gmail is not affected.") \
                != QMessageBox.StandardButton.Yes:
            return
        if mbx.remove_account(self.address) or AccountDialog(self, first=True).exec():
            self.reopen(new_worker=True)
        else:
            self.close()

    def _set_theme(self, key: str) -> None:
        s = look.load_settings()
        s["theme"] = key
        look.save_settings(s)
        self.reopen(new_worker=False)

    def reopen(self, new_worker: bool) -> None:
        """A fresh window — for a new theme (same connection) or another account (new connection)."""
        w = MailWindow(None if new_worker else self.worker)
        w.setGeometry(self.geometry())
        w.showMaximized() if self.isMaximized() else w.show()
        _windows.append(w)
        self._keep_worker = not new_worker
        self.close()

    def _failed(self, result) -> bool:
        if isinstance(result, Exception):
            self._loading = False
            self.statusBar().showMessage(f"Gmail error: {result}", 10000)
            return True
        return False

    def closeEvent(self, e) -> None:
        self._closed = True
        if self.watcher:
            self.watcher.stop()
        if not self._keep_worker:
            self.worker.shutdown()
        if self in _windows:
            _windows.remove(self)
        self.view.stop()
        _retired.append(self)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        super().closeEvent(e)


def main() -> int:
    if sys.platform == "win32":
        import ctypes   # its own taskbar identity and icon instead of Python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Ritesh.JUDO.Mail")
    # web views in more than one window (a theme or account change opens a new one) need shared
    # GL contexts — without this the second window's mail view crashes
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("JUDO Mail")
    import traceback
    sys.excepthook = lambda *exc: traceback.print_exception(*exc)   # log it; keep the window running
    app.setStyle("Fusion")
    from judo_browser.context_menu import TextMenus
    TextMenus.install(app)       # select and copy text anywhere with the mouse
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(look.app_icon())
    if not mbx.accounts() and not AccountDialog(first=True).exec():
        return 0
    w = MailWindow()
    _windows.append(w)
    w.show()
    return app.exec()
