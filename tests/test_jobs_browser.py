"""JUDO Browser's job-application actions (judo_browser/control.py) against real local
forms in a real Qt WebEngine page — fully offline, profile in a temp folder — and one
whole ApplicationSession run through them up to a VERIFIED submit after CONFIRM."""
import http.server
import os
import threading
from functools import partial
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWebEngineWidgets")

GREENHOUSE = """<!doctype html><html><head><meta charset="utf-8"><title>Backend Engineer - Acme Corp</title></head>
<body><h1>Backend Engineer</h1><p>Acme Corp</p><form id="f">
 <label for="fn">First Name *</label><input id="fn" name="first_name" required>
 <label for="ln">Last Name *</label><input id="ln" name="last_name" required>
 <label for="em">Email *</label><input id="em" name="email" type="email" required>
 <label for="cv">Resume/CV *</label><input id="cv" name="resume" type="file" required>
 <label for="np">What is your notice period? *</label><input id="np" name="q1" required>
 <label for="vs">Will you require visa sponsorship? *</label>
 <select id="vs" name="q2" required><option value="">Select...</option><option value="1">Yes</option><option value="0">No</option></select>
 <fieldset><legend>Gender</legend><label><input type="radio" name="gender" value="m"> Male</label>
   <label><input type="radio" name="gender" value="f"> Female</label></fieldset>
 <label><input type="checkbox" id="ok" name="consent"> I agree to the privacy policy</label>
 <label for="tx">Additional information</label><textarea id="tx" name="extra"></textarea>
 <button type="submit">Submit Application</button></form>
<script>window.events = [];
for (const e of document.querySelectorAll('input,select,textarea')) e.addEventListener('change', ev => events.push(ev.target.name));
document.getElementById('f').addEventListener('submit', ev => { ev.preventDefault();
  if (!document.getElementById('cv').files.length || !document.getElementById('vs').value) return;
  document.body.innerHTML = '<h1>Thank you for applying! Your application has been submitted.</h1>'; });
</script></body></html>"""

LEVER = """<!doctype html><html><head><meta charset="utf-8"><title>Lever</title></head><body>
<div class="application-label">Resume/CV *</div>
<a class="visible-resume-upload" role="button" style="display:inline-block;padding:8px"
   onclick="document.getElementById('rf').click()">Attach resume</a>
<input type="file" id="rf" name="resume" style="display:none">
<label for="cv2" style="display:inline-block;padding:8px">Upload CV</label>
<input type="file" id="cv2" name="cv2" style="position:absolute;left:-9999px"></body></html>"""

WALLS = {"captcha.html": '<title>Apply</title><input name="n"><div class="g-recaptcha" data-sitekey="k"></div>',
         "login.html": '<title>Sign in</title><input name="u"><input type="password" name="p"><button>Sign in</button>'}

_APP = None
_BROWSER = None


def _app():
    """One QApplication per test run (Qt WebEngine can't start twice in a process),
    made like tests/test_judo_mail.py's: shared GL contexts, offscreen."""
    from PyQt6.QtCore import Qt
    from PyQt6 import QtWebEngineWidgets  # noqa: F401 — must be loaded before the QApplication exists
    from PyQt6.QtWidgets import QApplication
    global _APP
    if QApplication.instance() is None:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        _APP = QApplication(["x", "-platform", "offscreen"])
    return QApplication.instance()


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    """A JUDO Browser whose whole profile (settings, cookies, control.json) is a temp dir."""
    from judo_browser import accounts, app_data, control, newtab
    from judo_browser import app as ba
    data = tmp_path_factory.mktemp("judo_browser")
    with pytest.MonkeyPatch.context() as mp:
        for mod in (app_data, ba, accounts):
            mp.setattr(mod, "DATA", data)
        mp.setattr(newtab, "BG_FILE", data / "ntp_background.jpg")
        mp.setattr(control, "CONTROL_FILE", data / "control.json")
        if not (ba.load_settings()["sandbox"] and ba.sandbox_ready()):
            os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
        app = _app()
        global _BROWSER
        if _BROWSER is None:
            _BROWSER = ba.Browser(app)
            app.aboutToQuit.disconnect(_BROWSER.save_session)     # never writes outside the temp profile
            _BROWSER.start(None)
        yield _BROWSER
        _BROWSER._session_timer.stop()


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    d = tmp_path_factory.mktemp("pages")
    for name, html in {"greenhouse.html": GREENHOUSE, "lever.html": LEVER, **WALLS}.items():
        (d / name).write_text(html, encoding="utf-8")
    (d / "resume.txt").write_text("Jane Doe\nPython, SQL\n", encoding="utf-8")
    return d


