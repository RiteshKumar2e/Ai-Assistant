"""GitHubAgent — the GitHub side, through the user's own `gh` login: repository
info, issues, pull requests, and creating a PR (confirmed first)."""
from __future__ import annotations

from agent.agents.base import Agent, int_arg
from agent.tools.base import EXTERNAL, READ, ToolResult
from agent.tools.github import owner_repo


class GitHubAgent(Agent):
    name = "GitHubAgent"
    description = "GitHub (needs `gh` login): repository info, issues, pull requests, create a pull request."
    requires = ("GitAgent",)

    def _slug(self, repo: str) -> str:
        if repo and "/" in repo:
            return repo
        url = self.cc.ctx.remote or (self.project() and self.cc.git.remote_url(self.project())) or ""
        return owner_repo(url)

    def _need(self, repo):
        slug = self._slug(repo)
        return slug, (None if slug else ToolResult.fail("No GitHub repository known — open a project with a GitHub remote, or pass owner/repo."))

    def tools(self):
        gh = self.cc.github
        def wrap(fn):
            def call(repo="", **kw):
                slug, err = self._need(repo)
                return err or fn(slug, **kw)
            return call
        return [
            self.tool("gh_repo", "Repository summary: visibility, default branch, last push.", {"repo": "owner/repo, default current"},
                      wrap(lambda s: gh.repo(s)), READ),
            self.tool("gh_issues", "List issues.", {"repo": "owner/repo", "state": "open|closed|all", "limit": "int"},
                      wrap(lambda s, state="open", limit=15: gh.issues(s, state, int_arg(limit, 15))), READ),
            self.tool("gh_prs", "List pull requests.", {"repo": "owner/repo", "state": "open|closed|merged|all", "limit": "int"},
                      wrap(lambda s, state="open", limit=15: gh.prs(s, state, int_arg(limit, 15))), READ),
            self.tool("gh_create_pr", "Open a pull request from the current (already pushed) branch.",
                      {"title": "PR title", "body": "description", "base": "target branch", "draft": "bool"},
                      lambda title, body="", base="", draft=False: gh.create_pr(self.project(), title, body, base, bool(draft)),
                      EXTERNAL, confirm_detail=lambda a: ("Create pull request", f"{a.get('title')}\n→ {a.get('base') or 'default branch'}")),
        ]
