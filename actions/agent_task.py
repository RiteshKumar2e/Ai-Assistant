"""
agent_task.py — hands multi-step work to JUDO's task agent (agent/).

The Live voice model stays the conversation. For anything that takes several
steps — open a project, find and fix a bug, run the tests, commit and push,
research and compare, deploy and verify, message / email / schedule — it calls
this tool with the user's words. The task runs on a background thread, so the
user can keep talking; progress milestones are spoken (rate-limited), every
step lands in the activity log and the AGENT box, and the final, verified
result is spoken when it finishes.

The task context persists between calls, so follow-ups like "fix it",
"run the tests again", "push it" refer to the same project and issue.
"""
from __future__ import annotations

import threading
from pathlib import Path

_svc = None
_lock = threading.Lock()
# Extra consumers of the live task stream, each called with Event.to_dict() from the
# agent's worker thread — main.py registers the phone dashboard here.
event_sinks: list = []
_ICON = {"task_started": "●", "agent_started": "◆", "agent_status": "●", "tool_completed": "", "confirmation_required": "⚠",
         "clarification_needed": "?", "error": "✗", "task_completed": "■"}


def _say(player, instruction: str) -> None:
    fn = getattr(player, "request_say", None)
    if callable(fn):
        fn(instruction)


def _integrations(svc) -> str:
    rows = []
    for name, (ok, why) in svc.cc.capabilities().items():
        rows.append(f"{'✓' if ok else '✗'} {name:<18} {why if why else 'ready'}")
    s = svc.settings
    rows += ["", f"Confirmation mode: {s['confirmation_mode']}", "Workspaces: " + ", ".join(map(str, s["workspaces"])),
             f"Production deploys: {'allowed' if s['deploy'].get('allow_production') else 'not allowed'}",
             f"Decision model: {s['decision_provider'].get('model') or 'main LLM (Groq → Gemini)'}"]
    return "\n".join(rows)


def _service(player):
    global _svc
    with _lock:
        if _svc is not None:
            return _svc
        from agent.events import SpeechThrottle
        from agent.runtime import AgentService

        def show_prompt(text, repo, path):
            if hasattr(player, "show_task_card"):
                player.show_task_card("CLAUDE CODE TASK", text, repo, path)
            elif player:
                player.show_content("CLAUDE CODE TASK", text)

        svc = AgentService(player=player, show_prompt=show_prompt)
        throttle = SpeechThrottle()

        def on_event(ev):
            for sink in list(event_sinks):
                try:
                    sink(ev.to_dict())
                except Exception as e:
                    print(f"[agent_task] event sink failed: {e}")
            if player is None:
                return
            if ev.type != "tool_started":
                icon = _ICON.get(ev.type, "·")
                player.write_log(f"[agent] {icon + ' ' if icon else ''}{ev.message[:140]}")
            if hasattr(player, "agent_event"):
                player.agent_event(ev.to_dict())
            if ev.type != "task_completed" and throttle.should_speak(ev):
                _say(player, f"[AGENT_PROGRESS] {ev.message}\nTell the user this in their own language in ONE brief sentence. "
                             "Do not call any tool for this message.")

        svc.events.subscribe(on_event)
        if player is not None:
            player.get_integrations = lambda: _integrations(svc)
        _svc = svc
        return svc


def _on_done(player, report) -> None:
    if player is None:
        return
    text = report.text()
    player.show_content("TASK RESULT" if report.status != "waiting_input" else "QUESTION", text)
    tail = ("Ask the user this question in their own language; their answer comes back to agent_task as a new request."
            if report.status == "waiting_input" else
            "Relay this result to the user naturally in their own language in 2-4 short sentences. Say only what is stated here — "
            "never add success the report doesn't state. Do not call agent_task again unless the user asks for something new.")
    _say(player, f"[AGENT_REPORT status={report.status}]\n{text[:2500]}\n\n{tail}")


def agent_task(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "run") or "run").lower().strip()
    request = str(p.get("request", "") or "").strip()
    svc = _service(player)

    if action == "status":
        return svc.status()
    if action == "cancel":
        return svc.cancel()
    if action == "integrations":
        return _integrations(svc)
    if not request:
        return "Tell me what you want done."
    if svc.busy():
        return (f"[AGENT_BUSY] Still working on: {svc.ctx.goal[:120]} ({svc.ctx.status}). Tell the user in one sentence, and offer "
                "to cancel it or wait. Call agent_task with action=cancel only if they ask to stop it.")

    attached = getattr(player, "current_file", None)
    if attached and Path(attached).is_file():
        f = Path(attached).resolve()
        svc.cc.files.allowed_files.add(f)
        svc.ctx.attached_file = str(f)

    svc.submit(request, on_done=lambda rep: _on_done(player, rep))
    return ("[TASK_STARTED] The agent is working on this in the background and will report back. Say ONE short natural "
            "sentence in the user's language acknowledging it (no details, no claims of results). Do not call agent_task again for it.")


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "agent_task",
    "description": (
        "JUDO's autonomous task agent. Use for anything needing SEVERAL steps or real project/dev work: open/find a project, "
        "inspect or fix code, find why something fails, run tests/builds, git commit/push, GitHub issues/PRs, research and compare "
        "with sources, read/summarise documents, send chat messages (Telegram/Slack/Discord/WhatsApp Cloud), email, schedule "
        "meetings, deploy and verify a deployment, or prepare a Claude Code prompt. Pass the user's request in their words, "
        "including follow-ups like 'fix it', 'run the tests', 'push it' — the agent remembers the current project and task. "
        "It runs in the background and reports back itself. Use action='status' when the user asks what it is doing, "
        "action='cancel' to stop it, action='integrations' for what is connected. Simple one-step things (open an app, "
        "volume, a quick web answer) should still use their own tools."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "request": {"type": "STRING", "description": "The user's request, in their words (translate to English if needed)"},
            "action":  {"type": "STRING", "description": "run (default) | status | cancel | integrations"},
        },
        "required": ["request"],
    },
    "handler": agent_task,
}
