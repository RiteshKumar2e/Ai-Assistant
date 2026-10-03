"""Opening mail always means JUDO Mail: open_app names, webmail URLs in browser_control,
and one running copy that is focused instead of relaunched. Nothing real starts —
the launcher is faked (see conftest.no_real_judo_mail)."""
import subprocess
import sys

import pytest

import actions.browser_control as bc
import actions.open_app as oa
from judo_mail import client as mc

# the real ones, taken before conftest.no_real_judo_mail stubs them for each test
_REAL_START, _REAL_MUTEX_EXISTS = mc.start, mc._mutex_exists

MAIL_APP_NAMES = ["mail", "Mail", "gmail", "Gmail", "GMAIL", "email", "e-mail", "E-Mail", "inbox", "outlook",
                  "Outlook", "mail app", "windows mail", "Windows Mail", "judo mail", "JUDO Mail", "judo_mail",
                  "yahoo mail", "google mail", "my mailbox", "mail kholo", "Gmail kholo", "open my email",
                  "check mail", "meri mail kholo", "email app", "gmail.com", "mail.google.com", "मेल खोलो", "जीमेल"]
NOT_MAIL_APPS = ["Spotify", "notepad", "calculator", "WhatsApp", "VS Code", "voicemail", "Mailchimp", "chrome"]

MAIL_URLS = ["gmail.com", "https://gmail.com", "www.gmail.com", "mail.google.com", "https://mail.google.com/mail/u/0/#inbox",
             "inbox.google.com", "outlook.live.com", "https://outlook.live.com/mail/0/", "outlook.office.com/mail",
             "outlook.com", "mail.yahoo.com", "https://proton.me/mail", "mail.proton.me", "gmail", "Outlook"]
NOT_MAIL_URLS = ["google.com", "https://www.google.com/search?q=gmail", "youtube.com", "mailchimp.com",
                 "proton.me/vpn", "chatgpt.com",
                 # send_email's no-account fallback: Gmail's compose page must still open as a tab
                 "https://mail.google.com/mail/?to=a%40b.com&view=cm&fs=1&su=Hi",
                 "https://mail.google.com/mail/u/0/#inbox?compose=new", "mailto:a@b.com"]


@pytest.fixture
def opened(monkeypatch):
    calls = []
    monkeypatch.setattr(mc, "open_mail", lambda: calls.append(True) or "Opening JUDO Mail.")
    return calls


@pytest.fixture
def judo_browser(monkeypatch):
    """JUDO Browser as the default browser, faked: records what it is asked to do."""
    sent = []
    monkeypatch.setattr(bc, "_judo_browser_default", lambda: True)
    monkeypatch.setattr(bc, "_judo_browser", lambda action, params: sent.append((action, params)) or "Opened tab")
    return sent


# ── classification ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", MAIL_APP_NAMES)
def test_mail_app_names_are_mail(name):
    assert mc.is_mail_app(name)


@pytest.mark.parametrize("name", NOT_MAIL_APPS)
def test_other_apps_are_not_mail(name):
    assert not mc.is_mail_app(name)


@pytest.mark.parametrize("url", MAIL_URLS)
def test_webmail_urls_are_mail(url):
    assert mc.is_mail_url(url)


@pytest.mark.parametrize("url", NOT_MAIL_URLS)
def test_other_urls_and_compose_links_are_not_mail(url):
    assert not mc.is_mail_url(url)


# ── open_app ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", MAIL_APP_NAMES)
def test_open_app_mail_names_open_judo_mail(name, opened, monkeypatch):
    monkeypatch.setattr(oa, "_OS_LAUNCHERS", {})          # a real launch would return "Unsupported…"
    assert oa.open_app({"app_name": name}) == "Opening JUDO Mail."
    assert opened == [True]


@pytest.mark.parametrize("name", ["Spotify", "notepad", "voicemail", "Mailchimp"])
def test_open_app_other_names_do_not_open_judo_mail(name, opened, monkeypatch):
    launched = []
    monkeypatch.setattr(oa, "_OS_LAUNCHERS", {oa._SYSTEM: lambda n: launched.append(n) or True})
    assert oa.open_app({"app_name": name}) == f"Opened {name}."
    assert opened == [] and launched


# ── browser_control ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["go_to", "new_tab"])
@pytest.mark.parametrize("url", MAIL_URLS)
def test_browser_mail_urls_open_judo_mail_not_a_tab(action, url, opened, judo_browser):
    assert bc.browser_control({"action": action, "url": url}) == "Opening JUDO Mail."
    assert opened == [True] and judo_browser == []


