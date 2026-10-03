"""
judo_mail/mailbox.py — Gmail over IMAP (read) and SMTP (send), no UI.

Credentials come from config/api_keys.json: "gmail_address" and
"gmail_app_password" (a Google App Password, not the account password) are
the account in use; "gmail_accounts" remembers the others for switching.
Listing never marks anything read (BODY.PEEK); only mark_read() does, which
the app calls when a message is opened — Gmail's own behaviour.

Not thread-safe (imaplib isn't): use one Mailbox from one thread at a time.
"""
from __future__ import annotations

import base64
import email
import html as _html
import imaplib
import json
import quopri
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
_HEADER_FIELDS = "(FROM TO CC SUBJECT DATE MESSAGE-ID LIST-UNSUBSCRIBE CONTENT-TYPE CONTENT-TRANSFER-ENCODING)"
SNIPPET_BYTES = 6000          # enough of each body for a one-line preview
CATEGORIES = ("primary", "promotions", "social", "updates")


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_config(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def credentials() -> tuple[str, str]:
    cfg = _config()
    user, pw = cfg.get("gmail_address", "").strip(), cfg.get("gmail_app_password", "").replace(" ", "")
    if not user or not pw:
        raise RuntimeError("Add gmail_address and gmail_app_password to config/api_keys.json")
    return user, pw


# ── accounts: switch between Gmail accounts like Gmail's own profile menu ──

def accounts() -> list[str]:
    """Every saved address, the one in use first."""
    cfg = _config()
    cur = cfg.get("gmail_address", "").strip()
    others = [a["address"] for a in cfg.get("gmail_accounts", []) if a.get("address") and a["address"] != cur]
    return ([cur] if cur else []) + others


def check_login(address: str, app_password: str) -> None:
    """Raises with Gmail's reason when the address / App Password don't work."""
    m = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
    try:
        m.login(address.strip(), app_password.replace(" ", ""))
    finally:
        try:
            m.logout()
        except Exception:
            pass


def add_account(address: str, app_password: str) -> None:
    """Save an account (after check_login) and make it the one in use."""
    cfg = _config()
    address, app_password = address.strip(), app_password.replace(" ", "")
    saved = [a for a in cfg.get("gmail_accounts", []) if a.get("address") != address]
    cur = cfg.get("gmail_address", "").strip()
    if cur and cur != address and not any(a["address"] == cur for a in saved):
        saved.append({"address": cur, "app_password": cfg.get("gmail_app_password", "")})
    saved.append({"address": address, "app_password": app_password})
    cfg.update(gmail_accounts=saved, gmail_address=address, gmail_app_password=app_password)
    _save_config(cfg)


def switch_account(address: str) -> None:
    cfg = _config()
    found = next((a for a in cfg.get("gmail_accounts", []) if a.get("address") == address), None)
    if not found:
        raise RuntimeError(f"{address} is not saved in JUDO Mail")
    add_account(found["address"], found["app_password"])


def remove_account(address: str) -> str | None:
    """Forget an account; returns the address now in use (another saved one), or None."""
    cfg = _config()
    saved = [a for a in cfg.get("gmail_accounts", []) if a.get("address") != address]
    cfg["gmail_accounts"] = saved
    if cfg.get("gmail_address", "").strip() == address:
        nxt = saved[0] if saved else {"address": "", "app_password": ""}
        cfg["gmail_address"], cfg["gmail_app_password"] = nxt["address"], nxt["app_password"]
    _save_config(cfg)
    return cfg.get("gmail_address") or None


# ── previews ────────────────────────────────────────────────────────────────

def _decode(payload: bytes, encoding: str, charset: str) -> str:
    enc = (encoding or "").strip().lower()
    try:
        if enc == "base64":
            data = re.sub(rb"[^A-Za-z0-9+/=]", b"", payload)
            data = base64.b64decode(data[:len(data) - len(data) % 4] or b"", validate=False)
        elif enc == "quoted-printable":
            data = quopri.decodestring(payload)
        else:
            data = payload
        return data.decode(charset or "utf-8", errors="replace")
    except (LookupError, ValueError):
        return payload.decode("utf-8", errors="replace")


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(style|script|head|title)\b.*?(</\1>|$)", " ", text)
    text = re.sub(r"(?s)<!--.*?(-->|$)", " ", text)
    return _html.unescape(re.sub(r"<[^>]*>?", " ", text))


def snippet(content_type: str, encoding: str, body: bytes, depth: int = 0) -> str:
    """A one-line preview from the first few KB of a body (it may be cut off mid-part),
    preferring the plain-text part, like Gmail's list does."""
    msg = email.message_from_string(f"Content-Type: {content_type or 'text/plain'}\n\n")
    ctype = msg.get_content_type()
    if ctype.startswith("multipart/") and depth < 3:
        boundary = msg.get_param("boundary")
        if not boundary:
            return ""
        found = {}
        for chunk in body.split(b"--" + boundary.encode())[1:]:
            head, _, part_body = chunk.lstrip(b"\r\n").partition(b"\r\n\r\n")
            if not part_body:
                head, _, part_body = chunk.lstrip(b"\n").partition(b"\n\n")
            h = email.message_from_bytes(head + b"\r\n\r\n")
            text = snippet(h.get("Content-Type", "text/plain"), h.get("Content-Transfer-Encoding", ""),
                           part_body, depth + 1)
            if text:
                kind = "plain" if h.get_content_type() == "text/plain" else "other"
                found.setdefault(kind, text)
                if kind == "plain" and len(text) >= 30:
                    break
        plain, other = found.get("plain", ""), found.get("other", "")
        # a stub like "Please enable HTML" is no preview — the HTML part says more
        return plain if len(plain) >= 30 or not other else other
    if ctype not in ("text/plain", "text/html"):
        return ""
    text = _decode(body, encoding, msg.get_param("charset") or "utf-8")
    if ctype == "text/html":
        text = _strip_html(text)
    text = re.sub(r"[\[(<]?https?://\S+[\])>]?", " ", text)         # links read as noise in a preview
    text = re.sub(r"\s+", " ", text.replace("\u200c", "").replace("\xa0", " ")).strip(" :-|\u00b7")
    return text[:240]


def unsubscribe_target(value: str) -> str:
    """List-Unsubscribe header -> a web link (preferred) or a mailto: address."""
    links = re.findall(r"<([^>]+)>", value or "")
    return next((l for l in links if l.lower().startswith("http")), next(iter(links), ""))


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
    snippet: str = ""                # first words of the body, shown after the subject
    attachment: bool = False         # has a file attached (Gmail's has:attachment)
    unsubscribe: str = ""            # List-Unsubscribe link or mailto: — the "Unsubscribe" button


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
        typ, data = m.uid("FETCH", ",".join(uids),
                          f"(UID FLAGS BODY.PEEK[HEADER.FIELDS {_HEADER_FIELDS}] BODY.PEEK[TEXT]<0.{SNIPPET_BYTES}>)")
        try:   # one search tells which of these have files attached
            typ_a, att = m.uid("SEARCH", "UID", ",".join(uids), "X-GM-RAW", '"has:attachment"')
            with_files = set(att[0].decode().split()) if typ_a == "OK" and att and att[0] else set()
        except imaplib.IMAP4.error:
            with_files = set()
        heads: dict[str, tuple[str, bytes]] = {}
        bodies: dict[str, bytes] = {}
        uid = None
        for part in data:
            if not isinstance(part, tuple):
                continue
            meta = part[0].decode(errors="replace")
            m_uid = re.search(r"UID (\d+)", meta)
            uid = m_uid.group(1) if m_uid else uid
            if uid is None:
                continue
            if "HEADER.FIELDS" in meta:
                flags = re.search(r"FLAGS \(([^)]*)\)", meta)
                heads[uid] = (flags.group(1) if flags else "", part[1])
            if "BODY[TEXT]" in meta:
                bodies[uid] = part[1]
        found: dict[str, Summary] = {}
        for uid, (flags, head) in heads.items():
            h = email.message_from_bytes(head)
            try:
                when = parsedate_to_datetime(h["Date"])
                date = when.strftime("%d %b %Y, %H:%M")
            except Exception:
                when, date = None, _text(h["Date"])
            sent = folder == FOLDERS["Sent"]
            who = _text(h["To"] if sent else h["From"])
            try:
                preview = snippet(h.get("Content-Type", "text/plain"), h.get("Content-Transfer-Encoding", ""),
                                  bodies.get(uid, b""))
            except Exception:
                preview = ""
            found[uid] = Summary(uid, ("To: " if sent else "") + _who(who), _text(h["Subject"]) or "(no subject)", date,
                                 "\\Seen" not in flags, "\\Flagged" in flags, when,
                                 next((a for _, a in getaddresses([who]) if a), ""), preview, uid in with_files,
                                 unsubscribe_target(_text(h["List-Unsubscribe"])))
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

    def labels(self) -> list[str]:
        """The user's own Gmail labels (not Inbox or Gmail's system folders)."""
        typ, data = self._conn("INBOX").list()
        out = []
        for line in data or []:
            text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
            m = re.match(r'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" "?(?P<name>.*?)"?$', text)
            if not m or "\\Noselect" in m["flags"]:
                continue
            name = m["name"]
            if name.upper() != "INBOX" and not name.startswith("[Gmail]"):
                out.append(name)
        return sorted(out, key=str.lower)

    def category_new(self) -> dict[str, int]:
        """"N new" per Inbox tab (Primary / Promotions / Social / Updates): unread from the last day."""
        m = self._conn("INBOX")
        out = {}
        for c in CATEGORIES:
            typ, data = m.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW", f'"category:{c} is:unread newer_than:1d"')
            out[c] = len(data[0].split()) if typ == "OK" and data and data[0] else 0
        return out

    def flags(self, folder: str, uids: list[str]) -> dict[str, tuple[bool, bool]]:
        """uid -> (unread, starred) right now — to catch changes made in Gmail itself."""
        if not uids:
            return {}
        typ, data = self._conn(folder).uid("FETCH", ",".join(uids), "(UID FLAGS)")
        out = {}
        for part in data or []:
            text = (part[0] if isinstance(part, tuple) else part or b"").decode(errors="replace")
            u, f = re.search(r"UID (\d+)", text), re.search(r"FLAGS \(([^)]*)\)", text)
            if u and f:
                out[u.group(1)] = ("\\Seen" not in f.group(1), "\\Flagged" in f.group(1))
        return out

    def wait_for_change(self, folder: str = "INBOX", seconds: int = 300) -> bool:
        """IMAP IDLE: block until Gmail reports something new or changed in `folder`
        (True), or `seconds` pass quietly (False). Use a Mailbox of its own for this."""
        import select
        m = self._conn(folder)
        tag = m._new_tag()
        m.send(tag + b" IDLE\r\n")
        if not m.readline().startswith(b"+"):
            raise RuntimeError("Gmail refused IDLE")
        changed = False
        try:
            # wait on the socket itself — a read timeout would break imaplib's file object for good
            ready = m.sock.pending() > 0 or bool(select.select([m.sock], [], [], seconds)[0])
            if ready:
                line = m.readline()
                changed = line.startswith(b"*") and any(w in line for w in (b"EXISTS", b"EXPUNGE", b"FETCH"))
        finally:
            m.send(b"DONE\r\n")
            while True:   # drain until IDLE's own reply, so the connection is clean for the next command
                line = m.readline()
                if not line or line.startswith(tag):
                    break
        return changed

    def find_contacts(self, name: str, limit: int = 5) -> list[tuple[str, str, int]]:
        """People in this Gmail matching a spoken name -> [(display name, address, how often)],
        best first. People the user has written to count double."""
        words = [w for w in re.split(r"\s+", name.lower().strip()) if w]
        if not words:
            return []
        q = " ".join(re.sub(r'["\\]', "", w) for w in words)
        m = self._conn(FOLDERS["All Mail"])
        typ, data = m.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW", f'"from:({q}) OR to:({q})"')
        uids = (data[0].split() if typ == "OK" and data and data[0] else [])[-60:]
        if not uids:
            return []
        typ, data = m.uid("FETCH", b",".join(uids).decode(), "(BODY.PEEK[HEADER.FIELDS (FROM TO CC)])")
        seen: dict[str, list] = {}
        me = self.user.lower()
        for part in data or []:
            if not isinstance(part, tuple):
                continue
            h = email.message_from_bytes(part[1])
            from_me = me in _text(h["From"]).lower()
            for field_name in ("From", "To", "Cc"):
                for disp, addr in getaddresses([_text(h[field_name])]):
                    a = addr.lower()
                    hay = f"{disp} {a}".lower()
                    if not a or a == me or not all(w in hay for w in words):
                        continue
                    entry = seen.setdefault(a, [disp or addr, a, 0])
                    entry[0] = disp or entry[0]
                    entry[2] += 2 if (from_me and field_name != "From") else 1
        ranked = sorted(seen.values(), key=lambda e: -e[2])
        return [tuple(e) for e in ranked[:limit]]

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

    def archive(self, folder: str, uid: str) -> None:
        """Gmail's Archive: take it out of the Inbox (it stays in All Mail)."""
        self._conn(folder).uid("STORE", uid, "-X-GM-LABELS", "(\\Inbox)")

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
