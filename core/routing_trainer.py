"""
core/routing_trainer.py — JUDO sits its 20,000-question routing exam and
learns from every wrong answer.

The exam is core/training_corpus (spoken commands with a known right tool).
Each batch shows a text model the SAME tool catalogue the live voice session
gets, plus the lessons learned so far, and asks it to route 50 commands at
once. Every wrong pick is recorded as a confusion ("expected X, picked Y")
with example phrasings; the most frequent confusions become the
[ROUTING LESSONS] block that main.py puts in the live system prompt. Items
it got wrong are re-asked in later batches — when they start passing, that
confusion's count drops and the lesson fades out on its own.

This is prompt-level learning, not weight training: Gemini Live cannot be
fine-tuned, but it follows concrete "this phrase → this tool" examples very
well, and those are exactly what the exam produces.

Like core/self_trainer, nothing here ever EXECUTES a tool — it only asks
"which tool would you pick".

CLI (run from the project folder):
    python -m core.routing_trainer              # full pass over all 20,000
    python -m core.routing_trainer --limit 2000 # just the next 2,000
    python -m core.routing_trainer --status     # accuracy + top confusions
    python -m core.routing_trainer --reset      # start over
"""
from __future__ import annotations

import ast
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Lock

from core import training_corpus


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STATE_PATH = _base_dir() / "memory" / "routing_training.json"
BATCH_SIZE = 50
MAX_RETEST = 3000
_lock = Lock()
_inflight: set[int] = set()   # items a parallel worker is routing right now
_corpus: list[tuple[str, str]] | None = None
_SELECTORS = ("action", "mode")   # the parameter that picks what a tool does (web_search uses mode)
_LINE_RE = re.compile(r"^\W*(\d+)\W+`?([a-z_]+)(?:\.([a-z_]+))?", re.I)


def corpus() -> list[tuple[str, str]]:
    global _corpus
    if _corpus is None:
        _corpus = training_corpus.build()
    return _corpus


# ── State ────────────────────────────────────────────────────────────────────

def _fresh() -> dict:
    return {"fingerprint": training_corpus.fingerprint(corpus()), "cursor": 0, "round": 1,
            "seen": 0, "correct": 0, "labels": {}, "confusions": {}, "wrong": {}, "history": []}


def _read() -> dict:
    try:
        s = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(s, dict) and s.get("fingerprint") == training_corpus.fingerprint(corpus()):
            return s
    except Exception:
        pass
    return _fresh()   # missing, corrupt, or the corpus changed → old indexes are meaningless


def _write(s: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_PATH)
    except OSError as e:
        print(f"[RoutingTrainer] could not save progress: {e}")


def reset() -> None:
    with _lock:
        _write(_fresh())


# ── Tool catalogue ───────────────────────────────────────────────────────────

def load_tool_decls() -> list[dict]:
    """Same declarations the live session sends: main.py's inline tools plus
    every auto-discovered action and plugin. Parses main.py rather than
    importing it, so the CLI does not start audio or the UI."""
    from core.action_loader import discover_actions
    from core.plugin_loader import discover_plugins
    base, quiet = _base_dir(), (lambda _m: None)
    inline: list[dict] = []
    try:
        tree = ast.parse((base / "main.py").read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.Assign)
                    and getattr(n.targets[0], "id", "") == "TOOL_DECLARATIONS")
        inline = ast.literal_eval(node.value)
    except Exception as e:
        print(f"[RoutingTrainer] inline tools unavailable ({e})")
    names = {d["name"] for d in inline}
    acts = discover_actions(base / "actions", names, quiet).get_tool_declarations()
    names |= {d["name"] for d in acts}
    return inline + acts + discover_plugins(base / "plugins", names, quiet).get_tool_declarations()


def _catalogue(decls: list[dict]) -> str:
    rows = []
    for d in decls:
        if not d.get("name"):
            continue
        row = f"- {d['name']}: {d.get('description', '')[:600]}"
        props = d.get("parameters", {}).get("properties", {})
        key = next((k for k in _SELECTORS if k in props), "")
        if key:
            row += f"\n    {key}s: {(props[key].get('description') or '')[:420]}"
        rows.append(row)
    return "\n".join(rows)


# ── Scoring ──────────────────────────────────────────────────────────────────

