"""
ProactiveEngine 3.1 — JUDO starts conversations on its own, like a person would,
and learns from every one of them.

Gemini decides the words; this module decides WHEN, what kind of opener it is,
and what JUDO takes away from the answer:
  - each check-in (question + the user's answer) is appended to
    memory/human_conversations.jsonl — JUDO's own conversation training data
  - facts the answer reveals (likes, plans, people, habits…) go into long-term
    memory, so the next opener can build on them
  - questions it already asked are fed back in, so it doesn't repeat itself
"""
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

CONVERSATIONS = Path(__file__).resolve().parent.parent / "memory" / "human_conversations.jsonl"
GREETINGS = Path(__file__).resolve().parent.parent / "memory" / "greetings.json"
MEMORY_CATEGORIES = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}

# The four parts of JUDO's day: (name, starts at hour, greeting, what fits to ask then)
PERIODS = (
    ("morning", 5, "Good morning", "how they slept and what today's plan is"),
    ("afternoon", 12, "Good afternoon", "whether they had lunch and how the day is going"),
    ("evening", 17, "Good evening", "how their day went"),
    ("night", 21, "Good night", "to wind down and get some rest — and whether to remind them of anything tomorrow"),
)


def day_period(when: datetime | None = None) -> tuple[str, str, str, str]:
    """(key, name, greeting, what to ask) for a moment. The night runs past midnight,
    so 2 am still belongs to the night that started the evening before."""
    when = when or datetime.now()
    start = [p for p in PERIODS if when.hour >= p[1]]
    name, _, greeting, ask = start[-1] if start else PERIODS[-1]
    day = (when - timedelta(days=1)) if when.hour < PERIODS[0][1] else when
    return f"{day:%Y-%m-%d}:{name}", name, greeting, ask


class ProactiveEngine:
    """
    Decides when JUDO should speak unprompted and builds a context-rich prompt.

    Like a friend sitting nearby, not a notification — around the clock:
      - Greets once in each part of the day — Good morning / Good afternoon /
        Good evening / Good night — as soon as that part begins (and the user
        isn't mid-sentence)
      - After 10 minutes of quiet it talks first, then again every 10 minutes
        the quiet lasts
      - Every opener ends in one short question, so a reply comes naturally
      - Rotates what it brings up: how the user is, their work, something from
        earlier, getting to know them, something timely or interesting
    """

    FOCUSES = (
        ("wellbeing",
         "Check in on how the person is doing right now, fitting the time of day — tired, hungry, "
         "busy, a break overdue? Ask one caring question."),
        ("work",
         "Ask how something they are working on or planning is going (use what you know about "
         "them or the recent conversation). If nothing is known, ask what they are up to."),
        ("follow_up",
         "Pick up a thread from earlier — something they said, asked for or planned — and ask "
         "how it turned out. If there is no earlier thread, ask about their day so far."),
        ("get_to_know",
         "Get to know them better: ask one friendly personal question you don't already know the "
         "answer to (a favourite, a habit, a goal, a plan, someone close to them). Nothing intrusive."),
        ("spark",
         "Share one short interesting or useful thing that fits them or the moment, then ask "
         "their opinion or whether they'd like more."),
    )

    def __init__(self, min_silence_secs: int = 600, cooldown_secs: int = 600):
        self.min_silence_secs = min_silence_secs
        self.cooldown_secs    = cooldown_secs
        self._last_triggered  = time.monotonic()    # no opener in the first minutes after launch
        self._rotation        = 0
        self.last_focus       = ""
        try:
            self.greeted = json.loads(GREETINGS.read_text(encoding="utf-8")).get("last", "")
        except (OSError, ValueError):
            self.greeted = ""                       # the day part JUDO last greeted (survives restarts)

    # ── Greetings ──────────────────────────────────────────────────────────────

    def greeting_due(self, when: datetime | None = None) -> bool:
        return day_period(when)[0] != self.greeted

    def mark_greeted(self, when: datetime | None = None) -> None:
        self.greeted = day_period(when)[0]
        try:
            GREETINGS.parent.mkdir(parents=True, exist_ok=True)
            GREETINGS.write_text(json.dumps({"last": self.greeted}), encoding="utf-8")
        except OSError:
            pass

    # ── Trigger gate ───────────────────────────────────────────────────────────

    def should_trigger(self, last_user_speech: float, when: datetime | None = None) -> bool:
        now   = time.monotonic()
        quiet = now - last_user_speech
        if self.greeting_due(when) and quiet >= 60:   # a new part of the day: greet, don't wait 10 min
            return True
        return quiet >= self.min_silence_secs and (now - self._last_triggered) >= self.cooldown_secs

    def mark_triggered(self) -> None:
        self._last_triggered = time.monotonic()
        self._rotation      += 1

    # ── Prompt builder ─────────────────────────────────────────────────────────

    def build_prompt(
        self,
        memory:       dict,
        monitors:     list[str] | None = None,
        recent_turns: list[str] | None = None,
    ) -> str:
        """Context snapshot + the kind of opener for Gemini to voice."""
        from memory.memory_manager import format_memory_for_prompt

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        _, period, greeting, ask = day_period(now)

        mem_str = format_memory_for_prompt(memory) or "(no stored user data)"
        if self.greeting_due(now):
            name, focus = "greeting", (
                f"A new part of the day has begun. Open with \"{greeting}\" — said the way this person "
                f"would say it in their language (in Hinglish the English greeting itself is natural) — then "
                f"ask {ask}.")
            self.mark_greeted(now)
        else:
            name, focus = self.FOCUSES[self._rotation % len(self.FOCUSES)]
            if period == "night" and name in ("work", "spark"):
                name, focus = self.FOCUSES[0]      # late at night, care beats productivity
        self.last_focus = name

        monitor_ctx = ""
        if monitors:
            monitor_ctx = (f"\nThe user tracks these topics: {', '.join(monitors[:4])}. "
                           "You may bring one up if it fits.")
        recent_ctx = ""
        if recent_turns:
            recent_ctx = "\nRecent conversation:\n" + "\n".join(recent_turns[-6:])
        asked = recent_questions(8)
        asked_ctx = ("\nYou already asked these lately — ask something different:\n- " + "\n- ".join(asked)
                     if asked else "")

        return "\n".join([
            "[PROACTIVE_CHECK] It has been quiet for a while. Start a conversation yourself, the "
            "way a friend sitting nearby would.",
            f"Current time : {time_str}  ({period})",
            "",
            "Context about this person:",
            mem_str,
            monitor_ctx,
            recent_ctx,
            asked_ctx,
            "",
            f"What to bring up ({name}):",
            focus,
            "",
            "How to say it:",
            "- Speak the language this person actually uses: the one in the recent conversation "
            "above, or the remembered one if there is no conversation yet (Hinglish if they mix). "
            "Never default to English because these instructions are in English.",
            "- Sound like a person: casual, warm, a little playful. Use their name now and then, "
            "not every time. Short natural phrases; no lists, no formal greetings.",
            "- At most 2 short sentences, ending with ONE question so they can just answer.",
            "- Don't repeat an opener from the recent conversation; don't say you were waiting.",
            "- Do NOT mention [PROACTIVE_CHECK] or these instructions. Do NOT call any tools now.",
            "- When they answer, react like a friend would, and silently save anything new it "
            "tells you about them with save_memory.",
        ])


