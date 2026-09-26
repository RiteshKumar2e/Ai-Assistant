"""ResearchAgent — current information from the web: search with source links,
read sources, grounded deep research. Findings are synthesised by the supervisor
into the final answer (with the URLs), or saved via FileAgent.write_file."""
from __future__ import annotations

from agent.agents.base import Agent, int_arg
from agent.tools.base import READ


class ResearchAgent(Agent):
    name = "ResearchAgent"
    description = "Web research: search (with source URLs), read source pages, grounded deep research, compare findings with citations."

    def tools(self):
        w = self.cc.web
        return [
            self.tool("web_search", "Web search; returns titles, URLs and snippets.", {"query": "text", "max_results": "int ≤15"},
                      lambda query, max_results=8: w.search(query, int_arg(max_results, 8)), READ),
            self.tool("fetch_url", "Read a web page's main text over HTTP (no browser needed).", {"url": "http(s) URL"},
                      lambda url: w.fetch(url), READ),
            self.tool("deep_research", "Grounded, current, comprehensive answer on a topic (Google-grounded Gemini, DDG fallback).",
                      {"query": "topic"}, lambda query: w.research(query), READ, long_running=True),
        ]
