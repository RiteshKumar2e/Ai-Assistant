"""
core/routing_trainer.py — JUDO sits a 20,000-TASK exam and learns from every
task it would get wrong.

The exam is core/training_corpus: spoken commands (English/Hinglish/Hindi),
each with the tool that must handle it AND the details a correct call must
carry — who to message and what, which folder, which city, what value. Each
batch shows a text model the SAME tool catalogue the live voice session gets
(names, descriptions, parameters) plus the lessons learned so far, and asks it
to turn every command into a full call. A task passes only if:

  1. the right tool (and action) was picked,
  2. every required detail is right ("Rahul", "main late aaunga", "Projects"),
  3. for the tools where running it is harmless (files in a throwaway folder,
     unit conversion, calendar, system status, memory lookup — see
     core/task_sandbox), the real handler actually RAN with those arguments
     and the result was verified.

Every failure is recorded with its reason. The most frequent ones become the
[TASK LESSONS] block main.py puts in the live system prompt — concrete
"this sentence → this exact call" examples, which the live model follows
well. Failed tasks are re-asked in later batches; once they pass, the lesson
fades out on its own.

This is prompt-level learning, not weight training: Gemini Live cannot be
fine-tuned. Nothing outside core/task_sandbox's fences is ever executed.

CLI (run from the project folder):
    python -m core.routing_trainer              # full pass over all 20,000
    python -m core.routing_trainer --limit 2000 # just the next 2,000
    python -m core.routing_trainer --status     # scores, weak spots, lessons
    python -m core.routing_trainer --reset      # start over
"""
from __future__ import annotations

import ast
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Lock

from core import task_sandbox, training_corpus


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


# Two exams: "core" = the 20,000 template tasks (core/training_corpus),
# "seed" = the cross-verified tasks core/seed_generator writes. Each keeps its
# own progress file; the live prompt merges the lessons of both.
EXAMS = {"core": "routing_training.json", "seed": "seed_training.json"}
_exam = "core"
STATE_PATH = _base_dir() / "memory" / EXAMS["core"]
BATCH_SIZE = 100
MAX_RETEST = 3000
_lock = Lock()
_inflight: set[int] = set()   # items a parallel worker is routing right now
_corpus: list[tuple[str, str, dict]] | None = None
_by_cmd: dict[str, tuple[str, dict]] = {}
_fp: str | None = None
_SELECTORS = ("action", "mode")   # the parameter that picks what a tool does (web_search uses mode)
_LINE_RE = re.compile(r"^\W*(\d+)[.):\s-]+`?([a-z_]+)(?:\.([a-z_]+))?`?\s*(.*)$", re.I)


def use_exam(name: str) -> None:
    """Switch this process to the "core" or "seed" exam."""
    global _exam, STATE_PATH, _corpus, _fp
    _exam, _corpus, _fp = name, None, None
    STATE_PATH = _base_dir() / "memory" / EXAMS[name]


def corpus() -> list[tuple[str, str, dict]]:
    global _corpus, _fp
    if _corpus is None:
        if _exam == "seed":
            from core import seed_generator
            _corpus = [(t["cmd"], t["label"], t.get("args", {})) for t in seed_generator.load()]
        else:
            _corpus = training_corpus.build()
        _by_cmd.update({c: (l, a) for c, l, a in _corpus})
        _fp = training_corpus.fingerprint(_corpus)
    return _corpus


def _fingerprint() -> str:
    corpus()
    return _fp


# ── State ────────────────────────────────────────────────────────────────────

def _fresh() -> dict:
    return {"fingerprint": _fingerprint(), "cursor": 0, "round": 1,
            "seen": 0, "correct": 0, "tool_ok": 0, "args": [0, 0], "exec": [0, 0],
            "labels": {}, "confusions": {}, "wrong": {}, "exec_fail": [], "history": []}


def _read() -> dict:
    try:
        s = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(s, dict) and s.get("fingerprint") == _fingerprint():
            return s
    except Exception:
        pass
    return _fresh()   # missing, corrupt, or the corpus changed → old indexes are meaningless


