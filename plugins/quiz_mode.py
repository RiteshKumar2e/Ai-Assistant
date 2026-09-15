"""
quiz_mode.py — Voice-driven quiz. Say a topic to start; JUDO asks questions
one at a time, checks each spoken answer, and keeps score. Questions are
generated locally via core.llm_client (the same Ollama/OpenAI-compatible
backend already used by code_helper/dev_agent) — no cloud call, no API key.

State is a single in-process quiz session (module-level dict): fine for a
single-user desktop assistant, and it persists for the app's lifetime since
plugin modules are imported once at startup.
"""
import difflib
import json
import re

_STATE = {"active": False, "topic": "", "items": [], "idx": 0, "score": 0}

_SYSTEM_PROMPT = (
    "You are a quiz question generator. Respond with ONLY a JSON array — no "
    "markdown fences, no commentary. Each element must be an object with "
    'exactly two string fields: "question" and "answer". Keep each answer '
    "short (a word or a few words)."
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _is_correct(user_answer: str, correct: str) -> bool:
    u, c = _norm(user_answer), _norm(correct)
    if not u or not c:
        return False
    if u == c or u in c or c in u:
        return True
    return difflib.SequenceMatcher(None, u, c).ratio() >= 0.6


def _extract_json_array(text: str) -> list:
    try:
        return json.loads(text)
    except Exception:
        pass
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    return []


def _generate_questions(topic: str, count: int, difficulty: str) -> list[dict]:
    from core.llm_client import call_llm_text

    prompt = (
        f"Create {count} quiz questions about '{topic}' at {difficulty} difficulty."
    )
    content = call_llm_text(prompt, system=_SYSTEM_PROMPT)
    raw = _extract_json_array(content)

    items = []
    for it in raw:
        if isinstance(it, dict) and it.get("question") and it.get("answer"):
            items.append({"question": str(it["question"]).strip(),
                           "answer": str(it["answer"]).strip()})
    return items[:count]


def _start(p: dict) -> str:
    topic = str(p.get("topic", "")).strip()
    if not topic:
        return "What topic would you like the quiz on?"
    try:
        count = max(1, min(int(p.get("count", 5) or 5), 15))
    except (TypeError, ValueError):
        count = 5
    difficulty = str(p.get("difficulty", "medium")).strip() or "medium"

    try:
        items = _generate_questions(topic, count, difficulty)
    except Exception as e:
        return (f"Sir, I couldn't reach the local LLM to generate quiz questions "
                f"({e}). Make sure Ollama (or your configured LLM server) is running.")

    if not items:
        return f"Sir, I couldn't generate quiz questions for '{topic}' — try a different topic."

    _STATE.update(active=True, topic=topic, items=items, idx=0, score=0)
    return (f"Quiz started on {topic} — {len(items)} questions. "
            f"Question 1: {items[0]['question']}")


def _answer(p: dict) -> str:
    if not _STATE["active"]:
        return "No quiz is currently running. Say 'start a quiz' on a topic first."

    user_answer = str(p.get("answer", "")).strip()
    items, idx = _STATE["items"], _STATE["idx"]
    current = items[idx]

    correct = _is_correct(user_answer, current["answer"])
    if correct:
        _STATE["score"] += 1
        feedback = "Correct!"
    else:
        feedback = f"Not quite — the answer was {current['answer']}."

    _STATE["idx"] += 1
    if _STATE["idx"] >= len(items):
        score, total = _STATE["score"], len(items)
        _STATE.update(active=False, items=[], idx=0)
        return f"{feedback} That's the quiz — final score: {score}/{total}."

    next_q = items[_STATE["idx"]]["question"]
    return f"{feedback} Question {_STATE['idx'] + 1}: {next_q}"


def _skip() -> str:
    if not _STATE["active"]:
        return "No quiz is currently running."
    items = _STATE["items"]
    _STATE["idx"] += 1
    if _STATE["idx"] >= len(items):
        score, total = _STATE["score"], len(items)
        _STATE.update(active=False, items=[], idx=0)
        return f"Skipped. That's the quiz — final score: {score}/{total}."
    next_q = items[_STATE["idx"]]["question"]
    return f"Skipped. Question {_STATE['idx'] + 1}: {next_q}"


def _stop() -> str:
    if not _STATE["active"]:
        return "No quiz is running."
    score, total = _STATE["score"], len(_STATE["items"])
    _STATE.update(active=False, topic="", items=[], idx=0, score=0)
    return f"Quiz stopped early — score: {score}/{total}."


PLUGIN = {
    "name": "quiz_mode",
    "description": (
        "Runs a voice-driven quiz: generates questions on a topic the user names, "
        "asks them one at a time, checks each spoken answer, and tracks score. Use "
        "'start' when the user asks to be quizzed/tested on a topic (e.g. 'quiz me "
        "on world capitals'), 'answer' every time the user responds to the current "
        "question, 'skip' if they want to skip a question, and 'stop' to end early."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "start | answer | skip | stop"},
            "topic": {"type": "STRING", "description": "Quiz topic, required for 'start'"},
            "count": {"type": "NUMBER", "description": "Number of questions, default 5, max 15"},
            "difficulty": {"type": "STRING", "description": "easy | medium | hard (default medium)"},
            "answer": {"type": "STRING", "description": "The user's spoken answer, for action 'answer'"},
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "")).strip().lower()
    try:
        if action == "start":
            result = _start(p)
        elif action == "answer":
            result = _answer(p)
        elif action == "skip":
            result = _skip()
        elif action == "stop":
            result = _stop()
        else:
            return f"Unknown quiz action: '{action}'. Use start, answer, skip or stop."
    except Exception as e:
        return f"Sir, the quiz plugin failed: {e}"

    if player:
        try:
            player.write_log(f"[quiz] {result}")
        except Exception:
            pass
    return result
