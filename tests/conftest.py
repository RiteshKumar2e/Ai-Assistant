import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def no_real_judo_browser(monkeypatch):
    """browser_control sends to JUDO Browser first and would LAUNCH it — no
    test may open a real window. Tests of that routing fake the client."""
    import actions.browser_control as bc
    monkeypatch.setattr(bc, "_judo_browser_default", lambda: False)


@pytest.fixture(autouse=True)
def no_real_judo_mail(monkeypatch, tmp_path):
    """open_app("mail") / go_to gmail.com start or focus JUDO Mail — no test may
    launch the real app or poke one the user has open. Tests of that path fake
    running()/focus()/start() themselves."""
    from judo_mail import client as mc
    monkeypatch.setattr(mc, "CONTROL_FILE", tmp_path / "judo-mail-control.json")
    monkeypatch.setattr(mc, "_mutex_exists", lambda: False)
    monkeypatch.setattr(mc, "start", lambda: pytest.fail("a test tried to launch the real JUDO Mail"))


# ── Agent test doubles ───────────────────────────────────────────────────────
class ScriptedLLM:
    """Stands in for the model. `plan` answers the planner prompt; `actions` are
    returned one per execution-step prompt; anything else gets `summary`."""

    def __init__(self, plan=None, actions=(), summary="Summary."):
        self.plan, self.actions, self.summary = plan or {"intent": "task", "agents": []}, list(actions), summary
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "planning module" in prompt:
            return json.dumps(self.plan)
        if "execution module" in prompt:
            return json.dumps(self.actions.pop(0) if self.actions else {"done": True})
        return self.summary


class Answers:
    """ConfirmationBroker asker: records what was asked, answers from a list."""

    def __init__(self, *answers: bool):
        self.answers, self.asked = list(answers), []

    def __call__(self, title, detail):
        self.asked.append((title, detail))
        return self.answers.pop(0) if self.answers else False


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A workspace containing a Python project in git, with a bare 'remote' origin."""
    ws = tmp_path / "ws"
    proj = ws / "demo-app"
    (proj / "tests").mkdir(parents=True)
    (proj / "app.py").write_text("def login(user, pw):\n    return user == 'admin' and pw == 'secret'\n", encoding="utf-8")
    (proj / "tests" / "test_app.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n"
        "from app import login\n\ndef test_login():\n    assert login('admin', 'secret')\n", encoding="utf-8")
    (proj / "requirements.txt").write_text("", encoding="utf-8")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    for k, v in (("user.name", "Test"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        git(proj, "config", k, v)
    git(proj, "checkout", "-q", "-b", "feature/login-fix")
    git(proj, "add", "-A"); git(proj, "commit", "-q", "-m", "init")
    git(proj, "remote", "add", "origin", str(remote))
    git(proj, "push", "-q", "-u", "origin", "feature/login-fix")
    return proj


@pytest.fixture
def make_service(tmp_path):
    """AgentService on a temp workspace with a scripted LLM and a scripted human."""
    from agent import config
    from agent.orchestration.state import TaskContext
    from agent.runtime import AgentService

    controllers = {"messaging", "email", "calendar", "deploy", "browser", "git", "github", "files", "terminal", "vscode", "web", "claude"}

    def make(llm, asker=None, mode="balanced", **over):
        settings = config.load(tmp_path / "none.json")
        settings.update({"workspaces": [(tmp_path / "ws").resolve()], "confirmation_mode": mode, "max_iterations": 12,
                         **{k: v for k, v in over.items() if k not in controllers}})
        (tmp_path / "ws").mkdir(exist_ok=True)
        ctx = TaskContext(state_file=tmp_path / "state.json")
        return AgentService(settings=settings, llm=llm, asker=asker or Answers(), ctx=ctx,
                            **{k: v for k, v in over.items() if k in controllers})
    return make
