"""
jobs/window.py — JUDO Jobs: the Job Agent's screen.

    Profile        the parsed resume summary · Upload resume · Looks Correct · Edit · Upload different
    Jobs           preferences · Search / Fresh jobs · results with match class · details & reasoning ·
                   Tailor resume · Apply
    Application    the APPLICATION REVIEW · questions needing the user · Submit…
    Applications   the tracker

Submitting goes through core/confirm exactly as it does on JUDO's HUD: in this
window the confirmation is a dialog with CONFIRM & SUBMIT / CANCEL, bound to
core/confirm, and only a click there resolves it. Long work (search, filling a
form) runs off the UI thread.

Run: python -m jobs
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QMainWindow, QMessageBox, QPushButton, QSplitter, QTableWidget,
                             QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from jobs import match as mt
from jobs import search as sr

ROOT = Path(__file__).resolve().parent.parent


def launch() -> str:
    """Open the window in its own process (called from JUDO's plugin thread)."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    subprocess.Popen([str(pyw if pyw.exists() else exe), "-m", "jobs"], cwd=str(ROOT), close_fds=True)
    return "Opening JUDO Jobs."


class _Bus(QObject):
    done = pyqtSignal(object, object)


class ReviewDialog(QDialog):
    """The on-screen confirmation for core/confirm in this window's process."""

    def __init__(self, parent, title: str, detail: str):
        super().__init__(parent, windowTitle="Confirm application")
        self.resize(640, 620)
        lay = QVBoxLayout(self)
        head = QLabel(title, objectName="Title", wordWrap=True)
        body = QTextEdit(readOnly=True)
        body.setPlainText(detail.replace("\n\n[CONFIRM & SUBMIT] [CANCEL]", ""))
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel", objectName="Pill")
        ok = QPushButton("CONFIRM && SUBMIT", objectName="Primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addWidget(head)
        lay.addWidget(body, 1)
        lay.addLayout(row)


class JobsWindow(QMainWindow):
    def __init__(self, service=None):
        super().__init__()
        from jobs.service import for_user
        from judo_mail import look
        self.svc = service or for_user(log=lambda s: None)
        self.setWindowTitle("JUDO Jobs")
        self.resize(1250, 820)
        self.t = look.palette()
        self.setStyleSheet(look.stylesheet(self.t))
        self.setWindowIcon(look.app_icon())
        self.bus = _Bus()
        self.bus.done.connect(lambda cb, r: cb(r))
        self._bind_confirm()

        tabs = QTabWidget()
        tabs.addTab(self._profile_tab(), "Profile")
        tabs.addTab(self._jobs_tab(), "Jobs")
        tabs.addTab(self._app_tab(), "Application")
        tabs.addTab(self._tracker_tab(), "Applications")
        self.tabs = tabs
        self.setCentralWidget(tabs)
        self.refresh_profile()
        self.refresh_jobs()
        self.refresh_tracker()

    # ── confirmation (core/confirm bound to a dialog in this process) ───────
    def _bind_confirm(self) -> None:
        from core import confirm
        self._confirm = confirm
        show = lambda title, detail: self.bus.done.emit(lambda _: self._ask(title, detail), None)
        confirm.bind(show, lambda: None, lambda msg: self.bus.done.emit(lambda m: self._log(m), msg))

    def _ask(self, title: str, detail: str) -> None:
        accepted = ReviewDialog(self, title, detail).exec() == QDialog.DialogCode.Accepted
        self._confirm.resolve(accepted)              # only this click submits
        self.status.setText("Submitting… checking the page for a confirmation." if accepted else "Not submitted.")

    def _log(self, msg: str) -> None:
        self.status.setText(msg.replace("SYS: ", "").replace("ERR: ", "")[:300])
        self.refresh_tracker()
        self.refresh_review()

    def _bg(self, fn, cb, busy: str = "Working…") -> None:
        self.status.setText(busy)

        def work():
            try:
                r = fn()
            except Exception as e:
                r = f"Error: {e}"
            self.bus.done.emit(cb, r)
        threading.Thread(target=work, daemon=True).start()

    # ── Profile ─────────────────────────────────────────────────────────────
    def _profile_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.profile_text = QTextEdit(readOnly=True)
        row = QHBoxLayout()
        for text, fn, name in (("Upload resume…", self.upload, "Primary"), ("Looks Correct", self.looks_correct, "Pill"),
                               ("Edit Profile", self.edit_profile, "Pill"), ("Upload Different Resume", self.upload, "Pill"),
                               ("Enter profile manually", self.manual, "Pill")):
            b = QPushButton(text, objectName=name)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self.status = QLabel("", objectName="Sub", wordWrap=True)
        v.addLayout(row)
        v.addWidget(self.profile_text, 1)
        v.addWidget(self.status)
        return w

    def refresh_profile(self) -> None:
        self.profile_text.setPlainText(self.svc.show_profile() + "\n\n" + self.svc.resumes())

    def upload(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Your resume", str(Path.home()), "Resume (*.pdf *.docx *.txt)")
        if path:
            self._bg(lambda: self.svc.upload_resume(path), lambda r: (self.profile_text.setPlainText(r),
                                                                     self.status.setText("Check the summary above.")),
                     "Reading your resume…")

    def looks_correct(self) -> None:
        r = self.svc.confirm_profile()
        self.status.setText(r)
        self.refresh_profile()
        self.refresh_jobs()

    def _form(self, title: str, fields: list[tuple[str, str]]) -> dict | None:
        d = QDialog(self, windowTitle=title)
        f = QFormLayout(d)
        edits = {}
        for key, label in fields:
            edits[key] = QLineEdit()
            f.addRow(label, edits[key])
        ok = QPushButton("Save", objectName="Primary")
        ok.clicked.connect(d.accept)
        f.addRow(ok)
        if d.exec() != QDialog.DialogCode.Accepted:
            return None
        return {k: e.text().strip() for k, e in edits.items() if e.text().strip()}

    def edit_profile(self) -> None:
        ch = self._form("Edit profile", [("name", "Name"), ("email", "Email"), ("phone", "Phone"), ("location", "Location"),
                                         ("add_skills", "Add skills (comma)"), ("remove_skills", "Remove skills (comma)"),
                                         ("experience_years", "Full-time experience (years)"),
                                         ("roles", "Roles you're targeting (comma)"),
                                         ("work_authorization", "Work authorisation (your words)")])
        if ch:
            self.profile_text.setPlainText(self.svc.edit_profile(ch))

    def manual(self) -> None:
        ch = self._form("Your profile", [("name", "Name"), ("email", "Email"), ("phone", "Phone"), ("location", "Location"),
                                         ("skills", "Skills (comma)"), ("experience_years", "Full-time experience (years)"),
                                         ("roles", "Roles you're targeting (comma)")])
        if ch:
            self.profile_text.setPlainText(self.svc.create_manual_profile(ch))

    # ── Jobs ────────────────────────────────────────────────────────────────
    def _jobs_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        prefs = QHBoxLayout()
        self.roles = QLineEdit(placeholderText="Roles (e.g. Software Engineer, Backend Developer)")
        self.places = QLineEdit(placeholderText="Locations (e.g. Bengaluru, India, Remote)")
        self.exp = QLineEdit(placeholderText="Experience (e.g. 0-2)")
        self.exp.setMaximumWidth(150)
        save = QPushButton("Save preferences", objectName="Pill")
        save.clicked.connect(self.save_prefs)
        for x in (self.roles, self.places, self.exp, save):
            prefs.addWidget(x)
        actions = QHBoxLayout()
        for text, fn, name in (("Search jobs", lambda: self.search(None), "Primary"),
                               ("Fresh (last 3 days)", lambda: self.search(3), "Pill"),
                               ("Show all", lambda: self.refresh_jobs(True), "Pill"),
                               ("Tailor resume", self.tailor, "Pill"), ("Apply", self.apply, "Primary")):
            b = QPushButton(text, objectName=name)
            b.clicked.connect(fn)
            actions.addWidget(b)
        actions.addStretch(1)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["#", "Company", "Role", "Location", "Posted", "Match", "Source"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.details = QTextEdit(readOnly=True)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.details)
        split.setSizes([420, 300])
        self.jobs_note = QLabel("", objectName="Sub", wordWrap=True)
        v.addLayout(prefs)
        v.addLayout(actions)
        v.addWidget(self.jobs_note)
        v.addWidget(split, 1)
        return w

    def save_prefs(self) -> None:
        self.jobs_note.setText(self.svc.set_preferences({"roles": self.roles.text(), "locations": self.places.text(),
                                                         "experience_range": self.exp.text()}))

    def search(self, days) -> None:
        if (g := self.svc.gate()):
            self.tabs.setCurrentIndex(0)
            self.profile_text.setPlainText(g)
            return
        self._bg(lambda: self.svc.find_jobs(days), lambda r: (self.jobs_note.setText(r.split("\n")[0]), self.refresh_jobs()),
                 "Searching live job sources and checking each posting…")

    def refresh_jobs(self, show_all: bool = False) -> None:
        jobs = self.svc._jobs()
        rows = [(i, j) for i, j in enumerate(jobs, 1)
                if show_all or (j["match"]["classification"] != mt.NOT and j["status"] != "expired")]
        self.table.setRowCount(len(rows))
        for r, (i, j) in enumerate(rows):
            m = j["match"]
            for c, val in enumerate([str(i), j.get("company") or "?", j.get("title") or "?", j.get("location") or "—",
                                     sr.freshness(j), f"{m['classification']} {m['score']}",
                                     j["source"] + ("" if j["verified"] else " (unverified)")]):
                self.table.setItem(r, c, QTableWidgetItem(val))
        prefs, defaulted = self.svc.profiles.effective_preferences() if self.svc.profiles.profile() else ({}, [])
        if prefs and not self.roles.text():
            self.roles.setText(", ".join(prefs.get("roles") or []))
            self.places.setText(", ".join(prefs.get("locations") or []))
            lo, hi = (prefs.get("experience_range") or [0, 2])[:2]
            self.exp.setText(f"{lo:g}-{hi:g}")

    def _selected(self) -> str | None:
        r = self.table.currentRow()
        return self.table.item(r, 0).text() if r >= 0 and self.table.item(r, 0) else None

    def show_details(self) -> None:
        n = self._selected()
        if n:
            self.details.setPlainText(self.svc.details(n))

    def tailor(self) -> None:
        n = self._selected()
        if not n:
            return
        r = self.svc.tailor(n)
        self.details.setPlainText(r)
        if "Say \"use the tailored resume\"" in r and QMessageBox.question(
                self, "Tailored resume", "Use this tailored resume for this application?") == QMessageBox.StandardButton.Yes:
            self.jobs_note.setText(self.svc.approve_tailored(n))

    def apply(self) -> None:
        n = self._selected()
        if not n:
            return
        self.current = n
        self.tabs.setCurrentIndex(2)
        self._bg(lambda: self.svc.apply(n), lambda r: self.review.setPlainText(r),
                 "Opening the application in JUDO Browser and filling it…")

    # ── Application ─────────────────────────────────────────────────────────
    def _app_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.review = QTextEdit(readOnly=True)
        row = QHBoxLayout()
        self.q_no = QLineEdit(placeholderText="Question #")
        self.q_no.setMaximumWidth(110)
        self.q_ans = QLineEdit(placeholderText="Your answer (or 'skip' for an optional question)")
        ans = QPushButton("Answer", objectName="Pill")
        ans.clicked.connect(self.answer)
        cont = QPushButton("Continue (after CAPTCHA / sign-in)", objectName="Pill")
        cont.clicked.connect(lambda: self._bg(lambda: self.svc.continue_application(getattr(self, "current", None)),
                                              lambda r: self.review.setPlainText(r)))
        submit = QPushButton("Submit…", objectName="Primary")
        submit.clicked.connect(self.submit)
        for x in (self.q_no, self.q_ans, ans, cont, submit):
            row.addWidget(x)
        v.addWidget(self.review, 1)
        v.addLayout(row)
        return w

    def refresh_review(self) -> None:
        if getattr(self, "current", None):
            self.review.setPlainText(self.svc.review(self.current))

    def answer(self) -> None:
        if not self.q_no.text().strip():
            return
        q, a = self.q_no.text().strip(), self.q_ans.text()
        self._bg(lambda: self.svc.answer(q, a, getattr(self, "current", None)), lambda r: self.review.setPlainText(r))
        self.q_ans.clear()

    def submit(self) -> None:
        r = self.svc.submit(getattr(self, "current", None))
        if "[CONFIRMATION_PENDING]" not in r:
            self.review.setPlainText(r)

    # ── Applications ────────────────────────────────────────────────────────
    def _tracker_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.tracker = QTableWidget(0, 6)
        self.tracker.setHorizontalHeaderLabels(["Company", "Role", "Status", "Applied", "Evidence", "Link"])
        self.tracker.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tracker.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        v.addWidget(self.tracker)
        return w

    def refresh_tracker(self) -> None:
        rows = self.svc.tracker.all()
        self.tracker.setRowCount(len(rows))
        for r, rec in enumerate(rows):
            for c, val in enumerate([rec.get("company", ""), rec.get("role", ""), rec.get("status", ""),
                                     rec.get("application_date", ""), rec.get("verification_evidence", ""),
                                     rec.get("application_url") or rec.get("job_url", "")]):
                self.tracker.setItem(r, c, QTableWidgetItem(str(val)))


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    try:
        from judo_browser.context_menu import TextMenus
        TextMenus.install(app)
    except Exception:
        pass
    w = JobsWindow()
    w.show()
    return app.exec()
