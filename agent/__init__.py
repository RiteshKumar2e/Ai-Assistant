"""
agent/ — JUDO's task agent: plan → execute → observe → verify, over controlled tools.

The Gemini Live voice session (main.py) stays the conversational front end. When
a request needs several steps — open a project, find a bug, fix it, run the
tests, commit, push — it hands the request to this package through the
`agent_task` action (actions/agent_task.py). Nothing here depends on voice: the
same runner is driven from text by `python -m agent "..."`.

Layout
    agent/config.py              settings (config/agent.json, all optional)
    agent/tools/                 controllers: terminal, filesystem, git, github, browser, vscode, web
    agent/computer.py            ComputerController — one object holding every controller
    agent/agents/                modular agents; each owns a set of tools
    agent/orchestration/         planner, executor loop, task state, permissions
    agent/integrations/          Claude Code adapter, optional MCP client
"""
