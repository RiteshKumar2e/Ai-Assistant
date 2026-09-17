"""
Tests for actions/web_search.py — Gemini-grounded search with a DuckDuckGo
fallback. No real network calls: _gemini_search and DDGS are monkeypatched.
"""
import actions.web_search as ws


# ── _search(): Gemini -> DDG fallback ───────────────────────────────────────

def test_search_uses_gemini_when_available(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: "gemini answer")
    assert ws._search("weather today") == "gemini answer"


def test_search_falls_back_to_ddg_when_gemini_fails(monkeypatch):
    def boom(q):
        raise RuntimeError("quota exhausted")
    monkeypatch.setattr(ws, "_gemini_search", boom)
    monkeypatch.setattr(ws, "_ddg_search", lambda q, max_results=6: [
        {"title": "Result 1", "snippet": "snippet 1", "url": "http://example.com/1"},
    ])
    result = ws._search("weather today")
    assert "Result 1" in result
    assert "Search failed" not in result   # a successful fallback is not a failure


def test_search_empty_ddg_results_reports_no_results_not_error(monkeypatch):
    def boom(q):
        raise RuntimeError("gemini down")
    monkeypatch.setattr(ws, "_gemini_search", boom)
    monkeypatch.setattr(ws, "_ddg_search", lambda q, max_results=6: [])
    result = ws._search("an extremely obscure query")
    assert "No results found" in result
    assert "Search failed" not in result


# ── _ddg_news(): news backend failure -> text search fallback ──────────────

def test_ddg_news_failure_falls_back_to_text_search(monkeypatch):
    class _BoomDDGS:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def news(self, query, max_results):
            raise RuntimeError("news backend timed out")

    monkeypatch.setattr(ws, "DDGS", lambda timeout=8: _BoomDDGS(), raising=False)
    import ddgs
    monkeypatch.setattr(ddgs, "DDGS", lambda timeout=8: _BoomDDGS())
    monkeypatch.setattr(ws, "_ddg_search", lambda query, max_results=6: [
        {"title": "Fallback result", "snippet": "s", "url": "http://x"},
    ])

    results = ws._ddg_news("technology")
    assert results and results[0]["title"] == "Fallback result"


def test_ddg_news_success_does_not_trigger_fallback(monkeypatch):
    class _OkDDGS:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def news(self, query, max_results):
            return [{"title": "Real news", "body": "b", "url": "u", "source": "src"}]

    import ddgs
    monkeypatch.setattr(ddgs, "DDGS", lambda timeout=8: _OkDDGS())
    fallback_called = []
    monkeypatch.setattr(ws, "_ddg_search", lambda *a, **kw: fallback_called.append(1))

    results = ws._ddg_news("technology")
    assert results[0]["title"] == "Real news"
    assert fallback_called == []


# ── _news(): Gemini/DDG race, both failing must not hang forever ───────────

def test_news_both_backends_fail_reports_no_news_within_timeout(monkeypatch):
    monkeypatch.setattr(ws, "_gemini_search", lambda q: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(ws, "_ddg_news", lambda q, max_results=8: (_ for _ in ()).throw(RuntimeError("down")))

    import time
    start = time.monotonic()
    result = ws._news("technology")
    elapsed = time.monotonic() - start

    assert "No news found" in result
    assert elapsed < 10.5   # bounded by the existing 10.0s done_evt.wait timeout


def test_news_gemini_success_returns_immediately(monkeypatch):
    long_answer = "x" * 100   # must exceed the 60-char "valid result" threshold in _store
    monkeypatch.setattr(ws, "_gemini_search", lambda q: long_answer)
    monkeypatch.setattr(ws, "_ddg_news", lambda q, max_results=8: (_ for _ in ()).throw(RuntimeError("slow")))

    result = ws._news("technology")
    assert result == long_answer


# ── Malformed / empty result formatting ─────────────────────────────────────

def test_format_ddg_handles_missing_fields_gracefully():
    results = [{"title": "Only a title"}, {"url": "http://only-url.example"}]
    out = ws._format_ddg("query", results)
    assert "Only a title" in out
    assert "http://only-url.example" in out


def test_format_news_skips_entries_without_title():
    results = [{"snippet": "no title here"}, {"title": "Has a title", "source": "src"}]
    out = ws._format_news("query", results)
    assert "Has a title" in out
    assert "no title here" not in out


# ── Public entry point ───────────────────────────────────────────────────────

def test_web_search_requires_query_or_items():
    result = ws.web_search({"query": "", "mode": "search"})
    assert "provide a search query" in result.lower()


def test_web_search_compare_mode_routes_correctly(monkeypatch):
    monkeypatch.setattr(ws, "_compare", lambda items, aspect: f"compared {items} on {aspect}")
    result = ws.web_search({"query": "", "items": ["A", "B"], "aspect": "price"})
    assert "compared ['A', 'B'] on price" == result
