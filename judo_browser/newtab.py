"""
judo_browser/newtab.py — the New Tab page, laid out like Google's home page
with Chrome's New Tab extras: the JUDO logo in Google-style colours, a search
box with suggestions, shortcut tiles (add / edit / remove / undo), an apps
launcher, and a "Customize JUDO" panel (background, colour theme, light/dark,
shortcut options).

The page is plain HTML set into the tab. It talks back by logging
`CMD + json` to the console; Page.javaScriptConsoleMessage hands that to
MainWindow.ntp_command, which calls apply() here and redraws the page.
"""
from __future__ import annotations

import base64
import html
import json
from collections import Counter

from PyQt6.QtCore import QUrl

from judo_browser import theme
from judo_browser.app_data import DATA, SEARCH_ENGINES

CMD = "__judo_ntp__"
BG_FILE = DATA / "ntp_background.jpg"
MAX_TILES = 10
DEFAULT_TILES = [("Gmail", "https://mail.google.com"), ("YouTube", "https://www.youtube.com"),
                 ("ChatGPT", "https://chatgpt.com"), ("GitHub", "https://github.com"),
                 ("Google", "https://www.google.com"), ("News", "https://news.google.com"),
                 ("WhatsApp", "https://web.whatsapp.com"), ("Maps", "https://maps.google.com")]
KNOWN_NAMES = {"mail.google.com": "Gmail", "youtube.com": "YouTube", "chatgpt.com": "ChatGPT",
               "github.com": "GitHub", "web.whatsapp.com": "WhatsApp", "maps.google.com": "Maps",
               "drive.google.com": "Drive", "linkedin.com": "LinkedIn", "x.com": "X"}
APPS = [("Search", "https://www.google.com"), ("YouTube", "https://www.youtube.com"),
        ("Gmail", "https://mail.google.com"), ("Maps", "https://maps.google.com"),
        ("Drive", "https://drive.google.com"), ("News", "https://news.google.com"),
        ("Translate", "https://translate.google.com"), ("Photos", "https://photos.google.com"),
        ("Calendar", "https://calendar.google.com"), ("Meet", "https://meet.google.com"),
        ("ChatGPT", "https://chatgpt.com"), ("GitHub", "https://github.com")]
# id: (name, CSS background). Offline: no image downloads.
BACKGROUNDS = {
    "aurora": ("Aurora", "linear-gradient(160deg,#0B1D3A 0%,#12476B 38%,#1FA38A 72%,#A8E6A1 100%)"),
    "sunset": ("Sunset", "linear-gradient(165deg,#2E1A47 0%,#8E3B66 42%,#E9725A 76%,#F9C46B 100%)"),
    "ocean": ("Ocean", "linear-gradient(180deg,#0F2027 0%,#203A43 50%,#2C5364 100%)"),
    "forest": ("Forest", "linear-gradient(160deg,#0B3D2E 0%,#1E6B45 55%,#8DBF6A 100%)"),
    "midnight": ("Midnight", "radial-gradient(ellipse at top,#2B3A67 0%,#0E1328 60%,#05070F 100%)"),
    "nebula": ("Nebula", "radial-gradient(circle at 22% 30%,#7B2FF7 0%,transparent 45%),"
                         "radial-gradient(circle at 78% 72%,#F107A3 0%,transparent 45%),#140B2D"),
    "slate": ("Slate", "linear-gradient(135deg,#232526 0%,#414345 100%)"),
    "peach": ("Peach", "linear-gradient(135deg,#FFDDD2 0%,#FFB4A2 52%,#E5989B 100%)"),
    "lavender": ("Lavender", "linear-gradient(135deg,#E0C3FC 0%,#8EC5FC 100%)"),
    "mint": ("Mint", "linear-gradient(135deg,#D4FC79 0%,#96E6A1 100%)"),
    "desert": ("Desert", "linear-gradient(170deg,#F6D365 0%,#FDA085 100%)"),
    "rose": ("Rose gold", "linear-gradient(135deg,#F4C4C9 0%,#D9A7B0 45%,#B07D8A 100%)"),
}
LIGHT_BACKGROUNDS = {"peach", "lavender", "mint", "desert", "rose"}   # dark text reads better on these
PAGES = ("settings", "history", "downloads", "bookmarks")


def _host(url: str) -> str:
    return QUrl(url).host().removeprefix("www.")


def _name(host: str) -> str:
    if host in KNOWN_NAMES:
        return KNOWN_NAMES[host]
    parts = host.split(".")
    return (parts[1] if parts[0] in ("m", "en") and len(parts) > 2 else parts[0]).capitalize()


def _web_url(text: str) -> str | None:
    """A shortcut address as typed -> an http(s) URL, or None."""
    t = str(text or "").strip()
    if t and "://" not in t:
        t = "https://" + t
    u = QUrl(t)
    return u.toString() if u.isValid() and u.scheme() in ("http", "https") and u.host() else None


def has_add_tile(settings: dict) -> bool:
    return settings["ntp_shortcut_mode"] == "custom" and len(settings["shortcuts"]) < MAX_TILES


def tiles(settings: dict, history: list[dict]) -> list[dict]:
    """Chrome's shortcut grid: my shortcuts first (custom mode), then most visited, then
    defaults — MAX_TILES in all, counting the "Add shortcut" tile when it is shown."""
    limit = MAX_TILES - 1 if has_add_tile(settings) else MAX_TILES
    out = []
    if settings["ntp_shortcut_mode"] == "custom":
        out += [{"title": s["title"], "url": s["url"], "custom": True} for s in settings["shortcuts"]]
    hidden, seen = set(settings["hidden_tiles"]), {_host(t["url"]) for t in out}
    visited = Counter(QUrl(h["url"]).host() for h in history if QUrl(h["url"]).host())
    candidates = [(_name(_host("https://" + h)), "https://" + h) for h, _ in visited.most_common()] + DEFAULT_TILES
    for title, url in candidates:
        if len(out) >= limit:
            break
        h = _host(url)
        if h not in hidden and h not in seen:
            out.append({"title": title, "url": url, "custom": False})
            seen.add(h)
    return out[:limit]


