"""Right-click menus that behave like a normal desktop app, for every JUDO window.

web_menu()  — the Chrome-style menu for a web page: what it offers depends on where you
              clicked (a link, an image, a video, a text box, selected text, or the page).
TextMenus   — an application-wide helper for ordinary Qt widgets: text in labels can be
              selected with the mouse and copied, and labels get a "Copy text" menu.
              Text boxes keep Qt's own Undo / Redo / Cut / Copy / Paste / Delete / Select all.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QEvent, QObject, Qt, QUrl
from PyQt6.QtWidgets import QAbstractButton, QApplication, QLabel, QMenu, QWidget


def _copy(text: str) -> None:
    QApplication.clipboard().setText(text)


def web_menu(view, *, open_tab: Callable[[QUrl], None] | None = None,
             open_window: Callable[[QUrl], None] | None = None,
             open_incognito: Callable[[QUrl], None] | None = None,
             search: tuple[str, Callable[[str], None]] | None = None,
             navigation: bool = True, downloads: bool = True,
             save_page: Callable | None = None, print_page: Callable | None = None,
             view_source: Callable | None = None, inspect: Callable | None = None) -> QMenu | None:
    """Build the menu for the spot the user right-clicked in `view` (a QWebEngineView).
    Each hook that is None leaves its entries out (JUDO Mail has no tabs or back button)."""
    # imported here: JUDO's main window uses TextMenus without loading the web engine
    from PyQt6.QtWebEngineCore import QWebEngineContextMenuRequest as Request, QWebEnginePage
    A, Edit, Media = QWebEnginePage.WebAction, Request.EditFlag, Request.MediaFlag
    req = view.lastContextMenuRequest()
    if req is None:
        return None
    page = view.page()
    m = QMenu(view)
    m.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

    def act(text: str, fn, enabled: bool = True, keys: str = ""):
        a = m.addAction(text + (f"\t{keys}" if keys else ""))
        if fn is not None:
            a.triggered.connect(lambda _=False: fn())
        a.setEnabled(enabled and fn is not None)
        return a

    def web(action):
        return lambda: page.triggerAction(action)

    flags, editable = req.editFlags(), req.isContentEditable()
    link, media, kind = req.linkUrl(), req.mediaUrl(), req.mediaType()
    selected = req.selectedText().strip()

    # spelling suggestions first, like Chrome
    if editable and req.misspelledWord():
        suggestions = req.spellCheckerSuggestions()[:5]
        for word in suggestions:
            act(word, lambda w=word: page.replaceMisspelledWord(w))
        if not suggestions:
            act("No spelling suggestions", None)
        m.addSeparator()

    if link.isValid() and not link.isEmpty():
        if open_tab:
            act("Open link in new tab", lambda: open_tab(link))
        if open_window:
            act("Open link in new window", lambda: open_window(link))
        if open_incognito:
            act("Open link in Incognito window", lambda: open_incognito(link))
        m.addSeparator()
        if downloads:
            act("Save link as…", web(A.DownloadLinkToDisk))
        act("Copy link address", lambda: _copy(link.toString()))
        if req.linkText().strip() and not selected:
            act("Copy link text", lambda: _copy(req.linkText().strip()))
        m.addSeparator()

    if kind == Request.MediaType.MediaTypeImage and media.isValid():
        if open_tab:
            act("Open image in new tab", lambda: open_tab(media))
        if downloads:
            act("Save image as…", web(A.DownloadImageToDisk))
        act("Copy image", web(A.CopyImageToClipboard))
        act("Copy image address", web(A.CopyImageUrlToClipboard))
        m.addSeparator()
    elif kind in (Request.MediaType.MediaTypeVideo, Request.MediaType.MediaTypeAudio):
        state = req.mediaFlags()
        name = "video" if kind == Request.MediaType.MediaTypeVideo else "audio"
        act("Play" if Media.MediaPaused in state else "Pause", web(A.ToggleMediaPlayPause))
        act("Unmute" if Media.MediaMuted in state else "Mute", web(A.ToggleMediaMute))
        loop = act("Loop", web(A.ToggleMediaLoop))
        loop.setCheckable(True)
        loop.setChecked(Media.MediaLoop in state)
        if Media.MediaCanToggleControls in state:
            shown = act("Show controls", web(A.ToggleMediaControls))
            shown.setCheckable(True)
            shown.setChecked(Media.MediaControls in state)
        m.addSeparator()
        if open_tab and media.isValid():
            act(f"Open {name} in new tab", lambda: open_tab(media))
        if downloads and Media.MediaCanSave in state:
            act(f"Save {name} as…", web(A.DownloadMediaToDisk))
        act(f"Copy {name} address", web(A.CopyMediaUrlToClipboard))
        m.addSeparator()

    if editable:
        act("Undo", web(A.Undo), Edit.CanUndo in flags, "Ctrl+Z")
        act("Redo", web(A.Redo), Edit.CanRedo in flags, "Ctrl+Y")
        m.addSeparator()
        act("Cut", web(A.Cut), Edit.CanCut in flags, "Ctrl+X")
        act("Copy", web(A.Copy), Edit.CanCopy in flags, "Ctrl+C")
        act("Paste", web(A.Paste), Edit.CanPaste in flags, "Ctrl+V")
        act("Paste as plain text", web(A.PasteAndMatchStyle), Edit.CanPaste in flags, "Ctrl+Shift+V")
        act("Delete", lambda: page.runJavaScript("document.execCommand('delete')"), Edit.CanDelete in flags)
        m.addSeparator()
        act("Select all", web(A.SelectAll), Edit.CanSelectAll in flags, "Ctrl+A")
        m.addSeparator()
    elif selected:
        act("Copy", web(A.Copy), True, "Ctrl+C")
        if search:
            short = selected if len(selected) <= 30 else selected[:30] + "…"
            act(f"Search {search[0]} for “{short}”", lambda: search[1](selected))
        m.addSeparator()
    elif not (link.isValid() and not link.isEmpty()) and kind == Request.MediaType.MediaTypeNone:
        if navigation:
            history = page.history()
            act("Back", web(A.Back), history.canGoBack(), "Alt+Left")
            act("Forward", web(A.Forward), history.canGoForward(), "Alt+Right")
            act("Reload", web(A.Reload), True, "Ctrl+R")
            m.addSeparator()
        if save_page:
            act("Save as…", save_page, True, "Ctrl+S")
        if print_page:
            act("Print…", print_page, True, "Ctrl+P")
        m.addSeparator()
        act("Select all", web(A.SelectAll), True, "Ctrl+A")
        if view_source:
            act("View page source", view_source, True, "Ctrl+U")

    if inspect:
        m.addSeparator()
        act("Inspect", inspect, True, "Ctrl+Shift+I")
    return m


# ── ordinary Qt widgets ─────────────────────────────────────────────────────

def _handles_clicks(w: QWidget | None) -> bool:
    """True when the label sits in something that reacts to clicks itself (a button, a
    clickable card) — selecting text there would swallow those clicks."""
    while w is not None:
        if isinstance(w, QAbstractButton):
            return True
        for cls in type(w).__mro__:
            if cls.__module__.startswith("PyQt6"):
                break
            if "mousePressEvent" in cls.__dict__ or "mouseReleaseEvent" in cls.__dict__:
                return True
        w = w.parentWidget()
    return False


class TextMenus(QObject):
    """Install once per app: `TextMenus.install(app)`."""

    _installed: "TextMenus | None" = None

    @classmethod
    def install(cls, app: QApplication) -> "TextMenus":
        if cls._installed is None:
            cls._installed = cls(app)
            app.installEventFilter(cls._installed)
        return cls._installed

    @staticmethod
    def make_selectable(label: QLabel) -> None:
        flags = label.textInteractionFlags()
        if (Qt.TextInteractionFlag.TextSelectableByMouse in flags or not label.text().strip()
                or not label.wordWrap() or label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                or _handles_clicks(label)):
            return
        label.setTextInteractionFlags(flags | Qt.TextInteractionFlag.TextSelectableByMouse
                                      | Qt.TextInteractionFlag.LinksAccessibleByMouse)

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.Polish and isinstance(obj, QLabel):
            self.make_selectable(obj)
        elif (t == QEvent.Type.ContextMenu and isinstance(obj, QLabel)
              and obj.contextMenuPolicy() == Qt.ContextMenuPolicy.DefaultContextMenu
              and Qt.TextInteractionFlag.TextSelectableByMouse not in obj.textInteractionFlags()
              and obj.text().strip()):
            from PyQt6.QtGui import QTextDocument
            doc = QTextDocument()
            doc.setHtml(obj.text())
            text = doc.toPlainText().strip()
            m = QMenu(obj)
            m.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            m.addAction("Copy text", lambda: _copy(text))
            m.popup(e.globalPos())
            return True
        return False