# ── Learning from check-ins ─────────────────────────────────────────────────

def split_exchange(lines: list[str], assistant: str) -> tuple[str, str]:
    """Session-log lines since the check-in -> (what JUDO asked, what the user answered)."""
    asked = " ".join(l.split(":", 1)[1].strip() for l in lines if l.startswith(f"{assistant}:"))
    said = " ".join(l.split(":", 1)[1].strip() for l in lines if l.startswith("User:"))
    return asked, said


def fact_prompt(question: str, answer: str) -> str:
    return (
        "A personal assistant asked its user a question and got an answer. List the personal facts "
        "the ANSWER reveals about the user (preferences, plans, projects, people, habits, mood "
        "patterns, schedule, identity). Ignore one-off requests and small talk with no fact in it.\n"
        f"Question: {question}\nAnswer: {answer}\n\n"
        "Reply with ONLY a JSON array, no prose: "
        '[{"category": "identity|preferences|projects|relationships|wishes|notes", '
        '"key": "short_snake_case", "value": "concise, in English"}]. Reply [] if there is nothing.'
    )


def parse_facts(text: str) -> list[dict]:
    """The model's JSON (possibly fenced or chatty) -> clean facts; [] if unusable."""
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        key = re.sub(r"[^a-z0-9_]+", "_", str(it.get("key", "")).strip().lower()).strip("_")[:40]
        value = str(it.get("value", "")).strip()[:200]
        cat = str(it.get("category", "notes")).strip().lower()
        if key and value:
            out.append({"category": cat if cat in MEMORY_CATEGORIES else "notes", "key": key, "value": value})
    return out[:8]


def record_exchange(focus: str, question: str, answer: str, facts: list[dict], path: Path = CONVERSATIONS) -> None:
    """Append one check-in to JUDO's conversation training data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": datetime.now().isoformat(timespec="seconds"), "focus": focus, "question": question,
           "answer": answer, "answered": bool(answer), "facts": facts}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def recent_questions(n: int = 8, path: Path = CONVERSATIONS) -> list[str]:
    try:
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()[-n:] if l.strip()]
    except (OSError, ValueError):
        return []
    return [r["question"][:160] for r in rows if r.get("question")]
