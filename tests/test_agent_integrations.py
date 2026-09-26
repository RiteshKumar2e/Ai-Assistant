"""Integrations: messaging, calendar, documents, deployment policy, Claude Code
adapter, MCP client, and the voice-session tool (actions/agent_task)."""
import sys
import textwrap
import time
from datetime import datetime, timedelta

from conftest import Answers, ScriptedLLM

from agent.integrations.claude_code import ClaudeCodeAdapter
from agent.tools.calendar import Calendar, LocalCalendar
from agent.tools.documents import Documents, chunks, retrieve
from agent.tools.filesystem import WorkspaceFS
from agent.tools.messaging import Messaging, MessagingProvider


class FakeTelegram(MessagingProvider):
    name = "telegram"
    def __init__(self): self.sent = []
    def available(self): return True, ""
    def send(self, to, text):
        self.sent.append((to, text))
        return self._ok(to, 777)


def _messaging_service(make_service, asker):
    tg = FakeTelegram()
    msg = Messaging({"Rahul": {"telegram": "42"}}, providers={"telegram": tg})
    llm = ScriptedLLM(plan={"intent": "task", "agents": ["MessagingAgent"]},
                      actions=[{"tool": "send_message", "args": {"to": "rahul", "text": "I'll reach at 6"}}, {"done": True}])
    return make_service(llm, asker=asker, messaging=msg), tg


def test_message_requires_confirmation_and_reports_provider_id(make_service):
    human = Answers(True)
    svc, tg = _messaging_service(make_service, human)
    rep = svc.run("Send Rahul: I'll reach at 6")
    assert tg.sent == [("42", "I'll reach at 6")]
    assert "via telegram" in human.asked[0][1] and "I'll reach at 6" in human.asked[0][1]
    assert any("confirmed by provider" in f and "777" in f for f in rep.facts)


def test_declined_message_is_not_sent(make_service):
    svc, tg = _messaging_service(make_service, Answers(False))
    rep = svc.run("Send Rahul: I'll reach at 6")
    assert tg.sent == [] and any("NOT done (declined)" in f for f in rep.facts)


def test_unknown_contact_and_unconnected_providers_say_so():
    m = Messaging({"Rahul": {"telegram": "42"}})       # real providers, no tokens configured
    assert m.find("rahul")[0] == "Rahul" and m.find("Rahull")[0] == "Rahul" and m.find("Zed") is None
    r = m.send("Rahul", "hi")
    assert not r.ok and "Telegram isn't connected" in r.output
    assert "don't know how to reach" in m.send("Zed", "hi").output


