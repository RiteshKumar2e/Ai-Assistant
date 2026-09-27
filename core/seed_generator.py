"""
core/seed_generator.py — grow JUDO's exam with 1,00,000 real-sounding tasks.

core/training_corpus builds its 20,000 tasks from templates, so they all
sound alike. Real speech does not: typos from speech recognition, long
rambling sentences, indirect needs ("thand lag rahi hai"), languages mixed
mid-sentence. This module seeds two different model families with each tool
and a speaking style and has them write NEW tasks, then cross-checks every one:

  GENERATE  a model gets one tool (what it does, its actions, the details a
            correct call carries) and one style, and writes 20 things a user
            might say for it — each with the details a correct call must have.
  VERIFY    a model from the OTHER family (Gemini-written → Groq checks,
            Groq-written → Gemini checks) routes those sentences BLIND. A task
            is kept only when both agree on the tool and on every detail —
            disagreement means the sentence is ambiguous or the label is wrong,
            and a wrong label would teach JUDO a wrong lesson.

Kept tasks go to memory/seed_tasks.jsonl (append-only, resumable). The
routing trainer then examines JUDO on them: `python -m core.routing_trainer
--exam seed`.

Quota: generation and most checking run on Gemma (14,400 req/day). Groq is
used through qwen/gpt-oss-20b only — never gpt-oss-120b, JUDO's first-choice
model — and a Groq model is left alone for the day once it is down to
GROQ_RESERVE requests, so JUDO's own live actions always have quota.

CLI:
    python -m core.seed_generator                # grow to 1,00,000
    python -m core.seed_generator --target 5000  # stop earlier
    python -m core.seed_generator --status
"""
from __future__ import annotations

import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path
from threading import Lock

from core import routing_trainer as rt
from core import training_corpus as tc

STORE = rt._base_dir() / "memory" / "seed_tasks.jsonl"
TARGET = 100_000
PER_CALL = 20
GROQ_MODELS = ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"]   # never gpt-oss-120b: JUDO's live first choice
GROQ_RESERVE = 500                                          # requests/day always left for JUDO
GEMMA = ["gemma-4-26b-a4b-it", "gemma-4-31b-it"]

STYLES = [
    "casual Hinglish (Hindi in Latin letters mixed with English), the way friends talk",
    "pure Hindi written in Devanagari script",
    "plain everyday Indian English",
    "one long rambling sentence that gives some context or a reason BEFORE the actual request",
    "speech-recognition mistakes: misheard or misspelled words, missing punctuation (e.g. 'wtsapp', 'spotifi', 'yuutube')",
    "indirect — the user states a need or a situation instead of giving a command, but the right action is still clear",
    "language switching in the middle of the sentence (Hindi ↔ English ↔ Hinglish)",
    "with spoken fillers and hesitations (umm, acha, suno, matlab, arey yaar)",
    "very polite and formal (kripya, please, could you, aap)",
]

_lock = Lock()
_groq_left: dict[str, int] = {}
_groq_pause: dict[str, float] = {}   # model -> monotonic time its per-minute window reopens
_known: set[str] | None = None


def _norm_cmd(c: str) -> str:
    return re.sub(r"[\W_]+", " ", c.lower()).strip()


def load() -> list[dict]:
    if not STORE.exists():
        return []
    out = []
    for line in STORE.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _known_cmds() -> set[str]:
    global _known
    if _known is None:
        _known = {_norm_cmd(c) for c, _, _ in rt.corpus()} | {_norm_cmd(t["cmd"]) for t in load()}
    return _known


def _append(tasks: list[dict]) -> int:
    with _lock:
        known, fresh = _known_cmds(), []
        for t in tasks:
            key = _norm_cmd(t["cmd"])
            if key and key not in known:
                known.add(key)
                fresh.append(t)
        if fresh:
            STORE.parent.mkdir(parents=True, exist_ok=True)
            with STORE.open("a", encoding="utf-8") as f:
                for t in fresh:
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")
        return len(fresh)


# ── Providers ────────────────────────────────────────────────────────────────

def _gemma(prompt: str, model: str) -> str:
    from core.text_model import generate_bulk
    return generate_bulk(prompt, models=[model])


def _groq_model() -> str | None:
    now = time.monotonic()
    with _lock:
        ok = [m for m in GROQ_MODELS
              if _groq_left.get(m, GROQ_RESERVE + 1) > GROQ_RESERVE and _groq_pause.get(m, 0) <= now]
    return random.choice(ok) if ok else None