def apply(settings: dict, cmd: dict, undo: list) -> str | None:
    """One New Tab page command -> what the window must do next:
    "refresh" (redraw New Tab pages), "restyle" (re-theme the browser too),
    "removed" (redraw with an Undo toast), "pick_bg" (ask for an image),
    "open:<page>", or None for a command that is not valid."""
    c, s = cmd.get("cmd"), settings
    if c == "bg" and (cmd.get("id") in BACKGROUNDS or cmd.get("id") == ""):
        s["ntp_background"] = cmd["id"]
        return "refresh"
    if c == "upload_bg":
        return "pick_bg"
    if c == "color":
        color = str(cmd.get("color", ""))
        if color and not (len(color) == 7 and color[0] == "#" and all(x in "0123456789abcdefABCDEF" for x in color[1:])):
            return None
        s["theme_color"] = color.upper()
        return "restyle"
    if c == "mode" and cmd.get("mode") in ("system", "light", "dark"):
        s["theme"] = cmd["mode"]
        return "restyle"
    if c == "show_shortcuts":
        s["ntp_show_shortcuts"] = bool(cmd.get("on"))
        return "refresh"
    if c == "shortcut_mode" and cmd.get("mode") in ("custom", "most_visited"):
        s["ntp_shortcut_mode"] = cmd["mode"]
        return "refresh"
    if c in ("add", "edit"):
        url, title = _web_url(cmd.get("url")), str(cmd.get("title", "")).strip()[:60]
        if not url:
            return None
        entry = {"title": title or _name(_host(url)), "url": url}
        old = str(cmd.get("old", ""))
        mine = [x["url"] for x in s["shortcuts"]]
        if c == "edit" and old in mine:
            s["shortcuts"][mine.index(old)] = entry
        else:
            if c == "edit" and old:   # editing a most-visited tile turns it into my shortcut
                s["hidden_tiles"].append(_host(old))
            if len(s["shortcuts"]) >= MAX_TILES:
                return None
            s["shortcuts"].append(entry)
        s["ntp_shortcut_mode"] = "custom"
        return "refresh"
    if c == "remove":
        url = str(cmd.get("url", ""))
        undo[:] = [[dict(x) for x in s["shortcuts"]], list(s["hidden_tiles"])]
        if url in [x["url"] for x in s["shortcuts"]]:
            s["shortcuts"] = [x for x in s["shortcuts"] if x["url"] != url]
        elif _host(url):
            s["hidden_tiles"].append(_host(url))
        else:
            return None
        return "removed"
    if c == "undo" and undo:
        s["shortcuts"], s["hidden_tiles"] = undo
        undo.clear()
        return "refresh"
    if c == "restore_tiles":
        s["shortcuts"], s["hidden_tiles"] = [], []
        return "refresh"
    if c == "reset":
        s.update(theme_color="", ntp_background="", ntp_show_shortcuts=True, ntp_shortcut_mode="custom")
        return "restyle"
    if c == "open" and cmd.get("page") in PAGES:
        return "open:" + cmd["page"]
    return None


def set_custom_background(path: str, settings: dict) -> bool:
    """Use an image from the PC as the background — scaled down and stored as
    JPEG so the page (which embeds it) stays under setHtml's 2 MB limit."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage
    img = QImage(path)
    if img.isNull():
        return False
    for width, quality in ((1920, 82), (1600, 70), (1280, 60)):
        scaled = img.scaledToWidth(min(width, img.width()), Qt.TransformationMode.SmoothTransformation)
        DATA.mkdir(parents=True, exist_ok=True)
        if scaled.save(str(BG_FILE), "JPG", quality) and BG_FILE.stat().st_size < 1_100_000:
            settings["ntp_background"] = "custom"
            return True
    return False


def _background(settings: dict) -> tuple[str, bool]:
    """(CSS background, is it a light one) for the chosen background; ("", False) for none."""
    bg = settings["ntp_background"]
    if bg in BACKGROUNDS:
        return BACKGROUNDS[bg][1], bg in LIGHT_BACKGROUNDS
    if bg == "custom" and BG_FILE.exists():
        data = base64.b64encode(BG_FILE.read_bytes()).decode()
        return f"url(data:image/jpeg;base64,{data}) center/cover no-repeat", False
    return "", False


def _favicon(url: str) -> str:
    return f"https://www.google.com/s2/favicons?sz=64&domain={html.escape(QUrl(url).host())}"


SVG = {
    "search": '<svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 '
              '3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 '
              '14 9.5 11.99 14 9.5 14z"/></svg>',
    "close": '<svg viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 '
             '13.41 17.59 19 19 17.59 13.41 12z"/></svg>',
    "apps": '<svg viewBox="0 0 24 24"><path d="M6 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm6 12c1.1 0 2-.9 2-2s-.9-2'
            '-2-2-2 .9-2 2 .9 2 2 2zm-6 0c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0-6c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2'
            ' 2 .9 2 2 2zm6 0c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm4-8c0 1.1.9 2 2 2s2-.9 2-2-.9-2-2-2-2 .9-2 '
            '2zm-4 2c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm6 6c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 6c1.1'
            ' 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2z"/></svg>',
    "more": '<svg viewBox="0 0 24 24"><path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 '
            '2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/></svg>',
    "add": '<svg viewBox="0 0 24 24"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>',
    "pen": '<svg viewBox="0 0 24 24"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a.996.996 0 '
           '000-1.41l-2.34-2.34a.996.996 0 00-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>',
    "history": '<svg viewBox="0 0 24 24"><path d="M13 3a9 9 0 00-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7'
               ' 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0013 21a9 9 0 000-18zm-1 5v5l4.28 2.54.72'
               '-1.21-3.5-2.08V8H12z"/></svg>',
    "upload": '<svg viewBox="0 0 24 24"><path d="M9 16h6v-6h4l-7-7-7 7h4zm-4 2h14v2H5z"/></svg>',
    "camera": '<svg viewBox="0 0 24 24"><path d="M12 15.2a3.2 3.2 0 100-6.4 3.2 3.2 0 000 6.4zM9 2L7.17 4H4c-1.1 0-2 '
              '.9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2h-3.17L15 2H9zm3 15c-2.76 0-5-2.24-5-5s2.24-5 5-5'
              ' 5 2.24 5 5-2.24 5-5 5z"/></svg>',
    "logout": '<svg viewBox="0 0 24 24"><path d="M5 5h7V3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h7v-2H5V5zm16 7l-4-4v3H9v2h8v3'
              'l4-4z"/></svg>',
    "check": '<svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>',
}

CSS = """
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:__BG_PLAIN__;color:__TEXT__;font:14px 'Segoe UI',Roboto,Arial,sans-serif;overflow-x:hidden}
body.has-bg{background:__BG__;background-attachment:fixed}
svg{width:20px;height:20px;fill:currentColor;flex:none}
a{color:inherit}
header{position:fixed;top:0;right:0;left:0;display:flex;justify-content:flex-end;align-items:center;gap:4px;
 padding:10px 16px;z-index:5}
