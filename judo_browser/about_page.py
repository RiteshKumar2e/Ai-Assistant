"""
judo_browser/about_page.py — "About JUDO Browser" (judo://about), laid out like
Chrome's About page: logo, version, who made it, what JUDO is, the browser's
features and the technical details (with a Copy button for bug reports).
"""
from __future__ import annotations

import html
import json

from judo_browser import RELEASE, __version__
from judo_browser.newtab import CMD, _sub

URL = "judo://about"
AUTHOR = "Ritesh"
FEATURES = [
    ("🎙️", "Voice controlled", "JUDO opens sites, reads pages, clicks and types here when you ask — in Hindi, English or Hinglish."),
    ("⚡", "Chromium engine", "The same engine as Chrome, so every site works the way it should."),
    ("🛡️", "Sandboxed pages", "Web pages run isolated from your files and programs."),
    ("👤", "JUDO Accounts", "Separate logins, cookies and passwords for each person or profile."),
    ("🔑", "Password manager", "Saves and fills site passwords, encrypted for your Windows account."),
    ("🎨", "Customize JUDO", "Light, dark or device mode, colour themes and New Tab backgrounds."),
    ("🕶️", "Incognito", "Browse without saving history, cookies or site data."),
    ("🧰", "Everyday tools", "Tabs, bookmarks, history, downloads, find, zoom, print, DevTools."),
]

CSS = """
*{box-sizing:border-box}
body{margin:0;background:__BG__;color:__TEXT__;font:14px 'Segoe UI',Roboto,Arial,sans-serif}
main{max-width:760px;margin:0 auto;padding:48px 24px 64px}
.hero{text-align:center}
.logo{font:500 72px/1 'Poppins','Segoe UI',sans-serif;letter-spacing:-2px;display:inline-flex}
.logo span:nth-child(1){color:__J__}.logo span:nth-child(2){color:__U__}
.logo span:nth-child(3){color:__D__}.logo span:nth-child(4){color:__O__}
.product{font:400 26px 'Segoe UI',sans-serif;color:__SUB__;margin-top:6px}
.ver{margin:18px 0 4px;font-size:15px}
.ver small{display:block;color:__SUB__;margin-top:4px}
.by{display:inline-flex;align-items:center;gap:10px;margin-top:20px;padding:10px 20px;border-radius:24px;
 background:__CARD__;border:1px solid __BORDER__;font-size:15px}
.by b{color:__ACCENT__}
.heart{color:__U__}
.card{background:__CARD__;border:1px solid __BORDER__;border-radius:12px;margin-top:24px;overflow:hidden}
.card h2{font:500 16px 'Segoe UI',sans-serif;margin:0;padding:18px 24px 6px}
.card p{margin:0;padding:6px 24px 18px;color:__SUB__;line-height:1.6}
.features{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));padding:4px 12px 14px}
.f{display:flex;gap:14px;padding:12px}
.f span{font-size:22px;line-height:1}
.f b{display:block;font-weight:600;margin-bottom:3px}
.f small{color:__SUB__;line-height:1.5;font-size:13px}
table{width:100%;border-collapse:collapse}
td{padding:10px 24px;border-top:1px solid __BORDER__;vertical-align:top}
td:first-child{color:__SUB__;width:200px}
td code{font:13px Consolas,monospace;word-break:break-all}
.actions{display:flex;gap:8px;padding:14px 24px;border-top:1px solid __BORDER__;flex-wrap:wrap}
.btn{border:1px solid __BORDER__;background:transparent;color:__ACCENT__;border-radius:20px;padding:8px 18px;
 font:500 14px 'Segoe UI',sans-serif;cursor:pointer}
.btn:hover{background:__HOVER__}
footer{text-align:center;color:__SUB__;font-size:12px;margin-top:32px;line-height:1.8}
"""


def page(t: dict, info: dict, token: str = "") -> str:
    """`info`: chromium, qt, pyqt, python, os, sandbox (bool), profile (path), accounts (count)."""
    e = html.escape
    dark = t.get("dark", False)
    details = [("JUDO Browser", f"{__version__} ({RELEASE})"), ("Chromium", info["chromium"]),
               ("Qt / PyQt", f"{info['qt']} / {info['pyqt']}"), ("Python", info["python"]),
               ("Operating system", info["os"]), ("Sandbox", "On" if info["sandbox"] else "Off"),
               ("JUDO Accounts", str(info["accounts"])), ("Profile folder", info["profile"])]
    rows = "".join(f"<tr><td>{e(k)}</td><td><code>{e(v)}</code></td></tr>" for k, v in details)
    text = "\n".join(f"{k}: {v}" for k, v in details)
    feats = "".join(f'<div class="f"><span>{i}</span><div><b>{e(n)}</b><small>{e(d)}</small></div></div>'
                    for i, n, d in FEATURES)
    css = _sub(CSS, {"BG": t["toolbar"], "TEXT": t["text"], "SUB": t["sub"], "BORDER": t["border"],
                     "CARD": t["popup"] if dark else "#FFFFFF", "ACCENT": t["accent"], "HOVER": t["hover"]})
    btn = lambda c, label, **a: (f'<button class="btn" data-cmd="{c}" data-args=\'{e(json.dumps(a))}\'>{label}</button>')
    js = f"""const CMD={json.dumps(CMD)}, TOKEN={json.dumps(token)}, SRC='about', DETAILS={json.dumps(text, ensure_ascii=False)};
const send = o => console.log(CMD + JSON.stringify(Object.assign({{t: TOKEN, src: SRC}}, o)));
document.querySelectorAll('[data-cmd]').forEach(b => b.onclick = () =>
  send(Object.assign({{cmd: b.dataset.cmd}}, JSON.parse(b.dataset.args || '{{}}'))));
document.querySelector('#copy').onclick = () => send({{cmd: 'about_copy', text: DETAILS}});"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>About JUDO Browser</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@500&display=swap" rel="stylesheet">
<style>{css}</style></head><body><main>
<div class="hero"><div class="logo"><span>J</span><span>U</span><span>D</span><span>O</span></div>
<div class="product">Browser</div>
<div class="ver">Version {e(__version__)} ({e(RELEASE)})<small>Chromium {e(info["chromium"])} · 64-bit</small></div>
<div class="by">Made with <span class="heart">♥</span> by <b>{AUTHOR}</b></div></div>

<div class="card"><h2>About JUDO</h2>
<p>JUDO is a real-time voice AI assistant for your computer — say <b>“Hey Judo”</b> and it hears, sees and
acts: it opens apps and websites, sends messages, manages files, sets reminders and answers questions, in
Hindi, English or Hinglish. JUDO Browser is JUDO's own web browser: every website JUDO opens comes here,
and JUDO can read the page, click and type in it for you — while you can use it like any other browser.</p></div>

<div class="card"><h2>What JUDO Browser can do</h2><div class="features">{feats}</div></div>

<div class="card"><h2>Technical details</h2><table>{rows}</table>
<div class="actions"><button class="btn" id="copy">Copy details</button>
{btn("open", "Settings", page="settings")}{btn("account_manage", "JUDO Account")}</div></div>

<footer>JUDO Browser · {e(RELEASE)} · Made by {AUTHOR}<br>Open source under the MIT License ·
Built on Qt WebEngine (Chromium)</footer>
</main><script>{js}</script></body></html>"""
