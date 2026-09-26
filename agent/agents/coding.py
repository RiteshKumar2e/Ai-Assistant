"""CodingAgent — understand a codebase and either fix things directly (through
FileAgent's search/read/edit tools, which it pulls in) or hand a precise,
context-grounded task to Claude Code. Testing is delegated to TestingAgent,
also pulled in, so a fix is always followed by a re-test."""
from __future__ import annotations

import time
from pathlib import Path

from agent.agents.base import Agent, list_arg
from agent.agents.testing import detect_stack
from agent.tools.base import EXEC, READ, ToolResult


class CodingAgent(Agent):
    name = "CodingAgent"
    description = ("Code work in the current project: inspect architecture, reproduce/debug issues, patch code (via file tools), "
                   "prepare a Claude Code task prompt, or run Claude Code locally when configured.")
    requires = ("FileAgent", "TestingAgent")

    def _context(self, files: list[str], suspected: str, error: str) -> dict:
        root = self.project()
        st = self.cc.git.status(root) if root else None
        info = detect_stack(Path(root)) if root else {}
        return {"repo": root, "branch": (st.data.get("branch") if st and st.ok else ""),
                "stack": info.get("stack", ""), "test_command": info.get("test_command", ""),
                "files": files or sorted(self.cc.ctx.files_modified), "suspected": suspected,
                "error": error or self.cc.ctx.last_error or (self.cc.ctx.last_test and not self.cc.ctx.last_test.ok and self.cc.ctx.last_test.summary) or "",
                "git_status": st.output[:1500] if st and st.ok else "",
                "tree": self.cc.files.tree(root, 2).output[:2500] if root else ""}

    def claude_prompt(self, task: str, files="", suspected: str = "", error: str = "") -> ToolResult:
        res = self.cc.claude.generate_prompt(task, self._context(list_arg(files), suspected, error))
        if not res.ok:
            return res
        self.cc.ctx.last_claude_prompt = res.output
        show = self.sv.get("show_prompt")
        if show:
            show(res.output, self.project(), res.data["prompt_path"])
        ok_local, why = self.cc.claude.local_available()
        tail = ("Local Claude Code is available — call claude_code_run to execute it." if ok_local
                else f"{why} The prompt is on screen with a Copy Prompt button.")
        res.output = f"Claude Code prompt prepared ({res.data['built_by']}, saved to {res.data['prompt_path']}). {tail}\n\n{res.output}"
        return res

    def claude_run(self) -> ToolResult:
        if (e := self.need_project()):
            return e
        if not self.cc.ctx.last_claude_prompt:
            return ToolResult.fail("No Claude Code prompt prepared yet — call claude_code_prompt first.")
        return self.cc.claude.execute(self.cc.ctx.last_claude_prompt, self.project())

    def claude_wait(self, job: str, seconds: int = 600) -> ToolResult:
        end = time.monotonic() + min(int(seconds or 600), 1800)
        while time.monotonic() < end and not self.cc.cancel_event.is_set():
            st = self.cc.claude.get_status(job)
            if not st.ok or st.data.get("state") == "done":
                out = self.cc.claude.get_output(job)
                return ToolResult(out.ok and st.ok, f"{st.output}\n{out.output}")
            time.sleep(3)
        return ToolResult(True, f"Claude Code job {job} is still running; check again with claude_code_status.", {"state": "running"})

    def tools(self):
        return [
            self.tool("claude_code_prompt", "Prepare a precise Claude Code task prompt from the real repo context and show it with a Copy button. "
                      "Use for larger changes or when the user asks for Claude Code.",
                      {"task": "what needs doing", "files": "relevant files (comma list)", "suspected": "suspected cause", "error": "error text"},
                      self.claude_prompt, READ),
            self.tool("claude_code_run", "Run the prepared prompt with local Claude Code (only works when configured).", {},
                      self.claude_run, EXEC, long_running=True),
            self.tool("claude_code_wait", "Wait for a local Claude Code job to finish and get its real output.", {"job": "job id", "seconds": "max wait"},
                      self.claude_wait, READ, long_running=True),
        ]
