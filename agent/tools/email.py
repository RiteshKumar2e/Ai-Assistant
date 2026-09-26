"""
agent/tools/email.py — EmailProvider: search, read, draft, send, reply.

    EmailProvider
    ├── GmailProvider     Gmail API over Google OAuth (agent/integrations/google_oauth.py)
    └── OutlookProvider   Microsoft Graph over MSAL device-code sign-in (pip install msal;
                          outlook_client_id in config/api_keys.json; `python -m agent.tools.email outlook-login`)

No passwords anywhere; OAuth tokens stay in gitignored files read only by the
client libraries. Sending is EXTERNAL (confirmed in strict/balanced) and is
reported as sent only when the provider confirms it: Gmail returns the message
with the SENT label; Outlook's sendMail returns 202 with no id, so the message
is then looked up in Sent Items.
"""
from __future__ import annotations

import base64
import sys
import time
from abc import ABC, abstractmethod
from email.message import EmailMessage

from agent.config import BASE_DIR
from agent.credentials import secret
from agent.tools.base import ToolResult, redact


class EmailProvider(ABC):
    name = ""

    @abstractmethod
    def available(self) -> tuple[bool, str]: ...
    @abstractmethod
    def search(self, query: str, limit: int = 10) -> ToolResult: ...
    @abstractmethod
    def read(self, message_id: str) -> ToolResult: ...
    @abstractmethod
    def draft(self, to: str, subject: str, body: str) -> ToolResult: ...
    @abstractmethod
    def send(self, to: str, subject: str, body: str, reply_to: str = "") -> ToolResult: ...


# ── Gmail ─────────────────────────────────────────────────────────────────────
def _gmail_text(payload: dict) -> str:
    if payload.get("mimeType", "").startswith("text/plain") and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        if (t := _gmail_text(part)):
            return t
    if payload.get("mimeType", "").startswith("text/html") and payload.get("body", {}).get("data"):
        from bs4 import BeautifulSoup
        return BeautifulSoup(base64.urlsafe_b64decode(payload["body"]["data"]), "html.parser").get_text("\n", strip=True)
    return ""


class GmailProvider(EmailProvider):
    name = "gmail"

    def available(self):
        from agent.integrations.google_oauth import status
        ok, why = status()
        return ok, why.replace("Google account", "Email (Gmail)") if not ok else ""

    def _svc(self):
        from agent.integrations.google_oauth import service
        return service("gmail", "v1").users()

    def search(self, query, limit=10):
        u = self._svc()
        ids = u.messages().list(userId="me", q=query or "in:inbox", maxResults=min(int(limit), 25)).execute().get("messages", [])
        rows = []
        for m in ids:
            d = u.messages().get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"]).execute()
            h = {x["name"]: x["value"] for x in d["payload"].get("headers", [])}
            rows.append(f"[{m['id']}] {h.get('Date', '')[:16]} · {h.get('From', '')} · {h.get('Subject', '(no subject)')}\n    {d.get('snippet', '')[:160]}")
        return ToolResult(True, redact("\n".join(rows)) or f"No messages match {query!r}.", {"count": len(rows)})

    def read(self, message_id):
        d = self._svc().messages().get(userId="me", id=message_id, format="full").execute()
        h = {x["name"]: x["value"] for x in d["payload"].get("headers", [])}
        body = _gmail_text(d["payload"])[:15000]
        return ToolResult(True, redact(f"From: {h.get('From')}\nTo: {h.get('To')}\nDate: {h.get('Date')}\nSubject: {h.get('Subject')}\n\n{body}"),
                          {"thread_id": d.get("threadId"), "message_id_header": h.get("Message-ID", ""), "subject": h.get("Subject", ""),
                           "from": h.get("From", "")})

    def _mime(self, to, subject, body, reply_to=""):
        msg, extra = EmailMessage(), {}
        msg["To"], msg["Subject"] = to, subject
        if reply_to:
            orig = self.read(reply_to).data
            msg["In-Reply-To"] = msg["References"] = orig.get("message_id_header", "")
            msg["Subject"] = subject or ("Re: " + orig.get("subject", ""))
            extra["threadId"] = orig.get("thread_id")
        msg.set_content(body)
        return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode(), **extra}

    def draft(self, to, subject, body):
        d = self._svc().drafts().create(userId="me", body={"message": self._mime(to, subject, body)}).execute()
        return ToolResult(True, f"Draft saved in Gmail (id {d['id']}) — not sent.", {"draft_id": d["id"]})

    def send(self, to, subject, body, reply_to=""):
        sent = self._svc().messages().send(userId="me", body=self._mime(to, subject, body, reply_to)).execute()
        if "SENT" not in (sent.get("labelIds") or []):
            return ToolResult.fail(f"Gmail did not confirm the message as sent: {sent}")
        return ToolResult(True, f"Gmail confirmed sent to {to} (id {sent['id']}).", {"message_id": sent["id"]})


# ── Outlook ───────────────────────────────────────────────────────────────────
_OUTLOOK_CACHE = BASE_DIR / "config" / "token_outlook.json"
_GRAPH = "https://graph.microsoft.com/v1.0/me"
_OUTLOOK_SCOPES = ["Mail.Read", "Mail.Send", "Mail.ReadWrite"]


def _msal_app():
    import msal
    cache = msal.SerializableTokenCache()
    if _OUTLOOK_CACHE.is_file():
        cache.deserialize(_OUTLOOK_CACHE.read_text(encoding="utf-8"))
    return msal.PublicClientApplication(secret("outlook_client_id"), authority="https://login.microsoftonline.com/common",
                                        token_cache=cache), cache


