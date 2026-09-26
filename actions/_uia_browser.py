"""
_uia_browser.py — drive the user's ALREADY-OPEN Chromium window through Windows
UI Automation, for when the browser has no remote-debugging port (the normal
case: JUDO never restarts a browser to get one — see browser_control.py).

What it can do on the window that is already there: switch to an open tab
(e.g. the ChatGPT tab), type into a field, click an element by its visible
name, press keys, read the visible page text and the current URL. Typing uses
Unicode keystrokes (send_keys), so Hindi/any script works and the clipboard is
never touched.

Honest limits, reported rather than hidden:
  * Windows only (UI Automation); elsewhere every call returns None and the
    caller reports "no automation access".
  * It sees what Chrome's accessibility tree exposes — normally all visible
    text, links, buttons and fields; not hidden DOM, not CSS selectors.
  * The first UI Automation query switches Chrome into its full accessibility
    mode, which can make a very heavy browser session a little slower until
    Chrome is next restarted by the user.

Every public function returns a result string, or None when UI Automation is
unavailable (not Windows / pywinauto missing / no window of that browser).
It never closes, restarts or creates a browser window.
"""
from __future__ import annotations

import platform
import time
from urllib.parse import urlparse

_EXE = {"chrome": "chrome.exe", "edge": "msedge.exe", "brave": "brave.exe", "vivaldi": "vivaldi.exe",
        "opera": "opera.exe", "operagx": "opera.exe"}
_KEYS = {"enter": "{ENTER}", "return": "{ENTER}", "tab": "{TAB}", "esc": "{ESC}", "escape": "{ESC}",
         "backspace": "{BACKSPACE}", "delete": "{DELETE}", "space": " ", "up": "{UP}", "down": "{DOWN}",
         "left": "{LEFT}", "right": "{RIGHT}", "arrowup": "{UP}", "arrowdown": "{DOWN}", "pageup": "{PGUP}",
         "pagedown": "{PGDN}", "home": "{HOME}", "end": "{END}"}
_CLICKABLE = ("Button", "Hyperlink", "MenuItem", "TabItem", "CheckBox", "RadioButton", "ListItem", "ComboBox", "Text")
_TEXTUAL = ("Text", "Hyperlink", "ListItem", "Button", "Header", "HeaderItem", "DataItem")


def available() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import pywinauto  # noqa: F401
        return True
    except ImportError:
        return False


def _com() -> None:
    """UI Automation is COM; each calling thread must have COM initialised."""
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass


def _desktop():
    from pywinauto import Desktop
    return Desktop(backend="uia")


def _send(keys: str) -> None:
    from pywinauto import keyboard
    keyboard.send_keys(keys, with_spaces=True, with_tabs=True, pause=0.01)


def _ctype(e) -> str:
    try:
        return e.element_info.control_type or ""
    except Exception:
        return ""


def _name(e) -> str:
    try:
        return (e.window_text() or "").strip()
    except Exception:
        return ""


def _focusable(e) -> bool:
    try:
        return bool(e.is_keyboard_focusable())
    except Exception:
        return False


def window(browser: str):
    """Top-most visible window belonging to that browser's process (z-order =
    the one the user used last), or None."""
    import psutil
    exe = _EXE.get(browser, f"{browser}.exe")
    for w in _desktop().windows(class_name="Chrome_WidgetWin_1", visible_only=True):
        if not _name(w):
            continue
        try:
            if psutil.Process(w.process_id()).name().lower() == exe:
                return w
        except Exception:
            continue
    return None


def _document(w):
    docs = w.descendants(control_type="Document")
    return docs[0] if docs else None


def escape(text: str) -> str:
    """send_keys syntax: {x} for its special characters; newline → Shift+Enter
    (a new line in chat boxes instead of sending the message)."""
    return "".join("{%s}" % c if c in "+^%~(){}[]" else "+{ENTER}" if c == "\n" else c for c in text)


def _site_label(url: str) -> str:
    """'https://chatgpt.com' → 'chatgpt'; '' for URLs with a path (a specific
    page was asked for — open it, don't just switch to any tab of that site)."""
    u = urlparse(url if "://" in url else f"https://{url}")
    if u.path not in ("", "/") or u.query:
        return ""
    host = (u.hostname or "").removeprefix("www.")
    return host.split(".")[0] if host else ""


