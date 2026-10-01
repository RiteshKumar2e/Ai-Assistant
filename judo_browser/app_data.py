"""judo_browser/app_data.py — where the browser keeps its files, and settings."""
from __future__ import annotations

import json
from pathlib import Path

DATA = Path.home() / ".judo" / "browser"

DEFAULT_SETTINGS = {
    "theme": "system",                 # system | light | dark
    "theme_color": "",                 # "" = classic grey, else a hex seed that tints the frame (Customize JUDO)
    "ntp_background": "",              # "" | a newtab.BACKGROUNDS id | "custom" (ntp_background.jpg)
    "ntp_show_shortcuts": True,
    "ntp_shortcut_mode": "custom",     # custom (my shortcuts + most visited) | most_visited
    "shortcuts": [],                   # [{title, url}] added on the New Tab page
    "hidden_tiles": [],                # most-visited hosts removed from the New Tab page
    "startup": "continue",             # newtab | continue | page
    "startup_page": "",
    "search_engine": "google",         # google | bing | duckduckgo
    "home_page": "",                   # empty = new tab page
    "show_home": False,
    "show_bookmarks_bar": True,
    "ask_download_location": False,
    "download_dir": "",                # empty = the user's Downloads folder
    "offer_passwords": True,
    "sandbox": True,                   # Chromium renderer sandbox (app.sandbox_ready makes Qt readable to it)
    "sandbox_fixed": True,             # marks settings saved after the sandbox fix (see load_settings)
    "zoom": {},                        # host -> zoom factor, remembered per site like Chrome
    "window": {},                      # last geometry
}

SEARCH_ENGINES = {"google": ("Google", "https://www.google.com/search?q="),
                  "bing": ("Bing", "https://www.bing.com/search?q="),
                  "duckduckgo": ("DuckDuckGo", "https://duckduckgo.com/?q=")}


def load(name: str, default):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except Exception:
        return default


def save(name: str, value) -> None:
    (DATA / name).parent.mkdir(parents=True, exist_ok=True)   # names may sit in a subfolder (accounts/<id>/…)
    tmp = DATA / (name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DATA / name)


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    stored = load("settings.json", {})
    if not stored.get("sandbox_fixed"):
        stored.pop("sandbox", None)    # older builds saved "off" because the sandbox couldn't start then
    s.update(stored)
    s["sandbox_fixed"] = True
    return s
