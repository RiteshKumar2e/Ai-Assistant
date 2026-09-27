"""core/seed_generator.py — cross-verified seed tasks. All model calls mocked."""
import json

import pytest

from core import seed_generator as sg

DECLS = [{"name": "send_message", "description": "Sends a chat message.",
          "parameters": {"properties": {"receiver": {}, "message_text": {}, "platform": {}}}},
         {"name": "open_app", "description": "Opens apps.", "parameters": {"properties": {"app_name": {}}}}]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "STORE", tmp_path / "seed_tasks.jsonl")
    monkeypatch.setattr(sg, "_known", set())
    monkeypatch.setattr(sg, "_groq_left", {})


def test_generate_reads_tasks_and_drops_selector_args(monkeypatch):
    monkeypatch.setattr(sg, "_ask", lambda *a: '[{"cmd": "Rahul ko bol do late hoon", "args": '
                                               '{"receiver": "Rahul", "message_text": "late hoon", "action": "x"}}]')
    out = sg.generate("send_message", sg.STYLES[0], "gemma", "m", {d["name"]: d for d in DECLS})
    assert out == [{"cmd": "Rahul ko bol do late hoon", "label": "send_message",
                    "claimed": {"receiver": "Rahul", "message_text": "late hoon"}}]


def test_verify_keeps_only_tasks_both_models_agree_on(monkeypatch):
    cands = [{"cmd": "a", "label": "send_message", "claimed": {"receiver": "Rahul", "message_text": "late hoon"}},
             {"cmd": "b", "label": "send_message", "claimed": {"receiver": "Rahul"}},
             {"cmd": "c", "label": "send_message", "claimed": {"receiver": "Priya"}}]
    monkeypatch.setattr(sg, "_ask", lambda *a: '1 send_message {"receiver": "rahul", "message_text": "late hoon!"}\n'
                                               '2 open_app {"app_name": "Rahul"}\n'
                                               '3 send_message {"receiver": "Neha"}')
    kept = sg.verify(cands, "groq", "m", DECLS)
    assert kept == [{"cmd": "a", "label": "send_message",
                     "args": {"receiver": "~Rahul", "message_text": "~late hoon"}}]


def test_append_skips_duplicates_and_template_commands(monkeypatch):
    monkeypatch.setattr(sg, "_known", {"already here"})
    added = sg._append([{"cmd": "Already, here!", "label": "none", "args": {}},
                        {"cmd": "brand new", "label": "none", "args": {}},
                        {"cmd": "BRAND new", "label": "none", "args": {}}])
    assert added == 1
    assert [json.loads(l)["cmd"] for l in sg.STORE.read_text(encoding="utf-8").splitlines()] == ["brand new"]


def test_groq_is_left_alone_once_every_key_is_down_to_the_reserve(monkeypatch):
    monkeypatch.setattr(sg, "_groq_keys", lambda: ["k1", "k2"])
    sg._groq_left.update({(k, m): sg.GROQ_RESERVE for k in ("k1", "k2") for m in sg.GROQ_MODELS})
    assert sg._groq_model() is None
    sg._groq_left[("k2", sg.GROQ_MODELS[0])] = sg.GROQ_RESERVE + 50
    assert sg._groq_model() == sg.GROQ_MODELS[0]
    assert "openai/gpt-oss-120b" not in sg.GROQ_MODELS   # JUDO's own first-choice model is never spent


def test_step_without_groq_pairs_the_two_gemma_models(monkeypatch):
    monkeypatch.setattr(sg, "_groq_keys", lambda: ["k1"])
    sg._groq_left.update({("k1", m): 0 for m in sg.GROQ_MODELS})
    calls = []

    def ask(provider, model, prompt):
        calls.append((provider, model))
        if len(calls) == 1:
            return '{"cmd": "Notion kholo", "args": {"app_name": "Notion"}}'
        return '1 open_app {"app_name": "Notion"}'

    monkeypatch.setattr(sg, "_ask", ask)
    monkeypatch.setattr(sg, "_next_label", lambda counts, target: "open_app")
    from collections import Counter
    added, msg = sg.step(DECLS, Counter(), 100)
    assert added == 1 and {p for p, _ in calls} == {"gemma"} and calls[0][1] != calls[1][1]


def test_spec_numbers_exact_text_loose():
    assert sg._spec(75) == "#75" and sg._spec("Rahul") == "~Rahul"


def test_key_args_keep_only_graded_details_and_join_split_paths():
    assert sg._key_args("computer_settings.full_screen", {"description": "awaaz", "value": "on"}) == {}
    assert sg._key_args("file_controller.copy", {"path": "C:/Downloads", "name": "movie.mp4", "destination": "D:/"}) ==         {"path|name": "C:/Downloads movie.mp4", "destination": "D:/"}


def test_parallel_workers_spread_over_labels_and_dry_labels_are_skipped(monkeypatch):
    from collections import Counter
    monkeypatch.setattr(sg, "_labels", lambda: ["a", "b", "c"])
    monkeypatch.setattr(sg, "_busy", Counter())
    monkeypatch.setattr(sg, "_dry", Counter({"c": 3}))
    picks = {sg._next_label(Counter(), 300) for _ in range(2)}
    assert picks == {"a", "b"}          # second worker avoids the label already in flight; dry "c" never picked


class _Resp:
    def __init__(self, code, left="900", text="ok"):
        self.status_code, self.headers, self._text = code, {"x-ratelimit-remaining-requests": left}, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return {"choices": [{"message": {"content": self._text}}]}


def test_groq_falls_through_to_the_next_key(monkeypatch):
    import requests
    monkeypatch.setattr(sg, "_groq_keys", lambda: ["dead", "limited", "good"])
    codes = {"dead": 401, "limited": 429, "good": 200}
    used = []

    def post(url, headers, **kw):
        key = headers["Authorization"].split()[-1]
        used.append(key)
        return _Resp(codes[key], text=f"from {key}")

    monkeypatch.setattr(requests, "post", post)
    assert sg._groq("hi", sg.GROQ_MODELS[0]) == "from good"
    used.clear()
    assert sg._groq("hi", sg.GROQ_MODELS[0]) == "from good" and used == ["good"]   # dead + resting keys skipped
