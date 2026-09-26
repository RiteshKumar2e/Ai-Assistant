"""
agent/config.py — agent settings, read from config/agent.json (optional).

Every field has a default, so the agent works with no file at all. The file is
gitignored because workspace paths are personal; config/agent.example.json is
the documented template. No secrets live here — API keys stay in
config/api_keys.json, and GitHub auth is whatever `gh` / git's credential
manager already holds.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from core import user_paths


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = _base_dir()
CONFIG_PATH = BASE_DIR / "config" / "agent.json"
DATA_DIR    = BASE_DIR / "memory" / "agent"          # prompts, state — gitignored

MODES = ("strict", "balanced", "trusted")

DEFAULTS: dict = {
    "confirmation_mode": "balanced",
    "workspaces": [],                 # [] → default_workspaces()
    "max_iterations": 30,
    "task_timeout_s": 1200,
    "command_timeout_s": 300,
    "protected_branches": ["main", "master", "production", "release"],
    "claude_code": {"mode": "copy", "cli": "claude", "permission_mode": "acceptEdits", "timeout_s": 1800},
    "mcp_servers": [],                # [{"name", "command", "args", "description", "risk"}]
    "contacts": {},                   # {"Rahul": {"telegram": "…", "slack": "…", "whatsapp": "+91…", "email": "…"}}
    "email_provider": "",             # "" = first connected of gmail, outlook
    "calendar_provider": "",          # "" = google if connected, else local
    "deploy": {"provider": "", "allow_production": False, "render_service_id": ""},
    "decision_provider": {},          # optional fast model: {"base_url", "model"} — key: api_keys.json decision_api_key
    "expected_remote": "",            # optional: pushes to any other remote are flagged
}


def default_workspaces() -> list[Path]:
    home = Path.home()
    cands = [user_paths.desktop(), home / "Projects", home / "projects", home / "source" / "repos",
             user_paths.documents() / "GitHub", home / "code", home / "dev"]
    seen, out = set(), []
    for c in cands:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r.is_dir() and str(r).lower() not in seen:
            seen.add(str(r).lower()); out.append(r)
    return out


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path: Path | None = None) -> dict:
    """Settings dict with defaults filled in. Never raises."""
    data: dict = {}
    try:
        raw = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))
        data = {k: v for k, v in raw.items() if not k.startswith("_")} if isinstance(raw, dict) else {}
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Agent] config/agent.json unreadable ({e}) — using defaults")
    cfg = _merge(DEFAULTS, data)
    if cfg["confirmation_mode"] not in MODES:
        print(f"[Agent] unknown confirmation_mode {cfg['confirmation_mode']!r} — using 'balanced'")
        cfg["confirmation_mode"] = "balanced"
    ws = [Path(p).expanduser() for p in cfg.get("workspaces") or []]
    cfg["workspaces"] = [p.resolve() for p in ws if p.is_dir()] or default_workspaces()
    return cfg
