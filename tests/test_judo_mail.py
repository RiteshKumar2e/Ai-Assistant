"""judo_mail/mailbox.py parsing and sending — offline, with a fake IMAP/SMTP."""
import email

import pytest

from judo_mail import mailbox as mb

RAW = (b"From: =?utf-8?q?Rahul_Kumar?= <rahul@x.com>\r\nTo: me@x.com\r\nSubject: =?utf-8?b?4KSo4KSu4KS44KWN4KSk4KWH?=\r\n"
       b"Date: Tue, 29 Sep 2026 10:00:00 +0530\r\nMessage-ID: <abc@x>\r\nMIME-Version: 1.0\r\n"
       b"Content-Type: multipart/mixed; boundary=B\r\n\r\n--B\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n"
       b"--B\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"bill.pdf\"\r\n"
       b"Content-Transfer-Encoding: base64\r\n\r\nJVBERg==\r\n--B--\r\n")


class FakeImap:
    def __init__(self):
        self.calls = []

    def select(self, folder):
        return "OK", [b"1"]

    def noop(self):
        return "OK", []

    def uid(self, cmd, *args):
        self.calls.append((cmd,) + args)
        if cmd == "SEARCH":
            return "OK", [b"3 7 9"]
        if cmd == "FETCH" and "HEADER.FIELDS" in args[1]:
            head = RAW.split(b"\r\n\r\n")[0] + b"\r\n\r\n"
            return "OK", [(b"1 (UID 9 FLAGS (\\Flagged) BODY[HEADER.FIELDS (FROM)] {10}", head), b")",
                          (b"2 (UID 7 FLAGS (\\Seen) BODY[HEADER.FIELDS (FROM)] {10}", head), b")"]
        if cmd == "FETCH":
            return "OK", [(b"1 (UID 9 BODY[] {10}", RAW), b")"]
        return "OK", []


@pytest.fixture
def box(monkeypatch):
    monkeypatch.setattr(mb, "credentials", lambda: ("me@x.com", "pw"))
    b = mb.Mailbox()
    b._imap = FakeImap()
    return b


def test_uids_newest_first_and_gmail_search(box):
    assert box.uids("INBOX") == ["9", "7", "3"]
    box.uids("INBOX", 'from:rahul "x"')
    assert box._imap.calls[-1][:4] == ("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")


def test_summaries_decode_names_subjects_and_flags(box):
    s = box.summaries("INBOX", ["9", "7"])
    assert [x.uid for x in s] == ["9", "7"]
    assert s[0].sender == "Rahul Kumar" and s[0].subject == "नमस्ते"
    assert (s[0].unread, s[0].starred, s[1].unread) == (True, True, False)


def test_get_reads_body_and_lists_attachments_without_marking_read(box):
    m = box.get("INBOX", "9")
    assert m.text.strip() == "Hello" and m.attachments == [("bill.pdf", 4)] and m.message_id == "<abc@x>"
    assert all("\\Seen" not in str(c) for c in box._imap.calls)


def test_attachment_saved_without_overwriting(box, tmp_path):
    m = box.get("INBOX", "9")
    (tmp_path / "bill.pdf").write_bytes(b"mine")
    saved = box.save_attachment(m, 0, tmp_path)
    assert saved.name == "bill (1).pdf" and (tmp_path / "bill.pdf").read_bytes() == b"mine"


def test_send_builds_threaded_reply_with_attachment(box, monkeypatch, tmp_path):
    sent = []

    class SMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def login(self, u, p): sent.append(("login", u))
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(mb.smtplib, "SMTP_SSL", SMTP)
    f = tmp_path / "a.txt"
    f.write_text("x")
    box.send("rahul@x.com", "Re: hi", "body", cc="c@x.com", attachments=[str(f)], reply_to=box.get("INBOX", "9"))
    msg = sent[-1]
    assert msg["To"] == "rahul@x.com" and msg["Cc"] == "c@x.com" and msg["In-Reply-To"] == "<abc@x>"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["a.txt"]


def test_missing_credentials_explain_what_to_add(monkeypatch, tmp_path):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mb, "CONFIG", cfg)
    with pytest.raises(RuntimeError, match="gmail_app_password"):
        mb.credentials()
