"""
judo_browser/account_page.py — "Manage your JUDO Account": a settings page laid
out like Google's account page (left navigation; Home, Personal info, Data &
privacy, Security, Accounts), opened as judo://account.

Like the New Tab page it is plain HTML that talks back by logging
`newtab.CMD + json`; MainWindow.account_command does the work and redraws it.
"""
from __future__ import annotations

import html
import json

from judo_browser.accounts import COLORS
from judo_browser.newtab import CMD, SVG, _avatar, _sub

URL = "judo://account"
SECTIONS = {"home": "Home", "personal": "Personal info", "privacy": "Data & privacy",
            "security": "Security", "accounts": "Accounts"}
ICONS = {
    "home": "M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z",
    "personal": "M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2"
                "c0-2.66-5.33-4-8-4z",
    "privacy": "M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 10.99h7c-.53 4.12-3.28 7.79-7 "
               "8.94V12H5V6.3l7-3.11v8.8z",
    "security": "M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10"
                "c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 "
                "3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z",
    "accounts": "M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34"
                " 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67"
                "-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z",
    "chevron": "M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z",
}


def _icon(name: str) -> str:
    return f'<svg viewBox="0 0 24 24"><path d="{ICONS[name]}"/></svg>'


CSS = """
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:__BG__;color:__TEXT__;font:14px 'Segoe UI',Roboto,Arial,sans-serif}
svg{width:20px;height:20px;fill:currentColor;flex:none}
button{font:inherit}
.av{border-radius:50%;display:grid;place-items:center;color:#fff;font-weight:500;overflow:hidden;flex:none;
 font-family:'Segoe UI',sans-serif}
.av img{width:100%;height:100%;object-fit:cover}
.top{position:sticky;top:0;z-index:5;display:flex;align-items:center;height:64px;padding:0 20px;background:__BG__;
 border-bottom:1px solid __BORDER__}
.brand{display:flex;align-items:baseline;gap:6px;font-size:22px;color:__SUB__}
.brand b{font:500 22px 'Poppins','Segoe UI',sans-serif;letter-spacing:-.5px}
.brand b span:nth-child(1){color:#4285F4}.brand b span:nth-child(2){color:#EA4335}
.brand b span:nth-child(3){color:#FBBC05}.brand b span:nth-child(4){color:#34A853}
.top .av{width:32px;height:32px;font-size:15px;margin-left:auto}
.wrap{display:flex;max-width:1180px;margin:0 auto}
nav{width:280px;padding:12px 0;position:sticky;top:64px;align-self:flex-start}
nav button{display:flex;align-items:center;gap:20px;width:calc(100% - 12px);height:48px;padding:0 24px;border:0;
 border-radius:0 24px 24px 0;background:transparent;color:__TEXT__;cursor:pointer;font-size:14px}
nav button svg{color:__SUB__}
nav button:hover{background:__HOVER__}
nav button.sel{background:__SELECT__;color:__ACCENT_TEXT__;font-weight:600}
nav button.sel svg{color:__ACCENT_TEXT__}
main{flex:1;min-width:0;padding:28px 32px 80px;max-width:820px}
section{display:none}section.show{display:block}
h1{font:400 28px 'Segoe UI',sans-serif;margin:8px 0 8px;text-align:center}
h2{font:400 26px 'Segoe UI',sans-serif;margin:8px 0 6px;text-align:center}
.lead{color:__SUB__;text-align:center;margin:0 0 28px;font-size:15px;line-height:1.5}
.hero{display:flex;flex-direction:column;align-items:center;margin:8px 0 6px}
.pic{position:relative}
.pic .av{width:96px;height:96px;font-size:42px}
.cam{position:absolute;right:-2px;bottom:0;width:32px;height:32px;border-radius:50%;border:1px solid __BORDER__;
 background:__CARD__;color:__TEXT__;display:grid;place-items:center;cursor:pointer}
.cam svg{width:16px;height:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
.card{border:1px solid __BORDER__;border-radius:12px;background:__CARD__;overflow:hidden;margin-bottom:16px}
.grid .card{margin:0;display:flex;flex-direction:column}
.card .pad{padding:20px 24px;flex:1}
.card h3{font:400 20px 'Segoe UI',sans-serif;margin:0 0 6px}
.card p{color:__SUB__;margin:0;line-height:1.5}
.card .big{font-size:30px;color:__TEXT__;margin:10px 0 0}
.link{display:block;width:100%;text-align:left;border:0;border-top:1px solid __BORDER__;background:transparent;
 color:__ACCENT_TEXT__;padding:14px 24px;cursor:pointer;font-weight:500}
.link:hover{background:__HOVER__}
.row{display:flex;align-items:center;gap:16px;width:100%;min-height:64px;padding:12px 24px;border:0;
 border-top:1px solid __BORDER__;background:transparent;color:__TEXT__;text-align:left;cursor:pointer}
.card .row:first-of-type{border-top:0}
.row:hover{background:__HOVER__}
.row .k{width:180px;flex:none;color:__SUB__;font-size:12px;text-transform:uppercase;letter-spacing:.6px}
.row .v{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}
.row .v small{display:block;color:__SUB__;margin-top:2px}
.row > svg{color:__SUB__}
.row.static{cursor:default}.row.static:hover{background:transparent}
.row .av{width:40px;height:40px;font-size:18px}
.edit{display:none;padding:4px 24px 18px 220px;border-top:0}
.edit.show{display:block}
.edit input{width:100%;max-width:420px;padding:11px 12px;border-radius:6px;border:1px solid __BORDER__;
 background:__BG__;color:__TEXT__;font-size:15px;outline:none}
.edit input:focus{border-color:__ACCENT__}
.actions{display:flex;gap:8px;margin-top:12px}
.btn{border:1px solid __BORDER__;background:transparent;color:__ACCENT_TEXT__;border-radius:20px;padding:8px 20px;
 font-weight:500;cursor:pointer}
.btn:hover{background:__HOVER__}
.btn.primary{background:__ACCENT__;color:__ON_ACCENT__;border-color:transparent}
.btn.danger{color:#D93025}
.colors{display:flex;gap:10px;flex-wrap:wrap}
.colors span{width:28px;height:28px;border-radius:50%;cursor:pointer}
.colors span.sel{outline:2px solid __TEXT__;outline-offset:2px}
.pill{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600}
.on{background:#E6F4EA;color:#137333}.off{background:#FCE8E6;color:#C5221F}
.dark .on{background:#0D3D23;color:#81C995}.dark .off{background:#5C1A16;color:#F28B82}
.switch{position:relative;width:36px;height:20px;flex:none}
.switch input{opacity:0;width:0;height:0}
.switch i{position:absolute;inset:0;border-radius:10px;background:__BORDER__;transition:.2s}
.switch i:before{content:'';position:absolute;left:2px;top:2px;width:16px;height:16px;border-radius:50%;
 background:#fff;transition:.2s}
.switch input:checked + i{background:__ACCENT__}.switch input:checked + i:before{transform:translateX(16px)}
.tag{margin-left:8px;font-size:12px;color:__ACCENT_TEXT__;font-weight:600}
.add{padding:18px 24px;border-top:1px solid __BORDER__;display:none}.add.show{display:block}
.add input{width:100%;max-width:420px;margin-bottom:10px;padding:11px 12px;border-radius:6px;
 border:1px solid __BORDER__;background:__BG__;color:__TEXT__;font-size:15px;outline:none;display:block}
.note{color:__SUB__;font-size:12px;text-align:center;margin-top:24px}
@media (max-width:860px){nav{width:72px}nav button{padding:0 26px;gap:0;font-size:0}.edit{padding-left:24px}
 .row .k{width:110px}}
"""

