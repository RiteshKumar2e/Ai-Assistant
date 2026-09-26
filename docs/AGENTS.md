# Agents and tools

The supervisor routes each request to the agents it needs. Every tool returns a
`ToolResult` whose `ok` comes from the real operation (exit code, HTTP status,
provider response). Risk classes are explained in [ARCHITECTURE.md](ARCHITECTURE.md#permissions).

| Agent | Tools (risk) |
|---|---|
| **SupervisorAgent** | plans, picks agents/tools, writes the final report — owns no tools |
| **GeneralAgent** | none — conversation answered directly |
| **ComputerAgent** | `open_project` (finds by folder *or* git-remote name), `open_in_vscode`, `open_file_in_vscode`, `open_application`, `open_folder`, `open_terminal`, `run_command` (classified per command), `start_process` / `read_process` / `stop_process`, `read_screen` (screenshot → Gemini vision), `screen_input` (exec:unknown) |
| **CodingAgent** (+File, +Testing) | `claude_code_prompt` (context-grounded prompt + Copy card), `claude_code_run` (local mode only), `claude_code_wait` |
| **TestingAgent** | `project_stack`, `run_tests` (records pass/fail + tested-code fingerprint), `run_build`, `run_lint` |
| **GitAgent** | `git_status`, `git_diff`, `git_log`, `git_branches`, `git_create_branch`, `git_switch`, `git_add` (never stages secrets or build artifacts), `git_commit_message`, `git_commit` (message generated if omitted), `git_pull` (ff-only), `git_push` (external, preflight + approval + remote verification), `git_delete_branch` (destructive) |
| **GitHubAgent** (+Git) | `gh_repo`, `gh_issues`, `gh_prs`, `gh_create_pr` (external) — via `gh` |
| **ResearchAgent** | `web_search` (with URLs), `fetch_url`, `deep_research` (Google-grounded Gemini, DDG fallback) |
| **FileAgent** (File/Document) | `list_dir`, `tree`, `find_files`, `search_code`, `read_file`, `write_file`, `edit_file` (exact unique match), `rename_path`, `delete_path` (destructive, recycle bin), `read_document`, `ask_document` (BM25 retrieval over chunks), `summarize_document` (map-reduce), `create_document` (md/txt/json/csv/docx) |
| **BrowserAgent** | `open_url`, `browser_search`, `new_tab`, `switch_tab`, `read_page`, `current_url`, `click` / `smart_click` (external if it looks like submit/pay/send/delete), `type_text`, `press_key`, `scroll`, `browser_back` |
| **MessagingAgent** | `list_contacts`, `draft_message`, `send_message` (external) |
| **EmailAgent** | `search_email`, `read_email`, `draft_email`, `send_email` (external), `reply_email` (external) |
| **CalendarAgent** | `list_events`, `find_free_slots`, `create_event` (conflict-checked; external with attendees), `update_event` (external), `cancel_event` (destructive) |
| **DeploymentAgent** (+Testing) | `inspect_deployment`, `deploy` (external; production forbidden unless allowed), `deployment_status`, `verify_deployment` (HTTP check) |
| **MCP_<name>** | one agent per configured MCP server; its `tools/list`, gated as configured `risk` (default external) |

## Terminal rules (ComputerAgent `run_command`)

One program per call. `; & | > < ` $(`, newlines are rejected, as are `% ^ !`
for `.cmd` shims. Classification:

* **read:** `ls`, `cat`, `git status/diff/log/…`, `gh … list/view`
* **exec:** npm/pnpm/yarn, python/pytest/pip, cargo, go, dotnet, gradle, flutter, …
* **forbidden:** shells (cmd, powershell, bash), disk/OS/credential tools,
  `curl`/`wget`/`ssh`, `git push/commit/reset/…` (use the git tools), anything
  naming a secret file
* **destructive:** `rm`, `del`, `kill`, `git branch -D`, `pip uninstall`. Forbidden
  outright when a target is outside the workspaces.

Runs with cwd inside a workspace, a timeout, output capped (head + tail) and
redacted, secrets removed from the environment, `CI=1`, and the process tree
killed on timeout or cancel.

## Adding an agent

1. Create `agent/agents/<name>.py`:

   ```python
   from agent.agents.base import Agent
   from agent.tools.base import READ, EXTERNAL, ToolResult

   class WeatherAgent(Agent):
       name = "WeatherAgent"
       description = "Current weather and forecasts."          # what the planner routes on
       requires = ()                                            # other agents it always needs

       def tools(self):
           return [self.tool("forecast", "Forecast for a city.", {"city": "name"},
                             lambda city: ToolResult(True, f"…{city}…"), READ)]
   ```

   Pick the honest risk; use a callable `risk(args)` when it depends on
   arguments, `confirm_detail(args) -> (title, detail)` for a good HUD banner,
   and `prepare(args)` if the tool must receive an `Approval`.
2. Add the class to `AGENTS` in `agent/runtime.py`, and a line about it to
   the routing rules in `PLAN_PROMPT` (`agent/orchestration/planner.py`).
3. Put vendor code behind a controller in `agent/tools/` and hang it on
   `ComputerController`, so it can be swapped or faked in tests (`**overrides`).
4. Test with `tests/conftest.py`'s `ScriptedLLM` + `make_service` fixtures.
