"""
agent/integrations/mcp.py — optional MCP (Model Context Protocol) servers as agents.

Configure in config/agent.json:
    "mcp_servers": [{"name": "github", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                     "description": "GitHub via MCP", "risk": "external", "env": {}}]

Each server becomes one agent (MCP_<name>) whose tools are the server's own
`tools/list`. Every call still goes through the executor's permission gate with
the configured risk — default "external", i.e. confirmed in strict/balanced —
because a third-party server's side effects can't be known from here.
Nothing here is needed for normal operation; a server that fails to start is
logged and skipped.
"""
from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import threading

from agent.agents.base import Agent
from agent.tools.base import ToolResult, redact

PROTOCOL = "2025-06-18"


class McpClient:
    def __init__(self, command: str, args: list[str], env: dict | None = None, timeout: float = 30):
        exe = shutil.which(command) or command
        self.p = subprocess.Popen([exe, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, encoding="utf-8", env={**os.environ, **(env or {})},
                                  shell=exe.lower().endswith((".cmd", ".bat")))
        self.timeout, self._ids, self._lock = timeout, itertools.count(1), threading.Lock()
        self._waiting: dict[int, tuple[threading.Event, dict]] = {}
        threading.Thread(target=self._reader, daemon=True, name="mcp-reader").start()

    def _reader(self) -> None:
        for line in self.p.stdout:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (w := self._waiting.pop(m.get("id"), None)):
                w[1]["m"] = m; w[0].set()

    def _send(self, msg: dict) -> None:
        with self._lock:
            self.p.stdin.write(json.dumps(msg) + "\n"); self.p.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        rid, done, box = next(self._ids), threading.Event(), {}
        self._waiting[rid] = (done, box)
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        if not done.wait(self.timeout):
            self._waiting.pop(rid, None)
            raise TimeoutError(f"MCP {method} timed out")
        if "error" in box["m"]:
            raise RuntimeError(box["m"]["error"].get("message", "MCP error"))
        return box["m"].get("result", {})

    def initialize(self) -> list[dict]:
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "judo-agent", "version": "1.0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self.request("tools/list").get("tools", [])

    def call(self, name: str, arguments: dict) -> ToolResult:
        try:
            r = self.request("tools/call", {"name": name, "arguments": arguments})
        except Exception as e:
            return ToolResult.fail(f"MCP tool {name} failed: {e}")
        text = "\n".join(c.get("text", "") for c in r.get("content", []) if c.get("type") == "text")
        return ToolResult(not r.get("isError"), redact(text) or json.dumps(r)[:4000])

    def close(self) -> None:
        try:
            self.p.kill()
        except Exception:
            pass


class McpAgent(Agent):
    def __init__(self, cc, cfg: dict, client: McpClient, tools: list[dict]):
        super().__init__(cc)
        self.name = f"MCP_{cfg['name']}"
        self.description = cfg.get("description") or f"MCP server '{cfg['name']}': " + ", ".join(t["name"] for t in tools[:8])
        self._client, self._specs, self._risk = client, tools, cfg.get("risk", "external")

    def tools(self):
        out = []
        for spec in self._specs:
            props = (spec.get("inputSchema") or {}).get("properties", {})
            params = {k: str(v.get("description") or v.get("type", ""))[:80] for k, v in props.items()}
            n = spec["name"]
            out.append(self.tool(f"mcp_{self.name[4:]}_{n}"[:64], (spec.get("description") or n)[:300], params,
                                 lambda _n=n, **kw: self._client.call(_n, kw), self._risk))
        return out


def load_mcp_agents(cc, servers: list[dict]) -> list[McpAgent]:
    agents = []
    for cfg in servers or []:
        if not cfg.get("name") or not cfg.get("command"):
            continue
        try:
            client = McpClient(cfg["command"], cfg.get("args", []), cfg.get("env"))
            agents.append(McpAgent(cc, cfg, client, client.initialize()))
            print(f"[Agent] MCP server '{cfg['name']}' connected")
        except Exception as e:
            print(f"[Agent] MCP server '{cfg['name']}' unavailable: {e}")
    return agents
