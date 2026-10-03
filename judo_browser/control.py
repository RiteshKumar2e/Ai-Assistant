"""
judo_browser/control.py — lets JUDO drive JUDO Browser by voice.

A tiny HTTP server on 127.0.0.1 (random port, fresh secret token per run,
both written to ~/.judo/browser/control.json for judo_browser/client.py).
Requests are handed to the Qt main thread — every page/tab operation must run
there — and answered when the page has actually done it (loaded, clicked…).

A command the browser does not know returns null, so JUDO falls back to its
Edge path instead of failing.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from judo_browser.app import DATA, to_url

CONTROL_FILE = DATA / "control.json"
ENGINES = {"google": "https://www.google.com/search?q=", "bing": "https://www.bing.com/search?q=",
           "duckduckgo": "https://duckduckgo.com/?q=", "youtube": "https://www.youtube.com/results?search_query="}
LOAD_WAIT_MS = 15000

# Finds the element a spoken description means: a CSS selector if one was
# given, else the visible element whose text/label/placeholder best matches.
_FIND_JS = r"""
const norm = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
const visible = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
const label = e => norm([e.innerText, e.value, e.getAttribute('aria-label'), e.getAttribute('title'),
  e.getAttribute('placeholder'), e.getAttribute('name'), e.id,
  e.labels && [...e.labels].map(l => l.innerText).join(' ')].filter(Boolean).join(' '));
const CLICKABLE = 'a,button,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],' +
  'input[type=submit],input[type=button],summary,label,[onclick]';
const EDITABLE = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=checkbox])' +
  ':not([type=radio]),textarea,[contenteditable=""],[contenteditable=true],[role=textbox],[role=searchbox],[role=combobox]';
