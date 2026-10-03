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


def test_open_app_mail_aliases_launch_judo_mail(monkeypatch):
    import actions.open_app as oa

    from judo_mail import client as mc

    launched = []
    monkeypatch.setattr(mc, "open_mail", lambda: launched.append(True) or "Opening JUDO Mail.")
    for name in ("mail", "Gmail", "email", "inbox", "JUDO Mail", "judo_mail", "gmail.com", "my mailbox"):
        assert oa.open_app({"app_name": name}) == "Opening JUDO Mail."
    assert len(launched) == 8


def test_open_app_mail_reports_launch_failure(monkeypatch):
    import actions.open_app as oa
    from judo_mail import client as mc

    monkeypatch.setattr(mc, "running", lambda: False)
    monkeypatch.setattr(mc, "start", lambda: False)
    assert oa.open_app({"app_name": "Gmail"}) == "Could not open JUDO Mail."


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
    assert s["theme"] == "dark" and s["sandbox"] is True and s["search_engine"] == "google"


def test_old_sandbox_off_setting_is_dropped_once(tmp_path, monkeypatch):
    import judo_browser.app_data as ad
    monkeypatch.setattr(ad, "DATA", tmp_path)
    ad.save("settings.json", {"sandbox": False})                  # saved by a build where it couldn't start
    assert ad.load_settings()["sandbox"] is True
    ad.save("settings.json", {"sandbox": False, "sandbox_fixed": True})   # the user turned it off since
    assert ad.load_settings()["sandbox"] is False


def _ntp_settings():
    import json as _json
    from judo_browser.app_data import DEFAULT_SETTINGS
    return _json.loads(_json.dumps(DEFAULT_SETTINGS))


def test_new_tab_shortcuts_add_edit_remove_undo():
    pytest.importorskip("PyQt6.QtWebEngineCore")
    from judo_browser import newtab
    s, undo = _ntp_settings(), []
    hist = [{"url": "https://www.youtube.com/watch?v=1", "title": "v"}] * 3 + [{"url": "https://amazon.in/x", "title": "a"}]
    assert [t["title"] for t in newtab.tiles(s, hist)][:2] == ["YouTube", "Amazon"]      # most visited first
    assert newtab.apply(s, {"cmd": "add", "title": "", "url": "judo.ai"}, undo) == "refresh"
    assert s["shortcuts"] == [{"title": "Judo", "url": "https://judo.ai"}]
    assert newtab.tiles(s, hist)[0]["custom"] is True
    assert newtab.apply(s, {"cmd": "add", "url": "javascript:alert(1)"}, undo) is None    # only web addresses
    assert newtab.apply(s, {"cmd": "edit", "old": "https://www.youtube.com", "title": "YT", "url": "youtube.com"}, undo)
    assert "youtube.com" in s["hidden_tiles"] and s["shortcuts"][-1]["title"] == "YT"
    assert newtab.apply(s, {"cmd": "remove", "url": "https://amazon.in"}, undo) == "removed"
    assert all(t["title"] != "Amazon" for t in newtab.tiles(s, hist))
    assert newtab.apply(s, {"cmd": "undo"}, undo) == "refresh" and "amazon.in" not in s["hidden_tiles"]
    assert newtab.apply(s, {"cmd": "restore_tiles"}, undo) and s["shortcuts"] == s["hidden_tiles"] == []
    assert len(newtab.tiles(s, hist)) <= newtab.MAX_TILES


def test_customize_commands_validate_and_report_what_to_redraw():
    pytest.importorskip("PyQt6.QtWebEngineCore")
    from judo_browser import newtab
    s, undo = _ntp_settings(), []
    assert newtab.apply(s, {"cmd": "color", "color": "#e25fa4"}, undo) == "restyle" and s["theme_color"] == "#E25FA4"
    assert newtab.apply(s, {"cmd": "color", "color": "red;}body{"}, undo) is None and s["theme_color"] == "#E25FA4"
    assert newtab.apply(s, {"cmd": "mode", "mode": "dark"}, undo) == "restyle" and s["theme"] == "dark"
    assert newtab.apply(s, {"cmd": "bg", "id": "aurora"}, undo) == "refresh" and s["ntp_background"] == "aurora"
    assert newtab.apply(s, {"cmd": "bg", "id": "../../x"}, undo) is None
    assert newtab.apply(s, {"cmd": "upload_bg"}, undo) == "pick_bg"
    assert newtab.apply(s, {"cmd": "open", "page": "history"}, undo) == "open:history"
    assert newtab.apply(s, {"cmd": "open", "page": "close"}, undo) is None                # only the four pages
    assert newtab.apply(s, {"cmd": "reset"}, undo) == "restyle" and s["theme_color"] == s["ntp_background"] == ""


def test_new_tab_page_shows_judo_logo_and_theme():
    pytest.importorskip("PyQt6.QtWebEngineCore")
    from judo_browser import newtab, theme
    s = _ntp_settings()
    s.update(ntp_background="sunset")
    page = newtab.page(s, [], theme.resolve("light"), panel=True)
    assert 'data-l="J">J</span>' in page and 'data-l="O">O</span>' in page
    assert "Customize JUDO" in page and 'id="panel" class="show"' in page and "on-dark" in page
    assert "Incognito" in newtab.page(s, [], theme.THEMES["incognito"], incognito=True)


