"""plugins/send_email.py — names to addresses from Gmail, confirmation before sending, reading
unread mail. A fake mailbox stands in for Gmail; nothing is sent."""
import pytest

import plugins.send_email as se


class FakeMailbox:
    sent = []

    def __init__(self, contacts=None):
        self.contacts = contacts or {}

    def find_contacts(self, name):
        return self.contacts.get(name.lower(), [])

    def send(self, to, subject, body, cc=""):
        FakeMailbox.sent.append((to, subject, body, cc))

    def uids(self, folder, query=""):
        return ["9", "8"]

    def summaries(self, folder, uids):
        from judo_mail.mailbox import Summary
        return [Summary(u, f"Sender {u}", f"Subject {u}", "", True, False, snippet="hello there") for u in uids]

    def close(self):
        pass


CONTACTS = {"rahul": [("Rahul Kumar", "rahul@x.com", 12), ("Rahul Jain", "rj@y.com", 2)],
            "aman": [("Aman Verma", "aman@x.com", 5), ("Aman Gupta", "ag@x.com", 4)]}


def test_names_resolve_to_the_person_mailed_most():
    to, question = se.resolve("Rahul, priya@z.com", FakeMailbox(CONTACTS))
    assert to == ["Rahul Kumar <rahul@x.com>", "priya@z.com"] and not question


def test_two_likely_people_or_none_means_ask():
    to, question = se.resolve("Aman", FakeMailbox(CONTACTS))
    assert not to and "aman@x.com" in question and "ag@x.com" in question and "which" in question
    to, question = se.resolve("Neha", FakeMailbox(CONTACTS))
    assert not to and "couldn't find" in question


@pytest.fixture
def plugin(monkeypatch):
    FakeMailbox.sent = []
    import judo_mail.mailbox as mb
    monkeypatch.setattr(mb, "Mailbox", lambda: FakeMailbox(CONTACTS))
    monkeypatch.setattr(se, "get_user_name", lambda: "Ritesh")
    return monkeypatch


def test_sending_waits_for_confirm_on_screen(plugin):
    from core import confirm
    shown = []
    confirm.bind(lambda title, detail: shown.append((title, detail)), lambda: None)
    plugin.setattr(se, "get_plugin_setting", lambda ns, key, default=None: default)
    out = se.run({"to": "Rahul", "subject": "Meeting", "body": "Hi Rahul,\n\nThe meeting is at 5 tomorrow."})
    assert out.startswith("[CONFIRMATION_PENDING]") and FakeMailbox.sent == []          # nothing sent yet
    title, detail = shown[-1]
    assert "rahul@x.com" in title and "Subject: Meeting" in detail and detail.rstrip().endswith("Ritesh")
    confirm.resolve(True)                                                              # the user presses CONFIRM
    import time
    for _ in range(50):
        if FakeMailbox.sent:
            break
        time.sleep(0.02)
    assert FakeMailbox.sent and FakeMailbox.sent[0][0] == "Rahul Kumar <rahul@x.com>"


def test_without_confirmation_setting_it_sends_straight_away(plugin):
    plugin.setattr(se, "get_plugin_setting", lambda ns, key, default=None: False)
    out = se.run({"to": "rahul@x.com", "body": "Thanks, Ritesh"})
    assert out == "Email sent to rahul@x.com." and FakeMailbox.sent[0][2] == "Thanks, Ritesh"   # no double sign-off


def test_missing_details_and_checking_unread(plugin):
    assert "who the email should go to" in se.run({"body": "hi"})
    assert "what the email should say" in se.run({"to": "Rahul"})
    out = se.run({"action": "check"})
    assert "2 unread" in out and "Sender 9: Subject 9 — hello there" in out
