"""
agent/tools/vscode.py — VS Code through its `code` launcher (the same thing
right-click → "Open with Code" runs). Reuses actions/open_folder's discovery.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from agent.tools.base import ToolResult, VSCodeController


class CodeCLI(VSCodeController):
    def _exe(self) -> str | None:
        from actions.open_folder import _find_vscode
        return _find_vscode()

    def available(self) -> tuple[bool, str]:
        return (True, "") if self._exe() else (False, "VS Code isn't installed (no `code` command found).")

    def _launch(self, *args: str) -> ToolResult:
        exe = self._exe()
        if not exe:
            return ToolResult.fail(self.available()[1], "unavailable")
        try:
            subprocess.Popen([exe, *args], shell=exe.lower().endswith((".cmd", ".bat")),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return ToolResult(True, f"Opened in VS Code: {' '.join(args)}")
        except OSError as e:
            return ToolResult.fail(f"Could not launch VS Code: {e}")

    def open_folder(self, path: str) -> ToolResult:
        p = Path(path)
        return self._launch(str(p)) if p.is_dir() else ToolResult.fail(f"Not a folder: {p}")

    def open_file(self, path: str, line: int = 0) -> ToolResult:
        p = Path(path)
        if not p.exists():
            return ToolResult.fail(f"No such file: {p}")
        return self._launch("-r", "-g", f"{p}:{int(line)}") if line else self._launch("-r", str(p))
