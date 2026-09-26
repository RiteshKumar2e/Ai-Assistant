"""
agent/tools/deploy.py — DeploymentProvider: detect, deploy, status, verify.

    DeploymentProvider
    ├── VercelProvider   `vercel` CLI (logged in via `vercel login`, or vercel_token in config/api_keys.json)
    │                    preview deploys by default; --prod only when allowed (see below)
    └── RenderProvider   Render REST API: render_api_key (config/api_keys.json) + the service id
                         (config/agent.json → deploy.render_service_id). Render deploys a
                         service's live URL, so it always counts as production.

Production is DENIED unless config/agent.json has "deploy": {"allow_production": true},
and even then it is confirmed (EXTERNAL). "Deployed" is reported only when the
provider says the deployment is ready/live, and verify() then fetches the URL.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

import requests

from agent.credentials import secret
from agent.tools.base import ToolResult, redact


class DeploymentProvider(ABC):
    name = ""
    always_production = False

    @abstractmethod
    def available(self) -> tuple[bool, str]: ...
    @abstractmethod
    def detected(self, root: Path) -> bool: ...
    @abstractmethod
    def deploy(self, root: Path, production: bool) -> ToolResult: ...
    @abstractmethod
    def status(self, root: Path, ref: str = "") -> ToolResult: ...


class VercelProvider(DeploymentProvider):
    name = "vercel"

    def _cli(self, root: Path, *args, timeout=600) -> tuple[int, str]:
        exe = shutil.which("vercel")
        tok = ["--token", secret("vercel_token")] if secret("vercel_token") else []
        try:
            p = subprocess.run([exe, *args, *tok], cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, shell=exe.lower().endswith((".cmd", ".bat")))
            return p.returncode, redact((p.stdout or "") + "\n" + (p.stderr or "")).strip()
        except subprocess.TimeoutExpired:
            return 124, f"vercel timed out after {timeout}s"

    _cache: tuple[float, bool, str] = (0.0, False, "")

    def available(self):
        if not shutil.which("vercel"):
            return False, "Vercel CLI isn't installed (npm i -g vercel)."
        at, ok, why = VercelProvider._cache
        if time.monotonic() - at < 300:
            return ok, why
        code, _ = self._cli(Path.cwd(), "whoami", timeout=30)
        ok, why = (True, "") if code == 0 else (False, "Vercel isn't connected (run `vercel login` or add vercel_token to config/api_keys.json).")
        VercelProvider._cache = (time.monotonic(), ok, why)
        return ok, why

    def detected(self, root):
        return (root / "vercel.json").exists() or (root / ".vercel" / "project.json").exists()

    def deploy(self, root, production):
        code, out = self._cli(root, "deploy", "--yes", *(["--prod"] if production else []), timeout=900)
        urls = re.findall(r"https://[\w.-]+\.vercel\.app\S*", out)
        if code or not urls:
            return ToolResult.fail(f"Vercel deploy failed (exit {code}):\n{out[-3000:]}")
        url = urls[-1].rstrip(".,)")
        st = self.status(root, url)
        if not st.ok:
            return ToolResult.fail(f"Vercel returned {url} but the deployment is not ready:\n{st.output}", deployment_url=url)
        return ToolResult(True, f"Vercel deployment ready ({'production' if production else 'preview'}): {url}",
                          {"deployment_url": url, "production": production})

    def status(self, root, ref=""):
        code, out = self._cli(root, "inspect", ref, "--wait", timeout=600) if ref else self._cli(root, "ls", timeout=60)
        ready = code == 0 and (not ref or re.search(r"(?i)\b(ready|●\s*Ready)\b", out))
        return ToolResult(bool(ready), out[-3000:])


class RenderProvider(DeploymentProvider):
    name = "render"
    always_production = True
    API = "https://api.render.com/v1"

    def __init__(self, service_id: str = ""):
        self.service_id = service_id

    def _h(self):
        return {"Authorization": f"Bearer {secret('render_api_key')}", "Accept": "application/json"}

    def available(self):
        if not secret("render_api_key"):
            return False, "Render isn't connected (add render_api_key to config/api_keys.json)."
        return (True, "") if self.service_id else (False, "Render service id not set (config/agent.json → deploy.render_service_id).")

    def detected(self, root):
        return (root / "render.yaml").exists()

    def deploy(self, root, production=True):
        r = requests.post(f"{self.API}/services/{self.service_id}/deploys", headers=self._h(), json={}, timeout=30)
        if r.status_code >= 400:
            return ToolResult.fail(f"Render refused the deploy (HTTP {r.status_code}): {r.text[:300]}")
        dep = r.json()
        for _ in range(120):                                     # up to ~20 min
            time.sleep(10)
            st = requests.get(f"{self.API}/services/{self.service_id}/deploys/{dep['id']}", headers=self._h(), timeout=30).json()
            s = st.get("status", "")
            if s == "live":
                url = requests.get(f"{self.API}/services/{self.service_id}", headers=self._h(), timeout=30).json() \
                    .get("serviceDetails", {}).get("url", "")
                return ToolResult(True, f"Render deploy {dep['id']} is live: {url}", {"deployment_url": url, "production": True})
            if s in ("build_failed", "update_failed", "canceled", "deactivated", "pre_deploy_failed"):
                return ToolResult.fail(f"Render deploy {dep['id']} ended with status {s}.")
        return ToolResult.fail(f"Render deploy {dep['id']} still not live after 20 minutes — check the Render dashboard.")

    def status(self, root, ref=""):
        r = requests.get(f"{self.API}/services/{self.service_id}/deploys", headers=self._h(), params={"limit": 3}, timeout=30)
        if r.status_code >= 400:
            return ToolResult.fail(f"Render API HTTP {r.status_code}")
        rows = [f"{d['deploy']['id']} {d['deploy']['status']} {d['deploy'].get('finishedAt') or ''}" for d in r.json()]
        return ToolResult(True, "\n".join(rows) or "No deploys.")


def verify_url(url: str) -> ToolResult:
    if not re.match(r"https?://", url or ""):
        return ToolResult.fail("Need a deployment URL to verify.")
    try:
        r = requests.get(url, timeout=25, allow_redirects=True, headers={"User-Agent": "judo-deploy-check"})
    except requests.RequestException as e:
        return ToolResult.fail(f"{url} is not reachable: {e}")
    title = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
    t = f" — “{title.group(1).strip()[:80]}”" if title else ""
    if r.status_code >= 400:
        return ToolResult.fail(f"{url} responds HTTP {r.status_code}{t} — the deployment is NOT working.")
    return ToolResult(True, f"{url} responds HTTP {r.status_code} in {r.elapsed.total_seconds():.1f}s{t}.", {"url": url})


class Deployment:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.allow_production = bool(cfg.get("allow_production"))
        self.preferred = cfg.get("provider", "")
        self.providers = {"vercel": VercelProvider(), "render": RenderProvider(cfg.get("render_service_id", ""))}

    def status_all(self) -> dict:
        return {n: p.available() for n, p in self.providers.items()}

    def pick(self, root: Path) -> tuple[DeploymentProvider | None, str]:
        order = ([self.preferred] if self.preferred in self.providers else []) + \
                [n for n, p in self.providers.items() if p.detected(root)] + list(self.providers)
        reasons = []
        for n in dict.fromkeys(order):
            ok, why = self.providers[n].available()
            if ok:
                return self.providers[n], ""
            reasons.append(why)
        return None, "No deployment provider is ready. " + " / ".join(dict.fromkeys(reasons))

    def inspect(self, root: Path) -> ToolResult:
        found = {n: p.detected(root) for n, p in self.providers.items()}
        conn = self.status_all()
        pkg = root / "package.json"
        build = ""
        if pkg.is_file():
            try:
                build = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}).get("build", "")
            except (json.JSONDecodeError, OSError):
                pass
        lines = [f"{n}: {'config found' if found[n] else 'no config'} · {'connected' if conn[n][0] else conn[n][1]}" for n in self.providers]
        lines.append(f"Production deploys: {'allowed by config' if self.allow_production else 'NOT allowed (deploy.allow_production is false)'}")
        if build:
            lines.append(f"Build script: {build}")
        return ToolResult(True, "\n".join(lines), {"detected": [n for n, v in found.items() if v]})
