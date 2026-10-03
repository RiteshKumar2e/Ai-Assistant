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


def test_summaries_keep_the_real_date_and_sender_address(box):
    s = box.summaries("INBOX", ["9"])[0]
    assert s.address == "rahul@x.com" and s.when.year == 2026 and s.when.hour == 10


def test_unseen_count_for_the_inbox_badge(box):
    box._imap.status = lambda folder, what: ("OK", [b'"INBOX" (UNSEEN 12)'])
    assert box.unseen("INBOX") == 12
    box._imap.status = lambda folder, what: ("NO", [None])
    assert box.unseen("INBOX") == 0


def test_list_dates_avatars_and_initials():
    from datetime import datetime, timedelta
    from judo_mail import look
    now = datetime(2026, 10, 3, 18, 0)
    assert look.when_text(now.replace(hour=9, minute=5), now=now) == "9:05 AM"          # today: a time
    assert look.when_text(now - timedelta(days=3), now=now) == "30 Sep"                 # this year: day month
    assert look.when_text(datetime(2025, 9, 23), now=now) == "23/09/25"                 # older: full date
    assert look.when_text(None, "raw date") == "raw date"
    assert look.avatar_color("Aman@x.com") == look.avatar_color("aman@x.com")           # same sender, same colour
    assert look.initial("To: priya") == "P" and look.initial("  ") == "?"


def test_window_tabs_bulk_actions_and_reading_with_a_fake_mailbox():
    pytest.importorskip("PyQt6.QtWebEngineWidgets")
    from datetime import datetime, timezone
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtWidgets import QApplication
    app = _app()
    from judo_mail import app as ma
    rows = [mb.Summary(str(i), f"Sender {i}", f"Subject {i}", "", i == 1, False,
                       datetime(2026, 10, 3, 9, i, tzinfo=timezone.utc), f"s{i}@x.com", f"preview {i}", i == 2,
                       "https://x.com/unsub" if i == 1 else "") for i in (1, 2, 3)]
    calls = []

    class FakeMB:
        def uids(self, folder, query=""): calls.append(("uids", folder, query)); return [r.uid for r in rows]
        def summaries(self, folder, uids): return [r for r in rows if r.uid in uids]
        def unseen(self, folder="INBOX"): return 1
        def category_new(self): return {"primary": 2, "promotions": 5, "social": 0, "updates": 0}
        def display_name(self): return ""
        def labels(self): return ["Unsubscribe", "Work"]
        def get(self, folder, uid):
            m = mb.Mail(uid, f"Sender {uid} <s{uid}@x.com>", "me@x.com", "", f"Subject {uid}", "", "<id>")
            m.text = "hello"
            return m
        def mark_read(self, folder, uid, read=True): calls.append(("read" if read else "unread", uid))
        def star(self, folder, uid, on=True): calls.append(("star", uid, on))
        def trash(self, folder, uid): calls.append(("trash", uid))
        def archive(self, folder, uid): calls.append(("archive", uid))

    class FakeWorker:                                    # runs synchronously: no threads in the test
        def run(self, fn, cb=lambda r: None): cb(fn(FakeMB()))
        def shutdown(self): pass

    w = ma.MailWindow(FakeWorker(), "me@x.com")
    assert ("uids", "INBOX", "category:primary") in calls                     # Inbox opens on the Primary tab
    assert "5 new" in w.tabs["promotions"].text() and w.tab_bar.isVisibleTo(w)
    names = [w.folders.item(i).text() for i in range(w.folders.count())]
    assert "Purchases" in names and names[-3:] == ["Labels", "Unsubscribe", "Work"]  # user labels listed
    assert w.model.rowCount() == 3 and w.folder_delegate.counts["Inbox"] == 1 and w.count.text() == "1–3 of 3"

    w._tab("promotions")
    assert ("uids", "INBOX", "category:promotions") in calls and w.title.text() == "Promotions"

    w.list.setCurrentIndex(w.model.index(0, 0))          # opening an unread mail marks it read
    QCoreApplication.processEvents()
    assert w.current.uid == "1" and ("read", "1") in calls and not w.rows[0].unread
    assert w.unsub.isVisibleTo(w)                          # it has a List-Unsubscribe link

    w._check_row(1)                                        # tick two rows, then act on both at once
    w._check_row(2)
    assert w.selected_label.text() == "2 selected" and w.bulk["archive"].isVisibleTo(w)
    w._bulk("unread")
    assert ("unread", "2,3") in calls and w.rows[1].unread and not w.checked
    w._select_all()
    assert len(w.checked) == 3
    w._bulk("archive")
    assert ("archive", "1,2,3") in calls and w.model.rowCount() == 0 and not w.reader.isVisibleTo(w)
    w.close()


