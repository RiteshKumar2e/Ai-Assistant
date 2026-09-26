"""GeneralAgent — conversation and questions that need no tool. It owns no tools:
when the supervisor routes here, the planner's direct reply is the answer."""
from __future__ import annotations

from agent.agents.base import Agent


class GeneralAgent(Agent):
    name = "GeneralAgent"
    description = "Greetings, small talk, and questions answerable from general knowledge without touching the computer or the web."
