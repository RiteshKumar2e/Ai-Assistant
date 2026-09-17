"""
core/self_trainer.py — JUDO practising on itself while nobody is talking.

memory/self_training.py is PASSIVE: it only learns when a mistake actually
reaches the user — they say "undo", or a generated file crashes. That means
the user pays for every lesson.

This module makes the same loop ACTIVE. While the user is idle, JUDO sets
itself an exam it already knows the answer to:

  ROUTING DRILL   Pick one real tool. Ask the model to invent a user command
                  that should trigger it (so the correct answer is known by
                  construction). Then, in a SEPARATE stateless call with no
                  hint, show it the whole tool catalogue and ask which tool
                  that command means. A mismatch is a routing weakness the
                  user has not hit yet — logged as an understanding mistake,
                  which rides in the next session's system prompt.

  CODING DRILL    Invent a small task, generate code for it through the same
                  prompt shape code_helper uses (its own past lessons
                  included), then compile-check the result. A SyntaxError is
                  a typing/coding weakness — logged as a coding lesson.

TWO HARD RULES, both about not doing damage while unsupervised:

  1. Drills NEVER execute anything. The routing drill only asks "which tool
     would you pick" — it never calls the tool, so practice can never open an
     app, send a message or delete a file. The coding drill compiles, it does
     not run: self-written code is not executed without the user asking.
  2. Only failures are written. A passed drill is silent, so the lesson store
     stays a list of real weaknesses rather than a training transcript.

Cost: one drill per cycle, two short text calls, on the Groq path that
core/text_model.py already prefers (large free quota, fast). It never touches
the Gemini Live voice session, so it cannot slow down or interrupt a
conversation — and it only runs when there is no conversation.
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

from memory import self_training

_LANGUAGES = ["Hindi (Devanagari)", "Hinglish (Hindi written in Latin script)", "English"]


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _enabled() -> bool:
    """Off switch in config/api_keys.json, alongside morning_brief_enabled.
    Background drills spend API quota, so they must be switchable off."""
    try:
        cfg = json.loads((_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8"))
        return bool(cfg.get("self_training_enabled", True))
    except Exception:
        return True


def _ask(prompt: str) -> str:
    from core.text_model import get_text_model
    return (get_text_model().generate_content(prompt).text or "").strip()


# Self-training is optional background work — it should never be the thing
# that pushes an already-struggling machine further into a slowdown, or that
# competes with a real user request for CPU/RAM right when the machine needs
# it most. Thresholds are deliberately high (not the everyday 70-80% range
# a lightly loaded dev machine can sit at) so this only skips at genuinely
# critical load, not routine usage.
_CPU_SKIP_THRESHOLD = 90.0
_RAM_SKIP_THRESHOLD = 90.0


def _system_overloaded() -> bool:
    try:
        import psutil
        return (psutil.cpu_percent(interval=None) >= _CPU_SKIP_THRESHOLD
                or psutil.virtual_memory().percent >= _RAM_SKIP_THRESHOLD)
    except Exception:
        return False   # can't tell -> don't block drills over it


class SelfTrainer:
    """Decides WHEN to practise and runs one drill. Blocking — call run_drill()
    from a thread so the audio loop is never held up."""

    def __init__(self, min_idle_secs: int = 120, cooldown_secs: int = 300):
        self.min_idle_secs = min_idle_secs
        self.cooldown_secs = cooldown_secs
        self._last_run = 0.0
        self._rotation = 0
        self.passed = 0
        self.failed = 0

    def should_trigger(self, last_user_speech: float) -> bool:
        if not _enabled():
            return False
        if _system_overloaded():
            return False
        now = time.monotonic()
        return ((now - last_user_speech) >= self.min_idle_secs
                and (now - self._last_run) >= self.cooldown_secs)

    def mark_triggered(self) -> None:
        self._last_run = time.monotonic()
        self._rotation += 1

    def run_drill(self, tool_decls: list[dict]) -> str:
        """One drill. Returns a short line for the activity log, or "" if the
        drill could not run (no API key, model unreachable) — never raises, a
        failed practice session must not disturb a running assistant."""
        try:
            if self._rotation % 2 == 0 and tool_decls:
                return self._routing_drill(tool_decls)
            return self._coding_drill()
        except Exception as e:
            print(f"[SelfTrainer] drill skipped: {e}")
            return ""

    # ── Routing drill ────────────────────────────────────────────────────────

    def _routing_drill(self, tool_decls: list[dict]) -> str:
        usable = [t for t in tool_decls if t.get("name") and t.get("description")]
        if len(usable) < 2:
            return ""

        target = random.choice(usable)
        language = random.choice(_LANGUAGES)

        raw = _ask(
            "You are generating test data for a voice assistant's routing tests.\n"
            f"Tool name: {target['name']}\n"
            f"What it does: {target['description'][:400]}\n\n"
            f"Write ONE realistic thing a user would say out loud, in {language}, "
            "that should trigger exactly this tool. Everyday phrasing, not a "
            "command template. Reply with ONLY that sentence, nothing else."
        )
        lines = [l for l in raw.splitlines() if l.strip()]
        command = lines[0].strip().strip('"')[:200] if lines else ""
        if not command:
            return ""

        catalogue = "\n".join(f"{t['name']}: {t['description'][:110]}" for t in usable)
        reply = _ask(
            "You are the router of a voice assistant. These are the available tools:\n\n"
            f"{catalogue}\n\n"
            f"The user said: \"{command}\"\n\n"
            "Which ONE tool should run? Reply with ONLY the tool name, exactly as "
            "written above. If none fit, reply: none"
        )
        words = reply.strip().strip("`'\"., \n").split()
        answer = words[0] if words else ""

        expected = target["name"]
        if answer == expected:
            self.passed += 1
            return f"drill passed — \"{command[:60]}\" → {expected}"

        self.failed += 1
        self_training.log_understanding_mistake(
            command, f"{answer or 'nothing'} — the right tool is {expected}"
        )
        return f"drill FAILED — \"{command[:60]}\" → {answer or 'nothing'} (should be {expected}); lesson saved"

    # ── Coding drill ─────────────────────────────────────────────────────────

    def _coding_drill(self) -> str:
        raw = _ask(
            "Invent ONE small self-contained Python programming task that a "
            "single file under 40 lines can solve, using only the standard "
            "library. Reply with ONLY the task description in one sentence."
        )
        lines = [l for l in raw.splitlines() if l.strip()]
        task = lines[0].strip().strip('"')[:160] if lines else ""
        if not task:
            return ""

        lessons = self_training.get_coding_lessons("python")
        code = _ask(
            "You are an expert Python developer.\n"
            "Write clean, working, well-commented Python code for the task below.\n\n"
            "Rules:\n"
            "- Output ONLY the code. No explanation, no markdown, no backticks.\n"
            "- Handle errors and edge cases properly.\n\n"
            f"{lessons + chr(10) if lessons else ''}Task: {task}\n\nCode:"
        )
        code = code.removeprefix("```python").removeprefix("```").removesuffix("```").strip()
        if not code:
            return ""

        try:
            compile(code, "<self_drill>", "exec")
        except SyntaxError as e:
            self.failed += 1
            self_training.log_coding_lesson(
                "python", f"SyntaxError: {e.msg}",
                f"Generated code for '{task[:60]}' would not compile: {e.msg} (line {e.lineno}). "
                "Check syntax before returning generated code."
            )
            return f"code drill FAILED — SyntaxError: {e.msg}; lesson saved"

        self.passed += 1
        return f"code drill passed — \"{task[:60]}\""