def test_local_calendar_conflict_check(tmp_path):
    cal = Calendar("local")
    cal.providers["local"] = LocalCalendar(tmp_path / "cal.json")
    start = (datetime.now() + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
    iso = start.strftime("%Y-%m-%dT%H:%M")
    r = cal.create("Standup", iso, duration_min=60)
    assert r.ok and r.data["event_id"]
    clash = cal.create("Other", (start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M"), duration_min=30)
    assert clash.status == "conflict" and "Standup" in clash.output
    assert cal.create("Other", (start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M"), allow_conflict=True).ok
    assert "17:00" not in cal.free_slots(start.strftime("%Y-%m-%d")).output.split(": ", 1)[1].split(",")[0]
    assert not cal.create("Past", "2020-01-01T10:00").ok
    assert "Standup" in cal.list(start.strftime("%Y-%m-%d")).output


def test_calendar_invite_risk_is_external(make_service):
    svc = make_service(ScriptedLLM())
    tool = svc.runner.tools["create_event"]
    assert tool.risk_for({"attendees": "rahul@example.com"}) == "external"
    assert tool.risk_for({}) == "write"


def test_documents_chunk_retrieve_and_docx_roundtrip(tmp_path):
    text = "\n\n".join(f"Section {i}. " + ("filler words " * 60) + (" The refund window is 45 days." if i == 7 else "")
                       for i in range(20))
    parts = chunks(text)
    assert len(parts) > 3 and all(len(p) <= 3600 for p in parts)
    top = retrieve("how many days is the refund window", parts, k=2)
    assert any("45 days" in c for _, c in top)
    fs = WorkspaceFS([tmp_path], cwd_provider=lambda: str(tmp_path))
    docs = Documents(fs)
    assert docs.create("report.docx", "# Findings\n- one\n- two\nPlain para").ok
    r = docs.read("report.docx")
    assert r.ok and "Findings" in r.output and "two" in r.output
    (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert "1,2" in docs.read("data.csv").output
    assert docs.read("nope.pdf").status == "error"


def test_production_deploy_denied_without_config(make_service, repo):
    svc = make_service(ScriptedLLM())
    svc.ctx.set_project(str(repo))
    tool = svc.runner.tools["deploy"]
    assert tool.risk_for({"production": True}) == "forbidden"
    assert svc.runner.invoke(tool, {"production": True}).status == "denied"
    svc.cc.deploy.allow_production = True
    assert tool.risk_for({"production": True}) == "external"


def test_verify_deployment_reports_http_status(monkeypatch):
    import requests
    from agent.tools import deploy
    class R:
        status_code, text = 503, "<title>Down</title>"
        elapsed = timedelta(seconds=0.2)
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    r = deploy.verify_url("https://x.vercel.app")
    assert not r.ok and "503" in r.output and "NOT working" in r.output


def test_claude_code_copy_mode(tmp_path, monkeypatch):
    from agent.integrations import claude_code
    monkeypatch.setattr(claude_code, "PROMPT_DIR", tmp_path)
    cc = ClaudeCodeAdapter({"mode": "copy"})
    res = cc.generate_prompt("Fix the login bug", {"repo": "C:/p", "files": ["auth.py"], "error": "401 on /login",
                                                   "test_command": "npm test"})
    assert res.ok and "auth.py" in res.output and "401 on /login" in res.output and "npm test" in res.output
    assert "Do not claim success without verification" in res.output
    ex = cc.execute(res.output, str(tmp_path))
    assert not ex.ok and ex.status == "unavailable" and "one-click copy" in ex.output


def test_claude_code_local_mode_reports_real_exit(tmp_path, monkeypatch):
    """Local mode through the real execute(), with a stand-in `claude` CLI that
    echoes the prompt it got on stdin: status/output come from the process."""
    import os
    fake = tmp_path / "fakeclaude.py"
    fake.write_text('import sys, json\np = sys.stdin.read()\nassert "-p" in sys.argv\n'
                    'print(json.dumps({"result": "got: " + p.strip(), "is_error": False}))\n')
    if os.name == "nt":
        (tmp_path / "claude.cmd").write_text(f'@"{sys.executable}" "{fake}" %*\n')
    else:
        (tmp_path / "claude").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n'); (tmp_path / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    cc = ClaudeCodeAdapter({"mode": "local"})
    assert cc.local_available()[0]
    started = cc.execute("fix the login bug", str(tmp_path))
    assert started.ok, started.output
    job = started.data["job"]
    for _ in range(100):
        if cc.get_status(job).data.get("state") == "done":
            break
        time.sleep(0.1)
    assert cc.get_status(job).ok and cc.get_output(job).output == "got: fix the login bug"


def test_mcp_server_tools_become_gated_agent_tools(tmp_path, make_service):
    server = tmp_path / "mcp_server.py"
    server.write_text(textwrap.dedent('''
        import json, sys
        for line in sys.stdin:
            m = json.loads(line)
            if "id" not in m: continue
            if m["method"] == "initialize": r = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "t"}}
            elif m["method"] == "tools/list": r = {"tools": [{"name": "echo", "description": "Echo text",
                                                   "inputSchema": {"properties": {"text": {"type": "string"}}}}]}
            elif m["method"] == "tools/call": r = {"content": [{"type": "text", "text": "echo:" + m["params"]["arguments"]["text"]}]}
            print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
    '''))
    llm = ScriptedLLM(plan={"intent": "task", "agents": ["MCP_test"]},
                      actions=[{"tool": "mcp_test_echo", "args": {"text": "hi"}}, {"done": True}])
    human = Answers(True)
    svc = make_service(llm, asker=human, mcp_servers=[{"name": "test", "command": sys.executable, "args": [str(server)]}])
    assert "MCP_test" in svc.agents and svc.runner.tools["mcp_test_echo"].to_mcp()["inputSchema"]["properties"]["text"]
    rep = svc.run("echo hi via mcp")
    assert rep.steps[0].result.ok and rep.steps[0].result.output == "echo:hi"
    assert len(human.asked) == 1                          # default MCP risk = external → confirmed


def test_agent_task_action_runs_in_background_and_reports(monkeypatch, make_service):
    import actions.agent_task as at
    llm = ScriptedLLM(plan={"intent": "conversation", "reply": "Hello there."})
    svc = make_service(llm)

    class Player:
        current_file = None
        def __init__(self): self.said, self.logs, self.events, self.content = [], [], [], []
        def write_log(self, t): self.logs.append(t)
        def request_say(self, t): self.said.append(t)
        def agent_event(self, e): self.events.append(e)
        def show_content(self, t, x): self.content.append((t, x))

    player = Player()
    monkeypatch.setattr(at, "_svc", None)
    monkeypatch.setattr("agent.runtime.AgentService", lambda **kw: svc)
    sink = []
    at.event_sinks.append(sink.append)
    try:
        out = at.agent_task({"request": "hello"}, player=player)
        assert out.startswith("[TASK_STARTED]")
        svc._thread.join(10)
        assert any(s.startswith("[AGENT_REPORT status=conversation]") and "Hello there." in s for s in player.said)
        assert player.events and sink and sink[-1]["type"] == "task_completed"
        assert "status: completed" in at.agent_task({"request": "", "action": "status"}, player=player)
        assert "browser" in at.agent_task({"request": "", "action": "integrations"}, player=player)
    finally:
        at.event_sinks.remove(sink.append)
