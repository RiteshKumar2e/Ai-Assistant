"""
agent/agents/supervisor.py — SupervisorAgent: routes a request to the agents it
needs, decides each next action from what the previous ones actually returned,
and writes the final report from observations only.
"""
from __future__ import annotations

import json

from agent.llm import ask_json
from agent.orchestration.planner import Plan, make_plan

STEP_PROMPT = """You are the execution module of a personal computer assistant. You act ONLY by calling the tools listed below,
one per reply, and you see each tool's real result before choosing the next one.

GOAL: {goal}
USER REQUEST: {request}
PLAN: {steps}

TASK CONTEXT:
{context}

TOOLS (name(params) — description):
{tools}

HISTORY OF THIS TASK (your actions and their real results):
{history}

Reply with ONE JSON object:
  {{"status": "<optional short progress note for the user, only at milestones>", "tool": "<tool name>", "args": {{...}}}}
or {{"done": true}}                      when the goal is achieved (or as achieved as it can be)
or {{"ask": "<question>"}}               only if you truly cannot continue without the user
or {{"fail": "<reason>"}}                if it cannot be done with these tools
Rules:
- Base every decision on real results above. Never assume a tool succeeded; never invent file contents, errors or URLs.
- No project selected but one is needed → call open_project with whatever the user called it ("shop backend", "my AI Assistant
  project"); it searches the workspaces. Never "ask" where a project or file is — find it with tools; ask only if open_project
  reports several equally good matches or none. Read a file before editing it; edit with exact text from read_file.
- Do exactly what was asked. "Check / why / find / investigate / look at" = diagnose and REPORT — do not edit, create or
  delete files, commit or push. Change things only when the request asks for a change (fix, implement, add, update, write, create).
- To fix a bug: locate the code (search_code/read_file), reproduce or find the evidence, identify the root cause, make the smallest fix,
  then run_tests (if the project has tests). If tests fail, read the failure and fix the actual cause.
- "Fix it" means the issue named in the TASK CONTEXT / earlier requests. If that is already fixed and the tests pass, say so and
  finish — never invent other work. Warnings from the user's environment/tooling are not bugs unless the user asked about them.
- Large multi-file features, or when the user mentions Claude Code → claude_code_prompt (and claude_code_run if local mode is available).
- Commit only if the user asked to commit or push; push only if the user asked to push. Stage with git_add, commit with git_commit
  (omit the message to auto-generate one), push with git_push — it asks the user itself; if declined, do not retry.
- Research: web_search, then fetch_url the 2-4 best sources, then finish (the report will cite the URLs). Save to a file with write_file if asked.
- A tool marked unavailable will not start working — work around it or finish and say what is missing.
- Never repeat an action that already failed with the same arguments. If stuck after two different attempts, finish with done.
- "status" is spoken to the user: use it rarely — when you find the cause, start a long step, or change direction. Never expose reasoning."""

SUMMARY_PROMPT = """Write the assistant's spoken report to the user for this finished task: 2-5 short, plain sentences.
State what was done and what was found, using ONLY the results below. Say clearly what failed or was not done.
Do not claim anything the results or VERIFIED FACTS don't show. If it makes sense, end with the next sensible action as a
short suggestion. If research was done, give the key findings concisely (sources are appended separately).

REQUEST: {request}
OUTCOME STATUS: {status}
VERIFIED FACTS:
{facts}
RESULTS:
{history}"""


class SupervisorAgent:
    name = "SupervisorAgent"

    def __init__(self, llm, agents: dict, decider=None):
        self.llm, self.agents = llm, agents
        self.decider = decider or llm           # structured JSON decisions (plan, next action)

    def catalogue(self) -> str:
        return "\n".join(f"- {a.name}: {a.description}" for a in self.agents.values())

    def plan(self, request: str, caps: str, context: str) -> Plan:
        return make_plan(self.decider, request, self.catalogue(), caps, context, set(self.agents))

    def expand(self, names: list[str]) -> list[str]:
        out: list[str] = []
        def add(n):
            if n in self.agents and n not in out:
                out.append(n)
                for r in self.agents[n].requires:
                    add(r)
        for n in names:
            add(n)
        return out

    def next_action(self, request: str, plan: Plan, tools: list, history: str, context: str) -> dict:
        prompt = STEP_PROMPT.format(goal=plan.goal, request=request, steps=json.dumps(plan.steps), context=context,
                                    tools="\n".join(t.signature() for t in tools), history=history or "(nothing yet)")
        return ask_json(self.decider, prompt, retries=1)

    def summarize(self, request: str, status: str, facts: list[str], history: str) -> str:
        try:
            return self.llm.complete(SUMMARY_PROMPT.format(request=request, status=status, facts="\n".join(facts) or "(none)",
                                                           history=history[-9000:])).strip()
        except Exception as e:
            print(f"[Agent] summary model failed: {e}")
            return ""