def _call(b, action, params=None, timeout=20000):
    from PyQt6.QtCore import QEventLoop, QTimer
    from judo_browser.control import Commands
    box, loop = [], QEventLoop()
    Commands(b).dispatch(action, params or {}, lambda r: (box.append(r), loop.quit()))
    if not box:
        QTimer.singleShot(timeout, loop.quit)
        loop.exec()
    assert box, f"{action} never answered"
    return box[0]


def _js(b, script):
    from PyQt6.QtCore import QEventLoop
    box, loop = [], QEventLoop()
    b.windows[-1].view().page().runJavaScript(script, 0, lambda r: (box.append(r), loop.quit()))
    if not box:
        loop.exec()
    return box[0]


def _open(b, path: Path):
    from PyQt6.QtCore import QUrl
    assert _call(b, "go_to", {"url": QUrl.fromLocalFile(str(path)).toString()}).startswith("Opened")


def test_read_set_upload_submit_on_a_greenhouse_style_form(browser, pages):
    _open(browser, pages / "greenhouse.html")
    st = _call(browser, "page_state")
    assert st["captcha"] is False and st["password_fields"] == 0 and "Submit Application" in st["buttons"]
    form = _call(browser, "read_form")
    by = {f["label"]: f for f in form}
    assert by["First Name *"]["required"] and by["Resume/CV *"]["type"] == "file"
    assert by["Will you require visa sponsorship? *"]["options"] == ["Yes", "No"]      # no "Select..." placeholder
    assert by["Gender"]["type"] == "radio" and by["Gender"]["options"] == ["Male", "Female"]
    assert by["I agree to the privacy policy"]["type"] == "checkbox"
    sel = {k: f["selector"] for k, f in by.items()}
    for label, value in [("First Name *", "Jane"), ("Will you require visa sponsorship? *", "No"), ("Gender", "Female"),
                         ("I agree to the privacy policy", "yes"), ("Additional information", "Two\nlines")]:
        assert _call(browser, "set_field", {"selector": sel[label], "value": value}) == "ok", label
    assert _call(browser, "set_field", {"selector": sel["Will you require visa sponsorship? *"], "value": "Maybe"}) == "no-option"
    assert _call(browser, "set_field", {"selector": "#nope", "value": "x"}) == "missing"
    assert _js(browser, "events") == ["first_name", "q2", "gender", "consent", "extra"]      # site listeners saw it
    resume = str(pages / "resume.txt")
    assert _call(browser, "upload_file", {"selector": sel["Resume/CV *"], "path": resume + ".gone"}) == "missing-file"
    assert _call(browser, "upload_file", {"selector": sel["Resume/CV *"], "path": resume}) == "attached:resume.txt"
    assert browser.windows[-1].view().page().armed_files is None                        # never left armed
    after = {f["label"]: f["value"] for f in _call(browser, "read_form")}
    assert after["Resume/CV *"] == "resume.txt" and after["Will you require visa sponsorship? *"] == "No"
    assert after["Gender"] == "Female" and after["I agree to the privacy policy"] == "yes"
    res = _call(browser, "submit_form", {"wait_ms": 300})        # required fields still empty: the browser refuses
    assert res["clicked"] == "Submit Application" and "Thank you" not in res["after"]["text"]
    assert any(x.startswith("Last Name *:") for x in res["after"]["invalid"])
    for label, value in [("Last Name *", "Doe"), ("Email *", "jane@example.com"), ("What is your notice period? *", "30 days")]:
        assert _call(browser, "set_field", {"selector": sel[label], "value": value}) == "ok", label
    res = _call(browser, "submit_form", {"wait_ms": 300})
    assert res["clicked"] == "Submit Application" and "Thank you for applying" in res["after"]["text"]