def _run(browser: str, fn) -> str | None:
    if not available():
        return None
    _com()
    try:
        w = window(browser)
        if w is None:
            return None
        return fn(w)
    except Exception as e:
        return f"Could not control your open {browser} window: {e}"


# ── public operations ────────────────────────────────────────────────────────
def switch_tab(browser: str, target: str) -> str | None:
    """Bring an already-open tab whose title contains `target` to the front."""
    def go(w):
        t = target.lower().strip()
        tabs = [x for x in w.descendants(control_type="TabItem") if t and t in _name(x).lower()]
        if not tabs:
            return f"Could not find an open tab matching '{target}'."
        tab = tabs[0]
        w.set_focus()
        try:
            tab.select()
        except Exception:
            tab.click_input()
        return f"Switched to your open tab: {_name(tab)}"
    return _run(browser, go)


def switch_to_site(browser: str, url: str) -> str | None:
    """For go_to of a bare site (chatgpt.com): reuse its open tab if there is one.
    None → nothing matched (or no UIA); the caller then opens a new tab."""
    label = _site_label(url)
    if not label:
        return None
    r = switch_tab(browser, label)
    return r if r and r.startswith("Switched") else None


def type_text(browser: str, text: str, description: str = "") -> str | None:
    def go(w):
        w.set_focus()
        doc = _document(w)
        if doc is None:
            return "Could not find the page content in your browser window."
        # Real inputs are "Edit"; rich editors (ChatGPT's contenteditable box) can
        # surface as a focusable Group/Custom instead — used only if no Edit exists.
        cands = [e for e in doc.descendants() if _ctype(e) in ("Edit", "Group", "Custom") and _focusable(e)]
        edits = [e for e in cands if _ctype(e) == "Edit"] or cands
        words = [x for x in description.lower().split() if len(x) > 2]
        pick = next((e for e in edits if words and any(x in _name(e).lower() for x in words)), None) \
            or next((e for e in edits if e.has_keyboard_focus()), None) or (edits[0] if edits else None)
        if pick is None:
            return f"Could not find an input field{' for ' + repr(description) if description else ''} on the page."
        pick.click_input()
        time.sleep(0.15)
        _send(escape(text))
        return f"Typed into ({_name(pick) or _ctype(pick)}) in your open {browser} window."
    return _run(browser, go)


def click(browser: str, text: str) -> str | None:
    def go(w):
        w.set_focus()
        doc = _document(w)
        if doc is None:
            return "Could not find the page content in your browser window."
        t = text.lower().strip()
        hits = [e for e in doc.descendants() if _ctype(e) in _CLICKABLE and t and t in _name(e).lower()]
        if not hits:
            return f"Could not find '{text}' on the page."
        hits.sort(key=lambda e: (_name(e).lower() != t, _CLICKABLE.index(_ctype(e))))
        hits[0].click_input()
        return f"Clicked '{_name(hits[0])}' in your open {browser} window."
    return _run(browser, go)


def press(browser: str, key: str) -> str | None:
    def go(w):
        w.set_focus()
        k = (key or "enter").strip()
        _send(_KEYS.get(k.lower().replace(" ", ""), escape(k) if len(k) == 1 else "{%s}" % k.upper()))
        return f"Pressed: {k}"
    return _run(browser, go)


def get_url(browser: str) -> str | None:
    def go(w):
        for e in w.descendants(control_type="Edit"):          # the toolbar's address box comes first
            try:
                v = e.iface_value.CurrentValue
            except Exception:
                v = e.legacy_properties().get("Value", "")
            if v:
                return v if "://" in v else f"https://{v}"
        return "Could not read the address bar."
    return _run(browser, go)


def get_text(browser: str, limit: int = 12000) -> str | None:
    def go(w):
        doc = _document(w)
        if doc is None:
            return "Could not find the page content in your browser window."
        out, last = [], ""
        for e in doc.descendants():
            if _ctype(e) in _TEXTUAL and (n := _name(e)) and n != last:
                out.append(n); last = n
                if sum(map(len, out)) > limit:
                    break
        return f"{_name(w)}\n\n" + "\n".join(out)[:limit] if out else "The page has no readable text."
    return _run(browser, go)
