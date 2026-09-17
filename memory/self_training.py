"""
memory/self_training.py — JUDO's own mistake log.

Two kinds of mistake feed this, both already visible elsewhere in the code
and previously thrown away the moment they happened:

  1. UNDERSTANDING — the user says "undo" / "wrong" right after a tool call.
     core/undo.py already reverses the action; this module additionally
     remembers *what was misread* so the same phrasing does not fool the
     model twice.
  2. CODING — code_helper/dev_agent's build-fix loop already retries until a
     generated file runs clean. The error that triggered a fix is a free
     lesson; this module keeps it so the next generation for that language
     starts already knowing the pitfall instead of rediscovering it.

This is prompt-level adaptation, not model training: mistakes are logged,
deduplicated, capped, and the newest few are injected into the next prompt.

Entries are stored NEWEST FIRST, so readers slice from the front and never
have to sort — several mistakes inside one second would otherwise be
indistinguishable by timestamp.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from threading import Lock


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STORE_PATH = _base_dir() / "memory" / "self_training.json"
_lock = Lock()

MAX_UNDERSTANDING = 25
MAX_CODING        = 40
MAX_PROMPT_ITEMS  = 20


def _empty() -> dict:
    return {"understanding": [], "coding": []}


def _sanitize_section(value) -> list[dict]:
    """A hand-edited or partially-corrupted store can have a section that
    isn't a list, or a list containing non-dict junk — every reader below
    does e.get(...)/e[...] on each entry, so one bad entry would otherwise
    crash prompt-building (format_lessons_for_prompt runs on every session
    start) rather than just losing that one lesson."""
    if not isinstance(value, list):
        return []
    return [e for e in value if isinstance(e, dict)]


def _read() -> dict:
    """Unlocked — callers hold _lock."""
    if not STORE_PATH.exists():
        return _empty()
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty()
        return {
            "understanding": _sanitize_section(data.get("understanding")),
            "coding":        _sanitize_section(data.get("coding")),
        }
    except Exception as e:
        print(f"[SelfTraining] load error: {e}")
        return _empty()


def _write(data: dict) -> None:
    """Unlocked — callers hold _lock. Writes to a temp file first so a crash
    mid-write leaves the previous store intact rather than a truncated one.
    A failure here (e.g. disk full) is swallowed on purpose: losing one new
    lesson is fine, but log_understanding_mistake/log_coding_lesson must
    never raise into a caller like undo's tool dispatch, where it would turn
    an action that actually succeeded into a reported failure."""
    try:
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STORE_PATH)
    except OSError as e:
        print(f"[SelfTraining] Could not save lesson store ({e}) — this lesson will not persist.")


def _prepend(section: str, entry: dict, dedupe_keys: tuple[str, ...], cap: int) -> None:
    """Read-modify-write under one lock — two threads logging at once would
    otherwise each load the same store and the second save would drop the
    first's entry."""
    with _lock:
        data = _read()
        entries = [e for e in data.get(section, [])
                   if any(e.get(k) != entry.get(k) for k in dedupe_keys)]
        entries.insert(0, entry)
        data[section] = entries[:cap]
        _write(data)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _recent(section: str, limit: int) -> list[dict]:
    limit = max(1, min(int(limit), MAX_PROMPT_ITEMS))
    with _lock:
        return _read().get(section, [])[:limit]


def log_understanding_mistake(command: str, wrong_action: str) -> None:
    """Called right after `undo` fires: `command` is what the user said,
    `wrong_action` is the tool call that turned out to be the wrong read of it."""
    command      = (command or "").strip()[:200]
    wrong_action = (wrong_action or "").strip()[:150]
    if not command or not wrong_action:
        return
    _prepend(
        "understanding",
        {"date": _now(), "command": command, "wrong_action": wrong_action},
        ("command", "wrong_action"),
        MAX_UNDERSTANDING,
    )
    print(f"[SelfTraining] learned: \"{command}\" is not \"{wrong_action}\"")


def log_coding_lesson(language: str, error_signature: str, lesson: str) -> None:
    """Called when a build/fix loop succeeds after at least one failed attempt."""
    language        = (language or "python").strip().lower()
    error_signature = (error_signature or "").strip()[:120]
    lesson          = (lesson or "").strip()[:200]
    if not error_signature or not lesson:
        return
    _prepend(
        "coding",
        {"date": _now(), "language": language,
         "error_signature": error_signature, "lesson": lesson},
        ("language", "error_signature"),
        MAX_CODING,
    )
    print(f"[SelfTraining] {language} lesson learned: {lesson}")


def get_coding_lessons(language: str, limit: int = 5) -> str:
    """Short bullet block for code-generation prompts, most recent first."""
    language = (language or "python").strip().lower()
    limit    = max(1, min(int(limit), MAX_PROMPT_ITEMS))
    with _lock:
        entries = [e for e in _read().get("coding", []) if e.get("language") == language][:limit]
    lines = [f"- {e['lesson']}" for e in entries if e.get("lesson")]
    if not lines:
        return ""
    return "Pitfalls learned from past mistakes in this language — avoid repeating them:\n" + "\n".join(lines)


def format_lessons_for_prompt(limit: int = 5) -> str:
    """Small block for the main system prompt: recent misunderstandings to avoid."""
    entries = _recent("understanding", limit)
    lines = [f'  - "{e["command"]}" is NOT {e["wrong_action"]} — the user corrected this before.'
             for e in entries if e.get("command") and e.get("wrong_action")]
    if not lines:
        return ""
    return ("[MISTAKES TO AVOID — learned from past corrections, do not repeat]\n"
            + "\n".join(lines) + "\n")
