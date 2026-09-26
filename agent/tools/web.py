"""
agent/tools/web.py — read-only web research: search with source URLs, and a
plain HTTP page reader. Neither needs the browser, so research works even when
browser automation is unavailable. Reuses actions/web_search's backends.
"""
from __future__ import annotations

import re

from agent.tools.base import ToolResult

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


class WebTools:
    def search(self, query: str, max_results: int = 8) -> ToolResult:
        from actions.web_search import _ddg_search
        try:
            rows = _ddg_search(query, max_results=max(1, min(int(max_results), 15)))
        except Exception as e:
            return ToolResult.fail(f"Web search failed: {e}", "unavailable")
        if not rows:
            return ToolResult.fail(f"No results for {query!r}.")
        text = "\n\n".join(f"[{i}] {r['title']}\n{r['url']}\n{r['snippet']}" for i, r in enumerate(rows, 1))
        return ToolResult(True, text, {"sources": [r["url"] for r in rows]})

    def research(self, query: str) -> ToolResult:
        """Gemini grounded search (current web), DDG fallback — actions/web_search._research."""
        from actions.web_search import _research
        try:
            return ToolResult(True, _research(query))
        except Exception as e:
            return ToolResult.fail(f"Research failed: {e}", "unavailable")

    def fetch(self, url: str, max_chars: int = 10000) -> ToolResult:
        import requests
        if not re.match(r"https?://", url or ""):
            return ToolResult.fail("fetch_url needs an http(s) URL.")
        try:
            r = requests.get(url, headers={"User-Agent": _UA}, timeout=15)
        except requests.RequestException as e:
            return ToolResult.fail(f"Could not fetch {url}: {e}")
        if r.status_code >= 400:
            return ToolResult.fail(f"{url} returned HTTP {r.status_code}.")
        ctype = r.headers.get("content-type", "")
        if "html" in ctype:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            for t in soup(["script", "style", "nav", "footer", "noscript", "svg", "form"]):
                t.decompose()
            title = (soup.title.string or "").strip() if soup.title else ""
            text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
        elif "text" in ctype or "json" in ctype:
            title, text = "", r.text
        else:
            return ToolResult.fail(f"{url} is {ctype or 'binary'}, not a readable page.")
        body = text[:max_chars] + ("\n…[truncated]" if len(text) > max_chars else "")
        return ToolResult(True, f"{title}\n{url}\n\n{body}", {"url": url, "title": title})
