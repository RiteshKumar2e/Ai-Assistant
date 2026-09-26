"""
agent/orchestration/planner.py — natural language → a structured plan.

The model reads the request *with the task context* (current project, last
test run, last error, earlier requests), so "fix it" / "push it" / "the
project" resolve to the things they refer to. It returns which agents are
needed — only those — the goal, and the steps.

heuristic_plan() is a last-resort fallback for when no model is reachable at
all (every provider down / no keys). It is deliberately crude and says so; it
exists so the assistant degrades to "does the obvious thing" instead of dying.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.llm import ask_json

PLAN_PROMPT = """You are the planning module of a personal computer assistant that controls the user's own computer through tools.
Turn the user's request into a plan. Understand intent, not keywords; resolve words like "it", "that", "the project", "the bug",
"the changes" using the TASK CONTEXT below. If the user refers to something the context does not contain, you may ask.

AGENTS (choose only the ones this request actually needs):
{agents}

CAPABILITIES ON THIS MACHINE RIGHT NOW:
{caps}

TASK CONTEXT:
{context}

USER REQUEST: {request}

Reply with ONE JSON object, no prose:
{{
  "intent": "conversation" | "task" | "clarify",
  "reply": "only for conversation: your full answer to the user",
  "question": "only for clarify: the single question you must ask",
  "goal": "one line: what done looks like",
  "agents": ["AgentName", ...],
  "steps": ["short step", ...],
  "say": "optional one short sentence to tell the user as work starts (e.g. 'I'll inspect the authentication flow.'), or empty"
}}
Rules:
- conversation = greetings, chit-chat, general-knowledge questions. No tools.
- Prefer "task". The agents find things out and apply sensible defaults themselves. NEVER ask for:
  where a project is (open_project searches the workspaces by name), which branch/remote/test command, which messaging
  channel (the contact's configured one is used), which deployment platform (detected from the project), a meeting's
  title or length (default "Meeting", 60 min, no invitees unless named), or what to fix (tests/errors/code reveal it).
- A capability listed as NOT available is still planned with its agent (MessagingAgent, DeploymentAgent, EmailAgent…):
  its tools report exactly what is missing and how to connect it. Never swap in a different agent or ask the user
  to pick an alternative because of it.
- clarify ONLY when the request has no discernible objective, or needs content only the user can supply
  (e.g. "send Rahul a message" with no message text), or a destructive action's target is genuinely ambiguous.
- Include ComputerAgent whenever a project must be opened/selected or commands run. Coding work → CodingAgent (+ComputerAgent to run things).
- Pushing/committing → GitAgent. Pull requests/issues/remote repo info → GitHubAgent. Current web information → ResearchAgent.
  Interacting with a website in the user's browser → BrowserAgent. Saving research to a file, or reading/summarising a
  document (PDF, DOCX, …; "this file" = the attached file in the context) → FileAgent. Tests/build/lint → TestingAgent.
  Chat messages (Telegram/Slack/Discord/WhatsApp) → MessagingAgent. Email → EmailAgent. Meetings/events → CalendarAgent.
  Deploying or checking a deployment → DeploymentAgent (it pulls in TestingAgent to build first).
- Only include push/commit steps if the user asked for them (now or as a standing part of this request).
- Keep steps short; 1 step for simple things, up to ~12 for big ones."""


@dataclass
class Plan:
    intent: str = "task"
    goal: str = ""
    agents: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    reply: str = ""
    question: str = ""
    say: str = ""
    source: str = "model"


def make_plan(llm, request: str, agent_catalogue: str, caps: str, context: str, known: set[str]) -> Plan:
    try:
        d = ask_json(llm, PLAN_PROMPT.format(agents=agent_catalogue, caps=caps, context=context, request=request))
    except Exception as e:
        print(f"[Agent] planner model unavailable ({e}) — heuristic fallback")
        return heuristic_plan(request, context)
    agents = [a for a in d.get("agents") or [] if a in known]
    intent = d.get("intent") if d.get("intent") in ("conversation", "task", "clarify") else "task"
    if intent == "task" and not agents:
        agents = heuristic_plan(request, context).agents
    return Plan(intent, str(d.get("goal") or request), agents, [str(s) for s in d.get("steps") or []][:20],
                str(d.get("reply") or ""), str(d.get("question") or ""), str(d.get("say") or ""))


_RULES = [
    (r"\b(push|commit|git|branch|stage|diff)\b", ["GitAgent", "ComputerAgent"]),
    (r"\b(pull request|pr\b|issues?\b|github repo)", ["GitHubAgent", "GitAgent"]),
    (r"\b(research|latest|compare|news|find out|look up)\b", ["ResearchAgent"]),
    (r"\b(open|go to|visit|browse)\b.*\b(\.com|\.org|\.io|website|site|github|youtube|google|browser)\b", ["BrowserAgent"]),
    (r"\b(fix|bug|error|test|build|debug|implement|feature|refactor|login|auth|code)\b", ["CodingAgent", "ComputerAgent"]),
    (r"\b(project|repo|vs ?code|folder|terminal|run|start|install)\b", ["ComputerAgent"]),
    (r"\b(file|markdown|\.md|save|write|read|pdf|docx|document|summari[sz]e)\b", ["FileAgent"]),
    (r"\b(message|whatsapp|telegram|slack|discord)\b", ["MessagingAgent"]),
    (r"\b(e-?mail|inbox|gmail|outlook)\b", ["EmailAgent"]),
    (r"\b(meeting|calendar|schedule|event|appointment)\b", ["CalendarAgent"]),
    (r"\b(deploy|deployment|vercel|render)\b", ["DeploymentAgent", "ComputerAgent"]),
    (r"\b(tests?|build|lint)\b", ["TestingAgent", "ComputerAgent"]),
]
_GREETING = re.compile(r"^\s*(hi|hello|hey|yo|good (morning|afternoon|evening)|thanks|thank you|how are you)\b[\s!.?]*$", re.I)


def heuristic_plan(request: str, context: str = "") -> Plan:
    if _GREETING.match(request):
        return Plan("conversation", reply="Hello! What would you like me to do?", source="heuristic")
    agents: list[str] = []
    for rx, names in _RULES:
        if re.search(rx, request, re.I):
            agents += [n for n in names if n not in agents]
    return Plan("task", request, agents or ["ComputerAgent"], [request], source="heuristic")
