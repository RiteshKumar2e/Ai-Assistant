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


def test_unsupported_or_failing_judo_browser_falls_back_to_edge(monkeypatch):
    for reply in (None, RuntimeError("did not start")):
        _fake_client(monkeypatch, reply)
        monkeypatch.setattr(bc._registry, "get", lambda b=None: (_ for _ in ()).throw(RuntimeError("edge reached")))
        assert "edge reached" in bc.browser_control({"action": "fill_form", "fields": {}})


def test_naming_another_browser_skips_judo_browser(monkeypatch):
    sent = _fake_client(monkeypatch, "judo")
    monkeypatch.setattr(bc._registry, "get", lambda b=None: (_ for _ in ()).throw(RuntimeError(f"edge path for {b}")))
    assert "edge path for firefox" in bc.browser_control({"action": "go_to", "url": "x.com", "browser": "firefox"})
    assert sent == []


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