def _groq(prompt: str, model: str) -> str:
    import requests
    from core.text_model import _GROQ_URL, _groq_api_key
    r = requests.post(_GROQ_URL, headers={"Authorization": f"Bearer {_groq_api_key()}"},
                      json={"model": model, "messages": [{"role": "user", "content": prompt}]}, timeout=120)
    left = r.headers.get("x-ratelimit-remaining-requests")
    if left and left.isdigit():
        with _lock:
            _groq_left[model] = int(left)
    if r.status_code == 429:
        # Per-minute window full: rest this model until Groq says it reopens.
        # (The DAILY reserve is tracked separately, from remaining-requests.)
        wait = r.headers.get("retry-after", "")
        with _lock:
            _groq_pause[model] = time.monotonic() + (float(wait) if wait.replace(".", "").isdigit() else 60)
    r.raise_for_status()
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


def _ask(provider: str, model: str, prompt: str) -> str:
    return _groq(prompt, model) if provider == "groq" else _gemma(prompt, model)


# ── Generate ─────────────────────────────────────────────────────────────────

def _tool_brief(label: str, decls: dict) -> str:
    tool, _, action = label.partition(".")
    if tool == "none":
        return ("NO TOOL — small talk, thanks, greetings, jokes, opinions, or general-knowledge questions the "
                "assistant answers from its own knowledge (nothing needing live/current data, the computer, or files).")
    d = decls.get(tool, {})
    params = ", ".join(sorted({a for spec in tc.ARGS.get(label, {}) for a in spec.rstrip("?").split("|")}))
    brief = f"tool `{tool}`: {d.get('description', '')[:500]}"
    if action:
        brief += (f"\nONLY its action `{action}` — every sentence must ask for exactly `{action}` and "
                  f"nothing else this tool can do.")
    return brief + (f"\nDetails a correct call carries: {params}" if params else "\nNo details needed — args {}.")


def _objects(reply: str) -> list[dict]:
    """Every {"cmd": ...} object in a reply — one per line, a JSON array on a
    single line, or wrapped in a code fence all come out the same."""
    dec, out = json.JSONDecoder(), []
    for m in re.finditer(r'\{\s*["\']?cmd', reply):
        try:
            obj, _ = dec.raw_decode(reply, m.start())
        except ValueError:
            obj = rt._loose_obj(reply[m.start(): reply.find("\n", m.start()) % (len(reply) + 1)].rstrip(", ]"))
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _key_args(label: str, args: dict) -> dict:
    """Only the details that decide whether the task is done right (the same
    ones the template exam checks, tc.ARGS) — anything else a writer adds
    ("description": "awaaz") is noise the checker has no reason to repeat."""
    out = {}
    for spec in tc.ARGS.get(label, {}):
        vals = [str(args[a]).strip() for a in spec.rstrip("?").split("|") if str(args.get(a, "")).strip()]
        if vals:   # "path|name": folder + file together, however the call splits them
            out[spec.rstrip("?")] = " ".join(vals)
    return out


def generate(label: str, style: str, provider: str, model: str, decls: dict) -> list[dict]:
    examples = [c for c, l, _ in random.sample(rt.corpus(), 400) if l == label][:3]
    reply = _ask(provider, model, (
        "You write test data for JUDO, a Hindi/Hinglish/English voice assistant on a Windows PC in India.\n"
        f"Target: {_tool_brief(label, decls)}\n"
        f"Speaking style: {style}\n"
        + (f"Template examples (do NOT copy them, be far more varied): {examples}\n" if examples else "") +
        f"\nWrite {PER_CALL} DIFFERENT things a real user might say that must be handled by exactly this target"
        + (" and no other tool" if label != "none" else "") + ". Vary the details a lot (names, places, apps, "
        "files, numbers, times, topics) and make every one unambiguous. For each, give the arguments a correct "
        "call must contain, using the parameter names above, values exactly as the user said them (no invented "
        "paths or details). One JSON object per line, nothing else:\n"
        '{"cmd": "<what the user says>", "args": {"<param>": "<value>"}}'
    ))
    out = []
    for obj in _objects(reply):
        cmd = str(obj.get("cmd", "")).strip()
        args = obj.get("args") if isinstance(obj.get("args"), dict) else {}
        if 3 <= len(cmd) <= 300:
            out.append({"cmd": cmd, "label": label, "claimed": _key_args(label, args)})
    return out


# ── Verify (blind, other model family) ───────────────────────────────────────

def _compact_catalogue(decls: list[dict]) -> str:
    rows = []
    for d in decls:
        props = d.get("parameters", {}).get("properties", {})
        key = next((k for k in rt._SELECTORS if k in props), "")
        row = f"- {d['name']}: {d.get('description', '')[:220]}"
        if key:
            row += f" | {key}s: {(props[key].get('description') or '')[:260]}"
        others = [k for k in props if k != key]
        if others:
            row += f" | params: {', '.join(others)}"
        rows.append(row)
    return "\n".join(rows)


