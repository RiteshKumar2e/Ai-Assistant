"""TestingAgent — detect the project type and run its tests, build and linter,
extract the failures that matter, and record the result so a later push knows
whether the tests passed on exactly the code being pushed. Failures go back to
the CodingAgent (the supervisor sees both) and are re-run after the fix."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from agent.agents.base import Agent
from agent.orchestration.state import TestRun
from agent.tools.base import EXEC, READ, ToolResult


def detect_stack(root: Path) -> dict:
    """Language/framework and test/build/lint/run commands, from files that exist."""
    info: dict = {"stack": [], "test_command": "", "build_command": "", "lint_command": "", "run_command": ""}
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}) or {}
        except (json.JSONDecodeError, OSError):
            scripts = {}
        pm = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
        info["stack"].append(f"Node ({pm})")
        if "test" in scripts and "no test specified" not in scripts["test"]:
            info["test_command"] = f"{pm} test"
        info["build_command"] = f"{pm} run build" if "build" in scripts else ""
        info["lint_command"] = f"{pm} run lint" if "lint" in scripts else ""
        info["run_command"] = next((f"{pm} run {s}" for s in ("dev", "start", "serve") if s in scripts), "")
    if any((root / f).exists() for f in ("pyproject.toml", "setup.py", "requirements.txt", "setup.cfg")):
        info["stack"].append("Python")
        if not info["test_command"] and ((root / "tests").is_dir() or (root / "pytest.ini").exists() or list(root.glob("test_*.py"))):
            info["test_command"] = "python -m pytest -q"
        if not info["lint_command"] and ((root / "ruff.toml").exists() or "ruff" in _read(root / "pyproject.toml")):
            info["lint_command"] = "ruff check ."
        info["build_command"] = info["build_command"] or "python -m compileall -q ."
        for entry in ("main.py", "app.py", "manage.py"):
            if (root / entry).exists() and not info["run_command"]:
                info["run_command"] = f"python {entry}" + (" runserver" if entry == "manage.py" else "")
    for marker, lang, test, build in (("Cargo.toml", "Rust", "cargo test", "cargo build"), ("go.mod", "Go", "go test ./...", "go build ./..."),
                                      ("pubspec.yaml", "Flutter/Dart", "flutter test", "flutter build"),
                                      ("pom.xml", "Java (Maven)", "mvn -q test", "mvn -q package"),
                                      ("build.gradle", "Java/Kotlin (Gradle)", "gradle test", "gradle build")):
        if (root / marker).exists():
            info["stack"].append(lang)
            info["test_command"] = info["test_command"] or test
            info["build_command"] = info["build_command"] or build
    info["stack"] = ", ".join(info["stack"]) or "unknown"
    return info


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def failure_summary(output: str) -> str:
    """The lines that matter from a run: failures, errors and the final tally."""
    keep = [l for l in output.splitlines() if re.search(
        r"(?i)(\bfailed\b|\bpassed\b|\berror\b|FAIL|✕|✗|assert|Tests?:|test result|\d+ (passing|failing)|warning:)", l)]
    return "\n".join(keep[-25:]) or output[-1500:]


class TestingAgent(Agent):
    name = "TestingAgent"
    description = "Detect project type and run its tests, build and lint; report failures precisely (fixes go to CodingAgent, then re-test)."

    def stack(self) -> ToolResult:
        if (e := self.need_project()):
            return e
        info = detect_stack(Path(self.project()))
        return ToolResult(True, "\n".join(f"{k}: {v or '—'}" for k, v in info.items()), info)

    def _run(self, kind: str, command: str) -> ToolResult:
        if (e := self.need_project()):
            return e
        command = command or detect_stack(Path(self.project()))[f"{kind}_command"]
        if not command:
            return ToolResult.fail(f"I couldn't find a {kind} command for this project. Pass one explicitly.", "unavailable")
        label = {"test": "the test suite", "build": "the build", "lint": "the linter"}[kind]
        self.emit(f"Running {label}: {command}")
        res = self.cc.terminal.run(command, self.project())
        if res.status in ("denied", "unavailable", "cancelled"):
            return res
        summ = failure_summary(res.output)
        if kind == "test":
            self.cc.ctx.last_test = TestRun(command, res.ok, summ, time.time(), self.cc.git.worktree_id(self.project()))
            self.emit("The tests are passing." if res.ok else "Some tests failed — investigating.", error=not res.ok)
        elif not res.ok:
            self.emit(f"The {kind} failed — investigating.", error=True)
        res.output = f"{kind.upper()} {'PASSED' if res.ok else 'FAILED'}: {command}\n{summ}\n\n--- output (tail) ---\n{res.output[-6000:]}"
        res.data.update(command=command, passed=res.ok)
        return res

    def tools(self):
        return [
            self.tool("project_stack", "Detect language/framework and the test/build/lint/run commands of the current project.", {}, self.stack, READ),
            self.tool("run_tests", "Run the project's tests (auto-detected unless given); records pass/fail for push checks.",
                      {"command": "optional explicit test command"}, lambda command="": self._run("test", command), EXEC, long_running=True),
            self.tool("run_build", "Build/compile the project (auto-detected unless given).", {"command": "optional"},
                      lambda command="": self._run("build", command), EXEC, long_running=True),
            self.tool("run_lint", "Run the project's linter if it has one.", {"command": "optional"},
                      lambda command="": self._run("lint", command), EXEC, long_running=True),
        ]
