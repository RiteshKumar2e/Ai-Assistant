"""
judo_mail/mailbox.py — Gmail over IMAP (read) and SMTP (send), no UI.

Credentials come from config/api_keys.json: "gmail_address" and
"gmail_app_password" (a Google App Password, not the account password).
Listing never marks anything read (BODY.PEEK); only mark_read() does, which
the app calls when a message is opened — Gmail's own behaviour.

Not thread-safe (imaplib isn't): use one Mailbox from one thread at a time.
"""
from __future__ import annotations

import email
import imaplib
import json
import mimetypes
import re
import smtplib
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from datetime import datetime
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
FOLDERS = {"Inbox": "INBOX", "Starred": "[Gmail]/Starred", "Important": "[Gmail]/Important",
           "Sent": "[Gmail]/Sent Mail", "Drafts": "[Gmail]/Drafts", "All Mail": "[Gmail]/All Mail",
           "Spam": "[Gmail]/Spam", "Trash": "[Gmail]/Trash"}
_HEADER_FIELDS = "(FROM TO CC SUBJECT DATE MESSAGE-ID)"


def credentials() -> tuple[str, str]:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    user, pw = cfg.get("gmail_address", "").strip(), cfg.get("gmail_app_password", "").replace(" ", "")
    if not user or not pw:
        raise RuntimeError("Add gmail_address and gmail_app_password to config/api_keys.json")
    return user, pw


def _text(value) -> str:
    try:
        return str(make_header(decode_header(value or ""))).strip()
    except Exception:
        return str(value or "").strip()


def _who(value: str) -> str:
    """'"Rahul Kumar" <rahul@x.com>' -> 'Rahul Kumar' (or the address)."""
    pairs = getaddresses([value or ""])
    return ", ".join(n or a for n, a in pairs if n or a)


@dataclass
class Summary:
    uid: str
    sender: str
    subject: str
    date: str
    unread: bool
    starred: bool
    when: datetime | None = None     # the Date header, for "3:45 PM" / "12 Oct" in the list
    address: str = ""                # the sender's (or, in Sent, recipient's) e-mail address


@dataclass
class Mail:
    uid: str
    sender: str
    to: str
    cc: str
    subject: str
    date: str
    message_id: str
    text: str = ""
    html: str = ""
    attachments: list[tuple[str, int]] = field(default_factory=list)   # (file name, bytes)
    raw: Message | None = None