header a{font-size:13px;text-decoration:none;padding:6px 10px;border-radius:4px}
header a:hover{text-decoration:underline}
.icon-btn{width:40px;height:40px;border:0;border-radius:50%;background:transparent;color:inherit;display:grid;
 place-items:center;cursor:pointer}
.icon-btn:hover{background:__HOVER__}
.av{border-radius:50%;display:grid;place-items:center;color:#fff;font-weight:500;overflow:hidden;flex:none;
 font-family:'Segoe UI',sans-serif}
.av img{width:100%;height:100%;object-fit:cover}
#avatarBtn{width:32px;height:32px;border:0;padding:0;margin-left:6px;font-size:15px;cursor:pointer}
#avatarBtn:hover{box-shadow:0 0 0 4px __HOVER__}
#acct{top:56px;right:12px;width:412px;max-width:calc(100vw - 24px);border-radius:28px;padding:14px 14px 10px;
 background:__ACCT_BG__;max-height:calc(100vh - 70px);overflow-y:auto}
.a-top{position:relative;text-align:center;font-size:14px;font-weight:500;padding:8px 40px;min-height:36px;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.a-top .icon-btn{position:absolute;right:0;top:0}
.a-head{display:flex;flex-direction:column;align-items:center;margin:10px 0 16px}
.a-pic{position:relative}
.a-big{width:84px;height:84px;font-size:38px}
.a-cam{position:absolute;right:-4px;bottom:-2px;width:30px;height:30px;border-radius:50%;border:0;cursor:pointer;
 background:__ACCT_BG__;color:__TEXT__;display:grid;place-items:center;box-shadow:0 1px 3px rgba(0,0,0,.35)}
.a-cam svg{width:16px;height:16px}
.a-hi{font-size:22px;margin:12px 0 16px}
.a-manage{border:1px solid __SUB__;background:transparent;color:__ACCENT__;border-radius:100px;padding:9px 24px;
 font:500 14px 'Segoe UI',sans-serif;cursor:pointer}
.a-manage:hover{background:__HOVER__}
.a-card{background:__CARD__;border-radius:24px;overflow:hidden}
.a-card + .a-card{margin-top:4px}
.a-row{display:flex;align-items:center;gap:16px;width:100%;padding:14px 22px;border:0;background:transparent;
 color:__TEXT__;font:14px 'Segoe UI',sans-serif;text-align:left;cursor:pointer}
.a-row + .a-row{border-top:2px solid __ACCT_BG__}
.a-row:hover{background:__HOVER__}
.a-row .av{width:32px;height:32px;font-size:14px}
.a-row small{display:block;color:__SUB__;font-size:12px;margin-top:1px}
.a-icon{width:32px;display:grid;place-items:center;color:__SUB__}
.a-foot{text-align:center;font-size:12px;color:__SUB__;padding:12px 0 4px}
.a-foot a{cursor:pointer;text-decoration:none}.a-foot a:hover{text-decoration:underline}
.a-colors{display:flex;gap:10px;flex-wrap:wrap;margin-top:4px}
.a-colors span{width:28px;height:28px;border-radius:50%;cursor:pointer}
.a-colors span.sel{outline:2px solid __TEXT__;outline-offset:2px}
.btn.danger{color:#D93025;margin-right:auto}
main{display:flex;flex-direction:column;align-items:center;padding-top:max(13vh,72px);min-height:100%}
.logo{font:500 92px/1 'Poppins','Product Sans','Segoe UI',sans-serif;letter-spacing:-2px;user-select:none;
 margin-bottom:30px;display:flex}
.logo span{display:inline-block;transition:transform .25s}
.logo:hover span:nth-child(odd){transform:translateY(-5px)}
.logo:hover span:nth-child(even){transform:translateY(5px)}
.l1{color:#4285F4}.l2{color:#EA4335}.l3{color:#FBBC05}.l4{color:#34A853}
.on-dark .logo span{color:#fff;text-shadow:0 2px 12px rgba(0,0,0,.35)}
.search{position:relative;width:min(584px,90vw)}
.box{display:flex;align-items:center;height:48px;padding:0 8px 0 16px;border-radius:24px;background:__BOX__;
 border:1px solid __BORDER__;color:__SUB__}
.box:hover,.search.focus .box{box-shadow:0 1px 6px rgba(32,33,36,.28);border-color:transparent}
.search.open .box{border-radius:24px 24px 0 0;border-bottom-color:transparent}
.box input{flex:1;border:0;outline:0;background:transparent;color:__TEXT__;font-size:16px;margin:0 12px;height:100%}
.box .icon-btn{width:36px;height:36px}
#clear{visibility:hidden}.search.typed #clear{visibility:visible}
#sugg{display:none;position:absolute;left:0;right:0;top:47px;margin:0;padding:0 0 10px;list-style:none;
 background:__BOX__;border-radius:0 0 24px 24px;box-shadow:0 4px 6px rgba(32,33,36,.28);z-index:4}
.search.open #sugg{display:block}
#sugg:before{content:'';display:block;border-top:1px solid __BORDER__;margin:0 14px 6px}
#sugg li{display:flex;align-items:center;gap:14px;padding:7px 20px;cursor:pointer;color:__TEXT__}
#sugg li svg{color:__SUB__;width:18px;height:18px}
#sugg li.sel,#sugg li:hover{background:__HOVER__}
#sugg li small{color:__SUB__;margin-left:auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:45%}
.tiles{display:grid;grid-template-columns:repeat(5,112px);gap:4px;margin-top:30px;justify-content:center}
.tile{position:relative;height:112px;border-radius:8px;display:flex;flex-direction:column;align-items:center;
 padding-top:16px;text-decoration:none;color:__TEXT__;cursor:pointer}
.tile:hover{background:__HOVER__}
.on-dark .tile{color:#fff}.on-dark .tile:hover{background:rgba(255,255,255,.14)}
.bubble{width:48px;height:48px;border-radius:50%;background:__BUBBLE__;display:grid;place-items:center;margin-bottom:10px;
 position:relative}
.bubble img{width:24px;height:24px}
.bubble b{display:none;font-size:20px;color:__ACCENT__}
.bubble.noimg img{display:none}.bubble.noimg b{display:block}
.tile span{font-size:13px;max-width:96px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.on-dark .tile span{text-shadow:0 1px 4px rgba(0,0,0,.6)}
.tile .more{position:absolute;top:2px;right:2px;width:28px;height:28px;opacity:0}
.tile:hover .more{opacity:1}
.tile .more:hover{background:__HOVER__}
.tile .more svg{width:16px;height:16px}
.add .bubble svg{color:__TEXT__}
.on-dark .add .bubble svg{color:#202124}
#panel::-webkit-scrollbar{width:8px}
#panel::-webkit-scrollbar-thumb{background:__BORDER__;border-radius:4px}
.popup{display:none;position:fixed;background:__POPUP__;color:__TEXT__;border-radius:8px;
 box-shadow:0 2px 10px rgba(0,0,0,.25);z-index:20;padding:8px 0}
.popup.show{display:block}
#menu button{display:block;width:100%;text-align:left;border:0;background:transparent;color:inherit;
 padding:9px 24px;font:inherit;cursor:pointer}
#menu button:hover{background:__HOVER__}
#apps{top:58px;right:16px;width:328px;padding:16px;border-radius:24px;grid-template-columns:repeat(3,1fr);gap:4px}
#apps.show{display:grid}
#apps a{display:flex;flex-direction:column;align-items:center;gap:8px;padding:12px 4px;border-radius:12px;
 text-decoration:none;font-size:13px}
#apps a:hover{background:__HOVER__}
#apps img{width:32px;height:32px}
footer{position:fixed;bottom:0;left:0;padding:14px 18px;display:flex;gap:16px;font-size:13px;z-index:3}
footer a{text-decoration:none;cursor:pointer;color:__SUB__}
footer a:hover{text-decoration:underline}
.on-dark footer a{color:rgba(255,255,255,.85)}
#customize{position:fixed;right:18px;bottom:16px;display:flex;align-items:center;gap:8px;height:36px;padding:0 16px 0 12px;
 border:1px solid __BORDER__;border-radius:18px;background:__BOX__;color:__ACCENT__;font:500 14px 'Segoe UI',sans-serif;
 cursor:pointer;z-index:3}
#customize:hover{box-shadow:0 1px 4px rgba(0,0,0,.25)}
#customize svg{width:18px;height:18px}
#panel{position:fixed;top:0;right:0;bottom:0;width:360px;max-width:100vw;background:__POPUP__;color:__TEXT__;
 box-shadow:-2px 0 16px rgba(0,0,0,.2);transform:translateX(105%);transition:transform .22s ease;z-index:30;
 overflow-y:auto;padding:0 20px 24px}
#panel.show{transform:none}
#panel h2{display:flex;align-items:center;justify-content:space-between;font:500 16px 'Segoe UI',sans-serif;margin:0 -8px 4px 0;
 padding:14px 0;position:sticky;top:0;background:__POPUP__}
#panel h3{font:500 14px 'Segoe UI',sans-serif;margin:22px 0 10px}
.modes{display:flex;gap:8px}
.modes button{flex:1;padding:10px 0;border:1px solid __BORDER__;border-radius:10px;background:transparent;color:__TEXT__;
 font:inherit;cursor:pointer}
.modes button.sel{border:2px solid __ACCENT__;color:__ACCENT__;font-weight:600}
.swatches{display:grid;grid-template-columns:repeat(7,1fr);gap:10px}
.swatch{aspect-ratio:1;border-radius:50%;border:2px solid transparent;cursor:pointer;position:relative;
 box-shadow:inset 0 0 0 1px rgba(0,0,0,.12)}
.swatch.sel{outline:2px solid __ACCENT__;outline-offset:2px}
.swatch input{position:absolute;inset:0;opacity:0;cursor:pointer}
.swatch.picker{background:conic-gradient(red,yellow,lime,aqua,blue,magenta,red)}
.bgs{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.bg{height:64px;border-radius:10px;cursor:pointer;position:relative;border:1px solid __BORDER__;display:grid;
 place-items:center;font-size:12px;color:__SUB__;overflow:hidden}
.bg .nm{position:absolute;left:0;right:0;bottom:0;padding:3px 6px;font-size:11px;color:#fff;
 background:linear-gradient(transparent,rgba(0,0,0,.55));text-align:left}
.bg.sel{outline:3px solid __ACCENT__;outline-offset:1px}
.bg.sel:after{content:'✓';position:absolute;top:4px;right:6px;width:20px;height:20px;border-radius:50%;
 background:__ACCENT__;color:#fff;font-size:12px;display:grid;place-items:center}
.row{display:flex;align-items:center;justify-content:space-between;padding:8px 0}
.row label{cursor:pointer}
.radio{display:flex;flex-direction:column;gap:8px;margin-top:6px}
.btn{border:1px solid __BORDER__;background:transparent;color:__ACCENT__;border-radius:18px;padding:8px 18px;
 font:500 14px 'Segoe UI',sans-serif;cursor:pointer}
.btn.primary{background:__ACCENT__;color:__ON_ACCENT__;border:0}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:40;place-items:center}
.modal.show{display:grid}
.modal form{background:__POPUP__;color:__TEXT__;border-radius:12px;padding:22px 24px;width:min(480px,92vw);
 box-shadow:0 8px 28px rgba(0,0,0,.3)}
.modal h4{margin:0 0 16px;font:500 18px 'Segoe UI',sans-serif}
.modal label{display:block;font-size:12px;color:__SUB__;margin:12px 0 4px}
.modal input{width:100%;padding:10px 12px;border-radius:6px;border:1px solid __BORDER__;background:__BOX__;
 color:__TEXT__;font-size:14px;outline:none}
.modal input:focus{border-color:__ACCENT__}
.modal .actions{display:flex;justify-content:flex-end;gap:8px;margin-top:22px}
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);display:none;align-items:center;gap:18px;
 background:#323232;color:#fff;padding:10px 12px 10px 18px;border-radius:6px;z-index:35;font-size:14px}
#toast.show{display:flex}
#toast button{border:0;background:transparent;color:#8AB4F8;font:500 14px 'Segoe UI',sans-serif;cursor:pointer}
"""

JS = r"""
const send = o => console.log(CMD + JSON.stringify(o));
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function target(text) {
  text = text.trim();
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) return text;
  if (/^localhost(:\d+)?(\/|$)/.test(text)) return 'http://' + text;
  if (!/\s/.test(text) && /^[\w-]+(\.[\w-]+)+(:\d+)?(\/.*)?$/.test(text)) return 'https://' + text;
  return ENGINE + encodeURIComponent(text);
}
function go(text) { if (text.trim()) location.href = target(text); }