def test_browser_mail_urls_open_judo_mail_in_edge_mode_too(opened, monkeypatch):
    monkeypatch.setattr(bc._registry, "get", lambda *a: pytest.fail("no browser session for mail"))
    assert bc.browser_control({"action": "go_to", "url": "gmail.com", "browser": "edge"}) == "Opening JUDO Mail."
    assert opened == [True]


@pytest.mark.parametrize("url", NOT_MAIL_URLS[:-1])
def test_browser_other_urls_stay_in_judo_browser(url, opened, judo_browser):
    assert bc.browser_control({"action": "new_tab", "url": url}) == "Opened tab"
    assert opened == [] and judo_browser[0][0] == "new_tab"


@pytest.mark.parametrize("query", ["gmail", "open gmail", "Outlook", "gmail.com", "yahoo mail"])
def test_search_for_just_a_mail_app_opens_judo_mail(query, opened, judo_browser):
    assert bc.browser_control({"action": "search", "query": query}) == "Opening JUDO Mail."
    assert opened == [True] and judo_browser == []


@pytest.mark.parametrize("query", ["how to create a gmail account", "gmail storage full fix", "python tutorial"])
def test_real_searches_about_mail_stay_searches(query, opened, judo_browser):
    assert bc.browser_control({"action": "search", "query": query}) == "Opened tab"
    assert opened == [] and judo_browser[0][0] == "search"


def test_open_in_browser_sends_webmail_to_judo_mail(opened, monkeypatch):
    monkeypatch.setattr(bc, "_judo_browser_default", lambda: pytest.fail("no browser for mail"))
    assert bc.open_in_browser("https://mail.google.com") is True
    assert opened == [True]


# ── one copy: focus the running one, start only when none runs ────────────

def test_open_mail_focuses_running_copy_instead_of_starting(monkeypatch):
    focused = []
    monkeypatch.setattr(mc, "running", lambda: True)
    monkeypatch.setattr(mc, "focus", lambda: focused.append(True) or True)
    assert mc.open_mail() == "JUDO Mail is already open — brought it to the front."
    assert focused == [True]          # conftest's start() would have failed the test


def test_open_mail_never_starts_a_second_copy_while_one_is_starting(monkeypatch):
    monkeypatch.setattr(mc, "running", lambda: True)
    monkeypatch.setattr(mc, "focus", lambda: False)
    assert "starting" in mc.open_mail()


def test_open_mail_starts_when_not_running(monkeypatch):
    started = []
    monkeypatch.setattr(mc, "running", lambda: False)
    monkeypatch.setattr(mc, "start", lambda: started.append(True) or True)
    assert mc.open_mail() == "Opening JUDO Mail."
    assert started == [True]


def test_start_launches_module_windowed_from_repo_root_without_waiting(monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            calls.append((cmd, kw))

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    assert _REAL_START() is True
    cmd, kw = calls[0]
    assert cmd[1:] == ["-m", "judo_mail"] and kw["cwd"] == str(mc.ROOT)
    if sys.platform == "win32" and (mc.Path(sys.executable).with_name("pythonw.exe")).exists():
        assert cmd[0].lower().endswith("pythonw.exe")


def test_control_server_focus_round_trip(monkeypatch):
    """The real localhost server: ping, focus with the token, refuse a wrong token."""
    hits = []
    mc.serve(lambda: hits.append(True))
    assert mc.alive()
    assert mc.focus() is True and hits == [True]
    info = mc._info()
    info["token"] = "wrong"
    mc.CONTROL_FILE.write_text(__import__("json").dumps(info), encoding="utf-8")
    assert mc.focus() is False and hits == [True]


@pytest.mark.skipif(sys.platform != "win32", reason="named mutex is Windows-only")
def test_named_mutex_admits_one_instance(monkeypatch):
    import ctypes
    import uuid
    monkeypatch.setattr(mc, "MUTEX_NAME", f"Local\\JUDO.Mail.test.{uuid.uuid4().hex}")
    held = mc._held
    try:
        assert _REAL_MUTEX_EXISTS() is False
        assert mc.claim_instance() is True
        assert _REAL_MUTEX_EXISTS() is True
        assert mc.claim_instance() is False                # a second launch loses
    finally:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mc._held))
        mc._held = held


def test_second_launch_hands_over_and_opens_no_window(monkeypatch):
    pytest.importorskip("PyQt6.QtWebEngineWidgets")
    from judo_mail import app as ma
    handed = []
    monkeypatch.setattr(mc, "claim_instance", lambda: False)
    monkeypatch.setattr(mc, "hand_over", lambda: handed.append(True) or True)
    monkeypatch.setattr(ma, "QApplication", lambda *a: pytest.fail("a second JUDO Mail must not build a window"))
    assert ma.main() == 0
    assert handed == [True]
