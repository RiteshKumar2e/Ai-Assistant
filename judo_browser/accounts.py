"""
judo_browser/accounts.py — JUDO Accounts: local browser profiles, like Chrome's.

Each account has its own name, email, colour and picture, and its own cookies,
site logins, cache and saved passwords (a separate Chromium profile), so two
people — or one person's two Google logins — can share the browser. The
"default" account keeps the browser's original profile folders, so nothing a
user already signed in to is lost. History, bookmarks and settings are shared.

Nothing here leaves the PC: a JUDO Account is not an online account.
"""
from __future__ import annotations

import base64
import getpass
import re
import time

from judo_browser.app_data import DATA, load, save

FILE = "accounts.json"
COLORS = ["#1A73E8", "#D93025", "#188038", "#E37400", "#A142F4", "#007B83", "#C5221F", "#9334E6", "#1E8E3E", "#F29900"]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _default_name() -> str:
    try:
        return getpass.getuser().replace(".", " ").title()
    except Exception:
        return "You"


def load_all() -> dict:
    data = load(FILE, None)
    if not data or not data.get("list"):
        data = {"current": "default",
                "list": [{"id": "default", "name": _default_name(), "email": "", "color": COLORS[0], "photo": ""}]}
    if data["current"] not in {a["id"] for a in data["list"]}:
        data["current"] = data["list"][0]["id"]
    return data


def save_all(data: dict) -> None:
    save(FILE, data)


def get(data: dict, account_id: str) -> dict:
    return next((a for a in data["list"] if a["id"] == account_id), data["list"][0])


def storage(account_id: str):
    """(profile folder, cache folder, file-name prefix) for an account's own data."""
    if account_id == "default":
        return DATA / "profile", DATA / "cache", ""
    base = DATA / "accounts" / account_id
    return base / "profile", base / "cache", f"accounts/{account_id}/"


def clean(fields: dict) -> dict | None:
    """Validated name / email / colour from a form, or None if something is wrong."""
    name = str(fields.get("name", "")).strip()[:40]
    email = str(fields.get("email", "")).strip()[:80]
    color = str(fields.get("color", "")).strip().upper()
    if not name or (email and not _EMAIL.match(email)):
        return None
    if not re.fullmatch(r"#[0-9A-F]{6}", color):
        color = COLORS[0]
    return {"name": name, "email": email, "color": color}


def add(data: dict, fields: dict) -> dict | None:
    f = clean(fields)
    if f is None:
        return None
    acct = {"id": f"a{int(time.time() * 1000):x}", "photo": "", **f}
    data["list"].append(acct)
    return acct


def edit(data: dict, account_id: str, fields: dict) -> bool:
    f = clean(fields)
    if f is None:
        return False
    get(data, account_id).update(f)
    return True


def remove(data: dict, account_id: str) -> bool:
    """The default account can't be removed (it holds the original profile)."""
    if account_id == "default" or account_id not in {a["id"] for a in data["list"]}:
        return False
    data["list"] = [a for a in data["list"] if a["id"] != account_id]
    if data["current"] == account_id:
        data["current"] = "default"
    return True


def photo_from_file(path: str) -> str | None:
    """A picture file -> a small round-cropped-ready JPEG data URL (stored in accounts.json)."""
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PyQt6.QtGui import QImage
    img = QImage(path)
    if img.isNull():
        return None
    side = min(img.width(), img.height())
    img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side).scaled(
        192, 192, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    raw = QByteArray()
    buf = QBuffer(raw)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPG", 85)
    return "data:image/jpeg;base64," + base64.b64encode(bytes(raw)).decode()
