"""
agent/orchestration/executor.py — the task loop.

    PLAN → (SELECT AGENT/TOOL → PERMIT → EXECUTE → OBSERVE → VERIFY) × n → REPORT

Guarantees, all enforced here rather than asked of the model:
  * every tool call passes permissions.decide(); CONFIRM blocks this worker
    thread on a human answer, DENY returns the reason as the observation
  * a failing action gets one retry with the same arguments, then is blocked;
    a declined or denied action is never re-asked
  * max_iterations, a wall-clock timeout, cancel(), and a consecutive-failure
    limit all end the loop with an explicit status — never a silent stop
  * the report carries VERIFIED FACTS built from ToolResults (tests passed?,
    committed sha, push verified on the remote?, message delivered?) — the
    model's summary sits on top of them and cannot overrule them
"""
from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass, field

from agent.orchestration.permissions import CONFIRM, DENY, decide
from agent.tools.base import ToolResult

_PATH_ARGS = ("path", "src", "dst", "cwd")
MAX_CONSECUTIVE_FAILURES = 5


@dataclass
class Step:
    agent: str
    tool: str
    args: dict
    result: ToolResult


@dataclass
class TaskReport:
    status: str                  # completed | failed | cancelled | waiting_input | conversation
    summary: str
    facts: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    question: str = ""
    sources: list[str] = field(default_factory=list)
    task_id: str = ""

    def text(self) -> str:
        parts = [self.summary.strip()]
        if self.facts:
            parts.append("Verified:\n" + "\n".join(f"• {f}" for f in self.facts))
        if self.sources:
            parts.append("Sources:\n" + "\n".join(f"- {u}" for u in self.sources[:8]))
        return "\n\n".join(p for p in parts if p)


def call_tool(tool, args: dict, **extra) -> ToolResult:
    sig = inspect.signature(tool.fn)
    var_kw = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    clean = {k: v for k, v in args.items() if k != "approval" and (var_kw or k in sig.parameters)}
    try:
        r = tool.fn(**clean, **extra)
    except TypeError as e:
        return ToolResult.fail(f"Bad arguments for {tool.name}: {e}. Expected: {', '.join(tool.params)}")
    except Exception as e:
        return ToolResult.fail(f"{tool.name} crashed: {type(e).__name__}: {e}")
    return r if isinstance(r, ToolResult) else ToolResult(True, str(r))


def _first_line(r: ToolResult) -> str:
    return (r.output.strip().splitlines() or [r.status])[0][:160]