def test_colour_theme_tints_frame_light_and_dark():
    pytest.importorskip("PyQt6.QtGui")
    from judo_browser import theme
    light, dark = theme.resolve("light", "#34A853"), theme.resolve("dark", "#34A853")
    assert light["frame"] != theme.THEMES["light"]["frame"] and light["text"] == theme.THEMES["light"]["text"]
    assert dark["dark"] is True and dark["frame"] != theme.THEMES["dark"]["frame"]
    assert theme.resolve("light", "") == theme.THEMES["light"] and theme.resolve("light", "nope") == theme.THEMES["light"]


def test_add_shortcut_tile_counts_toward_the_ten():
    pytest.importorskip("PyQt6.QtWebEngineCore")
    from judo_browser import newtab
    s = _ntp_settings()
    hist = [{"url": f"https://site{i}.com/", "title": str(i)} for i in range(20)]
    assert newtab.has_add_tile(s) and len(newtab.tiles(s, hist)) == newtab.MAX_TILES - 1
    s["ntp_shortcut_mode"] = "most_visited"
    assert not newtab.has_add_tile(s) and len(newtab.tiles(s, hist)) == newtab.MAX_TILES


def test_judo_accounts_add_edit_remove_and_keep_their_own_storage(tmp_path, monkeypatch):
    import judo_browser.accounts as acc
    import judo_browser.app_data as ad
    monkeypatch.setattr(acc, "DATA", tmp_path)
    monkeypatch.setattr(ad, "DATA", tmp_path)             # load/save live in app_data
    data = acc.load_all()
    assert [a["id"] for a in data["list"]] == ["default"] and data["current"] == "default"
    assert acc.add(data, {"name": "  ", "email": ""}) is None                        # a name is required
    assert acc.add(data, {"name": "Work", "email": "not-an-email"}) is None
    work = acc.add(data, {"name": "Work", "email": "w@example.com", "color": "#188038"})
    assert work and work["color"] == "#188038" and len(data["list"]) == 2
    assert acc.edit(data, work["id"], {"name": "Office", "email": "", "color": "bad"})
    assert acc.get(data, work["id"])["name"] == "Office" and acc.get(data, work["id"])["color"] == acc.COLORS[0]
    assert acc.storage("default") == (tmp_path / "profile", tmp_path / "cache", "")      # original profile kept
    assert acc.storage(work["id"])[0] == tmp_path / "accounts" / work["id"] / "profile"
    assert not acc.remove(data, "default")
    data["current"] = work["id"]
    assert acc.remove(data, work["id"]) and data["current"] == "default"


def test_account_popup_on_new_tab_page():
    pytest.importorskip("PyQt6.QtWebEngineCore")
    from judo_browser import newtab, theme
    me = {"id": "default", "name": "Ritesh Kumar", "email": "r@example.com", "color": "#1A73E8", "photo": ""}
    other = {"id": "a1", "name": "Work", "email": "", "color": "#188038", "photo": ""}
    page = newtab.page(_ntp_settings(), [], theme.resolve("light"), account=me, others=[other])
    assert 'id="avatarBtn"' in page and "Hi, Ritesh!" in page and "Manage your JUDO Account" in page
    assert 'data-switch="a1"' in page and "Add another account" in page and "Sign out of all accounts" in page and "Remove an account" in page
    assert "<script>" not in newtab.page(_ntp_settings(), [], theme.resolve("light"),
                                         account=dict(me, name="<script>x"), others=[]).split("<script>const")[0]


def test_about_page_says_who_made_it():
    pytest.importorskip("PyQt6.QtWebEngineCore")
    from judo_browser import RELEASE, __version__, about_page, theme
    info = {"chromium": "140.0", "qt": "6.11", "pyqt": "6.11", "python": "3.10", "os": "Windows",
            "sandbox": True, "accounts": 1, "profile": "C:/x"}
    page = about_page.page(theme.resolve("light"), info)
    assert "Made with" in page and "Ritesh" in page and __version__ in page and RELEASE in page
    assert "Chromium 140.0" in page and "Copy details" in page


def test_judo_pages_sign_their_commands_with_the_run_token():
    pytest.importorskip("PyQt6.QtWebEngineCore")
    from judo_browser import about_page, account_page, newtab, theme
    t = theme.resolve("light")
    me = {"id": "default", "name": "R", "email": "", "color": "#1A73E8", "photo": ""}
    info = {"chromium": "1", "qt": "1", "pyqt": "1", "python": "1", "os": "W", "sandbox": True, "accounts": 1, "profile": "x"}
    stats = {"history": 0, "bookmarks": 0, "passwords": 0, "never_save": 0, "offer_passwords": True, "sandbox": True}
    for html_, src in ((newtab.page(_ntp_settings(), [], t, account=me, token="tok123"), "ntp"),
                       (account_page.page(me, [], stats, t, token="tok123"), "account"),
                       (about_page.page(t, info, token="tok123"), "about")):
        assert 'TOKEN="tok123"' in html_ and f"SRC='{src}'" in html_ and "{t: TOKEN, src: SRC}" in html_
