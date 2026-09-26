"""
agent/tools/browser.py — BrowserController over JUDO's existing Playwright/CDP
session (actions/browser_control.py), which drives the user's real, already-open
browser rather than a second profile.

browser_control returns human sentences, not status codes, so failure is read
from its known failure phrasings. Anything that looks like submitting, paying,
sending or deleting is classed EXTERNAL and therefore confirmed first.
"""
from __future__ import annotations

import importlib.util
import re

from agent.tools.base import EXTERNAL, READ, WRITE, BrowserController, ToolResult

# Anchored: get_text returns raw page text, which may well contain "error".
_FAIL = re.compile(r"(?i)^\s*(could not|unknown|no selector|no active|element not found|failed|[\w ]{0,20}error\b|browser action .* timed out)")
_CONSEQUENTIAL = re.compile(r"(?i)\b(submit|send|pay|buy|purchase|checkout|place order|order now|delete|remove|confirm|publish|"
                            r"post|sign ?up|register|transfer|subscribe|book now|donate|merge)\b")


def click_risk(args: dict) -> str:
    return EXTERNAL if _CONSEQUENTIAL.search(f"{args.get('text', '')} {args.get('selector', '')} {args.get('description', '')}") else WRITE


class PlaywrightBrowser(BrowserController):
    def __init__(self, player=None):
        self.player = player

    def available(self) -> tuple[bool, str]:
        if importlib.util.find_spec("playwright") is None:
            return False, ("Browser automation isn't configured (Playwright is not installed: "
                           "`pip install playwright && playwright install chromium`).")
        return True, ""

    def _call(self, **params) -> ToolResult:
        ok, why = self.available()
        if not ok:
            return ToolResult.fail(why, "unavailable")
        from actions.browser_control import browser_control
        out = str(browser_control(parameters=params, player=self.player) or "")
        return ToolResult(bool(out) and not _FAIL.match(out), out or "Browser returned nothing.")

    def open_url(self, url: str) -> ToolResult:
        r = self._call(action="go_to", url=url)
        r.data["url"] = url
        return r

    def search(self, query: str, engine: str = "google") -> ToolResult:
        return self._call(action="search", query=query, engine=engine)

    def switch_tab(self, target: str) -> ToolResult:
        return self._call(action="switch_tab", target=target)

    def new_tab(self, url: str = "") -> ToolResult:
        return self._call(action="new_tab", url=url)

    def click(self, text: str = "", selector: str = "") -> ToolResult:
        return self._call(action="click", text=text or None, selector=selector or None) if (text or selector) else ToolResult.fail("Give text or selector.")

    def smart_click(self, description: str) -> ToolResult:
        return self._call(action="smart_click", description=description)

    def type_text(self, text: str, selector: str = "", description: str = "") -> ToolResult:
        if description and not selector:
            return self._call(action="smart_type", description=description, text=text)
        return self._call(action="type", selector=selector or None, text=text)

    def press(self, key: str) -> ToolResult:
        return self._call(action="press", key=key)

    def scroll(self, direction: str = "down", amount: int = 600) -> ToolResult:
        return self._call(action="scroll", direction=direction, amount=amount)

    def read_page(self) -> ToolResult:
        r = self._call(action="get_text")
        if r.ok and len(r.output) > 12000:
            r.output = r.output[:12000] + "\n…[page truncated]"
        return r

    def current_url(self) -> ToolResult:
        return self._call(action="get_url")

    def back(self) -> ToolResult:
        return self._call(action="back")


RISKS = {"open_url": READ, "browser_search": READ, "read_page": READ, "current_url": READ, "scroll": READ,
         "new_tab": READ, "browser_back": READ, "type_text": WRITE, "press_key": WRITE}