class TaskRunner:
    def __init__(self, supervisor, cc, broker, events, settings: dict, ctx):
        self.sup, self.cc, self.broker, self.events, self.settings, self.ctx = supervisor, cc, broker, events, settings, ctx
        self.tools = {t.name: t for a in supervisor.agents.values() for t in a.tools()}

    # ── permissions ──────────────────────────────────────────────────────────
    def invoke(self, tool, args: dict) -> ToolResult:
        mode = self.settings["confirmation_mode"]
        risk = tool.risk_for(args)
        outside = any(self.cc.files.outside(str(args[k])) for k in _PATH_ARGS if args.get(k))
        if tool.prepare:
            prep = tool.prepare(args)
            if not prep.ok:
                return prep
            d = decide(risk, mode, outside_workspace=outside, flagged=prep.data.get("flagged", False))
            if d == DENY:
                return ToolResult.fail("Not allowed by policy.", "denied")
            if d == CONFIRM:
                self._waiting(tool, prep.data["title"], prep.data["detail"])
            approval = self.broker.approve(prep.data["scope"], prep.data["title"], prep.data["detail"], ask=(d == CONFIRM))
            self._resume()
            if approval is None:
                return ToolResult.fail(f"The user declined: {prep.data['title']}. Do not retry.", "declined")
            return call_tool(tool, args, approval=approval)
        d = decide(risk, mode, outside_workspace=outside)
        if d == DENY:
            reason = ""
            if tool.name in ("run_command", "start_process"):
                from agent.tools.terminal import classify
                reason = classify(args.get("command", ""))[1]
            return ToolResult.fail(f"Not allowed{': ' + reason if reason else ' by policy'}.", "denied")
        if d == CONFIRM:
            title, detail = (tool.confirm_detail(args) if tool.confirm_detail
                             else (tool.name.replace("_", " ").capitalize(), json.dumps(args, default=str)[:280]))
            if outside:
                detail += "\n(outside your configured workspaces)"
            self._waiting(tool, title, detail)
            ok = self.broker.ask(title, detail)
            self._resume()
            if not ok:
                return ToolResult.fail(f"The user declined: {title}. Do not retry.", "declined")
        return call_tool(tool, args)

    def _waiting(self, tool, title: str, detail: str) -> None:
        self.ctx.pending_confirmation = title
        if (t := self.ctx.task):
            t.set("waiting_confirmation"); t.requires_confirmation = True
        self.events.emit("confirmation_required", f"I need your confirmation on screen: {title}.", tool.agent,
                         "waiting_confirmation", title=title, detail=detail)

    def _resume(self) -> None:
        self.ctx.pending_confirmation = ""
        if (t := self.ctx.task):
            t.set("running"); t.requires_confirmation = False

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def history(steps: list[Step]) -> str:
        out = []
        for i, s in enumerate(steps, 1):
            n = 2500 if i > len(steps) - 5 else 300
            out.append(f"{i}. {s.tool}({json.dumps(s.args, ensure_ascii=False, default=str)[:400]}) → {s.result.brief(n)}")
        return "\n".join(out)

    def facts(self, steps: list[Step], started: float) -> tuple[list[str], list[str]]:
        facts: list[str] = []
        t = self.ctx.last_test
        if t and t.at >= started:
            facts.append(f"Tests {'PASSED' if t.ok else 'FAILED'}: {t.command}")
        for s in steps:
            r, d = s.result, s.result.data
            if s.tool == "git_commit" and r.ok:
                facts.append(f"Committed {d.get('sha')}")
            elif s.tool == "git_push":
                facts.append(f"Pushed {d.get('branch')} → {d.get('url')}, verified on remote" if r.ok and d.get("verified")
                             else f"Push NOT completed ({r.status})")
            elif s.tool in ("write_file", "edit_file", "rename_path", "create_document") and r.ok:
                facts.append(r.output.split("(")[0].strip())
            elif s.tool == "claude_code_prompt" and r.ok:
                facts.append("Claude Code prompt prepared (Copy Prompt on screen) — not executed")
            elif s.tool == "claude_code_run":
                facts.append(f"Claude Code local run: {'started' if r.ok else r.status}")
            elif s.tool in ("run_build", "run_lint") and r.status not in ("denied", "declined"):
                facts.append(f"{s.tool.split('_')[1].capitalize()} {'PASSED' if r.ok else 'FAILED'}: {d.get('command', '')}")
            elif s.tool in ("send_message", "send_email", "reply_email", "create_event", "update_event", "cancel_event", "deploy"):
                facts.append(f"{s.tool.replace('_', ' ')}: {'confirmed by provider — ' + _first_line(r) if r.ok else 'NOT done (' + r.status + ')'}")
            elif s.tool == "verify_deployment":
                facts.append(f"Deployment check: {_first_line(r)}")
            elif s.tool == "open_project" and r.ok:
                facts.append(f"Project: {d.get('project')}")
        # Pages actually read are the real sources; search hits only when nothing was read.
        sources = [s.result.data["url"] for s in steps if s.tool == "fetch_url" and s.result.ok] or \
                  [u for s in steps if s.tool == "web_search" and s.result.ok for u in s.result.data.get("sources", [])[:5]]
        return list(dict.fromkeys(facts)), list(dict.fromkeys(sources))

    # ── loop ─────────────────────────────────────────────────────────────────
    def run(self, request: str) -> TaskReport:
        cfg, ctx, ev = self.settings, self.ctx, self.events
        task = ctx.new_task(request)
        ev.task_id = task.task_id
        self.cc.cancel_event.clear()
        task.set("planning")
        caps = "\n".join(f"- {k}: {'available' if ok else 'NOT available — ' + why}" for k, (ok, why) in self.cc.capabilities().items())
        plan = self.sup.plan(request, caps, ctx.summary())
        task.steps, task.agents = plan.steps, self.sup.expand(plan.agents)

        if plan.intent == "conversation":
            task.agents = ["GeneralAgent"]; task.set("completed")
            ctx.history.append((request, "answered conversationally"))
            ev.emit("task_completed", plan.reply[:300], "GeneralAgent", "completed")
            return TaskReport("conversation", plan.reply or "Hello — what can I do for you?", task_id=task.task_id)
        if plan.intent == "clarify" and plan.question:
            task.set("waiting_input")
            ctx.history.append((request, f"asked the user: {plan.question}"))
            ev.emit("clarification_needed", plan.question, "SupervisorAgent", "waiting_input")
            return TaskReport("waiting_input", plan.question, question=plan.question, task_id=task.task_id)

        task.set("running")
        active = list(task.agents)
        ev.emit("task_started", plan.say or f"Working on it: {plan.goal}", "SupervisorAgent", "running",
                goal=plan.goal, agents=active, steps=plan.steps, planner=plan.source)
        steps: list[Step] = []
        failures: dict[str, int] = {}
        consecutive_fail, status, question = 0, "completed", ""
        deadline = time.monotonic() + cfg["task_timeout_s"]
        current = ""

        for _ in range(cfg["max_iterations"]):
            if self.cc.cancel_event.is_set():
                status = "cancelled"; break
            if time.monotonic() > deadline:
                status = "failed"; ev.emit("error", f"The task hit its {cfg['task_timeout_s']}s time limit.", status="timeout"); break
            tools = [t for t in self.tools.values() if t.agent in active]
            d, err = None, None
            for attempt in range(3):                      # a blip in the model chain must not end the task
                try:
                    d = self.sup.next_action(request, plan, tools, self.history(steps), ctx.summary())
                    break
                except Exception as e:
                    err = e
                    if self.cc.cancel_event.wait(2 * (attempt + 1)):
                        break
            if d is None:
                if self.cc.cancel_event.is_set():
                    status = "cancelled"; break
                ev.emit("error", f"I lost my planning model mid-task ({str(err)[:120]})."); status = "failed"; break
            if d.get("done"):
                break
            if d.get("ask"):
                status, question = "waiting_input", str(d["ask"])
                ev.emit("clarification_needed", question, current, "waiting_input"); break
            if d.get("fail"):
                status = "failed"; ev.emit("error", str(d["fail"])[:300], current); break
            name, args = str(d.get("tool", "")), d.get("args") if isinstance(d.get("args"), dict) else {}
            tool = self.tools.get(name)
            if not tool:
                steps.append(Step("", name, args, ToolResult.fail(f"No tool named '{name}'. Use one of the listed tools.")))
                consecutive_fail += 1
                if consecutive_fail >= MAX_CONSECUTIVE_FAILURES:
                    status = "failed"; break
                continue
            if tool.agent not in active:
                active = self.sup.expand(active + [tool.agent]); task.agents = active
            if tool.agent != current:
                if current:
                    ev.emit("agent_completed", f"{current} done.", current, "completed")
                current = task.current_agent = ctx.current_agent = tool.agent
                ev.emit("agent_started", f"{tool.agent} started.", tool.agent, "running")
            if d.get("status"):
                ev.emit("agent_status", str(d["status"])[:200], tool.agent, "running")
            sig = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
            if failures.get(sig, 0) >= 2:
                steps.append(Step(tool.agent, name, args, ToolResult.fail(
                    "BLOCKED: this exact action already failed or was declined — choose a different approach or finish.", "denied")))
                consecutive_fail += 1
                if consecutive_fail >= MAX_CONSECUTIVE_FAILURES:
                    status = "failed"; break
                continue
            ev.emit("tool_started", name, tool.agent, "running", tool=name, args=args)
            result = self.invoke(tool, args)
            ctx.note(name, result)
            steps.append(Step(tool.agent, name, args, result))
            task.tool_calls.append({"agent": tool.agent, "tool": name, "args": args, "ok": result.ok,
                                    "status": result.status, "summary": _first_line(result), "at": time.time()})
            for k in ("modified", "sha", "url", "prompt_path", "deployment_url", "message_id", "event_id"):
                if result.ok and result.data.get(k):
                    task.artifacts.append({"kind": k, "value": result.data[k]})
            ev.emit("tool_completed", f"{'✓' if result.ok else '✗'} {name}: {_first_line(result)}", tool.agent,
                    "ok" if result.ok else result.status, tool=name, ok=result.ok)
            if result.ok:
                consecutive_fail = 0
                continue
            task.errors.append(f"{name}: {_first_line(result)}")
            failures[sig] = 2 if result.status in ("declined", "denied") else failures.get(sig, 0) + 1
            consecutive_fail += 1
            if result.status == "error" and tool.long_running:
                ev.emit("error", f"{name} failed — looking into it.", tool.agent)
            if consecutive_fail >= MAX_CONSECUTIVE_FAILURES:
                status = "failed"; ev.emit("error", "Too many consecutive failures — stopping.", tool.agent); break
        else:
            status = "failed"
            ev.emit("error", f"I hit the {cfg['max_iterations']}-step limit before finishing.")

        if current:
            ev.emit("agent_completed", f"{current} done.", current, "completed")
        facts, sources = self.facts(steps, task.started)
        summary = question or self.sup.summarize(request, status, facts, self.history(steps)) or self._fallback_summary(status, steps)
        task.set(status); task.current_agent = ctx.current_agent = ""
        ctx.history.append((request, f"{status}: {summary[:220]}"))
        ev.emit("task_completed", summary[:500], "SupervisorAgent", status, facts=facts)
        return TaskReport(status, summary, facts, steps, question, sources, task.task_id)

    @staticmethod
    def _fallback_summary(status: str, steps: list[Step]) -> str:
        ok = sum(s.result.ok for s in steps)
        last_fail = next((s for s in reversed(steps) if not s.result.ok), None)
        msg = f"Task {status}. {ok} of {len(steps)} actions succeeded."
        return msg + (f" Last problem: {last_fail.tool} — {last_fail.result.output[:200]}" if last_fail else "")