// ── search box with suggestions (history from this browser + Google's suggest) ──
const q = $('#q'), box = $('.search'), list = $('#sugg');
let items = [], sel = -1, timer = 0;
function render() {
  box.classList.toggle('open', items.length > 0);
  list.innerHTML = items.map((it, i) => `<li class="${i === sel ? 'sel' : ''}" data-i="${i}">${it.hist ? ICON_HIST : ICON_SEARCH}`
    + `<span>${esc(it.text)}</span>${it.url ? `<small>${esc(it.url)}</small>` : ''}</li>`).join('');
}
function fromHistory(v) {
  const w = v.toLowerCase();
  return HISTORY.filter(h => h.t.toLowerCase().includes(w) || h.u.toLowerCase().includes(w)).slice(0, 3)
    .map(h => ({text: h.t, url: h.u.replace(/^https?:\/\/(www\.)?/, ''), go: h.u, hist: true}));
}
window.judoSuggest = data => {
  if (!data || data[0] !== q.value.trim()) return;
  const words = (data[1] || []).map(x => Array.isArray(x) ? x[0] : x).filter(Boolean);
  const hist = fromHistory(q.value.trim());
  items = hist.concat(words.filter(w => !hist.some(h => h.text === w)).slice(0, 8 - hist.length).map(w => ({text: w})));
  sel = -1; render();
};
window.google = {ac: {h: window.judoSuggest}};
q.addEventListener('input', () => {
  const v = q.value.trim();
  box.classList.toggle('typed', !!q.value);
  clearTimeout(timer);
  if (!v) { items = []; return render(); }
  items = fromHistory(v); sel = -1; render();
  timer = setTimeout(() => {
    const s = document.createElement('script');
    s.src = 'https://suggestqueries.google.com/complete/search?client=youtube&ds=&hl=en&jsonp=judoSuggest&q='
            + encodeURIComponent(v);
    s.onload = s.onerror = () => s.remove();
    document.head.appendChild(s);
  }, 120);
});
q.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (!items.length) return;
    e.preventDefault();
    const n = items.length;   // -1 = back to what was typed
    sel = e.key === 'ArrowDown' ? (sel + 1 >= n ? -1 : sel + 1) : (sel <= -1 ? n - 1 : sel - 1);
    render();
    if (sel >= 0 && !items[sel].hist) q.value = items[sel].text;
  } else if (e.key === 'Enter') {
    e.preventDefault();
    const it = items[sel];
    it ? (it.go ? location.href = it.go : go(it.text)) : go(q.value);
  } else if (e.key === 'Escape') { items = []; render(); }
});
q.addEventListener('focus', () => box.classList.add('focus'));
q.addEventListener('blur', () => setTimeout(() => { box.classList.remove('focus'); items = []; render(); }, 150));
list.addEventListener('mousedown', e => {
  const li = e.target.closest('li'); if (!li) return;
  const it = items[+li.dataset.i]; it.go ? location.href = it.go : go(it.text);
});
$('#clear').onclick = () => { q.value = ''; box.classList.remove('typed'); items = []; render(); q.focus(); };
$('#searchIcon').onclick = () => go(q.value);