def verify(cands: list[dict], provider: str, model: str, decls: list[dict]) -> list[dict]:
    numbered = "\n".join(f"{i}. {c['cmd']}" for i, c in enumerate(cands, 1))
    reply = _ask(provider, model, (
        "You are the tool router of JUDO, a Hindi/Hinglish/English voice assistant on Windows.\n"
        f"Tools:\n{_compact_catalogue(decls)}\n"
        "- none: no tool — small talk, thanks, jokes, or general knowledge answered directly.\n\n"
        "For EACH numbered command, one line: the number, the tool (with `.action` when it has an "
        "actions/modes list), then the arguments as a JSON object, e.g.\n"
        '7 send_message {"receiver": "Rahul", "message_text": "call me back"}\n'
        "Values exactly as the user said them. No other text.\n\n" + numbered
    ))
    picks = rt._parse(reply, len(cands))
    kept = []
    for i, c in enumerate(cands, 1):
        if i not in picks:
            continue
        got_label, got_args = picks[i]
        if not rt._is_correct(c["label"], got_label):
            continue
        # A detail survives only if both models gave the same value for it.
        agreed = {}
        for k, v in c["claimed"].items():
            if rt._args_error({k: _spec(v)}, got_args) is None:
                agreed[k] = _spec(v)
        if len(agreed) < len(c["claimed"]):
            continue
        kept.append({"cmd": c["cmd"], "label": c["label"], "args": agreed})
    return kept


def _spec(v) -> str:
    """Expected-value spec for a claimed arg: numbers exact, text loose (the
    exam's router may legitimately rephrase a message or query)."""
    s = str(v).strip()
    return "#" + s if re.fullmatch(r"-?\d+(\.\d+)?", s) else "~" + s


# ── Driver ───────────────────────────────────────────────────────────────────

def _labels() -> list[str]:
    return list(tc.T)


def _next_label(counts: Counter, target: int) -> str | None:
    share = target / len(_labels())
    open_ = [l for l in _labels() if counts[l] < share]
    return min(open_, key=lambda l: (counts[l], random.random())) if open_ else None


def step(decls_list: list[dict], counts: Counter, target: int) -> tuple[int, str]:
    decls = {d["name"]: d for d in decls_list}
    label = _next_label(counts, target)
    if label is None:
        return 0, "done"
    style = random.choice(STYLES)
    groq = _groq_model()
    # Mostly Gemma writes and Groq checks; a quarter the other way round, for
    # a second writing voice. Without Groq quota, the two Gemma sizes pair up.
    if groq and random.random() < 0.25:
        gen, ver = ("groq", groq), ("gemma", random.choice(GEMMA))
    elif groq:
        gen, ver = ("gemma", random.choice(GEMMA)), ("groq", groq)
    else:
        a, b = random.sample(GEMMA, 2)
        gen, ver = ("gemma", a), ("gemma", b)
    cands = generate(label, style, *gen, decls)
    if not cands:
        return 0, f"{label}: generator returned nothing"
    kept = verify(cands, *ver, decls_list)
    for k in kept:
        k.update(gen=gen[1], ver=ver[1], style=style[:40])
    added = _append(kept)
    counts[label] += added
    return added, f"{label} [{style[:28]}] {gen[1]}→{ver[1]}: {len(cands)} written, {len(kept)} agreed, {added} new"


def run(target: int = TARGET, workers: int = 6, log=print) -> None:
    from concurrent.futures import ThreadPoolExecutor
    decls = rt.load_tool_decls()
    counts = Counter(t["label"] for t in load())
    stats = {"added": 0}

    def worker():
        fails = 0
        while sum(counts.values()) < target:
            try:
                added, msg = step(decls, counts, target)
            except Exception as e:
                fails += 1
                if fails >= 10:
                    log(f"[Seed] worker stopping after repeated failures ({str(e)[:120]}); rerun to resume.")
                    return
                if not rt._online():
                    time.sleep(60)
                    continue
                time.sleep(min(300, 15 * 2 ** (fails - 1)))
                continue
            if msg == "done":
                return
            fails = 0
            with _lock:
                stats["added"] += added
                log(f"[Seed] {sum(counts.values()):,}/{target:,}  {msg}")

    with ThreadPoolExecutor(workers) as pool:
        for _ in range(workers):
            pool.submit(worker)


def status() -> str:
    tasks = load()
    by_label = Counter(t["label"] for t in tasks)
    by_style = Counter(t.get("style", "?") for t in tasks)
    by_pair = Counter(f"{t.get('gen')}→{t.get('ver')}" for t in tasks)
    lines = [f"Seed tasks: {len(tasks):,} / {TARGET:,}  ({len(by_label)} labels)",
             "Thinnest labels: " + ", ".join(f"{l} {by_label[l]}" for l in sorted(_labels(), key=lambda l: by_label[l])[:6]),
             "By style: " + ", ".join(f"{s}… {n}" for s, n in by_style.most_common()),
             "Writer→checker: " + ", ".join(f"{p} {n}" for p, n in by_pair.most_common())]
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Grow JUDO's exam with cross-verified seed tasks")
    ap.add_argument("--target", type=int, default=TARGET)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        print(status())
    else:
        run(a.target, a.workers)
        print(status())
