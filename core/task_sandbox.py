"""
core/task_sandbox.py — really run the SAFE exam tasks and check they worked.

The routing exam (core/routing_trainer) checks that JUDO picked the right tool
with the right details. For tools where running the call cannot hurt anything,
this module then actually runs it — the real handler, the model's own
arguments — and verifies the outcome, so "the task got DONE" is tested, not
just "the call looked right".

Only these run, each fenced in:
  file_controller  every path shortcut (Desktop, Downloads, ...) points into a
                   throwaway temp folder seeded with sample files; the tool's
                   own safe-roots list is narrowed to that folder; absolute,
                   home-relative or ".." paths are refused before the call;
                   deletes remove from the temp folder, never the Recycle Bin.
  calendar_agenda  a temp calendar file; the OS notification (Task Scheduler)
                   is switched off.
  reminder         the date/time reading runs for real; writing the notify
                   script and registering it with Task Scheduler are stubbed.
  unit_converter   local maths; the live-currency ones only for 1 in 10, so
                   the free exchange-rate API is not hammered.
  system_status    read-only, 1 in 10 (each call samples the CPU for 0.2s).
  recall_memory    read-only search of the local memory file.

Everything else — opening apps/sites, messages, email, volume, shutdown,
screen capture, code execution — is never run: the exam only checks the call.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import tempfile
import zlib
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from core import training_corpus

_BASE = Path(__file__).resolve().parent.parent
_lock = Lock()   # the fences below patch module globals — one task at a time
_FAIL = ("could not", "access denied", "not found", "unknown action", "error", "not a file",
         "no such", "does not exist", "please give", "please tell", "failed")

EXECUTABLE = {"file_controller", "calendar_agenda", "reminder", "unit_converter", "system_status", "recall_memory"}
_SAMPLED = {"system_status": 10}          # run 1 in N
_CURRENCY = {"dollars", "rupees", "euro", "usd", "inr", "eur"}


def _load_plugin(name: str):
    spec = importlib.util.spec_from_file_location(f"_sandbox_{name}", _BASE / "plugins" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _one_in(n: int, key: str) -> bool:
    return zlib.crc32(key.encode()) % n == 0


def should_run(tool: str, action: str, args: dict, command: str) -> bool:
    if tool not in EXECUTABLE:
        return False
    if tool == "file_controller" and action == "rename" and not args.get("new_name"):
        return False   # no new name given — the right move is to ask, not to run
    if tool == "unit_converter":
        units = {str(args.get(k, "")).lower() for k in ("from_unit", "to_unit")}
        if units & _CURRENCY:
            return _one_in(10, command)
    return _one_in(_SAMPLED.get(tool, 1), command)


# ── file_controller fence ────────────────────────────────────────────────────

_KINDS = ("desktop", "downloads", "documents", "pictures", "music", "videos")


@contextmanager
def _file_sandbox():
    import actions.file_controller as fc
    root = Path(tempfile.mkdtemp(prefix="judo_exam_"))
    dirs = {k: root / k.capitalize() for k in _KINDS}
    for d in dirs.values():
        d.mkdir()
        (d / "inside.txt").write_text("JUDO sandbox\n", encoding="utf-8")
    for name in training_corpus.S["file"]:
        (dirs["desktop"] / name).write_text(f"JUDO sandbox file {name}\n", encoding="utf-8")
    for name in training_corpus.S["folder"]:
        if name.lower() not in _KINDS:
            (dirs["desktop"] / name).mkdir(exist_ok=True)
            (dirs["desktop"] / name / "inside.txt").write_text("JUDO sandbox\n", encoding="utf-8")

    saved = {n: getattr(fc, n) for n in
             [f"_get_{k}" for k in _KINDS] + ["_SAFE_ROOTS", "_safe_trash", "push_undo"]}
    for k in _KINDS:
        setattr(fc, f"_get_{k}", lambda d=dirs[k]: d)
    fc._SAFE_ROOTS = [root]
    fc._safe_trash = lambda t: (shutil.rmtree(t) if t.is_dir() else t.unlink(), f"Moved to Trash: {t.name}")[1]
    fc.push_undo = lambda *a, **k: None
    try:
        yield fc, dirs["desktop"]
    finally:
        for n, v in saved.items():
            setattr(fc, n, v)
        shutil.rmtree(root, ignore_errors=True)


def _unsafe_path(v) -> bool:
    s = str(v or "").strip().strip("\"'")
    return bool(re.match(r"^([a-zA-Z]:|[\\/]|~|home\b|%)", s, re.I)) or ".." in s


def _run_file(action: str, args: dict) -> tuple[bool, str]:
    for k in ("path", "destination", "name", "new_name"):
        if _unsafe_path(args.get(k)):
            return False, f"invented an absolute/home path {k}={args.get(k)!r} (bare names are resolved on the Desktop)"
    with _file_sandbox() as (fc, desk):
        target = lambda: fc._resolve_path(args.get("path", "desktop")) / (args.get("name") or "")
        before = target()
        existed = before.exists()
        result = str(fc.file_controller({"action": action, **args}))
        low = result.lower()
        if any(m in low for m in _FAIL):
            return False, result
        if action in ("create_folder", "create_file"):
            return target().exists(), result
        if action in ("delete", "rename", "move"):
            if not existed:
                return False, f"source not found: {before.name} — {result}"
            if before.exists():
                return False, f"{before.name} is still there — {result}"
            if action == "move":
                dest = fc._resolve_path(args.get("destination", ""))
                return (dest / before.name).exists(), result
            return True, result
        if action == "copy":
            dest = fc._resolve_path(args.get("destination", ""))
            return (dest / before.name).exists() and before.exists(), result
        if action == "read":
            return "JUDO sandbox" in result, result
        return bool(result.strip()), result


# ── other tools ──────────────────────────────────────────────────────────────

_REF = {("km", "miles"): lambda v: v * 0.621371, ("miles", "km"): lambda v: v * 1.60934,
        ("kg", "pounds"): lambda v: v * 2.20462, ("kg", "lbs"): lambda v: v * 2.20462,
        ("feet", "cm"): lambda v: v * 30.48, ("inches", "cm"): lambda v: v * 2.54,
        ("fahrenheit", "celsius"): lambda v: (v - 32) * 5 / 9, ("celsius", "fahrenheit"): lambda v: v * 9 / 5 + 32}


def _run_unit(args: dict, expected: dict) -> tuple[bool, str]:
    result = str(_load_plugin("unit_converter").run(dict(args)))
    if any(m in result.lower() for m in _FAIL):
        return False, result
    uf, ut = expected.get("from_unit", "@").lstrip("@"), expected.get("to_unit", "@").lstrip("@")
    ref = _REF.get((uf, ut))
    if not ref:
        return bool(re.search(r"\d", result)), result
    want = ref(float(expected["value"].lstrip("#")))
    nums = [float(n.replace(",", "")) for n in re.findall(r"-?\d[\d,]*\.?\d*", result)]
    return any(abs(n - want) <= max(0.01, abs(want) * 0.01) for n in nums), result


def _run_calendar(action: str, args: dict) -> tuple[bool, str]:
    cal = _load_plugin("calendar_agenda")
    tmp = Path(tempfile.mkdtemp(prefix="judo_cal_"))
    cal._STORE = tmp / "calendar.json"
    cal._schedule_notification = lambda *a, **k: None
    try:
        result = str(cal.run({"action": action, **args}))
        ok = not any(m in result.lower() for m in _FAIL)
        if action == "add":
            ok = ok and cal._STORE.exists() and str(args.get("title", "")) in cal._STORE.read_text(encoding="utf-8")
        return ok, result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_reminder(args: dict) -> tuple[bool, str]:
    import actions.reminder as rem
    saved = {n: getattr(rem, n) for n in ("_write_notify_script", "_schedule_windows", "_schedule_mac", "_schedule_linux")}
    rem._write_notify_script = lambda *a: Path(tempfile.gettempdir()) / "judo_exam_reminder.py"
    rem._schedule_windows = rem._schedule_mac = rem._schedule_linux = lambda *a: "exam"
    try:
        result = str(rem.reminder(dict(args)))
        # "4 baje" asked at 6 pm is read correctly and refused for the clock's
        # sake — that is not a task JUDO misunderstood.
        return result.startswith("Reminder set") or "already passed" in result, result
    finally:
        for n, v in saved.items():
            setattr(rem, n, v)


def run(tool: str, action: str, args: dict, expected: dict) -> tuple[bool, str]:
    """Execute one safe task inside its fence. Never raises."""
    try:
        with _lock:
            if tool == "file_controller":
                return _run_file(action, args)
            if tool == "unit_converter":
                return _run_unit(args, expected)
            if tool == "calendar_agenda":
                return _run_calendar(action, args)
            if tool == "reminder":
                return _run_reminder(args)
            if tool == "system_status":
                from actions.system_monitor import get_system_status
                r = get_system_status()
                return bool(r), str(r)[:200]
            if tool == "recall_memory":
                from memory.memory_manager import search_memory
                return True, str(search_memory(args.get("query", ""), limit=3))[:200]
    except Exception as e:
        return False, f"crashed: {type(e).__name__}: {e}"
    return False, "not executable"