// ── favicons: the site's icon, else its main domain's (web.whatsapp.com -> whatsapp.com), else
// its first letter. Google's service answers "no icon" with a 16px globe, not an error.
$$('.bubble img, #apps img').forEach(img => {
  const host = new URL(img.src).searchParams.get('domain') || '';
  const parts = host.split('.'), tries = parts.length > 2 ? [parts.slice(-2).join('.')] : [];
  const next = () => {
    const h = tries.shift();
    if (h) img.src = 'https://www.google.com/s2/favicons?sz=64&domain=' + h;
    else img.parentNode.classList.add('noimg');
  };
  const check = () => { if (img.naturalWidth && img.naturalWidth <= 16) next(); };
  img.addEventListener('error', next);
  img.addEventListener('load', check);
  if (img.complete) img.naturalWidth ? check() : next();   // may have loaded before this script ran
});

// ── popups ──
const menu = $('#menu'), apps = $('#apps');
let menuTile = null;
const acct = $('#acct');
function hidePopups() { [menu, apps, acct].forEach(x => x.classList.remove('show')); }
document.addEventListener('click', e => { if (!e.target.closest('.popup, .more, #appsBtn, #avatarBtn')) hidePopups(); });
$('#appsBtn').onclick = () => { menu.classList.remove('show'); acct.classList.remove('show'); apps.classList.toggle('show'); };
$('#avatarBtn').onclick = () => { menu.classList.remove('show'); apps.classList.remove('show'); acct.classList.toggle('show'); };

