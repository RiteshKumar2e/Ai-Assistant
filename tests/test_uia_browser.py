"""actions/_uia_browser.py against a FAKE Chrome accessibility tree — no real
browser, no real UI Automation. Plus the browser_control fallback wiring."""
import pytest

import actions._uia_browser as uia
import actions.browser_control as bc


class El:
    def __init__(self, ctype, name="", kids=(), focused=False, focusable=True, value=""):
        self.element_info = type("I", (), {"control_type": ctype})()
        self._name, self.kids, self.focused, self.focusable, self.value = name, list(kids), focused, focusable, value
        self.log = []
        self.iface_value = type("V", (), {"CurrentValue": value})()

    def window_text(self): return self._name
    def descendants(self, control_type=None):
        out = []
        for k in self.kids:
            out += [k] + k.descendants()
        return [e for e in out if control_type is None or e.element_info.control_type == control_type]
    def click_input(self): self.log.append("click")
    def select(self): self.log.append("select")
    def set_focus(self): self.log.append("focus")
    def has_keyboard_focus(self): return self.focused
    def is_keyboard_focusable(self): return self.focusable
    def process_id(self): return 1
    def legacy_properties(self): return {"Value": self.value}


@pytest.fixture
def chrome(monkeypatch):
    composer = El("Edit", "Ask anything")
    send = El("Button", "Send prompt")
    doc = El("Document", "ChatGPT", [El("Text", "Hello! How can I help?"), El("Hyperlink", "Pricing"), composer, send])
    tabs = [El("TabItem", "Inbox (5,039) - Gmail"), El("TabItem", "ChatGPT")]
    win = El("Pane", "ChatGPT - Google Chrome", [El("Edit", "Address and search bar", value="chatgpt.com/"), *tabs, doc])
    keys = []
    monkeypatch.setattr(uia, "available", lambda: True)
    monkeypatch.setattr(uia, "_desktop", lambda: type("D", (), {"windows": lambda self, **kw: [win]})())
    monkeypatch.setattr(uia, "_send", keys.append)
    import psutil
    monkeypatch.setattr(psutil, "Process", lambda pid: type("P", (), {"name": lambda self: "chrome.exe"})())
    return {"win": win, "tabs": tabs, "composer": composer, "send": send, "keys": keys}


def test_switch_tab_selects_the_open_chatgpt_tab(chrome):
    assert uia.switch_tab("chrome", "chatgpt") == "Switched to your open tab: ChatGPT"
    assert chrome["tabs"][1].log == ["select"] and chrome["tabs"][0].log == []
    assert uia.switch_tab("chrome", "notion").startswith("Could not find")


def test_bare_site_reuses_open_tab_but_specific_page_does_not(chrome):
    assert uia.switch_to_site("chrome", "https://chatgpt.com") is not None
    assert uia.switch_to_site("chrome", "https://chatgpt.com/c/123") is None      # a specific page → new tab
    assert uia.switch_to_site("chrome", "https://github.com") is None             # no open GitHub tab


def test_type_uses_unicode_keystrokes_not_clipboard(chrome):
    out = uia.type_text("chrome", "नमस्ते (hi)\nline2", "message box")
    assert "Typed into" in out and chrome["composer"].log == ["click"]
    assert chrome["keys"] == ["नमस्ते {(}hi{)}+{ENTER}line2"]


def test_click_press_url_and_text(chrome):
    assert "Send prompt" in uia.click("chrome", "send")
    assert chrome["send"].log == ["click"]
    assert uia.press("chrome", "Enter") == "Pressed: Enter" and chrome["keys"][-1] == "{ENTER}"
    assert uia.get_url("chrome") == "https://chatgpt.com/"
    text = uia.get_text("chrome")
    assert "Hello! How can I help?" in text and "Pricing" in text


def test_unavailable_returns_none(monkeypatch):
    monkeypatch.setattr(uia, "available", lambda: False)
    assert uia.type_text("chrome", "x") is None and uia.switch_tab("chrome", "x") is None


def test_browser_control_falls_back_to_open_window(monkeypatch):
    """No debug port: smart_type goes to UI Automation on the open window — never a restart."""
    class Sess:
        browser_name, _spec = "chrome", {"engine": "chromium"}
        def run(self, coro, timeout=60):
            coro.close()
            raise bc._NoAutomation(bc._NO_AUTOMATION.format(name="Chrome", port=9222))
        def smart_type(self, d, t): return self._c()
        async def _c(self): pass
    monkeypatch.setattr(bc._registry, "get", lambda b=None: Sess())
    monkeypatch.setattr(bc, "_cdp_alive", lambda port: False)
    calls = []
    monkeypatch.setattr(bc.uia, "type_text", lambda b, text, desc: calls.append((b, text, desc)) or "Typed into (Ask anything)")
    out = bc.browser_control({"action": "smart_type", "description": "ChatGPT box", "text": "hi"})
    assert out.startswith("Typed into") and calls == [("chrome", "hi", "ChatGPT box")]
    monkeypatch.setattr(bc.uia, "type_text", lambda *a: None)          # UIA unavailable → honest message
    assert "never close or restart" in bc.browser_control({"action": "smart_type", "text": "hi"})
