"""
send_email.py — Sends email through the user's own, already-open browser
using their real, already-logged-in Google account (Gmail's compose-window
URL scheme), driven via actions/browser_control's CDP session — the same
real-browser infrastructure browser_control and send_message already use.
No SMTP, no app password, nothing stored: it never touches credentials,
it just opens Gmail compose pre-filled and clicks Send.

If more than one Google account is signed in to the browser, pass 'account'
(the sender's email address) to pick which one composes the mail — Gmail
honours the `authuser` query param for this. A default can also be set once
via the plugin settings panel instead of saying it every time.
"""
import time
from urllib.parse import quote

from memory.config_manager import get_plugin_setting

_NAMESPACE = "send_email"


def _compose_url(to: str, subject: str, body: str, account: str) -> str:
    parts = [f"to={quote(to)}", "view=cm", "fs=1"]
    if subject:
        parts.append(f"su={quote(subject)}")
    if body:
        parts.append(f"body={quote(body)}")
    if account:
        parts.append(f"authuser={quote(account)}")
    return "https://mail.google.com/mail/?" + "&".join(parts)


def _looks_like_email(s: str) -> bool:
    if "@" not in s:
        return False
    local, _, domain = s.rpartition("@")
    return bool(local) and "." in domain


PLUGIN = {
    "name": "send_email",
    "description": (
        "Sends an email using the user's own, already-open browser and their real, "
        "already-logged-in Google account (Gmail) — opens a pre-filled compose "
        "window and sends it, never asking for or storing a password. Use for "
        "requests like 'email John about the meeting', 'send an email to "
        "x@example.com saying ...'. The 'to' field must be a real email address. "
        "Do NOT use this for WhatsApp/Telegram/Signal/Discord — use send_message "
        "for those instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "to": {"type": "STRING", "description": "Recipient's email address"},
            "subject": {"type": "STRING", "description": "Email subject line"},
            "body": {"type": "STRING", "description": "Email body text"},
            "account": {
                "type": "STRING",
                "description": "Sender's Gmail address, only needed if multiple "
                                "Google accounts are signed in to the browser",
            },
            "browser": {
                "type": "STRING",
                "description": "chrome | edge | brave | vivaldi | opera — omit to "
                                "use whichever browser is already active",
            },
        },
        "required": ["to"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    to = str(p.get("to", "")).strip()
    subject = str(p.get("subject", "")).strip()
    body = str(p.get("body", "")).strip()
    account = str(p.get("account", "")).strip() or get_plugin_setting(_NAMESPACE, "default_account", "")
    browser = str(p.get("browser", "")).strip() or None

    if not to:
        return "Please tell me who to send the email to."
    if not _looks_like_email(to):
        return f"'{to}' doesn't look like a valid email address — please give me the full address."

    try:
        from actions.browser_control import browser_control
    except Exception as e:
        return f"Sir, the browser automation engine isn't available: {e}"

    url = _compose_url(to, subject, body, account)

    nav = browser_control({"action": "go_to", "url": url, "browser": browser}, player=player)
    if not nav.startswith("Opened"):
        return f"Sir, I couldn't open Gmail compose: {nav}"

    time.sleep(2.5)  # Gmail's compose UI needs a moment to render after navigation

    send = browser_control({"action": "smart_click", "description": "Send"}, player=player)
    if send.startswith("Clicked"):
        result = f"Email sent to {to}."
    else:
        result = (f"I opened a Gmail compose window addressed to {to}, but couldn't "
                  f"confirm the Send click — please check the browser and press Send.")

    if player:
        try:
            player.write_log(f"[email] {result}")
        except Exception:
            pass
    return result


PLUGIN_SETTINGS = {
    "namespace": _NAMESPACE,
    "title": "Email (Gmail)",
    "fields": [
        {
            "key": "default_account",
            "label": "Default Gmail account",
            "type": "text",
            "placeholder": "you@gmail.com (only needed with multiple signed-in accounts)",
        },
    ],
}