// ── JUDO Account popup ──
$('#acctClose').onclick = () => acct.classList.remove('show');
$('#aPhoto').onclick = () => send({cmd: 'account_photo'});
$$('[data-switch]').forEach(b => b.onclick = () => { hidePopups(); send({cmd: 'account_switch', id: b.dataset.switch}); });
$('#aSignout').onclick = () => { hidePopups(); send({cmd: 'account_signout'}); };
const am = $('#amodal');
let amode = 'edit', acolor = ACCOUNT.color;
function paintColors() { $$('[data-acolor]').forEach(x => x.classList.toggle('sel', x.dataset.acolor === acolor)); }
function openAccountForm(mode) {
  amode = mode; hidePopups();
  const edit = mode === 'edit';
  $('#amodal h4').textContent = edit ? 'Manage your JUDO Account' : 'Add another account';
  $('#aName').value = edit ? ACCOUNT.name : '';
  $('#aEmail').value = edit ? ACCOUNT.email : '';
  acolor = edit ? ACCOUNT.color : NEXT_COLOR; paintColors();
  $('#aRemove').style.display = edit && ACCOUNT.id !== 'default' ? '' : 'none';
  $('#aPhotoRow').style.display = edit ? '' : 'none';
  $('#aPhotoDel').style.display = edit && ACCOUNT.photo ? '' : 'none';
  $('#aSave').textContent = edit ? 'Save' : 'Add account';
  am.classList.add('show'); $('#aName').focus();
}
$$('[data-acolor]').forEach(x => x.onclick = () => { acolor = x.dataset.acolor; paintColors(); });
$('#aManage').onclick = () => openAccountForm('edit');
$('#aAdd').onclick = () => openAccountForm('add');
$('#aCancel').onclick = () => am.classList.remove('show');
$('#aRemove').onclick = () => { am.classList.remove('show'); send({cmd: 'account_remove'}); };
$('#aPhotoChange').onclick = () => send({cmd: 'account_photo'});
$('#aPhotoDel').onclick = () => { am.classList.remove('show'); send({cmd: 'account_photo_remove'}); };
am.addEventListener('mousedown', e => { if (e.target === am) am.classList.remove('show'); });
$('#amodal form').onsubmit = e => {
  e.preventDefault();
  if (!$('#aName').value.trim()) return $('#aName').focus();
  send({cmd: amode === 'edit' ? 'account_edit' : 'account_add', name: $('#aName').value,
        email: $('#aEmail').value, color: acolor});
  am.classList.remove('show');
};
function showMenu(tile, x, y) {
  menuTile = tile;
  menu.style.left = Math.min(x, innerWidth - 190) + 'px'; menu.style.top = Math.min(y, innerHeight - 100) + 'px';
  apps.classList.remove('show'); menu.classList.add('show');
}
$$('.tile .more').forEach(b => b.onclick = e => {
  e.preventDefault(); e.stopPropagation();
  const r = b.getBoundingClientRect();
  showMenu(b.closest('.tile'), r.left, r.bottom + 4);
});
$$('.tile:not(.add)').forEach(t => t.addEventListener('contextmenu', e => {
  e.preventDefault(); showMenu(t, e.clientX, e.clientY);
}));
$('#mEdit').onclick = () => { hidePopups(); openModal(menuTile.dataset.title, menuTile.dataset.url); };
$('#mRemove').onclick = () => { hidePopups(); send({cmd: 'remove', url: menuTile.dataset.url}); };

// ── add / edit shortcut ──
const modal = $('#modal'), fName = $('#fName'), fUrl = $('#fUrl');
let editing = '';
function openModal(title, url) {
  editing = url || '';
  $('#modal h4').textContent = editing ? 'Edit shortcut' : 'Add shortcut';
  fName.value = title || ''; fUrl.value = url || '';
  modal.classList.add('show'); fName.focus();
  check();
}
function check() { $('#fDone').disabled = !fUrl.value.trim(); }
fUrl.addEventListener('input', check);
$('#fCancel').onclick = () => modal.classList.remove('show');
modal.addEventListener('mousedown', e => { if (e.target === modal) modal.classList.remove('show'); });
$('#modal form').onsubmit = e => {
  e.preventDefault();
  if (!fUrl.value.trim()) return;
  send({cmd: editing ? 'edit' : 'add', old: editing, title: fName.value, url: fUrl.value});
};
const addTile = $('.add'); if (addTile) addTile.onclick = e => { e.preventDefault(); openModal('', ''); };

// ── customize panel ──
const panel = $('#panel');
$('#customize').onclick = () => panel.classList.add('show');
$('#closePanel').onclick = () => panel.classList.remove('show');
const p = o => send(Object.assign(o, {panel: true}));
$$('[data-mode]').forEach(b => b.onclick = () => p({cmd: 'mode', mode: b.dataset.mode}));
$$('[data-color]').forEach(b => b.onclick = () => p({cmd: 'color', color: b.dataset.color}));
$('#pickColor').addEventListener('change', e => p({cmd: 'color', color: e.target.value}));
$$('[data-bg]').forEach(b => b.onclick = () => p({cmd: 'bg', id: b.dataset.bg}));
$('#upload').onclick = () => p({cmd: 'upload_bg'});
$('#showTiles').onchange = e => p({cmd: 'show_shortcuts', on: e.target.checked});
$$('[name=tmode]').forEach(r => r.onchange = () => p({cmd: 'shortcut_mode', mode: r.value}));
$('#restoreTiles').onclick = () => p({cmd: 'restore_tiles'});
$('#reset').onclick = () => p({cmd: 'reset'});
$$('[data-open]').forEach(a => a.onclick = () => send({cmd: 'open', page: a.dataset.open}));
const undo = $('#undo'); if (undo) undo.onclick = () => send({cmd: 'undo'});
const toast = $('#toast'); if (toast.classList.contains('show')) setTimeout(() => toast.classList.remove('show'), 8000);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { hidePopups(); panel.classList.remove('show'); modal.classList.remove('show');
                            am.classList.remove('show'); }
});
"""


def _avatar(acct: dict, cls: str, attrs: str = "") -> str:
    """A JUDO Account's picture, or its initial on its colour."""
    inner = (f'<img src="{html.escape(acct["photo"])}" alt="">' if acct.get("photo")
             else html.escape((acct.get("name") or "J")[:1].upper()))
    return f'<span class="av {cls}" style="background:{html.escape(acct.get("color") or "#1A73E8")}" {attrs}>{inner}</span>'