function find(want, sel, kind) {
  if (sel) { try { const e = document.querySelector(sel); if (e) return e; } catch (_) {} }
  const w = norm(want), all = [...document.querySelectorAll(kind === 'click' ? CLICKABLE : EDITABLE)].filter(visible);
  if (!w) {
    const a = document.activeElement;
    if (kind !== 'click' && a && a.matches && a.matches(EDITABLE)) return a;
    return all[0] || null;
  }
  let best = null, top = 0;
  for (const e of all) {
    const l = label(e); let s = 0;
    if (l === w) s = 100;
    else if (l.includes(w)) s = 70 - Math.min(40, l.length / 25);
    else { const ws = w.split(' '), hit = ws.filter(x => l.includes(x)).length; s = hit ? 40 * hit / ws.length : 0; }
    if (s > top) { top = s; best = e; }
  }
  return best;
}
"""


def _scoped(body: str) -> str:
    """The finder helpers + one command, inside a function: top-level consts
    in the page would clash with the next command's ("already declared")
    and every command after the first would silently fail."""
    return "(() => {" + _FIND_JS + body + "})()"


class _Job:
    def __init__(self):
        self.event, self.result, self._done = threading.Event(), None, False

    def finish(self, result) -> None:
        if not self._done:
            self._done, self.result = True, result
            self.event.set()


class ControlServer(QObject):
    request = pyqtSignal(object)

    def __init__(self, browser):
        super().__init__()
        self.browser = browser
        self.token = secrets.token_urlsafe(24)
        self.request.connect(self._run, Qt.ConnectionType.QueuedConnection)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONTROL_FILE.write_text(json.dumps({"port": server.server_address[1], "token": self.token,
                                            "pid": os.getpid()}), encoding="utf-8")
        QApplication.instance().aboutToQuit.connect(self._cleanup)

    def _cleanup(self) -> None:
        try:
            if json.loads(CONTROL_FILE.read_text(encoding="utf-8")).get("pid") == os.getpid():
                CONTROL_FILE.unlink()
        except Exception:
            pass

    def call(self, action: str, params: dict, timeout: float = 60):
        job = _Job()
        self.request.emit((action, params, job))
        return job.result if job.event.wait(timeout) else f"Browser action '{action}' timed out."

    def _run(self, item) -> None:
        action, params, job = item
        try:
            Commands(self.browser).dispatch(action, params, job.finish)
        except Exception as e:
            job.finish(f"Browser error ({action}): {e}")


def _handler(server: ControlServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):   # keep the console quiet
            pass

        def _reply(self, obj, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._reply({"ok": True, "pid": os.getpid()}) if self.path == "/ping" else self._reply({}, 404)

        def do_POST(self):
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except ValueError:
                return self._reply({"error": "bad json"}, 400)
            if self.path != "/cmd" or not hmac.compare_digest(str(req.get("token", "")), server.token):
                return self._reply({"error": "forbidden"}, 403)
            self._reply({"result": server.call(str(req.get("action", "")), req.get("params") or {})})
    return Handler


class Commands:
    """One method per browser_control action; each calls done(text) when the
    page has finished — or done(None) when it can't be done here."""

    def __init__(self, browser):
        self.b = browser

    def dispatch(self, action: str, p: dict, done) -> None:
        fn = getattr(self, "do_" + action, None)
        if fn is None:
            return done(None)
        w = self._window()
        fn(w, w.view(), p, done)

    # helpers
    def _window(self):
        live = [w for w in self.b.windows if not w.incognito]
        w = next((x for x in live if x.isActiveWindow()), live[-1] if live else None) or self.b.new_window()
        w.showMaximized() if w.isMinimized() else w.show()
        w.raise_()
        w.activateWindow()
        return w

    @staticmethod
    def _after_load(v, done, fallback: str) -> None:
        state = {"fired": False}

        def finish(ok=True):
            if not state["fired"]:
                state["fired"] = True
                try:
                    v.loadFinished.disconnect(finish)
                except TypeError:
                    pass
                done(f"Opened {v.title() or v.url().toString()} ({v.url().toString()})" if ok else fallback)
        v.loadFinished.connect(finish)
        QTimer.singleShot(LOAD_WAIT_MS, lambda: finish(False))

    @staticmethod
    def _js(v, script: str, done, fmt=lambda r: r) -> None:
        v.page().runJavaScript(script, 0, lambda r: done(fmt(r)))

    # navigation
    def do_focus(self, w, v, p, done):
        done("JUDO Browser is in front.")

    def do_go_to(self, w, v, p, done):
        url = to_url(p.get("url", ""))
        host = url.host().removeprefix("www.")
        for i in range(w.tabs.count()):   # already open? switch instead of a duplicate tab
            tab = w.tabs.widget(i)
            if host and tab.url().host().removeprefix("www.") == host:
                w.tabs.setCurrentIndex(i)
                if url.path() in ("", "/") and not url.hasQuery() or tab.url().matches(
                        url, QUrl.UrlFormattingOption.StripTrailingSlash | QUrl.UrlFormattingOption.RemoveFragment):
                    return done(f"Switched to the open {tab.title() or host} tab.")
                self._after_load(tab, done, f"Opening {url.toString()} (still loading).")   # same site, another page
                return w.load(url, tab)
        blank = v.url().toString() in ("", "about:blank")
        target = v if blank else w.new_tab(blank=True)
        self._after_load(target, done, f"Opening {url.toString()} (still loading).")
        w.load(url, target)

    def do_new_tab(self, w, v, p, done):
        target = w.new_tab(blank=bool(p.get("url")))
        if not p.get("url"):
            return done("Opened a new tab.")
        self._after_load(target, done, "Opening in a new tab (still loading).")
        w.load(to_url(p["url"]), target)

    def do_search(self, w, v, p, done):
        base = ENGINES.get(str(p.get("engine", "google")).lower(), ENGINES["google"])
        q = QUrl.toPercentEncoding(str(p.get("query", ""))).data().decode()
        self.do_go_to(w, v, {"url": base + q}, done)

    def do_back(self, w, v, p, done):
        v.back()
        done("Went back.")

    def do_forward(self, w, v, p, done):
        v.forward()
        done("Went forward.")

    def do_reload(self, w, v, p, done):
        v.reload()
        done("Reloaded the page.")

    def do_get_url(self, w, v, p, done):
        done(f"{v.title()} — {v.url().toString()}")

    def do_get_text(self, w, v, p, done):
        self._js(v, "document.title + '\\n\\n' + (document.body ? document.body.innerText : '').slice(0, 8000)",
                 done, lambda r: (r or "").strip() or "The page has no readable text.")

    # tabs
    def do_switch_tab(self, w, v, p, done):
        words = str(p.get("target") or p.get("text") or "").lower().split()
        for i in range(w.tabs.count()):
            tab = w.tabs.widget(i)
            hay = (tab.title() + " " + tab.url().toString()).lower()
            if words and all(x in hay for x in words):
                w.tabs.setCurrentIndex(i)
                return done(f"Switched to {tab.title()}.")
        done(f"No open tab matches '{' '.join(words)}'.")

    def do_close_tab(self, w, v, p, done):
        w.close_tab(w.tabs.currentIndex())
        done("Closed the tab.")

    # page interaction
    def do_click(self, w, v, p, done):
        want = p.get("text") or p.get("description") or ""
        script = _scoped(f"""
          const e = find({json.dumps(want)}, {json.dumps(p.get('selector') or '')}, 'click');
          if (!e) return null; e.scrollIntoView({{block: 'center'}}); e.focus(); e.click();
          return (label(e) || e.tagName.toLowerCase()).slice(0, 80);""")
        self._js(v, script, done, lambda r: f"Clicked '{r}'." if r else f"Could not find '{want}' to click on this page.")

    do_smart_click = do_click

    def do_type(self, w, v, p, done):
        text, clear = str(p.get("text", "")), bool(p.get("clear_first", True))
        want = p.get("description") or ""
        script = _scoped(f"""
          const e = find({json.dumps(want)}, {json.dumps(p.get('selector') or '')}, 'input');
          if (!e) return null; e.scrollIntoView({{block: 'center'}}); e.focus();
          const text = {json.dumps(text)}, clear = {json.dumps(clear)};
          if (e.isContentEditable) {{ if (clear) document.execCommand('selectAll'); document.execCommand('insertText', false, text); }}
          else {{ const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, clear ? text : e.value + text);
            e.dispatchEvent(new Event('input', {{bubbles: true}})); e.dispatchEvent(new Event('change', {{bubbles: true}})); }}
          return (label(e) || e.tagName.toLowerCase()).slice(0, 60);""")
        self._js(v, script, done, lambda r: f"Typed into '{r}'." if r else "No text box found to type into.")

    do_smart_type = do_type

    def do_fill_form(self, w, v, p, done):
        fields = p.get("fields") or {}
        results: list[str] = []
        items = list(fields.items())

        def step(i=0):
            if i == len(items):
                return done("; ".join(results) or "No fields given.")
            key, value = items[i]
            self.do_type(w, v, {"description": key, "selector": key if key[:1] in "#.[" else "", "text": value},
                         lambda r: (results.append(r), step(i + 1)))
        step()

    def do_press(self, w, v, p, done):
        name = str(p.get("key", "Enter"))
        parts = [x.strip().lower() for x in name.replace("-", "+").split("+") if x.strip()]
        mods = Qt.KeyboardModifier.NoModifier
        for m, flag in (("ctrl", Qt.KeyboardModifier.ControlModifier), ("shift", Qt.KeyboardModifier.ShiftModifier),
                        ("alt", Qt.KeyboardModifier.AltModifier)):
            if m in parts:
                mods |= flag
                parts.remove(m)
        key_name = parts[-1] if parts else "enter"
        named = {"enter": (Qt.Key.Key_Return, "\r"), "return": (Qt.Key.Key_Return, "\r"),
                 "escape": (Qt.Key.Key_Escape, ""), "esc": (Qt.Key.Key_Escape, ""), "tab": (Qt.Key.Key_Tab, "\t"),
                 "backspace": (Qt.Key.Key_Backspace, ""), "delete": (Qt.Key.Key_Delete, ""),
                 "space": (Qt.Key.Key_Space, " "), "up": (Qt.Key.Key_Up, ""), "arrowup": (Qt.Key.Key_Up, ""),
                 "down": (Qt.Key.Key_Down, ""), "arrowdown": (Qt.Key.Key_Down, ""), "left": (Qt.Key.Key_Left, ""),
                 "arrowleft": (Qt.Key.Key_Left, ""), "right": (Qt.Key.Key_Right, ""), "arrowright": (Qt.Key.Key_Right, ""),
                 "pagedown": (Qt.Key.Key_PageDown, ""), "pageup": (Qt.Key.Key_PageUp, ""),
                 "home": (Qt.Key.Key_Home, ""), "end": (Qt.Key.Key_End, ""), "f5": (Qt.Key.Key_F5, "")}
        if key_name in named:
            key, text = named[key_name]
        elif len(key_name) == 1:
            key, text = Qt.Key(ord(key_name.upper())), ("" if mods & Qt.KeyboardModifier.ControlModifier else key_name)
        else:
            return done(f"Unknown key '{name}'.")
        target = v.focusProxy() or v
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):   # real key events, not synthetic JS ones
            QApplication.sendEvent(target, QKeyEvent(kind, key, mods, text))
        done(f"Pressed {name}.")

    def do_scroll(self, w, v, p, done):
        amount = int(p.get("amount", 500)) * (-1 if str(p.get("direction", "down")).lower() == "up" else 1)
        self._js(v, f"window.scrollBy({{top: {amount}, behavior: 'smooth'}}); 1", done,
                 lambda r: f"Scrolled {'up' if amount < 0 else 'down'}.")

    def do_screenshot(self, w, v, p, done):
        path = p.get("path") or str(Path.home() / "Desktop" / f"judo_browser_{time.strftime('%Y%m%d_%H%M%S')}.png")
        done(f"Screenshot saved: {path}" if v.grab().save(path) else "Could not save the screenshot.")

    # ── job applications (jobs/apply.py) ─────────────────────────────────────
    # Structured answers (dicts) so the agent decides from what the page really shows.

    def do_page_state(self, w, v, p, done):
        script = r"""(() => {
          const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
          const text = (document.body ? document.body.innerText : '').slice(0, 12000);
          const frames = [...document.querySelectorAll('iframe')].map(f => f.src || '').join(' ');
          const captcha = /recaptcha|hcaptcha|turnstile|arkoselabs|funcaptcha|captcha/i.test(frames)
            || !!document.querySelector('.g-recaptcha,.h-captcha,[data-sitekey],#captcha,.cf-turnstile')
            || /verify (that )?you are (a )?human|i'm not a robot|complete the captcha/i.test(text);
          const pw = [...document.querySelectorAll('input[type=password]')].filter(vis).length;
          const fields = [...document.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(
            e => vis(e) || e.type === 'file').length;
          const buttons = [...document.querySelectorAll('button,input[type=submit],[role=button],a.button,a[class*=apply]')]
            .filter(vis).map(b => (b.innerText || b.value || b.getAttribute('aria-label') || '').trim()).filter(Boolean).slice(0, 40);
          // fields the browser itself refuses (HTML5 validation): its message is a bubble, not page text
          const invalid = [...document.querySelectorAll('input:not([type=hidden]),select,textarea')]
            .filter(e => (vis(e) || e.type === 'file') && e.willValidate && !e.validity.valid).slice(0, 10)
            .map(e => (((e.labels && e.labels[0] && e.labels[0].innerText) || e.name || e.id || '').trim().slice(0, 80)
                       + ': ' + e.validationMessage).trim());
          return {url: location.href, title: document.title, text, captcha, password_fields: pw, fields, buttons, invalid};
        })()"""
        self._js(v, script, done, lambda r: r or {"error": "page did not answer"})

    def do_read_form(self, w, v, p, done):
        script = r"""(() => {
          const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
          const clean = s => (s || '').replace(/\s+/g, ' ').trim();
          const labelOf = e => {
            let t = '';
            if (e.labels && e.labels.length) t = [...e.labels].map(l => l.innerText).join(' ');
            if (!t && e.getAttribute('aria-labelledby')) t = e.getAttribute('aria-labelledby').split(' ')
              .map(id => (document.getElementById(id) || {}).innerText || '').join(' ');
            if (!t) t = e.getAttribute('aria-label') || '';
            if (!t) { const fs = e.closest('fieldset'); if (fs && fs.querySelector('legend')) t = fs.querySelector('legend').innerText; }
            if (!t) { let n = e.parentElement; for (let i = 0; n && i < 3 && !t; i++, n = n.parentElement) {
                const l = n.querySelector('label,legend,.label,[class*=label],[class*=question]'); if (l && !l.contains(e)) t = l.innerText; } }
            if (!t) t = e.getAttribute('placeholder') || e.name || e.id || '';
            return clean(t).slice(0, 300);
          };
          let n = 0; const out = []; const groups = {};
          for (const e of document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]),select,textarea')) {
            const type = e.tagName === 'SELECT' ? 'select' : e.tagName === 'TEXTAREA' ? 'textarea' : (e.type || 'text');
            if (type !== 'file' && !vis(e)) continue;
            if (!e.dataset.judoId) e.dataset.judoId = 'f' + (++n) + '_' + Math.random().toString(36).slice(2, 6);
            const sel = '[data-judo-id="' + e.dataset.judoId + '"]';
            const label = labelOf(e);
            const required = e.required || e.getAttribute('aria-required') === 'true' || /\*/.test(label);
            const grouped = type === 'radio' || (type === 'checkbox' && e.name &&
                            document.querySelectorAll('input[name="' + CSS.escape(e.name) + '"]').length > 1);
            if (grouped) {
              const key = e.name || sel;
              const optLabel = clean((e.labels && e.labels[0] && e.labels[0].innerText) || e.value);
              if (!groups[key]) {
                let q = ''; const fs = e.closest('fieldset');
                if (fs && fs.querySelector('legend')) q = fs.querySelector('legend').innerText;
                for (let p = e.parentElement, i = 0; p && i < 4 && !q; i++, p = p.parentElement) {
                  const l = p.querySelector('legend,.label,[class*=label],[class*=question],label:not([for])');
                  if (l && !l.contains(e) && clean(l.innerText) !== optLabel) q = l.innerText; }
                groups[key] = {selector: 'input[name="' + CSS.escape(e.name) + '"]', type, label: clean(q || e.name).slice(0, 300),
                               required, options: [], value: ''};
                out.push(groups[key]);
              }
              groups[key].options.push(optLabel);
              if (e.checked) groups[key].value = optLabel;
              if (required) groups[key].required = true;
              continue;
            }
            const f = {selector: sel, type, label, name: e.name || '', required,
                       value: type === 'file' ? (e.files.length ? e.files[0].name : '')
                            : (type === 'checkbox' ? (e.checked ? 'yes' : '') : (e.value || '')),
                       accept: e.getAttribute('accept') || '', autocomplete: e.getAttribute('autocomplete') || ''};
            if (type === 'select') {   // choices by their text; the "Select..." placeholder (value "") isn't one
              f.options = [...e.options].filter(o => o.value !== '').map(o => clean(o.text)).filter(Boolean);
              f.value = e.selectedIndex >= 0 && e.value !== '' ? clean(e.options[e.selectedIndex].text) : '';
            }
            out.push(f);
          }
          return out;
        })()"""
        self._js(v, script, done, lambda r: r if isinstance(r, list) else [])

    def do_set_field(self, w, v, p, done):
        sel, value = str(p.get("selector", "")), str(p.get("value", ""))
        script = r"""(() => {
          const sel = %s, value = %s;
          const els = [...document.querySelectorAll(sel)]; if (!els.length) return 'missing';
          const e = els[0]; const norm = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
          const fire = x => { x.dispatchEvent(new Event('input', {bubbles: true}));
                              x.dispatchEvent(new Event('change', {bubbles: true}));
                              x.dispatchEvent(new Event('blur', {bubbles: true})); };
          const optText = r => norm((r.labels && r.labels[0] && r.labels[0].innerText) || r.value);
          if (e.type === 'radio' || (e.type === 'checkbox' && els.length > 1)) {
            const want = norm(value);
            const hit = els.find(r => optText(r) === want) || els.find(r => optText(r).startsWith(want));
            if (!hit) return 'no-option'; hit.click(); return hit.checked ? 'ok' : 'unchanged';
          }
          if (e.type === 'checkbox') { const on = /^(yes|true|1|on|checked)$/i.test(value); if (e.checked !== on) e.click(); return 'ok'; }
          e.scrollIntoView({block: 'center'}); e.focus();
          if (e.tagName === 'SELECT') {
            const want = norm(value);
            const o = [...e.options].find(o => norm(o.text) === want || norm(o.value) === want)
                   || [...e.options].find(o => norm(o.text).startsWith(want));
            if (!o) return 'no-option'; e.value = o.value; fire(e); return 'ok';
          }
          const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, value); fire(e);
          return e.value === value ? 'ok' : 'unchanged';
        })()""" % (json.dumps(sel), json.dumps(value))
        self._js(v, script, done, lambda r: r or "error")

    def do_upload_file(self, w, v, p, done):
        """Attach a file to a file input. Chromium opens a file picker only after a real
        click, so the page's picker is armed with exactly this one file (Page.chooseFiles)
        and the input — or the button that opens it — gets a genuine mouse click."""
        path, sel = str(p.get("path", "")), str(p.get("selector", ""))
        if not Path(path).is_file():
            return done("missing-file")
        locate = r"""(() => {
          const e = document.querySelector(%s); if (!e) return null;
          const vis = x => { const r = x.getBoundingClientRect(); const s = getComputedStyle(x);   // on screen, not parked at left:-9999px
            return r.width > 2 && r.height > 2 && r.right > 0 && r.left < innerWidth && s.visibility !== 'hidden'
              && s.display !== 'none' && s.opacity !== '0'; };
          let t = vis(e) ? e : ((e.id && document.querySelector('label[for="' + CSS.escape(e.id) + '"]')) || null);
          if (!t || !vis(t)) { t = null; for (let n = e.parentElement, i = 0; n && i < 4 && !t; i++, n = n.parentElement) {
              t = [...n.querySelectorAll('button,[role=button],label,a')].find(vis) || null; } }
          if (!t) { e.style.cssText += ';display:block!important;opacity:1!important;width:200px;height:30px;position:relative;';
                    t = e; }
          t.scrollIntoView({block: 'center'}); const r = t.getBoundingClientRect();
          return [r.left + r.width / 2, r.top + r.height / 2];
        })()""" % json.dumps(sel)
        check = ("(() => { const e = document.querySelector(%s); "
                 "return e && e.files && e.files.length ? e.files[0].name : ''; })()" % json.dumps(sel))

        def click(pt):
            if not pt:
                return done("missing")
            from PyQt6.QtCore import QPointF
            from PyQt6.QtGui import QMouseEvent
            v.page().armed_files = [path]
            z, target = v.zoomFactor(), (v.focusProxy() or v)
            pos = QPointF(pt[0] * z, pt[1] * z)
            for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                buttons = Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton
                QApplication.sendEvent(target, QMouseEvent(kind, pos, target.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                                           buttons, Qt.KeyboardModifier.NoModifier))

            def verify(r):
                v.page().armed_files = None              # never left armed for a later picker
                done(f"attached:{r}" if r else "not-attached")
            QTimer.singleShot(1500, lambda: self._js(v, check, verify))
        QTimer.singleShot(300, lambda: self._js(v, locate, click))

    def do_submit_form(self, w, v, p, done):
        """Press the form's submit button. jobs/apply.py only sends this from inside the
        on-screen confirmation's callback."""
        sel = str(p.get("selector") or "")
        script = r"""(() => {
          const vis = x => { const r = x.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
          let e = %s ? document.querySelector(%s) : null;
          if (!e) e = [...document.querySelectorAll('button,input[type=submit],[role=button]')].filter(vis)
            .find(x => /submit|send application|apply now|^apply$|finish|complete application/i.test((x.innerText || x.value || '').trim()));
          if (!e) return null; e.scrollIntoView({block: 'center'}); e.click();
          return (e.innerText || e.value || 'submit').trim().slice(0, 60);
        })()""" % (json.dumps(sel), json.dumps(sel))

        def pressed(r):
            if not r:
                return done({"clicked": False})
            QTimer.singleShot(int(p.get("wait_ms", 5000)),
                              lambda: self.do_page_state(w, v, {}, lambda st: done({"clicked": r, "after": st})))
        self._js(v, script, pressed)
