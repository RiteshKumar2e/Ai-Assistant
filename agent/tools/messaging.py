"""
agent/tools/messaging.py — MessagingProvider over official APIs only.

    MessagingProvider
    ├── TelegramProvider     Bot API          telegram_bot_token            (recipient must have /start-ed the bot)
    ├── SlackProvider        chat.postMessage slack_bot_token               (channel or user id)
    ├── DiscordProvider      channel webhook  discord_webhooks {name: url}  (a webhook posts to one channel)
    └── WhatsAppCloudProvider  Meta WhatsApp Cloud API  whatsapp_token + whatsapp_phone_number_id
                             (all keys in config/api_keys.json)
                             (Meta only allows free-form text inside a 24h customer window; outside it the
                              API returns an error, which is reported as-is)

No UI scraping, no unofficial clients. "Sent" is reported only when the
provider's API returns a message id; that confirms the provider accepted it —
end-device delivery isn't something these APIs report synchronously, and the
result says so. Contacts are mapped to provider ids in config/agent.json:

    "contacts": {"Rahul": {"telegram": "123456789", "slack": "U0123ABC", "whatsapp": "+919876543210",
                           "discord": "TEAM", "email": "rahul@example.com"}}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from difflib import get_close_matches

import requests

from agent.credentials import secret, secret_map
from agent.tools.base import ToolResult, redact


def _post(url: str, **kw) -> tuple[int, dict | str]:
    try:
        r = requests.post(url, timeout=20, **kw)
    except requests.RequestException as e:
        return 0, f"network error: {e}"
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:300]


class MessagingProvider(ABC):
    name = ""

    @abstractmethod
    def available(self) -> tuple[bool, str]: ...

    @abstractmethod
    def send(self, to: str, text: str) -> ToolResult: ...

    def _ok(self, to: str, mid) -> ToolResult:
        return ToolResult(True, f"{self.name}: message accepted by the provider (id {mid}) for {to}.",
                          {"message_id": str(mid), "provider": self.name})

    def _err(self, code, body) -> ToolResult:
        return ToolResult.fail(f"{self.name} did not accept the message (HTTP {code}): {redact(str(body))[:300]}")


class TelegramProvider(MessagingProvider):
    name = "telegram"

    def available(self):
        return (True, "") if secret("telegram_bot_token") else (False, "Telegram isn't connected (add telegram_bot_token to config/api_keys.json).")

    def send(self, to, text):
        code, body = _post(f"https://api.telegram.org/bot{secret('telegram_bot_token')}/sendMessage", json={"chat_id": to, "text": text})
        return self._ok(to, body["result"]["message_id"]) if isinstance(body, dict) and body.get("ok") else self._err(code, body)


class SlackProvider(MessagingProvider):
    name = "slack"

    def available(self):
        return (True, "") if secret("slack_bot_token") else (False, "Slack isn't connected (add slack_bot_token to config/api_keys.json).")

    def send(self, to, text):
        code, body = _post("https://slack.com/api/chat.postMessage", headers={"Authorization": f"Bearer {secret('slack_bot_token')}"},
                           json={"channel": to, "text": text})
        return self._ok(to, body.get("ts")) if isinstance(body, dict) and body.get("ok") else self._err(code, body)


class DiscordProvider(MessagingProvider):
    name = "discord"

    def available(self):
        return (True, "") if secret_map("discord_webhooks") else \
            (False, "Discord isn't connected (add discord_webhooks {name: url} to config/api_keys.json).")

    def send(self, to, text):
        hooks = {k.lower(): v for k, v in secret_map("discord_webhooks").items()}
        url = hooks.get(to.lower(), "")
        if not url:
            return ToolResult.fail(f"No Discord webhook named '{to}' in discord_webhooks.", "unavailable")
        code, body = _post(url + ("&" if "?" in url else "?") + "wait=true", json={"content": text})
        return self._ok(to, body.get("id")) if isinstance(body, dict) and body.get("id") else self._err(code, body)


class WhatsAppCloudProvider(MessagingProvider):
    name = "whatsapp"

    def available(self):
        return (True, "") if secret("whatsapp_token") and secret("whatsapp_phone_number_id") else \
            (False, "WhatsApp isn't connected (official WhatsApp Cloud API: add whatsapp_token and whatsapp_phone_number_id to config/api_keys.json).")

    def send(self, to, text):
        code, body = _post(f"https://graph.facebook.com/v20.0/{secret('whatsapp_phone_number_id')}/messages",
                           headers={"Authorization": f"Bearer {secret('whatsapp_token')}"},
                           json={"messaging_product": "whatsapp", "to": to.lstrip("+"), "type": "text", "text": {"body": text}})
        mid = (body.get("messages") or [{}])[0].get("id") if isinstance(body, dict) else None
        return self._ok(to, mid) if mid else self._err(code, body)


PROVIDERS = {p.name: p for p in (TelegramProvider(), SlackProvider(), DiscordProvider(), WhatsAppCloudProvider())}


class Messaging:
    def __init__(self, contacts: dict, providers: dict | None = None):
        self.contacts = {str(k): v for k, v in (contacts or {}).items() if isinstance(v, dict)}
        self.providers = providers or PROVIDERS

    def status(self) -> dict[str, tuple[bool, str]]:
        return {n: p.available() for n, p in self.providers.items()}

    def find(self, name: str) -> tuple[str, dict] | None:
        lower = {k.lower(): k for k in self.contacts}
        q = (name or "").strip().lower()
        key = q if q in lower else next(iter(get_close_matches(q, list(lower), 1, 0.75)), None)
        return (lower[key], self.contacts[lower[key]]) if key else None

    def list_contacts(self) -> ToolResult:
        if not self.contacts:
            return ToolResult(True, "No contacts configured (config/agent.json → \"contacts\").")
        return ToolResult(True, "\n".join(f"{n}: {', '.join(k for k in c if k in self.providers or k == 'email')}" for n, c in self.contacts.items()))

    def route(self, to: str, channel: str = "") -> tuple[MessagingProvider | None, str, str]:
        """(provider, provider-specific id, error). Explicit channel wins; otherwise the
        first connected provider this contact has an id for."""
        hit = self.find(to)
        ids = hit[1] if hit else {}
        order = [channel] if channel else [n for n in self.providers if n in ids]
        if not hit and channel:
            ids = {channel: to}                     # raw id given with an explicit channel
        if not order:
            return None, "", (f"I don't know how to reach '{to}' — add them to config/agent.json contacts." if not hit
                              else f"{hit[0]} has no messaging ids configured.")
        why = []
        for n in order:
            p = self.providers.get(n)
            if not p:
                why.append(f"unknown channel {n}"); continue
            ok, reason = p.available()
            if ok and ids.get(n):
                return p, str(ids[n]), ""
            why.append(reason or f"{hit[0] if hit else to} has no {n} id")
        return None, "", "; ".join(why)

    def preview(self, to: str, text: str, channel: str = "") -> ToolResult:
        p, rid, err = self.route(to, channel)
        if not p:
            return ToolResult.fail(err, "unavailable")
        name = (self.find(to) or (to,))[0]
        return ToolResult(True, f"Draft via {p.name} to {name}:\n{text}", {"provider": p.name, "recipient": name})

    def send(self, to: str, text: str, channel: str = "") -> ToolResult:
        if not text.strip():
            return ToolResult.fail("Message text is empty.")
        p, rid, err = self.route(to, channel)
        return p.send(rid, text) if p else ToolResult.fail(err, "unavailable")