def _account_popup(acct: dict, others: list[dict]) -> str:
    first = html.escape((acct["name"] or "there").split()[0])
    rows = "".join(f'<button class="a-row" data-switch="{html.escape(o["id"])}" title="Switch to {html.escape(o["name"])}">'
                   f'{_avatar(o, "")}<span>{html.escape(o["name"])}<small>{html.escape(o["email"] or "JUDO Account")}'
                   f'</small></span></button>' for o in others)
    from judo_browser.accounts import COLORS
    colors = "".join(f'<span data-acolor="{c}" style="background:{c}"></span>' for c in COLORS)
    return f"""<div class="popup" id="acct" role="dialog" aria-label="JUDO Account">
<div class="a-top">{html.escape(acct["email"] or "JUDO Account")}
<button class="icon-btn" id="acctClose" title="Close">{SVG["close"]}</button></div>
<div class="a-head"><div class="a-pic">{_avatar(acct, "a-big")}
<button class="a-cam" id="aPhoto" title="Change profile picture">{SVG["camera"]}</button></div>
<div class="a-hi">Hi, {first}!</div>
<button class="a-manage" id="aManage">Manage your JUDO Account</button></div>
<div class="a-card">{rows}
<button class="a-row" id="aAdd"><span class="a-icon">{SVG["add"]}</span>Add another account</button>
<button class="a-row" id="aSignout"><span class="a-icon">{SVG["logout"]}</span>Sign out of all websites</button></div>
<div class="a-foot">Kept only on this PC · <a data-open="settings">Settings</a></div></div>
<div id="amodal" class="modal"><form><h4>Manage your JUDO Account</h4>
<label for="aName">Name</label><input id="aName" maxlength="40" required>
<label for="aEmail">Email (optional)</label><input id="aEmail" type="email" maxlength="80" placeholder="you@example.com">
<label>Colour</label><div class="a-colors">{colors}</div>
<div id="aPhotoRow"><label>Profile picture</label><button type="button" class="btn" id="aPhotoChange">Change picture</button>
<button type="button" class="btn" id="aPhotoDel">Remove picture</button></div>
<div class="actions"><button type="button" class="btn danger" id="aRemove">Remove account</button>
<button type="button" class="btn" id="aCancel">Cancel</button>
<button type="submit" class="btn primary" id="aSave">Save</button></div></form></div>"""


def _sub(template: str, values: dict) -> str:
    for k, v in values.items():
        template = template.replace(f"__{k}__", v)
    return template


