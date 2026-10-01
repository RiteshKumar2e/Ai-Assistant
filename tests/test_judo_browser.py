"""JUDO Browser: address-bar parsing and browser_control routing. No window is
ever opened here — the client is faked (see conftest.no_real_judo_browser)."""
import sys
import types

import pytest

import actions.browser_control as bc


def test_address_bar_turns_words_into_search_and_names_into_urls():
    pytest.importorskip("PyQt6.QtWebEngineCore")
    from judo_browser.app import to_url
    assert to_url("youtube.com").toString() == "https://youtube.com"
    assert to_url("localhost:8000").toString() == "http://localhost:8000"
    assert to_url("https://x.org/a?b=1").toString() == "https://x.org/a?b=1"
    assert to_url("kal ka mausam").toString().startswith("https://www.google.com/search?q=kal")


def _fake_client(monkeypatch, reply):
    sent = []

    def send(action, params):
        sent.append((action, params))
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setitem(sys.modules, "judo_browser.client", types.SimpleNamespace(send=send))
    import judo_browser
    monkeypatch.setattr(judo_browser, "client", sys.modules["judo_browser.client"], raising=False)
    monkeypatch.setattr(bc, "_judo_browser_default", lambda: True)
    return sent


def test_default_goes_to_judo_browser(monkeypatch):
    sent = _fake_client(monkeypatch, "Opened YouTube")
    monkeypatch.setattr(bc._registry, "get", lambda b=None: pytest.fail("Edge path must not run"))
    assert bc.browser_control({"action": "go_to", "url": "youtube.com"}) == "Opened YouTube"
    assert sent == [("go_to", {"action": "go_to", "url": "youtube.com"})]


def test_unsupported_or_failing_judo_browser_never_opens_edge(monkeypatch):
    monkeypatch.setattr(bc._registry, "get", lambda b=None: pytest.fail("Edge path must not run"))
    _fake_client(monkeypatch, None)
    assert "can't do 'fill_form'" in bc.browser_control({"action": "fill_form", "fields": {}})
    _fake_client(monkeypatch, RuntimeError("did not start"))
    out = bc.browser_control({"action": "go_to", "url": "x.com"})
    assert "could not be started" in out and "No other browser" in out


def test_naming_chrome_or_edge_still_uses_judo_browser(monkeypatch):
    sent = _fake_client(monkeypatch, "Opened X")
    monkeypatch.setattr(bc._registry, "get", lambda b=None: pytest.fail("Edge path must not run"))
    for name in ("chrome", "edge", "firefox"):
        assert bc.browser_control({"action": "go_to", "url": "x.com", "browser": name}) == "Opened X"
    assert sent == [("go_to", {"action": "go_to", "url": "x.com"})] * 3     # 'browser' is dropped


def test_open_app_chrome_brings_up_judo_browser(monkeypatch):
    import actions.open_app as oa
    sent = _fake_client(monkeypatch, "JUDO Browser is in front.")
    monkeypatch.setattr(oa, "_OS_LAUNCHERS", {})       # any real launch would fail the assertion below
    for name in ("Chrome", "edge", "browser"):
        assert oa.open_app({"app_name": name}) == "JUDO Browser is in front."
    assert [a for a, _ in sent] == ["focus"] * 3


def test_open_in_browser_uses_judo_browser(monkeypatch):
    sent = _fake_client(monkeypatch, "Opened")
    monkeypatch.setattr(bc, "_open_in_running", lambda *a: pytest.fail("Edge path must not run"))
    assert bc.open_in_browser("youtube.com/watch?v=1") is True
    assert sent[0][0] == "new_tab" and "youtube.com/watch?v=1" in sent[0][1]["url"]


def test_passwords_encrypted_per_windows_user_and_matched_by_origin(tmp_path, monkeypatch):
    pytest.importorskip("win32crypt")
    import judo_browser.app_data as ad
    monkeypatch.setattr(ad, "DATA", tmp_path)
    from judo_browser.passwords import PasswordStore
    s = PasswordStore()
    s.put("https://github.com", "ritesh", "s3cret!")
    s.put("https://github.com", "ritesh", "newer")          # same user: replaced, not duplicated
    assert "s3cret" not in (tmp_path / "passwords.json").read_text() and "newer" not in (tmp_path / "passwords.json").read_text()
    assert PasswordStore().for_origin("https://github.com") == [{"username": "ritesh", "password": "newer"}]
    assert PasswordStore().for_origin("https://evil.example") == []
    s.block("https://bank.example")
    assert "https://bank.example" in PasswordStore().never
    s.delete("https://github.com", "ritesh")
    assert PasswordStore().for_origin("https://github.com") == []


def test_address_bar_search_follows_the_chosen_engine():
    pytest.importorskip("PyQt6.QtWebEngineCore")
    import judo_browser.app as jb
    from judo_browser.app_data import SEARCH_ENGINES
    old = jb._search_base
    try:
        jb._search_base = SEARCH_ENGINES["duckduckgo"][1]
        assert jb.to_url("judo ai").toString().startswith("https://duckduckgo.com/?q=judo")
        assert jb.to_url("github.com").toString() == "https://github.com"
    finally:
        jb._search_base = old


def test_settings_fill_missing_keys_with_defaults(tmp_path, monkeypatch):
    import judo_browser.app_data as ad
    monkeypatch.setattr(ad, "DATA", tmp_path)
    ad.save("settings.json", {"theme": "dark"})
    s = ad.load_settings()
    assert s["theme"] == "dark" and s["sandbox"] is False and s["search_engine"] == "google"