JS = r"""
const send = o => console.log(CMD + JSON.stringify(o));
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
function show(id) {
  $$('section').forEach(s => s.classList.toggle('show', s.id === id));
  $$('nav button').forEach(b => b.classList.toggle('sel', b.dataset.go === id));
  scrollTo(0, 0);
  send({cmd: 'account_section', section: id});
}
$$('[data-go]').forEach(b => b.onclick = () => show(b.dataset.go));
$$('[data-photo]').forEach(b => b.onclick = e => { e.stopPropagation(); send({cmd: 'account_photo'}); });
const save = o => send(Object.assign({cmd: 'account_edit', name: ACCOUNT.name, email: ACCOUNT.email,
                                      color: ACCOUNT.color}, o));
$$('[data-edit]').forEach(r => r.onclick = () => {
  const box = $('#edit-' + r.dataset.edit); box.classList.toggle('show');
  const i = box.querySelector('input'); if (i) { i.focus(); i.select(); }
});
$$('.edit form').forEach(f => f.onsubmit = e => {
  e.preventDefault();
  const i = f.querySelector('input'); if (i.name === 'name' && !i.value.trim()) return i.focus();
  save({[i.name]: i.value});
});
$$('.edit [data-cancel]').forEach(b => b.onclick = () => b.closest('.edit').classList.remove('show'));
$$('[data-acolor]').forEach(s => s.onclick = () => save({color: s.dataset.acolor}));
$$('[data-cmd]').forEach(b => b.onclick = () => send(Object.assign({cmd: b.dataset.cmd}, JSON.parse(b.dataset.args || '{}'))));
const offer = $('#offer'); if (offer) offer.onchange = () => send({cmd: 'account_offer_passwords', on: offer.checked});
$('#addBtn').onclick = () => { $('#addForm').classList.add('show'); $('#addName').focus(); };
$('#addCancel').onclick = () => $('#addForm').classList.remove('show');
$('#addForm form').onsubmit = e => {
  e.preventDefault();
  if (!$('#addName').value.trim()) return $('#addName').focus();
  send({cmd: 'account_add', name: $('#addName').value, email: $('#addEmail').value, color: NEXT_COLOR});
};
"""