def test_accounts_add_switch_and_sign_out(tmp_path, monkeypatch):
    import json
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": "keep-me", "gmail_address": "a@gmail.com", "gmail_app_password": "aaaa"}))
    monkeypatch.setattr(mb, "CONFIG", cfg)
    mb.add_account("b@gmail.com", "bbbb bbbb")
    assert mb.accounts() == ["b@gmail.com", "a@gmail.com"] and mb.credentials() == ("b@gmail.com", "bbbbbbbb")
    mb.switch_account("a@gmail.com")
    assert mb.credentials() == ("a@gmail.com", "aaaa") and mb.accounts()[0] == "a@gmail.com"
    assert mb.remove_account("a@gmail.com") == "b@gmail.com" and mb.accounts() == ["b@gmail.com"]
    assert json.loads(cfg.read_text())["gemini_api_key"] == "keep-me"            # other settings untouched
    assert mb.remove_account("b@gmail.com") is None and mb.accounts() == []


def test_previews_from_partial_bodies():
    import base64 as b64
    plain = mb.snippet('text/plain; charset="utf-8"', "quoted-printable", b"Hi Ritesh =E2=80=94 see https://x.com/a now")
    assert plain == "Hi Ritesh — see now"
    html_part = b64.b64encode(b"<style>p{color:red}</style><p>Your order &amp; invoice is ready</p>")[:-3]
    multi = (b"--B\r\nContent-Type: text/plain\r\n\r\nPlease enable HTML\r\n--B\r\nContent-Type: text/html\r\n"
             b"Content-Transfer-Encoding: base64\r\n\r\n" + html_part)      # cut off mid-way, like a partial fetch
    assert mb.snippet('multipart/alternative; boundary="B"', "", multi).startswith("Your order & invoice")
    assert mb.unsubscribe_target("<mailto:u@x.com?subject=bye>, <https://x.com/u>") == "https://x.com/u"
    assert mb.unsubscribe_target("<mailto:u@x.com>") == "mailto:u@x.com"


def test_live_sync_adds_new_mail_drops_deleted_and_picks_up_gmail_flags():
    pytest.importorskip("PyQt6.QtWebEngineWidgets")
    from datetime import datetime, timezone
    from PyQt6.QtWidgets import QApplication
    app = _app()
    from judo_mail import app as ma
    S = lambda i, unread=False: mb.Summary(str(i), f"S{i}", f"Subj {i}", "", unread, False,
                                           datetime(2026, 10, 3, 9, i, tzinfo=timezone.utc), f"s{i}@x.com")
    state = {"uids": ["3", "2", "1"], "flags": {}}
    rows = {i: S(i) for i in (1, 2, 3, 4)}
    rows[4].unread = True

    class FakeMB:
        def uids(self, folder, query=""): return list(state["uids"])
        def summaries(self, folder, uids): return [rows[int(u)] for u in uids]
        def flags(self, folder, uids): return {u: state["flags"].get(u, (False, False)) for u in uids}
        def unseen(self, folder="INBOX"): return 1
        def category_new(self): return {}
        def display_name(self): return ""
        def labels(self): return []
        def get(self, folder, uid):
            m = mb.Mail(uid, f"S{uid} <s{uid}@x.com>", "me@x.com", "", f"Subj {uid}", "", "<id>")
            m.text = "hi"
            return m
        def mark_read(self, *a): pass

    class FakeWorker:
        def run(self, fn, cb=lambda r: None): cb(fn(FakeMB()))
        def shutdown(self): pass

    w = ma.MailWindow(FakeWorker(), "me@x.com")
    assert w.watcher is None                                      # tests never open a real Gmail connection
    w.list.setCurrentIndex(w.model.index(1, 0))                   # reading mail 2
    assert w.current.uid == "2"
    state["uids"] = ["4", "3", "2"]                               # mail 4 arrived, mail 1 deleted in Gmail…
    state["flags"] = {"3": (False, True)}                         # …and mail 3 starred on the phone
    w._sync()
    assert [s.uid for s in w.rows] == ["4", "3", "2"] and w.rows[0].unread and w.rows[1].starred
    assert w.current.uid == "2" and w.reader.isVisibleTo(w)       # the open mail stayed open
    assert "1 new message" in w.statusBar().currentMessage()
    w.close()


