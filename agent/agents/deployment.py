"""DeploymentAgent — inspect deployment config, deploy (Vercel / Render), check
status, and verify the live URL actually responds. Production is denied unless
config allows it, and confirmed even then."""
from __future__ import annotations

from pathlib import Path

from agent.agents.base import Agent
from agent.tools.base import EXTERNAL, FORBIDDEN, READ, ToolResult
from agent.tools.deploy import verify_url


class DeploymentAgent(Agent):
    name = "DeploymentAgent"
    description = "Deploy the current project (Vercel/Render), check deployment status, verify the deployed URL works."
    requires = ("TestingAgent",)

    def _prod(self, args: dict) -> bool:
        p, _ = self.cc.deploy.pick(self._root())
        return bool(args.get("production")) or bool(p and p.always_production)

    def _root(self) -> Path:
        return Path(self.project() or ".")

    def _detail(self, args: dict) -> tuple[str, str]:
        p, _ = self.cc.deploy.pick(self._root())
        return "Deploy project", f"Deploy {self._root().name} {'to PRODUCTION' if self._prod(args) else 'as a preview'} via {p.name if p else '?'}"

    def status(self, ref: str = "") -> ToolResult:
        p, why = self.cc.deploy.pick(self._root())
        return p.status(self._root(), ref) if p else ToolResult.fail(why, "unavailable")

    def _risk(self, args: dict) -> str:
        return FORBIDDEN if self._prod(args) and not self.cc.deploy.allow_production else EXTERNAL

    def deploy(self, production=False) -> ToolResult:
        if (e := self.need_project()):
            return e
        root = Path(self.project())
        p, why = self.cc.deploy.pick(root)
        if not p:
            return ToolResult.fail(why, "unavailable")
        prod = bool(production) or p.always_production
        if prod and not self.cc.deploy.allow_production:
            return ToolResult.fail("Production deploys aren't permitted (config/agent.json → deploy.allow_production).", "denied")
        self.emit(f"Deploying with {p.name}{' to production' if prod else ' (preview)'}.")
        return p.deploy(root, prod)

    def tools(self):
        d = self.cc.deploy
        return [
            self.tool("inspect_deployment", "Which providers are configured/connected for this project, build script, production policy.", {},
                      lambda: self.need_project() or d.inspect(self._root()), READ),
            self.tool("deploy", "Deploy the current project (preview unless production=true; user confirms). Run the build/tests first.",
                      {"production": "bool"}, self.deploy, self._risk, long_running=True, confirm_detail=self._detail),
            self.tool("deployment_status", "Recent deployments / status from the provider.", {"ref": "deployment URL or id"}, self.status, READ),
            self.tool("verify_deployment", "Fetch a deployed URL and report whether it actually responds.", {"url": "https URL"},
                      lambda url: verify_url(url), READ),
        ]
