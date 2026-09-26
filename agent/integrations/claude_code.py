"""
agent/integrations/claude_code.py — hand a coding task to Claude Code.

    ClaudeCodeAdapter
    ├── generate_prompt()   task + real repo context → a precise Claude Code prompt
    ├── execute()           local mode only: runs `claude -p` in the repo
    ├── get_status()        from the real process, never assumed
    ├── get_output()
    └── cancel()

Mode 1 — copy (default, always works): the prompt is saved, shown on the HUD
with COPY PROMPT / OPEN IN VS CODE, and the user pastes it into Claude Code.
Mode 2 — local: config/agent.json → "claude_code": {"mode": "local"} AND the
`claude` CLI on PATH. Execution is reported as done only when that process
exits, with its exit code and output attached.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import uuid

from agent.config import DATA_DIR
from agent.tools.base import ToolResult, redact

PROMPT_DIR = DATA_DIR / "claude_tasks"

_META = """You write task prompts for Claude Code, an autonomous coding agent that will work inside the repository below.
Write ONE prompt for this task, grounded in the real context given — name the actual files, the actual error text,
the actual test command. Do not invent files or errors that are not in the context; where something is unknown,
tell Claude Code to find it out.

TASK (the user's words): {task}

REPOSITORY CONTEXT
{context}

The prompt must contain, as short headed sections:
Objective · Context (relevant files, suspected issue, evidence) · Approach (understand the current architecture first,
reproduce, find the root cause before editing, smallest clean fix, no unrelated rewrites) · Constraints ·
Verification (exact commands to run; run the app/build if relevant) · Report (files changed, changes made, tests run,
results, remaining issues) — and end with: "Do not claim success without verification."
Output only the prompt text, no preamble."""


def fallback_prompt(task: str, context: dict) -> str:
    files = "\n".join(f"- {f}" for f in context.get("files", [])) or "- (not yet identified — locate them first)"
    return f"""## Objective
{task}

## Context
Repository: {context.get('repo', 'current folder')} (branch {context.get('branch') or 'unknown'})
Stack: {context.get('stack') or 'inspect the repository to determine it'}
Relevant files:
{files}
{('Evidence / error output:' + chr(10) + context['error']) if context.get('error') else ''}
{('Suspected issue: ' + context['suspected']) if context.get('suspected') else ''}

## Approach
1. Inspect the existing implementation and understand the current architecture before changing anything.
2. Reproduce the problem (or confirm the missing behaviour).
3. Identify the root cause before modifying code.
4. Implement the smallest clean fix. Do not rewrite unrelated components.

## Constraints
- Keep existing behaviour and public interfaces intact unless the task requires changing them.
- Do not commit secrets or touch credential files.

## Verification
- Run the existing tests: `{context.get('test_command') or 'the project test command'}`.
- If tests fail, diagnose and fix the actual issue — do not weaken or skip tests.
- Run the application/build if appropriate and verify the affected flow.

## Report
1. Files changed  2. Changes made  3. Tests executed  4. Test results  5. Remaining issues

Do not claim success without verification."""


class ClaudeCodeAdapter:
    def __init__(self, cfg: dict | None = None, llm=None):
        cfg = cfg or {}
        self.mode_cfg   = cfg.get("mode", "copy")
        self.cli        = cfg.get("cli", "claude")
        self.perm_mode  = cfg.get("permission_mode", "acceptEdits")
        self.timeout_s  = int(cfg.get("timeout_s", 1800))
        self.llm        = llm
        self._jobs: dict[str, dict] = {}

    # ── mode ────────────────────────────────────────────────────────────────
    def local_available(self) -> tuple[bool, str]:
        if self.mode_cfg != "local":
            return False, "Claude Code local execution isn't enabled (copy mode). Set claude_code.mode = \"local\" in config/agent.json."
        if not shutil.which(self.cli):
            return False, f"Claude Code local execution isn't configured: the `{self.cli}` CLI is not on PATH."
        return True, ""

    # ── prompt ──────────────────────────────────────────────────────────────
    def generate_prompt(self, task: str, context: dict) -> ToolResult:
        ctx_text = "\n".join(f"{k}: {v}" for k, v in context.items() if v)
        prompt, how = "", "template"
        if self.llm:
            try:
                prompt = self.llm.complete(_META.format(task=task, context=redact(ctx_text)[:12000])).strip()
                how = "model"
            except Exception as e:
                print(f"[ClaudeCode] prompt model failed ({e}) — using template")
        if len(prompt) < 200 or "verif" not in prompt.lower():
            prompt, how = fallback_prompt(task, context), "template"
        prompt = redact(prompt)
        PROMPT_DIR.mkdir(parents=True, exist_ok=True)
        path = PROMPT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}.md"
        path.write_text(prompt, encoding="utf-8")
        return ToolResult(True, prompt, {"prompt_path": str(path), "built_by": how})

    # ── local execution ─────────────────────────────────────────────────────
    def execute(self, prompt: str, cwd: str) -> ToolResult:
        ok, why = self.local_available()
        if not ok:
            return ToolResult.fail(f"I prepared the Claude Code task. {why} The prompt is ready for one-click copy.", "unavailable")
        exe = shutil.which(self.cli)
        argv = [exe, "-p", "--output-format", "json", "--permission-mode", self.perm_mode]
        try:
            p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", errors="replace", shell=exe.lower().endswith((".cmd", ".bat")))
        except OSError as e:
            return ToolResult.fail(f"Could not start Claude Code: {e}")
        jid = uuid.uuid4().hex[:6]
        job = self._jobs[jid] = {"proc": p, "started": time.time(), "out": "", "err": "", "done": False, "cwd": cwd}
        def run():
            try:
                job["out"], job["err"] = p.communicate(prompt, timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                p.kill(); job["err"] = f"timed out after {self.timeout_s}s"
            job["done"] = True
        threading.Thread(target=run, daemon=True, name=f"claude-{jid}").start()
        return ToolResult(True, f"Claude Code started locally in {cwd} (job {jid}). I'll report when it actually finishes.", {"job": jid})

    def get_status(self, job_id: str) -> ToolResult:
        job = self._jobs.get(job_id)
        if not job:
            return ToolResult.fail(f"No Claude Code job {job_id}.")
        if not job["done"]:
            return ToolResult(True, f"Claude Code job {job_id} still running ({int(time.time() - job['started'])}s).", {"state": "running"})
        code = job["proc"].returncode
        return ToolResult(code == 0, f"Claude Code job {job_id} finished with exit code {code}.", {"state": "done", "exit_code": code})

    def get_output(self, job_id: str) -> ToolResult:
        job = self._jobs.get(job_id)
        if not job:
            return ToolResult.fail(f"No Claude Code job {job_id}.")
        if not job["done"]:
            return ToolResult(True, "Still running — no final output yet.", {"state": "running"})
        text = job["out"]
        try:
            d = json.loads(text)
            text = d.get("result") or text
            failed = bool(d.get("is_error"))
        except (json.JSONDecodeError, AttributeError):
            failed = job["proc"].returncode != 0
        return ToolResult(not failed and job["proc"].returncode == 0, redact(text or job["err"])[:20000])

    def cancel(self, job_id: str) -> ToolResult:
        job = self._jobs.get(job_id)
        if not job or job["done"]:
            return ToolResult.fail("Nothing running to cancel.")
        job["proc"].kill()
        return ToolResult(True, f"Cancelled Claude Code job {job_id}.")
