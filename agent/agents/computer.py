"""ComputerAgent — the machine itself: find and open projects, open VS Code /
folders / apps / a terminal window, run development commands through the
controlled terminal, manage background processes, and read the screen."""
from __future__ import annotations


from agent.agents.base import Agent, int_arg
from agent.tools.base import FORBIDDEN, READ, ToolResult
from agent.tools.terminal import classify, split
from agent.workspace import resolve_project


class ComputerAgent(Agent):
    name = "ComputerAgent"
    description = ("Local computer: find/open a project, open VS Code, folders, apps or a terminal window, run dev commands "
                   "(npm, pytest, build, run app), start/stop dev servers, read the screen.")

    def _command_risk(self, args: dict) -> str:
        risk, _ = classify(args.get("command", ""))
        if risk == FORBIDDEN:
            return risk
        fs = self.cc.files
        if args.get("cwd") and fs.outside(args["cwd"]):
            return "exec:unknown"
        if risk == "destructive":
            try:
                paths = [a for a in split(args["command"])[1:] if not a.startswith("-")]
            except ValueError:
                return FORBIDDEN
            if not paths or any(fs.outside(p) for p in paths):
                return FORBIDDEN           # rm with no target, or a target outside the workspaces
        return risk

    def open_project(self, name: str) -> ToolResult:
        ws = self.cc.settings["workspaces"]
        path, ranked = resolve_project(name, ws)
        if not path:
            if ranked:
                opts = ", ".join(f"{p.name} ({p})" for _, p in ranked[:4])
                return ToolResult.fail(f"Several projects match '{name}': {opts}. Ask the user which one.", "needs_input")
            return ToolResult.fail(f"No project matching '{name}' in workspaces: {', '.join(map(str, ws))}.")
        if not self.cc.files.inside(path):
            return ToolResult.fail(f"{path} is outside the configured workspaces.", "denied")
        st = self.cc.git.status(str(path))
        branch, remote = st.data.get("branch", ""), st.data.get("remote", "")
        self.cc.ctx.set_project(str(path), path.name, branch, remote)
        return ToolResult(True, f"Project set: {path.name} at {path}\n" + (st.output if st.ok else "(not a git repository)"),
                          {"project": str(path), "branch": branch})

    def tools(self):
        cc, t = self.cc, self.cc.terminal
        wd = lambda c: c or cc.project_dir() or None     # default working dir: the current project
        return [
            self.tool("open_project", "Find a project in the workspaces by name or repo name and make it the current project.",
                      {"name": "what the user called it, or a path"}, self.open_project, READ),
            self.tool("open_in_vscode", "Open a folder (default: current project) in VS Code.", {"path": "folder"},
                      lambda path="": cc.vscode.open_folder(path and str(cc.files.resolve(path)) or cc.project_dir()), READ),
            self.tool("open_file_in_vscode", "Open a file in VS Code at a line.", {"path": "file", "line": "int"},
                      lambda path, line=0: cc.vscode.open_file(str(cc.files.resolve(path)), int_arg(line)), READ),
            self.tool("open_application", "Launch a desktop application by name.", {"name": "app name"},
                      lambda name: cc.open_application(name), READ),
            self.tool("open_folder", "Show a folder in the file manager.", {"path": "folder"}, lambda path=".": cc.open_folder(path), READ),
            self.tool("open_terminal", "Open a visible terminal window in a folder for the user.", {"path": "folder"},
                      lambda path="": cc.open_terminal(path), READ),
            self.tool("run_command", "Run ONE development command (no shell operators) and wait for it: npm install, npm run build, "
                      "pytest, python script.py, git status … Not for git push/commit (use git tools).",
                      {"command": "the command", "cwd": "folder, default project root", "timeout": "seconds"},
                      lambda command, cwd="", timeout=0: t.run(command, wd(cwd), int_arg(timeout) or None),
                      self._command_risk, long_running=True,
                      confirm_detail=lambda a: ("Run command", f"{a.get('command')}\nin {wd(a.get('cwd', '')) or 'workspace'}\n{classify(a.get('command', ''))[1]}")),
            self.tool("start_process", "Start a long-running process (dev server, watcher) in the background; returns an id and its first output.",
                      {"command": "e.g. npm run dev", "cwd": "folder"},
                      lambda command, cwd="": t.start(command, wd(cwd)), self._command_risk,
                      confirm_detail=lambda a: ("Start process", f"{a.get('command')}\nin {wd(a.get('cwd', '')) or 'workspace'}")),
            self.tool("read_process", "New output from a background process.", {"proc_id": "id"}, lambda proc_id: t.read(proc_id), READ),
            self.tool("stop_process", "Stop a background process (omit id to stop all the agent started).", {"proc_id": "id"},
                      lambda proc_id="": t.stop(proc_id), READ),
            self.tool("read_screen", "Screenshot the screen and describe it (visible errors, dialogs, text).", {"question": "what to look for"},
                      lambda question="": cc.read_screen(question), READ),
            self.tool("screen_input", "Type text or press a hotkey into the focused window (last resort — prefer specific tools).",
                      {"action": "type | hotkey | press", "text": "text to type", "keys": "e.g. ctrl+s", "key": "e.g. enter"},
                      lambda action, **kw: cc.screen_input(action, **kw), "exec:unknown",
                      confirm_detail=lambda a: ("Control keyboard", f"{a.get('action')}: {a.get('text') or a.get('keys') or a.get('key')}")),
        ]
