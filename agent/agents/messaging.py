"""MessagingAgent — find a configured contact, draft, and (after confirmation)
send through an official provider API: Telegram, Slack, Discord, WhatsApp Cloud."""
from __future__ import annotations

from agent.agents.base import Agent
from agent.tools.base import EXTERNAL, READ


class MessagingAgent(Agent):
    name = "MessagingAgent"
    description = "Chat messages to configured contacts/channels via Telegram, Slack, Discord or WhatsApp Cloud API: find contact, draft, send (confirmed)."

    def _detail(self, a: dict) -> tuple[str, str]:
        p, _, err = self.cc.messaging.route(a.get("to", ""), a.get("channel", ""))
        via = p.name if p else f"? ({err})"
        return "Send message", f"To {a.get('to')} via {via}:\n“{a.get('text')}”"

    def tools(self):
        m = self.cc.messaging
        return [
            self.tool("list_contacts", "Configured contacts and the channels each can be reached on.", {}, m.list_contacts, READ),
            self.tool("draft_message", "Prepare a message and show which channel it would go through. Sends nothing.",
                      {"to": "contact name", "text": "message", "channel": "optional telegram|slack|discord|whatsapp"},
                      lambda to, text, channel="": m.preview(to, text, channel), READ),
            self.tool("send_message", "Send a message (the user confirms first). Reported sent only if the provider returns a message id.",
                      {"to": "contact name (or raw id with channel)", "text": "message", "channel": "optional"},
                      lambda to, text, channel="": m.send(to, text, channel), EXTERNAL, confirm_detail=self._detail),
        ]
