"""Supervisor routing, task state, the execution loop's guards, and the git
workflow end to end — against a real repo with a bare 'remote'."""
import time

import pytest
from conftest import Answers, ScriptedLLM, git

from agent.orchestration.planner import heuristic_plan
from agent.orchestration.state import Task, TaskContext
from agent.providers.decision import DecisionProvider
from agent.providers.llm import FallbackLLM, LLMProvider

# ── routing: the 13 required scenarios ───────────────────────────────────────
SCENARIOS = [
    ("Hello.", None),
    ("Open GitHub.", "BrowserAgent"),
    ("Open my AI Assistant project.", "ComputerAgent"),
    ("Check why login isn't working.", "CodingAgent"),
    ("Fix it and run tests.", "TestingAgent"),
    ("Commit the changes.", "GitAgent"),
    ("Push it to GitHub.", "GitAgent"),
    ("Research the latest AI agents.", "ResearchAgent"),
    ("Read this PDF and summarize it.", "FileAgent"),
    ("Send this message to Rahul.", "MessagingAgent"),
    ("Schedule a meeting tomorrow at 5.", "CalendarAgent"),
    ("Deploy the project.", "DeploymentAgent"),
]


@pytest.mark.parametrize("request_text,agent", SCENARIOS)
def test_heuristic_fallback_routes_scenarios(request_text, agent):
    """Used only when no model is reachable — must still pick the right agent."""
    plan = heuristic_plan(request_text)
    if agent is None:
        assert plan.intent == "conversation"
    else:
        assert agent in plan.agents


def test_compound_scenario_selects_every_needed_agent():
    plan = heuristic_plan("Open the project, find the bug, fix it, test it, commit it, push it and deploy it.")
    for a in ("ComputerAgent", "CodingAgent", "TestingAgent", "GitAgent", "DeploymentAgent"):
        assert a in plan.agents
    assert "EmailAgent" not in plan.agents and "MessagingAgent" not in plan.agents


def test_model_plan_is_used_and_dependencies_expanded(make_service):
    llm = ScriptedLLM(plan={"intent": "task", "goal": "fix", "agents": ["CodingAgent", "NotAnAgent"], "steps": ["a"]})
    svc = make_service(llm)
    plan = svc.supervisor.plan("fix the login bug", "", "")
    assert plan.agents == ["CodingAgent"]                                   # unknown agent dropped
    assert svc.supervisor.expand(plan.agents) == ["CodingAgent", "FileAgent", "TestingAgent"]


def test_conversation_goes_to_general_agent_without_tools(make_service):
    svc = make_service(ScriptedLLM(plan={"intent": "conversation", "reply": "Hi! How can I help?"}))
    rep = svc.run("Hello")
    assert rep.status == "conversation" and rep.summary.startswith("Hi")
    assert svc.ctx.task.agents == ["GeneralAgent"] and svc.ctx.task.tool_calls == []


def test_planner_falls_back_when_model_down(make_service):
    class Down:
        def complete(self, p): raise RuntimeError("all providers down")
    svc = make_service(Down())
    assert svc.supervisor.plan("Hello", "", "").intent == "conversation"


# ── providers ────────────────────────────────────────────────────────────────
class _P(LLMProvider):
    def __init__(self, name, out=None, ok=True): self.name, self.out, self.ok = name, out, ok
    def available(self): return self.ok
    def complete(self, prompt):
        if isinstance(self.out, Exception): raise self.out
        return self.out


def test_llm_fallback_groq_then_gemini():
    llm = FallbackLLM([_P("groq", RuntimeError("429")), _P("gemini", "from gemini")])
    assert llm.complete("x") == "from gemini" and llm.last_provider == "gemini"
    assert FallbackLLM([_P("groq", "g"), _P("gemini", "m")]).complete("x") == "g"
    assert FallbackLLM([_P("groq", "g", ok=False), _P("gemini", "m")]).complete("x") == "m"
    with pytest.raises(RuntimeError):
        FallbackLLM([_P("groq", RuntimeError("down"))]).complete("x")


def test_decision_provider_falls_back_to_main():
    main = _P("main", '{"ok": 1}')
    d = DecisionProvider(main, {"base_url": "http://127.0.0.1:9/v1", "model": "fast"})   # nothing listens on :9
    assert d.complete("x") == '{"ok": 1}'
    assert DecisionProvider(main, {}).fast is None


