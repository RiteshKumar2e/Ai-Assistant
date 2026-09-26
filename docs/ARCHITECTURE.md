# JUDO task agent — architecture

JUDO was already a voice assistant: a Gemini Live session (`main.py`) that calls
tools auto-discovered from `actions/*.py` and `plugins/*.py`, with a PyQt HUD
(`ui.py`), a phone dashboard (`dashboard/`), a Groq → Gemini text-model chain
(`core/text_model.py`) and a confirmation gate the model can't forge
(`core/confirm.py`). The task agent is added **on top of** that, not instead of it.

```
 voice / typed text / phone
          │
   Gemini Live session (main.py) ── simple one-step tools (open_app, volume, web_search, …)
          │
          │  multi-step work → actions/agent_task.py  (returns at once; runs in background)
          ▼
   AgentService (agent/runtime.py)          ← also driven from text: python -m agent "…"
          │
   SupervisorAgent ── planner (orchestration/planner.py)
          │               natural language + task context → {intent, agents, goal, steps}
          │
   TaskRunner loop (orchestration/executor.py)
     DECIDE next tool (supervisor) → PERMIT (orchestration/permissions.py)
       → EXECUTE → OBSERVE (ToolResult) → VERIFY → repeat … → REPORT
          │
   Agents (agent/agents/*)  — each owns a set of Tools
          │
   ComputerController (agent/computer.py) — one object holding every controller
     browser · vscode · terminal · files · git · github · web · messaging · email
     calendar · deploy · claude (Claude Code) · + optional MCP servers
```

## Key modules

| Path | Role |
|---|---|
| `agent/runtime.py` | `AgentService`: builds everything once; `run()` (sync), `submit()` (background), `status()`, `cancel()` |
| `agent/orchestration/planner.py` | Plan prompt + JSON parsing; `heuristic_plan()` only when no model is reachable |
| `agent/agents/supervisor.py` | Agent catalogue, dependency expansion (`requires`), next-action and final-report prompts |
| `agent/orchestration/executor.py` | The loop and its guards; builds the **verified facts** from real results |
| `agent/orchestration/permissions.py` | Risk × mode table, `Approval` tokens, `ConfirmationBroker` (HUD button / console) |
| `agent/orchestration/state.py` | `Task` (task_id, status, tool_calls, artifacts, errors) and session `TaskContext` |
| `agent/events.py` | Typed event stream + speech throttle |
| `agent/providers/` | `LLMProvider` → Groq, Gemini, `FallbackLLM`; optional `DecisionProvider` |
| `agent/tools/` | Controllers. Each returns `ToolResult(ok, output, data, status)` |
| `agent/integrations/` | Claude Code adapter, Google OAuth, MCP client |

## The loop and its guarantees

* **Plan with context.** The planner sees the current project, branch, last
  test run, last error, attached file, earlier requests and the current time,
  so "fix it", "push it" and "this PDF" resolve to what they refer to.
* **Only needed agents.** The plan names agents. Their `requires` are added
  (Coding → File + Testing; Deployment → Testing; GitHub → Git). Only those
  agents' tools are shown to the model. If a tool of another agent is chosen,
  that agent is brought in explicitly.
* **Permissions are enforced in `TaskRunner.invoke`**, never by the model or
  the UI (see below).
* **No infinite loops:** `max_iterations` (30), a wall-clock `task_timeout_s`
  (1200 s), a limit of 5 consecutive failures, `cancel()`. An action that fails
  twice with the same arguments is blocked. A declined or denied one is never
  re-asked. A failed next-action model call is retried 3× with backoff.
* **No invented success.** The final report = the model's short summary +
  **Verified** facts generated from `ToolResult`s ("Tests PASSED: pytest",
  "Committed 2d29c50", "Pushed … verified on remote", "send message: NOT done
  (declined)"). Research reports list the source URLs actually read.
* **Honest capability list.** `ComputerController.capabilities()` tells the
  planner what really works on this machine. Missing integrations are planned
  with their agent anyway, and its tool says exactly what to connect.

## Permissions

| risk | strict | balanced (default) | trusted |
|---|---|---|---|
| read | allow | allow | allow |
| write (in-workspace edits, git add/commit) | confirm | allow | allow |
| exec (recognised dev commands) | confirm | allow | allow |
| exec:unknown / exec:inline (`python -c`) | confirm | confirm | allow |
| external (push, PR, message, email, invite, deploy, form submit) | confirm | confirm | allow* |
| destructive (delete, branch delete, uninstall) | confirm | confirm | confirm |
| forbidden (disk/OS/credential tools, shells, `git push` via terminal) | deny | deny | deny |

\* trusted still confirms an external action the preflight flagged (protected
branch, unexpected remote, tests not passed on this exact code, production).
Anything outside the configured workspaces is escalated to at least *confirm*.

`git push` needs an `Approval` object that only `ConfirmationBroker` can mint.
It is bound to repo + branch + remote URL + HEAD and is single-use, so a
commit made after the user said yes can't ride along. Success is reported only
after `git ls-remote` shows the remote branch at our HEAD.

## Task state and events

`Task.status`: `pending → planning → running ⇄ waiting_confirmation →
completed | failed | cancelled | waiting_input` (waiting_input = a question
for the user). Events: `task_started, agent_started, agent_status,
agent_completed, tool_started, tool_completed, confirmation_required,
clarification_needed, error, task_completed`. Messages are redacted and hold
no model reasoning.

Consumers: the HUD AGENT box and activity log, spoken progress (rate-limited
milestones, errors, confirmations, result), and the phone dashboard
(`{"type": "agent_event", "event": …}` over its authenticated websocket).

## Voice-ready by construction

Nothing under `agent/` imports audio or Gemini Live. Voice is one front end
(`actions/agent_task.py`); `python -m agent` is another. The pipeline is Voice →
STT (Gemini Live input transcription, with language hints) → `agent_task` →
AgentService → tools → report → spoken by the Live session.

## Security principles

No shell for the model (one program per call, no `; & | > <`); secrets are
scrubbed from command environments and redacted from all output; `.env`, keys,
tokens and cookie stores are never read or staged; OAuth tokens are read only
by client libraries; workspaces bound file access. JUDO never closes, kills
or restarts the user's browser, and opens web pages in Edge. There is no CAPTCHA
bypass, stealth or persistence.
