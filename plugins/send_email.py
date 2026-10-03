"""
send_email.py — JUDO writes and sends email from the user's own Gmail, by voice.

    "Rahul ko mail bhejo ki kal ki meeting 5 baje hai"
    "Email Priya the notes from today"   ·   "meri nayi mails batao"

It goes through JUDO Mail's Gmail connection (judo_mail/mailbox.py: SMTP to
send, IMAP to look things up), so a sent mail shows up in Gmail's Sent like any
other, and JUDO Mail picks it up live.

  - Names become addresses from the user's own Gmail: everyone they've mailed or
    heard from, ranked by how often (people they wrote to count double). Two
    likely matches → JUDO asks which one; none → JUDO asks for the address.
  - The model writes the mail itself (greeting, the message, sign-off with the
    user's name) in the language and tone the user asked for.
  - Sending is outward and can't be undone, so by default it waits for CONFIRM on
    the HUD, showing who it goes to, the subject and the text (core/confirm.py —
    the model cannot press it). The plugin settings can turn that off.
  - "check" reads out the newest unread mail in the Inbox.

Without a Gmail account in JUDO Mail it falls back to opening Gmail's compose
page in JUDO Browser and pressing Send there.
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote

from memory.config_manager import get_plugin_setting, get_user_name

_NAMESPACE = "send_email"


def _looks_like_email(s: str) -> bool:
    local, _, domain = s.rpartition("@")
    return bool(local) and "." in domain


def _split(value: str) -> list[str]:
    """'Rahul, priya@x.com aur Aman' -> ['Rahul', 'priya@x.com', 'Aman']"""
    parts = re.split(r"\s*(?:,|;|\band\b|\baur\b|&)\s*", value or "", flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def resolve(names: str, mailbox) -> tuple[list[str], str]:
    """Spoken recipients -> ("Name <address>" list, "") or ([], a question for the user)."""
    out = []
    for who in _split(names):
        if _looks_like_email(who):
            out.append(who)
            continue
        found = mailbox.find_contacts(who)
        if not found:
            return [], (f"I couldn't find anyone called '{who}' in your Gmail. Ask the user for "
                        f"{who}'s email address, then call send_email again with it.")
        best = found[0]
        rivals = [f for f in found[1:] if f[2] * 2 > best[2]]      # close enough to be a real doubt
        if rivals:
            options = "; ".join(f"{d} <{a}>" for d, a, _ in [best, *rivals][:3])
            return [], (f"More than one '{who}' in the user's Gmail: {options}. Ask the user which one "
                        f"(or for the address), then call send_email again with the exact address.")
        out.append(f"{best[0]} <{best[1]}>" if best[0] and best[0] != best[1] else best[1])
    return out, ""


def _check(mailbox) -> str:
    from judo_mail.mailbox import FOLDERS
    uids = mailbox.uids(FOLDERS["Inbox"], "is:unread")
    if not uids:
        return "The inbox has no unread mail."
    rows = mailbox.summaries(FOLDERS["Inbox"], uids[:5])
    lines = [f"- {s.sender}: {s.subject}" + (f" — {s.snippet[:90]}" if s.snippet else "") for s in rows]
    return (f"{len(uids):,} unread in the inbox. Newest {len(rows)} (tell the user briefly, in their language):\n"
            + "\n".join(lines))


def _send_with_judo_mail(to: list[str], cc: list[str], subject: str, body: str, player) -> str:
    from judo_mail.mailbox import Mailbox

    def send() -> str:
        Mailbox().send(", ".join(to), subject, body, ", ".join(cc))
        return f"Email sent to {', '.join(to)}."

    if not get_plugin_setting(_NAMESPACE, "confirm_before_send", True):
        result = send()
        _log(player, result)
        return result
    from core import confirm
    preview = body if len(body) <= 600 else body[:600] + "…"
    detail = (f"To: {', '.join(to)}" + (f"\nCc: {', '.join(cc)}" if cc else "")
              + f"\nSubject: {subject or '(no subject)'}\n\n{preview}")
    return confirm.request("send_email", f"Send email to {', '.join(to)}", detail, send)


def _send_with_browser(to: str, subject: str, body: str, player) -> str:
    """No JUDO Mail account: Gmail's compose page in JUDO Browser, then its Send button."""
    from actions.browser_control import browser_control
    parts = [f"to={quote(to)}", "view=cm", "fs=1"] + ([f"su={quote(subject)}"] if subject else []) \
        + ([f"body={quote(body)}"] if body else [])
    nav = browser_control({"action": "new_tab", "url": "https://mail.google.com/mail/?" + "&".join(parts)},
                          player=player)
    if not nav.startswith("Opened"):
        return f"Sir, I couldn't open Gmail compose: {nav}"
    time.sleep(2.5)  # Gmail's compose UI needs a moment to render after navigation
    sent = browser_control({"action": "smart_click", "description": "Send"}, player=player)
    if sent.startswith("Clicked"):
        return f"Email sent to {to}."
    return (f"I opened a Gmail compose window addressed to {to}, but couldn't confirm the Send "
            f"click — please check JUDO Browser and press Send.")