def _app():
    """One QApplication for every window test, made the way JUDO Mail makes its own
    (shared GL contexts), so several windows with mail views can live in one process."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    global _APP                      # kept for the whole run: Qt WebEngine can't start twice in a process
    if QApplication.instance() is None:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        _APP = QApplication(["x", "-platform", "offscreen"])
    return QApplication.instance()


_APP = None


def test_greeting_uses_the_real_name_not_the_address(tmp_path, monkeypatch):
    from judo_mail import look
    monkeypatch.setattr(look, "SETTINGS_DIR", tmp_path)
    assert look.friendly_name("riteshkumar90359@gmail.com") == "Riteshkumar"          # tidy guess: no digits
    assert look.friendly_name("ritesh.kumar_2@x.com") == "Ritesh Kumar"
    look.remember_name("RiteshKumar90359@gmail.com", "Ritesh Kumar")                    # what Gmail says
    assert look.friendly_name("riteshkumar90359@gmail.com") == "Ritesh Kumar"


def test_account_card_lists_other_accounts_and_actions(tmp_path, monkeypatch):
    pytest.importorskip("PyQt6.QtWebEngineWidgets")
    _app()
    from PyQt6.QtWidgets import QLabel, QPushButton
    from judo_mail import app as ma, look
    monkeypatch.setattr(look, "SETTINGS_DIR", tmp_path)
    look.remember_name("me@x.com", "Ritesh Kumar")
    monkeypatch.setattr(ma.mbx, "accounts", lambda: ["me@x.com", "work@x.com"])

    class W:
        def run(self, fn, cb=lambda r: None): pass
        def shutdown(self): pass
    w = ma.MailWindow(W(), "me@x.com")
    card = ma.AccountPopup(w)
    labels = [l.text() for l in card.findChildren(QLabel)]
    buttons = [b.text().strip() for b in card.findChildren(QPushButton)]
    assert "Hi, Ritesh!" in labels and "me@x.com" in labels and "work@x.com" in labels
    assert {"Manage your Google Account", "Add another account", "Sign out of this account",
            "Device", "Light", "Dark"} <= set(buttons)
    card.close()
    w.close()


def test_avatar_opens_the_account_card_every_time(tmp_path, monkeypatch):
    pytest.importorskip("PyQt6.QtWebEngineWidgets")
    _app()
    from judo_mail import app as ma, look
    monkeypatch.setattr(look, "SETTINGS_DIR", tmp_path)
    monkeypatch.setattr(ma.mbx, "accounts", lambda: ["me@x.com"])

    class W:
        def run(self, fn, cb=lambda r: None): pass
        def shutdown(self): pass
    w = ma.MailWindow(W(), "me@x.com")
    w.show()
    for _ in range(3):                         # open, close with ✕, open again ...
        w._account_menu()
        assert w._popup is not None and w._popup.isVisible()
        w._popup.close()
        assert w._popup is None
        w._popup_closed = 0.0                  # (a later click, not the one that closed it)
    w._account_menu()
    w._account_menu()                          # clicking the avatar again closes the card
    assert w._popup is None
    w.close()


def test_add_account_asks_for_the_email_first_then_the_password(monkeypatch):
    pytest.importorskip("PyQt6.QtWebEngineWidgets")
    _app()
    from judo_mail import app as ma
    monkeypatch.setattr(ma.mbx, "accounts", lambda: ["me@gmail.com"])
    checked, added = [], []
    monkeypatch.setattr(ma, "run_async", lambda fn, cb: cb(fn()))
    monkeypatch.setattr(ma.mbx, "check_login", lambda a, p: checked.append((a, p)))
    monkeypatch.setattr(ma.mbx, "add_account", lambda a, p: added.append((a, p)))
    d = ma.AccountDialog()
    d.address.setText("me")                                  # already added
    d._next()
    assert d.pages.currentIndex() == 0 and "already added" in d.email_error.text()
    d.address.setText("aman.verma")                          # a bare username means Gmail
    d._next()
    assert d.pages.currentIndex() == 1 and "aman.verma@gmail.com" in d.who.text()
    d.password.setText("hunter2")
    d._check()
    assert not checked and "16 letters" in d.pw_error.text()
    d.password.setText("abcd efgh ijkl mnop")
    d._check()
    assert added == [("aman.verma@gmail.com", "abcdefghijklmnop")]
    d._back()
    assert d.pages.currentIndex() == 0


def test_message_rows_can_be_copied(monkeypatch):
    pytest.importorskip("PyQt6.QtWebEngineWidgets")
    app = _app()
    from judo_mail import app as ma
    from judo_mail.mailbox import Summary

    class W:
        def run(self, fn, cb=lambda r: None): pass
        def shutdown(self): pass
    w = ma.MailWindow(W(), "me@x.com")
    w.rows = [Summary("1", "Rahul Kumar", "Meeting at 5", "", True, False, address="rahul@x.com",
                      snippet="See you there")]
    w._copy_row(0, "address")
    assert app.clipboard().text() == "rahul@x.com"
    w._copy_row(0, "line")
    assert app.clipboard().text() == "Rahul Kumar <rahul@x.com>\nMeeting at 5\nSee you there"
    w.close()
