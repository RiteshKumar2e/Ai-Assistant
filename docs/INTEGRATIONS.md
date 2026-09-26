# Integrations and setup

Everything here is optional. JUDO starts and the agent works without any of it.
A missing integration reports exactly what to connect (⚙ HUD → AGENT →
**INTEGRATIONS**, or say "what's connected?").

All credentials live in **`config/api_keys.json`** (gitignored — see
`config/api_keys.example.json`). There is no `.env`. Non-secret agent settings
live in **`config/agent.json`** (gitignored — copy `config/agent.example.json`).
Nothing secret is ever put in a prompt, an event or a tool result.

## Works immediately (no extra setup)

* LLM: the existing `gemini_api_key` / `groq_api_key` (Groq first, Gemini fallback)
* Planning, supervisor, task memory, permissions, progress, HUD panels
* Projects in workspaces, files, documents (PDF/DOCX/XLSX/PPTX/CSV/JSON), terminal, tests/build/lint
* Local git: status, diff, commit, **push** (uses git's own credential manager)
* Research: web search + page reading (DuckDuckGo; Gemini grounding with your Gemini key)
* Claude Code **copy mode** (prompt + COPY PROMPT / OPEN IN VS CODE)
* Local calendar (`~/.judo/calendar.json`, shared with the calendar voice plugin)
* Browser: opens pages as tabs in your open **Edge** window

## Requires local software

| Feature                                | Needs                                                                                                                           |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| VS Code open                           | `code` on PATH (VS Code's "Add to PATH")                                                                                      |
| GitHub issues/PRs/repo info            | [GitHub CLI](https://cli.github.com) + `gh auth login`                                                                         |
| Claude Code local execution            | `claude` CLI on PATH + `"claude_code": {"mode": "local"}` in agent.json                                                     |
| Vercel deploys                         | `npm i -g vercel` + `vercel login` (or `vercel_token`)                                                                    |
| Browser page control (type/click/read) | Windows UI Automation (pywinauto, in requirements) on the open window, or a browser you started with`--remote-debugging-port` |
| Screen reading                         | `mss` (in requirements) + Gemini key                                                                                          |

## Requires credentials / OAuth

| Integration             | `config/api_keys.json` keys / setup                                                                                              |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Telegram                | `telegram_bot_token` (bot from @BotFather; the contact must have sent `/start` to it)                                          |
| Slack                   | `slack_bot_token` (`xoxb-…`, `chat:write`)                                                                                  |
| Discord                 | `discord_webhooks: {"TEAM": "https://discord.com/api/webhooks/…"}`                                                              |
| WhatsApp                | Meta**WhatsApp Cloud API**: `whatsapp_token`, `whatsapp_phone_number_id` (free-form text only inside Meta's 24 h window) |
| Gmail + Google Calendar | OAuth — see below                                                                                                                 |
| Outlook                 | `pip install msal`, `outlook_client_id`, then `python -m agent.tools.email outlook-login`                                    |
| Render                  | `render_api_key` + `deploy.render_service_id` in agent.json                                                                    |
| Fast decision model     | agent.json`decision_provider: {"base_url", "model"}`; key (if any) `decision_api_key`                                          |

Contacts are mapped to provider ids in `config/agent.json`:

```json
"contacts": {"Rahul": {"telegram": "123456789", "slack": "U0123ABCD", "whatsapp": "+919876543210",
                       "discord": "TEAM", "email": "rahul@example.com"}}
```

Messaging uses official APIs only (no UI scraping). "Sent" is reported only
when the provider returns a message id.

### Google OAuth (Gmail + Calendar)

1. Google Cloud Console → create a project → enable **Gmail API** and **Google Calendar API**.
2. OAuth consent screen → add yourself as a test user.
3. Credentials → **OAuth client ID → Desktop app** → download the JSON.
4. Save it as `config/client_secret_google.json` (gitignored).
5. `python -m agent.integrations.google_oauth` → approve on Google's page.
   The token is saved to `config/token_google.json` (gitignored). No password is ever seen.

## Claude Code

* **Copy mode (default):** CodingAgent's `claude_code_prompt` builds a prompt
  from the real repo: branch, stack, test command, relevant files, the actual
  error, git status and tree. It covers objective, context, approach,
  constraints, verification and report, and ends "Do not claim success without
  verification". It's saved to `memory/agent/claude_tasks/` and shown with
  **COPY PROMPT** and **OPEN IN VS CODE**.
* **Local mode:** `"claude_code": {"mode": "local", "permission_mode": "acceptEdits"}`.
  The agent runs `claude -p --output-format json` in the repo with the prompt
  on stdin. Status and output come from that process; "done" is reported only
  when it exits.

## MCP

Configure servers in `config/agent.json`:

```json
"mcp_servers": [{"name": "docs", "command": "npx", "args": ["-y", "@some/mcp-server"],
                 "description": "Project documentation", "risk": "read"}]
```

Each becomes an agent `MCP_docs` whose tools are the server's `tools/list`
(stdio JSON-RPC, protocol 2025-06-18). Calls go through the same permission
gate with the configured `risk` (default `external`, i.e. confirmed). Every
agent tool can also describe itself MCP-style (`Tool.to_mcp()`).

## Browser

JUDO uses **Microsoft Edge** (override: `"browser": "chrome"` in api_keys.json),
and **never closes, kills or restarts a browser**:

* opening a site switches to its already-open tab if there is one (e.g. ChatGPT), else opens a new tab in the open window
* typing/clicking/pressing/reading work on the open window through Windows UI
  Automation when there is no debug port. Note: UI Automation switches the
  browser into accessibility mode, which can make a very heavy session a bit
  slower until you restart the browser yourself
* a browser you started with `--remote-debugging-port=<port>` is driven over CDP (Playwright)

## Workspaces and modes

```json
"workspaces": ["C:/Users/<you>/Projects", "D:/Projects"],
"confirmation_mode": "balanced"
```

Default workspaces (when none are set): Desktop, ~/Projects, ~/source/repos, …
that exist. See [ARCHITECTURE.md](ARCHITECTURE.md#permissions) for the modes.

## Setup and tests

```bash
pip install -r requirements.txt
python main.py                                  # the voice assistant, as before
python -m agent "open my AI Assistant project and run the tests"   # text mode, same agent
python -m pytest -q                             # 171 tests, no network or real browser needed
```