def page(account: dict, others: list[dict], stats: dict, t: dict, section: str = "home") -> str:
    """The JUDO Account page. `stats`: history, bookmarks, passwords, never_save (counts),
    sandbox, offer_passwords (bools)."""
    section = section if section in SECTIONS else "home"
    dark = t.get("dark", False)
    e = html.escape
    name, email = e(account["name"]), e(account["email"])
    cmd = lambda c, label, cls="link", **args: (f'<button class="{cls}" data-cmd="{c}" '
                                                f"data-args='{e(json.dumps(args))}'>{label}</button>")
    go = lambda s, label: f'<button class="link" data-go="{s}">{label}</button>'
    chevron = _icon("chevron")
    pw, sandbox = stats["passwords"], stats["sandbox"]

    nav = "".join(f'<button data-go="{k}" class="{"sel" if k == section else ""}">{_icon(k)}<span>{v}</span></button>'
                  for k, v in SECTIONS.items())

    home = f"""<div class="hero"><div class="pic">{_avatar(account, "")}
<button class="cam" data-photo="1" title="Change profile picture">{SVG["camera"]}</button></div></div>
<h1>Welcome, {name}</h1>
<p class="lead">Manage your info, privacy and security to make JUDO work better for you.</p>
<div class="grid">
<div class="card"><div class="pad"><h3>Privacy &amp; personalisation</h3>
<p>See the data in your JUDO Account and choose what is kept.</p><p class="big">{stats["history"]:,}</p>
<p>pages in your history</p></div>{go("privacy", "Manage your data &amp; privacy")}</div>
<div class="card"><div class="pad"><h3>{"Your account is protected" if sandbox else "Security check"}</h3>
<p>{"Web pages run in the Chromium sandbox, isolated from this PC." if sandbox else
    "The Chromium sandbox is off — web pages are not isolated from this PC."}</p>
<p class="big">{pw}</p><p>saved password{"" if pw == 1 else "s"}</p></div>{go("security", "See details")}</div>
<div class="card"><div class="pad"><h3>Personal info</h3><p>Your name, email, picture and colour.</p></div>
{go("personal", "Manage your personal info")}</div>
<div class="card"><div class="pad"><h3>Accounts on this browser</h3>
<p>Each account keeps its own logins, cookies and passwords.</p><p class="big">{len(others) + 1}</p></div>
{go("accounts", "Manage accounts")}</div></div>"""

    colors = "".join(f'<span data-acolor="{c}" class="{"sel" if c == account["color"] else ""}" style="background:{c}"></span>'
                     for c in COLORS)
    personal = f"""<h2>Personal info</h2><p class="lead">Info about you that JUDO Browser shows — only on this PC.</p>
<div class="card"><div class="pad"><h3>Basic info</h3></div>
<button class="row" data-photo="1"><span class="k">Profile picture</span><span class="v">
<small>A picture helps you recognise your account</small></span>{_avatar(account, "")}</button>
<button class="row" data-edit="name"><span class="k">Name</span><span class="v">{name}</span>{chevron}</button>
<div class="edit" id="edit-name"><form><input name="name" value="{name}" maxlength="40">
<div class="actions"><button type="button" class="btn" data-cancel>Cancel</button>
<button class="btn primary">Save</button></div></form></div>
<button class="row" data-edit="email"><span class="k">Email</span><span class="v">{email or "<small>Not set</small>"}</span>{chevron}</button>
<div class="edit" id="edit-email"><form><input name="email" type="email" value="{email}" maxlength="80"
placeholder="you@example.com"><div class="actions"><button type="button" class="btn" data-cancel>Cancel</button>
<button class="btn primary">Save</button></div></form></div>
<div class="row static"><span class="k">Colour</span><span class="v"><span class="colors">{colors}</span></span></div>
{('<div class="row static"><span class="k">Picture</span><span class="v">'
  + cmd("account_photo_remove", "Remove picture", "btn") + '</span></div>') if account.get("photo") else ""}
</div>"""

    privacy = f"""<h2>Data &amp; privacy</h2><p class="lead">Your browsing data and the choices that keep it private.</p>
<div class="card"><div class="pad"><h3>History</h3><p>{stats["history"]:,} pages visited, kept on this PC.</p></div>
{cmd("open", "Open history", page="history")}{cmd("open", "Clear browsing data", page="clear")}</div>
<div class="card"><div class="pad"><h3>Bookmarks</h3><p>{stats["bookmarks"]:,} saved pages.</p></div>
{cmd("open", "Open bookmarks", page="bookmarks")}</div>
<div class="card"><div class="pad"><h3>Cookies &amp; site logins</h3>
<p>Signing out clears this account's cookies and site logins. History, bookmarks and passwords stay.</p></div>
{cmd("account_signout", "Sign out of all websites")}</div>
<div class="card"><div class="pad"><h3>Download your data</h3>
<p>A copy of your profile, bookmarks, history and settings as a JSON file (no passwords).</p></div>
{cmd("account_export", "Download your data")}</div>"""

    security = f"""<h2>Security</h2><p class="lead">Settings and recommendations to keep your account secure.</p>
<div class="card"><div class="pad"><h3>Protection</h3></div>
<div class="row static"><span class="k">Chromium sandbox</span><span class="v">
<span class="pill {"on" if sandbox else "off"}">{"On" if sandbox else "Off"}</span>
<small>{"Web pages run isolated from your files and programs." if sandbox else
        "Turn it on in Settings → Advanced and restart the browser."}</small></span></div>
<div class="row static"><span class="k">Password encryption</span><span class="v">Windows DPAPI
<small>Saved passwords can only be read by your Windows account on this PC.</small></span></div></div>
<div class="card"><div class="pad"><h3>Password manager</h3></div>
<button class="row" data-cmd="open" data-args='{e(json.dumps({"page": "passwords"}))}'><span class="k">Saved passwords</span>
<span class="v">{pw}<small>{stats["never_save"]} site{"" if stats["never_save"] == 1 else "s"} set to never save</small></span>{chevron}</button>
<label class="row"><span class="k">Offer to save</span><span class="v">Ask to save passwords when you sign in</span>
<span class="switch"><input type="checkbox" id="offer" {"checked" if stats["offer_passwords"] else ""}><i></i></span></label></div>
<div class="card"><div class="pad"><h3>Signed-in sites</h3><p>Lost a device or used a shared PC? Sign out everywhere in this account.</p></div>
{cmd("account_signout", "Sign out of all websites")}</div>"""

    acct_rows = f"""<div class="row static">{_avatar(account, "")}<span class="v">{name}<span class="tag">This account</span>
<small>{email or "JUDO Account"}</small></span></div>""" + "".join(
        f"""<div class="row static">{_avatar(o, "")}<span class="v">{e(o["name"])}<small>{e(o["email"] or "JUDO Account")}</small></span>
{cmd("account_switch", "Switch", "btn", id=o["id"])}</div>""" for o in others)
    remove = ("" if account["id"] == "default" else
              f'<div class="card"><div class="pad"><h3>Remove this account</h3><p>Closes its windows. Its saved site '
              f'data stays on this PC.</p></div>{cmd("account_remove", "Remove " + name)}</div>')
    accounts_html = f"""<h2>Accounts</h2><p class="lead">People and profiles using JUDO Browser on this PC.</p>
<div class="card"><div class="pad"><h3>Accounts on this browser</h3></div>{acct_rows}
<button class="link" id="addBtn">+ Add another account</button>
<div class="add" id="addForm"><form><input id="addName" maxlength="40" placeholder="Name">
<input id="addEmail" type="email" maxlength="80" placeholder="Email (optional)">
<div class="actions"><button type="button" class="btn" id="addCancel">Cancel</button>
<button class="btn primary">Add account</button></div></form></div></div>{remove}"""

    body = {"home": home, "personal": personal, "privacy": privacy, "security": security, "accounts": accounts_html}
    sections = "".join(f'<section id="{k}" class="{"show" if k == section else ""}">{v}</section>' for k, v in body.items())
    css = _sub(CSS, {"BG": t["toolbar"], "TEXT": t["text"], "SUB": t["sub"], "HOVER": t["hover"],
                     "BORDER": t["border"], "CARD": t["popup"] if dark else "#FFFFFF", "ACCENT": t["accent"],
                     "ACCENT_TEXT": t["accent"], "SELECT": t["select"], "ON_ACCENT": "#202124" if dark else "#FFFFFF"})
    js = (f"const CMD={json.dumps(CMD)}, ACCOUNT={json.dumps({k: account.get(k, '') for k in ('id', 'name', 'email', 'color')})},"
          f" NEXT_COLOR={json.dumps(COLORS[(len(others) + 1) % len(COLORS)])};" + JS)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>JUDO Account</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@500&display=swap" rel="stylesheet">
<style>{css}</style></head><body class="{"dark" if dark else ""}">
<div class="top"><div class="brand"><b><span>J</span><span>U</span><span>D</span><span>O</span></b> Account</div>
{_avatar(account, "", 'title="' + name + '"')}</div>
<div class="wrap"><nav>{nav}</nav><main>{sections}
<p class="note">JUDO Accounts live only on this PC — nothing is sent anywhere.</p></main></div>
<script>{js}</script></body></html>"""