def _incognito_page(t: dict) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>New Incognito Tab</title>
<style>body{{margin:0;background:#202124;font:14px 'Segoe UI',sans-serif}}
.incog{{max-width:600px;margin:0 auto;padding:16vh 24px 0;color:#E8EAED}}
.incog h1{{font:400 26px 'Segoe UI',sans-serif;margin:24px 0 16px}}.incog p{{color:#BDC1C6;line-height:1.6}}
.incog svg{{width:72px;height:72px;fill:#9AA0A6}}</style></head><body><div class="incog">
<svg viewBox="0 0 24 24"><path d="{theme.PATHS['incognito']}"/></svg>
<h1>You've gone Incognito</h1>
<p>Others who use this device won't see your activity, so you can browse more privately. JUDO Browser won't
save your browsing history, cookies and site data, or information entered in forms. Downloads and bookmarks
are kept.</p><p>Your activity might still be visible to the websites you visit, your employer or school,
and your internet service provider.</p></div></body></html>"""


def page(settings: dict, history: list[dict], t: dict, *, incognito: bool = False,
         panel: bool = False, toast: str = "", chromium: str = "",
         account: dict | None = None, others: list[dict] | None = None) -> str:
    """The New Tab page for the current settings and theme."""
    if incognito:
        return _incognito_page(t)
    bg_css, light_bg = _background(settings)
    on_dark = bool(bg_css) and not light_bg
    dark = t.get("dark", False)
    box = t["popup"] if dark else "#FFFFFF"
    engine_name, engine = SEARCH_ENGINES[settings["search_engine"]]

    tile_html = ""
    if settings["ntp_show_shortcuts"]:
        shown = tiles(settings, history)
        for x in shown:
            title, url = html.escape(x["title"]), html.escape(x["url"])
            tile_html += (f'<a class="tile" href="{url}" data-url="{url}" data-title="{title}" title="{title}">'
                          f'<button class="icon-btn more" title="More actions">{SVG["more"]}</button>'
                          f'<div class="bubble"><img src="{_favicon(x["url"])}" alt=""><b>{title[:1]}</b></div>'
                          f'<span>{title}</span></a>')
        if has_add_tile(settings):
            tile_html += f'<a class="tile add" href="#"><div class="bubble">{SVG["add"]}</div><span>Add shortcut</span></a>'
        tile_html = f'<div class="tiles">{tile_html}</div>'

    apps_html = "".join(f'<a href="{u}"><span class="bubble" style="background:none;margin:0"><img src="{_favicon(u)}" '
                        f'alt=""><b>{n[:1]}</b></span>{n}</a>' for n, u in APPS)

    seen, hist = set(), []
    for h in reversed(history):
        if h["url"] not in seen and len(hist) < 300:
            seen.add(h["url"])
            hist.append({"t": h.get("title") or h["url"], "u": h["url"]})

    cur_color, cur_bg = settings["theme_color"], settings["ntp_background"]
    modes = "".join(f'<button data-mode="{m}" class="{"sel" if settings["theme"] == m else ""}">{label}</button>'
                    for m, label in (("light", "☀ Light"), ("dark", "☾ Dark"), ("system", "🖥 Device")))
    swatches = (f'<div class="swatch {"sel" if not cur_color else ""}" data-color="" title="Default" '
                f'style="background:linear-gradient(135deg,#FFFFFF 50%,#DEE1E6 50%)"></div>'
                + "".join(f'<div class="swatch {"sel" if cur_color == c.upper() else ""}" data-color="{c}" title="{n}" '
                          f'style="background:{c}"></div>' for n, c in theme.COLORS.items())
                + f'<label class="swatch picker {"sel" if cur_color and cur_color not in [c.upper() for c in theme.COLORS.values()] else ""}"'
                  f' title="Custom colour"><input type="color" id="pickColor" value="{cur_color or "#4285F4"}"></label>')
    bgs = (f'<div class="bg {"sel" if not cur_bg else ""}" data-bg="" style="background:{t["toolbar"]}">No background</div>'
           + "".join(f'<div class="bg {"sel" if cur_bg == k else ""}" data-bg="{k}" style="background:{css}">'
                     f'<span class="nm">{n}</span></div>' for k, (n, css) in BACKGROUNDS.items())
           + f'<div class="bg {"sel" if cur_bg == "custom" else ""}" id="upload" title="Upload from device">'
             f'{SVG["upload"]}<span class="nm" style="color:inherit;background:none">Upload image</span></div>')
    mode = settings["ntp_shortcut_mode"]
    toast_html = ('<div id="toast" class="show">Shortcut removed<button id="undo">Undo</button>'
                  '</div>'
                  if toast == "removed" else '<div id="toast"></div>')

    on_accent = "#202124" if dark else "#FFFFFF"
    acct = account or {"id": "default", "name": "You", "email": "", "color": "#1A73E8", "photo": ""}
    others = others or []
    from judo_browser.accounts import COLORS as ACOLORS
    next_color = ACOLORS[(len(others) + 1) % len(ACOLORS)]
    css = _sub(CSS, {"BG_PLAIN": t["toolbar"], "BG": bg_css or t["toolbar"], "TEXT": t["text"], "SUB": t["sub"],
                     "HOVER": t["hover"], "BORDER": t["border"], "BOX": box, "POPUP": t["popup"],
                     "ACCENT": t["accent"], "BUBBLE": t["omni"] if not on_dark else "#FFFFFF",
                     "ON_ACCENT": on_accent, "ACCT_BG": "#28292C" if dark else "#E9EEF6",
                     "CARD": "#1B1B1C" if dark else "#FFFFFF"})
    js = (f"const CMD={json.dumps(CMD)}, ENGINE={json.dumps(engine)}, HISTORY={json.dumps(hist, ensure_ascii=False)},"
          f" ICON_SEARCH={json.dumps(SVG['search'])}, ICON_HIST={json.dumps(SVG['history'])},"
          f" ACCOUNT={json.dumps({k: acct.get(k, '') for k in ('id', 'name', 'email', 'color', 'photo')})},"
          f" NEXT_COLOR={json.dumps(next_color)};" + JS)
    body_cls = " ".join(c for c in ("has-bg" if bg_css else "", "on-dark" if on_dark else "") if c)

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>New Tab</title>
<link rel="preconnect" href="https://fonts.gstatic.com">
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@500&display=swap" rel="stylesheet">
<style>{css}</style></head><body class="{body_cls}">
<header><a href="https://mail.google.com">Gmail</a><a href="https://www.google.com/imghp">Images</a>
<button class="icon-btn" id="appsBtn" title="Apps">{SVG["apps"]}</button>
{_avatar(acct, "", f'id="avatarBtn" role="button" tabindex="0" title="JUDO Account: {html.escape(acct["name"])}"')}</header>
{_account_popup(acct, others)}
<div class="popup" id="apps">{apps_html}</div>
<div class="popup" id="menu"><button id="mEdit">Edit shortcut</button><button id="mRemove">Remove</button></div>
<main>
<div class="logo" title="JUDO"><span class="l1">J</span><span class="l2">U</span><span class="l3">D</span><span class="l4">O</span></div>
<div class="search"><div class="box"><span id="searchIcon" style="display:flex;cursor:pointer">{SVG["search"]}</span>
<input id="q" autocomplete="off" spellcheck="false" placeholder="Search {html.escape(engine_name)} or type a URL" aria-label="Search">
<button class="icon-btn" id="clear" title="Clear">{SVG["close"]}</button></div><ul id="sugg"></ul></div>
{tile_html}
</main>
<footer><a data-open="history">History</a><a data-open="downloads">Downloads</a><a data-open="bookmarks">Bookmarks</a>
<a data-open="settings">Settings</a></footer>
<button id="customize">{SVG["pen"]}Customize JUDO</button>
<aside id="panel" class="{"show" if panel else ""}">
<h2>Customize JUDO <button class="icon-btn" id="closePanel" title="Close">{SVG["close"]}</button></h2>
<h3>Mode</h3><div class="modes">{modes}</div>
<h3>Colour</h3><div class="swatches">{swatches}</div>
<h3>Background</h3><div class="bgs">{bgs}</div>
<h3>Shortcuts</h3>
<div class="row"><label for="showTiles">Show shortcuts</label><input type="checkbox" id="showTiles" {"checked" if settings["ntp_show_shortcuts"] else ""}></div>
<div class="radio"><label><input type="radio" name="tmode" value="custom" {"checked" if mode == "custom" else ""}> My shortcuts — add your own, plus most visited</label>
<label><input type="radio" name="tmode" value="most_visited" {"checked" if mode == "most_visited" else ""}> Most visited sites</label></div>
<div class="row" style="margin-top:14px"><button class="btn" id="restoreTiles">Restore default shortcuts</button></div>
<div class="row" style="margin-top:18px"><button class="btn" id="reset">Reset to default</button>
<span style="color:{t["sub"]};font-size:12px">{html.escape(chromium)}</span></div>
</aside>
<div id="modal" class="modal"><form><h4>Add shortcut</h4><label for="fName">Name</label><input id="fName" maxlength="60">
<label for="fUrl">URL</label><input id="fUrl" placeholder="example.com">
<div class="actions"><button type="button" class="btn" id="fCancel">Cancel</button>
<button type="submit" class="btn primary" id="fDone">Done</button></div></form></div>
{toast_html}
<script>{js}</script></body></html>"""