def _is_correct(expected: str, got: str) -> bool:
    if got in training_corpus.EQUIVALENT.get(expected, ()):
        return True
    e_tool, _, e_act = expected.partition(".")
    g_tool, _, g_act = got.partition(".")
    return e_tool == g_tool and (not e_act or e_act == g_act)


def _parse(reply: str, n: int) -> dict[int, str]:
    picks: dict[int, str] = {}
    for line in reply.splitlines():
        m = _LINE_RE.match(line.strip())
        if m and 1 <= int(m.group(1)) <= n:
            picks[int(m.group(1))] = (m.group(2) + (f".{m.group(3)}" if m.group(3) else "")).lower()
    return picks


def _ask(prompt: str) -> str:
    from core.text_model import generate_bulk
    return generate_bulk(prompt)


def run_batch(decls: list[dict], size: int = BATCH_SIZE) -> dict:
    """Route one batch and fold the results into the state. Raises if the
    model is unreachable — callers decide whether to back off or give up."""
    items = corpus()
    with _lock:
        s = _read()
        retest = sorted((int(k) for k in s["wrong"] if int(k) not in _inflight),
                        key=lambda k: (k < s["cursor"], k))[: size // 3]
        fresh, cursor = [], s["cursor"]
        while len(fresh) < size - len(retest):
            if cursor >= len(items):
                cursor = 0
                s["round"] += 1
            fresh.append(cursor)
            cursor += 1
        # Reserve the slice now so a parallel worker never takes the same items.
        s["cursor"] = cursor
        _write(s)
        batch = retest + fresh
        _inflight.update(batch)
    try:
        return _route(decls, batch)
    except Exception:
        # Nothing was learned from this batch — queue its new items for a
        # retry ("" = not answered yet) instead of silently skipping them.
        with _lock:
            s = _read()
            for k in fresh:
                s["wrong"].setdefault(str(k), "")
            _write(s)
        raise
    finally:
        with _lock:
            _inflight.difference_update(batch)


def _route(decls: list[dict], batch: list[int]) -> dict:
    items = corpus()
    lessons = format_routing_lessons(limit=25)
    numbered = "\n".join(f"{i}. {items[k][0]}" for i, k in enumerate(batch, 1))
    reply = _ask(
        "You are the tool router of JUDO, a Hindi/Hinglish/English voice assistant on Windows.\n"
        f"Available tools:\n{_catalogue(decls)}\n"
        "- none: no tool — small talk, thanks, greetings, jokes, or general knowledge you can answer yourself.\n\n"
        f"{lessons}\n"
        "For EACH numbered command below, reply with exactly one line: `<number> <tool>` or, when the tool "
        "has an actions/modes list, `<number> <tool>.<action>` using a name from that list. No other text.\n\n"
        f"{numbered}"
    )
    picks = _parse(reply, len(batch))
    if len(picks) < len(batch) // 2:
        raise RuntimeError(f"unparseable router reply ({len(picks)}/{len(batch)} lines)")

    ok = wrong = 0
    with _lock:
        s = _read()
        for i, k in enumerate(batch, 1):
            if i not in picks:
                s["wrong"].setdefault(str(k), "")
                continue
            cmd, expected = items[k]
            got = picks[i]
            lab = s["labels"].setdefault(expected, [0, 0])
            lab[0] += 1
            s["seen"] += 1
            if _is_correct(expected, got):
                ok += 1
                lab[1] += 1
                s["correct"] += 1
                key = s["wrong"].pop(str(k), None)
                if key in s["confusions"]:   # a past mistake now answered right → its lesson weakens
                    c = s["confusions"][key]
                    c["n"] -= 1
                    c["ex"] = [e for e in c["ex"] if e != cmd]
            else:
                wrong += 1
                key = f"{expected}|{got}"
                old = s["wrong"].get(str(k))
                if old != key:
                    if old in s["confusions"]:
                        s["confusions"][old]["n"] -= 1
                    c = s["confusions"].setdefault(key, {"n": 0, "ex": []})
                    c["n"] += 1
                    c["ex"] = ([cmd] + [e for e in c["ex"] if e != cmd])[:3]
                    if len(s["wrong"]) < MAX_RETEST or old is not None:
                        s["wrong"][str(k)] = key
        s["confusions"] = {k: v for k, v in s["confusions"].items() if v["n"] > 0}
        s["history"] = (s["history"] + [[datetime.now().isoformat(timespec="seconds"), ok, ok + wrong]])[-2000:]
        _write(s)
    return {"ok": ok, "total": ok + wrong, "seen": s["seen"], "correct": s["correct"], "round": s["round"]}


# ── What the live session reads ──────────────────────────────────────────────

def _pretty(label: str) -> str:
    tool, _, act = label.partition(".")
    return f"{tool} (action={act})" if act else tool


def format_routing_lessons(limit: int = 15) -> str:
    """Top recurring confusions as concrete phrase → tool examples, for the
    system prompt. Empty until the exam has found something worth teaching."""
    with _lock:
        conf = _read()["confusions"]
    top = sorted((v["n"], k, v["ex"]) for k, v in conf.items() if v["n"] >= 2)[::-1][:limit]
    if not top:
        return ""
    lines = [f'  - "{ex[0]}" → {_pretty(k.split("|")[0])}, NOT {_pretty(k.split("|")[1])}'
             for n, k, ex in top if ex]
    return ("[ROUTING LESSONS — learned from JUDO's own practice tests; follow these]\n"
            + "\n".join(lines) + "\n")


def status() -> str:
    with _lock:
        s = _read()
    total = len(corpus())
    hist = s["history"][-20:]
    recent = sum(h[1] for h in hist) / max(1, sum(h[2] for h in hist))
    worst = sorted(((v[1] / v[0], l, v[0]) for l, v in s["labels"].items() if v[0] >= 5))[:8]
    lines = [f"Corpus: {total:,} commands | round {s['round']} | position {s['cursor']:,}/{total:,}",
             f"Routed: {s['seen']:,} | overall accuracy {s['correct'] / max(1, s['seen']):.1%}"
             f" | last {len(hist)} batches {recent:.1%} | pending re-tests {len(s['wrong']):,}",
             "Weakest labels:"] + [f"  {a:.0%}  {l}  ({n} seen)" for a, l, n in worst]
    lines.append(format_routing_lessons() or "No recurring confusions yet.")
    return "\n".join(lines)


def train(limit: int | None = None, size: int = BATCH_SIZE, workers: int = 1, log=print) -> None:
    """Blocking run over `limit` commands (default: the whole corpus) with
    `workers` batches in flight. Backs off on quota errors and resumes exactly
    where it stopped."""
    from concurrent.futures import ThreadPoolExecutor
    decls = load_tool_decls()
    target = limit or len(corpus())
    progress = {"done": 0, "claimed": 0}
    plock = Lock()

    def worker() -> None:
        fails = 0
        while True:
            with plock:
                n = min(size, target - progress["claimed"])
                if n <= 0:
                    return
                progress["claimed"] += n
            try:
                r = run_batch(decls, n)
            except Exception as e:
                with plock:
                    progress["claimed"] -= n
                fails += 1
                if fails >= 8:
                    log(f"[RoutingTrainer] worker stopping after repeated failures ({e}); rerun to resume.")
                    return
                wait = min(300, 20 * 2 ** (fails - 1))
                log(f"[RoutingTrainer] batch failed ({e}); retrying in {wait}s")
                time.sleep(wait)
                continue
            fails = 0
            with plock:
                progress["done"] += r["total"]
                log(f"[RoutingTrainer] {progress['done']:,}/{target:,}  batch {r['ok']}/{r['total']}  "
                    f"overall {r['correct'] / max(1, r['seen']):.1%}  round {r['round']}")

    with ThreadPoolExecutor(max(1, workers)) as pool:
        for _ in range(max(1, workers)):
            pool.submit(worker)


if __name__ == "__main__":
    import argparse
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="JUDO routing self-training")
    ap.add_argument("--limit", type=int, help="commands to route this run (default: all 20,000)")
    ap.add_argument("--batch", type=int, default=BATCH_SIZE)
    ap.add_argument("--workers", type=int, default=3, help="batches in flight at once")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    if a.reset:
        reset()
        print("Training progress reset.")
    elif a.status:
        print(status())
    else:
        train(a.limit, a.batch, a.workers)
        print(status())
