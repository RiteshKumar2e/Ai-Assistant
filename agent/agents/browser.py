"""BrowserAgent — drive the user's real browser: open sites, search, click, type,
scroll, read the visible page. Clicks that look like submit/pay/send/delete are
confirmed first (tools/browser.click_risk)."""
from __future__ import annotations

from agent.agents.base import Agent, int_arg
from agent.tools.base import READ, WRITE
from agent.tools.browser import click_risk


def _url(u: str) -> str:
    u = (u or "").strip()
    return u if "://" in u or u.startswith("about:") else f"https://{u}"


class BrowserAgent(Agent):
    name = "BrowserAgent"
    description = "The user's real web browser: open a website, search, click, type, scroll, read the visible page, open tabs."

    def tools(self):
        b = self.cc.browser
        confirm = lambda a: ("Browser action", f"Click “{a.get('text') or a.get('description') or a.get('selector')}” — this may submit or send something.")
        return [
            self.tool("open_url", "Open a URL in the browser (e.g. github.com/owner/repo).", {"url": "address"},
                      lambda url: b.open_url(_url(url)), READ),
            self.tool("browser_search", "Search the web in the browser.", {"query": "text", "engine": "google|bing|duckduckgo"},
                      lambda query, engine="google": b.search(query, engine), READ),
            self.tool("new_tab", "Open a new tab (optionally at a URL).", {"url": "address"}, lambda url="": b.new_tab(_url(url) if url else ""), READ),
            self.tool("switch_tab", "Bring an already-open tab to the front by words from its title (e.g. 'ChatGPT').",
                      {"target": "tab title words"}, lambda target: b.switch_tab(target), READ),
            self.tool("read_page", "Read the visible text of the current page.", {}, b.read_page, READ),
            self.tool("current_url", "URL of the current page.", {}, b.current_url, READ),
            self.tool("click", "Click an element by its visible text, or a CSS selector.", {"text": "visible text", "selector": "css"},
                      lambda text="", selector="": b.click(text, selector), click_risk, confirm_detail=confirm),
            self.tool("smart_click", "Click an element described in words.", {"description": "e.g. 'the green Code button'"},
                      lambda description: b.smart_click(description), click_risk, confirm_detail=confirm),
            self.tool("type_text", "Type into a field (by selector, or described in words). Never type passwords or secrets.",
                      {"text": "text", "selector": "css", "description": "field in words"},
                      lambda text, selector="", description="": b.type_text(text, selector, description), WRITE),
            self.tool("press_key", "Press a key in the page (Enter, Tab, Escape…).", {"key": "key"}, lambda key="Enter": b.press(key), WRITE),
            self.tool("scroll", "Scroll the page.", {"direction": "up|down", "amount": "pixels"},
                      lambda direction="down", amount=600: b.scroll(direction, int_arg(amount, 600)), READ),
            self.tool("browser_back", "Go back.", {}, b.back, READ),
        ]
