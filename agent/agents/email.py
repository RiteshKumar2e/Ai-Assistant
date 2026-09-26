"""EmailAgent — Gmail/Outlook over OAuth: search, read, summarise (via the
supervisor), draft, reply and send. Sending and replying are confirmed first."""
from __future__ import annotations

from agent.agents.base import Agent, int_arg
from agent.tools.base import EXTERNAL, READ, WRITE


class EmailAgent(Agent):
    name = "EmailAgent"
    description = "Email (Gmail or Outlook via OAuth): search and read mail, summarise, draft, reply, send (confirmed)."

    def tools(self):
        e = self.cc.email
        show = lambda a: ("Send email", f"To {a.get('to')}\nSubject: {a.get('subject')}\n\n{str(a.get('body', ''))[:220]}")
        return [
            self.tool("search_email", "Search mail (Gmail query syntax works on Gmail, e.g. 'from:rahul newer_than:7d').",
                      {"query": "search", "limit": "int ≤25"}, lambda query="", limit=10: e.do("search", query, int_arg(limit, 10)), READ),
            self.tool("read_email", "Read one message by id (from search_email).", {"message_id": "id"},
                      lambda message_id: e.do("read", message_id), READ),
            self.tool("draft_email", "Save a draft in the mailbox (not sent).", {"to": "address", "subject": "subject", "body": "text"},
                      lambda to, subject, body: e.do("draft", to, subject, body), WRITE),
            self.tool("send_email", "Send an email (user confirms first; reported sent only when the provider confirms).",
                      {"to": "address", "subject": "subject", "body": "text"},
                      lambda to, subject, body: e.do("send", to, subject, body), EXTERNAL, confirm_detail=show),
            self.tool("reply_email", "Reply to a message by id (user confirms first).", {"message_id": "id", "to": "address", "body": "text"},
                      lambda message_id, to, body, subject="": e.do("send", to, subject, body, message_id), EXTERNAL,
                      confirm_detail=lambda a: ("Send reply", f"Reply to {a.get('to')}:\n{str(a.get('body', ''))[:240]}")),
        ]