# ── task state ───────────────────────────────────────────────────────────────
def test_task_state_lifecycle_and_context(tmp_path):
    ctx = TaskContext(state_file=tmp_path / "s.json")
    t = ctx.new_task("fix login")
    assert t.status == "pending" and t.task_id and ctx.status == "pending"
    for s in ("planning", "running", "waiting_confirmation", "running", "completed"):
        t.set(s)
    assert t.finished and set(t.to_dict()) >= {"task_id", "goal", "status", "current_agent", "steps", "artifacts",
                                               "tool_calls", "errors", "requires_confirmation"}
    with pytest.raises(AssertionError):
        Task("x").set("bogus")
    ctx.set_project(str(tmp_path), "demo", "main", "origin-url")
    assert "demo" in ctx.summary() and "branch main" in ctx.summary()
    assert TaskContext.load(tmp_path / "s.json").project == str(tmp_path)       # survives a restart


def test_follow_up_request_sees_previous_context(make_service, repo):
    llm = ScriptedLLM(plan={"intent": "task", "goal": "g", "agents": ["ComputerAgent"]},
                      actions=[{"tool": "open_project", "args": {"name": "demo app"}}, {"done": True}])
    svc = make_service(llm)
    svc.run("Open my demo app project")
    llm.actions = [{"done": True}]
    svc.run("Run the tests")
    plan_prompt = [p for p in llm.prompts if "planning module" in p][-1]
    assert "demo-app" in plan_prompt and "Open my demo app project" in plan_prompt


# ── loop guards ──────────────────────────────────────────────────────────────
def test_identical_failure_is_blocked_after_retry(make_service):
    bad = {"tool": "read_file", "args": {"path": "missing.py"}}
    llm = ScriptedLLM(plan={"intent": "task", "agents": ["FileAgent"]}, actions=[bad, bad, bad, {"done": True}])
    svc = make_service(llm)
    rep = svc.run("read missing.py")
    statuses = [s.result.status for s in rep.steps]
    assert statuses[:2] == ["error", "error"] and "BLOCKED" in rep.steps[2].result.output


def test_max_iterations_and_consecutive_failures(make_service):
    llm = ScriptedLLM(plan={"intent": "task", "agents": ["FileAgent"]},
                      actions=[{"tool": "nope", "args": {"i": i}} for i in range(20)])
    rep = make_service(llm).run("loop")
    assert rep.status == "failed" and len(rep.steps) == 5            # stopped by the consecutive-failure limit
    llm2 = ScriptedLLM(plan={"intent": "task", "agents": ["FileAgent"]},
                       actions=[{"tool": "list_dir", "args": {"path": "."}} for _ in range(20)])
    rep2 = make_service(llm2, max_iterations=4).run("loop forever")
    assert rep2.status == "failed" and len(rep2.steps) == 4           # stopped by max_iterations


def test_cancel_stops_task(make_service, repo):
    (repo / "slow.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    llm = ScriptedLLM(plan={"intent": "task", "agents": ["ComputerAgent"]},
                      actions=[{"tool": "open_project", "args": {"name": str(repo)}},
                               {"tool": "run_command", "args": {"command": "python slow.py"}}, {"done": True}])
    svc = make_service(llm)
    svc.submit("run slow")
    time.sleep(2.5)
    assert svc.busy() and "Cancelling" in svc.cancel()
    svc._thread.join(15)
    assert svc.last_report.status == "cancelled"


def test_forbidden_command_is_denied_not_run(make_service, repo):
    llm = ScriptedLLM(plan={"intent": "task", "agents": ["ComputerAgent"]},
                      actions=[{"tool": "open_project", "args": {"name": str(repo)}},
                               {"tool": "run_command", "args": {"command": "git push --force origin HEAD"}}, {"done": True}])
    rep = make_service(llm).run("force push")
    assert rep.steps[1].result.status == "denied"


