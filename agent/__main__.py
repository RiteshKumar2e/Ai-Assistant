"""
Text front end for the agent — same runtime the voice assistant uses.

    python -m agent "open my AI Assistant project and run the tests"
    python -m agent                      # interactive: keeps task context between requests

Confirmations are asked on this terminal (y/N). Exit with Ctrl+C or 'exit'.
"""
from __future__ import annotations

import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from agent.orchestration.permissions import console_asker
from agent.runtime import AgentService

_ICON = {"task_started": "●", "agent_status": "●", "agent_started": "◆", "tool_completed": "  ", "confirmation_required": "⚠",
         "error": "✗", "clarification_needed": "?"}


def main() -> int:
    svc = AgentService(asker=console_asker, show_prompt=lambda text, repo, path: print(f"\n┌─ Claude Code Task ─────────\n{text}\n└────────────────────────────"))
    svc.events.subscribe(lambda e: e.type in _ICON and print(f"{_ICON[e.type]} {e.message}"))
    one_shot = " ".join(sys.argv[1:]).strip()
    while True:
        req = one_shot or input("\nyou › ").strip()
        if req.lower() in ("exit", "quit"):
            return 0
        if req:
            print("\n" + svc.run(req).text())
        if one_shot:
            return 0 if svc.last_report and svc.last_report.status in ("completed", "conversation") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print()
