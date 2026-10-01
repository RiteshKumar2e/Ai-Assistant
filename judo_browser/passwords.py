"""
judo_browser/passwords.py — save and fill site passwords, Chrome-style.

Passwords are encrypted with Windows DPAPI (the same mechanism Chrome uses on
Windows): only this Windows user on this PC can decrypt them; the JSON file
alone is useless to anyone who copies it.

A script in Qt's isolated ApplicationWorld (page JavaScript cannot see or
tamper with it) watches login forms: on submit it reports the site, username
and password; on load it fills saved credentials for that exact origin.
"""
from __future__ import annotations

import base64
import json
import time

import win32crypt
from PyQt6.QtCore import QFile, QIODevice, QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineScript

from judo_browser.app_data import DATA, load, save

STORE = "passwords.json"
WORLD = int(QWebEngineScript.ScriptWorldId.ApplicationWorld.value)

_JS = r"""
new QWebChannel(qt.webChannelTransport, ch => {
  const judo = ch.objects.judo;
  const setValue = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const userField = (root, pw) => {
    const inputs = [...root.querySelectorAll('input')];
    const before = inputs.slice(0, inputs.indexOf(pw)).reverse();
    return before.find(i => /^(text|email|tel)$/.test(i.type) && i.offsetParent !== null)
        || root.querySelector('input[autocomplete=username],input[type=email]');
  };
  const grab = root => {
    const pw = [...root.querySelectorAll('input[type=password]')].find(i => i.value);
    if (!pw) return;
    const u = userField(root, pw);
    judo.captured(location.origin, u ? u.value : '', pw.value);
  };
  document.addEventListener('submit', e => grab(e.target), true);
  document.addEventListener('click', e => {
    const b = e.target.closest && e.target.closest('button,input[type=submit],[role=button]');
    if (b) grab(b.form || document);
  }, true);
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.type === 'password') grab(e.target.form || document);
  }, true);
  // Ask Python once, when the first visible password box appears (login forms
  // often render late); then stop watching — busy pages mutate constantly.
  let asked = false;
  const fill = () => {
    if (asked) return;
    const pw = [...document.querySelectorAll('input[type=password]')].find(i => i.offsetParent !== null && !i.value);
    if (!pw) return;
    asked = true; watcher.disconnect();
    judo.credentials(location.origin, raw => {
      const list = JSON.parse(raw || '[]'); if (!list.length) return;
      const u = userField(pw.form || document, pw);
      if (u && !u.value) setValue(u, list[0].username);
      setValue(pw, list[0].password);
    });
  };
  const watcher = new MutationObserver(fill);
  watcher.observe(document.documentElement, {childList: true, subtree: true});
  fill();
});
"""


def _protect(secret: str) -> str:
    return base64.b64encode(win32crypt.CryptProtectData(secret.encode(), "JUDO Browser", None, None, None, 0)).decode()


def _unprotect(blob: str) -> str:
    return win32crypt.CryptUnprotectData(base64.b64decode(blob), None, None, None, 0)[1].decode()


def origin_of(url: QUrl) -> str:
    port = url.port()
    return f"{url.scheme()}://{url.host()}" + (f":{port}" if port > 0 else "")


class PasswordStore:
    def __init__(self, prefix: str = ""):
        """`prefix`: a JUDO Account's own folder ("accounts/<id>/"); "" = the default account."""
        self.prefix = prefix
        self.items: list[dict] = load(prefix + STORE, [])
        self.never: list[str] = load(prefix + "passwords_never.json", [])

    def _save(self) -> None:
        save(self.prefix + STORE, self.items)
        save(self.prefix + "passwords_never.json", self.never)

    def for_origin(self, origin: str) -> list[dict]:
        out = []
        for it in self.items:
            if it["origin"] == origin:
                try:
                    out.append({"username": it["username"], "password": _unprotect(it["password"])})
                except Exception:
                    pass
        return out

    def known(self, origin: str, username: str, password: str) -> bool:
        return any(c["username"] == username and c["password"] == password for c in self.for_origin(origin))

    def put(self, origin: str, username: str, password: str) -> None:
        self.items = [i for i in self.items if not (i["origin"] == origin and i["username"] == username)]
        self.items.append({"origin": origin, "username": username, "password": _protect(password), "t": time.time()})
        self._save()

    def delete(self, origin: str, username: str) -> None:
        self.items = [i for i in self.items if not (i["origin"] == origin and i["username"] == username)]
        self._save()

    def block(self, origin: str) -> None:
        if origin not in self.never:
            self.never.append(origin)
            self._save()


class Bridge(QObject):
    """One per tab. `offer` asks the window to show the "Save password?" bar."""
    offer = pyqtSignal(str, str, str)

    def __init__(self, store: PasswordStore, page):
        super().__init__(page)
        self.store, self.page = store, page

    def _same_site(self, origin: str) -> bool:
        # the isolated script reports the frame's real origin; also require the
        # tab to be on that site so a stray frame can't collect another site's logins
        top = QUrl(origin_of(self.page.url())).host()
        host = QUrl(origin).host()
        return bool(host) and (host == top or host.endswith("." + top) or top.endswith("." + host))

    @pyqtSlot(str, str, str)
    def captured(self, origin: str, username: str, password: str) -> None:
        if password and self._same_site(origin) and origin not in self.store.never \
                and not self.store.known(origin, username, password):
            self.offer.emit(origin, username, password)

    @pyqtSlot(str, result=str)
    def credentials(self, origin: str) -> str:
        return json.dumps(self.store.for_origin(origin) if self._same_site(origin) else [])


def install_script(profile) -> None:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    f.open(QIODevice.OpenModeFlag.ReadOnly)
    script = QWebEngineScript()
    script.setName("judo-passwords")
    script.setSourceCode(bytes(f.readAll()).decode() + "\n" + _JS)
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    script.setWorldId(WORLD)
    script.setRunsOnSubFrames(True)
    profile.scripts().insert(script)


def attach(page, store: PasswordStore) -> Bridge:
    bridge = Bridge(store, page)
    channel = QWebChannel(page)
    channel.registerObject("judo", bridge)
    page.setWebChannel(channel, WORLD)
    return bridge
