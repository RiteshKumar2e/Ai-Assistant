"""
Tests for core/text_model.py — the Groq-first/Gemini-fallback chain.

All network calls are mocked; these tests never hit a real provider and never
read the real config/*.json files (each test points the module at its own
tmp_path files via monkeypatch).
"""
import json

import pytest

import core.text_model as tm


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Every test gets its own empty config/models files unless it writes
    its own — never touches the real config/api_keys.json or models.json."""
    cfg_path = tmp_path / "api_keys.json"
    models_path = tmp_path / "models.json"
    cfg_path.write_text(json.dumps({"groq_api_key": "fake-groq-key",
                                     "gemini_api_key": "fake-gemini-key"}), encoding="utf-8")
    models_path.write_text(json.dumps({
        "groq":   [{"id": "model-a"}, {"id": "model-b"}, {"id": "model-c"}],
        "gemini": [{"id": "gem-a"}, {"id": "gem-b"}],
    }), encoding="utf-8")
    monkeypatch.setattr(tm, "_CONFIG_PATH", cfg_path)
    monkeypatch.setattr(tm, "_MODELS_PATH", models_path)
    return cfg_path, models_path


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"{self.status_code} error")


def _groq_ok(content="ok"):
    return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})


# ── Config loading robustness ────────────────────────────────────────────────

def test_load_config_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "_CONFIG_PATH", tmp_path / "does_not_exist.json")
    assert tm._load_config() == {}


def test_load_config_malformed_json(tmp_path, monkeypatch):
    p = tmp_path / "broken.json"
    p.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(tm, "_CONFIG_PATH", p)
    assert tm._load_config() == {}


def test_load_config_non_dict_root(tmp_path, monkeypatch):
    """Valid JSON, wrong top-level type — must not crash every .get() caller."""
    p = tmp_path / "array.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(tm, "_CONFIG_PATH", p)
    assert tm._load_config() == {}
    assert tm._groq_api_key() == ""


def test_load_models_missing_file_uses_emergency(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "_MODELS_PATH", tmp_path / "gone.json")
    assert tm._load_models("groq", ["emergency-model"]) == ["emergency-model"]


def test_load_models_malformed_json_uses_emergency(tmp_path, monkeypatch):
    p = tmp_path / "broken_models.json"
    p.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(tm, "_MODELS_PATH", p)
    assert tm._load_models("groq", ["emergency-model"]) == ["emergency-model"]


def test_load_models_empty_provider_list_uses_emergency(tmp_path, monkeypatch):
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"groq": [], "gemini": [{"id": "g1"}]}), encoding="utf-8")
    monkeypatch.setattr(tm, "_MODELS_PATH", p)
    assert tm._load_models("groq", ["emergency-model"]) == ["emergency-model"]


def test_load_models_missing_provider_key_uses_emergency(tmp_path, monkeypatch):
    p = tmp_path / "no_groq_key.json"
    p.write_text(json.dumps({"gemini": [{"id": "g1"}]}), encoding="utf-8")
    monkeypatch.setattr(tm, "_MODELS_PATH", p)
    assert tm._load_models("groq", ["emergency-model"]) == ["emergency-model"]


def test_load_models_invalid_entries_filtered(tmp_path, monkeypatch):
    """Non-dict entries and dicts missing 'id' are dropped, not crashed on."""
    p = tmp_path / "mixed.json"
    p.write_text(json.dumps({
        "groq": ["not-a-dict", {"no_id_field": True}, {"id": "real-model"}, {"id": ""}],
    }), encoding="utf-8")
    monkeypatch.setattr(tm, "_MODELS_PATH", p)
    assert tm._load_models("groq", ["emergency-model"]) == ["real-model"]


def test_model_chain_deduplicates():
    cfg = {}
    chain = tm._model_chain(cfg, "groq_models", "groq_model", "dup", ["dup", "other", "dup"])
    assert chain == ["dup", "other"]


def test_model_chain_config_override_takes_priority():
    cfg = {"groq_models": ["override-1", "override-2"]}
    chain = tm._model_chain(cfg, "groq_models", "groq_model", None, ["default-1"])
    assert chain == ["override-1", "override-2"]


# ── Groq chain behavior ──────────────────────────────────────────────────────

def test_groq_generate_success_first_model(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        return _groq_ok("hello")

    monkeypatch.setattr(tm.requests, "post", fake_post)
    result = tm._groq_generate("hi", None)
    assert result == "hello"
    assert calls == ["model-a"]   # first model in chain, no fallback needed


def test_groq_generate_404_falls_through_to_next_model(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        if json["model"] == "model-a":
            return _FakeResponse(404, text="model retired")
        return _groq_ok("recovered")

    monkeypatch.setattr(tm.requests, "post", fake_post)
    result = tm._groq_generate("hi", None)
    assert result == "recovered"
    assert calls == ["model-a", "model-b"]


def test_groq_generate_429_falls_through(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        if json["model"] == "model-a":
            return _FakeResponse(429, text="rate limited")
        return _groq_ok("ok")

    monkeypatch.setattr(tm.requests, "post", fake_post)
    assert tm._groq_generate("hi", None) == "ok"
    assert calls == ["model-a", "model-b"]


def test_groq_generate_timeout_falls_through(monkeypatch):
    import requests as real_requests
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        if json["model"] == "model-a":
            raise real_requests.exceptions.Timeout("timed out")
        return _groq_ok("ok")

    monkeypatch.setattr(tm.requests, "post", fake_post)
    assert tm._groq_generate("hi", None) == "ok"
    assert calls == ["model-a", "model-b"]


def test_groq_generate_all_models_exhausted_raises(monkeypatch):
    def fake_post(url, headers, json, timeout):
        return _FakeResponse(500, text="down")

    monkeypatch.setattr(tm.requests, "post", fake_post)
    with pytest.raises(Exception):
        tm._groq_generate("hi", None)


def test_groq_generate_auth_error_short_circuits_chain(monkeypatch):
    """A 401 fails identically for every model under the same key — must not
    waste a call on every remaining model in the chain."""
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        return _FakeResponse(401, text="invalid key")

    monkeypatch.setattr(tm.requests, "post", fake_post)
    with pytest.raises(Exception):
        tm._groq_generate("hi", None)
    assert calls == ["model-a"]   # stopped after the first, did not try model-b/model-c


def test_groq_generate_no_api_key_raises_immediately(monkeypatch, tmp_path):
    p = tmp_path / "no_key.json"
    p.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(tm, "_CONFIG_PATH", p)
    with pytest.raises(RuntimeError):
        tm._groq_generate("hi", None)


def test_error_message_never_contains_api_key(monkeypatch):
    def fake_post(url, headers, json, timeout):
        return _FakeResponse(401, text="invalid key")

    monkeypatch.setattr(tm.requests, "post", fake_post)
    try:
        tm._groq_generate("hi", None)
    except Exception as e:
        assert "fake-groq-key" not in str(e)


# ── TextModel / get_text_model integration ──────────────────────────────────

def test_generate_content_falls_back_to_gemini_when_groq_exhausted(monkeypatch):
    def fake_post(url, headers, json, timeout):
        return _FakeResponse(500, text="groq down")

    class _FakeGeminiResponse:
        text = "gemini answered"

    class _FakeModels:
        def generate_content(self, model, contents):
            return _FakeGeminiResponse()

    class _FakeClient:
        def __init__(self, api_key):
            self.models = _FakeModels()

    fake_genai_module = type("FakeGenaiModule", (), {"Client": _FakeClient})

    monkeypatch.setattr(tm.requests, "post", fake_post)
    monkeypatch.setitem(__import__("sys").modules, "google.genai", fake_genai_module)
    import types as _types
    fake_google = _types.ModuleType("google")
    fake_google.genai = fake_genai_module
    monkeypatch.setitem(__import__("sys").modules, "google", fake_google)

    model = tm.get_text_model()
    result = model.generate_content("hi")
    assert result.text == "gemini answered"


def test_multimodal_content_skips_groq_entirely(monkeypatch):
    """A list containing a non-string element must never reach the Groq
    (text-only) path — Groq has no image support."""
    groq_called = []
    monkeypatch.setattr(tm, "_groq_generate", lambda *a, **k: groq_called.append(1))

    class _FakeGeminiResponse:
        text = "described the image"

    class _FakeModels:
        def generate_content(self, model, contents):
            return _FakeGeminiResponse()

    class _FakeClient:
        def __init__(self, api_key):
            self.models = _FakeModels()

    import types as _types
    fake_genai_module = _types.ModuleType("genai")
    fake_genai_module.Client = _FakeClient
    fake_google = _types.ModuleType("google")
    fake_google.genai = fake_genai_module
    monkeypatch.setitem(__import__("sys").modules, "google", fake_google)
    monkeypatch.setitem(__import__("sys").modules, "google.genai", fake_genai_module)

    model = tm.get_text_model()
    result = model.generate_content([object(), "some text"])
    assert result.text == "described the image"
    assert groq_called == []
