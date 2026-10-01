"""
judo_browser/omnibox.py — Chrome's address bar: type a URL or a search, get
suggestions from history, bookmarks and the search engine as you type.
"""
from __future__ import annotations

import json

from PyQt6.QtCore import QPoint, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem

SUGGEST_URL = "https://suggestqueries.google.com/complete/search?client=chrome&q="
MAX_ROWS = 8


class Omnibox(QLineEdit):
    navigate = pyqtSignal(str)   # text the user committed (URL or search words)

    def __init__(self, browser, parent=None):
        super().__init__(parent)
        self.setObjectName("Omnibox")
        self.browser = browser
        self.setPlaceholderText("Search Google or type a URL")
        self.popup = QListWidget(parent.window() if parent else None)
        self.popup.setObjectName("Suggestions")
        self.popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.popup.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.popup.itemClicked.connect(lambda it: self._commit(it.data(Qt.ItemDataRole.UserRole)))
        self.popup.hide()
        self._net = QNetworkAccessManager(self)
        self._debounce = QTimer(self, singleShot=True, interval=120)
        self._debounce.timeout.connect(self._fetch_remote)
        self.textEdited.connect(self._typed)
        self.returnPressed.connect(lambda: self._commit(self._chosen() or self.text()))
        self._remote: list[str] = []
        self._select_on_click = False

    # focus: Chrome selects the whole address on the first click
    def focusInEvent(self, e):
        super().focusInEvent(e)
        self._select_on_click = True
        QTimer.singleShot(0, self.selectAll)

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        if self._select_on_click:
            self.selectAll()
            self._select_on_click = False

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        QTimer.singleShot(150, self.popup.hide)   # let a click on a suggestion land first

    def keyPressEvent(self, e):
        k = e.key()
        if self.popup.isVisible() and k in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            row = self.popup.currentRow() + (1 if k == Qt.Key.Key_Down else -1)
            row = max(-1, min(self.popup.count() - 1, row))
            self.popup.setCurrentRow(row)
            if row >= 0:
                self.setText(self.popup.item(row).data(Qt.ItemDataRole.UserRole))
            return
        if k == Qt.Key.Key_Escape:
            self.popup.hide()
            self.clearFocus()
            self.window().view().setFocus()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            t = self.text().strip()   # Chrome: Ctrl+Enter wraps a word in www. … .com
            if t and " " not in t and "." not in t:
                return self._commit(f"www.{t}.com")
        super().keyPressEvent(e)

    # suggestions
    def _typed(self, text: str) -> None:
        self._remote = []
        self._show()
        if text.strip():
            self._debounce.start()

    def _local(self, text: str) -> list[tuple[str, str, str]]:
        q = text.lower().strip()
        if not q:
            return []
        seen, out = set(), []
        pools = [("★", b["title"], b["url"]) for b in self.browser.bookmarks] + \
                [("🕘", h["title"], h["url"]) for h in reversed(self.browser.history)]
        for mark, title, url in pools:
            if url in seen or not (q in url.lower() or q in title.lower()):
                continue
            seen.add(url)
            out.append((mark, title, url))
            if len(out) >= 4:
                break
        return out

    def _fetch_remote(self) -> None:
        text = self.text().strip()
        if not text or self.window().incognito:   # incognito keystrokes never leave the machine
            return
        reply = self._net.get(QNetworkRequest(QUrl(SUGGEST_URL + QUrl.toPercentEncoding(text).data().decode())))
        reply.finished.connect(lambda r=reply, t=text: self._got_remote(r, t))

    def _got_remote(self, reply, text: str) -> None:
        try:
            if text == self.text().strip():
                self._remote = json.loads(bytes(reply.readAll()).decode("utf-8", "replace"))[1][:5]
                self._show()
        except Exception:
            pass
        reply.deleteLater()

    def _show(self) -> None:
        text = self.text().strip()
        self.popup.clear()
        if not text or not self.hasFocus():
            return self.popup.hide()
        rows = [("🔍", text, text)] + self._local(text) + [("🔍", s, s) for s in self._remote if s != text]
        for mark, label, value in rows[:MAX_ROWS]:
            shown = f"{mark}   {label}" + (f"   —   {value}" if value != label and value.startswith("http") else "")
            it = QListWidgetItem(shown)
            it.setData(Qt.ItemDataRole.UserRole, value)
            self.popup.addItem(it)
        self.popup.setCurrentRow(-1)
        top_left = self.mapTo(self.window(), QPoint(0, self.height() + 4))
        row_h = self.popup.sizeHintForRow(0) if self.popup.count() else 30
        self.popup.setGeometry(top_left.x(), top_left.y(), self.width(), row_h * self.popup.count() + 14)
        self.popup.raise_()
        self.popup.show()

    def _chosen(self) -> str | None:
        it = self.popup.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it and self.popup.isVisible() and self.popup.currentRow() >= 0 else None

    def _commit(self, text: str) -> None:
        self.popup.hide()
        if text and text.strip():
            self.navigate.emit(text.strip())

    def show_url(self, url: QUrl) -> None:
        """Chrome shows the address without https:// and the new-tab page as empty."""
        if self.hasFocus():
            return
        s = url.toString()
        if s in ("", "about:blank"):
            self.clear()
        elif url.scheme() == "https":
            self.setText(s[len("https://"):].removeprefix("www.").rstrip("/") if url.path() in ("", "/") and not url.query()
                         else s[len("https://"):])
        else:
            self.setText(s)
        self.setCursorPosition(0)