def _log(player, text: str) -> None:
    if player:
        try:
            player.write_log(f"[email] {text}")
        except Exception:
            pass


def _signed(body: str) -> str:
    """Add the user's name as the sign-off when the model forgot one."""
    name = get_user_name().strip()
    if not name or not body or name.lower() in body[-80:].lower():
        return body
    return body.rstrip() + f"\n\n{name}"


PLUGIN = {
    "name": "send_email",
    "description": (
        "Writes and sends an email from the user's own Gmail account, or reads their newest unread mail. "
        "Use for 'Rahul ko mail bhejo ki…', 'email Priya about the meeting', 'send a mail to x@y.com', "
        "'meri nayi mails batao', 'any new emails?'. For sending, 'to' may be a person's NAME (looked up in "
        "the user's Gmail) or an email address. Write the whole email yourself in 'body' — a greeting, the "
        "message in clear, polite sentences, and a sign-off with the user's name — in the language and tone "
        "the user asked for, plus a short fitting 'subject'. Do NOT use this for WhatsApp/Telegram/Signal/"
        "Discord — use send_message for those."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "send (default) | check — read the newest unread mail"},
            "to": {"type": "STRING", "description": "Recipient name(s) or email address(es), comma-separated"},
            "cc": {"type": "STRING", "description": "Optional Cc name(s) or address(es)"},
            "subject": {"type": "STRING", "description": "Email subject line — write one if the user didn't"},
            "body": {"type": "STRING", "description": "The full email text, written by you from what the user said"},
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "send")).strip().lower() or "send"
    to, cc = str(p.get("to", "")).strip(), str(p.get("cc", "")).strip()
    subject, body = str(p.get("subject", "")).strip(), _signed(str(p.get("body", "")).strip())

    try:
        from judo_mail.mailbox import Mailbox
        mailbox = Mailbox()                     # raises when no Gmail account is set up in JUDO Mail
    except Exception:
        mailbox = None

    if action == "check":
        if mailbox is None:
            return "Reading mail needs a Gmail account in JUDO Mail — ask the user to open JUDO Mail and sign in."
        try:
            return _check(mailbox)
        finally:
            mailbox.close()

    if not to:
        return "Ask the user who the email should go to."
    if not body:
        return "Ask the user what the email should say, then write it and call send_email again."

    if mailbox is None:
        if not all(_looks_like_email(x) for x in _split(to)):
            return (f"I need {to}'s email address (JUDO Mail isn't signed in to Gmail, so I can't look names up). "
                    "Ask the user for it.")
        result = _send_with_browser(", ".join(_split(to)), subject, body, player)
        _log(player, result)
        return result

    try:
        recipients, question = resolve(to, mailbox)
        if question:
            return question
        copies, question = resolve(cc, mailbox) if cc else ([], "")
        if question:
            return question
    finally:
        mailbox.close()
    result = _send_with_judo_mail(recipients, copies, subject, body, player)
    _log(player, result if not result.startswith("[CONFIRMATION") else f"awaiting confirmation for {recipients}")
    return result


PLUGIN_SETTINGS = {
    "namespace": _NAMESPACE,
    "title": "Email (Gmail)",
    "fields": [
        {
            "key": "confirm_before_send",
            "label": "Show the email and wait for CONFIRM before sending",
            "type": "toggle",
            "default": True,
        },
    ],
}