class OutlookProvider(EmailProvider):
    name = "outlook"

    def available(self):
        try:
            import msal  # noqa: F401
        except ImportError:
            return False, "Outlook isn't connected (pip install msal, add outlook_client_id to config/api_keys.json, run `python -m agent.tools.email outlook-login`)."
        if not secret("outlook_client_id") or not _OUTLOOK_CACHE.is_file():
            return False, "Outlook isn't connected (add outlook_client_id to config/api_keys.json and run `python -m agent.tools.email outlook-login`)."
        return (True, "") if self._token() else (False, "Outlook sign-in expired — run `python -m agent.tools.email outlook-login`.")

    def _token(self) -> str:
        app, cache = _msal_app()
        accts = app.get_accounts()
        res = app.acquire_token_silent(_OUTLOOK_SCOPES, account=accts[0]) if accts else None
        if cache.has_state_changed:
            _OUTLOOK_CACHE.write_text(cache.serialize(), encoding="utf-8")
        return (res or {}).get("access_token", "")

    def _req(self, method, path, **kw):
        import requests
        r = requests.request(method, _GRAPH + path, headers={"Authorization": f"Bearer {self._token()}"}, timeout=30, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Graph {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}

    def search(self, query, limit=10):
        params = {"$top": min(int(limit), 25), "$select": "id,subject,from,receivedDateTime,bodyPreview"}
        if query:
            params["$search"] = f'"{query}"'
        rows = [f"[{m['id'][:20]}…] {m['receivedDateTime'][:16]} · {m['from']['emailAddress']['address']} · {m.get('subject')}\n    {m.get('bodyPreview', '')[:160]}"
                for m in self._req("GET", "/messages", params=params).get("value", [])]
        return ToolResult(True, redact("\n".join(rows)) or "No messages.", {"count": len(rows)})

    def read(self, message_id):
        m = self._req("GET", f"/messages/{message_id}", headers={"Prefer": 'outlook.body-content-type="text"'})
        return ToolResult(True, redact(f"From: {m['from']['emailAddress']['address']}\nSubject: {m.get('subject')}\n\n{m['body']['content'][:15000]}"),
                          {"subject": m.get("subject", "")})

    def draft(self, to, subject, body):
        m = self._req("POST", "/messages", json={"subject": subject, "body": {"contentType": "Text", "content": body},
                                                 "toRecipients": [{"emailAddress": {"address": to}}]})
        return ToolResult(True, f"Draft saved in Outlook (id {m['id'][:20]}…) — not sent.", {"draft_id": m["id"]})

    def send(self, to, subject, body, reply_to=""):
        if reply_to:
            self._req("POST", f"/messages/{reply_to}/reply", json={"comment": body})
        else:
            self._req("POST", "/sendMail", json={"message": {"subject": subject, "body": {"contentType": "Text", "content": body},
                                                             "toRecipients": [{"emailAddress": {"address": to}}]}, "saveToSentItems": True})
        for _ in range(5):                               # 202 carries no id — confirm via Sent Items
            time.sleep(2)
            for m in self._req("GET", "/mailFolders/SentItems/messages", params={"$top": 5, "$orderby": "sentDateTime desc",
                                                                                  "$select": "id,subject,toRecipients"}).get("value", []):
                if any(r["emailAddress"]["address"].lower() == to.lower() for r in m.get("toRecipients", [])) and \
                        (reply_to or m.get("subject") == subject):
                    return ToolResult(True, f"Outlook confirmed sent to {to} (found in Sent Items).", {"message_id": m["id"]})
        return ToolResult.fail("Outlook accepted the request but the message hasn't appeared in Sent Items — check Outlook.")


class Email:
    def __init__(self, preferred: str = ""):
        self.providers = {"gmail": GmailProvider(), "outlook": OutlookProvider()}
        self.preferred = preferred

    def status(self) -> dict:
        return {n: p.available() for n, p in self.providers.items()}

    def pick(self) -> tuple[EmailProvider | None, str]:
        order = [self.preferred] if self.preferred in self.providers else list(self.providers)
        reasons = []
        for n in order:
            ok, why = self.providers[n].available()
            if ok:
                return self.providers[n], ""
            reasons.append(why)
        return None, "Email integration isn't connected. " + " / ".join(reasons)

    def do(self, method: str, *a) -> ToolResult:
        p, why = self.pick()
        if not p:
            return ToolResult.fail(why, "unavailable")
        try:
            return getattr(p, method)(*a)
        except Exception as e:
            return ToolResult.fail(f"{p.name} {method} failed: {redact(str(e))[:300]}")


def _outlook_login() -> int:
    if not secret("outlook_client_id"):
        print("Add outlook_client_id to config/api_keys.json (an Azure app registration with 'Allow public client flows' on).")
        return 1
    app, cache = _msal_app()
    flow = app.initiate_device_flow(scopes=_OUTLOOK_SCOPES)
    print(flow["message"])
    res = app.acquire_token_by_device_flow(flow)
    if "access_token" not in res:
        print("Sign-in failed:", res.get("error_description")); return 1
    _OUTLOOK_CACHE.write_text(cache.serialize(), encoding="utf-8")
    print(f"Connected. Token cache saved to {_OUTLOOK_CACHE} (gitignored).")
    return 0


if __name__ == "__main__":
    sys.exit(_outlook_login() if sys.argv[1:] == ["outlook-login"] else (print("usage: python -m agent.tools.email outlook-login") or 2))