def test_browser_validation_bubble_counts_as_not_sent(tmp_path):
    from jobs.apply import ApplicationSession
    from jobs.store import UserStore
    from jobs.tracker import Tracker
    job = {"id": "j1", "title": "Dev", "company": "Acme", "url": "https://x.test/j/1"}
    tracker = Tracker(UserStore("smoketest", root=tmp_path))
    s = ApplicationSession(job, {}, "", tracker, browser=object())
    after = {"url": "https://x.test/j/1#form", "text": "Apply", "fields": 3, "invalid": ["Email *: Please fill out this field."]}
    assert "wasn't sent" in s._verify(after, before_url="https://x.test/j/1")
    assert s.state == "failed" and tracker.find(job)["status"] == "FAILED"


def test_upload_through_a_button_or_label_when_the_input_is_hidden(browser, pages):
    _open(browser, pages / "lever.html")
    resume = str(pages / "resume.txt")
    assert _call(browser, "upload_file", {"selector": "#rf", "path": resume}) == "attached:resume.txt"   # display:none
    assert _call(browser, "upload_file", {"selector": "#cv2", "path": resume}) == "attached:resume.txt"  # left:-9999px
    assert browser.windows[-1].view().page().armed_files is None


def test_page_state_flags_captcha_and_login_walls(browser, pages):
    _open(browser, pages / "captcha.html")
    assert _call(browser, "page_state")["captcha"] is True
    _open(browser, pages / "login.html")
    st = _call(browser, "page_state")
    assert st["password_fields"] == 1 and st["captcha"] is False


def test_go_to_another_page_on_an_open_site_navigates_it(browser, pages):
    """A second job on the same ATS host must open that job, not just switch to the old tab."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(pages)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/"
        assert _call(browser, "go_to", {"url": base + "captcha.html"}).startswith("Opened")
        assert _call(browser, "go_to", {"url": base + "login.html"}).startswith("Opened Sign in")
        assert _call(browser, "go_to", {"url": base + "login.html"}).startswith("Switched")
    finally:
        server.shutdown()


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def test_application_session_submits_only_after_confirm_and_verifies(browser, pages, tmp_path):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtWidgets import QApplication
    from jobs.apply import ApplicationSession
    from jobs.store import UserStore
    from jobs.tracker import Tracker

    class Port:   # the real control path (queued onto the Qt thread), minus HTTP
        calls = []

        def call(self, action, params=None):
            self.calls.append(action)
            params = dict(params or {}, **({"wait_ms": 300} if action == "submit_form" else {}))
            return browser.control.call(action, params, timeout=60)

    class Confirm:
        req = None

        def request(self, key, title, detail, run, on_cancel=None):
            Confirm.req = (detail, run)
            return "[CONFIRMATION_PENDING]"

    url = QUrl.fromLocalFile(str(pages / "greenhouse.html")).toString()
    job = {"id": "acme-1", "title": "Backend Engineer", "company": "Acme Corp", "url": url, "apply_url": url}
    profile = {"candidate": {"name": "Jane Doe", "email": "jane@example.com", "phone": "+91 98765 43210"}, "links": []}
    tracker = Tracker(UserStore("smoketest", root=tmp_path))
    port = Port()
    s = ApplicationSession(job, profile, str(pages / "resume.txt"), tracker, browser=port, confirm_module=Confirm())
    out = {}

    def run():
        try:
            out["prepare"] = s.prepare()
            out["state1"] = s.state
            s.answer("notice period", "30 days")
            s.answer("visa", "no")
            out["request"] = s.request_submission()
            out["before"] = (list(port.calls), tracker.find(job)["status"])
            out["confirm"] = Confirm.req[1]()            # the human pressed CONFIRM
        except Exception as e:                          # surfaced by the asserts below
            out["error"] = repr(e)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    while t.is_alive():
        QApplication.processEvents()
        t.join(0.01)
    assert "error" not in out, out
    assert out["state1"] == "needs_input" and "resume.txt (attached)" in out["prepare"]
    assert out["request"] == "[CONFIRMATION_PENDING]" and "APPLICATION REVIEW" in Confirm.req[0]
    calls, status = out["before"]
    assert "submit_form" not in calls and status == "WAITING_FOR_CONFIRMATION"
    rec = tracker.find(job)
    assert s.state == "verified" and rec["status"] == "VERIFIED", out["confirm"]
    assert "Thank you for applying" in rec["verification_evidence"]
