"""The JUDO log panel keeps typing new lines without taking away the user's selection,
so text in it can be selected and copied at any time."""
import pytest


def test_new_log_lines_keep_the_users_selection():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtGui import QTextCursor
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import ui
    log = ui.LogWidget()
    log._write("JUDO: first line to copy\n")
    c = log.textCursor()
    c.setPosition(6)
    c.setPosition(16, QTextCursor.MoveMode.KeepAnchor)
    log.setTextCursor(c)
    log._text, log._pos = "SYS: more", 0
    for _ in range(len("SYS: more") + 1):
        log._step()
    assert log.textCursor().selectedText() == "first line"
    log.copy()
    assert app.clipboard().text() == "first line"
    assert log.toPlainText().startswith("JUDO: first line to copy\nSYS: more")