def _write(s: dict) -> None:
    """Atomic replace, retried: on Windows the replace fails while anything —
    another thread, `--status` in a second window, JUDO reading its lessons —
    has the file open, and a dropped write silently loses a batch."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    for attempt in range(20):
        try:
            tmp.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
            tmp.replace(STATE_PATH)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
        except OSError:
            break
    print(f"[RoutingTrainer] could not save progress to {STATE_PATH.name}")


def _locked_read() -> dict:
    with _lock:
        return _read()


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
        row = f"- {d['name']}: {d.get('description', '')[:500]}"
        props = d.get("parameters", {}).get("properties", {})
        key = next((k for k in _SELECTORS if k in props), "")
        if key:
            row += f"\n    {key}s: {(props[key].get('description') or '')[:420]}"
        other = [f"{k} ({(v.get('description') or '')[:70]})" for k, v in props.items() if k != key]
        if other:
            row += "\n    params: " + "; ".join(other)
        rows.append(row)
    return "\n".join(rows)


# ── Grading ──────────────────────────────────────────────────────────────────

_UNITS = [("km", "kilometer", "kilometers", "kilometre", "kms"), ("miles", "mile", "mi"),
          ("fahrenheit", "f", "°f", "degf"), ("celsius", "c", "°c", "centigrade", "degc"),
          ("kg", "kilogram", "kilograms", "kgs"), ("pounds", "pound", "lbs", "lb"),
          ("dollars", "dollar", "usd", "$"), ("rupees", "rupee", "inr", "rs", "₹"),
          ("euro", "euros", "eur", "€"), ("feet", "foot", "ft"), ("cm", "centimeter", "centimeters", "centimetre"),
          ("inches", "inch", "in")]
_UNIT_OF = {alias: group[0] for group in _UNITS for alias in group}
_UNIT_OF["lbs"] = "pounds"


def _norm(x) -> str:
    return re.sub(r"[\W_]+", " ", str(x).lower()).strip()


def _value_ok(expected: str, got) -> bool:
    """Does one argument the model gave satisfy one expectation from ARGS?"""
    if got is None or str(got).strip() == "":
        return False
    tag, exp = (expected[0], expected[1:]) if expected[:1] in "~#@" else ("", expected)
    if tag == "#":
        nums = re.findall(r"-?\d+(?:\.\d+)?", str(got))
        return any(math.isclose(float(n), float(exp)) for n in nums)
    if tag == "@":
        return _UNIT_OF.get(_norm(got), _norm(got)) == _UNIT_OF.get(exp.lower(), exp.lower())
    words = _norm(exp).split()
    joined = _norm(got).replace(" ", "")
    hits = sum(w in joined for w in words)
    if tag == "~":   # the model may rephrase — half the meaningful words is enough
        return hits >= math.ceil(len(words) / 2)
    return hits == len(words)


def _args_error(expected: dict, got: dict) -> str | None:
    """Name of the first wrong/missing parameter, or None if all are right."""
    for spec, want in expected.items():
        optional = spec.endswith("?")
        alts = spec.rstrip("?").split("|")
        given = [got[a] for a in alts if a in got and str(got[a]).strip() != ""]
        if len(given) > 1:   # the details may be split across the alternatives
            given.append(" ".join(map(str, given)))
        if not given:
            if optional:
                continue
            return spec.rstrip("?")
        if not any(_value_ok(want, v) for v in given):
            return spec.rstrip("?")
    return None


def _is_correct(expected: str, got: str) -> bool:
    eq = training_corpus.EQUIVALENT.get(expected, ())
    if got in eq or got.split(".")[0] in eq:
        return True
    e_tool, _, e_act = expected.partition(".")
    g_tool, _, g_act = got.partition(".")
    return e_tool == g_tool and (not e_act or e_act == g_act)


def effective_label(got: str, got_args: dict) -> str:
    """The action that would really run. computer_settings with no action but
    a description is resolved locally by the tool itself
    (actions/computer_settings._detect_action) — grade what would happen."""
    if got == "computer_settings" and got_args.get("description"):
        from actions.computer_settings import _detect_action
        act = _detect_action(str(got_args["description"])).get("action")
        return f"computer_settings.{act}" if act else got
    return got


def grade(expected: str, want_args: dict, command: str, got: str, got_args: dict,
          execute: bool = True) -> tuple[bool, str | None, str]:
    """(passed, failure key, detail). Keys: "exp|got" wrong tool,
    "exp|args:param" wrong detail, "exp|exec" the real run failed."""
    got = effective_label(got, got_args)
    if not _is_correct(expected, got):
        return False, f"{expected}|{got}", ""
    exact = got.split(".")[0] == expected.split(".")[0]
    if exact:
        bad = _args_error(want_args, got_args)
        if bad:
            return False, f"{expected}|args:{bad}", json.dumps(got_args, ensure_ascii=False)[:160]
    tool, _, action = got.partition(".")
    if execute and exact and task_sandbox.should_run(tool, action, got_args, command):
        ok, detail = task_sandbox.run(tool, action, got_args, want_args)
        if not ok:
            return False, f"{expected}|exec", detail[:200]
        return True, None, "ran"
    return True, None, ""


_FLAT_OBJ = re.compile(r"\{[^{}]*\}")
_BARE_KEY = re.compile(r"([{,]\s*)([A-Za-z_]\w*)\s*:")
_JS_WORDS = {"true": "True", "false": "False", "null": "None"}


def _loose_obj(blob: str) -> dict:
    """JSON, a Python dict, or the JS-style `{app_name: 'Notion'}` Gemma
    sometimes writes — all of them mean the same arguments."""
    for text in (blob, _BARE_KEY.sub(r'\1"\2":', blob)):
        try:
            return json.loads(text)
        except Exception:
            pass
        try:
            return ast.literal_eval(re.sub(r"\b(true|false|null)\b", lambda m: _JS_WORDS[m.group(1)], text))
        except Exception:
            pass
    return {}


def _parse(reply: str, n: int) -> dict[int, tuple[str, dict]]:
    picks: dict[int, tuple[str, dict]] = {}
    for line in reply.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m or not 1 <= int(m.group(1)) <= n:
            continue
        rest, args = m.group(4) or "", {}
        if "{" in rest and "}" in rest:
            args = _loose_obj(rest[rest.index("{"): rest.rindex("}") + 1])
            if not args:
                # e.g. `{json args: {"value": 5}}` — the model echoing the
                # format placeholder around the real (flat) args object.
                args = next((o for o in map(_loose_obj, reversed(_FLAT_OBJ.findall(rest))) if o), {})
        args = args if isinstance(args, dict) else {}
        label = m.group(2).lower()
        act = m.group(3) or next((str(args.pop(k)) for k in _SELECTORS if k in args), "")
        picks[int(m.group(1))] = (label + (f".{act.lower()}" if act else ""), args)
    return picks


def _now_line() -> str:
    """The live session tells the model the date and time (main.py time_ctx);
    without it "2 ghante baad" can only be guessed as some date in 2023."""
    return f"Current date and time: {datetime.now():%A, %Y-%m-%d %H:%M}.\n"


def _ask(prompt: str) -> str:
    from core.text_model import generate_bulk
    return generate_bulk(prompt)


def run_batch(decls: list[dict], size: int = BATCH_SIZE) -> dict:
    """Route one batch of tasks and fold the results into the state. Raises if
    the model is unreachable — callers decide whether to back off or give up."""
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
        # Reserve the slice now so a parallel worker never takes the same items,
        # and mark it pending ("") so a crash or restart mid-batch loses none:
        # pending items come back as re-tests until they get an answer.
        s["cursor"] = cursor
        for k in fresh:
            s["wrong"].setdefault(str(k), "")
        _write(s)
        batch = retest + fresh
        _inflight.update(batch)
    try:
        return _route(decls, batch)
    finally:
        with _lock:
            _inflight.difference_update(batch)


def _route(decls: list[dict], batch: list[int]) -> dict:
    items = corpus()
    lessons = format_routing_lessons(limit=25)
    numbered = "\n".join(f"{i}. {items[k][0]}" for i, k in enumerate(batch, 1))
    reply = _ask(
        "You are the tool router of JUDO, a Hindi/Hinglish/English voice assistant on Windows.\n"
        + _now_line() +
        f"Available tools:\n{_catalogue(decls)}\n"
        "- none: no tool — small talk, thanks, greetings, jokes, or general knowledge you can answer yourself.\n\n"
        f"{lessons}\n"
        "Turn EACH numbered command below into the exact call JUDO should make. One line per command: the\n"
        "number, the tool (with `.action` when the tool has an actions/modes list), then the arguments as a\n"
        "JSON object. For example:\n"
        '7 send_message {"receiver": "Rahul", "message_text": "call me back"}\n'
        "8 computer_settings.volume_up {}\n"
        "9 none {}\n"
        "Put every detail the user gave into the args — names,\n"
        "messages in the user's own words, paths as said (a bare name, never an invented full path), cities,\n"
        "numbers. Leave out anything they did not say. No other text.\n\n"
        f"{numbered}"
    )
    picks = _parse(reply, len(batch))
    if len(picks) < len(batch) // 2:
        raise RuntimeError(f"unparseable router reply ({len(picks)}/{len(batch)} lines)")
    # A reply whose args all went missing is a broken reply, not 50 mistakes —
    # grading it would teach the live prompt lessons that are simply false.
    needs = [i for i, k in enumerate(batch, 1)
             if items[k][2] and i in picks and picks[i][0].split(".")[0] == items[k][1].split(".")[0]]
    if len(needs) >= 5 and sum(1 for i in needs if picks[i][1]) < len(needs) / 2:
        raise RuntimeError(f"router reply dropped the arguments ({len(needs)} tasks) — retrying")

    graded = {}
    for i, k in enumerate(batch, 1):   # run sandboxed tasks outside the state lock
        if i in picks:
            cmd, expected, want = items[k]
            # Real sandbox runs only on the core exam: its tasks name the sample
            # files the sandbox is seeded with; seed tasks name any file at all.
            graded[i] = grade(expected, want, cmd, *picks[i], execute=_exam == "core")

    ok = wrong = 0
    with _lock:
        s = _read()
        for i, k in enumerate(batch, 1):
            if i not in graded:
                s["wrong"].setdefault(str(k), "")
                continue
            cmd, expected, want = items[k]
            passed, key, detail = graded[i]
            lab = s["labels"].setdefault(expected, [0, 0])
            lab[0] += 1
            s["seen"] += 1
            tool_right = passed or "|args:" in key or key.endswith("|exec")
            s["tool_ok"] += tool_right
            if want and tool_right:
                s["args"][0] += 1
                s["args"][1] += passed or key.endswith("|exec")
            if detail == "ran" or (key or "").endswith("|exec"):
                s["exec"][0] += 1
                s["exec"][1] += passed
            if passed:
                ok += 1
                lab[1] += 1
                s["correct"] += 1
                old = s["wrong"].pop(str(k), None)
                if old in s["confusions"]:   # a past mistake now done right → its lesson weakens
                    c = s["confusions"][old]
                    c["n"] -= 1
                    c["ex"] = [e for e in c["ex"] if e != cmd]
                continue
            wrong += 1
            if key.endswith("|exec"):
                s["exec_fail"] = ([{"cmd": cmd, "args": picks[i][1], "result": detail}] + s["exec_fail"])[:100]
            old = s["wrong"].get(str(k))
            if old != key:
                if old in s["confusions"]:
                    s["confusions"][old]["n"] -= 1
                c = s["confusions"].setdefault(key, {"n": 0, "ex": []})
                c["n"] += 1
                if detail and key.endswith("|exec"):
                    c["why"] = detail[:90]
                c["ex"] = ([cmd] + [e for e in c["ex"] if e != cmd])[:3]
                c["want"] = want   # the newest example's expected details, for the lesson text
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


def _requirement(spec: str, args: dict) -> str:
    """One detail in plain words: 'receiver "Rahul"'. Plain text on purpose —
    a lesson written like a call gets copied as the answer FORMAT."""
    want = args.get(spec) or args.get(spec + "?") or ""
    name = spec.split("|")[0]
    return f'{name} "{want.lstrip("~#@")}"' if want else name


def _all_confusions() -> dict:
    """Recurring mistakes from every exam's progress file, merged. Read raw —
    the lessons only need the counts and examples, not the corpus itself."""
    merged: dict[str, dict] = {}
    for fname in EXAMS.values():
        try:
            conf = json.loads((STATE_PATH.parent / fname).read_text(encoding="utf-8")).get("confusions", {})
        except Exception:
            continue
        for k, v in conf.items():
            m = merged.setdefault(k, {"n": 0, "ex": []})
            m["n"] += v.get("n", 0)
            m["ex"] = (m["ex"] + v.get("ex", []))[:3]
            for extra in ("want", "why"):
                if v.get(extra) and not m.get(extra):
                    m[extra] = v[extra]
    return merged


def format_routing_lessons(limit: int = 15) -> str:
    """Top recurring task failures as concrete sentence -> what to do, for the
    system prompt. Empty until the exam has found something to teach."""
    conf = _all_confusions()
    top = sorted((v["n"], k, v["ex"], v.get("why", "")) for k, v in conf.items() if v["n"] >= 2)[::-1][:limit]
    lines = []
    for _n, key, ex, why_run in top:
        if not ex:
            continue
        expected, why = key.split("|", 1)
        args = conf[key].get("want") or _by_cmd.get(ex[0], (expected, {}))[1]
        if why.startswith("args:"):
            lines.append(f'  - "{ex[0]}" → {_pretty(expected)} with {_requirement(why[5:], args)} '
                         f"exactly as the user said it")
        elif why == "exec":
            lines.append(f'  - "{ex[0]}" → {_pretty(expected)} failed when really run ({why_run or "error"}) '
                         f"— pass every detail the tool needs, in the form it expects")
        else:
            lines.append(f'  - "{ex[0]}" → use {_pretty(expected)}, NOT {_pretty(why)}')
    if not lines:
        return ""
    return ("[TASK LESSONS — learned from JUDO's own practice on 20,000 tasks; follow these]\n"
            + "\n".join(lines) + "\n")


def status() -> str:
    with _lock:
        s = _read()
    total = len(corpus())
    hist = s["history"][-20:]
    recent = sum(h[1] for h in hist) / max(1, sum(h[2] for h in hist))
    pct = lambda a, b: f"{a / max(1, b):.1%}"
    worst = sorted(((v[1] / v[0], l, v[0]) for l, v in s["labels"].items() if v[0] >= 5))[:10]
    lines = [f"Exam: {total:,} tasks | round {s['round']} | position {s['cursor']:,}/{total:,}",
             f"Tasks tried: {s['seen']:,} | fully correct {pct(s['correct'], s['seen'])}"
             f" | last {len(hist)} batches {recent:.1%} | pending re-tests {len(s['wrong']):,}",
             f"  right tool {pct(s['tool_ok'], s['seen'])} | right details {pct(s['args'][1], s['args'][0])} ({s['args'][0]:,} checked)"
             f" | really ran OK {pct(s['exec'][1], s['exec'][0])} ({s['exec'][0]:,} run in sandbox)",
             "Weakest tasks:"] + [f"  {a:.0%}  {l}  ({n} tried)" for a, l, n in worst]
    if s["exec_fail"]:
        lines.append("Recent real-run failures:")
        lines += [f"  \"{f['cmd']}\" {json.dumps(f['args'], ensure_ascii=False)[:80]} → {f['result'][:90]}"
                  for f in s["exec_fail"][:5]]
    lines.append(format_routing_lessons() or "No recurring mistakes yet.")
    return "\n".join(lines)


OFFLINE_GIVE_UP = 3 * 3600


def _online() -> bool:
    import socket
    try:
        socket.getaddrinfo("generativelanguage.googleapis.com", 443)
        return True
    except OSError:
        return False


def train(limit: int | None = None, size: int = BATCH_SIZE, workers: int = 1, log=print) -> None:
    """Blocking run over `limit` tasks, or (default) until every one of the
    20,000 has been asked in this round, with `workers` batches in flight.
    Backs off on quota errors, waits out lost internet, and resumes exactly
    where it stopped."""
    from concurrent.futures import ThreadPoolExecutor
    decls = load_tool_decls()
    start_round = _locked_read()["round"]
    target = limit or 10 ** 9
    progress = {"done": 0, "claimed": 0}
    plock = Lock()

    def worker() -> None:
        fails, offline_since = 0, None
        while True:
            with plock:
                n = min(size, target - progress["claimed"])
                if n <= 0 or (not limit and _locked_read()["round"] > start_round):
                    return
                progress["claimed"] += n
            try:
                r = run_batch(decls, n)
            except Exception as e:
                with plock:
                    progress["claimed"] -= n
                if not _online():
                    # No internet is not the model failing — wait it out
                    # instead of burning the retry budget.
                    offline_since = offline_since or time.monotonic()
                    if time.monotonic() - offline_since > OFFLINE_GIVE_UP:
                        log("[RoutingTrainer] offline too long; rerun to resume.")
                        return
                    log("[RoutingTrainer] no internet; checking again in 60s")
                    time.sleep(60)
                    continue
                offline_since = None
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
                goal = f"{target:,}" if limit else f"pass {_locked_read()['cursor']:,}/{len(corpus()):,}"
                log(f"[RoutingTrainer] {progress['done']:,} tasks ({goal})  batch {r['ok']}/{r['total']}  "
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
    ap = argparse.ArgumentParser(description="JUDO 20,000-task self-training")
    ap.add_argument("--limit", type=int, help="tasks to try this run (default: all 20,000)")
    ap.add_argument("--batch", type=int, default=BATCH_SIZE)
    ap.add_argument("--workers", type=int, default=3, help="batches in flight at once")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--exam", choices=list(EXAMS), default="core",
                    help="core = 20,000 template tasks; seed = cross-verified tasks from core.seed_generator")
    a = ap.parse_args()
    use_exam(a.exam)
    if a.reset:
        reset()
        print("Training progress reset.")
    elif a.status:
        print(status())
    else:
        train(a.limit, a.batch, a.workers)
        print(status())
