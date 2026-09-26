"""
agent/orchestration/permissions.py — who may do what, enforced below the model.

                     strict    balanced   trusted
    read             allow     allow      allow
    write            confirm   allow      allow        (in-workspace edits, git add/commit)
    exec             confirm   allow      allow        (recognised dev commands)
    exec:unknown     confirm   confirm    allow
    exec:inline      confirm   confirm    allow        (python -c / node -e)
    external         confirm   confirm    allow*       (git push, PR create, publish, form submit)
    destructive      confirm   confirm    confirm      (delete files/branches, uninstall, kill)
    forbidden        deny      deny       deny         (disk/OS/credential tools, shells)

    * trusted still confirms an external action the preflight flagged
      (protected branch, unexpected remote, …).
    Anything outside the configured workspaces is escalated to at least confirm.

An Approval is the only thing tools/git.push accepts. It is minted by
ConfirmationBroker after a human said yes (or the table said allow), is bound to
a scope string describing exactly what was approved, and is single-use.
"""
from __future__ import annotations

import secrets
import sys
import threading
from dataclasses import dataclass
from typing import Callable

ALLOW, CONFIRM, DENY = "allow", "confirm", "deny"

_TABLE = {
    "read":         (ALLOW,   ALLOW,   ALLOW),
    "write":        (CONFIRM, ALLOW,   ALLOW),
    "exec":         (CONFIRM, ALLOW,   ALLOW),
    "exec:unknown": (CONFIRM, CONFIRM, ALLOW),
    "exec:inline":  (CONFIRM, CONFIRM, ALLOW),
    "external":     (CONFIRM, CONFIRM, ALLOW),
    "destructive":  (CONFIRM, CONFIRM, CONFIRM),
    "forbidden":    (DENY,    DENY,    DENY),
}
_IDX = {"strict": 0, "balanced": 1, "trusted": 2}


def decide(risk: str, mode: str, *, outside_workspace: bool = False, flagged: bool = False) -> str:
    risk = "external" if risk == "exec:external" else risk
    d = _TABLE.get(risk, _TABLE["exec:unknown"])[_IDX.get(mode, 1)]
    if d == ALLOW and (outside_workspace or (flagged and risk == "external")):
        d = CONFIRM
    return d


_MINT = object()


@dataclass
class Approval:
    scope: str
    _token: object = None
    _used: bool = False

    def __post_init__(self):
        if self._token is not _MINT:
            raise PermissionError("Approvals are issued by ConfirmationBroker only.")

    def consume(self, scope: str) -> bool:
        if self._used or not secrets.compare_digest(self.scope, scope):
            return False
        self._used = True
        return True


class ConfirmationBroker:
    """Asks a human. `asker(title, detail) -> bool` blocks the task thread (never
    the UI thread) until the answer arrives; False on cancel or timeout."""

    def __init__(self, asker: Callable[[str, str], bool] | None = None):
        self._asker = asker or hud_asker

    def ask(self, title: str, detail: str) -> bool:
        try:
            return bool(self._asker(title, detail))
        except Exception as e:
            print(f"[Agent] confirmation failed: {e}")
            return False

    def approve(self, scope: str, title: str, detail: str, *, ask: bool = True) -> Approval | None:
        if ask and not self.ask(title, detail):
            return None
        return Approval(scope, _MINT)


def hud_asker(title: str, detail: str, timeout: float | None = None) -> bool:
    """Put the banner on JUDO's HUD (core/confirm.py) and wait for the button."""
    from core import confirm
    done, answer = threading.Event(), {"ok": False}
    def yes():
        answer["ok"] = True; done.set(); return "Approved."
    msg = confirm.request(f"agent:{title[:40]}", title, detail, yes, on_cancel=done.set)
    if "[CONFIRMATION_PENDING]" not in msg:          # no interface bound → refuse
        return False
    done.wait(timeout or confirm.TIMEOUT_SECONDS + 5)
    return answer["ok"]


def console_asker(title: str, detail: str) -> bool:
    """For `python -m agent` — a person at the terminal answers."""
    if not sys.stdin or not sys.stdin.isatty():
        return False
    print(f"\n⚠  {title}\n{detail}")
    try:
        return input("Proceed? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False