# ── git workflow ─────────────────────────────────────────────────────────────
def test_git_add_never_stages_secrets(make_service, repo):
    svc = make_service(ScriptedLLM())
    (repo / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
    (repo / "feature.py").write_text("x = 1\n", encoding="utf-8")
    r = svc.cc.git.add(str(repo), ["all"])
    assert r.ok and "feature.py" in r.data["staged"] and ".env" in r.data["blocked"]
    assert ".env" not in git(repo, "diff", "--cached", "--name-only")


def test_push_refused_without_approval(make_service, repo):
    svc = make_service(ScriptedLLM())
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    git(repo, "add", "a.py"); git(repo, "commit", "-q", "-m", "a")
    assert svc.cc.git.push(str(repo), approval=None).status == "denied"
    assert git(repo, "rev-parse", "HEAD") != git(repo, "rev-parse", "origin/feature/login-fix")


def test_full_workflow_fix_test_commit_push(make_service, repo):
    """Scenario 13 minus deploy: open → edit → test → stage → commit (generated msg) → push (confirmed) → verified."""
    llm = ScriptedLLM(
        plan={"intent": "task", "goal": "fix and push", "agents": ["ComputerAgent", "CodingAgent", "GitAgent"], "steps": []},
        actions=[{"tool": "open_project", "args": {"name": "demo app"}},
                 {"tool": "read_file", "args": {"path": "app.py"}},
                 {"status": "I found the issue.", "tool": "edit_file",
                  "args": {"path": "app.py", "old": "user == 'admin'", "new": "user.strip() == 'admin'"}},
                 {"tool": "run_tests", "args": {}},
                 {"tool": "git_add", "args": {"paths": "all"}},
                 {"tool": "git_commit", "args": {}},
                 {"tool": "git_push", "args": {}},
                 {"done": True}],
        summary="Fixed login, tests pass, pushed.")
    human = Answers(True)
    svc = make_service(llm, asker=human)
    events = []
    svc.events.subscribe(lambda e: events.append(e.type))
    rep = svc.run("Open the demo app, fix the login bug, test it, commit and push it")

    assert rep.status == "completed", rep.text()
    assert [s.result.ok for s in rep.steps] == [True] * 7, [(s.tool, s.result.output[:200]) for s in rep.steps]
    assert len(human.asked) == 1 and "Push" in human.asked[0][0] and "Tests: passed on exactly this code" in human.asked[0][1]
    assert any(f.startswith("Pushed feature/login-fix") and "verified" in f for f in rep.facts)
    assert any(f.startswith("Tests PASSED") for f in rep.facts)
    assert git(repo, "rev-parse", "HEAD") == git(repo, "ls-remote", "origin", "refs/heads/feature/login-fix").split()[0]
    assert {"task_started", "agent_started", "tool_started", "tool_completed", "confirmation_required",
            "agent_status", "task_completed"} <= set(events)
    assert svc.ctx.task.status == "completed" and any(a["kind"] == "sha" for a in svc.ctx.task.artifacts)


def test_declined_push_is_reported_and_not_retried(make_service, repo):
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    git(repo, "add", "b.py"); git(repo, "commit", "-q", "-m", "b")
    push = {"tool": "git_push", "args": {}}
    llm = ScriptedLLM(plan={"intent": "task", "agents": ["GitAgent", "ComputerAgent"]},
                      actions=[{"tool": "open_project", "args": {"name": str(repo)}}, push, push, {"done": True}])
    human = Answers(False)
    rep = make_service(llm, asker=human).run("push it")
    assert rep.steps[1].result.status == "declined" and "BLOCKED" in rep.steps[2].result.output
    assert len(human.asked) == 1
    assert "Push NOT completed (declined)" in rep.facts


def test_push_to_protected_branch_is_flagged_even_in_trusted_mode(make_service, repo):
    git(repo, "checkout", "-q", "-b", "main"); git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "c.py").write_text("c = 1\n", encoding="utf-8")
    git(repo, "add", "c.py"); git(repo, "commit", "-q", "-m", "c")
    llm = ScriptedLLM(plan={"intent": "task", "agents": ["GitAgent", "ComputerAgent"]},
                      actions=[{"tool": "open_project", "args": {"name": str(repo)}}, {"tool": "git_push", "args": {}}, {"done": True}])
    human = Answers(False)
    make_service(llm, asker=human, mode="trusted").run("push")
    assert len(human.asked) == 1 and "protected branch" in human.asked[0][1]