class Mailbox:
    def __init__(self):
        self.user, self._pw = credentials()
        self._imap: imaplib.IMAP4_SSL | None = None
        self._folder = ""

    # connection
    def _conn(self, folder: str) -> imaplib.IMAP4_SSL:
        for attempt in (1, 2):   # one silent reconnect: Gmail drops idle connections
            try:
                if self._imap is None:
                    self._imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
                    self._imap.login(self.user, self._pw)
                    self._folder = ""
                if self._folder != folder:
                    typ, _ = self._imap.select(f'"{folder}"')
                    if typ != "OK":
                        raise RuntimeError(f"Cannot open folder {folder}")
                    self._folder = folder
                else:
                    self._imap.noop()
                return self._imap
            except (imaplib.IMAP4.abort, OSError):
                self._imap = None
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")

    def close(self) -> None:
        try:
            if self._imap:
                self._imap.logout()
        except Exception:
            pass
        self._imap = None

    # reading
    def uids(self, folder: str = "INBOX", query: str = "") -> list[str]:
        """Newest first. `query` uses Gmail's own search syntax (from:, is:unread, …)."""
        m = self._conn(folder)
        if query:
            q = '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"'
            typ, data = m.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW", q)
        else:
            typ, data = m.uid("SEARCH", None, "ALL")
        return [u.decode() for u in reversed(data[0].split())]

    def summaries(self, folder: str, uids: list[str]) -> list[Summary]:
        if not uids:
            return []
        m = self._conn(folder)
        typ, data = m.uid("FETCH", ",".join(uids), f"(UID FLAGS BODY.PEEK[HEADER.FIELDS {_HEADER_FIELDS}])")
        found: dict[str, Summary] = {}
        for part in data:
            if not isinstance(part, tuple):
                continue
            meta = part[0].decode(errors="replace")
            uid = re.search(r"UID (\d+)", meta).group(1)
            flags = re.search(r"FLAGS \(([^)]*)\)", meta)
            flags = flags.group(1) if flags else ""
            h = email.message_from_bytes(part[1])
            try:
                when = parsedate_to_datetime(h["Date"])
                date = when.strftime("%d %b %Y, %H:%M")
            except Exception:
                when, date = None, _text(h["Date"])
            sent = folder == FOLDERS["Sent"]
            who = _text(h["To"] if sent else h["From"])
            found[uid] = Summary(uid, ("To: " if sent else "") + _who(who), _text(h["Subject"]) or "(no subject)", date,
                                 "\\Seen" not in flags, "\\Flagged" in flags, when,
                                 next((a for _, a in getaddresses([who]) if a), ""))
        return [found[u] for u in uids if u in found]

    def get(self, folder: str, uid: str) -> Mail:
        m = self._conn(folder)
        typ, data = m.uid("FETCH", uid, "(BODY.PEEK[])")
        raw = next(p[1] for p in data if isinstance(p, tuple))
        msg = email.message_from_bytes(raw)
        mail = Mail(uid, _text(msg["From"]), _text(msg["To"]), _text(msg["Cc"]), _text(msg["Subject"]),
                    _text(msg["Date"]), msg["Message-ID"] or "", raw=msg)
        for part in msg.walk():
            if part.is_multipart():
                continue
            name = part.get_filename()
            if name or part.get_content_disposition() == "attachment":
                mail.attachments.append((_text(name) or "attachment", len(part.get_payload(decode=True) or b"")))
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if part.get_content_type() == "text/html" and not mail.html:
                mail.html = body
            elif part.get_content_type() == "text/plain" and not mail.text:
                mail.text = body
        return mail

    def unseen(self, folder: str = "INBOX") -> int:
        """Unread messages in a folder — the badge next to Inbox."""
        typ, data = self._conn(folder).status(f'"{folder}"', "(UNSEEN)")
        found = re.search(rb"UNSEEN (\d+)", data[0] or b"") if typ == "OK" else None
        return int(found.group(1)) if found else 0

    def save_attachment(self, mail: Mail, index: int, folder: Path) -> Path:
        parts = [p for p in mail.raw.walk() if not p.is_multipart()
                 and (p.get_filename() or p.get_content_disposition() == "attachment")]
        part = parts[index]
        name = re.sub(r'[\\/:*?"<>|]', "_", _text(part.get_filename()) or f"attachment_{index}")
        dest = folder / name
        n = 1
        while dest.exists():   # never overwrite a file already there
            dest = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
            n += 1
        dest.write_bytes(part.get_payload(decode=True) or b"")
        return dest

    # changing
    def mark_read(self, folder: str, uid: str, read: bool = True) -> None:
        self._conn(folder).uid("STORE", uid, "+FLAGS" if read else "-FLAGS", "(\\Seen)")

    def star(self, folder: str, uid: str, on: bool = True) -> None:
        self._conn(folder).uid("STORE", uid, "+FLAGS" if on else "-FLAGS", "(\\Flagged)")

    def trash(self, folder: str, uid: str) -> None:
        """Gmail's Delete: move to Trash (recoverable for 30 days)."""
        m = self._conn(folder)
        typ, _ = m.uid("MOVE", uid, f'"{FOLDERS["Trash"]}"')
        if typ != "OK":
            m.uid("COPY", uid, f'"{FOLDERS["Trash"]}"')
            m.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            m.expunge()

    # sending
    def send(self, to: str, subject: str, body: str, cc: str = "", attachments: list[str] | None = None,
             reply_to: Mail | None = None) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.user, to, subject
        if cc:
            msg["Cc"] = cc
        if reply_to and reply_to.message_id:   # keeps the reply in the same Gmail thread
            msg["In-Reply-To"] = msg["References"] = reply_to.message_id
        msg.set_content(body)
        for path in attachments or []:
            p = Path(path)
            ctype, _ = mimetypes.guess_type(p.name)
            main, sub = (ctype or "application/octet-stream").split("/", 1)
            msg.add_attachment(p.read_bytes(), maintype=main, subtype=sub, filename=p.name)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:   # Gmail files it under Sent itself
            s.login(self.user, self._pw)
            s.send_message(msg)
